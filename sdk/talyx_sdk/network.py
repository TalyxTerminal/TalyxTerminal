"""System proxy, DNS and HTTP transport primitives, without any API's own policy."""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, TypeVar
from urllib import parse, request

import aiohttp

from .network_changes import (  # noqa: F401  (route_observation re-exported for kernel.platform)
    route_observation,
    system_route_epoch,
)


class NetworkConfigurationError(ValueError):
    """A configured system route cannot be represented; never fall back to direct."""

    code = "network_proxy_unsupported"


class NetworkRouteChanged(Exception):
    """Retire the old transport before retrying with the current system route."""


def _windows_proxy_settings() -> dict[str, Any]:
    if sys.platform != "win32":
        return {}
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            result = {}
            for name in ("AutoConfigURL", "AutoDetect", "ProxyEnable", "ProxyServer"):
                try:
                    result[name] = winreg.QueryValueEx(key, name)[0]
                except FileNotFoundError:
                    pass
            # Windows commonly stores automatic-proxy enable flags only in this
            # binary connection record; URL values can remain after disabling PAC.
            try:
                with winreg.OpenKey(key, "Connections") as connection:
                    data = winreg.QueryValueEx(connection, "DefaultConnectionSettings")[0]
                    if not isinstance(data, bytes) or len(data) < 12:
                        raise NetworkConfigurationError(
                            "Malformed system proxy connection settings"
                        )
                    flags = int.from_bytes(data[8:12], "little")
                    result["AutoDetect"] = bool(flags & 8)
                    result["ProxyEnable"] = bool(flags & 2)
                    result["AutoConfigURL"] = bool(flags & 4)
            except FileNotFoundError:
                pass
            return result
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise NetworkConfigurationError("Cannot read system proxy settings") from exc


def system_http_proxy_url() -> str | None:
    """Return the user's current HTTP CONNECT proxy, if one is configured.

    ``urllib.request.getproxies`` reads environment proxy settings on every platform
    and the current user's Internet Settings on Windows.  Reading on demand matters:
    toggling a proxy-mode VPN must affect the next REST request or WS reconnect without
    restarting TALYX.  Only HTTP(S) proxy URLs are accepted because aiohttp and the API clients
    can use those without granting ambient credential or PAC execution authority.
    """

    settings = _windows_proxy_settings()
    if settings.get("AutoConfigURL") or settings.get("AutoDetect"):
        raise NetworkConfigurationError(
            "System PAC/automatic proxy discovery is not supported by TALYX's network transports"
        )
    proxies = request.getproxies()
    for key in ("https", "http", "all"):
        raw = str(proxies.get(key) or "").strip()
        if not raw:
            continue
        candidate = raw if "://" in raw else f"http://{raw}"
        try:
            parsed = parse.urlsplit(candidate)
            valid = (
                parsed.scheme.casefold() in {"http", "https"}
                and parsed.hostname
                and parsed.username is None
                and parsed.password is None
                and parsed.path in {"", "/"}
                and not parsed.query
                and not parsed.fragment
                and (parsed.port is None or 0 < parsed.port <= 65535)
                and not any(character.isspace() for character in candidate)
            )
        except ValueError:
            valid = False
        if not valid:
            # Do not echo the URL: it may contain credentials.
            raise NetworkConfigurationError(
                "Configured proxy requires a valid HTTP(S) URL without authentication; "
                "direct fallback is disabled"
            )
        return candidate
    if settings.get("ProxyEnable") or any(value for key, value in proxies.items() if key != "no"):
        raise NetworkConfigurationError(
            "System proxy is configured but has no supported route"
        )
    return None


_T = TypeVar("_T")


async def _route_io(read: Callable[[], _T]) -> _T:
    pending = asyncio.create_task(asyncio.to_thread(read))
    cancelled = False
    while not pending.done():
        try:
            await asyncio.shield(pending)
        except asyncio.CancelledError:
            cancelled = True
    result = pending.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


async def await_network_route(
    operation: Callable[[], Awaitable[_T]],
    proxy: str | None,
    *,
    poll_seconds: float = 0.5,
    route_epoch: int | None = None,
) -> _T:
    """Cancel blocked network work when its route changes, including quiet WS feeds."""

    captured_epoch = await _route_io(system_route_epoch) if route_epoch is None else route_epoch
    def check() -> None:
        if system_http_proxy_url() != proxy:
            raise NetworkRouteChanged("System proxy changed; reconnecting")
        if captured_epoch is not None and system_route_epoch() != captured_epoch:
            raise NetworkRouteChanged("System IP route changed; reconnecting transport")

    await _route_io(check)
    pending = asyncio.ensure_future(operation())
    try:
        while True:
            done, _ = await asyncio.wait({pending}, timeout=poll_seconds)
            await _route_io(check)
            if done:
                return pending.result()
    finally:
        if not pending.done():
            pending.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await pending


async def network_route_events(
    factory: Callable[[], AsyncIterator[_T]],
    proxy: str | None,
) -> AsyncIterator[_T]:
    events = factory()
    try:
        epoch = await _route_io(system_route_epoch)
        while True:
            try:
                event = await await_network_route(lambda: anext(events), proxy, route_epoch=epoch)
            except StopAsyncIteration:
                return
            yield event
    finally:
        close = getattr(events, "aclose", None)
        if close is not None:
            await close()


def system_tcp_connector(*, ssl: Any = True) -> aiohttp.TCPConnector:
    """Build an aiohttp connector that resolves through the host OS each connect."""

    return aiohttp.TCPConnector(
        resolver=aiohttp.ThreadedResolver(),
        ssl=ssl,
        use_dns_cache=False,
        enable_cleanup_closed=True,
    )


def system_client_session(*, timeout: aiohttp.ClientTimeout | None = None) -> aiohttp.ClientSession:
    """Return a public session with system DNS and no ambient credential lookup.

    Direct aiohttp callers pass :func:`system_http_proxy_url` explicitly.  Keeping
    ``trust_env`` false prevents unrelated netrc credentials from entering anonymous
    public requests.
    """

    return aiohttp.ClientSession(
        connector=system_tcp_connector(),
        timeout=timeout,
        trust_env=False,
    )


def is_aiohttp_transport_error(exc: BaseException) -> bool:
    """True only for failures where reconnecting the public route can help.

    An HTTP/WebSocket 4xx response is an explicit provider decision, not a broken
    transport.  Retrying or rotating it would blur policy rejection into route
    recovery.  Server-side 5xx responses and connection-level aiohttp failures remain
    transient transport errors.
    """

    if isinstance(exc, aiohttp.ClientResponseError):
        return exc.status >= 500
    return isinstance(exc, (aiohttp.ClientError, TimeoutError, OSError))
