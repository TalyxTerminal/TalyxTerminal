"""A daily date is the venue's session date; timestamp windows and UTC research labels remain explicit."""
import asyncio
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from modules.market.backend import calendars
from modules.quant.backend import market_capture
from modules.quant.backend import research_calendar as C
from modules.quant.backend.object_data import day_ms
from tests.unit.quant_runtime_fakes import adapt


def stamp(text):
    return pd.Timestamp(text).value // 1000000


def context():
    async def sessions(args):
        return calendars.sessions(args["calendarId"], date.fromisoformat(args["startDate"]), date.fromisoformat(args["endDate"]))
    async def times(args):
        return calendars.bar_times(args["calendarId"], args["timeframe"], args["sessionScope"], int(args["start"]), int(args["end"]),
                                   int(args["after"]) if args.get("after") else None, args["limit"])
    return adapt(SimpleNamespace(require=lambda name: SimpleNamespace(sessions=sessions, bar_times=times)))


def test_shanghai_two_selected_dates_are_the_two_frozen_calendar_dates():
    since, until = day_ms("2024-01-02", False), day_ms("2024-01-03", True)
    window = asyncio.run(C.date_window(context(), "XSHG", "1d", since, until))
    assert window == (stamp("2024-01-01T16:00:00Z"), stamp("2024-01-03T16:00:00Z") - 1)
    zone, found = asyncio.run(C.date_boundaries(context(), "XSHG", "1d", "regular", since, until))
    assert [C.label_of(opened, "1d", zone) for opened, closed in found] == [since, since + 86400000]
    assert [pd.Timestamp(closed, unit="ms", tz="UTC").tz_convert(zone).strftime("%Y-%m-%d %H:%M") for opened, closed in found] == ["2024-01-02 15:00", "2024-01-03 15:00"]


@pytest.mark.parametrize("since,until,start,stop,hours", [
    ("2024-03-08", "2024-03-11", "2024-03-08T05:00:00Z", "2024-03-12T04:00:00Z", 95),
    ("2024-11-01", "2024-11-04", "2024-11-01T04:00:00Z", "2024-11-05T05:00:00Z", 97)])
def test_us_day_window_converts_each_endpoint_across_dst(since, until, start, stop, hours):
    found = asyncio.run(C.date_window(context(), "XNYS", "1d", day_ms(since, False), day_ms(until, True)))
    assert found == (stamp(start), stamp(stop) - 1)
    assert found[1] + 1 - found[0] == hours * 3600000
    zone, days = asyncio.run(C.date_boundaries(context(), "XNYS", "1d", "regular", day_ms(since, False), day_ms(until, True)))
    assert [C.label_of(opened, "1d", zone) for opened, _ in days] == [day_ms(since, False), day_ms(until, False)]


def test_intraday_windows_keep_the_callers_actual_instants():
    assert asyncio.run(C.date_window(None, "XSHG", "1h", 123, 456)) == (123, 456)


def test_capture_groups_markets_by_actual_window_and_retains_requested_order(monkeypatch):
    instruments = [{"instrumentId": "cn", "calendarId": "XSHG"}, {"instrumentId": "us", "calendarId": "XNYS"}]
    selections = [{"instrument": item, "series": {"seriesId": item["instrumentId"] + ":daily"}} for item in instruments]
    since, until = day_ms("2024-01-02", False), day_ms("2024-01-03", True)
    windows = asyncio.run(C.date_windows(context(), instruments, "1d", since, until))
    calls = []
    async def capture(ctx, request, owner):
        calls.append((request["selections"][0]["instrument"]["instrumentId"], request["since"], request["until"]))
        return {"references": [], "manifest": {"selections": request["selections"], "gaps": [], "failures": []}}
    monkeypatch.setattr(market_capture, "capture_window", capture)
    found = asyncio.run(market_capture.capture(None, {"selections": selections, "since": since, "until": until, "windows": windows}, "test"))
    assert calls == [(item["instrumentId"], *windows[item["instrumentId"]]) for item in instruments]
    assert calls[0][1:] != calls[1][1:] and found["manifest"]["selections"] == selections
    assert found["manifest"]["requestedRange"] == {"since": since, "until": until}
