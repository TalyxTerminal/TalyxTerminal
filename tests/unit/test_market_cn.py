"""Market 2.0 A shares and Hong Kong (build criteria 6 for 600519 and 22-25), replaying Baostock's
recorded answers at the library's boundary, the HKEX list, Yahoo and Tushare."""

from __future__ import annotations

import asyncio
import base64
import io
import threading

import openpyxl
import pytest

from modules.market.backend.app import Market
from modules.market.backend.common import ns_of_date
from modules.market.backend.sources.baostock import day_ns
from modules.market.backend.store import DocumentStore
from tests.fixtures.market.replay import BaostockReplay, ReplayEgress, recorded
from vocabulary.instruments import MarketInstrument, ohlcv_series

D = day_ns("2019-12-16")  # 000043.SZ became 001914.SZ


def run(coro):
    return asyncio.run(coro)


def series(record, adjustment=None, timeframe="1d"):
    return ohlcv_series(MarketInstrument.from_wire(record), timeframe, adjustment=adjustment).to_dict()


async def open_cn(root, egress=None, *, check_codes=False) -> Market:
    market = Market(None, root, egress=egress or ReplayEgress())
    await market.start(load_catalogs=False)
    source = market.registry.entry("baostock").source
    if not check_codes:
        source.after_catalog = lambda *_args: asyncio.sleep(0)
    await market.instruments.load("baostock", "CN", "listings")
    if not check_codes:
        del source.after_catalog
    return market


async def stock(market, code):
    return (await market.instruments.resolve({"text": code}))["instrument"]


async def read(market, record, start, end, adjustment=None, fill="await", timeframe="1d"):
    return await market.data.read({"instrument": record, "series": series(record, adjustment, timeframe), "fill": fill,
                                   "start": str(ns_of_date(start)), "end": str(ns_of_date(end))})


def row_of(page, day):
    index = page["columns"]["time"].index(str(day_ns(day)))
    return {name: values[index] for name, values in page["columns"].items()}


# ── 22. A-share code change ────────────────────────────────────────────────


def test_a_code_change_is_one_instrument_with_two_code_periods(tmp_path, monkeypatch):
    replay = BaostockReplay(monkeypatch, "cn")

    async def scenario():
        market = await open_cn(tmp_path)
        try:
            found = await market.instruments.search({"query": "000043", "markets": ["CN"], "includeInactive": True})
            old = found["results"][0]["instrument"]
            assert old["symbol"] == "000043.SZ" and old["active"] is False and old["delistedAt"] == str(D)
            new = await stock(market, "001914.SZ")
            assert new["instrumentId"] != old["instrumentId"]
            for record in (old, new):  # both codes carry the history before the change
                await read(market, record, "2019-12-02", "2019-12-21", "raw")
            assert len(market.store.keys(old["instrumentId"])) == 1

            source = market.registry.entry("baostock").source
            await source.after_catalog("baostock", "CN", "listings")
            identity = market.identity
            assert identity.survivor(old["instrumentId"]) == new["instrumentId"]
            got = await market.public.get({"symbols": [old["instrumentId"]], "include": ["codes"]})
            assert (got["items"][0]["previousId"], got["items"][0]["instrument"]["instrumentId"]) == (old["instrumentId"], new["instrumentId"])
            codes = [(c["code"], c["start"], c["end"]) for c in got["items"][0]["codes"]]
            assert codes == [("000043.SZ", "1994-09-28", "2019-12-15"), ("001914.SZ", "2019-12-16", None)]
            # One copy of the data stays: the old listing's is gone, the survivor keeps one daily series.
            assert market.store.keys(old["instrumentId"]) == []
            assert [key.timeframe for key in market.store.keys(new["instrumentId"])] == ["1d"]

            result = await market.instruments.search({"query": "000043", "markets": ["CN"]})
            assert len(result["results"]) == 1
            hit = result["results"][0]
            assert hit["instrument"]["instrumentId"] == new["instrumentId"]
            assert hit["formerCode"] == {"code": "000043.SZ", "until": str(D)}
            listed = identity.catalog_page("CN", kinds=("listing",), asset_classes=None, market_types=None,
                                           include_inactive=True, after=None, limit=1000)
            assert "000043.SZ" not in {r["symbol"] for r in listed}

            # A code whose old code the source never listed: the old code maps to nothing.
            assert identity.find_code("CN", "601313.SH", ns_of_date("2017-06-30")) is None
            sanliuling = await stock(market, "601360.SH")
            assert identity.codes(sanliuling["instrumentId"])[0]["from"] == str(day_ns("2012-01-16"))

            # The next directory load keeps the merge and asks nothing more about it.
            checks = replay.count("query_history_k_data_plus")
            await market.instruments.load("baostock", "CN", "listings")
            assert replay.count("query_history_k_data_plus") == checks
            assert [(c["code"], c["from"], c["until"]) for c in identity.codes(new["instrumentId"])] == [
                ("000043.SZ", str(day_ns("1994-09-28")), str(D)), ("001914.SZ", str(D), None)]
            assert identity.find_code("CN", "000043.SZ", ns_of_date("2019-12-02")) == new["instrumentId"]
            page = await read(market, new, "2019-12-02", "2019-12-21", "raw", fill="none")
            # 2019-12-03..12-20 (12-02's bar starts at Beijing midnight, before the UTC start): the rows held
            # under the old code before the change and under the new one after it, once.
            assert page["columns"]["time"][0] == str(day_ns("2019-12-03")) and len(page["columns"]["time"]) == 14
        finally:
            await market.close()
    run(scenario())


