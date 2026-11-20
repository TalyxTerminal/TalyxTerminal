"""Market 2.0 sources (build criteria 26-30): renames from Alpaca, ranking, switching sources,
conformance tests and "test all", and owner approval of write_source."""

from __future__ import annotations

import asyncio
import json
import socket
from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from ib_async import BarData

from modules.market.backend import bars, calendars
from modules.market.backend.app import Market
from modules.market.backend.common import DAY_NS, NS, MarketError, now_ns, ns_of_date
from modules.market.backend.sources.base import History, Source, coverage, row
from modules.market.backend.sources.gateways import FutuSource, IbSource, local_off
from tests.fixtures.market.replay import ReplayEgress
from vocabulary.instruments import MarketInstrument, ohlcv_series

NASDAQ = ("nasdaqtrader", "symbol-directory")


def run(coro):
    return asyncio.run(coro)


def ny(day: str) -> int:
    return int(calendars.local_midnight("XNYS", np.asarray([np.datetime64(day, "D")]))[0])


async def open_us(root, egress) -> Market:
    market = Market(None, root, egress=egress)
    await market.start(load_catalogs=False)
    await market.instruments.load("nasdaqtrader", "US", "listings")
    return market


async def us(market, code):
    return (await market.instruments.resolve({"text": code, "market": "US"}))["instrument"]


async def keys(market, source_id, values):
    await market.admin.configure(source_id, values, allow_secrets=True)


# ── 26. renames from Alpaca ────────────────────────────────────────────────


def test_alpaca_renames_make_fb_and_meta_one_listing_and_map_old_index_members(tmp_path):
    async def scenario():
        egress = ReplayEgress(NASDAQ, ("alpaca", "renames"), ("yfinance", "renames"), ("sp500_history", "members"))
        market = await open_us(tmp_path, egress)
        try:
            meta, fb_etf = await us(market, "META"), await us(market, "FB")
            elv, cor = await us(market, "ELV"), await us(market, "COR")
            # Today's FB is an ETF from 2025-06-26: its code period starts at its first bar.
            await market.data.read({"instrument": fb_etf, "series": ohlcv_series(
                MarketInstrument.from_wire(fb_etf), "1d", adjustment="raw").to_dict(), "fill": "await",
                "start": str(ns_of_date("2025-06-01")), "end": str(ns_of_date("2025-07-15"))})
            assert market.identity.codes(fb_etf["instrumentId"])[0]["from"] == str(ny("2025-06-26"))
            # Without a key nothing joins FB and META.
            assert await market.load_code_history(force=True) == 0
            assert [c["code"] for c in market.identity.codes(meta["instrumentId"])] == ["META"]

            await keys(market, "alpaca", {"keyId": "test-key", "secretKey": "test-secret"})
            await market.load_code_history(force=True)
            codes = [(c["code"], c["until"]) for c in market.identity.codes(meta["instrumentId"])]
            assert codes == [("FB", str(ny("2022-06-09"))), ("META", None)]
            assert market.identity.codes(meta["instrumentId"])[1]["from"] == str(ny("2022-06-09"))
            assert [(c["code"], c["until"]) for c in market.identity.codes(elv["instrumentId"])] == \
                [("ANTM", str(ny("2022-06-28"))), ("ELV", None)]
            assert [(c["code"], c["until"]) for c in market.identity.codes(cor["instrumentId"])] == \
                [("ABC", str(ny("2023-08-30"))), ("COR", None)]
            assert market.identity.codes(fb_etf["instrumentId"]) == \
                [{"code": "FB", "name": market.identity.codes(fb_etf["instrumentId"])[0]["name"],
                  "from": str(ny("2025-06-26")), "until": None}]

            found = await market.instruments.search({"query": "FB", "markets": ["US"]})
            by_id = {r["instrument"]["instrumentId"]: r for r in found["results"]}
            assert by_id[meta["instrumentId"]]["formerCode"] == {"code": "FB", "until": str(ny("2022-06-09"))}
            assert fb_etf["instrumentId"] in by_id and by_id[fb_etf["instrumentId"]]["formerCode"] is None

            index = (await market.instruments.resolve({"text": "^GSPC"}))["instrument"] \
                if market.identity.find_code("US", "^GSPC") else None
            if index is None:
                market.identity.upsert_listings("US", [{"type": "listing", "market": "US", "code": "^GSPC",
                                                        "name": "S&P 500", "assetClass": "index"}], source="test")
                index = (await market.instruments.resolve({"text": "^GSPC"}))["instrument"]
            page = await market.research.read({"kind": "members", "index": index, "limit": 5000,
                                               "at": str(ns_of_date("2020-03-03"))})
            mapped = dict(zip(page["columns"]["code"], page["columns"]["instrumentId"], strict=True))
            assert mapped["FB"] == meta["instrumentId"]
            assert mapped["ANTM"] == elv["instrumentId"]
            assert mapped["ABC"] == cor["instrumentId"]
            # A second run asks Alpaca again (forced) but changes nothing.
            assert await market.load_code_history(force=True) == 0
        finally:
            await market.close()
    run(scenario())


