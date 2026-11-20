"""Trading dates, exact identities, table metadata and file output through Market's public handler."""

import asyncio

import numpy as np
import pandas as pd
import pytest

from modules.market.backend.actions import Actions
from modules.market.backend.app import Market
from modules.market.backend.common import MarketError
from modules.market.backend.public_data import make_series
from modules.market.backend.public_identity import instant
from modules.market.backend.store import SeriesKey
from vocabulary.instruments import MarketInstrument, ohlcv_series


def listing(market, code, venue, calendar, number):
    value = MarketInstrument(
        identityVersion=2,
        instrumentId=f"vi2_{number:032x}",
        venueId=venue,
        symbol=code,
        displaySymbol=code,
        assetClass="equity",
        marketType="spot",
        calendarId=calendar,
        currency="CNY" if venue == "CN" else "USD",
        listedFrom="1",
        adjustmentEligible=True,
    ).wire_dict()
    market.identity.adopt(value)
    return value


def seed(market, record, timeframe, times):
    series = make_series(record, {"timeframe": timeframe})
    key = SeriesKey(record["instrumentId"], "ohlcv", "last", series.sessionScope, timeframe)
    times = np.array(times, dtype=np.int64)
    step = int(pd.Timedelta("1h" if timeframe == "1h" else "1D").value)
    market.store.write(
        key,
        {
            "time": times,
            "close_time": times + step,
            **{name: np.ones(len(times)) for name in ("open", "high", "low", "close", "volume")},
        },
        fetched=[(int(times[0]), int(times[-1]) + step)],
    )


def test_mixed_markets_and_date_only_dividends(tmp_path):
    market = Market(None, tmp_path, sources=[])
    aapl = listing(market, "AAPL", "US", "XNYS", 1)
    moutai = listing(market, "600519.SH", "CN", "XSHG", 2)
    for record in (aapl, moutai):
        seed(market, record, "1d", [instant("2025-01-02", record["calendarId"])])
    result = asyncio.run(
        market.public.read(
            {"symbols": ["AAPL", "600519.SH"], "start": "2025-01-02", "end": "2025-01-02"}
        )
    )
    assert len(result["rows"]) == 2
    assert {m["symbol"]: m["adjustment"] for m in result["meta"]} == {
        "AAPL": "split",
        "600519.SH": "split",
    }
    assert ohlcv_series(MarketInstrument.from_wire(moutai), "1d").adjustment == "split_dividend"
    from types import SimpleNamespace

    import pyarrow.parquet as pq

    exported = asyncio.run(
        market.public.export(
            {
                "symbols": ["AAPL", "600519.SH"],
                "data": "bars",
                "timeframe": "1d",
                "start": "2025-01-02",
                "end": "2025-01-02",
                "fill": "none",
            },
            SimpleNamespace(workspace_id="test", progress=None),
        )
    )
    (file,) = exported["files"]
    assert file["content"] == "ohlcv:1d:split"
    assert set(pq.read_table(file["artifact"]["path"]).column("instrumentId").to_pylist()) == {
        aapl["instrumentId"],
        moutai["instrumentId"],
    }

    stamp = pd.Timestamp("2026-06-25T16:00Z").value
    market.actions.write(
        moutai["instrumentId"],
        [{"kind": "cash_dividend", "exDate": str(stamp), "amount": "25"}],
        start=stamp,
        end=stamp + 1,
        source="baostock",
        revision=1,
    )
    research = asyncio.run(
        market.public.research(
            {
                "data": "actions",
                "symbols": ["600519.SH"],
                "fill": "none",
                "start": "2026-06-26",
                "end": "2026-06-26",
            }
        )
    )
    ex_date = [c["name"] for c in research["columns"]].index("exDate")
    assert [row[ex_date] for row in research["rows"]] == ["2026-06-26"]


def test_perpetual_hourly_end_and_windows_file(tmp_path):
    market = Market(None, tmp_path, sources=[])
    record = MarketInstrument(
        venueId="okx",
        displaySymbol="BTCUSDT.P",
        symbol="BTC/USDT:USDT",
        marketType="swap",
        assetClass="crypto",
        base="BTC",
        quote="USDT",
        settle="USDT",
        contractOrientation="linear",
        contractSize="1",
    ).wire_dict()
    market.identity.upsert_contracts("okx", [record])
    times = pd.date_range("2025-01-02", periods=25, freq="h", tz="UTC").as_unit("ns").asi8
    seed(market, record, "1h", times)
    request = {
        "symbols": [record["instrumentId"]],
        "timeframe": "1h",
        "start": "2025-01-02",
        "end": "2025-01-02",
    }
    result = asyncio.run(market.public.read(request))
    assert len(result["rows"]) == 24
    assert pd.Timestamp(result["rows"][-1][1]) == pd.Timestamp("2025-01-02T23:00Z")
    from pathlib import Path

    result = asyncio.run(market.public.read({**request, "format": "file"}))
    assert Path(result["file"]["path"]).is_file() and result["total"] == 24


