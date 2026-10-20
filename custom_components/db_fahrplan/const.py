"""Constants for the DB Fahrplan integration."""

DOMAIN = "db_fahrplan"
NAME = "DB Fahrplan"
API_BASE_URL = "https://apis.deutschebahn.com/db-api-marketplace/apis/timetables/v1"

CONF_CLIENT_ID = "client_id"
CONF_API_KEY = "api_key"
CONF_SAVED_STATIONS = "saved_stations"
CONF_DISPLAY_NAME = "display_name"
CONF_ORIGIN_DISPLAY_NAME = "_origin_display_name"
CONF_DEST_DISPLAY_NAME = "_destination_display_name"
CONF_NAME = "name"
CONF_FROM_QUERY = "from_query"
CONF_TO_QUERY = "to_query"
CONF_FROM_EVA = "from_eva"
CONF_FROM_NAME = "from_name"
CONF_TO_EVA = "to_eva"
CONF_TO_NAME = "to_name"
CONF_INTERVAL = "interval_minutes"
CONF_LOOKAHEAD = "lookahead_hours"
CONF_INCLUDE_CANCELLED = "include_cancelled"
CONF_HISTORY_DAYS = "history_days"

DEFAULT_HISTORY_DAYS = 30
MIN_HISTORY_DAYS = 1
MAX_HISTORY_DAYS = 365

DEFAULT_INTERVAL = 1
MIN_INTERVAL = 1
MAX_INTERVAL = 60
DEFAULT_LOOKAHEAD = 4
MIN_LOOKAHEAD = 1
MAX_LOOKAHEAD = 8
SUBENTRY_ROUTE = "route"

PLATFORMS = ["sensor", "button"]
