"""Market 2.0: exchange directories by environment, the option chain, and current quotes with a failing instrument
(build criterion 31; its last part, Trade's USD valuation over the quotes, went with Trade)."""

from __future__ import annotations

import asyncio

import ccxt
import pytest

from modules.market.backend.app import Market
from modules.market.backend.common import MarketError
from modules.market.backend.sources import crypto
from tests.fixtures.market.replay import CcxtReplay, ReplayEgress, recorded

TESTNET = ("gate", "testnet")
KRAKEN = ("kraken", "markets")
RECORDED_AT = 1_794_277_000 * 10**9  # the options expiring 2026-11-10 08:00 UTC were listed then
EXPIRY = str(1_794_297_600 * 10**9)


def run(coro):
    return asyncio.run(coro)


def responses(name: str) -> dict:
    return {entry["url"]: entry["body"] for entry in recorded(*name)}


@pytest.fixture
def market(tmp_path, monkeypatch):
    monkeypatch.setattr(crypto, "now_ns", lambda: RECORDED_AT)
    CcxtReplay(monkeypatch, TESTNET, KRAKEN)
    return Market(None, tmp_path, egress=ReplayEgress())


def test_gate_testnet_directory_and_option_chain_carry_the_venue_values(market):
    raw = responses(TESTNET)
    perpetual = next(c for c in raw["https://api-testnet.gateapi.io/api/v4/futures/usdt/contracts"]
                     if c["name"] == "BTC_USDT")
    chain_url = next(u for u in raw if "expiration=" in u)
    contracts = {c["name"]: c for c in raw[chain_url]}
    tickers = {t["name"]: t for t in raw[next(u for u in raw if "/options/tickers" in u)]}

    async def scenario():
        await market.start(load_catalogs=False)
        try:
            page = await market.instruments.catalog({"venueId": "gate", "environment": "testnet",
                                                     "products": ["swap"], "limit": 100})
            btc = next(r for r in page["instruments"] if r["symbol"] == "BTC/USDT:USDT")
            assert btc["contractSize"] == perpetual["quanto_multiplier"]
            # Shown and typed like a production record: BTCUSDT.P on Gate, GATE:BTCUSDT.P resolves.
            assert (btc["displaySymbol"], btc["venueName"]) == ("BTCUSDT.P", "Gate")
            resolved = await market.instruments.resolve({"text": "gate:BTCUSDT.P", "environment": "testnet"})
            assert resolved["instrument"]["symbol"] == "BTC/USDT:USDT"
            chain = await market.quotes.option_chain({"venueId": "gate", "environment": "testnet",
                                                      "underlying": "BTC", "expiry": EXPIRY, "cursor": None,
                                                      "limit": 100})
            assert len(chain["rows"]) == len(contracts) == 40
            assert chain["expiries"] == [EXPIRY]
            for row in chain["rows"]:
                name = row["instrument"]["nativeId"]
                ticker, contract = tickers.get(name, {}), contracts[name]
                assert row["impliedVolatility"] == _plain(ticker.get("mark_iv"))  # a ratio, as Gate gives it
                assert row["delta"] == _plain(ticker.get("delta"))
                # Gate gives an empty side as price 0: no quote there, so null.
                for side, field in (("bid", "bid1_price"), ("ask", "ask1_price")):
                    assert row[side] == (None if float(contract[field]) == 0 else _plain(contract[field]))
                if row["bid"] is None and row["ask"] is None:
                    assert row["asOf"]["quote"] is None
                assert row["mark"] == _plain(contract["mark_price"])
        finally:
            await market.close()
    run(scenario())


def _plain(value):
    if value is None:
        return None
    from decimal import Decimal
    return format(Decimal(str(value)).normalize(), "f")