def test_symbol_roundtrip_with_duplicate_and_reassigned_codes(tmp_path):
    market = Market(None, tmp_path, sources=[])
    us = listing(market, "DASH", "US", "XNYS", 1)
    listing(market, "DASH", "COIN", "24_7", 2)
    match = asyncio.run(market.public.get({"symbols": [us["instrumentId"]]}))["items"][0]
    assert match["symbol"] == "US:DASH"
    assert asyncio.run(market.public.record(match["symbol"]))["instrumentId"] == us["instrumentId"]
    market.identity.set_code_periods(us["instrumentId"], "US", [("DASH", 1, 100)])
    listing(market, "DASH", "US", "XNYS", 3)
    assert market.public.spelling(us) == us["instrumentId"]


@pytest.mark.parametrize("status", [403, 451])
def test_regional_refusal_surfaces_immediately(status):
    import ccxt

    from modules.market.backend.sources.crypto import CryptoSource

    async def denied():
        raise ccxt.RateLimitExceeded(f"example GET https://example.test/ {status} denied")

    async def attempt():
        with pytest.raises(MarketError) as raised:
            await asyncio.wait_for(CryptoSource._call(None, "example", denied), 5)
        assert raised.value.status == status and raised.value.code == "forbidden"

    asyncio.run(attempt())


def test_historical_members_are_read_by_id_and_only_while_in_the_index(tmp_path, monkeypatch):
    market = Market(None, tmp_path, sources=[])
    index = listing(market, "^GSPC", "US", "XNYS", 1)
    dash = listing(market, "DASH", "US", "XNYS", 2)
    listing(market, "DASH", "COIN", "24_7", 3)
    apple = listing(market, "AAPL", "US", "XNYS", 4)
    stamps = [instant(day, "XNYS") for day in ("2025-01-02", "2025-01-03", "2025-01-06")]
    for record in (apple, dash):
        seed(market, record, "1d", stamps)

    async def membership(record, kind):
        assert record["instrumentId"] == index["instrumentId"] and kind == "members"
        return {
            "rows": [
                {"code": "DASH", "from": str(stamps[0]), "until": str(stamps[2])},
                {"code": "AAPL", "from": str(stamps[0]), "until": None},
                {"code": "LOST", "from": str(stamps[0]), "until": None},
            ]
        }

    monkeypatch.setattr(market.research, "ensure", membership)
    request = {"index": {"symbol": "^GSPC"}, "start": "2025-01-02", "end": "2025-01-06"}
    page = asyncio.run(market.public.read(request))
    assert page["missing"][0]["symbol"] == "LOST"
    assert {m["symbol"]: m["instrumentId"] for m in page["meta"]}["US:DASH"] == dash["instrumentId"]
    rows = page["rows"]
    assert [(r[0], r[1]) for r in rows if r[0] == "US:DASH"] == [
        ("US:DASH", "2025-01-02"),
        ("US:DASH", "2025-01-03"),
    ]
    rows = asyncio.run(
        market.public.read({**request, "index": {"symbol": "^GSPC", "at": "2025-01-06"}})
    )["rows"]
    assert len(rows) == 3 and {r[0] for r in rows} == {"AAPL"}

    async def reentry(record, kind):
        doc = await membership(record, kind)
        doc["rows"][0]["until"] = str(stamps[1])
        doc["rows"].append({"code": "DASH", "from": str(stamps[2]), "until": None})
        return doc

    monkeypatch.setattr(market.research, "ensure", reentry)
    result = asyncio.run(market.public.read({**request, "format": "file"}))
    table = pd.read_parquet(result["file"]["path"])
    periods = table.loc[table.instrumentId == dash["instrumentId"]]
    assert list(zip(periods.time.astype(str), periods.membershipPeriod, strict=True)) == [
        ("2025-01-02", 1), ("2025-01-06", 2)]
    assert result["missing"] == page["missing"]