# ── 27. ranking ────────────────────────────────────────────────────────────


def test_ranking_of_us_daily_and_intraday_follows_keys_and_switches(tmp_path):
    async def scenario():
        market = await open_us(tmp_path, ReplayEgress(NASDAQ))
        try:
            aapl = await us(market, "AAPL")

            def first(timeframe):
                return market.registry.rank(aapl, "bars", timeframe)[0][0]

            assert (first("1d"), first("1m")) == ("yfinance", "yfinance")
            await keys(market, "alpaca", {"keyId": "k", "secretKey": "s"})
            assert (first("1d"), first("1m"), first("5m")) == ("yfinance", "alpaca", "alpaca")
            await keys(market, "sharadar", {"apiKey": "k"})
            assert (first("1d"), first("1m")) == ("sharadar", "alpaca")
            await market.admin.write({"operation": {"op": "disable", "source": "alpaca"}})
            assert (first("1d"), first("1m")) == ("sharadar", "yfinance")
            await market.admin.configure("ib", {"port": 4002}, allow_secrets=False)
            for timeframe in ("1d", "1m", "1h"):
                assert "ib" not in [sid for sid, _row in market.registry.rank(aapl, "bars", timeframe)]
            selection = (await market.admin.read({"view": "selection", "market": "US"}))["selection"]
            daily = next(item for item in selection if item["kind"] == "bars" and item["timeframes"] == "daily")
            assert daily["source"]["sourceId"] == "sharadar" and "delisted" in daily["reason"]
        finally:
            await market.close()
    run(scenario())


# ── 28. switching sources ──────────────────────────────────────────────────


# ── 29. conformance tests and "test all" ───────────────────────────────────


def minute_bars(start: int, end: int) -> list[BarData]:
    """IB's documented reqHistoricalData answer (formatDate=2): UTC datetimes, regular hours."""
    opens, _closes = calendars.bar_grid("XNYS", "1m", start, end)
    rows = []
    for index, moment in enumerate(opens.tolist()):
        price = 190 + (index % 50) / 10
        rows.append(BarData(date=datetime.fromtimestamp(moment / NS, UTC), open=price, high=price + 0.2,
                            low=price - 0.2, close=price + 0.1, volume=1200.0 + index, average=price, barCount=40))
    return [bar for bar in rows if int(bar.date.timestamp()) * NS + 60 * NS <= now_ns()]


class FakeIB:
    def isConnected(self):
        return True

    async def connectAsync(self, *_args, **_kwargs):
        return self

    async def reqHistoricalDataAsync(self, contract, endDateTime, durationStr, barSizeSetting, **_kwargs):
        if contract.symbol.startswith("ZZZZQ"):
            return []  # IB answers an unknown contract with no bars (and error 200 on the event stream)
        end = int(endDateTime.timestamp()) * NS
        days = int(durationStr.split()[0])
        return minute_bars(end - days * DAY_NS, end)

    def disconnect(self):
        pass


