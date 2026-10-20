"""Deutsche Bahn Timetables: account credentials and independent route subentries."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DBClient
from .const import (
    CONF_CLIENT_ID, CONF_API_KEY, CONF_SAVED_STATIONS, CONF_FROM_EVA, CONF_TO_EVA,
    CONF_FROM_NAME, CONF_TO_NAME, CONF_ORIGIN_DISPLAY_NAME, CONF_DEST_DISPLAY_NAME,
    SUBENTRY_ROUTE,
)
from .coordinator import RouteCoordinator

PLATFORMS = [Platform.SENSOR, Platform.BUTTON]


@dataclass(slots=True)
class RuntimeData:
    """Objects shared within one configured DB API account."""

    client: DBClient
    routes: dict[str, RouteCoordinator]


type DBConfigEntry = ConfigEntry[RuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: DBConfigEntry) -> bool:
    """Create the API client and all route coordinators."""
    client = DBClient(
        async_get_clientsession(hass),
        entry.data[CONF_CLIENT_ID],
        entry.data[CONF_API_KEY],
    )
    stations = entry.options.get(CONF_SAVED_STATIONS, {})
    routes = {}
    for route in entry.get_subentries_of_type(SUBENTRY_ROUTE):
        config = dict(route.data)
        for eva_key, name_key, display_key in (
            (CONF_FROM_EVA, CONF_FROM_NAME, CONF_ORIGIN_DISPLAY_NAME),
            (CONF_TO_EVA, CONF_TO_NAME, CONF_DEST_DISPLAY_NAME),
        ):
            saved = stations.get(config[eva_key], {})
            # Official station name, never the friendly display name, is used
            # for train path matching.
            config[name_key] = saved.get("name") or config[name_key]
            config[display_key] = saved.get("display_name") or config[name_key]
        routes[route.subentry_id] = RouteCoordinator(hass, client, config, route.title)
    entry.runtime_data = RuntimeData(client=client, routes=routes)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DBConfigEntry) -> bool:
    """Stop the entity subscriptions and coordinator polling tasks."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