# ── 6. 600519, front adjusted by Baostock's factor ─────────────────────────


def test_600519_default_series_is_front_adjusted_with_baostocks_factor(tmp_path, monkeypatch):
    BaostockReplay(monkeypatch, "cn")

    async def scenario():
        market = await open_cn(tmp_path)
        try:
            moutai = await stock(market, "600519.SH")
            assert series(moutai)["adjustment"] == "split_dividend"
            # A search answer (Yahoo's English name) does not replace the exchange list's Chinese name.
            name = moutai["name"]
            market.instruments.ingest([{"type": "listing", "market": "CN", "code": "600519.SH",
                                        "name": "Kweichow Moutai Co., Ltd."}], "yfinance", names="fill")
            assert market.identity.record(moutai["instrumentId"])["name"] == name
            found = (await market.instruments.search({"query": name[-2:], "markets": ["CN"]}))["results"]
            assert moutai["instrumentId"] in [item["instrument"]["instrumentId"] for item in found]
            adjusted = await read(market, moutai, "2024-06-03", "2024-07-01")
            raw = await read(market, moutai, "2024-06-03", "2024-07-01", "raw", fill="none")
            assert row_of(raw, "2024-06-18")["close"] == "1521.5"
            # Only the 2024-06-19 dividend of 30.876 yuan falls in the window; CRSP would give 1,490.624,
            # Baostock's own factor (from the exchange's ex-dividend reference price) gives 1,490.6199.
            close = float(row_of(adjusted, "2024-06-18")["close"])
            assert abs(close - 1490.624) <= 0.01
            assert close == 1490.6199
            assert row_of(adjusted, "2024-06-19")["close"] == row_of(raw, "2024-06-19")["close"]
            actions = await market.research.read({"kind": "actions", "instruments": [moutai], "fill": "none",
                                                  "start": str(ns_of_date("2024-06-01")),
                                                  "end": str(ns_of_date("2024-07-01"))})
            assert actions["columns"]["kind"] == ["cash_dividend"]
            assert actions["columns"]["amount"] == ["30.876"]
            assert actions["columns"]["exDate"] == [str(day_ns("2024-06-19"))]
            factors = await market.research.read({"kind": "factors", "instruments": [moutai], "fill": "none"})
            assert factors["columns"]["basis"] == ["source"]
        finally:
            await market.close()
    run(scenario())


