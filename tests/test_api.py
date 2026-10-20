"""HTTP client tests with faked DB responses, without secrets or a live DB call."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.db_fahrplan.api import (
    DBAuthError, DBClient, DBAPIError, DBRateLimitError,
)


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self.body = body.encode("utf-8")

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
    session.fail_status = 403
    client = DBClient(session,"private-id", "private-secret")
    with pytest.raises(DBAuthError) as exc:
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
async def test_rate_safeguard_prevents_more_than_55_calls_in_a_minute():
    session = FakeSession()
    client = DBClient(session, "id", "key")
    for _ in range(55):
        await client._request("station/Trudering")
    with pytest.raises(DBRateLimitError):
        await client._request("station/Trudering")
    assert len(session.calls) == 55
