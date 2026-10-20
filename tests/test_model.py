"""Timetable XML matching, realtime overlay, cancellation and midnight edge cases."""

from datetime import datetime
from zoneinfo import ZoneInfo

from custom_components.db_fahrplan.model import (
    normalize_station, parse_board, parse_db_time, parse_stations,
    select_departures,
)

BERLIN = ZoneInfo("Europe/Berlin")


def test_station_search_decodes_eva_and_name():
    xml = '<stations><station name="München Hbf" eva="8000261" ds100="MH"/><station name="Zorneding" eva="8006649" ds100="MZ"/></stations>'
    stations = parse_stations(xml)
    assert len(stations) == 2
    assert stations[0].name == "München Hbf"
    assert stations[1].eva == "8006649"
    assert "EVA" in stations[0].label


def test_names_normalize_without_matching_substrings():
    assert normalize_station("München Hauptbahnhof") == normalize_station("München Hbf")
    assert normalize_station("Trudering") != normalize_station("München-Trudering")


def test_xml_blocks_entity_expansion():
    import pytest
    from defusedxml.common import DefusedXmlException
    xml = '<!DOCTYPE a [<!ENTITY x "boom">]><stations><station name="&x;" eva="123"/></stations>'
    with pytest.raises(DefusedXmlException):
        parse_stations(xml)


def test_direct_route_and_realtime_merge():
    plan = parse_board('''<timetable station="Zorneding"><s id="one"><tl c="S" n="4"/><dp pt="2610201110" pp="2" l="S4" ppth="Eglharting|Kirchseeon|München-Trudering|München Hbf"/></s><s id="two"><tl c="S" n="6"/><dp pt="2610201112" pp="1" ppth="München Ost|München Hbf"/></s><s id="three"><tl c="S" n="4"/><dp pt="2610201120" pp="1" ppth="München-Trudering|München Hbf"/></s><s id="four"><tl c="S" n="4"/><dp pt="2610201130" pp="1" ppth="München-Trudering|München Hbf"/></s></timetable>''')
    changes = parse_board('''<timetable><s id="one"><dp ct="2610201118" cp="3"/></s><s id="three"><dp cs="c"/></s></timetable>''')
    departures = select_departures(
        plan, changes, "München-Trudering", datetime(2026,10,20,11,0,tzinfo=BERLIN)
    )
    assert len(departures) == 2
    assert departures[0].line == "S4"
    assert departures[0].platform == "3"
    assert departures[0].planned_platform == "2"
    assert departures[0].delay_minutes == 8
    assert departures[0].expected.hour == 11
    assert departures[0].expected.minute == 18
    assert departures[1].stop_id == "four"
    assert len(select_departures(plan, changes, "München-Trudering", datetime(2026,10,20,11,0,tzinfo=BERLIN), include_cancelled=True)) == 3


def test_diversion_changed_path_excludes_destination():
    plan = parse_board('<timetable><s id="x"><tl c="S" n="4"/><dp pt="2610201110" ppth="Trudering|München Hbf"/></s></timetable>')
    changes = parse_board('<timetable><s id="x"><dp cpth="Grafing|Ostbahnhof"/></s></timetable>')
    assert select_departures(plan, changes, "Trudering", datetime(2026,10,20,11,0,tzinfo=BERLIN)) == []


def test_added_trip_and_limit_to_next_three():
    base = '<timetable><s id="{id}"><tl c="S" n="4"/><dp {field}="{ts}" ppth="Trudering|München Hbf"/></s></timetable>'
    plan = {}
    for idx in range(4):
        plan.update(parse_board(base.format(id=idx,field="pt",ts=f"26102011{10 + idx*5:02d}")))
    changes = parse_board(base.format(id="added",field="ct",ts="2610201103"))
    result = select_departures(plan, changes, "Trudering", datetime(2026,10,20,11,0,tzinfo=BERLIN))
    assert [x.stop_id for x in result] == ["added", "0", "1"]


def test_midnight_timestamps_are_date_aware():
    plan = parse_board('<timetable><s id="n"><tl c="S" n="4"/><dp pt="2610210004" ppth="Trudering"/></s></timetable>')
    result = select_departures(plan, {}, "Trudering", datetime(2026,10,20,23,58,tzinfo=BERLIN), hours=1)
    assert len(result) == 1
    assert result[0].expected.day == 21
    assert parse_db_time("invalid") is None
    assert parse_db_time("2610210061") is None
