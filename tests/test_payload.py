"""JSON completeness, station identifiers, route data and delay mean."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from custom_components.db_fahrplan.model import Departure
from custom_components.db_fahrplan.payload import build_route_payload, average_delay_minutes

BERLIN = ZoneInfo("Europe/Berlin")


def train(number, delay=0, cancelled=False):
    timestamp = datetime(2026,10,20,11,number * 5,tzinfo=BERLIN)
    return Departure(
        stop_id=f"s{number}", line="S6", train="S 6", destination="Tutzing",
        scheduled=timestamp, expected=timestamp, platform=str(number),
        planned_platform="1", delay_minutes=delay, cancelled=cancelled,
        path=("Trudering", "München Ost", "Tutzing"),
    )


def test_payload_contains_three_trains_every_required_field_and_eva_ids():
    data = [train(1, 4), train(2, 0), train(3, 8)]
    stations = {
        "origin": {"eva": "8006671", "station_name": "Zorneding", "display_name": "Zorneding Bahnhof"},
        "destination": {"eva": "370920", "station_name": "Trudering, München", "display_name": "Trudering"},
        "refresh_interval_minutes": 1,
    }
    now = datetime(2026,10,20,11,0,tzinfo=BERLIN)
    result = build_route_payload(data, stations, now)
    encoded = json.dumps(result, ensure_ascii=False)
    assert json.loads(encoded) == result
    assert result["departure_count"] == 3
    assert result["average_delay_minutes"] is None
    assert result["current_three_average_delay_minutes"] == 4
    assert result["schema_version"] == 2
    assert result["history"]["sample_count"] == 0
    assert result["destination"]["eva"] == "370920"
    assert result["destination"]["station_name"] != result["destination"]["display_name"]
    assert result["departures"][0]["departure_date"] == "2026-10-20"
    assert result["departures"][0]["departure_time"] == "11:05"
    assert result["departures"][0]["line"] == "S6"
    assert result["departures"][0]["terminal"] == "Tutzing"
    assert result["departures"][0]["platform"] == "1"
    assert result["departures"][0]["delay_minutes"] == 4
    assert result["departures"][2]["delay_minutes"] == 8


def test_delay_mean_excludes_cancelled_and_no_services_is_none():
    assert average_delay_minutes([train(1,4),train(2,7,True),train(3,0)]) == 2.0
    assert average_delay_minutes([train(1,0,True)]) is None
    assert average_delay_minutes([]) is None
    empty = build_route_payload([], {"origin":{"eva":"1"},"destination":{"eva":"2"},"refresh_interval_minutes":2}, None)
    assert empty["departure_count"] == 0
    assert empty["average_delay_minutes"] is None
    assert empty["current_three_average_delay_minutes"] is None
    unavailable = build_route_payload([], {"origin":{"eva":"1"},"destination":{"eva":"2"},"refresh_interval_minutes":2}, None, available=False)
    assert unavailable["departure_count"] is None
    assert unavailable["available"] is False


def test_history_in_json_is_distinct_from_three_upcoming_predictions():
    route = {"origin": {"eva": "1"}, "destination": {"eva": "2"}, "refresh_interval_minutes": 1}
    history = {"period_days": 30, "sample_count": 25, "average_delay_minutes": 4.4,
               "first_departure": "2026-10-01T00:00:00+02:00",
               "last_departure": "2026-10-20T00:00:00+02:00",
               "observation_method": "last_forecast_near_departure"}
    p = build_route_payload([train(1, 8), train(2, 0)], route, None, history_summary=history)
    assert p["average_delay_minutes"] == 4.4
    assert p["current_three_average_delay_minutes"] == 4.0
    assert p["history"]["sample_count"] == 25
    failed = build_route_payload([], route, None, available=False, history_summary=history)
    assert failed["departure_count"] is None
    assert failed["average_delay_minutes"] == 4.4
    assert failed["current_three_average_delay_minutes"] is None