# ── 24. industries for the market at a date; shares by quarter, resumable ──


def test_industries_of_the_whole_market_come_from_one_query(tmp_path, monkeypatch):
    replay = BaostockReplay(monkeypatch, "cn")

    async def scenario():
        market = await open_cn(tmp_path)
        try:
            before = len(replay.queries)
            page = await market.research.read({"kind": "classification", "market": "CN", "limit": 5000,
                                               "at": str(ns_of_date("2015-06-30"))})
            # 2,853 stocks at that date as recorded on 11-10.
            assert len(page["columns"]["instrumentId"]) == 2853
            assert replay.queries[before:] == [q for q in replay.queries[before:] if "query_stock_industry" in q]
            assert len(replay.queries) - before == 1
            index = page["columns"]["instrumentId"].index((await stock(market, "600519.SH"))["instrumentId"])
            assert page["columns"]["scheme"][index] == "csrc" and page["columns"]["code"][index] == "C15"
            assert page["unmapped"] > 2000  # the recorded list is trimmed to a few dozen stocks
            again = await market.research.read({"kind": "classification", "market": "CN", "limit": 5000,
                                                "at": str(ns_of_date("2015-06-30"))})
            assert len(again["columns"]["code"]) == 2853 and len(replay.queries) - before == 1
        finally:
            await market.close()
    run(scenario())


def test_shares_by_quarter_resume_after_the_workspace_closes_with_one_login(tmp_path, monkeypatch):
    replay = BaostockReplay(monkeypatch, "cn")

    async def scenario():
        market = await open_cn(tmp_path)
        loop = asyncio.get_running_loop()
        release = threading.Event()
        closed = asyncio.Event()

        def before(key):
            if "query_profit_data" in key and replay.count("query_profit_data") == 6:
                async def close():
                    await market.jobs.workspace_closed("w")
                    closed.set()
                loop.call_soon_threadsafe(lambda: asyncio.ensure_future(close()))
                release.wait(10)  # the sixth answer arrives after the job was interrupted

        try:
            records = [await stock(market, code) for code in ("600519.SH", "000001.SZ", "600036.SH")]
            value = {"targets": [{"instrument": r} for r in records], "kinds": [{"kind": "shares"}],
                     "start": "2023-01-01", "end": "2023-12-31"}
            replay.before = before
            first = await market.jobs.fetch(value, workspace_id="w")
            await closed.wait()
            assert first["state"] == "interrupted"
            # Each stock commits its own quarters. Starting the sixth source request does not order
            # the first five writes across the three stocks. Read a fresh store to check what survived.
            disk = DocumentStore(tmp_path / "research", "shares")
            committed = {(r["symbol"], q) for r in records for q in (disk.read(r["instrumentId"]) or {}).get("parts", [])}
            expected = {(r["symbol"], f"2023Q{q}") for r in records for q in range(1, 5)}
            assert 0 < len(committed) <= 5
            before = len(replay.requests)
            release.set()
            market.jobs.workspace_opened("w")
            job = market.jobs.get(first["jobId"])
            await asyncio.wait_for(job.done.wait(), 30)
            assert job.record["state"] == "completed"
            resumed = []
            for key in replay.requests[before:]:
                if key.startswith("query_profit_data\x01"):
                    code, year, quarter = key.split("\x01")[-3:]
                    exchange, symbol = code.split(".")
                    resumed.append((f"{symbol}.{exchange.upper()}", f"{year}Q{quarter}"))
            assert sorted(resumed) == sorted(expected - committed)
            docs = [market.research.stored(r["instrumentId"], "shares") for r in records]
            assert all(doc["parts"] == ["2023Q1", "2023Q2", "2023Q3", "2023Q4"] for doc in docs)
            assert sum(1 for key in replay.requests if key.startswith("login\x01")) == 1
            page = await market.research.read({"kind": "shares", "instruments": [records[0]], "fill": "none",
                                               "start": str(ns_of_date("2023-12-01")),
                                               "end": str(ns_of_date("2024-01-01"))})
            assert dict(zip(page["columns"]["kind"], page["columns"]["shares"], strict=True)) == \
                {"outstanding": "1256197800", "float": "1256197800"}
            assert page["columns"]["knownAt"][0] == str(day_ns("2024-04-03"))
        finally:
            release.set()
            await market.close()
    run(scenario())


