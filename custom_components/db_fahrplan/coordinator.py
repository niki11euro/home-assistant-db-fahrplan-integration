"""One independently configurable polling coordinator per route."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntryAuthFailed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DBAuthError, DBAPIError, DBClient
from .const import (
    CONF_FROM_EVA, CONF_TO_EVA, CONF_FROM_NAME, CONF_TO_NAME, CONF_INTERVAL, CONF_LOOKAHEAD,
    CONF_ORIGIN_DISPLAY_NAME, CONF_DEST_DISPLAY_NAME,
    CONF_INCLUDE_CANCELLED, DEFAULT_INTERVAL, DEFAULT_LOOKAHEAD,
)
from .model import BERLIN, Departure

_LOGGER = logging.getLogger(__name__)


class RouteCoordinator(DataUpdateCoordinator[list[Departure]]):
    """Refresh one route, reusing account-level API caches."""

    def __init__(self, hass: HomeAssistant, client: DBClient, data: dict, title: str) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"DB Fahrplan: {title}",
            update_interval=timedelta(minutes=int(data.get(CONF_INTERVAL, DEFAULT_INTERVAL))),
        )
        self._client = client
        self._config = data
        self.last_success: datetime | None = None
        self.last_attempt: datetime | None = None
        self.last_error: str | None = None
        self._manual_lock = asyncio.Lock()
        self._force_next_update = False

    @property
    def route_details(self) -> dict:
        """Stable station IDs, official names and friendly names."""
        return {
            "origin": {
                "eva": self._config[CONF_FROM_EVA],
                "station_name": self._config[CONF_FROM_NAME],
                "display_name": self._config.get(CONF_ORIGIN_DISPLAY_NAME) or self._config[CONF_FROM_NAME],
            },
            "destination": {
                "eva": self._config[CONF_TO_EVA],
                "station_name": self._config[CONF_TO_NAME],
                "display_name": self._config.get(CONF_DEST_DISPLAY_NAME) or self._config[CONF_TO_NAME],
            },
            "refresh_interval_minutes": int(self.update_interval.total_seconds() / 60),
        }

    async def async_manual_refresh(self) -> None:
        """Force a new DB fetch for the route, ignoring timetable caches."""
        async with self._manual_lock:
            self._force_next_update = True
            try:
                await self.async_refresh()
            finally:
                self._force_next_update = False

    async def _async_update_data(self) -> list[Departure]:
        force = self._force_next_update
        self.last_attempt = datetime.now(BERLIN)
        try:
            result = await self._client.departures(
                origin_eva=self._config[CONF_FROM_EVA],
                destination_name=self._config[CONF_TO_NAME],
                hours=int(self._config.get(CONF_LOOKAHEAD, DEFAULT_LOOKAHEAD)),
                include_cancelled=bool(self._config.get(CONF_INCLUDE_CANCELLED, False)),
                force=force,
            )
        except DBAuthError as err:
            self.last_error = "DB authentication failed"
            raise ConfigEntryAuthFailed("DB Timetables authentication failed") from err
        except DBAPIError as err:
            self.last_error = str(err)
            raise UpdateFailed(str(err)) from err
        self.last_error = None
        self.last_success = datetime.now(BERLIN)
        return result
