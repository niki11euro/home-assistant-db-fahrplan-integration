# DB Fahrplan for Home Assistant

A community Home Assistant custom integration for the **official Deutsche Bahn Timetables API**. Configure your own DB API credentials once and add as many independent direct train connections as you like, each with the next **three departures**. Designed for HACS custom repositories and the native Home Assistant UI.

> **v2.2.0**: improved direct-stop matching, manual refresh button, 18 detailed train sensors, average delay and JSON snapshot per route, and separate friendly station labels. Unit tested; live DB data depends on the API response. Unofficial community integration.

## Features

- **One API account, many connections**: Uses native Home Assistant *config subentries*. Each connection gets its own device and polling interval.
- **Station search and library**: Search the official DB endpoint by name, EVA number or DS100 code, select the correct EVA ID, then save it in the account-wide library. New connections select both endpoints from dropdowns of saved stations, without repeating an API search. Configure an optional station display name without changing the official DB name or EVA ID.
- **Three original departure timestamp sensors** (unchanged unique IDs for existing dashboards) plus **18 new dedicated values**: for each of three trains a departure time, local date, line (e.g. `S6`), final terminus (e.g. `Tutzing`), platform and delay in minutes.
- **Summary sensors**: count, last successful update, average delay in minutes (arithmetic mean of the up to three upcoming, non-cancelled trips; empty result = unknown), and a full route JSON snapshot.
- **Manual refresh**: One refresh button per connection forces new planned and realtime API requests, bypassing normal caches, while respecting quota limits.
- **JSON for automation**: The sensor state is a small departure count; `data` is the complete JSON-compatible dictionary, and `json` is the serialized JSON string (sensor states cannot safely hold large JSON documents). The snapshot includes official station names, EVA IDs, friendly display names, API status, timestamps, train details, delay mean and paths.
- **Options per connection**: Name, departure station, destination, refresh interval **1–60 minutes** (default **1 minute**), time horizon **1–8 hours** (default **4 hours**), and whether cancelled trains are shown.
- **Live changes**: Merges `/plan/...` with `/fchg/...` for delays, cancellations, changed platforms and route changes. Identical origin-station requests are shared across connections.
- **Efficient and considerate**: 30-minute plan cache, 45-second change cache and account-wide request safeguard at 55 calls/minute (DB free tier: 60 calls/minute).
- **No secrets in GitHub**: You enter your own Client ID and API key privately in the Home Assistant configuration wizard.
- German and English UI strings. Unavailable API data marks sensors unavailable; no invented departures.

## DB API prerequisite

