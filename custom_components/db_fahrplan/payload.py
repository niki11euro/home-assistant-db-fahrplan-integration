"""Stable, JSON-serializable route data for dashboards and automations."""

from __future__ import annotations

from datetime import datetime
from statistics import mean
from typing import Any

from .model import BERLIN, Departure


def average_delay_minutes(departures: list[Departure] | None) -> float | None:
    """Mean delay of up to three available, non-cancelled trains.

    0 indicates no reported delay; None indicates no usable readings.
    """
    delays = [departure.delay_minutes for departure in (departures or [])
              if not departure.cancelled and departure.delay_minutes is not None]
    return round(mean(delays), 1) if delays else None


def build_route_payload(
    departures: list[Departure] | None,
    route_details: dict[str, Any],
    last_success: datetime | None,
    *,
    available: bool = True,
    last_attempt: datetime | None = None,
    last_error: str | None = None,
) -> dict[str, Any]:
    """One predictable record for the three next departures per route."""
    trains: list[dict[str, Any]] = []
    for number, train in enumerate((departures or [])[:3], start=1):
        expected = train.expected.astimezone(BERLIN)
        scheduled = train.scheduled.astimezone(BERLIN) if train.scheduled else None
        trains.append({
            "number": number,
            "line": train.line,
            "train": train.train,
            "terminal": train.destination,
            "departure_date": expected.date().isoformat(),
            "departure_time": expected.strftime("%H:%M"),
            "departure_datetime": expected.isoformat(),
            "scheduled_departure": scheduled.isoformat() if scheduled else None,
            "platform": train.platform,
            "planned_platform": train.planned_platform,
            "delay_minutes": train.delay_minutes,
            "cancelled": train.cancelled,
            "stop_id": train.stop_id,
            "stops": list(train.path),
        })

    return {
        "schema_version": 1,
        "available": available,
        "origin": dict(route_details["origin"]),
        "destination": dict(route_details["destination"]),
        "refresh_interval_minutes": route_details["refresh_interval_minutes"],
        "last_success": last_success.isoformat() if last_success else None,
        "last_attempt": last_attempt.isoformat() if last_attempt else None,
        "last_error": last_error,
        "departure_count": len(trains) if available else None,
        "average_delay_minutes": average_delay_minutes(departures) if available else None,
        "departures": trains,
    }
