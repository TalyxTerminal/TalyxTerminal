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


def test_existing_member_caches_keep_trading_dates_and_separate_monthly_weights(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from modules.market.backend.entry import upgrade_member_dates

    store = DocumentStore(tmp_path / "research", "members")
    day = ns_of_date("2018-09-03")
    row = {"code": "AAPL", "from": str(day), "until": None, "knownAt": str(day)}
    store.write("sp500", {"source": "sp500_history", "rows": [row]})
    weighted = {"source": "tushare", "rows": [{**row, "code": "000001.SZ", "weight": 0.8656}]}
    store.write("csi300", weighted)
    run(upgrade_member_dates(SimpleNamespace(platform=SimpleNamespace(storage=AsyncMock(
        return_value={"dataDir": str(tmp_path)})))))
    members = DocumentStore(tmp_path / "research", "members")
    assert members.read("sp500")["rows"][0]["from"] == str(day + 4 * 3600 * 10**9)
    assert members.read("csi300") is None and DocumentStore(tmp_path / "research", "weights").read("csi300") == weighted


def test_baostock_members_follow_rebalance_date(tmp_path, monkeypatch):
    async def scenario():
        market = Market(None, tmp_path, egress=ReplayEgress())
        await market.start(load_catalogs=False)
        try:
            listing = {"type": "listing", "market": "CN", "codeFrom": str(day_ns("2020-01-01"))}
            market.instruments.ingest([{**listing, "code": "000300.SH", "assetClass": "index"},
                                       *[{**listing, "code": c, "assetClass": "equity"} for c in
                                         ("600000.SH", "600132.SH", "600066.SH")]], "baostock")
            async def query(name, monday):
                assert name == "query_hs300_stocks"
                changed = {"2024-12-09": "sh.600132", "2024-12-16": "sh.600066"}[monday]
                return [], [[monday, code, ""] for code in ("sh.600000", changed)]

            monkeypatch.setattr(market.registry.entry("baostock").source.client, "query", query)
            for day, changed in (("2024-12-13", "600132.SH"), ("2024-12-16", "600066.SH")):
                page = await market.public.research({"data": "members", "index": {"symbol": "CN:000300.SH", "at": day}})
                col = [c["name"] for c in page["columns"]].index("code")
                assert {r[col] for r in page["rows"]} == {"600000.SH", changed}
                assert page["unmapped"] == 0
            records = await market.public.selections({"index": {"symbol": "CN:000300.SH"}, "start": "2024-12-13",
                                                      "end": "2024-12-16"})
            spans = {r["symbol"]: (a, b) for r, a, b in records}
            assert spans["600132.SH"][1] == spans["600066.SH"][0] == day_ns("2024-12-16")
        finally:
            await market.close()
    run(scenario())


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


def test_tushare_weights_and_daily_share_counts(tmp_path, monkeypatch):
    async def scenario():
        egress = ReplayEgress()
        market = Market(None, tmp_path, egress=egress)
        await market.start(load_catalogs=False)
        try:
            entry = {"type": "listing", "market": "CN", "codeFrom": str(day_ns("2005-01-01"))}
            market.instruments.ingest([{**entry, "code": "000300.SH", "assetClass": "index"},
                                       {**entry, "code": "000001.SZ", "assetClass": "equity"}], "baostock")
            async def membership(name, monday):
                assert name == "query_hs300_stocks"
                changed = "sz.000002" if monday == "2018-09-03" else "sh.600066"
                return [], [[monday, code, ""] for code in ("sz.000001", changed)]

            market.instruments.ingest([{**entry, "code": code, "assetClass": "equity"}
                                       for code in ("000002.SZ", "600066.SH")], "baostock")
            monkeypatch.setattr(market.registry.entry("baostock").source.client, "query", membership)
            # Tushare index_weight documentation, example for 2018-09-03 (percent).
            def response(_url, params):
                if params["api_name"] == "index_weight":
                    assert params["index_code"] == "399300.SZ"
                    fields, values = ["index_code", "con_code", "trade_date", "weight"], [
                        ["399300.SZ", "000001.SZ", "20180903", 0.8656], ["399300.SZ", "000002.SZ", "20180903", 1.133]]
                else:
                    assert params["api_name"] == "daily_basic" and params["ts_code"] == "000001.SZ"
                    fields, values = ["trade_date", "total_share", "float_share"], [["20180903", 1908.1234, 987.65]]
                return {"code": 0, "data": {"fields": fields, "items": values}}

            egress.add("api.tushare.pro", response)
            await market.admin.configure("tushare", {"token": "sample"}, allow_secrets=True)
            for day, expected in (("2018-09-07", {"000001.SZ": 0.8656, "000002.SZ": 1.133}),
                                  ("2018-09-10", {"000001.SZ": 0.8656, "600066.SH": None})):
                index = {"symbol": "CN:000300.SH", "at": day}
                page = await market.public.research({"data": "members", "index": index})
                code = [col["name"] for col in page["columns"]].index("code")
                weight = page["columns"].index({"name": "weight", "unit": "percent"})
                assert page["unmapped"] == 0 and {r[code]: r[weight] for r in page["rows"]} == expected
                assert {r["symbol"] for r, _, _ in await market.public.selections({"index": index})} == set(expected)
            doc = await market.research.read({"kind": "shares", "instruments": [await stock(market, "000001.SZ")],
                                              "start": str(day_ns("2018-09-03")), "end": str(day_ns("2018-09-04"))})
            assert dict(zip(doc["columns"]["kind"], doc["columns"]["shares"], strict=True)) == {
                "outstanding": "19081234", "float": "9876500"}
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
            terms = await market.terms.read({"instruments": [by_code["00700.HK"], by_code["00005.HK"]]})
            assert [(r["minQuantity"], r["quantityIncrement"]) for r in terms["items"]] == [
                ("100", "100"), ("400", "400")]
            # Upgraded from a version that read the list without keeping board lots: the terms read it again.
            (tmp_path / "board-lots" / "HK.json").unlink()
            again = await market.terms.read({"instruments": [by_code["00700.HK"]]})
            assert again["items"][0]["minQuantity"] == "100"
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



def test_a_stocks_industry_over_a_window_comes_from_the_dated_whole_market_answers(tmp_path, monkeypatch):
    """600519.SH as classified on each quarter's date: A in the answers up to 2021, B from 2022. Read over 2018-2022 it is A from the date
    A was known until the date B was, and B after; nothing later than a date's answer is used for it."""
    BaostockReplay(monkeypatch, "cn")
    from modules.market.backend.research import _day, snapshot_days

    async def scenario():
        market = await open_cn(tmp_path)
        try:
            record = await stock(market, "600519.SH")
            start, end = ns_of_date("2018-01-02"), ns_of_date("2022-12-31")
            days = snapshot_days(start, end)
            assert days[:3] == [ns_of_date("2018-01-02"), ns_of_date("2018-03-31"), ns_of_date("2018-06-30")] and days[-1] == ns_of_date("2022-12-31")
            for day in days:
                name, known = ("A", day_ns("2018-01-01")) if day < ns_of_date("2022-01-01") else ("B", day_ns("2022-03-28"))
                market.research.documents["classification"].write(f"market-CN-{_day(day)}", {
                    "source": "baostock", "fetchedAt": 0, "revision": 1,
                    "rows": [{"stock": "600519.SH", "scheme": "csrc", "level": "industry", "code": name, "name": name,
                              "from": None, "until": None, "knownAt": str(known)}]})
            page = await market.research.read({"kind": "classification", "instruments": [record], "fill": "none",
                                               "start": str(start), "end": str(end)})
            rows = list(zip(page["columns"]["name"], page["columns"]["from"], page["columns"]["until"], strict=True))
            assert rows == [("A", str(day_ns("2018-01-01")), str(day_ns("2022-03-28"))), ("B", str(day_ns("2022-03-28")), None)]
            early = await market.research.read({"kind": "classification", "instruments": [record], "fill": "none",
                                                "start": str(start), "end": str(end), "asOf": str(ns_of_date("2020-01-01"))})
            assert early["columns"]["name"] == ["A"]  # as known in 2020
        finally:
            await market.close()
    run(scenario())


def test_a_refreshed_classification_keeps_what_was_known_before():
    from modules.market.backend.research import classification_history, merged_classification
    old = [{"scheme": "yahoo", "level": "sector", "code": "tech", "name": "Technology", "from": None, "until": None, "knownAt": "100"}]
    same = [{**old[0], "knownAt": "200"}]
    assert merged_classification(old, same) == old  # unchanged: the earlier row stays, known since its first answer
    moved = [{"scheme": "yahoo", "level": "sector", "code": "comm", "name": "Communication", "from": None, "until": None, "knownAt": "300"}]
    merged = merged_classification(old, moved)
    assert [(row["name"], row["from"], row["until"]) for row in merged] == [("Technology", None, "300"), ("Communication", "300", None)]
    row = {"scheme": "csrc", "level": "industry", "code": "C15", "name": "Liquor"}
    history = classification_history([("10", {**row, "knownAt": "10"}), ("20", {**row, "knownAt": "20"}), ("30", None),
                                      ("40", {**row, "code": "C16", "name": "Tobacco", "knownAt": "40"})])
    assert [(item["code"], item["from"], item["until"]) for item in history] == [("C15", "10", "30"), ("C16", "40", None)]