1. Create an account and application at the [DB API Marketplace](https://developers.deutschebahn.com/).
2. Subscribe that application to the **Timetables** API, including its available free tier.
3. Copy your **Client ID** and **API Key**. The request headers are `DB-Client-Id` and `DB-Api-Key`.
4. Keep your credentials private. Never put them in the public repository, issues or screenshots.

The official endpoints used are `GET /station/{pattern}`, `GET /plan/{evaNo}/{date}/{hour}` and `GET /fchg/{evaNo}` under `https://apis.deutschebahn.com/db-api-marketplace/apis/timetables/v1`.

## Install and updates

Updating the GitHub repository does not automatically install code on Home Assistant. Use HACS:

1. In HACS add this GitHub URL as a **custom repository** of type **Integration**: `https://github.com/niki11euro/home-assistant-db-fahrplan-integration`.
2. Download **DB Fahrplan**. Restart Home Assistant when prompted.
3. Go to **Settings → Devices & services → Add integration → DB Fahrplan** and enter your API credentials.
4. Open **Configure** on the DB Fahrplan API account and save at least two stations using **Search and save station**.
5. Add a new **Connection** subentry. Choose the start and destination from the two saved-station dropdowns; the EVA IDs are used automatically.
6. Repeat step 5 to add further routes. Each route can be edited and removed individually. Use **Configure** on the main account to manage the saved-station library and optional station display names.

Required: **Home Assistant 2026.3.0+** (native subentries and local integration brand assets). HACS is optional for a manual installation, but recommended for future update notifications.

### Example: Zorneding → München-Trudering

Create a connection with:

| Setting | Example |
| --- | --- |
| Name | Zorneding → Trudering |
| Start | `Zorneding` (select from saved stations) |
| Destination | `München-Trudering` (select from saved stations) |
| Refresh interval | `1` minute |
| Lookahead | `4` hours |
| Show cancelled | `No` |
| Historical averaging window | `30` days |

This will create a connection device with **Departure 1**, **Departure 2**, **Departure 3**, **Available departures**, **Last successful update**, **Historical average delay**, **Timetable JSON**, the 18 requested per-train detail sensors, and a **Refresh now** button. Each departure timestamp sensor has these attributes: `line`, `train`, `direction`, `platform`, `planned_platform`, `planned_departure`, `expected_departure`, `delay_minutes`, `minutes_until_departure`, `cancelled`, `stops`, and `stop_id`.

The destination must appear as a stop **after the origin** on the same train's route. Transfers are *not* planned. Routes that do not have a matching stop in the fetched horizon show fewer than three departures (or none).

### JSON snapshot for dashboards and automations

Each route has a `Fahrplan JSON` sensor. The **state is the current train count** (0 through 3); the `data` attribute contains a JSON-serializable object, and `json` contains the same payload as a JSON string. A sensor's state cannot safely hold an entire JSON document because Home Assistant caps state length at 255 characters.

Example (illustrative data, **not live times**):

```json
{
  "schema_version": 2,
  "available": true,
  "origin": {"eva": "8006671", "station_name": "Zorneding", "display_name": "Zorneding"},
  "destination": {"eva": "370920", "station_name": "Trudering, München", "display_name": "Trudering"},
  "refresh_interval_minutes": 1,
  "last_success": "2026-10-20T12:00:00+02:00",
  "last_attempt": "2026-10-20T12:00:00+02:00",
  "last_error": null,
  "departure_count": 1,
  "average_delay_minutes": null,
  "current_three_average_delay_minutes": 3.0,
  "history": {"period_days": 30, "sample_count": 0, "average_delay_minutes": null,
              "first_departure": null, "last_departure": null,
              "observation_method": "last_forecast_near_departure"},
  "departures": [
    {"number": 1, "line": "S6", "train": "S 6", "terminal": "Tutzing",
     "departure_date": "2026-10-20", "departure_time": "12:15",
     "departure_datetime": "2026-10-20T12:15:00+02:00",
     "scheduled_departure": "2026-10-20T12:12:00+02:00",
     "platform": "1", "planned_platform": "1", "delay_minutes": 3,
     "cancelled": false, "stop_id": "sample", "stops": ["Trudering", "München Ost", "Tutzing"]}
  ]
}
```

For Home Assistant templates, use `state_attr('sensor.<route>_fahrplan_json', 'data')` and access the `departures` list. In a dashboard, show the dedicated sensors directly. The `Refresh now` button can be pressed manually or through the normal `button.press` action. When the DB API is unreachable, the snapshot reports `available: false` and a non-sensitive `last_error`; the other sensors become unavailable instead of misleadingly reporting 0 trains.

### Historical average delay (v2.2.0+)

The **existing average-delay sensor** now reports a **rolling historical mean** (30 days by default, configurable from 1 to 365 days independently per connection). It is no longer the mean of the next three trains. Every unique DB departure (`stop_id`) can contribute **at most one** sample. The integration retains the last valid API-predicted delay observed shortly before that train's estimated departure and commits it to the history once the estimated departure has passed. The observation must have been made within 2–10 minutes before the train's expected departure (depending on refresh interval) to avoid using very old predictions. Cancelled or unknown-delay entries are not recorded as observations.

History is stored persistently for up to 365 days per connection using Home Assistant's supported `Store` API, including across Home Assistant restarts; it does not contain credentials. The selected averaging window filters these samples. Changing the window does not reset collected history. The `sample_count`, `period_days`, `first_departure`, `last_departure` and `observation_method` attributes expose coverage and the method. **Until at least one qualifying departure has been observed, the historical sensor is `unknown`, not `0`.** The integration cannot reliably recover earlier historical trips from the DB Timetables API; accumulation starts when this update is installed.

The DB API provides forecast and timetable changes, **not an independently verified actual departure time**. This historical average is therefore based on near-departure *predictions*, not certified measured actual delays. Occasional cancellations or changes reported after the last observation may be missed. Use a 1-minute refresh interval for the best coverage.

In the JSON sensor, `average_delay_minutes` is the **historical** value from this release onward (`schema_version: 2`); `current_three_average_delay_minutes` preserves the old instantaneous mean for dashboard comparisons. The nested `history` object supplies historical sample count and time window. A DB outage does not invalidate already collected historical samples.


## Data model and limitations

The **Timetables API is a station timetable, not a journey planner**. A connection is a *direct service calling at station Y*, not a guarantee of a ticketable trip or successful transfer. The route stores the exact selected **origin and destination EVA IDs**, always queries the **origin EVA ID**, and keeps the official DB station name separately from its user-friendly display name. The Timetables `ppth/cpth` route strings contain *station names, not EVA IDs*, so individual intermediate stops cannot be matched by ID through this API. The official destination name is matched to the path using conservative spelling variants (for example `Trudering, München`, `Trudering` and `München-Trudering`) to prevent false zero results. Display names never affect this filter.

- Planned train data is cached for 30 minutes; reported changes are fetched at least every configured interval (45-second cache for shared station polls).
- The search endpoint may return a limited set of results. Prefer a full official station name or EVA ID if an abbreviated search is ambiguous.
- API timetable data can be missing, delayed, incorrect or limited. The DB data license is [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), with the DB as data source.
- The DB Timetables free subscription states **60 requests per minute**. Every distinct origin consumes at least one change request per polling cycle; increasing the horizon or adding many stations increases demand. The integration stops making requests beyond its local quota safeguard and retries at the next poll.
- Because the timetable timestamps do not contain an explicit UTC offset, they are interpreted in **Europe/Berlin**. The repeated hour on daylight-saving time transitions is intrinsically ambiguous.
- If an express service has no intermediate stops in its `ppth/cpth` payload, that station cannot be reliably inferred as a stop. Cancelled services are hidden by default.

## Versioning and icon behavior

- HACS uses **GitHub Release** tags such as `v2.2.0` for version display. This repository publishes a release after its automated test suite passes when the manifest version changes on `main`.
- Home Assistant 2026.3+ loads the bundled branding: `brand/icon.png` (256 x 256).
- **HACS frontend limitation (2026.10):** The HACS update dialog can still display "icon not available" even though the bundled icon works in Home Assistant's integration view. Home Assistant Marketplace 2026.11 is expected to address this upstream behavior. Do not upgrade Home Assistant Core only to change the icon.
- After installing a new version via HACS, restart Home Assistant to load the new Python code. Existing API credentials, station library and route subentries remain configured.

## Repository structure

```text
custom_components/db_fahrplan/
  __init__.py        # account runtime, subentries
  api.py             # HTTP, caching, rate safeguard
  model.py           # XML parsing and direct-route filtering
  coordinator.py     # per-connection refresh
  config_flow.py     # API credentials, station library, route wizard
  sensor.py          # 25 sensors per route including JSON and averages
  button.py          # one manual refresh button per route
  payload.py         # JSON serialization and delay calculation
  history.py         # deduplicated historical sample collection and rolling mean
  const.py
  manifest.json
  strings.json
  translations/de.json
  translations/en.json
  brand/icon.png
hacs.json
.github/workflows/validate.yml
.github/workflows/tests.yml
tests/
```

## Developer validation

```sh
python -m pip install -r requirements-test.txt
python -m pytest -q
python -m compileall -q custom_components/db_fahrplan
```

Validation and HACS checks run automatically via GitHub Actions. No real API tokens are needed for unit tests. Live end-to-end testing against authenticated DB Timetables responses remains recommended.

## License & attribution

This integration's source code is MIT licensed. Railway timetable data is supplied by Deutsche Bahn under CC BY 4.0, subject to the API terms. The integration is unofficial and not affiliated with Deutsche Bahn or Home Assistant.

Developed with assistance from ChatGPT (OpenAI). Human testing and review are recommended before production use.
