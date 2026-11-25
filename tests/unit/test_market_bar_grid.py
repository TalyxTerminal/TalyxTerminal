"""Bar times on exchange calendars and stored bars on the exchange's own slots
(Chart build criterion 2): every stored bar opens on a slot of ``bar_times`` and its
``closeTime`` is that slot's close."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from types import SimpleNamespace

import numpy as np
import pytest

from modules.market.backend import bars, calendars
from modules.market.backend.app import Market
from modules.market.backend.common import DAY_NS, MarketError, iso_of_ns, ns_of_date
from modules.market.backend.sources.yahoo import YahooSource
from tests.fixtures.market.replay import ReplayEgress, recorded
from vocabulary.instruments import MarketInstrument, ohlcv_series


def utc(text: str) -> int:
    return int(np.datetime64(text.rstrip("Z"), "ns").astype(np.int64))


def grid(calendar: str, timeframe: str, start: int, end: int) -> list[tuple[str, str]]:
    opens, closes = calendars.bar_grid(calendar, timeframe, start, end)
    return [(iso_of_ns(int(o)), iso_of_ns(int(c))) for o, c in zip(opens, closes, strict=True)]


def test_daily_bars_open_at_local_midnight_and_close_at_the_session_close():
    assert grid("XNYS", "1d", utc("2026-11-13T00:00"), utc("2026-11-14T00:00")) == [
        ("2026-11-13T05:00:00Z", "2026-11-13T21:00:00Z")]
    assert grid("XSHG", "1d", utc("2026-11-12T12:00"), utc("2026-11-13T12:00")) == [
        ("2026-11-12T16:00:00Z", "2026-11-13T07:00:00Z")]


def test_us_half_day_has_four_hourly_bars_ending_at_the_early_close():
    day = grid("XNYS", "1h", ns_of_date("2026-11-27"), ns_of_date("2026-11-28"))
    assert [o for o, _c in day] == ["2026-11-27T14:30:00Z", "2026-11-27T15:30:00Z", "2026-11-27T16:30:00Z",
                                    "2026-11-27T17:30:00Z"]
    assert day[-1][1] == "2026-11-27T18:00:00Z"


def test_hong_kong_hourly_bars_stop_at_the_lunch_break_and_the_close():
    assert grid("XHKG", "1h", ns_of_date("2026-11-13"), ns_of_date("2026-11-14")) == [
        ("2026-11-13T01:30:00Z", "2026-11-13T02:30:00Z"), ("2026-11-13T02:30:00Z", "2026-11-13T03:30:00Z"),
        ("2026-11-13T03:30:00Z", "2026-11-13T04:00:00Z"), ("2026-11-13T05:00:00Z", "2026-11-13T06:00:00Z"),
        ("2026-11-13T06:00:00Z", "2026-11-13T07:00:00Z"), ("2026-11-13T07:00:00Z", "2026-11-13T08:00:00Z")]
    minutes = grid("XHKG", "1m", ns_of_date("2026-11-13"), ns_of_date("2026-11-14"))
    assert minutes[-1] == ("2026-11-13T07:59:00Z", "2026-11-13T08:00:00Z")


def test_shanghai_calendar_covers_2027_with_the_published_holidays():
    for calendar in ("XSHG", "XSHE"):
        assert calendars.coverage(calendar)[1] >= date(2027, 12, 31)
    times = calendars.bar_times("XSHG", "1d", "regular", ns_of_date("2027-12-01"), ns_of_date("2028-01-01"),
                                None, 100)
    assert times["evidence"] == "verified"
    days = {s["date"] for s in calendars.sessions("XSHG", date(2027, 1, 1), date(2027, 12, 31))["sessions"]}
    # State Council notice for 2027 (国办发明电〔2026〕10号): weekday closures; make-up Saturdays stay closed.
    for closed in ("2027-01-01", "2027-02-05", "2027-02-12", "2027-04-05", "2027-05-03", "2027-05-05",
                   "2027-06-09", "2027-09-15", "2027-10-01", "2027-10-07", "2027-05-08", "2027-10-09"):
        assert closed not in days
    for open_day in ("2027-02-04", "2027-02-15", "2027-05-06", "2027-10-08", "2027-12-31"):
        assert open_day in days
    assert len(days) == 243


def test_hourly_bars_across_the_calendar_end_give_the_covered_sessions():
    """From the last covered day (2027-12-31) two days of hourly bars: that day's four, and the next day unknown, not an error."""
    start = utc("2027-12-30T16:00")   # 2027-12-31 00:00 Beijing
    times = calendars.bar_times("XSHG", "1h", "regular", start, start + 2 * DAY_NS, None, 100)
    assert times["evidence"] == "unknown" and times["coveredUntil"] == "2027-12-31"
    assert [iso_of_ns(int(value)) for value in times["open"]] == [
        "2027-12-31T01:30:00Z", "2027-12-31T02:30:00Z", "2027-12-31T05:00:00Z", "2027-12-31T06:00:00Z"]
    assert [iso_of_ns(int(value)) for value in times["close"]][-1] == "2027-12-31T07:00:00Z"


