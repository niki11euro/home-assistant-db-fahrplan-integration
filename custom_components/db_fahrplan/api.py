"""Shared async client, station search, timetable caching and API safeguards."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from math import ceil
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
    """DB returned HTTP 429, or the local request budget was reached."""


class DBAccessDeniedError(DBAPIError):
    """HTTP 403: access denied; not proof that the API key is invalid."""


class DBCooldownError(DBAPIError):
    """The shared account is temporarily cooling down after an API error."""


# The free Timetables tariff is commonly capped at 60 calls/minute.
# Leave extra capacity for simultaneous clients using the same DB account.
MAX_REQUESTS_PER_MINUTE = 45
MIN_REQUEST_SPACING_SECONDS = 0.30
MAX_CONCURRENT_REQUESTS = 2


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
        self._in_flight = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        self._spacing = MIN_REQUEST_SPACING_SECONDS
        self._next_request_at = 0.0
        self._minute_requests: deque[float] = deque()
        self._hour_requests: deque[float] = deque()
        self._total_requests = 0
        self._last_http_status: int | None = None
        self._http_401_count = 0
        self._http_403_count = 0
        self._http_429_count = 0
        self._http_5xx_count = 0
        self._cooldown_until = 0.0
        self._cooldown_reason: str | None = None
        self._consecutive_temporary_errors = 0

    def _purge_request_history(self, now: float) -> None:
        """Retain minute/hour usage counters without storing endpoints or keys."""
        while self._minute_requests and self._minute_requests[0] <= now - 60:
            self._minute_requests.popleft()
        while self._hour_requests and self._hour_requests[0] <= now - 3600:
            self._hour_requests.popleft()

    @property
    def request_statistics(self) -> dict[str, int | str | None]:
        """Read-only diagnostics for the whole API account, shared by every route."""
        now = asyncio.get_running_loop().time()
        self._purge_request_history(now)
        cooldown = max(0, ceil(self._cooldown_until - now))
        return {
            "requests_last_minute": len(self._minute_requests),
            "requests_last_hour": len(self._hour_requests),
            "requests_since_start": self._total_requests,
            "request_limit_per_minute": MAX_REQUESTS_PER_MINUTE,
            "last_http_status": self._last_http_status,
            "http_401_count": self._http_401_count,
            "http_403_count": self._http_403_count,
            "http_429_count": self._http_429_count,
            "http_5xx_count": self._http_5xx_count,
            "cooldown_seconds_remaining": cooldown,
            "cooldown_reason": self._cooldown_reason if cooldown else None,
        }

    def _check_cooldown(self, now: float) -> None:
        if self._cooldown_until > now:
            remaining = ceil(self._cooldown_until - now)
            reason = self._cooldown_reason or "temporary error"
            raise DBCooldownError(
                f"DB Timetables paused after {reason}; next attempt in {remaining}s"
            )

    def _start_cooldown(
        self, reason: str, base_seconds: float, max_seconds: float,
        retry_after: float | None = None,
    ) -> int:
        """Shared exponential backoff; honor provider retry instructions."""
        self._consecutive_temporary_errors = min(
            self._consecutive_temporary_errors + 1, 7
        )
        exponent = min(self._consecutive_temporary_errors - 1, 5)
        wait = min(max_seconds, base_seconds * (2 ** exponent))
        if retry_after is not None:
            wait = max(wait, min(3600.0, retry_after))
        loop = asyncio.get_running_loop()
        self._cooldown_until = max(self._cooldown_until, loop.time() + wait)
        self._cooldown_reason = reason
        return max(1, ceil(wait))

    @staticmethod
    def _retry_after_seconds(headers: object) -> float | None:
        """Accept Retry-After seconds or HTTP-date without logging headers."""
        raw = headers.get("Retry-After") if hasattr(headers, "get") else None
        if raw is None:
            return None
        try:
            delay = float(str(raw).strip())
            if 0 <= delay < float("inf"):
                return min(delay, 3600.0)
        except (ValueError, TypeError):
            pass
        try:
            date = parsedate_to_datetime(str(raw).strip())
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return min(3600.0, max(0.0, (date - datetime.now(timezone.utc)).total_seconds()))
        except (TypeError, ValueError, OverflowError, IndexError):
            return None

    async def _request(self, endpoint: str) -> bytes:
        """Bound, pace and count real HTTP calls across all configured routes."""
        loop = asyncio.get_running_loop()
        async with self._rate_lock:
            now = loop.time()
            self._check_cooldown(now)
            scheduled = max(now, self._next_request_at)
            self._next_request_at = scheduled + self._spacing
        wait = scheduled - loop.time()
        if wait > 0:
            await asyncio.sleep(wait)

        async with self._in_flight:
            # Recheck *after* the queue: another request may have received
            # 403/429 while this request was waiting for its turn.
            async with self._rate_lock:
                now = loop.time()
                self._check_cooldown(now)
                self._purge_request_history(now)
                if len(self._minute_requests) >= MAX_REQUESTS_PER_MINUTE:
                    cooldown = max(1.0, self._minute_requests[0] + 60.5 - now)
                    self._cooldown_until = max(self._cooldown_until, now + cooldown)
                    self._cooldown_reason = "local minute request limit"
                    raise DBRateLimitError(
                        f"DB Timetables local request limit; retry in {ceil(cooldown)}s"
                    )
                # Count only requests that actually leave the queue.
                self._minute_requests.append(now)
                self._hour_requests.append(now)
                self._total_requests += 1

            url = f"{API_BASE_URL}/{endpoint}"
            try:
                async with self._session.get(
                    url, headers=self._headers, timeout=ClientTimeout(total=15)
                ) as response:
                    status = response.status
                    self._last_http_status = status
                    if status == 401:
                        self._http_401_count += 1
                        raise DBAuthError("DB Timetables HTTP 401: credentials rejected")
                    if status == 403:
                        self._http_403_count += 1
                        seconds = self._start_cooldown(
                            "HTTP 403 (subscription, permission or quota)",
                            120, 900, self._retry_after_seconds(getattr(response, "headers", {})),
                        )
                        raise DBAccessDeniedError(
                            f"DB Timetables HTTP 403: access forbidden; retry in {seconds}s"
                        )
                    if status == 429:
                        self._http_429_count += 1
                        seconds = self._start_cooldown(
                            "HTTP 429 rate limit", 90, 900,
                            self._retry_after_seconds(getattr(response, "headers", {})),
                        )
                        raise DBRateLimitError(
                            f"DB Timetables HTTP 429: too many requests; retry in {seconds}s"
                        )
                    if status >= 500:
                        self._http_5xx_count += 1
                        seconds = self._start_cooldown(
                            f"HTTP {status} server error", 30, 300,
                            self._retry_after_seconds(getattr(response, "headers", {})),
                        )
                        raise DBAPIError(
                            f"DB Timetables HTTP {status}: server error; retry in {seconds}s"
                        )
                    if status == 404:
                        body = b""
                    elif status >= 400:
                        raise DBAPIError(f"DB Timetables HTTP {status}")
                    else:
                        body = await response.read()
                        if len(body) > 3_000_000:
                            raise DBAPIError("DB Timetables response exceeds 3 MB")
                    # An earlier 403 may have arrived while other requests
                    # were in flight: those successes must not cancel cooldown.
                    if loop.time() >= self._cooldown_until:
                        self._consecutive_temporary_errors = 0
                        self._cooldown_reason = None
                    return body
            except (asyncio.TimeoutError, ClientError) as err:
                seconds = self._start_cooldown("network error", 30, 300)
                raise DBAPIError(
                    f"DB Timetables network error; retry in {seconds}s"
                ) from err

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
            asyncio.gather(*(self._planned(origin_eva, hour) for hour in hours_to_fetch)),
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
                    self._planned(destination_eva, first_hour + timedelta(hours=i))
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
