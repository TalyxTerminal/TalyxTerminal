"""Market 2.0 live data: the session and quotes streams and their recovery (build criteria 12-14).

Fake Binance WebSocket services run on 127.0.0.1; REST history comes from
the CCXT replay, whose synthetic candles are the same rows the fake pushes. Time is
a fake clock the test advances minute by minute.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from modules.market.backend import common, live, network
from modules.market.backend.app import Market
from modules.market.backend.common import ns_of_date
from tests.fixtures.market.fake_ws import FakeBinance
from tests.fixtures.market.replay import CcxtReplay, ReplayEgress, synthetic_row
from vocabulary.instruments import MarketInstrument, ohlcv_series

BINANCE = ("binance", "markets")
NASDAQ = ("nasdaqtrader", "symbol-directory")
MINUTE = 60 * 10**9
T0 = ns_of_date("2025-01-06")


class Clock:
    def __init__(self, value: int):
        self.value = value

    def __call__(self) -> int:
        return self.value


@pytest.fixture
def clock(monkeypatch):
    value = Clock(T0 + 10 * 10**9)
    common.set_clock(value)
    monkeypatch.setattr(network, "proxy", lambda: None)
    yield value
    common.set_clock(None)


def as_bar(row: list) -> dict:
    return {"open": row[1], "high": row[2], "low": row[3], "close": row[4], "volume": row[6]}


async def drain(iterator, into: list, stop: asyncio.Event):
    try:
        while not stop.is_set():
            into.append(await iterator.__anext__())
    except (StopAsyncIteration, asyncio.CancelledError):
        pass


async def settle(seconds: float = 0.05) -> None:
    await asyncio.sleep(seconds)


def assembled(items: list[dict]) -> list[dict]:
    """The consumer's view of closed rows: the last replace plus the appends after it."""
    rows: dict[str, dict] = {}
    for item in items:
        if item["type"] == "replace":
            rows = {}
            columns = item["page"]["columns"]
        elif item["type"] == "append":
            columns = item["columns"]
        else:
            continue
        for i, time in enumerate(columns["time"]):
            assert time not in rows or item["type"] == "replace", f"duplicate row {time}"
            rows[time] = {name: columns[name][i] for name in ("open", "high", "low", "close", "volume")}
    return [rows[key] | {"time": key} for key in sorted(rows, key=int)]


def opened(items: list[dict]) -> list[dict]:
    """The replaces carrying a session's filled window (the first one goes out at once with what is stored)."""
    return [item for item in items if item["type"] == "replace" and item["page"]["fill"]["state"] != "filling"]


SPOT = ["BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "TRX", "LINK", "DOT", "LTC", "BCH", "AVAX", "UNI", "ATOM",
        "ETC", "XLM", "FIL", "NEAR", "APT"]