def test_interrupt_waits_for_a_quarter_being_written_before_it_can_be_requested_again(tmp_path, monkeypatch):
    replay = BaostockReplay(monkeypatch, "cn")

    async def scenario():
        market = await open_cn(tmp_path)
        release = threading.Event()
        started = asyncio.Event()
        loop = asyncio.get_running_loop()
        store = market.research.documents["shares"]
        write = store.write

        def hold_second(instrument_id, doc):
            if doc["parts"] == ["2023Q1", "2023Q2"]:
                loop.call_soon_threadsafe(started.set)
                assert release.wait(10), "The test did not release the quarterly write."
            write(instrument_id, doc)

        monkeypatch.setattr(store, "write", hold_second)
        try:
            record = await stock(market, "600519.SH")
            span = {"start": ns_of_date("2023-01-01"), "end": ns_of_date("2024-01-01")}
            fetching = asyncio.create_task(market.research.ensure(record, "shares", **span))
            await asyncio.wait_for(started.wait(), 10)
            fetching.cancel()
            await asyncio.sleep(0)  # deliver cancellation while the actual disk writer is blocked
            assert not fetching.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await fetching
            assert DocumentStore(tmp_path / "research", "shares").read(record["instrumentId"])["parts"] == ["2023Q1", "2023Q2"]
            result = await market.research.ensure(record, "shares", **span)
            assert result["parts"] == ["2023Q1", "2023Q2", "2023Q3", "2023Q4"]
            assert replay.count("query_profit_data") == 4
        finally:
            release.set()
            await market.close()

    run(scenario())


# ── 25. Tushare: the whole market by date ──────────────────────────────────

FIELDS = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount"]


def tushare_day(_url, params):
    # Tushare's documented layout with 600519's values as Baostock gives them for 2024-06-18
    # (volume in lots, amount in thousand yuan); the trial token cannot fetch the real answer.
    assert params == {"api_name": "daily", "trade_date": "20240618"}
    items = [["600519.SH", "20240618", 1530.89, 1554.54, 1516.68, 1521.5, 1541.5, -20.0, -1.2975, 41258.25,
              6317590.9427],
             ["000001.SZ", "20240618", 10.25, 10.35, 10.22, 10.3, 10.27, 0.03, 0.2921, 1102346.62, 1134811.556],
             ["600036.SH", "20240618", 33.8, 34.12, 33.65, 33.92, 33.87, 0.05, 0.1476, 524138.0, 1776514.27],
             ["300750.SZ", "20240618", 180.5, 182.0, 178.81, 179.6, 180.36, -0.76, -0.4214, 191842.23, 3446311.4]]
    return {"request_id": "test", "code": 0, "msg": "", "data": {"fields": FIELDS, "items": items, "has_more": False}}


