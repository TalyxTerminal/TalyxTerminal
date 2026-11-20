"""Market's only egress: every official source goes through the system proxy (build criterion 10)."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import socket
import sys
import types
from pathlib import Path

import pytest

from modules.market.backend import network
from modules.market.backend.app import Market
from modules.market.backend.common import MarketError
from modules.market.backend.sources.base import Target

pytestmark = pytest.mark.market_egress

BACKEND = Path(__file__).parents[2] / "modules" / "market" / "backend"
NETWORK_LIBRARIES = {"socket", "aiohttp", "requests", "websockets", "httpx", "curl_cffi"}


def test_only_the_network_module_imports_network_libraries():
    offenders = []
    for path in BACKEND.rglob("*.py"):
        if path.name == "network.py" and path.parent == BACKEND:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            offenders += [f"{path.relative_to(BACKEND)}: {name}" for name in names
                          if name.split(".")[0] in NETWORK_LIBRARIES]
    assert offenders == []


def fake_winreg(values: dict) -> types.ModuleType:
    """Internet Settings as Windows stores them (HKCU), for both urllib and the SDK."""
    module = types.ModuleType("winreg")
    module.HKEY_CURRENT_USER = object()
    module.REG_SZ, module.REG_DWORD = 1, 4

    class Key:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def Close(self):  # noqa: N802 - winreg spelling
            pass

    def open_key(parent, path, *args):
        if isinstance(parent, Key) or path.endswith("Connections"):
            raise FileNotFoundError(path)
        return Key(path)

    def query(key, name):
        if name not in values:
            raise FileNotFoundError(name)
        return values[name], 1

    module.OpenKey = open_key
    module.QueryValueEx = query
    return module


@pytest.fixture
def system_proxy(monkeypatch):
    def configure(values: dict) -> None:
        monkeypatch.setitem(sys.modules, "winreg", fake_winreg(values))
        monkeypatch.setattr(sys, "platform", "win32")
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy",
                     "NO_PROXY", "no_proxy"):
            monkeypatch.delenv(name, raising=False)
    return configure


def test_a_switched_off_proxy_is_not_used(system_proxy):
    if not hasattr(__import__("urllib.request").request, "getproxies_registry"):
        pytest.skip("urllib reads the registry on Windows only")
    system_proxy({"ProxyEnable": 0, "ProxyServer": "127.0.0.1:7897"})
    assert network.proxy() is None
    system_proxy({"ProxyEnable": 1, "ProxyServer": "127.0.0.1:7897"})
    assert network.proxy() == "http://127.0.0.1:7897"


class RecordingProxy:
    """An HTTP CONNECT proxy on 127.0.0.1 that records the hosts asked for and refuses them."""

    def __init__(self):
        self.hosts: list[str] = []
        self.server = None

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def _serve(self, reader, writer):
        try:
            line = (await reader.readline()).decode("latin-1").split()
            if len(line) >= 2:
                target = line[1]
                host = target.split("://", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0]
                self.hosts.append(host)
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
        finally:
            writer.close()

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()


def test_every_official_source_goes_through_the_system_proxy(tmp_path, system_proxy, monkeypatch):
    if not hasattr(__import__("urllib.request").request, "getproxies_registry"):
        pytest.skip("urllib reads the registry on Windows only")
    direct: list[str] = []
    local = {"127.0.0.1", "localhost", "::1"}
    real_getaddrinfo, real_create, real_connect = socket.getaddrinfo, socket.create_connection, socket.socket.connect

    def getaddrinfo(host, *args, **kwargs):
        if host not in local:
            direct.append(str(host))
            raise OSError(f"direct lookup of {host} blocked (test)")
        return real_getaddrinfo(host, *args, **kwargs)

    def create_connection(address, *args, **kwargs):
        if address[0] not in local:
            direct.append(str(address[0]))
            raise OSError(f"direct connection to {address[0]} blocked (test)")
        return real_create(address, *args, **kwargs)

    def connect(self, address):
        if isinstance(address, tuple) and address[0] not in local:
            direct.append(str(address[0]))
            raise OSError(f"direct connection to {address[0]} blocked (test)")
        return real_connect(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    monkeypatch.setattr(socket.socket, "connect", connect)

    async def scenario():
        proxy = RecordingProxy()
        port = await proxy.start()
        system_proxy({"ProxyEnable": 1, "ProxyServer": f"127.0.0.1:{port}"})
        market = Market(None, tmp_path)
        market.egress.reachability.probes = []
        await market.start(load_catalogs=False)
        aapl = {"instrumentId": "vi2_" + "0" * 32, "identityVersion": 2, "venueId": "US", "symbol": "AAPL",
                "calendarId": "XNYS"}
        btc = {"instrumentId": "vi1_x", "identityVersion": 1, "venueId": "okx", "symbol": "BTC/USDT:USDT",
               "calendarId": "24_7"}
        calls = {
            "query2.finance.yahoo.com": [
                lambda: market.registry.call("yfinance", "search", "AAPL", {}, 5),
                lambda: market.registry.call("yfinance", "history", Target(aapl, []), "bars", "1d", "last",
                                             1_700_000_000 * 10**9, 1_700_900_000 * 10**9),
            ],
            "www.nasdaqtrader.com": [lambda: market.registry.call("nasdaqtrader", "catalog", {"markets": ["US"]},
                                                                  "production", None)],
            "www.okx.com": [lambda: market.registry.call("okx", "history", Target(btc, []), "bars", "1m", "last",
                                                         1_700_000_000 * 10**9, 1_700_060_000 * 10**9)],
        }
        market.registry.entry("sec").source.settings["contactEmail"] = "research-test@example.com"
        crypto = market.registry.entry("binance").source
        gate_btc = {**btc, "venueId": "gate", "marketType": "swap", "settle": "USDT", "nativeId": "BTC_USDT",
                    "contractSize": "0.0001"}
        index = {**aapl, "symbol": "^GSPC", "assetClass": "index"}
        calls.update({
            "s3-ap-northeast-1.amazonaws.com": [lambda: crypto.files["binance"].listing(
                "data/futures/um/monthly/klines/")],
            "data.binance.vision": [lambda: crypto.files["binance"].rows(
                "https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2024-01.zip")],
            "download.gatedata.org": [lambda: crypto.files["gate"].history(
                Target(gate_btc, []), "bars", "1h", "last", 1_704_067_200 * 10**9, 1_706_745_600 * 10**9,
                ("month", 1_704_067_200 * 10**9))],
            "api.coingecko.com": [lambda: market.registry.call("coingecko", "catalog", {"markets": ["COIN"]},
                                                               "production", None)],
            "www.sec.gov": [lambda: market.registry.call("sec", "research", Target(aapl, []), "financials")],
            "raw.githubusercontent.com": [lambda: market.registry.call("sp500_history", "research", Target(index, []),
                                                                       "members")],
            # Baostock speaks raw TCP: its connection is a CONNECT tunnel to port 10030.
            "public-api.baostock.com": [lambda: market.registry.call("baostock", "catalog", {"markets": ["CN"]},
                                                                     "production", None)],
            "api.tushare.pro": [lambda: market.registry.call("tushare", "bulk", "bars", "1d", {"markets": ["CN"]}, [],
                                                             1_718_640_000 * 10**9, 1_718_726_400 * 10**9)],
            "www.hkex.com.hk": [lambda: market.registry.call("hkex", "catalog", {"markets": ["HK"]}, "production",
                                                             None)],
        })
        await market.registry.entry("tushare").source.configure("tushare", {"token": "test-token"})
        # Every CCXT venue's REST catalog (the 14 exchanges): the host is the venue's own API domain.
        venues = {"binance": "binance", "bitfinex": "bitfinex", "bitget": "bitget", "bybit": "bybit",
                  "coinbase": "coinbase", "cryptocom": "crypto.com", "deribit": "deribit", "gate": "gate",
                  "htx": "h", "hyperliquid": "hyperliquid", "kraken": "kraken", "kucoin": "kucoin", "mexc": "mexc",
                  "okx": "okx"}
        try:
            for host, steps in calls.items():
                for step in steps:
                    before = len(proxy.hosts)
                    with pytest.raises(MarketError) as raised:
                        await step()
                    assert raised.value.code != "handler_error"
                    assert host in proxy.hosts[before:], (host, proxy.hosts)
            for venue, part in venues.items():
                before = len(proxy.hosts)
                with pytest.raises(MarketError) as raised:
                    await market.registry.call(venue, "catalog", {"venues": [venue]}, "production", None)
                assert raised.value.code != "handler_error"
                assert any(part in host for host in proxy.hosts[before:]), (venue, proxy.hosts[before:])
            # Live: OKX and Binance over Market's own WebSocket pools, the others over CCXT Pro.
            target = Target({**btc, "symbol": "BTC/USDT", "nativeId": "BTC-USDT", "marketType": "spot"}, [])
            for venue, part in (("okx", "ws.okx.com"), ("kraken", "kraken")):
                source = market.registry.entry(venue).source
                if venue == "okx":
                    client = source.client("okx")
                    client.markets = {"BTC/USDT": {"id": "BTC-USDT", "spot": True}}
                    client.markets_by_id = {}
                    events = source.live(venue, target, "bars", "1m")
                else:
                    client = source.client("kraken")
                    client.markets = {"BTC/USDT": {"id": "XBT/USDT", "spot": True, "symbol": "BTC/USDT"}}
                    source.pro_client("kraken").markets = client.markets
                    events = source.live(venue, target, "ticker", None)
                before = len(proxy.hosts)
                task = asyncio.ensure_future(anext(events))
                for _ in range(50):
                    if any(part in host for host in proxy.hosts[before:]):
                        break
                    await asyncio.sleep(0.1)
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
                await events.aclose()
                assert any(part in host for host in proxy.hosts[before:]), (venue, proxy.hosts[before:])
        finally:
            await market.close()
            await proxy.stop()

    asyncio.run(scenario())
    assert direct == []
