"""(the shared data part): names on two calendars are refused naming which names keep which calendar, in an answer an AI can
carry (the details had tuple keys: "keys must be str ... not tuple")."""
import asyncio
import json

import pytest
from talyx_sdk import ContractError

from modules.quant.backend import research_data


def test_names_on_two_calendars_are_named_with_their_calendars(monkeypatch):
    calendars = {"BTC/USDT": "24_7", "SPY": "XNYS", "IEF": "XNYS"}

    async def frozen(ctx, data, progress=None):
        return data, None

    async def resolve(ctx, instrument, timeframe, adjusted=True):
        return instrument, {"seriesId": instrument["displaySymbol"], "sessionScope": "regular"}

    async def nothing(*args, **kwargs):
        return None

    async def windows(ctx, instruments, timeframe, since, until):
        return {item["instrumentId"]: (since, until) for item in instruments}

    async def boundaries(ctx, calendar, timeframe, scope, start, end):
        return "UTC", [1, 2, 3] if calendar == "24_7" else [1, 3]
    monkeypatch.setattr(research_data, "frozen_data", frozen)
    monkeypatch.setattr(research_data, "resolve_series", resolve)
    monkeypatch.setattr(research_data, "preflight", nothing)
    monkeypatch.setattr(research_data.research_calendar, "date_windows", windows)
    monkeypatch.setattr(research_data.research_calendar, "boundaries", boundaries)
    data = {"symbols": [{"instrument": {"instrumentId": name, "displaySymbol": name, "calendarId": calendar}} for name, calendar in calendars.items()],
            "timeframe": "1d", "since": "2024-01-01", "until": "2024-06-30"}
    with pytest.raises(ContractError) as raised:
        asyncio.run(research_data.prepare(None, data, "owner", "unused"))
    assert "24_7 (regular): BTC/USDT" in raised.value.message and "XNYS (regular): SPY, IEF" in raised.value.message
    assert raised.value.details == {"calendars": {"24_7 (regular)": ["BTC/USDT"], "XNYS (regular)": ["SPY", "IEF"]}}
    json.dumps(raised.value.details)


def stub_calendars(monkeypatch, calendars):
    """Market as the calendar of each name (see `week`)."""
    async def frozen(ctx, data, progress=None):
        return data, None

    async def resolve(ctx, instrument, timeframe, adjusted=True):
        return instrument, {"seriesId": instrument["displaySymbol"], "sessionScope": "regular"}

    async def nothing(*args, **kwargs):
        return None

    async def windows(ctx, instruments, timeframe, since, until):
        return {item["instrumentId"]: (since, until) for item in instruments}

    async def boundaries(ctx, calendar, timeframe, scope, start, end):
        return schedules[calendar, scope]
    schedules = week()
    monkeypatch.setattr(research_data, "frozen_data", frozen)
    monkeypatch.setattr(research_data, "resolve_series", resolve)
    monkeypatch.setattr(research_data, "preflight", nothing)
    monkeypatch.setattr(research_data.research_calendar, "date_windows", windows)
    monkeypatch.setattr(research_data.research_calendar, "boundaries", boundaries)
    return {"symbols": [{"instrument": {"instrumentId": name, "displaySymbol": name, "calendarId": calendar}} for name, calendar in calendars.items()],
            "timeframe": "1d", "since": "2024-01-01", "until": "2024-12-31"}


def week():
    """2024 as Market gives it: XNYS sessions 09:30-16:00 New York on weekdays (holidays left out here), 24_7 days 00:00-24:00 UTC."""
    import pandas as pd
    days = pd.date_range("2024-01-01", "2024-12-31", freq="D")
    stock = [(int(pd.Timestamp(f"{day.date()} 09:30", tz="America/New_York").value // 1_000_000), int(pd.Timestamp(f"{day.date()} 16:00", tz="America/New_York").value // 1_000_000))
             for day in days if day.weekday() < 5]
    coin = [(int(day.tz_localize("UTC").value // 1_000_000), int((day + pd.Timedelta(days=1)).tz_localize("UTC").value // 1_000_000)) for day in days]
    return {("XNYS", "regular"): ("America/New_York", stock), ("24_7", "regular"): ("UTC", coin)}


def test_an_allocation_pool_of_stocks_and_crypto_counts_the_stock_days_a_year():
    """SPY and BTC are taken on the days both have a close, the stock days, so a year is the stock calendar's (design.md: A from
    Market's calendar, about 252 for US stocks; weekly and monthly alignment count 52 and 12 in the allocation itself)."""
    from modules.quant.backend import research_calendar
    since, until = research_data.day_ms("2024-01-01", False), research_data.day_ms("2024-12-31", True)
    (calendar, scope), shared = research_data.shared_calendar(week(), "1d")
    assert (calendar, scope) == ("XNYS", "regular") and len(shared) == 262   # 2024's weekdays, which the stand-in has no holidays on
    assert research_calendar.annualization(shared, since, until) == 261       # 262 in 366 days, a year of 365.25 days
    assert research_calendar.annualization(research_data.shared_calendar({("24_7", "regular"): week()["24_7", "regular"]}, "1d")[1], since, until) == 365


def test_an_allocation_takes_a_pool_on_two_calendars_and_a_factor_still_refuses_it(monkeypatch):
    class Captured(Exception):
        pass

    class Materials:
        def __init__(self, ctx):
            pass

        async def capture(self, request, owner):
            raise Captured(sorted(request["market"]["windows"]))
    data = stub_calendars(monkeypatch, {"BTC/USDT": "24_7", "SPY": "XNYS"})
    monkeypatch.setattr(research_data, "Materials", Materials)
    monkeypatch.setattr(research_data.M, "price_series", lambda instrument, timeframe, adjusted: {"seriesId": instrument["displaySymbol"] + ":raw"})
    with pytest.raises(ContractError, match="different calendars"):
        asyncio.run(research_data.prepare(None, data, "owner", "unused"))
    with pytest.raises(Captured):  # past the calendars, on to fetching the prices
        asyncio.run(research_data.prepare(None, data, "owner", "unused", calendars="many"))