def test_a_batch_missing_one_day_per_symbol_is_rejected_before_fetch(tmp_path, monkeypatch):
    market = Market(None, tmp_path, sources=[])
    records = [listing(market, f"NAME{i}", "US", "XNYS", i + 1) for i in range(500)]
    monkeypatch.setattr(market.data, "source_for", lambda *args: "daily-source")
    with pytest.raises(MarketError, match="market.fetch") as failure:
        asyncio.run(
            market.public.read(
                {
                    "symbols": [r["instrumentId"] for r in records],
                    "start": "2025-01-02",
                    "end": "2025-01-02",
                }
            )
        )
    assert failure.value.code == "fill_too_large"


def test_a_default_public_page_of_a_500_symbol_selection_fits_and_continues(tmp_path):
    import orjson
    from talyx_sdk.process import Codec

    from modules.market.backend.public_results import DataPage

    market = Market(None, tmp_path, sources=[])
    stamp = instant("2025-01-02", "XNYS")
    symbols = []
    for n in range(500):
        record = listing(market, f"NAME{n}", "US", "XNYS", n + 1)
        seed(market, record, "1d", [stamp])
        symbols.append(record["instrumentId"])
    query = {"symbols": symbols, "timeframe": "1d", "start": "2025-01-02", "end": "2025-01-02"}
    page = asyncio.run(market.public.read(query))
    first = orjson.loads(Codec(DataPage).check(page))
    assert first["total"] == 500 and len(orjson.dumps(first).decode()) <= 18_000
    count = len(first["rows"])
    assert [m["instrumentId"] for m in first["meta"]] == symbols[:count] and first["nextCursor"]
    second = asyncio.run(market.public.read({**query, "cursor": first["nextCursor"]}))
    assert second["meta"][0]["instrumentId"] == symbols[count]


def test_a_year_of_daily_bars_fits_in_one_inline_read(tmp_path):
    market = Market(None, tmp_path, sources=[])
    record = listing(market, "AAPL", "US", "XNYS", 1)
    stamps = [instant(str(day.date()), "XNYS") for day in pd.bdate_range("2025-01-02", periods=252)]
    seed(market, record, "1d", stamps)
    page = asyncio.run(
        market.public.read({"symbols": ["AAPL"], "start": "2025-01-02", "end": "2025-12-31"})
    )
    assert len(page["rows"]) == page["total"] == 252
    assert page["nextCursor"] is None and page["meta"][0]["instrumentId"] == record["instrumentId"]
    assert [c["name"] for c in page["columns"]][:2] == ["symbol", "time"]


def test_cash_dividend_requires_the_previous_sessions_raw_close(tmp_path, monkeypatch):
    from modules.market.backend.data import Fill

    market = Market(None, tmp_path, sources=[])
    record = listing(market, "CASH", "US", "XNYS", 1)
    day, ex = (instant(date, "XNYS") for date in ("2025-02-07", "2025-02-10"))
    minute = day + (9 * 60 + 30) * 60 * 10**9
    series = make_series(record, {"timeframe": "1m", "adjustment": "split_dividend"})
    plan = market.data.plan(record, series, minute)
    cols = {"time": np.array([minute]), "close_time": np.array([minute + 60 * 10**9]),
            **{key: np.array([100.]) for key in ("open", "high", "low", "close", "volume")}}
    market.store.write(plan.key, cols, fetched=[(minute, minute + 60 * 10**9)])
    market.actions.write(record["instrumentId"], [{"kind": "cash_dividend", "exDate": str(ex), "amount": "1"}],
                         start=day, end=ex + 1, source="fixture", revision=1)
    with pytest.raises(MarketError, match="reference close"):
        market.data.local_rows(plan, minute, minute + 1)
    fetched = []
    async def fill(daily, start, end, **kwargs):
        fetched.append((daily.base, start))
        market.store.write(daily.key, {**cols, "time": np.array([day]), "close_time": np.array([minute])},
                           fetched=[(start, end)])
        return Fill("done")
    monkeypatch.setattr(market.data, "fill", fill)
    asyncio.run(market.data.fill_reference_closes(plan, 50))
    assert fetched == [("1d", day)]
    assert market.data.local_rows(plan, minute, minute + 1)["close"].tolist() == [99]


def test_a_1985_us_dividend_takes_the_friday_session_before_its_monday_ex_date():
    # JNJ went ex-dividend on Monday 1985-11-18; Yahoo's history reaches back past 1990, where the calendar used to start.
    ex = int(pd.Timestamp("1985-11-18", tz="America/New_York").value)
    days = Actions.reference_days([{"exDate": str(ex), "kind": "cash_dividend", "amount": "0.85"}], "XNYS")
    assert days == {ex: int(pd.Timestamp("1985-11-15", tz="America/New_York").value)}
