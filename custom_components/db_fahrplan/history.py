"""Deduplicated, persistent historical departure-delay observations.

The Timetables API publishes expected times, not independently confirmed
actual departure times. We therefore record the *last forecast observed close
to departure* once per unique stop, never once per refresh.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from statistics import mean
from typing import Any

from .model import Departure

# Keep enough history to allow widening the rolling calculation window later.
RETENTION_DAYS = 365


def _parse_time(text: str) -> datetime:
    timestamp = datetime.fromisoformat(text)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("A timezone-aware timestamp is required")
    return timestamp


class DepartureDelayHistory:
    """Record each observed train at most once after its expected departure.

    `records` is the durable history of completed observed trips and `pending`
    contains at most the recent approaching trains. All times are offset-aware
    ISO-8601 timestamps; none of the source credentials or station aliases is
    written into this record.
    """

    def __init__(self, window_days: int = 30, interval_minutes: int = 1) -> None:
        if not 1 <= window_days <= RETENTION_DAYS:
            raise ValueError("Invalid history window")
        self.window_days = window_days
        self.interval_minutes = interval_minutes
        self.records: dict[str, dict[str, Any]] = {}
        self.pending: dict[str, dict[str, Any]] = {}

    def load(self, stored: dict[str, Any] | None) -> None:
        """Restore valid entries, without trusting malformed storage contents."""
        if not isinstance(stored, dict):
            return
        for field in ("records", "pending"):
            result: dict[str, dict[str, Any]] = {}
            incoming = stored.get(field)
            if isinstance(incoming, dict):
                for stop_id, item in incoming.items():
                    if not isinstance(stop_id, str) or not isinstance(item, dict):
                        continue
                    try:
                        _parse_time(item["expected"])
                        _parse_time(item["observed_at"])
                        delay = item["delay_minutes"]
                        if isinstance(delay, bool) or not isinstance(delay, int):
                            continue
                        result[stop_id] = {
                            "expected": item["expected"],
                            "observed_at": item["observed_at"],
                            "delay_minutes": delay,
                        }
                    except (KeyError, TypeError, ValueError, OverflowError):
                        continue
            setattr(self, field, result)

    def dump(self) -> dict[str, Any]:
        return {"records": self.records.copy(), "pending": self.pending.copy()}

    def observe(self, departures: list[Departure], now: datetime) -> bool:
        """Capture live forecasts; finalize only recently observed departing trains.

        Called only after a *successful* board fetch. The last snapshot of a
        train is counted only when it was seen within 2 to 10 minutes of its
        expected departure, depending on the route refresh interval. Missing
        or cancelled trains are never inserted as fresh observations.
        """
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("The observation time must be timezone aware")
        dirty = False
        for dep in departures:
            if dep.cancelled:
                if self.pending.pop(dep.stop_id, None) is not None:
                    dirty = True
                continue
            if (dep.delay_minutes is None or dep.stop_id in self.records
                    or dep.expected <= now):
                continue
            observation = {
                "expected": dep.expected.isoformat(),
                "observed_at": now.isoformat(),
                "delay_minutes": dep.delay_minutes,
            }
            if self.pending.get(dep.stop_id) != observation:
                self.pending[dep.stop_id] = observation
                dirty = True

        # A long polling interval cannot turn a prediction hours ahead of
        # departure into a confident historical observation.
        observation_window = timedelta(minutes=max(2, min(10, self.interval_minutes * 2)))
        for stop_id, item in list(self.pending.items()):
            expected = _parse_time(item["expected"])
            observed = _parse_time(item["observed_at"])
            if expected > now:
                continue
            if (expected - observation_window <= observed <= expected
                    and stop_id not in self.records):
                self.records[stop_id] = item.copy()
            self.pending.pop(stop_id)
            dirty = True

        cutoff = now - timedelta(days=RETENTION_DAYS)
        for stop_id, item in list(self.records.items()):
            if _parse_time(item["expected"]) < cutoff:
                del self.records[stop_id]
                dirty = True
        return dirty

    def samples(self, now: datetime) -> list[dict[str, Any]]:
        """Samples inside the configured rolling history window."""
        cutoff = now - timedelta(days=self.window_days)
        return [
            item for item in self.records.values()
            if cutoff <= _parse_time(item["expected"]) <= now
        ]

    def summary(self, now: datetime) -> dict[str, Any]:
        """Historical average, sample size, and coverage metadata."""
        records = self.samples(now)
        timestamps = sorted(_parse_time(item["expected"]) for item in records)
        return {
            "period_days": self.window_days,
            "sample_count": len(records),
            "average_delay_minutes": round(mean(item["delay_minutes"] for item in records), 1) if records else None,
            "first_departure": timestamps[0].isoformat() if timestamps else None,
            "last_departure": timestamps[-1].isoformat() if timestamps else None,
            "observation_method": "last_forecast_near_departure",
        }