class FakeFutu:
    """futu-api's OpenQuoteContext.request_history_kline as documented: (ret, DataFrame, page_req_key)."""

    def request_history_kline(self, code, start, end, ktype, autype, max_count, page_req_key):
        if "ZZZZQ" in code:
            return -1, "Unknown stock ZZZZQ", None
        opens, _closes = calendars.bar_grid("XNYS", "1m", ns_of_date(start), ns_of_date(end) + DAY_NS)
        ends = [moment + 60 * NS for moment in opens.tolist() if moment + 60 * NS <= now_ns()]
        local = pd.DatetimeIndex(np.asarray(ends, dtype="datetime64[ns]")).tz_localize("UTC") \
            .tz_convert("America/New_York").strftime("%Y-%m-%d %H:%M:%S")
        frame = pd.DataFrame({"code": code, "name": "APPLE", "time_key": list(local),
                              "open": 190.0, "close": 190.1, "high": 190.3, "low": 189.9, "pe_ratio": 30.0,
                              "turnover_rate": 0.0001, "volume": 1000, "turnover": 190000.0,
                              "change_rate": 0.01, "last_close": 189.0})
        return 0, frame, None

    def close(self):
        pass


def databento_day(_url, params):
    """Databento's documented JSON lines (pretty_px, pretty_ts) for ohlcv-1m."""
    if params["symbols"].startswith("ZZZZQ"):
        return ""
    start = int(datetime.fromisoformat(params["start"].replace("Z", "+00:00")).timestamp()) * NS
    end = int(datetime.fromisoformat(params["end"].replace("Z", "+00:00")).timestamp()) * NS
    opens, _closes = calendars.bar_grid("XNYS", "1m", start, end)
    lines = []
    for moment in opens.tolist():
        if moment + 60 * NS > now_ns():
            continue
        stamp = datetime.fromtimestamp(moment / NS, UTC).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
        lines.append(json.dumps({"hd": {"ts_event": stamp, "rtype": 33, "publisher_id": 2, "instrument_id": 38},
                                 "open": "190.500000000", "high": "190.700000000", "low": "190.400000000",
                                 "close": "190.600000000", "volume": "1500", "symbol": params["symbols"]}))
    return "\n".join(lines)


