"""Manual refresh button on each independent connection device."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DBConfigEntry
from .const import DOMAIN, SUBENTRY_ROUTE
from .coordinator import RouteCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DBConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    for subentry in entry.get_subentries_of_type(SUBENTRY_ROUTE):
        coordinator = entry.runtime_data.routes[subentry.subentry_id]
        entity = RouteRefreshButton(coordinator, entry.entry_id, subentry.subentry_id, subentry.title)
        async_add_entities([entity], config_subentry_id=subentry.subentry_id)


class RouteRefreshButton(CoordinatorEntity[RouteCoordinator], ButtonEntity):
    """Force latest plan and realtime DB API responses on demand."""

    _attr_has_entity_name = True
    _attr_translation_key = "refresh"
    _attr_icon = "mdi:refresh"

    def __init__(
        self, coordinator: RouteCoordinator, entry_id: str, subentry_id: str, title: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_{subentry_id}_manual_refresh"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}:{subentry_id}")},
            name=title,
            manufacturer="DB Fahrplan (Community)",
            model="Timetables direct connection",
        )

    @property
    def available(self) -> bool:
        """Allow manual retry even after a temporary API failure."""
        return True

    async def async_press(self) -> None:
        """Ask DB for new source data rather than just reusing local caches."""
        await self.coordinator.async_manual_refresh()
