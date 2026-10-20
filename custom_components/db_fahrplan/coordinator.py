"""One independently configurable polling coordinator per route."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntryAuthFailed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DBAuthError, DBAPIError, DBClient
from .const import (
    CONF_FROM_EVA, CONF_TO_NAME, CONF_INTERVAL, CONF_LOOKAHEAD,
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

    async def _async_update_data(self) -> list[Departure]:
        try:
            result = await self._client.departures(
                origin_eva=self._config[CONF_FROM_EVA],
                destination_name=self._config[CONF_TO_NAME],
                hours=int(self._config.get(CONF_LOOKAHEAD, DEFAULT_LOOKAHEAD)),
                include_cancelled=bool(self._config.get(CONF_INCLUDE_CANCELLED, False)),
            )
        except DBAuthError as err:
            raise ConfigEntryAuthFailed("DB Timetables authentication failed") from err
        except DBAPIError as err:
            raise UpdateFailed(str(err)) from err
        self.last_success = datetime.now(BERLIN)
        return result
