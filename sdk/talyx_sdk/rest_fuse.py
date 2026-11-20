"""Per-host REST fuse for HTTP API clients.

APIs limit REST weight per IP and host, and some turn requests after a "too many" into an IP ban. The first
"too many" any fused client receives for a limit domain therefore stops every fused client, in every process, from
sending to it until the time the server gives. Requests in that window fail at once without reaching the network;
the fuse itself never retries anything.

Cooldowns live in the platform's shared folder (``TALYX_SHARED_DIR``): every process sees them and they survive
restarts. Each cooldown period is announced once, by whichever process opens it first (``on_cooldown``).
"""

import asyncio
import inspect
import os
import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import closing, contextmanager
from contextvars import ContextVar
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

from .rest_profiles import profiles

# Pause after a "too many" that names no time to resume: one full window.
COOLDOWN_SECONDS = 60

_lock = threading.Lock()
_fused_types: dict[type, type] = {}
_request = ContextVar('rest_request', default=None)
NETWORK_TIMEOUT = ContextVar('rest_network_timeout', default=None)
on_cooldown: Callable[["RateLimited"], None] | None = None
"""Called (on the thread that saw it) when this process opened a new cooldown period: announce it once."""


class RateLimited(Exception):
    """A request refused locally because its domain's fuse is open. Each one is also the client library's own
    rate-limit error (``Profiles.rate_limited``)."""

    api: str
    scope: str
    retry_at: float
    reason: str
    details: dict


_limited_types: dict[type, type] = {}


def rate_limited(api, scope, retry_at, reason) -> RateLimited:
    base = profiles().rate_limited
    with _lock:
        kind = _limited_types.setdefault(base, type('RateLimited', (RateLimited, base), {}))
    error = kind(f'{api} is rate limited. Requests resume at {_clock(retry_at)}.')
    error.api, error.scope, error.retry_at, error.reason = api, scope, retry_at, reason
    error.details = {'api': api, 'scope': scope, 'retryAt': int(retry_at * 1000), 'reason': reason}
    return error


