"""What the request queue needs to know about a remote API: how requests count against its limits.

The SDK only queues by limit domain and slows down on what the server reports. Which domain a request falls in,
what it costs, how the server reports its usage and how it says "too many" belong to the module that talks to that
API: it installs a ``Profiles`` subclass with ``use``. Without one, every host is one domain paced by the client's
own rate and only HTTP 418/429 count as "too many".
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class RequestProfile:
    api: str
    """Which API (a label in messages)."""
    scope: str
    """The limit domain: requests with the same scope share ``capacity`` per ``window`` seconds."""
    url: str
    capacity: float
    window: float
    """Seconds; 0 means ``capacity`` counts requests in flight (connections), not per time."""
    cost: float
    feedback: bool = False
    """The server reports the domain's usage in ``used_header``; local counts are then only a bound."""
    scale: float = 1
    used_header: str = ""


class Profiles:
    """The generic profile; a module subclasses it for the APIs it knows.

    The error types are the client library's own, so code that handles its errors handles the queue's and the fuse's
    refusals the same way: ``rate_limited`` (a domain's cooldown is open), ``refused`` (a request that can never fit
    the budget), ``busy`` (too many requests waiting), ``timeout`` and ``network``."""

    rate_limited: type[Exception] = RuntimeError
    refused: type[Exception] = ValueError
    busy: type[Exception] = RuntimeError
    timeout: type[Exception] = TimeoutError
    network: type[Exception] = OSError

    def request(self, client, path, api, method, params, config) -> RequestProfile:
        url = client.urls["api"]
        url = next(iter(url.values())) if isinstance(url, dict) else url
        url = next(iter(url.values())) if isinstance(url, dict) else url
        window = (client.tokenBucket.get("windowSize") or 1000) / 1000
        proxy = client.httpsProxy or client.httpProxy or ""
        return RequestProfile(client.id, "|".join((urlsplit(url).hostname or "", proxy, str(api))), url,
                              window * 1000 / client.rateLimit, window,
                              client.calculate_rate_limiter_cost(api, method, path, params, config))

    def companions(self, client, profile, path, api, method, params) -> tuple[RequestProfile, ...]:
        """Other domains the same request also counts against (a per-key limit besides the per-host one)."""
        return ()

    def capture(self, profile: RequestProfile, body: str) -> None:
        """Learn limits the server publishes in a response body."""

    def limited(self, profile: RequestProfile | None, status: int, body: str) -> tuple[bool, str | None]:
        """Whether a response says "too many requests", and the server's own words."""
        return status in (418, 429), None

    def resume_hints(self, profile: RequestProfile | None, headers: dict, body: str) -> list[float]:
        """Epoch seconds the server says requests may resume (besides Retry-After)."""
        return []

    def endpoint_limits(self, profile: RequestProfile, headers: dict) -> tuple[float, float] | None:
        """``(limit, remaining)`` when the server reports an endpoint's capacity in headers."""
        return None

    def websocket(self, api: str, url: str, proxy: str | None,
                  connection: int | None = None) -> tuple[RequestProfile, ...]:
        """Dial (``connection`` None) or per-connection message domains of a WebSocket."""
        return ()


_profiles: Profiles = Profiles()


def use(profiles: Profiles) -> None:
    """Install this process's profiles (a module does it once at start)."""
    global _profiles
    _profiles = profiles


def profiles() -> Profiles:
    return _profiles
