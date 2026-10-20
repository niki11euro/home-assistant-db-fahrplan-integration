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
    history_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One predictable record for the three next departures per route."""
    trains: list[dict[str, Any]] = []
    for number, train in enumerate((departures or [])[:3], start=1):
        expected = train.expected.astimezone(BERLIN)
        scheduled = train.scheduled.astimezone(BERLIN) if train.scheduled else None
        arrival = train.arrival_expected.astimezone(BERLIN) if train.arrival_expected else None
        planned_arrival = (
            train.arrival_scheduled.astimezone(BERLIN)
            if train.arrival_scheduled else None
        )
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
            "arrival_date": arrival.date().isoformat() if arrival else None,
            "arrival_time": arrival.strftime("%H:%M") if arrival else None,
            "arrival_datetime": arrival.isoformat() if arrival else None,
            "scheduled_arrival": planned_arrival.isoformat() if planned_arrival else None,
            "arrival_platform": train.arrival_platform,
            "arrival_delay_minutes": train.arrival_delay_minutes,
            "arrival_source": "destination_board" if arrival else None,
            "cancelled": train.cancelled,
            "stop_id": train.stop_id,
            "stops": list(train.path),
        })

    history = history_summary or {
        "period_days": 30, "sample_count": 0,
        "average_delay_minutes": None,
        "first_departure": None, "last_departure": None,
        "observation_method": "last_forecast_near_departure",
    }
    return {
        "schema_version": 3,
        "available": available,
        "origin": dict(route_details["origin"]),
        "destination": dict(route_details["destination"]),
        "refresh_interval_minutes": route_details["refresh_interval_minutes"],
        "api_usage": dict(route_details.get("api_usage", {})),
        "last_success": last_success.isoformat() if last_success else None,
        "last_attempt": last_attempt.isoformat() if last_attempt else None,
        "last_error": last_error,
        "departure_count": len(trains) if available else None,
        "average_delay_minutes": history["average_delay_minutes"],
        "current_three_average_delay_minutes": average_delay_minutes(departures) if available else None,
        "history": history,
        "departures": trains,
    }
