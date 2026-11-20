"""Two Market rules Quant depends on: the raw column of stock trading terms is raw, and a whole-market research read
knows only what was known by asOf."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import numpy as np

from modules.market.backend import calendars
from modules.market.backend.app import Market
from modules.market.backend.common import now_ns, ns_of_date
from modules.market.backend.research import _day
from tests.fixtures.market.replay import ReplayEgress
from vocabulary.instruments import MarketInstrument, ohlcv_series

NASDAQ = ("nasdaqtrader", "symbol-directory")


def run(coro):
    return asyncio.run(coro)


def test_stock_terms_raw_column_is_the_unadjusted_price_across_a_split(tmp_path):
    async def scenario():
        market = Market(None, tmp_path, egress=ReplayEgress(NASDAQ))
        await market.start(load_catalogs=False)
        await market.instruments.load("nasdaqtrader", "US", "listings")
        try:
            record = (await market.instruments.resolve({"text": "MSFT", "market": "US"}))["instrument"]
            start, end = ns_of_date("2024-06-03"), ns_of_date("2024-06-05")
            opens, closes = calendars.bar_grid(record["calendarId"], "1d", start, end)
            assert len(opens) == 2
            # Day 1 raw 100, day 2 a 1-for-2 split and raw 50: nothing changed economically.
            raw = ohlcv_series(MarketInstrument.from_wire(record), "1d", adjustment="raw")
            plan = market.data.plan(record, raw, None, local_only=True)
            market.data.store.write(plan.key, {
                "time": np.asarray(opens, dtype=np.int64), "close_time": np.asarray(closes, dtype=np.int64),
                **{name: np.array([100.0, 50.0]) for name in ("open", "high", "low", "close")},
                "volume": np.array([1000.0, 2000.0])}, fetched=[(0, now_ns())], source="yfinance")
            market.actions.write(record["instrumentId"], [{"exDate": str(int(opens[1])), "kind": "split", "ratio": 2.0}],
                                 start=0, end=now_ns(), source="yfinance", revision=1)
            series = ohlcv_series(MarketInstrument.from_wire(record), "1d", adjustment="split")
            found = await market.terms.read({"instruments": [record], "series": [series.to_dict()],
                                             "start": str(start), "end": str(end)})
            first, second = found["items"][0]["stock"]["sessions"]
            assert first["rawOHLC"] == ["100", "100", "100", "100"]
            assert first["adjustedOHLC"] == ["50", "50", "50", "50"]
            assert {key: Decimal(value) for key, value in first["priceMap"].items()} == \
                {"scale": Decimal("0.5"), "offset": 0, "maxError": 0}
            assert second["rawOHLC"] == second["adjustedOHLC"] == ["50", "50", "50", "50"]
            assert Decimal(second["priceMap"]["scale"]) == 1
        finally:
            await market.close()

    run(scenario())


def test_whole_market_classification_keeps_only_rows_known_by_as_of(tmp_path):
    async def scenario():
        market = Market(None, tmp_path, egress=ReplayEgress())
        at = ns_of_date("2024-07-02")
        market.research.documents["classification"].write(f"market-CN-{_day(at)}", {
            "source": "baostock", "fetchedAt": now_ns(), "revision": 1,
            "rows": [{"stock": "600519.SH", "scheme": "csrc", "level": "industry", "code": "C15", "name": "酒、饮料和精制茶制造业",
                      "from": None, "until": None, "knownAt": str(ns_of_date("2024-06-30"))}]})
        read = market.research.read
        before = await read({"kind": "classification", "market": "CN", "at": str(at), "fill": "none",
                             "asOf": str(ns_of_date("2024-01-01"))})
        after = await read({"kind": "classification", "market": "CN", "at": str(at), "fill": "none",
                            "asOf": str(ns_of_date("2024-07-01"))})
        assert before["columns"]["code"] == []
        assert after["columns"]["code"] == ["C15"]

    run(scenario())


def test_a_bar_stored_before_the_rule_is_read_with_high_and_low_widened_and_kept_as_stored(tmp_path):
    # MK-07: bars stored before the containment rule (low 101 above open 100, as the UA and HUBB bars in the trial data) were read as stored,
    # and a ten-year backtest failed on "low <= open" minutes in.
    async def scenario():
        market = Market(None, tmp_path, egress=ReplayEgress(NASDAQ))
        await market.start(load_catalogs=False)
        await market.instruments.load("nasdaqtrader", "US", "listings")
        try:
            record = (await market.instruments.resolve({"text": "MSFT", "market": "US"}))["instrument"]
            start, end = ns_of_date("2024-06-03"), ns_of_date("2024-06-05")
            opens, closes = calendars.bar_grid(record["calendarId"], "1d", start, end)
            raw = ohlcv_series(MarketInstrument.from_wire(record), "1d", adjustment="raw")
            plan = market.data.plan(record, raw, None, local_only=True)
            stored = {"time": np.asarray(opens, dtype=np.int64), "close_time": np.asarray(closes, dtype=np.int64),
                      "open": np.array([100.0, 50.0]), "high": np.array([102.0, 49.0]), "low": np.array([101.0, 48.0]),
                      "close": np.array([102.5, 50.0]), "volume": np.array([1000.0, 2000.0])}
            market.data.store.write(plan.key, stored, fetched=[(0, now_ns())], source="yfinance")
            read = market.data.local_rows(plan, start, end)
            assert read["open"].tolist() == [100.0, 50.0] and read["close"].tolist() == [102.5, 50.0]
            assert read["high"].tolist() == [102.5, 50.0] and read["low"].tolist() == [100.0, 48.0]
            assert market.data.store.read(plan.key, start, end)["low"].tolist() == [101.0, 48.0]
        finally:
            await market.close()

    run(scenario())
