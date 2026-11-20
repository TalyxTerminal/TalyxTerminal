"""Market's only network egress.

Every outbound connection Market makes starts here: aiohttp requests and
WebSockets, ``requests`` sessions handed to libraries, CCXT clients and raw TCP
(Baostock). Each connection reads the user's system proxy at connect time
(``system_http_proxy_url``: the Windows switch decides, a stale ProxyServer with
ProxyEnable=0 is ignored); TUN mode needs nothing. Source failures are mapped to
Market's codes here, so no library exception escapes as ``handler_error``.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import socket
import ssl
import time
from collections.abc import Iterable
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import aiohttp
import requests
from talyx_sdk.network import system_client_session, system_http_proxy_url

from .common import NS, MarketError, now_ns

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/130.0 Safari/537.36",
    "Accept": "*/*",
}
DEFAULT_TIMEOUT = 20.0


def proxy() -> str | None:
    return system_http_proxy_url()


def retry_at_of(headers, default_seconds: float = 60.0) -> int:
    value = headers.get("Retry-After") if headers else None
    if value:
        value = value.strip()
        if value.isdigit():
            return now_ns() + int(value) * NS
        try:
            return int(parsedate_to_datetime(value).timestamp()) * NS
        except (TypeError, ValueError):
            pass
    return now_ns() + int(default_seconds * NS)


# what a refusal means, in words; the request's address and the source's own response stay out of the message.
REFUSED = {402: "it needs a paid plan", 403: "access denied", 451: "not available from this region"}


def status_error(source_id: str, status: int, body: str, headers=None, url: str | None = None) -> MarketError:
    if status == 429 or status == 418:
        return MarketError("rate_limited", f"{source_id} is rate limiting requests (HTTP {status})", source_id=source_id,
                           status=status, retry_at=retry_at_of(headers))
    if status == 401:
        return MarketError("unauthorized", f"{source_id} rejected the key (HTTP 401)", source_id=source_id, status=status)
    if status in REFUSED:
        return MarketError("forbidden", f"{source_id} refused the request (HTTP {status}: {REFUSED[status]})",
                           source_id=source_id, status=status)
    if status == 404:
        return MarketError("not_found", f"{source_id} has no such data (HTTP 404)", source_id=source_id,
                           status=status)
    return MarketError("source_unavailable", f"{source_id} answered HTTP {status}", source_id=source_id,
                       status=status)


def handshake_status(error) -> int | None:
    """The HTTP status a refused WebSocket handshake answered, if ``error`` is one."""
    return error.status if isinstance(error, aiohttp.WSServerHandshakeError) else None


class Reachability:
    """Tells one source being down from the whole network being down.

    When a source cannot be reached, Market asks whether any other host answers;
    if none does, the failure is the user's network (network_unreachable).
    """

    def __init__(self, probes: Iterable[str] = ("https://www.okx.com/api/v5/public/time",
                                                "https://data-api.binance.vision/api/v3/ping",
                                                "https://query1.finance.yahoo.com/")):
        self.probes = list(probes)
        self._recent_ok: dict[str, float] = {}
        self._verdict: tuple[float, bool] | None = None
        self._lock = asyncio.Lock()

    def succeeded(self, url: str) -> None:
        self._recent_ok[urlsplit(url).netloc] = time.monotonic()

    async def network_up(self, egress: Egress, failed_url: str) -> bool:
        failed = urlsplit(failed_url).netloc
        if any(host != failed and time.monotonic() - at < 5 for host, at in self._recent_ok.items()):
            return True
        async with self._lock:
            if self._verdict and time.monotonic() - self._verdict[0] < 5:
                return self._verdict[1]
            targets = [url for url in self.probes if urlsplit(url).netloc != failed]

            results = await asyncio.gather(*(egress.probe(url) for url in targets))
            self._verdict = (time.monotonic(), any(results))
            return self._verdict[1]


class Egress:
    def __init__(self, reachability: Reachability | None = None):
        self._session: aiohttp.ClientSession | None = None
        self.reachability = reachability or Reachability()

    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = system_client_session(timeout=aiohttp.ClientTimeout(total=None))
        return self._session

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()

    async def probe(self, url: str) -> bool:
        """Whether ``url`` answers at all (any HTTP status counts)."""
        try:
            async with self.session().get(url, proxy=proxy(), timeout=aiohttp.ClientTimeout(total=4),
                                          allow_redirects=False) as response:
                await response.read()
                return True
        except (aiohttp.ClientError, OSError, TimeoutError):
            return False

    async def transport_failure(self, source_id: str, url: str, exc: BaseException) -> MarketError:
        words = f"{type(exc).__name__}: {exc}".strip()[:280]
        if await self.reachability.network_up(self, url):
            return MarketError("source_unavailable", f"Cannot reach {source_id}: {words}", source_id=source_id)
        return MarketError("network_unreachable", f"The network is unreachable ({words})", source_id=source_id)

    async def request(self, source_id: str, url: str, *, method: str = "GET", params=None, headers=None,
                      data=None, json_body=None, timeout: float = DEFAULT_TIMEOUT, cookies=None,
                      accept_status: Iterable[int] = ()) -> tuple[int, bytes, Any]:
        """One HTTP exchange; non-2xx statuses become Market errors unless accepted."""
        try:
            async with self.session().request(
                method, url, params=params, headers={**BROWSER_HEADERS, **(headers or {})}, data=data,
                json=json_body, cookies=cookies, proxy=proxy(), timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                body = await response.read()
                status, response_headers = response.status, response.headers
        except (aiohttp.ClientError, OSError, TimeoutError, ssl.SSLError) as exc:
            raise await self.transport_failure(source_id, url, exc) from exc
        self.reachability.succeeded(url)
        if status >= 400 and status not in set(accept_status):
            raise status_error(source_id, status, body.decode("utf-8", "replace"), response_headers, url)
        return status, body, response_headers

    async def json(self, source_id: str, url: str, **options) -> Any:
        _status, body, _headers = await self.request(source_id, url, **options)
        try:
            return json.loads(body)
        except ValueError as exc:
            raise MarketError("source_unavailable", f"{source_id} returned malformed JSON: {body[:200]!r}",
                              source_id=source_id) from exc

    async def bytes(self, source_id: str, url: str, **options) -> bytes:
        return (await self.request(source_id, url, **options))[1]

    async def websocket(self, source_id: str, url: str, *, heartbeat: float | None = None,
                        headers=None) -> aiohttp.ClientWebSocketResponse:
        try:
            return await self.session().ws_connect(url, proxy=proxy(), heartbeat=heartbeat,
                                                   headers={**BROWSER_HEADERS, **(headers or {})},
                                                   timeout=aiohttp.ClientWSTimeout(ws_close=5),
                                                   max_msg_size=64 * 1024 * 1024)
        except aiohttp.WSServerHandshakeError as exc:
            raise status_error(source_id, exc.status, exc.message, exc.headers, url) from exc
        except (aiohttp.ClientError, OSError, TimeoutError) as exc:
            raise await self.transport_failure(source_id, url, exc) from exc


def requests_session() -> requests.Session:
    """A ``requests`` session for libraries that call requests themselves (AKShare)."""
    session = requests.Session()
    session.trust_env = False
    current = proxy()
    if current:
        session.proxies = {"http": current, "https": current}
    session.headers.update(BROWSER_HEADERS)
    return session


def requests_proxies() -> dict[str, str] | None:
    current = proxy()
    return {"http": current, "https": current} if current else None


def ccxt_configure(client) -> None:
    """Point a CCXT client at the current system proxy (REST and WebSocket)."""
    current = proxy()
    client.httpsProxy = current
    client.wssProxy = current
    client.aiohttp_trust_env = False


BINANCE_SPOT_DATA_API = "https://data-api.binance.vision/api/v3"
# Futures exchangeInfo (with onboardDate) from the main site; fapi/dapi answer 451 in some regions.
BINANCE_FUTURES_CATALOG = {"fapiPublic": "https://www.binance.com/fapi/v1", "dapiPublic": "https://www.binance.com/dapi/v1"}


def ccxt_client(exchange_id: str, *, environment: str = "production", pro: bool = False):
    """A CCXT async (or CCXT Pro) client whose REST goes through the platform exchange queue."""
    import ccxt.async_support
    import ccxt.pro
    from talyx_sdk import rest_fuse

    module = ccxt.pro if pro else ccxt.async_support
    client = rest_fuse.fused(getattr(module, exchange_id))({"timeout": 20000, "enableRateLimit": False})
    if exchange_id == "binance":
        # The spot market-data domains answer where api.binance.com and stream.binance.com are region-blocked.
        client.urls["api"]["public"] = BINANCE_SPOT_DATA_API
        if pro and isinstance(client.urls["api"].get("ws"), dict) and "spot" in client.urls["api"]["ws"]:
            client.urls["api"]["ws"]["spot"] = "wss://data-stream.binance.vision/ws"
    if exchange_id == "okx" and "ws" in client.urls["api"]:
        client.urls["api"]["ws"] = client.urls["api"]["ws"].replace(":8443/", "/")
    if environment != "production":
        if (exchange_id, environment) == ("binance", "demo"):
            client.enable_demo_trading(True)
        elif (exchange_id, environment) == ("gate", "testnet"):
            client.set_sandbox_mode(True)
        else:
            raise MarketError("unsupported", f"{exchange_id} has no {environment} market data", source_id=exchange_id)
        client.options["fetchCurrencies"] = False
    ccxt_configure(client)
    return client


def open_tcp(host: str, port: int, timeout: float = 15.0) -> socket.socket:
    """A connected TCP socket to host:port, tunnelled through the system proxy when one is set.

    Used for libraries speaking raw TCP (Baostock's port 10030): with an HTTP proxy
    the socket is an HTTP CONNECT tunnel; without one it is a direct connection.
    """
    current = proxy()
    if current is None:
        return socket.create_connection((host, port), timeout=timeout)
    parts = urlsplit(current)
    tunnel = socket.create_connection((parts.hostname, parts.port or 80), timeout=timeout)
    try:
        request = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n"
        if parts.username:
            token = base64.b64encode(f"{parts.username}:{parts.password or ''}".encode()).decode()
            request += f"Proxy-Authorization: Basic {token}\r\n"
        tunnel.sendall((request + "\r\n").encode())
        reply = b""
        while b"\r\n\r\n" not in reply:
            chunk = tunnel.recv(4096)
            if not chunk:
                raise OSError("Proxy closed the CONNECT tunnel")
            reply += chunk
        status = reply.split(b"\r\n", 1)[0].split()
        if len(status) < 2 or status[1] != b"200":
            line = reply.split(b"\r\n", 1)[0].decode(errors="replace")
            raise OSError(f"Proxy refused CONNECT {host}:{port}: {line}")
        return tunnel
    except BaseException:
        tunnel.close()
        raise


def patch_baostock() -> None:
    """Route the Baostock library's one TCP connection through ``open_tcp`` (the proxy tunnel).

    The library opens its socket in ``SocketUtil.connect`` and keeps it in a module
    global; this replaces that method once per process.
    """
    import baostock.common.contants as constants
    import baostock.common.context as context
    import baostock.util.socketutil as socketutil

    if getattr(socketutil.SocketUtil.connect, "talyx", False):
        return

    def connect(self, api_key):
        host = constants.BAOSTOCK_VIP_SERVER_IP if str(api_key).startswith("bs-") else constants.BAOSTOCK_SERVER_IP
        connection = open_tcp(host, constants.BAOSTOCK_SERVER_PORT)
        connection.settimeout(120)
        old = getattr(context, "default_socket", None)
        if old is not None:
            with contextlib.suppress(OSError, AttributeError):
                old.close()
        context.default_socket = connection

    connect.talyx = True
    socketutil.SocketUtil.connect = connect
