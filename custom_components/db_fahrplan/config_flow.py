"""Account configuration, shared station library and repeatable route flows."""

from __future__ import annotations

from hashlib import sha256
from typing import Any
from uuid import uuid4

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlowWithReload,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import selector
import voluptuous as vol

from .api import DBAPIError, DBAuthError, DBClient
from .const import (
    DOMAIN, NAME, CONF_CLIENT_ID, CONF_API_KEY, CONF_SAVED_STATIONS,
    CONF_NAME, CONF_FROM_QUERY, CONF_TO_QUERY, CONF_FROM_EVA,
    CONF_FROM_NAME, CONF_TO_EVA, CONF_TO_NAME, CONF_INTERVAL,
    CONF_LOOKAHEAD, CONF_INCLUDE_CANCELLED, DEFAULT_INTERVAL,
    DEFAULT_LOOKAHEAD, MIN_INTERVAL, MAX_INTERVAL, MIN_LOOKAHEAD,
    MAX_LOOKAHEAD, SUBENTRY_ROUTE,
)
from .model import Station


def _password() -> selector.TextSelector:
    return selector.TextSelector(
        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
    )


def _account_schema(client_id: str = "") -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_CLIENT_ID, default=client_id): str,
        vol.Required(CONF_API_KEY): _password(),
    })


def _station_choices(stations: list[Station]) -> selector.SelectSelector:
    return selector.SelectSelector(selector.SelectSelectorConfig(
        options=[selector.SelectOptionDict(value=s.eva, label=s.label) for s in stations],
        mode=selector.SelectSelectorMode.DROPDOWN,
    ))


def _saved_stations(entry: ConfigEntry) -> dict[str, dict[str, str]]:
    """Read the user-maintained station library."""
    return dict(entry.options.get(CONF_SAVED_STATIONS, {}))


def _remember_stations(hass, entry: ConfigEntry, stations: list[Station]) -> None:
    """Keep every used origin/destination in the reusable station library."""
    saved = _saved_stations(entry)
    for station in stations:
        saved[station.eva] = {"name": station.name, "ds100": station.ds100}
    if saved != _saved_stations(entry):
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_SAVED_STATIONS: saved}
        )


class DBFahrplanConfigFlow(ConfigFlow, domain=DOMAIN):
    """Register one shared set of DB Timetables API credentials."""

    VERSION = 1

    async def _validate(self, client_id: str, api_key: str) -> None:
        client = DBClient(async_get_clientsession(self.hass), client_id, api_key)
        # A 200 response verifies the subscription. An empty station list is valid.
        await client.find_stations("Berlin Hbf")

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._validate(user_input[CONF_CLIENT_ID], user_input[CONF_API_KEY])
            except DBAuthError:
                errors["base"] = "invalid_auth"
            except DBAPIError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(sha256(user_input[CONF_CLIENT_ID].encode()).hexdigest())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=NAME, data=user_input)
        return self.async_show_form(step_id="user", data_schema=_account_schema(), errors=errors)

    async def async_step_reauth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm(user_input)

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            entry = self._get_reauth_entry()
            try:
                await self._validate(entry.data[CONF_CLIENT_ID], user_input[CONF_API_KEY])
            except DBAuthError:
                errors["base"] = "invalid_auth"
            except DBAPIError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_KEY: user_input[CONF_API_KEY]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): _password()}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow credentials to be updated without removing saved routes."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._validate(user_input[CONF_CLIENT_ID], user_input[CONF_API_KEY])
            except DBAuthError:
                errors["base"] = "invalid_auth"
            except DBAPIError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(sha256(user_input[CONF_CLIENT_ID].encode()).hexdigest())
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry, data_updates=user_input
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_account_schema(entry.data[CONF_CLIENT_ID]),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> DBOptionsFlow:
        return DBOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_ROUTE: DBRouteSubentryFlow}


