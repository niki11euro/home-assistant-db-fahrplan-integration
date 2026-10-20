# DB Fahrplan for Home Assistant

A community Home Assistant custom integration for the **official Deutsche Bahn Timetables API**. Configure your own DB API credentials once and add as many independent direct train connections as you like, each with the next **three departures**. Designed for HACS custom repositories and the native Home Assistant UI.

> Development preview **v0.1.0**: unit tested without real DB credentials. A live DB account and Home Assistant installation have not been used for integration tests yet. Not an official Deutsche Bahn or Home Assistant product.

## Features

- **One API account, many connections**: Uses native Home Assistant *config subentries*. Each connection gets its own device and polling interval.
- **Station search and library**: Search the official DB endpoint by name, EVA number or DS100 code, select the correct EVA ID, then save it for reuse. Stations from newly created connections are remembered automatically.
- **Exactly three departure sensors per connection**: Timestamp state (estimated or planned) plus line, platform, scheduled departure, delay, destination, cancellation status, route and minutes until departure as attributes.
- **Two additional sensors**: Number of available departures and last successful refresh for each connection.
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

## Install (when you're ready)

This repository **does not install or change anything** on your Home Assistant instance merely by being created on GitHub.

When you explicitly decide to install it later:

1. In HACS add this GitHub URL as a **custom repository** of type **Integration**: `https://github.com/niki11euro/home-assistant-db-fahrplan-integration`.
2. Download **DB Fahrplan**. Restart Home Assistant when prompted.
3. Go to **Settings → Devices & services → Add integration → DB Fahrplan** and enter your API credentials.
4. Open the configured API account. Add a new **Connection** subentry, search for your stations and confirm their displayed EVA IDs.
5. Repeat step 4 to add further routes. Each route can be edited and removed individually. Use **Configure** on the main account to manage the saved-station library.

Required: **Home Assistant 2026.3.0+** (native subentries and local integration brand assets). HACS is optional for a manual installation, but recommended for future update notifications.

### Example: Zorneding → München-Trudering

Create a connection with:

| Setting | Example |
| --- | --- |
| Name | Zorneding → Trudering |
| Start | `Zorneding` (select official EVA match) |
| Destination | `München-Trudering` (select exact API station match) |
| Refresh interval | `1` minute |
| Lookahead | `4` hours |
| Show cancelled | `No` |

This will create a connection device with **Departure 1**, **Departure 2**, **Departure 3**, **Available departures**, and **Last successful update**. Each departure timestamp sensor has these attributes: `line`, `train`, `direction`, `platform`, `planned_platform`, `planned_departure`, `expected_departure`, `delay_minutes`, `minutes_until_departure`, `cancelled`, `stops`, and `stop_id`.

The destination must appear as a stop **after the origin** on the same train's route. Transfers are *not* planned. Routes that do not have a matching stop in the fetched horizon show fewer than three departures (or none).

## Data model and limitations

The **Timetables API is a station timetable, not a journey planner**. A connection is a *direct service calling at station Y*, not a guarantee of a ticketable trip or successful transfer. The API's departure `ppth/cpth` station names are matched with the selected destination station name, not with destination EVA IDs because the path string contains names. Common 'Hauptbahnhof/Hbf' spelling variants are normalized; unknown aliases are not assumed equal.

- Planned train data is cached for 30 minutes; reported changes are fetched at least every configured interval (45-second cache for shared station polls).
- The search endpoint may return a limited set of results. Prefer a full official station name or EVA ID if an abbreviated search is ambiguous.
- API timetable data can be missing, delayed, incorrect or limited. The DB data license is [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), with the DB as data source.
- The DB Timetables free subscription states **60 requests per minute**. Every distinct origin consumes at least one change request per polling cycle; increasing the horizon or adding many stations increases demand. The integration stops making requests beyond its local quota safeguard and retries at the next poll.
- Because the timetable timestamps do not contain an explicit UTC offset, they are interpreted in **Europe/Berlin**. The repeated hour on daylight-saving time transitions is intrinsically ambiguous.
- If an express service has no intermediate stops in its `ppth/cpth` payload, that station cannot be reliably inferred as a stop. Cancelled services are hidden by default.

## Repository structure

```text
custom_components/db_fahrplan/
  __init__.py        # account runtime, subentries
  api.py             # HTTP, caching, rate safeguard
  model.py           # XML parsing and direct-route filtering
  coordinator.py     # per-connection refresh
  config_flow.py     # API credentials, station library, route wizard
  sensor.py          # five sensors per route
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

Validation and HACS checks run automatically via GitHub Actions. No real API tokens are needed for unit tests. Live end-to-end testing with actual HA and authenticated DB responses remains necessary before a stable release.

## License & attribution

This integration's source code is MIT licensed. Railway timetable data is supplied by Deutsche Bahn under CC BY 4.0, subject to the API terms. The integration is unofficial and not affiliated with Deutsche Bahn or Home Assistant.

Developed with assistance from ChatGPT (OpenAI). Human testing and review are recommended before production use.
