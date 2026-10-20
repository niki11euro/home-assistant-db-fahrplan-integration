"""Deutsche Bahn Timetables: account credentials and independent route subentries."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DBClient
from .const import CONF_CLIENT_ID, CONF_API_KEY, SUBENTRY_ROUTE
from .coordinator import RouteCoordinator

PLATFORMS = [Platform.SENSOR]


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
    routes = {
        route.subentry_id: RouteCoordinator(hass, client, dict(route.data), route.title)
        for route in entry.get_subentries_of_type(SUBENTRY_ROUTE)
    }
    entry.runtime_data = RuntimeData(client=client, routes=routes)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DBConfigEntry) -> bool:
    """Stop the entity subscriptions and coordinator polling tasks."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