class DBOptionsFlow(OptionsFlowWithReload):
    """Search, save and remove stations globally for this API account."""

    def __init__(self) -> None:
        self._results: dict[str, Station] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["add_station", "remove_station"])

    async def async_step_add_station(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            client = DBClient(
                async_get_clientsession(self.hass),
                self.config_entry.data[CONF_CLIENT_ID],
                self.config_entry.data[CONF_API_KEY],
            )
            try:
                found = await client.find_stations(user_input["query"])
            except DBAuthError:
                errors["base"] = "invalid_auth"
            except DBAPIError:
                errors["base"] = "cannot_connect"
            else:
                if not found:
                    errors["base"] = "no_stations"
                else:
                    self._results = {s.eva: s for s in found}
                    return await self.async_step_select_station()
        return self.async_show_form(
            step_id="add_station", errors=errors,
            data_schema=vol.Schema({vol.Required("query"): str}),
        )

    async def async_step_select_station(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            selected = self._results[user_input["station"]]
            saved = _saved_stations(self.config_entry)
            saved[selected.eva] = {"name": selected.name, "ds100": selected.ds100}
            return self.async_create_entry(
                title="", data={**self.config_entry.options, CONF_SAVED_STATIONS: saved}
            )
        return self.async_show_form(
            step_id="select_station",
            data_schema=vol.Schema({vol.Required("station"): _station_choices(list(self._results.values()))}),
        )

    async def async_step_remove_station(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        saved = _saved_stations(self.config_entry)
        if not saved:
            return self.async_abort(reason="no_saved_stations")
        if user_input is not None:
            saved.pop(user_input["station"], None)
            return self.async_create_entry(
                title="", data={**self.config_entry.options, CONF_SAVED_STATIONS: saved}
            )
        stations = [Station(eva=eva, name=data["name"], ds100=data.get("ds100", ""))
                    for eva, data in saved.items()]
        return self.async_show_form(
            step_id="remove_station",
            data_schema=vol.Schema({vol.Required("station"): _station_choices(stations)}),
        )


class DBRouteSubentryFlow(ConfigSubentryFlow):
    """Wizard to add or reconfigure independently polling direct connections."""

    def __init__(self) -> None:
        self._editing = False
        self._draft: dict[str, Any] = {}
        self._from: dict[str, Station] = {}
        self._to: dict[str, Station] = {}

    def _schema(self) -> vol.Schema:
        old = self._get_reconfigure_subentry().data if self._editing else {}
        return vol.Schema({
            vol.Optional(CONF_NAME, default=old.get(CONF_NAME, "")): str,
            vol.Required(CONF_FROM_QUERY, default=old.get(CONF_FROM_NAME, "")): str,
            vol.Required(CONF_TO_QUERY, default=old.get(CONF_TO_NAME, "")): str,
            vol.Required(CONF_INTERVAL, default=old.get(CONF_INTERVAL, DEFAULT_INTERVAL)):
                vol.All(vol.Coerce(int), vol.Range(min=MIN_INTERVAL, max=MAX_INTERVAL)),
            vol.Required(CONF_LOOKAHEAD, default=old.get(CONF_LOOKAHEAD, DEFAULT_LOOKAHEAD)):
                vol.All(vol.Coerce(int), vol.Range(min=MIN_LOOKAHEAD, max=MAX_LOOKAHEAD)),
            vol.Required(CONF_INCLUDE_CANCELLED, default=old.get(CONF_INCLUDE_CANCELLED, False)): bool,
        })

    async def _search(self, query: str) -> list[Station]:
        """Combine saved station IDs with official DB station lookup."""
        entry = self._get_entry()
        client = entry.runtime_data.client if entry.runtime_data else DBClient(
            async_get_clientsession(self.hass),
            entry.data[CONF_CLIENT_ID],
            entry.data[CONF_API_KEY],
        )
        found = await client.find_stations(query)
        stations = {station.eva: station for station in found}
        for eva, data in _saved_stations(entry).items():
            if query.casefold() in data["name"].casefold() or query == eva:
                stations.setdefault(eva, Station(eva, data["name"], data.get("ds100", "")))
        return list(stations.values())

    async def _route_form(self, user_input: dict[str, Any] | None, step: str) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._draft = dict(user_input)
            try:
                from_stations, to_stations = await self._search(user_input[CONF_FROM_QUERY]), await self._search(user_input[CONF_TO_QUERY])
            except DBAuthError:
                errors["base"] = "invalid_auth"
            except DBAPIError:
                errors["base"] = "cannot_connect"
            else:
                if not from_stations:
                    errors[CONF_FROM_QUERY] = "no_stations"
                if not to_stations:
                    errors[CONF_TO_QUERY] = "no_stations"
                if not errors:
                    self._from = {s.eva: s for s in from_stations}
                    self._to = {s.eva: s for s in to_stations}
                    return await self.async_step_choose_stations()
        return self.async_show_form(step_id=step, data_schema=self._schema(), errors=errors)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        self._editing = False
        return await self._route_form(user_input, "user")

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        self._editing = True
        return await self._route_form(user_input, "reconfigure")

    async def async_step_choose_stations(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            origin, destination = self._from[user_input[CONF_FROM_EVA]], self._to[user_input[CONF_TO_EVA]]
            if origin.eva == destination.eva:
                errors["base"] = "same_station"
            else:
                data = {
                    CONF_NAME: self._draft.get(CONF_NAME) or f"{origin.name} → {destination.name}",
                    CONF_FROM_EVA: origin.eva,
                    CONF_FROM_NAME: origin.name,
                    CONF_TO_EVA: destination.eva,
                    CONF_TO_NAME: destination.name,
                    CONF_INTERVAL: self._draft[CONF_INTERVAL],
                    CONF_LOOKAHEAD: self._draft[CONF_LOOKAHEAD],
                    CONF_INCLUDE_CANCELLED: self._draft[CONF_INCLUDE_CANCELLED],
                }
                entry = self._get_entry()
                _remember_stations(self.hass, entry, [origin, destination])
                if self._editing:
                    subentry = self._get_reconfigure_subentry()
                    return self.async_update_reload_and_abort(entry, subentry, data_updates=data)
                return self.async_create_entry(
                    title=data[CONF_NAME], data=data, unique_id=str(uuid4())
                )
        return self.async_show_form(
            step_id="choose_stations", errors=errors,
            data_schema=vol.Schema({
                vol.Required(CONF_FROM_EVA): _station_choices(list(self._from.values())),
                vol.Required(CONF_TO_EVA): _station_choices(list(self._to.values())),
            }),
        )
