"""Market 2.0 data backbone: identity and search, adjustment, units and day boundaries,
fill estimates, errors (build criteria 1-5, 7-9, 11), replaying recorded source data."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from talyx_sdk.process import Call, Caller

from modules.market.backend import codes
from modules.market.backend.app import Market
from modules.market.backend.common import DAY_NS, MarketError, iso_of_ns, ns_of_date
from tests.fixtures.market.replay import ReplayEgress
from vocabulary.identity import instrument_id, series_id
from vocabulary.instruments import MarketInstrument, ohlcv_series

FIXTURES = Path(__file__).parents[1] / "fixtures" / "market"
YAHOO = ("yfinance", "chart-daily")
NASDAQ = ("nasdaqtrader", "symbol-directory")


def run(coro):
    return asyncio.run(coro)


async def open_market(root: Path, egress: ReplayEgress | None = None, *, catalogs=("US",)) -> Market:
    market = Market(None, root, egress=egress or ReplayEgress(YAHOO, NASDAQ))
    await market.start(load_catalogs=False)
    if "US" in catalogs:
        await market.instruments.load("nasdaqtrader", "US", "listings")
    if "okx" in catalogs:
        await market.instruments.load("okx", "okx", "contracts")
    return market


def by_day(page: dict) -> dict[str, int]:
    return {iso_of_ns(int(t))[:10]: i for i, t in enumerate(page["columns"]["time"])}


async def read(market, record, timeframe="1d", start=None, end=None, **options):
    series = ohlcv_series(MarketInstrument.from_wire(record), timeframe, adjustment=options.pop("adjustment", None),
                          adjustment_anchor=options.pop("anchor", None))
    value = {"instrument": record, "series": series.to_dict(), "limit": options.pop("limit", 10_000), **options}
    if start:
        value["start"] = str(ns_of_date(start) if isinstance(start, str) else start)
    if end:
        value["end"] = str(ns_of_date(end) if isinstance(end, str) else end)
    return await market.data.read(value)


# ── 1. search and Yahoo codes ──────────────────────────────────────────────


def test_search_finds_one_listing_and_yahoo_codes_are_translated(tmp_path):
    async def scenario():
        market = await open_market(tmp_path)
        try:
            results = (await market.instruments.search({"query": "AAPL", "limit": 20}))["results"]
            exact = [r for r in results if r["instrument"]["symbol"] == "AAPL"]
            assert len(exact) == 1
            assert exact[0]["instrument"]["identityVersion"] == 2
            assert exact[0]["instrument"]["venueId"] == "US"
            # Typing a code: every keystroke that already starts a local code stays local, no source is asked.
            asked = len(market.egress.requests)
            for typed in ("A", "AA", "AAP", "MSF", "BRK"):
                await market.instruments.search({"query": typed, "limit": 30})
            assert len(market.egress.requests) == asked
            brk = [r for r in (await market.instruments.search({"query": "BRK.B", "limit": 20}))["results"]
                   if r["instrument"]["symbol"] == "BRK.B"]
            assert len(brk) == 1
            await read(market, brk[0]["instrument"], start="2024-06-03", end="2024-06-08")
            assert market.egress.count("/v8/finance/chart/BRK-B") == 1
            assert market.egress.count("/v8/finance/chart/BRK.B") == 0
        finally:
            await market.close()

    run(scenario())
    expected = {("US", "ABR$D"): "ABR-PD", ("US", "AAC.U"): "AAC-UN", ("US", "AAC.W"): "AAC-WT",
                ("US", "AIIA.R"): "AIIA-RI", ("US", "BRK.B"): "BRK-B", ("CN", "600519.SH"): "600519.SS",
                ("HK", "00700.HK"): "0700.HK", ("HK", "80700.HK"): "80700.HK"}
    for (market, code), symbol in expected.items():
        assert codes.yahoo_symbol(market, code) == symbol
    assert codes.parse_typed("600519.SH") == ("CN", "600519.SH")
    assert codes.parse_typed("00700.HK") == ("HK", "00700.HK")


# ── 2. resolving an index found only by Yahoo ──────────────────────────────


def test_resolving_an_unlisted_index_asks_yahoo_once(tmp_path):
    async def scenario():
        market = await open_market(tmp_path)
        try:
            found = (await market.instruments.resolve({"text": "^GSPC"}))["instrument"]
            assert (found["venueId"], found["assetClass"], found["adjustmentEligible"]) == ("US", "index", False)
            assert found["identityVersion"] == 2
            asked = len(market.egress.requests)
            assert market.egress.count("/v1/finance/search") == 1
            again = (await market.instruments.resolve({"text": "^GSPC"}))["instrument"]
            assert again["instrumentId"] == found["instrumentId"]
            assert len(market.egress.requests) == asked
        finally:
            await market.close()

    run(scenario())


# ── 3. assigned ids: stable, portable, redirected, code periods ─────────────


def test_assigned_ids_survive_restart_travel_and_redirect(tmp_path):
    async def scenario():
        first = await open_market(tmp_path / "a")
        aapl = (await first.instruments.resolve({"text": "AAPL"}))["instrument"]
        await first.close()

        reopened = await open_market(tmp_path / "a")
        assert (await reopened.instruments.resolve({"text": "AAPL"}))["instrument"]["instrumentId"] == \
            aapl["instrumentId"]
        await read(reopened, aapl, start="2015-12-01", end="2016-01-01")
        periods = reopened.identity.codes(aapl["instrumentId"])
        assert [iso_of_ns(int(p["from"]))[:10] for p in periods if p["code"] == "AAPL"] == ["2015-12-01"]
        await reopened.close()

        empty = Market(None, tmp_path / "b", egress=ReplayEgress(YAHOO, NASDAQ))
        await empty.start(load_catalogs=False)
        got = await empty.public.get({"records": [aapl]})
        assert got["items"][0]["status"] == "found"
        assert got["items"][0]["instrument"]["instrumentId"] == aapl["instrumentId"]
        await empty.close()

        other = await open_market(tmp_path / "c")
        theirs = (await other.instruments.resolve({"text": "AAPL"}))["instrument"]
        assert theirs["instrumentId"] != aapl["instrumentId"]
        got = await other.public.get({"records": [aapl]})
        assert got["items"][0]["status"] == "redirect"
        assert got["items"][0]["previousId"] == aapl["instrumentId"]
        assert got["items"][0]["instrument"]["instrumentId"] == theirs["instrumentId"]
        assert await other.public.get({"symbols": [aapl["instrumentId"]]}) == got
        foreign = {**aapl, "instrumentId": "vi2_" + "1" * 32, "listedFrom": "1"}
        await read(other, theirs, start="2015-12-01", end="2016-01-01")
        with pytest.raises(MarketError) as conflict:
            await other.public.get({"records": [foreign]})
        assert conflict.value.code == "ambiguous"
        assert conflict.value.details["candidates"][0]["instrumentId"] == theirs["instrumentId"]
        assert other.identity.survivor(foreign["instrumentId"]) is None
        # A listing rebuilt from its fields without its id would be a contract on a venue "us": refused, not stored.
        rebuilt = MarketInstrument(**{k: v for k, v in aapl.items() if k not in {"identityVersion", "instrumentId"}})
        with pytest.raises(MarketError) as refused:
            await other.public.get({"records": [rebuilt.wire_dict()]})
        assert refused.value.code == "invalid_input"
        assert other.identity.survivor(rebuilt.instrumentId) is None
        await other.close()

    run(scenario())


# ── 4. contract ids do not change ──────────────────────────────────────────


def test_contract_ids_and_series_ids_are_unchanged():
    binance = MarketInstrument(venueId="binance", symbol="BTC/USDT:USDT", displaySymbol="BTC/USDT:USDT",
                               marketType="swap", assetClass="crypto", base="BTC", quote="USDT", settle="USDT",
                               contractOrientation="linear", contractSize="1", adapterId="ccxt",
                               nativeId="BTCUSDT")
    assert binance.instrumentId == "vi1_66e0139e58a547479ad1a5ef12c7d15517bfe17f70d80c6c59960e99e037bdbf"

    vectors = json.loads((Path(__file__).parents[2] / "vocabulary" / "identity-vectors.json").read_text("utf-8"))
    assert vectors["instruments"][0]["instrumentId"] == \
        "vi1_70247aa2c8a30777a2cf652a055c5dbbb5747650147c7c624c32d9aa57aa3de8"

    record = binance.to_dict()
    base = {"instrumentId": record["instrumentId"], "capability": "ohlcv", "timeframe": "1h", "priceBasis": "last",
            "sessionScope": "continuous", "adjustment": "split"}
    assert series_id({**base, "adjustmentAnchor": "latest"}) == series_id({**base, "adjustmentAnchor": None})

    stored = json.loads((FIXTURES / "stored-instruments-v1.json").read_text("utf-8"))
    assert len(stored) == 8
    for item in stored:
        fields = {key: value for key, value in item.items() if key != "instrumentId"}
        assert instrument_id(fields) == item["instrumentId"]
        assert MarketInstrument.from_wire(item).instrumentId == item["instrumentId"]


# ── 5. adjustment (AAPL 2020 split and dividends) ──────────────────────────


def test_aapl_raw_split_and_dividend_adjustment(tmp_path):
    async def scenario():
        market = await open_market(tmp_path)
        try:
            aapl = (await market.instruments.resolve({"text": "AAPL"}))["instrument"]
            window = {"start": "2020-08-03", "end": "2020-10-01"}

            raw = await read(market, aapl, adjustment="raw", **window)
            day = by_day(raw)
            close, volume = raw["columns"]["close"], raw["columns"]["volume"]
            assert (close[day["2020-08-28"]], volume[day["2020-08-28"]]) == ("499.23", "46907500")
            assert close[day["2020-08-31"]] == "129.04"
            assert close[day["2020-08-06"]] == "455.61"

            actions = market.actions.read(aapl["instrumentId"])["rows"]
            kinds = {(iso_of_ns(int(a["exDate"]))[:10], a["kind"]): a for a in actions}
            assert kinds[("2020-08-31", "split")]["ratio"] in ("4", "4.0")
            assert kinds[("2020-08-07", "cash_dividend")]["amount"] == "0.82"

            split = await read(market, aapl, adjustment="split", **window)
            assert split["columns"]["close"][by_day(split)["2020-08-28"]] == "124.8075"
            earliest = await read(market, aapl, adjustment="split", anchor="earliest", **window)
            assert earliest["columns"]["close"][by_day(earliest)["2020-08-31"]] == "516.16"
            both = await read(market, aapl, adjustment="split_dividend", **window)
            assert both["columns"]["close"][by_day(both)["2020-08-06"]] == "113.6975"

            longer = await read(market, aapl, adjustment="split_dividend", start="2020-08-03", end="2020-12-01")
            assert longer["revision"] == both["revision"] + 1
            assert longer["columns"]["close"][by_day(longer)["2020-08-06"]] == "113.5017"
        finally:
            await market.close()

    run(scenario())


def test_nvda_split_after_window(tmp_path):
    async def scenario():
        market = await open_market(tmp_path)
        try:
            nvda = (await market.instruments.resolve({"text": "NVDA"}))["instrument"]
            raw = await read(market, nvda, adjustment="raw", start="2024-06-03", end="2024-06-15")
            assert raw["columns"]["close"][by_day(raw)["2024-06-07"]] == "1208.88"
            split = await read(market, nvda, adjustment="split", start="2024-06-03", end="2024-06-15")
            assert split["columns"]["close"][by_day(split)["2024-06-07"]] == "120.888"
        finally:
            await market.close()

    run(scenario())


# ── 8. daily bar times on exchange calendars ───────────────────────────────


def test_daily_bars_start_at_local_midnight_and_close_at_the_session_close(tmp_path):
    async def scenario():
        market = await open_market(tmp_path)
        try:
            aapl = (await market.instruments.resolve({"text": "AAPL"}))["instrument"]
            page = await read(market, aapl, adjustment="raw", start="2020-08-03", end="2020-10-01")
            i = by_day(page)["2020-08-28"]
            assert page["columns"]["time"][i] == "1598587200000000000"
            assert page["columns"]["closeTime"][i] == "1598644800000000000"

            market.instruments.ingest([{"type": "listing", "market": "HK", "code": "00700.HK", "name": "TENCENT",
                                        "assetClass": "equity", "currency": "HKD", "calendarId": "XHKG"}], "test")
            tencent = (await market.instruments.resolve({"text": "00700.HK"}))["instrument"]
            hk = await read(market, tencent, adjustment="raw", start="2024-10-28", end="2024-11-08")
            j = [k for k, t in enumerate(hk["columns"]["time"]) if t == "1730390400000000000"]
            assert j and hk["columns"]["closeTime"][j[0]] == "1730448000000000000"
        finally:
            await market.close()

    run(scenario())


# ── 11. errors: one source down, the network down ──────────────────────────


def test_unreachable_source_and_network_map_to_market_codes(tmp_path):
    async def scenario():
        market = await open_market(tmp_path)
        try:
            aapl = (await market.instruments.resolve({"text": "AAPL"}))["instrument"]
            market.egress.down = {"query2.finance.yahoo.com"}
            with pytest.raises(MarketError) as raised:
                await read(market, aapl, adjustment="raw", start="2020-08-03", end="2020-10-01")
            assert raised.value.code == "source_unavailable"
            assert raised.value.details["sourceId"] == "yfinance"

            market.egress.down = {"query2.finance.yahoo.com", "www.okx.com", "data-api.binance.vision"}
            await asyncio.sleep(0)
            market.egress.reachability._recent_ok.clear()
            market.egress.reachability._verdict = None
            with pytest.raises(MarketError) as raised:
                await read(market, aapl, adjustment="raw", start="2020-08-03", end="2020-10-01")
            assert raised.value.code == "network_unreachable"

            market.egress.down = set()
            market.egress.reachability._verdict = None
            page = await read(market, aapl, adjustment="raw", start="2020-08-03", end="2020-10-01")
            assert page["fill"]["state"] == "done"
        finally:
            await market.close()

    run(scenario())


# ── 8. coverage ────────────────────────────────────────────────────────────


def test_coverage_shows_source_depths_not_the_stored_daily_start(tmp_path):
    async def scenario():
        market = await open_market(tmp_path)
        try:
            aapl = (await market.instruments.resolve({"text": "AAPL"}))["instrument"]
            await read(market, aapl, adjustment="raw", start="2020-08-03", end="2020-10-01")
            items = {item["timeframe"]: item for item in
                     (await market.data.coverage({"instrument": aapl, "capabilities": ["ohlcv"]}))["items"]}
            now = time.time_ns()
            day = 86_400 * 10**9
            for timeframe, days in (("1m", 30), ("5m", 60), ("15m", 60), ("1h", 730)):
                earliest = int(items[timeframe]["available"]["earliest"])
                assert abs(earliest - (now - days * day)) <= day, timeframe
                assert items[timeframe]["status"] == "not_stored"
                assert items[timeframe]["source"]["sourceId"] == "yfinance"
            daily = items["1d"]
            assert daily["local"]["rows"] == len((await read(market, aapl, adjustment="raw", start="2020-08-03",
                                                              end="2020-10-01", fill="none"))["columns"]["time"])
            # Stored from 2020-08-03, but Yahoo has AAPL's daily history from long before.
            assert iso_of_ns(int(daily["local"]["range"]["start"]))[:10] == "2020-08-03"
            assert int(daily["available"]["earliest"]) < int(daily["local"]["range"]["start"])
            assert daily["status"] == "behind"
        finally:
            await market.close()

    run(scenario())


# ── read_data (AI) ─────────────────────────────────────────────────────────


def test_public_bars_and_lookup_match_for_ai_and_code(tmp_path, monkeypatch):
    import hashlib

    import pyarrow.parquet as pq
    from talyx_sdk.process import Codec

    from modules.market.backend import entry

    async def scenario():
        market = await open_market(tmp_path)
        monkeypatch.setattr(entry, "market", market)
        try:
            async def invoke(action, params, origin):
                spec = entry.module.actions[action]
                request = Codec(spec.input).parse(json.dumps(params).encode())
                return Codec(spec.output).check(await spec.handler(request, Call(None, Caller(None, None, origin), "r")))

            # Synthetic OHLC values around the known US trading day. Include the following
            # session to catch an extra day at the right edge.
            import numpy as np

            from modules.market.backend.store import SeriesKey
            aapl = (await market.instruments.resolve({"text": "AAPL"}))["instrument"]
            opens = np.array([ns_of_date(day) + 5 * 3600 * 10**9 for day in ("2025-01-02", "2025-01-03")])
            market.store.write(SeriesKey(aapl["instrumentId"], "ohlcv", "last", "regular", "1d"),
                {"time": opens, "close_time": opens + 16 * 3600 * 10**9,
                 **{k: np.ones(2) for k in ("open","high","low","close","volume")}},
                fetched=[(int(opens[0]), int(opens[-1]) + DAY_NS)], source="yfinance")
            window = {"symbols": ["AAPL"], "timeframe": "1d", "start": "2025-01-02", "end": "2025-01-02"}
            for action, params in (("instruments.get", {"symbols":["AAPL"]}), ("read_data", window)):
                answer = await invoke(action, params, "ai")
                assert answer == await invoke(action, params, "code")
            page = json.loads(answer)
            assert len(page["rows"]) == 1 and page["rows"][0][1] == "2025-01-02"
            assert page["meta"][0]["adjustment"] == "split"
            bulk = json.loads(await invoke("read_data", {**window, "format":"file"}, "ai"))
            ref = bulk["file"]
            path = Path(ref["path"])
            assert path.stat().st_size == ref["bytes"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == ref["sha256"]
            assert pq.read_table(path).column("symbol").to_pylist() == ["AAPL"]
        finally:
            await market.close()

    run(scenario())


def test_a_long_history_of_one_instrument_goes_on_over_pages(tmp_path, monkeypatch):
    import numpy as np
    from talyx_sdk.process import Codec

    from modules.market.backend import entry
    from modules.market.backend.store import SeriesKey

    async def scenario():
        market = await open_market(tmp_path)
        monkeypatch.setattr(entry, "market", market)
        try:
            spec = entry.module.actions["read_data"]

            async def read(params):
                request = Codec(spec.input).parse(json.dumps(params).encode())
                return json.loads(Codec(spec.output).check(await spec.handler(request, Call(None, Caller(None, None, "ai"), "r"))))

            aapl = (await market.instruments.resolve({"text": "AAPL"}))["instrument"]
            opens = ns_of_date("2022-01-03") + 5 * 3600 * 10**9 + np.arange(600) * DAY_NS
            prices = 100 + np.arange(600) / 7  # long decimals, as adjusted prices have
            market.store.write(SeriesKey(aapl["instrumentId"], "ohlcv", "last", "regular", "1d"),
                {"time": opens, "close_time": opens + 16 * 3600 * 10**9, **{k: prices for k in ("open", "high", "low", "close", "volume")}},
                fetched=[(int(opens[0]), int(opens[-1]) + DAY_NS)], source="yfinance")
            window = {"symbols": ["AAPL"], "timeframe": "1d", "start": "2022-01-03", "end": "2023-08-25"}
            rows, cursor, pages = [], None, 0
            while True:
                page = await read({**window, **({"cursor": cursor} if cursor else {})})
                assert len(json.dumps(page, separators=(",", ":"), ensure_ascii=False)) <= 18_000 and page["meta"][0]["symbol"] == "AAPL"
                rows += page["rows"]
                pages += 1
                if (cursor := page["nextCursor"]) is None:
                    break
                assert cursor.startswith("0:")
            assert pages > 1 and [row[1] for row in rows] == sorted({row[1] for row in rows}) and len(rows) == 600
        finally:
            await market.close()

    run(scenario())


def test_manifest_declares_every_official_source_as_it_describes_itself(tmp_path):
    import json as _json

    from modules.market.backend.app import Market as _Market
    manifest = _json.loads((Path(__file__).parents[2] / "modules/market/talyx.module.json").read_text(encoding="utf-8"))
    declared = {item["id"]: item["descriptor"] for item in manifest["provides"]["market.data_source"]}
    market = _Market(None, tmp_path)
    sources = {entry.source.id: entry.source.descriptor for entry in market.registry.entries.values()}
    assert declared == sources


def test_weekly_and_monthly_windows_stop_at_1970(tmp_path):
    """A chart's 10,000 weekly or monthly bars reach back centuries: the window starts at 1970, so no time on the
    wire is negative (Yahoo reaches back to 1962) and months do not overflow."""
    async def scenario():
        market = await open_market(tmp_path)
        try:
            aapl = (await market.instruments.resolve({"text": "AAPL"}))["instrument"]
            await read(market, aapl, start="2015-12-01", end="2016-01-01")
            for timeframe in ("1W", "1M"):
                series = ohlcv_series(MarketInstrument.from_wire(aapl), timeframe)
                page = await market.data.page(aapl, series, limit=10_000, fill="none")
                assert page["columns"]["time"]
                assert all(int(gap["start"]) >= 0 for gap in page["gaps"])
        finally:
            await market.close()

    run(scenario())
def test_read_data_rejects_obsolete_bars_per_row():
    from pydantic import ValidationError

    from modules.market.backend.public_inputs import ReadData
    with pytest.raises(ValidationError, match="barsPerRow"):
        ReadData(symbols=["AAPL"], barsPerRow=3)


def test_first_intraday_dividend_read_fills_the_raw_reference_close(tmp_path):
    import numpy as np

    async def scenario():
        market = await open_market(tmp_path)
        try:
            aapl = (await market.instruments.resolve({"text": "AAPL"}))["instrument"]
            model = MarketInstrument.from_wire(aapl)
            series = ohlcv_series(model, "1m", adjustment="split_dividend")
            # 2020-08-06 09:30 New York. Recorded Yahoo daily close = 455.61, next day's dividend = 0.82.
            minute = ns_of_date("2020-08-06") + (13 * 3600 + 30 * 60) * 10**9
            ex = ns_of_date("2020-08-07") + 4 * 3600 * 10**9
            plan = market.data.plan(aapl, series, minute)
            cols = {"time": np.array([minute]), "close_time": np.array([minute + 60 * 10**9]),
                    **{k: np.array([100.]) for k in ("open", "high", "low", "close", "volume")}}
            market.store.write(plan.key, cols, fetched=[(minute, minute + 60 * 10**9)], source="yfinance")
            market.actions.write(model.instrumentId, [{"kind": "cash_dividend", "exDate": str(ex), "amount": "0.82"}],
                                 start=minute, end=ex + 1, source="yfinance", revision=1)
            req = {"instrument": aapl, "series": series.to_dict(), "start": str(minute), "end": str(minute + 1)}
            cold = await market.data.read(req)
            assert float(cold["columns"]["close"][0]) == pytest.approx(100 * (1 - 0.82 / 455.61))
            warm = await market.data.read({**req, "fill": "none"})
            assert cold["columns"] == warm["columns"]
        finally:
            await market.close()
    run(scenario())