def test_binance_spot_shares_one_connection_rotates_without_gaps_and_recovers_from_silence(
        tmp_path, monkeypatch, clock):
    async def scenario():
        server = FakeBinance(lifetime=1.6)
        await server.start()
        CcxtReplay(monkeypatch, BINANCE, synthetic=True)
        egress = ReplayEgress(("binance", "files"))
        egress.add(lambda url, params: "/api/v3/klines" in url, [])  # no reused codes among these pairs
        market = Market(None, tmp_path, egress=egress)
        source = market.registry.entry("binance").source
        source.urls = {**source.urls, "binance:spot": server.url("/stream")}
        source.binance_rotate = 1.0
        source.heartbeat = 1.0
        await market.start(load_catalogs=False)
        await market.instruments.load("binance", "binance", "contracts")
        stop = asyncio.Event()
        try:
            feeds = {}
            for base in SPOT:
                record = (await market.instruments.resolve({"text": f"binance:{base}/USDT"}))["instrument"]
                series = ohlcv_series(MarketInstrument.from_wire(record), "1m").to_dict()
                items: list[dict] = []
                iterator = market.sessions.open({"instrument": record, "series": series, "start": str(T0)})
                feeds[base] = (items, asyncio.ensure_future(drain(iterator, items, stop)))
            for _ in range(200):
                if server.open_connections and len(server.open_connections[0]["subs"]) == 20:
                    break
                await settle(0.02)
            assert len(server.connections) == 1 and len(server.connections[0]["subs"]) == 20

            async def minute(m: int, push: bool = True):
                row = synthetic_row((T0 + m * MINUTE) // 10**6, 60_000)
                clock.value = T0 + m * MINUTE + 30 * 10**9
                if push:
                    for base in SPOT:
                        await server.kline(f"{base.lower()}usdt@kline_1m", f"{base}USDT", row, 60_000, False)
                clock.value = T0 + (m + 1) * MINUTE + 10**9
                if push:
                    for base in SPOT:
                        await server.kline(f"{base.lower()}usdt@kline_1m", f"{base}USDT", row, 60_000, True)
                await settle(0.05)

            for m in range(70):  # about 3.5 s: two planned rotations before the venue's disconnect
                await minute(m)
            await settle(0.2)
            assert len(server.connections) >= 3
            for base, (items, _task) in feeds.items():
                states = [item["state"] for item in items if item["type"] == "status"]
                assert "recovering" not in states[states.index("live"):], base
                assert states[-1] == "live"
                rows = assembled(items)
                assert [r["time"] for r in rows] == [str(T0 + m * MINUTE) for m in range(70)], base

            server.silent = True
            for m in range(70, 82):
                await minute(m, push=False)
                await settle(0.1)
            await settle(0.4)
            for base, (items, _task) in feeds.items():
                states = [item["state"] for item in items if item["type"] == "status"]
                assert states[-1] == "recovering", base
            server.silent = False
            for m in range(82, 86):
                await minute(m)
                await settle(0.2)
            await settle(0.5)
            for base, (items, _task) in feeds.items():
                states = [item["state"] for item in items if item["type"] == "status"]
                assert states[-1] == "live", base
                rows = assembled(items)
                assert [r["time"] for r in rows] == [str(T0 + m * MINUTE) for m in range(86)], base
        finally:
            stop.set()
            for _items, task in feeds.values():
                task.cancel()
            await asyncio.gather(*(task for _items, task in feeds.values()), return_exceptions=True)
            await market.close()
            await server.stop()

    asyncio.run(scenario())


def yahoo_daily(days: list[str], closes: list[float]) -> dict:
    """A Yahoo v8 chart answer with daily rows stamped at the New York open (EST)."""
    stamps = [int(common.ns_of_date(day) // 10**9) + 14 * 3600 + 1800 for day in days]
    return {"chart": {"result": [{"meta": {}, "timestamp": stamps, "events": {}, "indicators": {"quote": [{
        "open": closes, "high": [c + 1 for c in closes], "low": [c - 1 for c in closes], "close": closes,
        "volume": [1000] * len(days)}]}}], "error": None}}


@pytest.mark.parametrize("final_close", [101.0, 100.0])
def test_a_provisional_close_is_replaced_once_the_source_publishes_a_different_one(tmp_path, monkeypatch, clock,
                                                                                    final_close):
    """Yahoo's daily bar is final four hours after the close. The live poll sends 11-13 closing at 100 at
    16:01; history after 20:00 says ``final_close``: a different close ends in a replace carrying it, the
    same close sends nothing more (unadjusted series, so no corporate-action replace either)."""
    async def scenario():
        days = ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]
        egress = ReplayEgress(NASDAQ)
        # The poll shows 11-13 only once the opening replace is out, so it reaches the consumer as an append.
        released = []
        egress.add(lambda url, params: "/v8/finance/chart/" in url and params.get("range") == "5d",
                   lambda url, params: yahoo_daily(days[:5 if released else 4], [96.0, 97.0, 98.0, 99.0, 100.0]))
        egress.add(lambda url, params: "/v8/finance/chart/" in url and "period1" in params,
                   yahoo_daily(days, [96.0, 97.0, 98.0, 99.0, final_close]))
        market = Market(None, tmp_path, egress=egress)
        market.registry.entry("yfinance").source.poll_seconds = {"open": 0.05, "closed": 0.05}
        monkeypatch.setattr(live, "WRITE_EVERY", 0.05)
        await market.start(load_catalogs=False)
        await market.instruments.load("nasdaqtrader", "US", "listings")
        try:
            clock.value = common.ns_of_date("2026-11-13") + (21 * 60 + 1) * MINUTE  # 16:01 New York
            aapl = (await market.instruments.resolve({"text": "AAPL"}))["instrument"]
            series = ohlcv_series(MarketInstrument.from_wire(aapl), "1d", adjustment="raw").to_dict()
            reader = market.sessions.open({"instrument": aapl, "series": series, "warmup": 10})
            items = []

            async def until(done):
                while not done():
                    items.append(await asyncio.wait_for(reader.__anext__(), 10))
            await until(lambda: bool(opened(items)))   # the stored rows at once, then the window filled
            assert opened(items)[0]["page"]["columns"]["close"][-1] == "99"
            start = len(items)
            released.append(True)
            await until(lambda: any(i["type"] == "append" for i in items))
            assert [i for i in items if i["type"] == "append"][-1]["columns"]["close"] == ["100"]
            await settle(0.3)

            def history_requests():
                return sum(1 for r in egress.requests if "/v8/finance/chart/" in r["url"] and "period1" in r["params"])
            before = history_requests()
            clock.value = common.ns_of_date("2026-11-14") + (1 * 60 + 5) * MINUTE  # 20:05 New York
            if final_close == 100.0:
                pending = asyncio.ensure_future(reader.__anext__())
                for _ in range(100):
                    if history_requests() > before:
                        break
                    await settle(0.02)
                await settle(0.3)
                assert history_requests() == before + 1  # the provisional row was fetched again, once
                assert not pending.done() or pending.result()["type"] == "status"
                pending.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pending
            else:
                await until(lambda: any(i["type"] == "replace" for i in items[start:]))
                replace = [i for i in items[start:] if i["type"] == "replace"][-1]
                assert replace["page"]["columns"]["close"][-1] == "101"
                assert replace["page"]["columns"]["time"][-1] == str(common.ns_of_date("2026-11-13") + 5 * 60 * MINUTE)
            await reader.aclose()
        finally:
            await market.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("code", ["rate_limited", "forbidden"])
def test_a_failed_settle_keeps_the_provisional_rows_or_fails_the_series(tmp_path, monkeypatch, clock, code):
    """The fetch of a provisional row fails: rate limited, the row stays provisional and the next settle
    replaces it with the final close; forbidden, the series says failed with the source's reason."""
    from modules.market.backend.data import Fill

    async def scenario():
        days = ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]
        egress = ReplayEgress(NASDAQ)
        # The poll shows 11-13 only once the opening replace is out, so it reaches the consumer as an append.
        released = []
        egress.add(lambda url, params: "/v8/finance/chart/" in url and params.get("range") == "5d",
                   lambda url, params: yahoo_daily(days[:5 if released else 4], [96.0, 97.0, 98.0, 99.0, 100.0]))
        egress.add(lambda url, params: "/v8/finance/chart/" in url and "period1" in params,
                   yahoo_daily(days, [96.0, 97.0, 98.0, 99.0, 101.0]))
        market = Market(None, tmp_path, egress=egress)
        market.registry.entry("yfinance").source.poll_seconds = {"open": 0.05, "closed": 0.05}
        monkeypatch.setattr(live, "WRITE_EVERY", 0.05)
        await market.start(load_catalogs=False)
        await market.instruments.load("nasdaqtrader", "US", "listings")
        try:
            clock.value = common.ns_of_date("2026-11-13") + (21 * 60 + 1) * MINUTE  # 16:01 New York
            aapl = (await market.instruments.resolve({"text": "AAPL"}))["instrument"]
            series = ohlcv_series(MarketInstrument.from_wire(aapl), "1d", adjustment="raw").to_dict()
            reader = market.sessions.open({"instrument": aapl, "series": series, "warmup": 10})
            items = []

            async def until(done):
                while not done():
                    items.append(await asyncio.wait_for(reader.__anext__(), 10))
            await until(lambda: bool(opened(items)))   # the stored rows at once, then the window filled
            assert opened(items)[0]["page"]["columns"]["close"][-1] == "99"
            start = len(items)
            released.append(True)
            await until(lambda: any(i["type"] == "append" for i in items))
            await settle(0.3)
            fill, failed = market.data.fill, []

            async def failing_once(*args, **kwargs):
                if not failed:
                    failed.append(True)
                    return Fill("failed", {"code": code, "message": f"yfinance said {code}", "sourceId": "yfinance",
                                           "status": None, "retryAt": None})
                return await fill(*args, **kwargs)
            monkeypatch.setattr(market.data, "fill", failing_once)
            clock.value = common.ns_of_date("2026-11-14") + (1 * 60 + 5) * MINUTE  # 20:05 New York
            if code == "rate_limited":
                await until(lambda: any(i["type"] == "replace" for i in items[start:]))
                assert [i for i in items[start:] if i["type"] == "replace"][-1]["page"]["columns"]["close"][-1] == "101"
            else:
                await until(lambda: any(i["type"] == "status" and i["state"] == "failed" for i in items))
                status = [i for i in items if i["type"] == "status"][-1]
                assert status["problem"]["code"] == "forbidden" and status["problem"]["message"] == "yfinance said forbidden"
            assert failed
            await reader.aclose()
        finally:
            await market.close()

    asyncio.run(scenario())