def test_one_failing_quote_leaves_the_others(market, monkeypatch):
    async def scenario():
        await market.start(load_catalogs=False)
        await market.instruments.load("kraken", "kraken", "contracts")
        source = market.registry.entry("kraken").source
        client = source.client("kraken")
        prices = {"BTC/USD": 74_850.5, "ETH/USD": 2_610.25}

        async def fetch_tickers(symbols, params=None):
            if "SOL/USD" in symbols:
                raise ccxt.ExchangeNotAvailable("kraken GET https://api.kraken.com/0/public/Ticker 503 Service Unavailable")
            return {s: {"symbol": s, "last": prices[s], "bid": prices[s] - 1, "ask": prices[s] + 1,
                        "timestamp": RECORDED_AT // 10**6} for s in symbols}

        async def fetch_ticker(symbol, params=None):
            return (await fetch_tickers([symbol]))[symbol]

        monkeypatch.setattr(client, "fetch_tickers", fetch_tickers)
        monkeypatch.setattr(client, "fetch_ticker", fetch_ticker)
        try:
            records = [(await market.instruments.resolve({"text": f"kraken:{c}/USD"}))["instrument"]
                       for c in ("BTC", "ETH", "SOL")]
            result = await market.quotes.current({"instruments": records})
            by_symbol = {r["symbol"]: item for r, item in zip(records, result["items"], strict=True)}
            assert by_symbol["BTC/USD"]["quote"]["price"] == "74850.5"
            assert by_symbol["ETH/USD"]["quote"]["price"] == "2610.25"
            assert by_symbol["SOL/USD"]["quote"] is None
            assert by_symbol["SOL/USD"]["problem"]["code"] == "source_unavailable"
            assert "503" in by_symbol["SOL/USD"]["problem"]["message"]
        finally:
            await market.close()
    run(scenario())


def test_gate_import_matches_existing_contract(market, tmp_path):
    async def scenario():
        await market.start(load_catalogs=False)
        try:
            catalog = await market.instruments.catalog({"venueId": "gate", "environment": "testnet",
                                                        "products": ["swap"], "limit": 100})
            btc = next(r for r in catalog["instruments"] if r["symbol"] == "BTC/USDT:USDT")
            market.identity.upsert_contracts("gate", [btc])
            folder = tmp_path / "prices"
            folder.mkdir()
            (folder / "btc.csv").write_text("date,open,high,low,close\n2024-02-01,1,1,1,1\n")
            spec = {"market": "Gate", "symbol": btc["symbol"], "timeframe": "1d"}
            result = await market.write_data.import_folder(folder, spec)
            assert result["matched"] == 1
            key, = market.store.keys(btc["instrumentId"])
            assert key.import_id == result["importId"]
            with pytest.raises(MarketError, match="No file held rows"):
                await market.write_data.import_folder(folder, {**spec, "symbol": "UNKNOWN/USDT:USDT"})
        finally:
            await market.close()
    run(scenario())


def test_history_coverage_and_forming_rows_are_environment_specific(market, monkeypatch):
    import numpy as np

    from modules.market.backend.common import DAY_NS, ns_of_date
    from modules.market.backend.sources.base import History
    from vocabulary.instruments import MarketInstrument, ohlcv_series

    async def scenario():
        await market.start(load_catalogs=False)
        try:
            catalog = await market.instruments.catalog({"venueId": "gate", "environment": "testnet",
                                                       "products": ["swap"], "limit": 100})
            btc = next(r for r in catalog["instruments"] if r["symbol"] == "BTC/USDT:USDT")
            market.identity.upsert_contracts("gate", [btc])
            source = market.registry.entry("gate").source
            moment = ns_of_date("2026-10-01")
            seen = []

            async def history(source_id, target, *args, **kwargs):
                seen.append(target.environment)
                price = 123.0 if target.environment == "testnet" else 999.0
                return History({"time": np.array([moment]), "close_time": np.array([moment + DAY_NS]),
                                **{k: np.array([price]) for k in ("open", "high", "low", "close", "volume")}})

            monkeypatch.setattr(source, "history", history)
            series = ohlcv_series(MarketInstrument.from_wire(btc), "1d")
            request = {"instrument": btc, "series": series.to_dict(), "start": str(moment), "end": str(moment + DAY_NS)}
            for environment, price in [("testnet", "123"), ("production", "999")]:
                page = await market.data.read({**request, "environment": environment})
                assert page["columns"]["close"] == [price]
                assert seen[-1] == environment
                if environment == "testnet":
                    for env, expected in [("production", False), ("testnet", True)]:
                        covered = await market.data.coverage({"instrument": btc, "capabilities": ["ohlcv"],
                                                              "environment": env})
                        assert any(r["local"] for r in covered["items"]) == expected
            assert len(market.store.keys(btc["instrumentId"], environment=None)) == 2
            from types import SimpleNamespace
            market.sessions.sessions[series.seriesId, "testnet"] = SimpleNamespace(forming={"close": "123"})
            assert market.sessions.forming(series, "production") is None
            assert market.sessions.forming(series, "testnet") == {"close": "123"}
            market.sessions.sessions.clear()
            await market.write_data.delete({"symbols": ["gate:BTC/USDT:USDT"], "environment": "testnet"})
            assert market.store.keys(btc["instrumentId"], environment="testnet") == []
            production = await market.data.read({**request, "fill": "none"})
            assert production["columns"]["close"] == ["999"]
        finally:
            await market.close()
    run(scenario())