class Cooldowns:
    """Open fuses by limit domain, in ``folder`` (shared by processes) or, without one, in this process only."""

    def __init__(self, folder=None):
        self.memory: dict[str, list] = {}
        self.path = None if folder is None else Path(folder) / 'request-cooldowns.sqlite'
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as database:
                database.execute('CREATE TABLE IF NOT EXISTS cooldowns (scope TEXT PRIMARY KEY, api TEXT NOT NULL,'
                                 ' retry_at REAL NOT NULL, reason TEXT NOT NULL, announced INTEGER NOT NULL)')

    @contextmanager
    def _connect(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as database, database:
            yield database

    def state(self, scope):
        """``(api, retry_at, reason)`` of the domain's latest cooldown, or None."""
        if self.path is None:
            with _lock:
                row = self.memory.get(scope)
                return None if row is None else tuple(row[:3])
        with self._connect() as database:
            return database.execute('SELECT api,retry_at,reason FROM cooldowns WHERE scope=?', (scope,)).fetchone()

    def until(self, scope):
        state = self.state(scope)
        return None if state is None or state[1] <= time.time() else state[1]

    def open(self, scope, *, until, reason, api=''):
        """Record a cooldown (extending an open one). True when this starts a period nobody announced yet."""
        now = time.time()
        if self.path is None:
            with _lock:
                previous = self.memory.get(scope)
                active = previous is not None and previous[1] > now
                self.memory[scope] = [api, max(until, previous[1]) if active else until, reason, True]
                return not active
        with self._connect() as database:
            database.execute('BEGIN IMMEDIATE')
            previous = database.execute('SELECT retry_at,announced FROM cooldowns WHERE scope=?', (scope,)).fetchone()
            active = previous is not None and previous[0] > now
            database.execute('INSERT OR REPLACE INTO cooldowns VALUES (?,?,?,?,1)',
                             (scope, api, max(until, previous[0]) if active else until, reason))
            return not (active and previous[1])

    def open_scopes(self, hosts):
        """Retry times of open cooldowns of these hosts and their domains."""
        now = time.time()
        if self.path is None:
            with _lock:
                return [row[1] for scope, row in self.memory.items()
                        if scope.split('|')[0] in hosts and row[1] > now]
        with self._connect() as database:
            return [row[0] for host in hosts for row in database.execute(
                'SELECT retry_at FROM cooldowns WHERE (scope=? OR scope LIKE ?) AND retry_at>?',
                (host, host + '|%', now))]


_cooldowns = Cooldowns(os.environ.get('TALYX_SHARED_DIR'))


def configure(directory):
    """Keep cooldowns in ``directory`` (the platform's shared folder is used by default)."""
    global _cooldowns
    _cooldowns = Cooldowns(directory)


def _clock(moment):
    pattern = "%H:%M:%S" if moment - time.time() < 20 * 3600 else "%Y-%m-%d %H:%M:%S"
    return time.strftime(pattern, time.localtime(moment))


def check(url, *, scope=None):
    """Refuse a request to a domain whose fuse is open."""
    host = urlsplit(url).hostname
    for key in dict.fromkeys((host, scope)):
        if key is None:
            continue
        state = _cooldowns.state(key)
        if state is not None and time.time() < state[1]:
            raise rate_limited(state[0], key, state[1], state[2])


def check_request(url):
    from .rest_queue import check_cancelled
    check_cancelled()
    ticket = _request.get()
    check(url, scope=None if ticket is None else ticket.profile.scope)


def _retry_after(headers, now):
    for name, value in headers.items():
        if name.lower() == "retry-after":
            value = value.strip()
            if value.isdigit():
                return int(value)
            try:
                return parsedate_to_datetime(value).timestamp() - now
            except (TypeError, ValueError):
                return None
    return None


def observe(url, status, headers, body, *, ticket=None):
    """Open the domain's fuse when a response says its limit was exceeded; returns the error to raise."""
    headers = {key.lower(): str(value) for key, value in headers.items()}
    profile = None if ticket is None else ticket.profile
    api = urlsplit(url).hostname if profile is None else profile.api
    limited, words = profiles().limited(profile, status, body)
    if not limited:
        return None
    reason = words or f'The server refused requests (HTTP {status})'
    now = time.time()
    delays = [hint - now for hint in profiles().resume_hints(profile, headers, body)]
    retry_after = _retry_after(headers, now)
    if retry_after is not None:
        delays.append(retry_after)
    fallback = COOLDOWN_SECONDS if profile is None else profile.window
    until = now + (max(delays) if delays else fallback)
    if until <= now:
        until = now + fallback
    scope = urlsplit(url).hostname if profile is None else profile.scope
    error = rate_limited(api, scope, until, reason)
    if _cooldowns.open(scope, until=until, reason=reason, api=api) and on_cooldown is not None:
        on_cooldown(error)
    return error


def paused_until(urls):
    """When the latest open fuse among these URLs' hosts closes (epoch seconds), or None."""
    open_ = _cooldowns.open_scopes({urlsplit(url).hostname for url in urls})
    return max(open_) if open_ else None


class RestFuse:
    """Mixin placing the fuse in front of a synchronous client's transport (``fetch2``, ``fetch``,
    ``on_rest_response``)."""

    def fetch2(self, path, api=None, method='GET', params=None, config=None):
        from . import rest_queue
        api = ['public'] if api is None else api
        params, config = {} if params is None else params, {} if config is None else config
        profile = profiles().request(self, path, api, method, params, config)
        ticket = rest_queue.acquire_sync((profile, *profiles().companions(self, profile, path, api, method, params)))
        token = _request.set(ticket)
        try:
            return super().fetch2(path, api, method, {**params, 'maxRetriesOnFailure': 0}, config)
        finally:
            for item in (ticket, *ticket.companions):
                rest_queue.owner().finished(item)
            _request.reset(token)

    def throttle(self, cost=None):
        return None

    def rest_sending(self):
        pass

    def fetch(self, url, method="GET", headers=None, body=None):
        check_request(url)
        self.rest_sending()
        return super().fetch(url, method, headers, body)

    def on_rest_response(self, code, reason, url, method, response_headers, response_body,
                         request_headers, request_body):
        from . import rest_queue
        ticket = _request.get()
        if ticket is not None:
            for item in (ticket, *ticket.companions):
                rest_queue.owner().observe(item, {key.lower(): str(value) for key, value in response_headers.items()})
            profiles().capture(ticket.profile, response_body)
        error = observe(url, code, response_headers, response_body, ticket=ticket)
        if error is not None:
            raise error
        return super().on_rest_response(code, reason, url, method, response_headers, response_body,
                                         request_headers, request_body)


class AsyncRestFuse(RestFuse):
    """Mixin placing the fuse in front of an asyncio client's transport."""

    async def fetch2(self, path, api=None, method='GET', params=None, config=None):
        from . import rest_queue
        api = ['public'] if api is None else api
        params, config = {} if params is None else params, {} if config is None else config
        profile = profiles().request(self, path, api, method, params, config)
        ticket = await rest_queue.acquire((profile, *profiles().companions(self, profile, path, api, method, params)))
        token = _request.set(ticket)
        try:
            return await super(RestFuse, self).fetch2(path, api, method, {**params, 'maxRetriesOnFailure': 0}, config)
        finally:
            for item in (ticket, *ticket.companions):
                rest_queue.owner().finished(item)
            _request.reset(token)

    async def throttle(self, cost=None):
        await self.rest_before_sign()

    async def rest_before_sign(self):
        pass

    def client(self, url):
        from .ws_limits import guard_client
        created = not self.clients or url not in self.clients
        client = super().client(url)
        if created:
            guard_client(client, self.id, self.httpsProxy)
        return client

    async def fetch(self, url, method="GET", headers=None, body=None):
        check_request(url)
        self.rest_sending()
        try:
            async with asyncio.timeout(NETWORK_TIMEOUT.get()):
                return await super(RestFuse, self).fetch(url, method, headers, body)
        except TimeoutError as error:
            raise profiles().timeout(f'{self.id} HTTP request timed out') from error


def fused(client_type):
    """The client class with the fuse in front of its REST transport."""
    with _lock:
        if client_type not in _fused_types:
            mixin = AsyncRestFuse if inspect.iscoroutinefunction(client_type.fetch2) else RestFuse
            _fused_types[client_type] = type(client_type.__name__, (mixin, client_type), {})
        return _fused_types[client_type]


