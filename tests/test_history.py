"""Historical averages are built per unique departure, not per minute poll."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.db_fahrplan.history import DepartureDelayHistory
from custom_components.db_fahrplan.model import Departure

BERLIN = ZoneInfo("Europe/Berlin")
T0 = datetime(2026, 10, 20, 12, 0, tzinfo=BERLIN)


def departure(stop, expected, delay=0, cancelled=False):
    return Departure(
        stop_id=stop, line="S6", train=f"S 66{stop}", destination="Tutzing",
        scheduled=expected-timedelta(minutes=delay) if delay is not None else expected, expected=expected,
        platform="5", planned_platform="5", delay_minutes=delay,
        cancelled=cancelled, path=("Trudering", "Tutzing"),
    )


def test_mean_only_after_departure_not_next_three_at_every_poll():
    h=DepartureDelayHistory(30)
    scheduled=T0+timedelta(minutes=5)
    for minutes_before in (5,4,3,2,1):
        h.observe([departure('train1',scheduled,4)],scheduled-timedelta(minutes=minutes_before))
        assert h.summary(scheduled-timedelta(minutes=minutes_before))["sample_count"] == 0
    assert h.observe([], scheduled+timedelta(seconds=3))
    assert h.summary(scheduled+timedelta(seconds=3))["average_delay_minutes"] == 4
    assert h.summary(scheduled+timedelta(seconds=3))["sample_count"] == 1
    # 25 more polls and duplicates cannot inflate sample count.
    for i in range(25):
        h.observe([departure('train1', scheduled, 4)], scheduled+timedelta(minutes=i+1))
    assert h.summary(scheduled+timedelta(minutes=25))["sample_count"] == 1


def test_latest_near_departure_prediction_wins_and_multiple_trains_average():
    h=DepartureDelayHistory(30)
    for i,delay in enumerate((1,0,8)):
        expected=T0+timedelta(minutes=(i+1)*5)
        h.observe([departure(f'train{i}',expected,12)], expected-timedelta(minutes=2))
        h.observe([departure(f'train{i}',expected,delay)], expected-timedelta(minutes=1))
        h.observe([],expected+timedelta(seconds=1))
    s=h.summary(T0+timedelta(minutes=16))
    assert s['sample_count']==3
    assert s['average_delay_minutes']==3.0


def test_cancelled_or_unknown_delay_is_never_included():
    h=DepartureDelayHistory(30)
    scheduled=T0+timedelta(minutes=3)
    h.observe([departure('cancel',scheduled,5)],scheduled-timedelta(minutes=1))
    h.observe([departure('cancel',scheduled,5,True)],scheduled-timedelta(seconds=20))
    h.observe([],scheduled+timedelta(seconds=1))
    assert h.summary(scheduled+timedelta(seconds=2))['sample_count']==0
    h.observe([departure('missing',scheduled+timedelta(minutes=10),None)],scheduled)
    assert 'missing' not in h.pending


def test_very_early_forecasts_are_not_counted():
    h=DepartureDelayHistory(30)
    later=T0+timedelta(minutes=20)
    h.observe([departure('too_early',later,7)],T0)
    h.observe([],later+timedelta(minutes=1))
    assert h.summary(later+timedelta(minutes=1))['sample_count']==0


def test_persistence_across_restarts_and_adjustable_window():
    h=DepartureDelayHistory(30)
    a=T0+timedelta(minutes=4)
    h.observe([departure('persist',a,-1)], a-timedelta(minutes=1))
    restored=DepartureDelayHistory(30)
    restored.load(h.dump())
    restored.observe([], a+timedelta(seconds=2))
    end_state=restored.dump()
    new=DepartureDelayHistory(30)
    new.load(end_state)
    assert new.summary(a+timedelta(minutes=1))['average_delay_minutes']==-1.0
    assert new.summary(a+timedelta(minutes=1))['sample_count']==1
    # New rolling window has effect without deleting persisted observations.
    later=a+timedelta(days=31)
    assert new.summary(later)['sample_count']==0
    expanded=DepartureDelayHistory(90)
    expanded.load(new.dump())
    assert expanded.summary(later)['sample_count']==1


def test_history_pruned_only_after_maximum_retention():
    h=DepartureDelayHistory(30)
    old=T0-timedelta(days=366)
    h.records={'old': {'expected':old.isoformat(),'observed_at':old.isoformat(),'delay_minutes':17}}
    h.observe([], T0)
    assert h.records=={}


def test_malformed_storage_is_ignored_and_not_used_in_mean():
    h=DepartureDelayHistory(30)
    h.load({'records': {'bad': {'expected':'?','observed_at':'2026-10-20T12:00:00+02:00','delay_minutes':5},
                         'boolean': {'expected':T0.isoformat(),'observed_at':T0.isoformat(),'delay_minutes':True},
                         'valid': {'expected':T0.isoformat(),'observed_at':T0.isoformat(),'delay_minutes':2}},
            'pending': {'no': {'expected':'bad','observed_at':'bad','delay_minutes':42}}})
    assert h.summary(T0)['sample_count']==1
    assert h.summary(T0)['average_delay_minutes']==2


def test_invalid_history_window_rejected():
    with pytest.raises(ValueError):
        DepartureDelayHistory(0)
    with pytest.raises(ValueError):
        DepartureDelayHistory(366)


def test_old_pending_is_not_overwritten_with_timestamp_after_departure():
    h=DepartureDelayHistory(30)
    expected=T0+timedelta(minutes=4)
    h.observe([departure('last_second',expected,3)],expected-timedelta(minutes=1))
    # The API request may have started before the train departed but
    # completed a few seconds after its scheduled expected departure.
    h.observe([departure('last_second',expected,3)],expected+timedelta(seconds=3))
    assert h.summary(expected+timedelta(seconds=3))['sample_count']==1
