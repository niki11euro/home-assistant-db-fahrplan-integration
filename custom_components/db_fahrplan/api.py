"""Shared async client, station search, timetable caching and API safeguards."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from urllib.parse import quote

from aiohttp import ClientError, ClientSession, ClientTimeout
from defusedxml.common import DefusedXmlException
from xml.etree.ElementTree import ParseError

from .const import API_BASE_URL
from .model import (BERLIN, Station, Departure, parse_board, parse_stations, select_departures, attach_destination_arrivals)


class DBAPIError(Exception):
    """A DB API request or response failed."""


class DBAuthError(DBAPIError):
    """DB rejected the configured credentials."""


class DBRateLimitError(DBAPIError):
    """DB or the local safeguard rejected excessive requests."""


class DBClient:
    """Share API requests across routes using one DB account."""

    def __init__(self, session: ClientSession, client_id: str, api_key: str) -> None:
        self._session = session
        self._headers = {
            "DB-Client-Id": client_id,
            "DB-Api-Key": api_key,
            "Accept": "application/xml",
        }
        self._cached: dict[str, tuple[float, object]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._rate_lock = asyncio.Lock()
        self._requests: deque[float] = deque()

    async def _request(self, endpoint: str) -> bytes:
        """Make one authenticated request without exposing secrets in logs."""
        loop = asyncio.get_running_loop()
        async with self._rate_lock:
            now = loop.time()
            while self._requests and self._requests[0] <= now - 60:
                self._requests.popleft()
            # The free plan allows up to 60 requests/minute; leave a margin.
            if len(self._requests) >= 55:
                raise DBRateLimitError("DB Timetables local 55 requests/minute safeguard")
            self._requests.append(now)
        url = f"{API_BASE_URL}/{endpoint}"
        try:
            async with self._session.get(
                url, headers=self._headers, timeout=ClientTimeout(total=15)
            ) as response:
                if response.status in (401, 403):
                    raise DBAuthError("DB Timetables credentials rejected")
                if response.status == 429:
                    raise DBRateLimitError("DB Timetables quota exceeded")
                if response.status == 404:
                    return b""
                if response.status >= 400:
                    raise DBAPIError(f"DB Timetables returned HTTP {response.status}")
                body = await response.read()
                if len(body) > 3_000_000:
                    raise DBAPIError("DB Timetables response exceeds 3 MB")
                return body
        except (asyncio.TimeoutError, ClientError) as err:
            raise DBAPIError("DB Timetables is temporarily unavailable") from err

    async def _get_cached(
        self, key: str, seconds: int, loader: Callable[[], Awaitable[object]],
        *, force: bool = False,
    ) -> object:
        loop = asyncio.get_running_loop()
        cached = self._cached.get(key)
        if not force and cached is not None and cached[0] > loop.time():
            return cached[1]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._cached.get(key)
            if not force and cached is not None and cached[0] > loop.time():
                return cached[1]
            result = await loader()
            self._cached[key] = (loop.time() + seconds, result)
            return result

    async def find_stations(self, query: str) -> list[Station]:
        """Search by name, EVA ID or DS100 code."""
        query = query.strip()
        if not (1 <= len(query) <= 100):
            return []

        async def load() -> object:
            xml = await self._request(f"station/{quote(query, safe='')}")
            try:
                return parse_stations(xml) if xml else []
            except (ValueError, ParseError, DefusedXmlException) as err:
                raise DBAPIError("DB Timetables returned malformed station XML") from err

        return await self._get_cached(f"station:{query.casefold()}", 300, load)  # type: ignore[return-value]

    async def _planned(self, eva: str, hour: datetime, *, force: bool = False) -> dict[str, dict]:
        key = f"plan:{eva}:{hour:%y%m%d%H}"

        async def load() -> object:
            xml = await self._request(f"plan/{eva}/{hour:%y%m%d}/{hour:%H}")
            try:
                return parse_board(xml) if xml else {}
            except (ValueError, ParseError, DefusedXmlException) as err:
                raise DBAPIError("DB Timetables returned malformed plan XML") from err

        return await self._get_cached(key, 1800, load, force=force)  # type: ignore[return-value]

    async def _changes(self, eva: str, *, force: bool = False) -> dict[str, dict]:
        async def load() -> object:
            xml = await self._request(f"fchg/{eva}")
            try:
                return parse_board(xml) if xml else {}
            except (ValueError, ParseError, DefusedXmlException) as err:
                raise DBAPIError("DB Timetables returned malformed changes XML") from err

        return await self._get_cached(f"changes:{eva}", 45, load, force=force)  # type: ignore[return-value]

    async def departures(
        self, origin_eva: str, destination_name: str, hours: int,
        include_cancelled: bool = False,
        now: datetime | None = None,
        force: bool = False,
        destination_eva: str | None = None,
    ) -> list[Departure]:
        """Fetch direct connections with optional arrival time at the target EVA."""
        if not origin_eva.isdigit() or not (1 <= hours <= 8):
            raise DBAPIError("Invalid station EVA number or lookahead")
        current = now or datetime.now(BERLIN)
        current = current.astimezone(BERLIN)
        start = current.replace(minute=0, second=0, microsecond=0)
        # Include final hour slice because the lookahead spans a rolling window.
        hours_to_fetch = [start + timedelta(hours=i) for i in range(hours + 1)]
        planned_slices, changes = await asyncio.gather(
            asyncio.gather(*(self._planned(origin_eva, hour, force=force) for hour in hours_to_fetch)),
            self._changes(origin_eva, force=force),
        )
        planned: dict[str, dict] = {}
        for slice_ in planned_slices:
            planned.update(slice_)
        candidates = select_departures(
            planned, changes, destination_name, current, hours,
            include_cancelled, limit=6 if destination_eva else 3,
        )
        if not candidates or not destination_eva:
            return candidates[:3]
        if not destination_eva.isdigit():
            raise DBAPIError("Invalid destination station EVA number")

        # Fetch the destination's arrival board: its <ar> times are the
        # only reliable source for the target-station arrival prediction.
        # A three-hour window covers normal regional/S-Bahn travel; if
        # no matching destination stop is found the arrival stays unknown.
        earliest = min(d.scheduled or d.expected for d in candidates)
        latest = max(d.scheduled or d.expected for d in candidates) + timedelta(hours=3)
        first_hour = earliest.replace(minute=0, second=0, microsecond=0)
        number_hours = int((latest - first_hour).total_seconds() // 3600) + 1
        try:
            arrivals_per_hour, destination_changes = await asyncio.gather(
                asyncio.gather(*(
                    self._planned(destination_eva, first_hour + timedelta(hours=i), force=force)
                    for i in range(number_hours)
                )),
                self._changes(destination_eva, force=force),
            )
        except DBAPIError:
            # Partial destination-board outage must not suppress departures.
            return candidates[:3]

        arrival_plan: dict[str, dict] = {}
        for arrival_slice in arrivals_per_hour:
            arrival_plan.update(arrival_slice)
        return attach_destination_arrivals(
            candidates, arrival_plan, destination_changes
        )[:3]
