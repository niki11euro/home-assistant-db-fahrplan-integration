"""Three independent timestamp entities plus two summary entities per route."""

from __future__ import annotations

from datetime import datetime
from math import ceil
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DBConfigEntry
from .const import DOMAIN, SUBENTRY_ROUTE
from .coordinator import RouteCoordinator
from .model import BERLIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DBConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add entities under their corresponding HA config subentry."""
    for subentry in entry.get_subentries_of_type(SUBENTRY_ROUTE):
        coordinator = entry.runtime_data.routes[subentry.subentry_id]
        device = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}:{subentry.subentry_id}")},
            name=subentry.title,
            manufacturer="DB Fahrplan (Community)",
            model="Timetables direct connection",
        )
        entities = [
            DepartureSensor(coordinator, entry.entry_id, subentry.subentry_id, device, position)
            for position in range(3)
        ]
        entities.extend([
            DepartureCountSensor(coordinator, entry.entry_id, subentry.subentry_id, device),
            LastRefreshSensor(coordinator, entry.entry_id, subentry.subentry_id, device),
        ])
        async_add_entities(entities, config_subentry_id=subentry.subentry_id)
        await coordinator.async_refresh()


class RouteEntity(CoordinatorEntity[RouteCoordinator], SensorEntity):
    """Base sensor bound to a single named route device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: RouteCoordinator, entry_id: str, subentry_id: str,
        device: DeviceInfo, suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_{subentry_id}_{suffix}"
        self._attr_device_info = device


class DepartureSensor(RouteEntity):
    """Timestamp and details of a specific (1st/2nd/3rd) departure."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:train"

    def __init__(
        self, coordinator: RouteCoordinator, entry_id: str, subentry_id: str,
        device: DeviceInfo, position: int,
    ) -> None:
        super().__init__(coordinator, entry_id, subentry_id, device, f"departure_{position + 1}")
        self._position = position
        self._attr_translation_key = "departure"
        self._attr_translation_placeholders = {"number": str(position + 1)}

    @property
    def native_value(self) -> datetime | None:
        """Expected departure time, which HA displays as a local timestamp."""
        if self.coordinator.data is None or self._position >= len(self.coordinator.data):
            return None
        return self.coordinator.data[self._position].expected

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the train information for cards, templates and automations."""
        data = self.coordinator.data
        if not data or self._position >= len(data):
            return {}
        dep = data[self._position]
        now = datetime.now(BERLIN)
        return {
            "line": dep.line,
            "train": dep.train,
            "direction": dep.destination,
            "platform": dep.platform,
            "planned_platform": dep.planned_platform,
            "planned_departure": dep.scheduled.isoformat() if dep.scheduled else None,
            "expected_departure": dep.expected.isoformat(),
            "delay_minutes": dep.delay_minutes,
            "minutes_until_departure": max(0, ceil((dep.expected - now).total_seconds() / 60)),
            "cancelled": dep.cancelled,
            "stops": list(dep.path),
            "stop_id": dep.stop_id,
        }


class DepartureCountSensor(RouteEntity):
    """Number of currently available direct departures (0 to 3)."""

    _attr_translation_key = "departure_count"
    _attr_icon = "mdi:train-variant"

    def __init__(self, coordinator: RouteCoordinator, entry_id: str, subentry_id: str, device: DeviceInfo) -> None:
        super().__init__(coordinator, entry_id, subentry_id, device, "departure_count")

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return len(self.coordinator.data)


class LastRefreshSensor(RouteEntity):
    """Timestamp of the most recent successful route poll."""

    _attr_translation_key = "last_update"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-check-outline"

    def __init__(self, coordinator: RouteCoordinator, entry_id: str, subentry_id: str, device: DeviceInfo) -> None:
        super().__init__(coordinator, entry_id, subentry_id, device, "last_update")

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.last_success