class Faulty(Source):
    """Pages that repeat a row and times in milliseconds: exactly the paging and unit checks fail."""

    id = "faulty"
    descriptor = {"name": "Faulty test source", "sourceIds": ["faulty"], "kinds": ["bars"], "assetClasses": ["equity"],
                  "credentials": "none", "paid": False, "supports": ["history"]}

    async def describe(self, source_id):
        return coverage([row({"markets": ["US"]}, "bars", [("1d", "P3650D", 5)])], concurrency=1, rpm=None)

    async def history(self, source_id, target, kind, timeframe, price_basis, start, end):
        if target.code_at().startswith("ZZZZQ"):
            raise MarketError("not_found", "no such code", source_id=source_id)
        days = [d for d in range(start // DAY_NS + 1, end // DAY_NS)][:6]
        times = np.asarray([d * DAY_NS // 10**6 for d in days], dtype=np.int64)  # milliseconds: wrong
        columns = {"time": times, "close_time": times + 86_400_000, "open": np.ones(len(times)),
                   "high": np.ones(len(times)), "low": np.ones(len(times)), "close": np.ones(len(times)),
                   "volume": np.ones(len(times))}
        page = bars.take(columns, np.arange(len(times)) < 5)
        # The next page starts one row early: the fifth row comes again.
        return History(page, next=days[4] * DAY_NS if len(days) > 5 else None)


def test_conformance_checks_pass_for_ib_databento_futu_and_catch_a_faulty_source(tmp_path, monkeypatch):
    async def scenario():
        egress = ReplayEgress()
        egress.add(lambda url, params: "hist.databento.com" in url, databento_day)
        market = Market(None, tmp_path, egress=egress)
        market.registry.add(Faulty(market))
        monkeypatch.setattr(IbSource, "client", lambda self: FakeIB())
        monkeypatch.setattr(FutuSource, "context", lambda self: FakeFutu())
        await market.start(load_catalogs=False)
        try:
            await keys(market, "databento", {"apiKey": "test-key", "dataset": "XNAS.ITCH"})
            result = await market.admin.write({"operation": {"op": "test",
                                                             "sources": ["ib", "databento", "futu", "faulty"]}})
            tests = {item["source"]: item for item in result["tests"]}
            for name in ("ib", "databento", "futu"):
                assert tests[name]["failed"] == [], (name, tests[name]["failed"])
                assert tests[name]["unreachable"] is None and tests[name]["passed"] >= 6
                assert isinstance(tests[name]["latencyMs"], int)
            assert sorted(item["check"] for item in tests["faulty"]["failed"]) == ["paging", "time_unit"]
            assert isinstance(tests["faulty"]["latencyMs"], int)
        finally:
            await market.close()
    run(scenario())


class Fake(Source):
    def __init__(self, market, source_id, behaviour, *, credentials="none", local=False):
        super().__init__(market)
        self.id, self.behaviour = source_id, behaviour
        self.descriptor = {"name": source_id, "sourceIds": [source_id], "kinds": ["bars"],
                           "assetClasses": ["equity"], "credentials": credentials, "paid": False,
                           "supports": ["history"], **({"localGateway": True} if local else {})}

    async def describe(self, source_id):
        if self.behaviour == "rejected":
            raise MarketError("unauthorized", "Invalid token", source_id=source_id, status=401)
        return coverage([row({"markets": ["US"]}, "bars", [("1d", "P30D", 100)])], concurrency=1, rpm=None)

    async def history(self, source_id, target, kind, timeframe, price_basis, start, end):
        if self.behaviour == "451":
            raise MarketError("forbidden", "Service unavailable from a restricted location", source_id=source_id,
                              status=451)
        if self.behaviour == "slow":
            await asyncio.sleep(12)
        if self.behaviour == "local":
            port = _closed_port()
            try:
                _reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
            except OSError as exc:
                raise local_off(source_id, "Test gateway", exc) from exc
        await asyncio.sleep(0.02)
        return History(bars.empty())


def _closed_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def test_test_all_reports_latency_or_why_for_every_source_within_ten_seconds(tmp_path):
    async def scenario():
        market = Market(None, tmp_path, egress=ReplayEgress(), sources=[])
        for source_id, behaviour, credentials, local in (("ok", "ok", "none", False), ("blocked", "451", "none", False),
                                                         ("silent", "slow", "none", False),
                                                         ("keyed", "ok", "required", False),
                                                         ("gateway", "local", "none", True),
                                                         ("switched_off", "ok", "none", False)):
            market.registry.add(Fake(market, source_id, behaviour, credentials=credentials, local=local))
        market.registry.set_enabled("switched_off", False)
        await market.start(load_catalogs=False)
        try:
            started = asyncio.get_running_loop().time()
            result = await market.admin.write({"operation": {"op": "test_all"}})
            elapsed = asyncio.get_running_loop().time() - started
            assert 10 <= elapsed < 11.5
            tests = {item["source"]: item for item in result["tests"]}
            assert isinstance(tests["ok"]["latencyMs"], int) and tests["ok"]["unreachable"] is None
            assert (tests["blocked"]["unreachable"], tests["blocked"]["httpStatus"]) == ("http_error", 451)
            assert tests["silent"]["unreachable"] == "timeout" and tests["silent"]["latencyMs"] is None
            assert tests["keyed"]["unreachable"] == "key_missing"
            assert tests["gateway"]["unreachable"] == "local_program_off"
            assert isinstance(tests["switched_off"]["latencyMs"], int)
            listed = {item["source"]["sourceId"]: item for item in
                      (await market.admin.read({"view": "list"}))["sources"]}
            assert listed["switched_off"]["state"] == "off" and listed["keyed"]["state"] == "not_configured"
            # A source whose last test did not get through is not listed as working.
            assert listed["ok"]["state"] == "working" and listed["ok"]["problem"] is None
            assert (listed["blocked"]["state"], listed["blocked"]["problem"]["code"],
                    listed["blocked"]["problem"]["status"]) == ("failing", "forbidden", 451)
            assert listed["silent"]["state"] == "failing" and listed["gateway"]["state"] == "failing"
        finally:
            await market.close()
    run(scenario())


def test_a_single_test_that_did_not_get_through_stands_until_the_next(tmp_path):
    """Testing one source (as after typing its key) leaves the same mark as testing all: a rejected key is listed
    as failing, not as working, until a test gets through."""
    async def scenario():
        market = Market(None, tmp_path, egress=ReplayEgress(), sources=[])
        source = Fake(market, "keyed", "ok")
        market.registry.add(source)
        await market.start(load_catalogs=False)
        try:
            source.behaviour = "rejected"
            result = await market.admin.write({"operation": {"op": "test", "sources": ["keyed"]}})
            assert result["tests"][0]["unreachable"] == "key_rejected"
            listed = (await market.admin.read({"view": "list"}))["sources"][0]
            assert (listed["state"], listed["problem"]["code"]) == ("failing", "unauthorized")
            source.behaviour = "ok"
            await market.admin.write({"operation": {"op": "test", "sources": ["keyed"]}})
            assert (await market.admin.read({"view": "list"}))["sources"][0]["state"] == "working"
        finally:
            await market.close()
    run(scenario())


# ── 30. settings, never keys ───────────────────────────────────────────────
# Whether an AI may call write_source at all is decided by the AI side from the declared effects; Market itself
# never asks for approval.


def test_write_source_sets_declared_settings_and_never_a_key(tmp_path):
    secrets = {}

    async def secret_configured(key):
        return key in secrets

    async def secret(key):
        return secrets.get(key)

    async def set_secret(key, value):
        secrets[key] = value

    ctx = SimpleNamespace(platform=SimpleNamespace(secret=secret, set_secret=set_secret,
                                                   secret_configured=secret_configured))

    async def scenario():
        market = Market(ctx, tmp_path, egress=ReplayEgress())
        await market.start(load_catalogs=False)
        try:
            await market.admin.write({"operation": {"op": "configure", "source": "ib", "settings": {"port": 4002}}})
            call = SimpleNamespace(call_id="call-port", workspace_id="work",
                                   caller=SimpleNamespace(origin="ai", run={"id":"run-source"}))
            await market.admin.write({"operation":{"op":"configure","source":"ib","settings":{"port":4003}}}, call)
            await market.admin.write({"operation":{"op":"configure","source":"ib","settings":{"clientId":22}}})
            changes = market.registry.preferences.list(["call-port"], "run-source")
            assert [item["objectId"] for item in changes] == ["ib"]
            for direction, port in (("undo",4002),("redo",4003)):
                await market.admin.revert("call-port", direction)
                ib = market.registry.entry("ib").source
                assert ib.settings["port"] == port and ib.settings["clientId"] == 22
            await market.admin.write({"operation": {"op": "disable", "source": "yfinance"}})
            assert not market.registry.enabled("yfinance")
            with pytest.raises(MarketError) as secret_setting:
                await market.admin.write({"operation": {"op": "configure", "source": "alpaca",
                                                        "settings": {"keyId": "x"}}})
            assert secret_setting.value.code == "invalid_input" and secrets == {}
        finally:
            await market.close()
    run(scenario())
    contract = json.loads(open("modules/market/public/contract.json", encoding="utf-8").read())
    assert {"write", "external"} <= set(contract["actions"]["write_source"]["effects"])