# ── stored bars: Yahoo's rows on the exchange's slots (recorded responses) ──

def yahoo_rows(symbol: str, interval: str, calendar: str, timeframe: str, day: str):
    entry = next(e for e in recorded("yfinance", "intraday")
                 if e["url"].endswith("/" + symbol) and e["params"]["interval"] == interval)
    source = YahooSource(SimpleNamespace(egress=None))
    columns = source._intraday(json.loads(entry["body"])["chart"]["result"][0], calendar, timeframe, {})
    return bars.window(columns, ns_of_date(day), ns_of_date(day) + DAY_NS)


def assert_on_slots(columns, calendar: str, timeframe: str) -> None:
    times, ends = columns["time"], columns["close_time"]
    opens, closes = calendars.bar_grid(calendar, timeframe, int(times[0]), int(times[-1]) + 1)
    slot = np.searchsorted(opens, times)
    assert (opens[slot] == times).all()
    assert (closes[slot] == ends).all()


def as_table(columns) -> list[tuple]:
    return [(iso_of_ns(int(t)), iso_of_ns(int(c)), float(o), float(h), float(lo), float(x), float(v))
            for t, c, o, h, lo, x, v in zip(*(columns[n] for n in bars.BAR_COLUMNS), strict=True)]


def test_hong_kong_closing_auction_joins_the_last_minute():
    rows = yahoo_rows("0700.HK", "1m", "XHKG", "1m", "2026-11-13")
    assert_on_slots(rows, "XHKG", "1m")
    assert len(rows["time"]) == 330
    # 15:59 bar (387.0 close, 94,267 shares) plus the 16:08 auction row (386.6, 31,493,453 shares).
    assert as_table(rows)[-1] == ("2026-11-13T07:59:00Z", "2026-11-13T08:00:00Z", 386.8, 387.0, 386.6, 386.6,
                                  31_587_720.0)


def test_hong_kong_hourly_bars_come_from_quarter_hours_on_the_exchange_slots():
    quarters = yahoo_rows("0700.HK", "15m", "XHKG", "15m", "2026-11-13")
    assert_on_slots(quarters, "XHKG", "15m")
    hours = bars.aggregate(quarters, "15m", "1h", "XHKG")
    assert_on_slots(hours, "XHKG", "1h")
    table = as_table(hours)
    assert [(o, c) for o, c, *_ in table] == [
        ("2026-11-13T01:30:00Z", "2026-11-13T02:30:00Z"), ("2026-11-13T02:30:00Z", "2026-11-13T03:30:00Z"),
        ("2026-11-13T03:30:00Z", "2026-11-13T04:00:00Z"), ("2026-11-13T05:00:00Z", "2026-11-13T06:00:00Z"),
        ("2026-11-13T06:00:00Z", "2026-11-13T07:00:00Z"), ("2026-11-13T07:00:00Z", "2026-11-13T08:00:00Z")]
    assert table[-1][5] == 386.6  # the closing auction price
    with pytest.raises(MarketError) as raised:  # Yahoo's 60m afternoon runs 12:30-16:30
        yahoo_rows("0700.HK", "60m", "XHKG", "1h", "2026-11-13")
    assert raised.value.code == "unsupported"


def test_hong_kong_hourly_reads_are_built_from_quarter_hours(tmp_path):
    async def scenario():
        market = Market(None, tmp_path, egress=ReplayEgress())
        await market.start(load_catalogs=False)
        try:
            market.instruments.ingest([{"type": "listing", "market": "HK", "code": "00700.HK", "name": "TENCENT",
                                        "assetClass": "equity", "currency": "HKD", "calendarId": "XHKG"}], "test")
            tencent = (await market.instruments.resolve({"text": "00700.HK"}))["instrument"]
            series = ohlcv_series(MarketInstrument.from_wire(tencent), "1h")
            plan = market.data.plan(tencent, series, None)
            assert (plan.source_id, plan.base) == ("yfinance", "15m")
        finally:
            await market.close()

    asyncio.run(scenario())


def test_us_closing_rows_join_the_last_slot():
    minutes = yahoo_rows("AAPL", "1m", "XNYS", "1m", "2026-11-13")
    assert_on_slots(minutes, "XNYS", "1m")
    assert len(minutes["time"]) == 390
    assert as_table(minutes)[-1][:2] == ("2026-11-13T20:59:00Z", "2026-11-13T21:00:00Z")
    assert as_table(minutes)[-1][5] == 356.27  # the zero-volume 16:00 row: the official close
    # Half day: Yahoo leaves 12:30 empty and puts the last half hour under 13:00, after the 13:00 close.
    half = yahoo_rows("AAPL", "60m", "XNYS", "1h", "2025-11-28")
    assert_on_slots(half, "XNYS", "1h")
    table = as_table(half)
    assert [o for o, *_ in table] == ["2025-11-28T14:30:00Z", "2025-11-28T15:30:00Z", "2025-11-28T16:30:00Z",
                                      "2025-11-28T17:30:00Z"]
    assert table[-1][1] == "2025-11-28T18:00:00Z"
    assert table[-1][2:6] == (277.06, 279.0, 276.9008, 278.61)


