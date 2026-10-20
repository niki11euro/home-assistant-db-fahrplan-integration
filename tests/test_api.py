"""HTTP client tests with faked DB responses, without secrets or a live DB call."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.db_fahrplan.api import (
    DBAuthError, DBAccessDeniedError, DBCooldownError, DBClient, DBAPIError, DBRateLimitError,
)


class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status = status
        self.body = body.encode("utf-8")
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def read(self):
        return self.body


class FakeSession:
    def __init__(self):
        self.calls = []
        self.fail_status = None

    def get(self, url, *, headers, timeout):
        assert "DB-Client-Id" in headers
        assert "DB-Api-Key" in headers
        self.calls.append(url)
        if self.fail_status:
            return FakeResponse(self.fail_status, "error")
        if "/station/" in url:
            return FakeResponse(200, '<stations><station name="Trudering" eva="8000001"/></stations>')
        if "/fchg/" in url:
            return FakeResponse(200, '<timetable><s id="a"><dp ct="2610201117" cp="3"/></s></timetable>')
        return FakeResponse(200, '<timetable><s id="a"><tl c="S" n="4"/><dp pt="2610201110" pp="1" ppth="Kirchseeon|Trudering|München Hbf"/></s></timetable>')


@pytest.mark.asyncio
async def test_account_shared_station_cache_and_three_departures():
    session = FakeSession()
    client = DBClient(session, "test-id", "not-a-real-key")
    now = datetime(2026,10,20,11,0,tzinfo=ZoneInfo("Europe/Berlin"))
    a, b = await asyncio.gather(
        client.departures("8006649", "Trudering", hours=2, now=now),
        client.departures("8006649", "Trudering", hours=2, now=now),
    )
    assert len(a) == 1
    assert a == b
    assert a[0].delay_minutes == 7
    assert a[0].platform == "3"
    assert len([x for x in session.calls if "/fchg/" in x]) == 1
    assert len([x for x in session.calls if "/plan/" in x]) == 3
    assert (await client.find_stations("Trudering"))[0].eva == "8000001"
    await client.find_stations("Trudering")
    assert len([x for x in session.calls if "/station/" in x]) == 1


@pytest.mark.asyncio
async def test_authentication_error_no_secret_in_error():
    session = FakeSession()
    session.fail_status = 401
    client = DBClient(session,"private-id", "private-secret")
    with pytest.raises(DBAuthError, match="HTTP 401") as exc:
        await client.find_stations("Berlin")
    assert "private-secret" not in str(exc.value)
    assert "private-id" not in str(exc.value)


@pytest.mark.asyncio
async def test_404_returns_no_stations():
    session = FakeSession()
    session.fail_status = 404
    client = DBClient(session,"id", "key")
    assert await client.find_stations("unknown") == []


@pytest.mark.asyncio
async def test_malformed_xml_is_reported_safely():
    class Malformed(FakeSession):
        def get(self, url, *, headers, timeout):
            return FakeResponse(200, "<invalid")
    client = DBClient(Malformed(), "id", "key")
    with pytest.raises(DBAPIError, match="malformed"):
        await client.find_stations("Berlin")


@pytest.mark.asyncio
async def test_rate_safeguard_prevents_more_than_45_calls_in_a_minute():
    session = FakeSession()
    client = DBClient(session, "id", "key")
    client._spacing = 0  # Avoid waiting for real-world pacing in tests.
    for _ in range(45):
        await client._request("station/Trudering")
    with pytest.raises(DBRateLimitError, match="local request limit"):
        await client._request("station/Trudering")
    assert len(session.calls) == 45
    usage = client.request_statistics
    assert usage["requests_last_minute"] == 45
    assert usage["request_limit_per_minute"] == 45
    assert usage["requests_since_start"] == 45
    assert usage["cooldown_seconds_remaining"] > 0


@pytest.mark.asyncio
async def test_manual_refresh_forces_changes_but_keeps_plan_cache():
    session = FakeSession()
    client = DBClient(session, "id", "key")
    now = datetime(2026, 10, 20, 11, 0, tzinfo=ZoneInfo("Europe/Berlin"))
    await client.departures("8006671", "Trudering, München", hours=1, now=now)
    count = len(session.calls)
    await client.departures("8006671", "Trudering, München", hours=1, now=now)
    assert len(session.calls) == count
    await client.departures("8006671", "Trudering, München", hours=1, now=now, force=True)
    assert len(session.calls) == count + 1
    assert len([url for url in session.calls if "/plan/" in url]) == 2
    assert len([url for url in session.calls if "/fchg/" in url]) == 2

@pytest.mark.asyncio
async def test_origin_to_destination_arrival_board_realtime_and_lost_destination():
    class TwoBoards(FakeSession):
        destination_fails = False
        def get(self, url, *, headers, timeout):
            self.calls.append(url)
            if "/fchg/8098263" in url:
                return FakeResponse(200, "<timetable/>")
            if "/fchg/8006671" in url:
                if self.destination_fails:
                    return FakeResponse(503, "temporarily unavailable")
                return FakeResponse(200, '<timetable><s id="abc-2610201311-28"><ar ct="2610201424"/></s></timetable>')
            if "/plan/8098263" in url:
                return FakeResponse(200, '<timetable><s id="abc-2610201311-16"><tl c="S" n="6665"/><dp pt="2610201348" l="S6" pp="1" ppth="München Ost|Zorneding|Grafing Bahnhof"/></s></timetable>')
            if "/plan/8006671" in url:
                return FakeResponse(200, '<timetable><s id="abc-2610201311-28"><tl c="S" n="6665"/><ar pt="2610201422" pp="2"/></s></timetable>')
            return FakeResponse(404, "nothing")

    now = datetime(2026,10,20,13,0,tzinfo=ZoneInfo("Europe/Berlin"))
    ok = TwoBoards()
    client = DBClient(ok, "id", "key")
    deps = await client.departures("8098263", "Zorneding", hours=1, now=now, destination_eva="8006671")
    assert len(deps) == 1
    assert deps[0].arrival_scheduled.strftime("%H:%M") == "14:22"
    assert deps[0].arrival_expected.strftime("%H:%M") == "14:24"
    assert deps[0].arrival_delay_minutes == 2
    assert any("/plan/8006671/" in url for url in ok.calls)

    failing = TwoBoards()
    failing.destination_fails = True
    broken_client = DBClient(failing, "id", "key")
    fallback = await broken_client.departures(
        "8098263", "Zorneding", hours=1, now=now, destination_eva="8006671"
    )
    assert len(fallback) == 1
    assert fallback[0].arrival_expected is None



@pytest.mark.asyncio
async def test_http_403_is_not_treated_as_bad_api_key_and_enters_shared_backoff():
    session = FakeSession()
    session.fail_status = 403
    client = DBClient(session, "private-id", "private-key")
    with pytest.raises(DBAccessDeniedError, match="HTTP 403") as exc:
        await client.find_stations("Trudering")
    assert "private-key" not in str(exc.value)
    assert "private-id" not in str(exc.value)
    before = len(session.calls)
    with pytest.raises(DBCooldownError, match="HTTP 403"):
        await client._request("fchg/8006671")
    assert len(session.calls) == before  # No additional HTTP request.
    usage = client.request_statistics
    assert usage["last_http_status"] == 403
    assert usage["http_403_count"] == 1
    assert usage["http_401_count"] == 0
    assert usage["cooldown_seconds_remaining"] >= 119
    assert "HTTP 403" in usage["cooldown_reason"]
    assert not any("private" in str(value) for value in usage.values())


@pytest.mark.asyncio
async def test_429_honors_retry_after_and_tracks_usage():
    class RetrySession(FakeSession):
        def get(self, url, *, headers, timeout):
            self.calls.append(url)
            return FakeResponse(429, "no quota", headers={"Retry-After": "180"})

    session = RetrySession()
    client = DBClient(session, "id", "key")
    with pytest.raises(DBRateLimitError, match="HTTP 429"):
        await client._request("fchg/8006671")
    usage = client.request_statistics
    assert usage["requests_last_minute"] == 1
    assert usage["requests_since_start"] == 1
    assert usage["http_429_count"] == 1
    assert usage["last_http_status"] == 429
    assert usage["cooldown_seconds_remaining"] >= 179
    with pytest.raises(DBCooldownError):
        await client._request("fchg/8000261")
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_503_server_error_is_temporary_and_backed_off():
    session = FakeSession()
    session.fail_status = 503
    client = DBClient(session, "id", "key")
    with pytest.raises(DBAPIError, match="HTTP 503"):
        await client._request("plan/8006671/261020/11")
    usage = client.request_statistics
    assert usage["http_5xx_count"] == 1
    assert usage["http_401_count"] == 0
    assert usage["cooldown_seconds_remaining"] >= 29
    with pytest.raises(DBCooldownError):
        await client._request("plan/8006671/261020/12")
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_usage_is_account_wide_and_no_secret_is_exposed():
    client = DBClient(FakeSession(), "super-secret-client", "super-secret-key")
    await client._request("station/Zorneding")
    await client._request("station/Trudering")
    stats = client.request_statistics
    assert stats["requests_last_minute"] == 2
    assert stats["requests_last_hour"] == 2
    assert stats["requests_since_start"] == 2
    assert stats["last_http_status"] == 200
    assert stats["cooldown_seconds_remaining"] == 0
    assert "super-secret" not in repr(stats)


@pytest.mark.asyncio
async def test_no_more_than_two_network_requests_in_flight():
    class SlowSession(FakeSession):
        active = 0
        maximum = 0

        def get(self, url, *, headers, timeout):
            self.calls.append(url)
            outer = self

            class SlowResponse(FakeResponse):
                async def __aenter__(self):
                    outer.active += 1
                    outer.maximum = max(outer.maximum, outer.active)
                    await asyncio.sleep(0.015)
                    return self

                async def __aexit__(self, *_):
                    outer.active -= 1

            return SlowResponse(200, "<timetable/>")

    session = SlowSession()
    client = DBClient(session, "id", "key")
    client._spacing = 0
    await asyncio.gather(*(client._request(f"plan/8006671/261020/{x:02d}") for x in range(8)))
    assert len(session.calls) == 8
    assert session.maximum == 2
