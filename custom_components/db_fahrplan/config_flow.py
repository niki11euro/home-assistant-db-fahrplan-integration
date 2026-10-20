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
    DOMAIN, NAME, CONF_CLIENT_ID, CONF_API_KEY, CONF_SAVED_STATIONS, CONF_DISPLAY_NAME,
    CONF_NAME, CONF_FROM_EVA,
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
        self._selected_eva = ""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["add_station", "set_display_name", "remove_station"])

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
            previous = saved.get(selected.eva, {})
            saved[selected.eva] = {
                "name": selected.name, "ds100": selected.ds100,
                "display_name": previous.get("display_name", ""),
            }
            return self.async_create_entry(
                title="", data={**self.config_entry.options, CONF_SAVED_STATIONS: saved}
            )
        return self.async_show_form(
            step_id="select_station",
            data_schema=vol.Schema({vol.Required("station"): _station_choices(list(self._results.values()))}),
        )

    async def async_step_set_display_name(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a saved station whose friendly name should be changed."""
        saved = _saved_stations(self.config_entry)
        if not saved:
            return self.async_abort(reason="no_saved_stations")
        if user_input is not None:
            self._selected_eva = user_input["station"]
            return await self.async_step_station_display_name()
        stations = [
            Station(eva, data["name"], data.get("ds100", ""), data.get("display_name", ""))
            for eva, data in saved.items()
        ]
        return self.async_show_form(
            step_id="set_display_name",
            data_schema=vol.Schema({vol.Required("station"): _station_choices(stations)}),
        )

    async def async_step_station_display_name(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        saved = _saved_stations(self.config_entry)
        old = saved[self._selected_eva]
        if user_input is not None:
            new_name = user_input.get(CONF_DISPLAY_NAME, "").strip()
            if len(new_name) > 80:
                return self.async_show_form(
                    step_id="station_display_name",
                    data_schema=vol.Schema({
                        vol.Optional(CONF_DISPLAY_NAME, default=old.get("display_name", "")): str,
                    }),
                    errors={CONF_DISPLAY_NAME: "name_too_long"},
                )
            saved[self._selected_eva] = {**old, "display_name": new_name}
            return self.async_create_entry(
                title="", data={**self.config_entry.options, CONF_SAVED_STATIONS: saved}
            )
        return self.async_show_form(
            step_id="station_display_name",
            data_schema=vol.Schema({
                vol.Optional(CONF_DISPLAY_NAME, default=old.get("display_name", "")): str,
            }),
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
        stations = [Station(eva=eva, name=data["name"], ds100=data.get("ds100", ""), display_name=data.get("display_name", ""))
                    for eva, data in saved.items()]
        return self.async_show_form(
            step_id="remove_station",
            data_schema=vol.Schema({vol.Required("station"): _station_choices(stations)}),
        )



class DBRouteSubentryFlow(ConfigSubentryFlow):
    """Create and edit direct connections using the saved station library."""

    def __init__(self) -> None:
        self._editing = False

    def _available_stations(self) -> dict[str, Station]:
        """Offer saved stations, plus legacy route endpoints during editing.

        A station may have been removed from the shared library after a route
        was created. Keep those endpoints selectable during reconfiguration
        so the existing route is not silently changed or made uneditable.
        """
        stations = {
            eva: Station(eva, data["name"], data.get("ds100", ""), data.get("display_name", ""))
            for eva, data in _saved_stations(self._get_entry()).items()
        }
        if self._editing:
            old = self._get_reconfigure_subentry().data
            for eva_key, name_key in (
                (CONF_FROM_EVA, CONF_FROM_NAME),
                (CONF_TO_EVA, CONF_TO_NAME),
            ):
                eva = old.get(eva_key)
                if eva and eva not in stations:
                    stations[eva] = Station(eva, old.get(name_key) or eva, "")
        return dict(sorted(stations.items(), key=lambda item: ((item[1].display_name or item[1].name).casefold(), item[0])))

    def _schema(self, stations: dict[str, Station]) -> vol.Schema:
        """Present two dropdowns keyed by EVA ID, not free-form text fields."""
        old = self._get_reconfigure_subentry().data if self._editing else {}
        choices = _station_choices(list(stations.values()))
        fields: dict = {vol.Optional(CONF_NAME, default=old.get(CONF_NAME, "")): str}
        for key in (CONF_FROM_EVA, CONF_TO_EVA):
            previous = old.get(key)
            marker = (
                vol.Required(key, default=previous)
                if previous in stations else vol.Required(key)
            )
            fields[marker] = choices
        fields.update({
            vol.Required(CONF_INTERVAL, default=old.get(CONF_INTERVAL, DEFAULT_INTERVAL)):
                vol.All(vol.Coerce(int), vol.Range(min=MIN_INTERVAL, max=MAX_INTERVAL)),
            vol.Required(CONF_LOOKAHEAD, default=old.get(CONF_LOOKAHEAD, DEFAULT_LOOKAHEAD)):
                vol.All(vol.Coerce(int), vol.Range(min=MIN_LOOKAHEAD, max=MAX_LOOKAHEAD)),
            vol.Required(CONF_INCLUDE_CANCELLED, default=old.get(CONF_INCLUDE_CANCELLED, False)): bool,
        })
        return vol.Schema(fields)

    async def _route_form(
        self, user_input: dict[str, Any] | None, step: str
    ) -> SubentryFlowResult:
        stations = self._available_stations()
        if len(stations) < 2:
            return self.async_abort(reason="not_enough_saved_stations")

        errors: dict[str, str] = {}
        if user_input is not None:
            origin = stations.get(user_input.get(CONF_FROM_EVA, ""))
            destination = stations.get(user_input.get(CONF_TO_EVA, ""))
            if origin is None:
                errors[CONF_FROM_EVA] = "invalid_station"
            if destination is None:
                errors[CONF_TO_EVA] = "invalid_station"
            if origin is not None and destination is not None:
                if origin.eva == destination.eva:
                    errors["base"] = "same_station"
                else:
                    name = (user_input.get(CONF_NAME) or "").strip()
                    data = {
                        CONF_NAME: name or f"{origin.display_name or origin.name} \u2192 {destination.display_name or destination.name}",
                        CONF_FROM_EVA: origin.eva,
                        CONF_FROM_NAME: origin.name,
                        CONF_TO_EVA: destination.eva,
                        CONF_TO_NAME: destination.name,
                        CONF_INTERVAL: user_input[CONF_INTERVAL],
                        CONF_LOOKAHEAD: user_input[CONF_LOOKAHEAD],
                        CONF_INCLUDE_CANCELLED: user_input[CONF_INCLUDE_CANCELLED],
                    }
                    if self._editing:
                        return self.async_update_reload_and_abort(
                            self._get_entry(), self._get_reconfigure_subentry(),
                            data_updates=data,
                        )
                    return self.async_create_entry(
                        title=data[CONF_NAME], data=data, unique_id=str(uuid4())
                    )

        return self.async_show_form(
            step_id=step, data_schema=self._schema(stations), errors=errors
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        self._editing = False
        return await self._route_form(user_input, "user")

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        self._editing = True
        return await self._route_form(user_input, "reconfigure")
