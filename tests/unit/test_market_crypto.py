"""Market 2.0 crypto: reused Binance codes split into contracts, Binance files, funding history
(build criteria 19-21), replaying recorded Binance responses."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from modules.market.backend.app import Market
from modules.market.backend.common import ns_of_date
from tests.fixtures.market.replay import CcxtReplay, ReplayEgress
from vocabulary.instruments import MarketInstrument, market_data_series, ohlcv_series

MARKETS = ("binance", "markets")
HISTORY = ("binance", "history")
FILES = ("binance", "files")
DAY = 86_400 * 10**9


def run(coro):
    return asyncio.run(coro)


async def open_market(tmp_path, monkeypatch, *, onboard=True):
    CcxtReplay(monkeypatch, MARKETS, HISTORY)
    egress = ReplayEgress(FILES)
    market = Market(None, tmp_path, egress=egress)
    await market.start(load_catalogs=False)
    await market.instruments.load("binance", "binance", "contracts")
    if not onboard:
        markets = await market.registry.entry("binance").source.markets("binance")
        for value in markets.values():
            (value.get("info") or {}).pop("onboardDate", None)
    return market, egress


async def contracts(market, symbol, *, market_type, include_inactive=True):
    found = await market.instruments.search({"query": symbol, "venues": ["binance"], "marketTypes": [market_type],
                                             "includeInactive": include_inactive})
    return [r["instrument"] for r in found["results"] if r["instrument"]["symbol"] == symbol]


async def daily(market, record, start, end):
    series = ohlcv_series(MarketInstrument.from_wire(record), "1d").to_dict()
    return await market.data.read({"instrument": record, "series": series, "fill": "await",
                                   "start": str(ns_of_date(start)), "end": str(ns_of_date(end))})


def none_spans(page):
    return [(int(g["start"]), int(g["end"])) for g in page["gaps"] if g["reason"] == "source_has_none"]


def covered(spans, start, end):
    return any(a <= ns_of_date(start) and ns_of_date(end) <= b for a, b in spans)


# ── 19. spot LUNA/USDT: old LUNA until 2022-05-13, new LUNA from 2022-05-31 ─


def test_spot_code_reuse_splits_old_luna_into_a_retired_contract(tmp_path, monkeypatch):
    async def scenario():
        market, _egress = await open_market(tmp_path, monkeypatch)
        try:
            (current,) = await contracts(market, "LUNA/USDT", market_type="spot")
            page = await daily(market, current, "2022-05-01", "2022-06-02")
            columns = page["columns"]
            assert columns["time"][0] == str(ns_of_date("2022-05-31"))
            assert float(columns["open"][0]) == 1.0 and float(columns["close"][0]) == 8.87
            assert page["instrumentId"] == current["instrumentId"]  # the current contract keeps its id
            assert covered(none_spans(page), "2022-05-14", "2022-05-31")
            assert len(await contracts(market, "LUNA/USDT", market_type="spot", include_inactive=False)) == 1
            both = await contracts(market, "LUNA/USDT", market_type="spot")
            assert len(both) == 2
            (old,) = [r for r in both if r["retiredAt"]]
            assert old["retiredAt"] == "1652400000000000000"
            assert old["instrumentId"] != current["instrumentId"]
            page = await daily(market, old, "2022-05-01", "2022-06-02")
            assert len(page["columns"]["time"]) == 13
            assert page["columns"]["time"][-1] == str(ns_of_date("2022-05-13"))
            assert float(page["columns"]["close"][-1]) == 0.00005
            assert covered(none_spans(page), "2022-05-14", "2022-05-31")
            again = (await market.public.get({"symbols": [current["instrumentId"]]}))["items"][0]
            assert again["instrument"]["instrumentId"] == current["instrumentId"]
        finally:
            await market.close()
    run(scenario())


# ── 20. perpetuals: TLM reused, FTT settled, ICP not split ─────────────────


def tlm_checks(market):
    async def check():
        (current,) = await contracts(market, "TLM/USDT:USDT", market_type="swap", include_inactive=False)
        page = await daily(market, current, "2022-06-01", "2023-04-05")
        assert page["columns"]["time"][0] == str(ns_of_date("2023-03-30"))
        assert float(page["columns"]["open"][0]) == 0.02073
        records = await contracts(market, "TLM/USDT:USDT", market_type="swap")
        (old,) = [r for r in records if r["retiredAt"]]
        assert old["retiredAt"] == "1654732800000000000"  # 2022-06-09, its last real trade
        page = await daily(market, old, "2022-06-01", "2023-04-05")
        times = page["columns"]["time"]
        assert times[-1] == str(ns_of_date("2022-06-09")) and len(times) == 9
        # The flat 0.0347 filler line from 06-10 is not a price: the source has none there.
        assert covered(none_spans(page), "2022-06-10", "2023-03-29")
    return check()


def test_perpetual_code_reuse_cuts_at_onboard_date_and_drops_filler_rows(tmp_path, monkeypatch):
    async def scenario():
        market, _egress = await open_market(tmp_path, monkeypatch)
        try:
            await tlm_checks(market)
        finally:
            await market.close()
    run(scenario())


def test_without_an_onboard_date_the_cut_is_where_real_trading_resumes(tmp_path, monkeypatch):
    async def scenario():
        market, _egress = await open_market(tmp_path, monkeypatch, onboard=False)
        try:
            await tlm_checks(market)
        finally:
            await market.close()
    run(scenario())


def test_a_settled_perpetual_has_no_rows_after_its_last_trade_and_icp_is_one_contract(tmp_path, monkeypatch):
    async def scenario():
        market, _egress = await open_market(tmp_path, monkeypatch)
        try:
            (ftt,) = await contracts(market, "FTT/USDT:USDT", market_type="swap")
            page = await daily(market, ftt, "2022-11-01", "2023-01-01")
            assert page["columns"]["time"][-1] == str(ns_of_date("2022-11-14"))
            assert covered(none_spans(page), "2022-11-15", "2023-01-01")
            (icp,) = await contracts(market, "ICP/USDT:USDT", market_type="swap")
            page = await daily(market, icp, "2021-07-01", "2021-09-01")
            times = [int(t) for t in page["columns"]["time"]]
            assert len(times) == 62 and all(b - a == DAY for a, b in zip(times, times[1:], strict=False))
            assert len(await contracts(market, "ICP/USDT:USDT", market_type="swap")) == 1
        finally:
            await market.close()
    run(scenario())


# ── 21. month files missing days; funding history ──────────────────────────


def test_month_file_gaps_are_read_from_day_files(tmp_path, monkeypatch):
    async def scenario():
        market, egress = await open_market(tmp_path, monkeypatch)
        try:
            (luna,) = await contracts(market, "LUNA/USDT:USDT", market_type="swap")
            assert luna["active"] is False  # delisted: known from Binance's files only
            page = await daily(market, luna, "2022-02-01", "2022-03-01")
            assert len(page["columns"]["time"]) == 28
            assert egress.count("/daily/klines/LUNAUSDT/1d/LUNAUSDT-1d-2022-02-2") == 3
        finally:
            await market.close()
    run(scenario())


def test_binance_funding_history_has_three_eight_hour_rows_a_day(tmp_path, monkeypatch):
    async def scenario():
        market, _egress = await open_market(tmp_path, monkeypatch)
        try:
            (btc,) = await contracts(market, "BTC/USDT:USDT", market_type="swap")
            series = market_data_series(MarketInstrument.from_wire(btc), "funding_rate").to_dict()
            page = await market.data.read({"instrument": btc, "series": series, "fill": "await",
                                           "start": str(ns_of_date("2024-11-04")),
                                           "end": str(ns_of_date("2024-11-05"))})
            columns = page["columns"]
            start = ns_of_date("2024-11-04")
            assert columns["time"] == [str(start + h * 3600 * 10**9) for h in (0, 8, 16)]
            assert columns["rate"] == ["0.0001"] * 3
            assert columns["intervalHours"] == ["8"] * 3
        finally:
            await market.close()
    run(scenario())


def test_a_fetch_job_stores_every_crypto_capability_the_contract_names(tmp_path, monkeypatch):
    """fetch takes bars, the crypto capabilities and research kinds: a capability is a stored series filled like
    bars (Quant's funding, the panel's Get data, AI's market_fetch), not a research document."""
    from modules.market.backend.data import SERIES_CAPABILITIES

    async def scenario():
        market, _egress = await open_market(tmp_path, monkeypatch)
        try:
            (btc,) = await contracts(market, "BTC/USDT:USDT", market_type="swap")
            job = SimpleNamespace(record={"start": None, "environment": "production"})
            for capability in SERIES_CAPABILITIES:
                plan = market.jobs.plan(job, {"instrument": btc, "kind": {"kind": capability}})
                assert plan.series.capability == capability
            result = await market.jobs.fetch({"targets": [{"instrument": btc}], "kinds": [{"kind": "funding_rate"}],
                                              "start": "2024-11-04", "end": "2024-11-04"},
                                             workspace_id=None)
            assert result["failures"] == [] and result["completed"] == 1 and result["rows"] == 3
            series = market_data_series(MarketInstrument.from_wire(btc), "funding_rate").to_dict()
            page = await market.data.read({"instrument": btc, "series": series, "fill": "none",
                                           "start": str(ns_of_date("2024-11-04")),
                                           "end": str(ns_of_date("2024-11-05"))})
            assert page["columns"]["rate"] == ["0.0001"] * 3
        finally:
            await market.close()
    run(scenario())


# ── live observations for Trade: book, trades, ticker, funding, open interest ─


class FakeExchange:
    """A stand-in CCXT client (REST and Pro): scripted pushes, counted subscriptions."""

    def __init__(self):
        self.watches = {"order_book": 0, "trades": 0, "ticker": 0}
        self.queue: dict[str, asyncio.Queue] = {name: asyncio.Queue() for name in self.watches}

    async def watch_order_book(self, symbol, limit=None):
        self.watches["order_book"] += 1 if limit == 50 else 100
        return await self.queue["order_book"].get()

    async def watch_trades(self, symbol):
        self.watches["trades"] += 1
        return await self.queue["trades"].get()

    async def watch_ticker(self, symbol):
        self.watches["ticker"] += 1
        return await self.queue["ticker"].get()

    async def fetch_trades(self, symbol, since=None, limit=None):
        return [{"timestamp": 1_730_678_400_000 + i, "price": 68_000 + i, "amount": 0.5, "side": "buy",
                 "id": str(i)} for i in range(limit)]

    async def fetch_funding_rate(self, symbol):
        return {"timestamp": 1_730_678_400_000, "fundingRate": 0.0001, "fundingTimestamp": 1_730_707_200_000,
                "markPrice": 68_010.5, "indexPrice": 68_000.25}

    async def fetch_open_interest(self, symbol):
        return {"timestamp": 1_730_678_400_000, "openInterestAmount": 81_234.5, "openInterestValue": 5.5e9}

    async def fetch_funding_rate_history(self, symbol, since=None, limit=None, params=None):
        return []

    async def fetch_open_interest_history(self, symbol, timeframe, since=None, limit=None, params=None):
        return []


async def take(iterator, count, timeout=5.0):
    return [await asyncio.wait_for(anext(iterator), timeout) for _ in range(count)]


def test_observation_sessions_share_one_upstream_and_carry_trade_rows(tmp_path, monkeypatch):
    async def scenario():
        market, egress = await open_market(tmp_path, monkeypatch)
        egress.add("/daily/metrics/", b"", status=404)  # funding and open interest are stored series: none kept yet
        fake = FakeExchange()
        source = market.registry.entry("binance").source
        monkeypatch.setattr(source, "pro_client", lambda venue, environment="production": fake)
        rest = source.client("binance")
        for name in ("fetch_trades", "fetch_funding_rate", "fetch_open_interest", "fetch_funding_rate_history",
                     "fetch_open_interest_history"):
            monkeypatch.setattr(rest, name, getattr(fake, name))
        monkeypatch.setattr(source, "poll_seconds", {"funding_rate": 0.05, "open_interest": 0.05})
        try:
            (btc,) = await contracts(market, "BTC/USDT:USDT", market_type="swap")
            instrument = MarketInstrument.from_wire(btc)

            def params(capability, **extra):
                return {"instrument": btc, "series": market_data_series(instrument, capability).to_dict(), **extra}

            first = market.sessions.open(params("order_book", depth=50, warmup=1))
            second = market.sessions.open(params("order_book", depth=50, warmup=1))
            assert [item["type"] for item in await take(first, 2)] == ["replace", "status"]
            assert [item["type"] for item in await take(second, 2)] == ["replace", "status"]
            book = {"timestamp": 1_730_678_400_000, "bids": [[68_000.5, 1.25], [68_000, 2]],
                    "asks": [[68_001, 0.75]]}
            await fake.queue["order_book"].put(book)
            seen = [item for item in await take(first, 2) if item["type"] == "update"]
            assert seen[-1]["row"]["bids"] == [["68000.5", "1.25"], ["68000", "2"]]
            assert seen[-1]["row"]["asks"] == [["68001", "0.75"]]
            assert fake.watches["order_book"] <= 2  # one upstream for both consumers, asked with depth 50
            trades = market.sessions.open(params("trades", warmup=100))
            replace = (await take(trades, 1))[0]
            assert replace["type"] == "replace" and len(replace["page"]["columns"]["id"]) == 100
            await fake.queue["trades"].put([{"timestamp": 1_730_678_500_000, "price": 68_100, "amount": 0.1,
                                             "side": "sell", "id": "t-1"},
                                            {"timestamp": 1_730_678_500_000, "price": 68_101, "amount": 0.2,
                                             "side": "buy", "id": "t-2"}])
            appended = [item for item in await take(trades, 3) if item["type"] == "append"]
            assert appended[0]["columns"]["id"] == ["t-1", "t-2"]  # same millisecond, both kept
            assert appended[0]["columns"]["price"] == ["68100", "68101"]
            funding = market.sessions.open(params("funding_rate", warmup=1))
            rows = [item["row"] for item in await take(funding, 4) if item["type"] == "update"]
            assert rows[0]["rate"] == "0.0001" and rows[0]["mark"] == "68010.5" and rows[0]["index"] == "68000.25"
            assert rows[0]["nextFundingTime"] == str(1_730_707_200_000 * 10**6)
            interest = market.sessions.open(params("open_interest", warmup=1))
            rows = [item["row"] for item in await take(interest, 4) if item["type"] == "update"]
            assert rows[0]["value"] == "81234.5"
            for iterator in (first, second, trades, funding, interest):
                await iterator.aclose()
        finally:
            await market.close()
    run(scenario())


# ── CoinGecko: coins, market cap history, categories ───────────────────────


def test_coingecko_coins_have_market_cap_history_and_categories(tmp_path):
    async def scenario():
        egress = ReplayEgress(("coingecko", "coins"))
        market = Market(None, tmp_path, egress=egress)
        await market.start(load_catalogs=False)
        try:
            await market.instruments.load("coingecko", "COIN", "listings")
            found = await market.instruments.search({"query": "BTC", "markets": ["COIN"]})
            bitcoin = found["results"][0]["instrument"]
            assert (bitcoin["symbol"], bitcoin["name"], bitcoin["venueId"]) == ("bitcoin", "Bitcoin (BTC)", "COIN")
            page = await market.research.read({"kind": "market_cap", "instruments": [bitcoin], "limit": 5000})
            columns = page["columns"]
            assert len(columns["date"]) >= 365 and set(columns["currency"]) == {"USD"}
            assert all(int(known) >= int(day) for day, known in zip(columns["date"], columns["knownAt"], strict=True))
            assert all(float(value) > 1e11 for value in columns["value"])
            assert page["note"] == "Data provided by CoinGecko"
            page = await market.research.read({"kind": "categories", "instruments": [bitcoin]})
            assert "Layer 1 (L1)" in page["columns"]["name"]
            assert egress.count("api.coingecko.com") == 3
        finally:
            await market.close()
    run(scenario())


# ── Gate: whole past months from Gate's files ──────────────────────────────


GATE_BTC = {"identityVersion": 1, "venueId": "gate", "symbol": "BTC/USDT:USDT", "displaySymbol": "BTC/USDT:USDT",
            "base": "BTC", "quote": "USDT", "settle": "USDT", "marketType": "swap", "assetClass": "crypto",
            "contractOrientation": "linear", "contractSize": "0.0001", "expiry": None, "strike": None,
            "optionSide": None, "underlyingSymbol": None, "retiredAt": None, "calendarId": "24_7",
            "adjustmentEligible": False, "nativeId": "BTC_USDT", "active": True}


def test_gate_months_beyond_the_api_come_from_its_files_in_base_units(tmp_path):
    from modules.market.backend.sources.base import Target

    async def scenario():
        egress = ReplayEgress(("gate", "files"))
        market = Market(None, tmp_path, egress=egress)
        source = market.registry.entry("gate").source
        try:
            target = Target(GATE_BTC, [])
            month = ("month", ns_of_date("2024-01-01"))
            history = await source.history("gate", target, "bars", "1h", "last", ns_of_date("2024-01-01"),
                                           ns_of_date("2024-02-01"), month)
            columns = history.columns
            assert len(columns["time"]) == 744 and history.next is None
            assert int(columns["time"][0]) == ns_of_date("2024-01-01")
            # Gate's row is time, volume, close, high, low, open; volume in contracts of 0.0001 BTC.
            first = [float(columns[name][0]) for name in ("open", "high", "low", "close")]
            assert first == [42317.5, 42591.6, 42291.8, 42507.9]
            assert round(float(columns["volume"][0]), 4) == 331.2413
            funding = await source.history("gate", target, "funding_rate", None, "last", ns_of_date("2024-01-01"),
                                           ns_of_date("2024-02-01"), month)
            assert len(funding.columns["time"]) == 93 and float(funding.columns["rate"][0]) == 0.000325
            assert egress.count("download.gatedata.org") == 2
        finally:
            await market.close()
    run(scenario())


def test_open_interest_history_asks_one_request_window_at_a_time_in_base_units(tmp_path, monkeypatch):
    """A venue answers the newest rows of the window it is asked for (KuCoin does), so each request names a window
    of one request's rows: paging from start leaves no hole. Gate counts contracts of 0.0001 BTC."""
    from modules.market.backend.sources.base import Target

    step_ms = 300_000
    start, end = ns_of_date("2024-01-01"), ns_of_date("2024-01-02")
    asked = []

    async def history(symbol, timeframe, since=None, limit=None, params=None):
        asked.append((since, params["until"]))
        times = [t for t in range(start // 10**6, end // 10**6, step_ms) if since <= t <= params["until"]]
        return [{"timestamp": t, "openInterestAmount": 5_000_000, "openInterestValue": 2.1e10}
                for t in times[-limit:]]

    async def scenario():
        market = Market(None, tmp_path, egress=ReplayEgress())
        source = market.registry.entry("gate").source
        monkeypatch.setattr(source.client("gate"), "fetch_open_interest_history", history)

        async def no_markets(venue, environment="production"):
            return {}
        monkeypatch.setattr(source, "markets", no_markets)
        try:
            rows, cursor = [], start
            while cursor is not None:
                answer = await source.history("gate", Target(GATE_BTC, []), "open_interest", "5m", "last", cursor, end)
                rows.extend(int(t) for t in answer.columns["time"])
                values = set(answer.columns["value"].tolist())
                cursor = answer.next
            assert rows == list(range(start, end, step_ms * 10**6))  # 288 points, three requests of 100
            assert len(asked) == 3 and values == {500.0}
        finally:
            await market.close()
    run(scenario())


# ── a region block is not a rate limit ──────────────────────────────────────


def test_bybit_region_block_is_forbidden_and_its_rate_limit_still_waits():
    import ccxt
    import pytest

    from modules.market.backend.common import MarketError
    from modules.market.backend.sources.crypto import CryptoSource

    url = "https://api.bybit.com/v5/market/instruments-info?category=spot"

    async def refuse(message):
        async def call():
            raise ccxt.RateLimitExceeded(message)
        with pytest.raises(MarketError) as raised:
            await CryptoSource._call(None, "bybit", call)
        return raised.value

    blocked = run(refuse(f"bybit GET {url} 403 Forbidden {{error:The Amazon CloudFront distribution is configured "
                         "to block access from your country}"))
    assert (blocked.code, blocked.status) == ("forbidden", 403)
    limited = run(refuse(f"bybit GET {url} 403 Forbidden {{retCode:10006, retMsg:Too many visits!}}"))
    assert limited.code == "rate_limited"


def test_a_websocket_handshake_ccxt_pro_saw_refused_is_forbidden():
    """A region block on a CCXT Pro venue's live connect (the handshake answered 451) is forbidden at once, so the
    series fails with the reason instead of reconnecting for ever."""
    import aiohttp
    import ccxt
    import pytest

    from modules.market.backend.common import MarketError
    from modules.market.backend.sources.crypto import CryptoSource

    async def call():
        raise ccxt.NetworkError(aiohttp.WSServerHandshakeError(None, (), status=451, message="Invalid response status"))
    with pytest.raises(MarketError) as raised:
        run(CryptoSource._call(None, "bybit", call))
    assert (raised.value.code, raised.value.status) == ("forbidden", 451)


def test_polled_rows_carry_what_the_venue_said_and_nothing_in_its_place():
    from modules.market.backend.sources import crypto_live

    # Binance's fetchOpenInterest gives the amount only: the quote value stays null, never the 24h volume.
    linear = {"contractOrientation": "linear", "contractSize": "1"}
    binance = crypto_live.open_interest_row({"timestamp": 1_700_000_000_000, "openInterestAmount": 81_234.5,
                                             "openInterestValue": None, "baseVolume": 9e9, "quoteVolume": 7e14}, linear)
    assert (binance["value"], binance["valueQuote"]) == ("81234.5", None)
    neither = crypto_live.open_interest_row({"timestamp": 1_700_000_000_000, "baseVolume": 9e9, "quoteVolume": 7e14},
                                            linear)
    assert (neither["value"], neither["valueQuote"]) == (None, None)
    # CCXT's amount counts contracts: OKX's BTC swap contract is 0.01 BTC, so 12,345 contracts are 123.45 BTC; an
    # inverse contract counts dollars, which are not base units.
    okx = crypto_live.open_interest_row({"timestamp": 1_700_000_000_000, "openInterestAmount": 12_345,
                                         "openInterestValue": 1.2e7},
                                        {"contractOrientation": "linear", "contractSize": "0.01"})
    assert (okx["value"], okx["valueQuote"]) == ("123.45", "12000000")
    inverse = crypto_live.open_interest_row({"timestamp": 1_700_000_000_000, "openInterestAmount": 5_000,
                                             "openInterestValue": 5e5},
                                            {"contractOrientation": "inverse", "contractSize": "100"})
    assert (inverse["value"], inverse["valueQuote"]) == (None, "500000")
    # OKX names both settlements: the rate is settled at fundingTimestamp, nextFundingTimestamp is the one after.
    okx = crypto_live.funding_row({"timestamp": 1_700_000_000_000, "fundingRate": 0.0001,
                                   "fundingTimestamp": 1_700_006_400_000, "nextFundingTimestamp": 1_700_035_200_000})
    assert okx["nextFundingTime"] == str(1_700_006_400_000 * 10**6)
    assert crypto_live.funding_row({"timestamp": 1_700_000_000_000, "fundingRate": 0.0001,
                                    "nextFundingTimestamp": 1_700_035_200_000})["nextFundingTime"] is None


def test_okx_ticker_change_is_over_the_rolling_24_hours_its_basis_names():
    import json

    from modules.market.backend.sources import crypto_live

    message = {"arg": {"channel": "tickers", "instId": "BTC-USDT"}, "data": [
        {"instId": "BTC-USDT", "last": "110", "open24h": "100", "sodUtc0": "105", "bidPx": "109", "askPx": "111",
         "ts": "1730678400000"}]}
    ((_key, event),) = crypto_live.okx_route({"BTC-USDT": False})(json.dumps(message))
    assert event["quote"]["changeBasis"] == "rolling_24h"
    assert float(event["quote"]["change"]) == 10 and float(event["quote"]["changePercent"]) == 10


def test_quote_numbers_keep_their_significant_digits_however_small_the_price():
    import json

    from modules.market.backend.sources import crypto_live, yahoo
    from modules.market.backend.sources.quotes import make_quote

    # BABYDOGE trades near 1e-9: a fixed count of decimals turned its 24-hour change into 0.
    message = {"arg": {"channel": "tickers", "instId": "BABYDOGE-USDT"}, "data": [
        {"instId": "BABYDOGE-USDT", "last": "0.000000001234", "open24h": "0.000000001200", "bidPx": "0.000000001233",
         "askPx": "0.000000001235", "ts": "1730678400000"}]}
    ((_key, event),) = crypto_live.okx_route({"BABYDOGE-USDT": False})(json.dumps(message))
    quote = event["quote"]
    assert (quote["price"], quote["change"], quote["changePercent"]) == \
        ("0.000000001234", "0.000000000034", "2.83333333333")
    # A small move on a large price, and a float ratio, carry no binary noise.
    message["data"][0].update(instId="BTC-USDT", last="65000.2", open24h="65000.1")
    message["arg"]["instId"] = "BTC-USDT"
    ((_key, event),) = crypto_live.okx_route({"BTC-USDT": False})(json.dumps(message))
    assert event["quote"]["change"] == "0.1"
    assert make_quote(price=1.234e-9, change=0.1 + 0.2)["price"] == "0.000000001234"
    assert make_quote(price=1.234e-9, change=0.1 + 0.2)["change"] == "0.3"
    stock = yahoo._quote_of_meta({"regularMarketPrice": 0.0004, "previousClose": 0.0003, "regularMarketTime": 1})
    assert (stock["change"], stock["changePercent"]) == ("0.0001", "33.3333333333")


def test_a_price_basis_the_venue_api_does_not_publish_is_refused_not_served_as_last(tmp_path):
    """Premium index bars are only in Binance's files: OKX live and any venue's REST history refuse
    them instead of streaming or returning last-price bars."""
    import pytest

    from modules.market.backend.common import MarketError
    from modules.market.backend.sources import crypto_live
    from modules.market.backend.sources.base import Target

    assert crypto_live.okx_channel("1m", "mark") == "mark-price-candle1m"
    with pytest.raises(MarketError) as live:
        crypto_live.okx_channel("1m", "premium")
    assert live.value.code == "unsupported"

    async def scenario():
        market = Market(None, tmp_path, egress=ReplayEgress())
        source = market.registry.entry("okx").source
        try:
            with pytest.raises(MarketError) as history:
                await source.history("okx", Target({"symbol": "BTC/USDT:USDT"}, []), "bars", "1m", "premium",
                                     ns_of_date("2024-01-01"), ns_of_date("2024-01-02"))
            assert history.value.code == "unsupported" and "Binance's files" in history.value.message
        finally:
            await market.close()
    run(scenario())


def test_okx_index_bars_are_asked_by_the_index_and_carry_no_volume(tmp_path, monkeypatch):
    """OKX keys index candles by the index (BTC-USDT), not a contract: a swap's instFamily, a spot pair's own id
    (CCXT sends the instFamily, empty for spot). Mark and index candles have no volume; CCXT reads the confirm flag."""
    import math

    from modules.market.backend.sources.base import Target

    start = ns_of_date("2024-01-01")
    asked = []
    markets = {"BTC/USDT": {"id": "BTC-USDT", "info": {"instFamily": ""}},
               "BTC/USDT:USDT": {"id": "BTC-USDT-SWAP", "info": {"instFamily": "BTC-USDT"}}}

    async def ohlcv(symbol, timeframe, since=None, limit=None, params=None):
        asked.append(params["instId"])
        return [[start // 10**6 + i * 60_000, 1.0, 2.0, 0.5, 1.5, 1.0] for i in range(3)]

    async def scenario():
        market = Market(None, tmp_path, egress=ReplayEgress())
        source = market.registry.entry("okx").source
        monkeypatch.setattr(source.client("okx"), "fetch_ohlcv", ohlcv)

        async def known(venue, environment="production"):
            return markets
        monkeypatch.setattr(source, "markets", known)
        try:
            for symbol in markets:
                answer = await source.history("okx", Target({"symbol": symbol}, []), "bars", "1m", "index",
                                              start, start + 3 * 60 * 10**9)
                assert all(math.isnan(v) for v in answer.columns["volume"])
            assert asked == ["BTC-USDT", "BTC-USDT"]
        finally:
            await market.close()
    run(scenario())


@pytest.mark.parametrize("timeframe", ["1d", "1m"])
def test_a_contract_listed_after_the_depth_is_found_in_a_few_requests(tmp_path, monkeypatch, timeframe):
    """OKX ETH perpetual: its first bar is two years after the source's depth. A fill from the depth finds the
    first window with bars by doubling then halving (1m: about twenty requests, not one per empty 300-bar window,
    3,456), and only the span before that window holds none."""
    from modules.market.backend.sources.base import Target
    from vocabulary.identity import timeframe_nanoseconds

    step = timeframe_nanoseconds(timeframe)
    start = ns_of_date("2018-01-11")
    listed = ns_of_date("2020-01-01")
    end = listed + 600 * step
    asked = []

    async def ohlcv(symbol, timeframe, since=None, limit=None, params=None):
        asked.append(since * 10**6)
        first = max(since * 10**6, listed)
        return [[(first + i * step) // 10**6, 1.0, 2.0, 0.5, 1.5, 1.0] for i in range(limit)
                if first + i * step < since * 10**6 + limit * step]

    async def scenario():
        market = Market(None, tmp_path, egress=ReplayEgress())
        source = market.registry.entry("okx").source
        monkeypatch.setattr(source.client("okx"), "fetch_ohlcv", ohlcv)

        async def known(venue, environment="production"):
            return {"ETH/USDT:USDT": {"id": "ETH-USDT-SWAP", "info": {}}}
        monkeypatch.setattr(source, "markets", known)
        try:
            target = Target({"symbol": "ETH/USDT:USDT"}, [])
            history = await source.history("okx", target, "bars", timeframe, "last", start, end)
            span = 300 * step
            begin = start + (listed - start) // span * span  # the window holding the listing
            assert int(history.columns["time"][0]) == listed and history.empty_reason is None
            assert history.none == [(start, begin)]
            assert len(asked) <= (5 if timeframe == "1d" else 26)
        finally:
            await market.close()
    run(scenario())




def test_inverse_binance_archive_uses_base_asset_volume():
    from unittest.mock import AsyncMock

    from modules.market.backend.sources.binance_files import BinanceFiles

    archive = [["1704067200000", "40000", "40000", "40000", "40000", "20", "1704067259999", "0.05", "2"]]
    files = BinanceFiles.__new__(BinanceFiles)
    files.rows = AsyncMock(return_value=archive)
    start = 1704067200000000000
    history = asyncio.run(files._file("futures/cm", "BTCUSD_PERP", "bars", "1m", "last",
                                      ("day", start), start, start + 60_000_000_000, None, None))
    assert history.columns["volume"].tolist() == [20 * 100 / 40000]
