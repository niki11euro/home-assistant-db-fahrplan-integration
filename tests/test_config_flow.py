"""Test route configuration with minimal HA UI stubs, no live HA required."""
import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest


class Marker:
    def __init__(self, name, default=None):
        self.name, self.default = name, default

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        return isinstance(other, Marker) and self.name == other.name


class Schema:
    def __init__(self, schema):
        self.schema = schema


class Select:
    def __init__(self, config):
        self.config = config


class ConfigFlow:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__()


class ConfigSubentryFlow:
    def _get_entry(self):
        return self.entry

    def _get_reconfigure_subentry(self):
        return self.subentry

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_abort(self, **kwargs):
        return {"type": "abort", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}

    def async_update_reload_and_abort(self, entry, subentry, **kwargs):
        return {"type": "update_entry", "entry": entry, "subentry": subentry, **kwargs}


@pytest.fixture
def flows(monkeypatch):
    vol = ModuleType("voluptuous")
    vol.Required = lambda key, default=None: Marker(key, default)
    vol.Optional = lambda key, default=None: Marker(key, default)
    vol.Schema = Schema
    vol.All = lambda *args: args
    vol.Coerce = lambda kind: kind
    vol.Range = lambda **kwargs: kwargs
    ha = ModuleType("homeassistant")
    ha.__path__ = []
    entries = ModuleType("homeassistant.config_entries")
    entries.ConfigEntry = type("ConfigEntry", (), {})
    entries.ConfigFlow = ConfigFlow
    entries.ConfigFlowResult = dict
    entries.ConfigSubentryFlow = ConfigSubentryFlow
    entries.OptionsFlowWithReload = type("OptionsFlowWithReload", (), {})
    entries.SubentryFlowResult = dict
    core = ModuleType("homeassistant.core")
    core.callback = lambda f: f
    helpers = ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    helpers.selector = SimpleNamespace(
        SelectSelector=Select,
        SelectSelectorConfig=lambda **kwargs: SimpleNamespace(**kwargs),
        SelectSelectorMode=SimpleNamespace(DROPDOWN="dropdown"),
        SelectOptionDict=lambda **kwargs: kwargs,
        TextSelector=Select,
        TextSelectorConfig=lambda **kwargs: SimpleNamespace(**kwargs),
        TextSelectorType=SimpleNamespace(PASSWORD="password"),
    )
    aiohttp = ModuleType("homeassistant.helpers.aiohttp_client")
    aiohttp.async_get_clientsession = lambda hass: None
    for name, mod in {
        "voluptuous": vol,
        "homeassistant": ha,
        "homeassistant.config_entries": entries,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.aiohttp_client": aiohttp,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    name = "custom_components.db_fahrplan.config_flow"
    monkeypatch.delitem(sys.modules, name, raising=False)
    yield importlib.import_module(name)
    monkeypatch.delitem(sys.modules, name, raising=False)


STATIONS = {
    "8007380": {"name": "Zorneding", "ds100": "MZOR"},
    "8004168": {"name": "Muenchen-Trudering", "ds100": "MTR"},
}


def route(flows, stations=None, old=None):
    flow = flows.DBRouteSubentryFlow()
    flow.entry = SimpleNamespace(options={"saved_stations": stations or {}})
    flow.subentry = SimpleNamespace(data=old or {})
    return flow


def field(form, name):
    return next((key, value) for key, value in form["data_schema"].schema.items() if key.name == name)


@pytest.mark.asyncio
async def test_route_form_uses_saved_station_dropdowns(flows):
    form = await route(flows, STATIONS).async_step_user()
    assert form["type"] == "form" and form["step_id"] == "user"
    names = {key.name for key in form["data_schema"].schema}
    assert {"from_eva", "to_eva"} <= names
    assert "from_query" not in names and "to_query" not in names
    for key in ("from_eva", "to_eva"):
        _, control = field(form, key)
        assert {option["value"] for option in control.config.options} == set(STATIONS)
        assert all(option["label"] for option in control.config.options)


@pytest.mark.asyncio
async def test_route_requires_two_saved_stations(flows):
    for stations in ({}, {"1": {"name": "Only one"}}):
        result = await route(flows, stations).async_step_user()
        assert result == {"type": "abort", "reason": "not_enough_saved_stations"}


@pytest.mark.asyncio
async def test_route_created_without_second_station_search(flows):
    flow = route(flows, STATIONS)
    data = {
        "name": "Stadteinwaerts", "from_eva": "8007380", "to_eva": "8004168",
        "interval_minutes": 1, "lookahead_hours": 4, "include_cancelled": False,
    }
    result = await flow.async_step_user(data)
    assert result["type"] == "create_entry" and result["title"] == "Stadteinwaerts"
    assert result["data"]["from_name"] == "Zorneding"
    assert result["data"]["to_name"] == "Muenchen-Trudering"
    assert result["data"]["interval_minutes"] == 1
    assert set(flow.entry.options["saved_stations"]) == set(STATIONS)


@pytest.mark.asyncio
async def test_same_or_unsaved_station_is_rejected(flows):
    flow = route(flows, STATIONS)
    data = {"from_eva": "8007380", "to_eva": "8007380"}
    result = await flow.async_step_user(data)
    assert result["errors"]["base"] == "same_station"
    data["to_eva"] = "unknown"
    result = await flow.async_step_user(data)
    assert result["errors"]["to_eva"] == "invalid_station"


@pytest.mark.asyncio
async def test_old_route_editable_after_stations_removed(flows):
    old = {
        "name": "Old", "from_eva": "8007380", "from_name": "Zorneding",
        "to_eva": "8004168", "to_name": "Muenchen-Trudering",
        "interval_minutes": 2, "lookahead_hours": 3, "include_cancelled": True,
    }
    flow = route(flows, {}, old)
    form = await flow.async_step_reconfigure()
    assert field(form, "from_eva")[0].default == old["from_eva"]
    assert field(form, "to_eva")[0].default == old["to_eva"]
    result = await flow.async_step_reconfigure({
        "name": "", "from_eva": old["from_eva"], "to_eva": old["to_eva"],
        "interval_minutes": 2, "lookahead_hours": 3, "include_cancelled": True,
    })
    assert result["type"] == "update_entry"
    assert result["data_updates"]["from_eva"] == old["from_eva"]
    assert result["data_updates"]["name"] == "Zorneding \u2192 Muenchen-Trudering"


@pytest.mark.asyncio
async def test_custom_display_name_does_not_replace_official_station_name(flows):
    stations = {
        "8007380": {"name":"Zorneding","ds100":"MZOR", "display_name":"Heimat"},
        "8004168": {"name":"Trudering, München","ds100":"PQTR", "display_name":"Stadt"},
    }
    flow = route(flows, stations)
    form = await flow.async_step_user()
    label = next(o["label"] for o in field(form,"to_eva")[1].config.options if o["value"]=="8004168")
    assert "Stadt" in label
    result = await flow.async_step_user({
        "name":"", "from_eva":"8007380", "to_eva":"8004168",
        "interval_minutes":1,"lookahead_hours":3,"include_cancelled":False,
    })
    assert result["data"]["to_name"]=="Trudering, München"
    assert result["data"]["from_name"]=="Zorneding"
    assert result["title"]=="Heimat → Stadt"


@pytest.mark.asyncio
async def test_station_display_name_saved_separately_from_official_record(flows):
    opt = flows.DBOptionsFlow()
    opt.config_entry = SimpleNamespace(options={"saved_stations": {
        "370920": {"name": "Trudering, München", "ds100": "PQTR"}
    }})
    opt.async_show_form = lambda **kwargs: {"type": "form", **kwargs}
    opt.async_create_entry = lambda **kwargs: {"type": "create_entry", **kwargs}
    form = await opt.async_step_set_display_name()
    assert form["step_id"] == "set_display_name"
    form = await opt.async_step_set_display_name({"station": "370920"})
    assert form["step_id"] == "station_display_name"
    saved = await opt.async_step_station_display_name({"display_name": "Mein Bahnhof"})
    assert saved["type"] == "create_entry"
    item = saved["data"]["saved_stations"]["370920"]
    assert item == {"name": "Trudering, München", "ds100": "PQTR", "display_name": "Mein Bahnhof"}
