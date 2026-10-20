"""Per-train values, route averages and one JSON summary per connection."""

from __future__ import annotations

import json
from datetime import date, datetime
from math import ceil
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DBConfigEntry
from .const import DOMAIN, SUBENTRY_ROUTE
from .coordinator import RouteCoordinator
from .model import BERLIN, Departure
from .payload import build_route_payload


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DBConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Expose sensors under the existing devices and config subentries."""
    for subentry in entry.get_subentries_of_type(SUBENTRY_ROUTE):
        coordinator = entry.runtime_data.routes[subentry.subentry_id]
        device = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}:{subentry.subentry_id}")},
            name=subentry.title,
            manufacturer="DB Fahrplan (Community)",
            model="Timetables direct connection",
        )
        entities: list[SensorEntity] = [
            DepartureSensor(coordinator, entry.entry_id, subentry.subentry_id, device, position)
            for position in range(3)
        ]
        # Dedicated sensors for easy dashboard cards and direct automation access.
        for position in range(3):
            entities.extend(
                DepartureFieldSensor(coordinator, entry.entry_id, subentry.subentry_id,
                                     device, position, field)
                for field in ("departure_time", "departure_date", "line", "terminal", "platform", "delay_minutes", "arrival_time", "arrival_date", "arrival_delay_minutes")
            )
        entities.extend([
            DepartureCountSensor(coordinator, entry.entry_id, subentry.subentry_id, device),
            LastRefreshSensor(coordinator, entry.entry_id, subentry.subentry_id, device),
            AverageDelaySensor(coordinator, entry.entry_id, subentry.subentry_id, device),
            RouteJSONSensor(coordinator, entry.entry_id, subentry.subentry_id, device),
        ])
        async_add_entities(entities, config_subentry_id=subentry.subentry_id)
        await coordinator.async_refresh()


class RouteEntity(CoordinatorEntity[RouteCoordinator], SensorEntity):
    """Base class preserving all existing unique IDs."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: RouteCoordinator, entry_id: str, subentry_id: str,
        device: DeviceInfo, suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_{subentry_id}_{suffix}"
        self._attr_device_info = device


class DepartureEntity(RouteEntity):
    """Base for data indexed by an individual departure position."""

    def __init__(
        self, coordinator: RouteCoordinator, entry_id: str, subentry_id: str,
        device: DeviceInfo, suffix: str, position: int,
    ) -> None:
        super().__init__(coordinator, entry_id, subentry_id, device, suffix)
        self._position = position

    @property
    def departure(self) -> Departure | None:
        data = self.coordinator.data
        return data[self._position] if data is not None and self._position < len(data) else None


class DepartureSensor(DepartureEntity):
    """Original timestamp sensor, retained for existing dashboards."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:train"

    def __init__(
        self, coordinator: RouteCoordinator, entry_id: str, subentry_id: str,
        device: DeviceInfo, position: int,
    ) -> None:
        super().__init__(coordinator, entry_id, subentry_id, device, f"departure_{position + 1}", position)
        self._attr_translation_key = "departure"
        self._attr_translation_placeholders = {"number": str(position + 1)}

    @property
    def native_value(self) -> datetime | None:
        dep = self.departure
        return dep.expected if dep else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        dep = self.departure
        if dep is None:
            return {}
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
            "arrival_time": dep.arrival_expected.isoformat() if dep.arrival_expected else None,
            "planned_arrival": dep.arrival_scheduled.isoformat() if dep.arrival_scheduled else None,
            "arrival_platform": dep.arrival_platform,
            "arrival_delay_minutes": dep.arrival_delay_minutes,
            "minutes_until_departure": max(0, ceil((dep.expected - now).total_seconds() / 60)),
            "cancelled": dep.cancelled,
            "stops": list(dep.path),
            "stop_id": dep.stop_id,
        }


class DepartureFieldSensor(DepartureEntity):
    """A separate sensor for each requested display value per train."""

    _attr_icon = "mdi:train"

    def __init__(
        self, coordinator: RouteCoordinator, entry_id: str, subentry_id: str,
        device: DeviceInfo, position: int, field: str,
    ) -> None:
        super().__init__(coordinator, entry_id, subentry_id, device, f"departure_{position + 1}_{field}", position)
        self._field = field
        self._attr_translation_key = field
        self._attr_translation_placeholders = {"number": str(position + 1)}
        if field in ("departure_date", "arrival_date"):
            self._attr_device_class = SensorDeviceClass.DATE
        if field in ("delay_minutes", "arrival_delay_minutes"):
            self._attr_native_unit_of_measurement = UnitOfTime.MINUTES
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> str | int | date | None:
        dep = self.departure
        if dep is None:
            return None
        if self._field == "departure_time":
            return dep.expected.astimezone(BERLIN).strftime("%H:%M")
        if self._field == "departure_date":
            return dep.expected.astimezone(BERLIN).date()
        if self._field == "arrival_time":
            return dep.arrival_expected.astimezone(BERLIN).strftime("%H:%M") if dep.arrival_expected else None
        if self._field == "arrival_date":
            return dep.arrival_expected.astimezone(BERLIN).date() if dep.arrival_expected else None
        if self._field == "arrival_delay_minutes":
            return dep.arrival_delay_minutes
        if self._field == "line":
            return dep.line
        if self._field == "terminal":
            return dep.destination
        if self._field == "platform":
            return dep.platform
        if self._field == "delay_minutes":
            return dep.delay_minutes
        return None


class DepartureCountSensor(RouteEntity):
    """Number of direct upcoming departures (0 to 3)."""

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
    """Most recent successful API polling timestamp."""

    _attr_translation_key = "last_update"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-check-outline"

    def __init__(self, coordinator: RouteCoordinator, entry_id: str, subentry_id: str, device: DeviceInfo) -> None:
        super().__init__(coordinator, entry_id, subentry_id, device, "last_update")

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.last_success


class AverageDelaySensor(RouteEntity):
    """Historical rolling mean of uniquely observed departed trains."""

    _attr_translation_key = "average_delay"
    _attr_icon = "mdi:clock-alert-outline"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: RouteCoordinator, entry_id: str, subentry_id: str, device: DeviceInfo) -> None:
        super().__init__(coordinator, entry_id, subentry_id, device, "average_delay")

    @property
    def available(self) -> bool:
        """Previously collected history remains readable during API outages."""
        return True

    @property
    def native_value(self) -> float | None:
        return self.coordinator.history_summary["average_delay_minutes"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.coordinator.history_summary


class RouteJSONSensor(RouteEntity):
    """Route snapshot as a JSON attribute, not an oversized sensor state."""

    _attr_translation_key = "json_route"
    _attr_icon = "mdi:code-json"

    def __init__(self, coordinator: RouteCoordinator, entry_id: str, subentry_id: str, device: DeviceInfo) -> None:
        super().__init__(coordinator, entry_id, subentry_id, device, "json_route")

    @property
    def available(self) -> bool:
        """Keep diagnostic JSON readable if a source request failed."""
        return True

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data
        return len(data) if self.coordinator.last_update_success and data is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        payload = build_route_payload(
            self.coordinator.data, self.coordinator.route_details,
            self.coordinator.last_success,
            available=self.coordinator.last_update_success,
            last_attempt=self.coordinator.last_attempt,
            last_error=self.coordinator.last_error,
            history_summary=self.coordinator.history_summary,
        )
        # HA states are limited to 255 chars: the complete JSON therefore
        # lives in attributes, while the state is a small departure count.
        return {"data": payload, "json": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}