def test_tushare_takes_a_share_daily_when_configured_with_one_request_for_the_day(tmp_path, monkeypatch):
    BaostockReplay(monkeypatch, "cn")

    async def scenario():
        reference = await open_cn(tmp_path / "baostock")
        try:
            moutai = await stock(reference, "600519.SH")
            assert reference.registry.rank(moutai, "bars", "1d")[0][0] == "baostock"
            await read(reference, moutai, "2024-06-03", "2024-07-01")
            baostock_row = row_of(await read(reference, moutai, "2024-06-03", "2024-07-01", "raw", fill="none"),
                                  "2024-06-18")
        finally:
            await reference.close()

        egress = ReplayEgress()
        egress.add(lambda url, params: url == "https://api.tushare.pro", tushare_day)
        market = await open_cn(tmp_path / "tushare", egress)
        try:
            await market.registry.entry("tushare").source.configure("tushare", {"token": "test-token"})
            records = [await stock(market, code) for code in ("600519.SH", "000001.SZ", "600036.SH")]
            assert market.registry.rank(records[0], "bars", "1d")[0][0] == "tushare"
            assert market.registry.rank(records[0], "bars", "1h")[0][0] == "baostock"
            result = await market.jobs.fetch({"targets": [{"instrument": r} for r in records],
                                              "kinds": [{"kind": "bars", "timeframe": "1d"}],
                                              "start": "2024-06-18", "end": "2024-06-18"},
                                             workspace_id="w")
            assert result["completed"] == 3 and result["failureCount"] == 0
            assert egress.count("api.tushare.pro") == 1
            page = await read(market, records[0], "2024-06-17", "2024-06-19", "raw", fill="none")
            assert page["source"]["sourceId"] == "tushare"
            mine = row_of(page, "2024-06-18")
            assert mine["close"] == "1521.5" and mine["volume"] == "4125825"
            for name in ("time", "closeTime", "open", "high", "low", "close", "volume"):
                assert mine[name] == baostock_row[name], name
        finally:
            await market.close()
    run(scenario())


# ── 23. Hong Kong: the HKEX list and a reused code ─────────────────────────


def test_hkex_list_keeps_equities_etps_and_reits_and_00013_starts_at_its_first_bar(tmp_path):
    async def scenario():
        market = Market(None, tmp_path, egress=ReplayEgress(("hkex", "list"), ("yfinance", "hk")))
        await market.start(load_catalogs=False)
        try:
            await market.instruments.load("hkex", "HK", "listings")
            body = base64.b64decode(recorded("hkex", "list")[0]["bodyBase64"])
            sheet = openpyxl.load_workbook(io.BytesIO(body), read_only=True).active
            sheet.reset_dimensions()
            rows = [r for r in sheet.iter_rows(values_only=True) if r[0] and str(r[0]).isdigit()]
            kept = {f"{r[0]}.HK" for r in rows
                    if r[2] in ("Equity", "Exchange Traded Products", "Real Estate Investment Trusts")}
            left = {f"{r[0]}.HK" for r in rows} - kept
            assert left and kept
            listed = market.identity.catalog_page("HK", kinds=("listing",), asset_classes=None, market_types=None,
                                                  include_inactive=True, after=None, limit=1000)
            assert {r["symbol"] for r in listed} == kept
            by_code = {r["symbol"]: r for r in listed}
            assert by_code["00823.HK"]["securityType"] == "reit" and by_code["02800.HK"]["assetClass"] == "fund"
            assert by_code["80700.HK"]["currency"] == "CNY"

            hutchmed = await stock(market, "00013.HK")
            identity = market.identity
            assert identity.find_code("HK", "00013.HK", ns_of_date("2021-07-02")) is None  # first seen today
            page = await read(market, hutchmed, "2014-01-01", "2021-08-01", "raw")
            first = page["columns"]["time"][0]
            assert first == str(day_ns_hk("2021-06-30"))
            assert identity.codes(hutchmed["instrumentId"])[0]["from"] == first
            assert identity.find_code("HK", "00013.HK", ns_of_date("2014-01-02")) is None
            assert identity.find_code("HK", "00013.HK", ns_of_date("2021-07-02")) == hutchmed["instrumentId"]
        finally:
            await market.close()
    run(scenario())


def day_ns_hk(text: str) -> int:
    import numpy as np

    from modules.market.backend import calendars
    return int(calendars.local_midnight("XHKG", np.asarray([np.datetime64(text, "D")]))[0])