def test_us_latest_trade_row_during_the_session_joins_its_slot():
    # Yahoo's 60m answer on 2026-11-16 at 17:37 UTC: the hourly rows, then the latest trade with volume 0.
    stamps = [utc(t) // 1_000_000_000 for t in ("2026-11-16T14:30", "2026-11-16T15:30", "2026-11-16T16:30",
                                                  "2026-11-16T17:30", "2026-11-16T17:37:22")]
    quote = {"open": [350.0, 352.0, 355.0, 354.5, 354.9], "high": [353.0, 358.0, 361.0, 355.5, 354.9],
             "low": [349.0, 351.0, 353.0, 353.9, 354.9], "close": [352.0, 355.0, 354.6, 354.2, 354.9],
             "volume": [17_488_440, 34_182_332, 27_067_227, 337_084, 0]}
    source = YahooSource(SimpleNamespace(egress=None))
    rows = source._live_rows({"timestamp": stamps, "indicators": {"quote": [quote]}}, "XNYS", "1h")
    assert_on_slots(rows, "XNYS", "1h")
    assert as_table(rows)[-1] == ("2026-11-16T17:30:00Z", "2026-11-16T18:30:00Z", 354.5, 355.5, 353.9, 354.9,
                                  337_084.0)

def test_prepared_calendars_serve_their_aliases():
    """Market builds its exchanges' calendars on a worker thread at start; a code naming the same exchange (XNAS for
    XNYS, XSHE for XSHG) must find that calendar, not build another one on the loop."""
    calendars.prepare()
    for alias, name in (("XNAS", "XNYS"), ("XSHE", "XSHG")):
        assert calendars.exchange(alias) is calendars.exchange(name)
    assert {calendars.canonical(name) for name in calendars.EXCHANGES} == set(calendars.EXCHANGES)


def test_intraday_aggregation_keeps_the_bucket_containing_the_first_trade():
    times = np.array([utc("2025-04-01T13:35Z"), utc("2025-04-01T14:00Z")])
    values = np.array([100., 200.])
    source = {"time": times, "close_time": times + 300 * 10**9,
              **{name: values for name in ("open", "high", "low", "close", "volume")}}
    for count in (1, 2):
        result = bars.aggregate(bars.take(source, slice(count)), "5m", "30m", "XNYS")
        assert result["time"].tolist() == [utc("2025-04-01T13:30Z"), utc("2025-04-01T14:00Z")][:count]
        assert result["close"].tolist() == values[:count].tolist()


def test_weekly_page_uses_whole_bucket_and_separates_forming(tmp_path, monkeypatch):
    from modules.market.backend import data
    from tests.unit.test_market_trading_dates import listing

    market = Market(None, tmp_path, sources=[])
    record = listing(market, "TEST", "US", "24_7", 1)
    series = ohlcv_series(MarketInstrument.from_wire(record), "1w", adjustment="raw")
    start = ns_of_date("2025-04-07")
    plan = market.data.plan(record, series, start)
    # With no provider, explicitly store the seven daily bars of the Monday-Sunday period.
    from modules.market.backend.store import SeriesKey
    key = SeriesKey(record["instrumentId"], "ohlcv", "last", series.sessionScope, "1d")
    days = np.arange(7) * DAY_NS + start
    values = np.arange(1., 8.)
    market.store.write(key, {"time": days, "close_time": days + DAY_NS,
                             **{name: values for name in ("open", "high", "low", "close", "volume")}},
                       fetched=[(start, start + 7 * DAY_NS)])
    from dataclasses import replace
    plan = replace(plan, base="1d", key=key)
    monkeypatch.setattr(market.registry, "rank", lambda *args: [("fixture", {})])
    _, action_gaps = market.data.action_gaps(plan, start, start + 2 * DAY_NS)
    assert action_gaps == [(start, start + 7 * DAY_NS)]
    for end in (start + 2 * DAY_NS, start + 7 * DAY_NS):
        rows = market.data.local_rows(plan, start, end)
        assert rows["close"].tolist() == [7] and rows["volume"].tolist() == [28]
    monkeypatch.setattr(market.data, "plan", lambda *args, **kwargs: plan)
    # Only the first three daily bars have actually closed.
    market.store.replace(key, {"time": days[:3], "close_time": days[:3] + DAY_NS,
                               **{name: values[:3] for name in ("open", "high", "low", "close", "volume")}},
                         [[start, start + 3 * DAY_NS, "have"]], None)
    # With only three days in storage the historical week is incomplete.
    assert not len(market.data.local_rows(plan, start, start + 2 * DAY_NS)["time"])
    monkeypatch.setattr(data, "now_ns", lambda: start + 3 * DAY_NS)
    page = asyncio.run(market.data.page(record, series, start=start, end=start + 2 * DAY_NS, fill="none"))
    assert page["columns"]["time"] == []
    assert (page["forming"]["close"], page["forming"]["volume"]) == ("3", "6")
