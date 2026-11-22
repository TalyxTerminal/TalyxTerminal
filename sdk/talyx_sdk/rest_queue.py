"""One process-owned priority ledger for synchronous and asyncio HTTP API clients.

All ledger state belongs to one loop. Only immutable request descriptions and
concurrent futures cross threads; no asyncio synchronization object does.

Several processes (modules) may send to the same limit domain. Each registers itself for the domains it uses in
the platform's shared folder (``TALYX_SHARED_DIR``) and keeps ``1 / processes`` of what the server does not
report back to it, so together they stay within the budget however many there are.
"""
import asyncio
import concurrent.futures
import os
import sqlite3
import threading
import time
from contextlib import closing
from contextvars import ContextVar
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from itertools import count
from pathlib import Path

from .errors import ContractError
from .i18n import platform_t
from .rest_fuse import RateLimited, check
from .rest_profiles import profiles

WRITE, RECONCILE, INTERACTIVE, LIVE, BULK = range(5)
PRIORITY = ContextVar('rest_request_priority', default=INTERACTIVE)
STOP_REQUEST = ContextVar('rest_request_stop', default=None)
_owner = None
_lock = threading.Lock()


# A process that has not touched a domain for this long no longer counts in its share.
REGISTRATION_SECONDS = 120
SHARE_REFRESH_SECONDS = 10


def process_share(folder, domain, process) -> float:
    """Register ``process`` as using ``domain`` and return its share of what is counted locally (1 / processes)."""
    path = Path(folder) / "request-domains.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    with closing(sqlite3.connect(path, timeout=10)) as database, database:
        database.execute("CREATE TABLE IF NOT EXISTS users (domain TEXT NOT NULL, process TEXT NOT NULL, "
                         "seen REAL NOT NULL, PRIMARY KEY(domain, process))")
        database.execute("INSERT OR REPLACE INTO users VALUES (?,?,?)", (domain, str(process), now))
        count = database.execute("SELECT COUNT(*) FROM users WHERE domain=? AND seen>?",
                                 (domain, now - REGISTRATION_SECONDS)).fetchone()[0]
    return 1 / max(1, count)


def _shared_folder():
    return os.environ.get("TALYX_SHARED_DIR")


@dataclass
class Ticket:
    profile: object
    sequence: int
    sent: float
    received: bool = False
    reflected: bool = False
    companions: tuple = ()


@dataclass
class Domain:
    capacity: float
    window: float
    requests: list = field(default_factory=list)
    server_window: int = -1
    server_used: float = 0
    server_offset: float = 0
    share: float = 1.0
    share_checked: float = 0

    def refresh_share(self, scope, now):
        folder = _shared_folder()
        if folder is not None and now - self.share_checked > SHARE_REFRESH_SECONDS:
            self.share_checked = now
            self.share = process_share(folder, scope, os.getpid())

    def delay(self, profile, priority, now):
        self.refresh_share(profile.scope, now)
        if self.window == 0:
            self.requests = [ticket for ticket in self.requests if not ticket.received]
            used = sum(ticket.profile.cost for ticket in self.requests)
            return 0 if used + profile.cost <= self.capacity * self.share else .05
        self.requests = [ticket for ticket in self.requests if not ticket.received or ticket.sent > now - self.window]
        fraction = (.93, .9, .8, .7, .6)[priority]
        # Usage the server reports covers every process; what is only counted here is this process's share.
        ceiling = self.capacity * fraction * (1 if profile.feedback else self.share)
        if profile.cost > ceiling:
            raise profiles().refused('One request exceeds the shared budget; reduce its batch size')
        if profile.feedback:
            # Unreflected weight of all processes together stays within the 7% the highest ceiling leaves.
            burst = self.capacity * .06 * self.share
            if profile.cost > burst:
                raise profiles().refused('One request exceeds the unconfirmed weight allowance; reduce its batch size')
            unreflected = sum(t.profile.cost for t in self.requests if not t.reflected)
            inflight = sum(t.profile.cost for t in self.requests if not t.received)
            fresh = int((now + self.server_offset) // self.window) == self.server_window
            used = self.server_used + unreflected if fresh else sum(t.profile.cost for t in self.requests)
            # At startup/window boundaries a single light request establishes
            # feedback. Missing headers retain local cost and pace conservatively.
            if not fresh and any(not ticket.reflected for ticket in self.requests):
                return .05
            if inflight + profile.cost > burst or unreflected + profile.cost > burst:
                return .05
        else:
            used = sum(t.profile.cost for t in self.requests)
        if used + profile.cost <= ceiling:
            return 0
        return .05 if profile.feedback else max(.001, self.requests[0].sent + self.window - now)

    def observe(self, ticket, headers, now):
        ticket.received = True
        profile = ticket.profile
        if profile.feedback and profile.used_header in headers and 'date' in headers:
            server = parsedate_to_datetime(headers['date']).timestamp()
            window = int(server // self.window)
            used = float(headers[profile.used_header]) / profile.scale
            if window < self.server_window:
                return
            if window > self.server_window:
                self.server_window, self.server_used = window, used
                self.server_offset = server - now
            else:
                self.server_used = max(self.server_used, used)
            ticket.reflected = True
        elif (reported := profiles().endpoint_limits(profile, headers)) is not None:
            limit, remaining = reported
            self.capacity = min(self.capacity, limit)
            external = max(0, limit - remaining - sum(t.profile.cost for t in self.requests))
            if external:
                self.requests.append(Ticket(profile, -1, now, True, False))
                self.requests[-1].profile = type(profile)(profile.api, profile.scope, profile.url,
                    profile.capacity, profile.window, external)


class RequestQueue:
    def __init__(self):
        self.domains, self.waiting = {}, []
        self.sequence = count()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, name='rest-request-ledger', daemon=True)
        self.thread.start()
        self.ready.wait()

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.changed = asyncio.Event()
        self.task = self.loop.create_task(self._dispatch())
        self.ready.set()
        self.loop.run_forever()

    def submit(self, profile, priority):
        profiles = profile if isinstance(profile, tuple) else (profile,)
        return asyncio.run_coroutine_threadsafe(self._acquire(profiles, priority), self.loop)

    async def _acquire(self, profile, priority):
        if len(self.waiting) >= 512:
            raise profiles().busy('The shared request queue is full')
        future = self.loop.create_future()
        item = (priority, next(self.sequence), profile, future)
        self.waiting.append(item)
        self.changed.set()
        try:
            return await future
        finally:
            if item in self.waiting:
                self.waiting.remove(item)
            self.changed.set()

    async def _dispatch(self):
        while True:
            self.changed.clear()
            delay = None
            blocked = set()
            for priority, sequence, wanted, future in sorted(self.waiting, key=lambda item: item[:2]):
                if future.done():
                    continue
                keys = {(priority, profile.scope) for profile in wanted}
                if keys & blocked:
                    continue
                try:
                    now = time.time()
                    waits = []
                    for profile in wanted:
                        check(profile.url, scope=profile.scope)
                        domain = self.domains.setdefault(profile.scope, Domain(profile.capacity, profile.window))
                        waits.append(domain.delay(profile, priority, now))
                    wait = max(waits)
                except (RateLimited, profiles().refused) as error:
                    future.set_exception(error)
                    continue
                if wait:
                    blocked.update(keys)
                    delay = wait if delay is None else min(delay, wait)
                    continue
                # A request consumes all its counting domains at one instant.
                # Waiting for a companion domain never ages an IP reservation.
                tickets = tuple(Ticket(profile, sequence, now) for profile in wanted)
                for ticket in tickets:
                    self.domains[ticket.profile.scope].requests.append(ticket)
                tickets[0].companions = tickets[1:]
                future.set_result(tickets[0])
            try:
                if delay is None:
                    await self.changed.wait()
                else:
                    await asyncio.wait_for(self.changed.wait(), delay)
            except TimeoutError:
                pass

    def observe(self, ticket, headers):
        def update():
            self.domains[ticket.profile.scope].observe(ticket, headers, time.time())
            self.changed.set()
        self.loop.call_soon_threadsafe(update)

    def finished(self, ticket):
        # Lost responses/cancelled sends keep consuming unconfirmed weight.
        def update():
            ticket.received = True
            self.changed.set()
        self.loop.call_soon_threadsafe(update)

    def close(self):
        async def stop():
            for _, _, _, future in self.waiting:
                if not future.done():
                    future.cancel()
            pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        asyncio.run_coroutine_threadsafe(stop(), self.loop).result()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join()
        self.loop.close()


def owner():
    global _owner
    with _lock:
        if _owner is None:
            _owner = RequestQueue()
        return _owner


async def acquire(profile):
    future = owner().submit(profile, PRIORITY.get())
    delivered = False
    try:
        wrapped = asyncio.wrap_future(future)
        while not wrapped.done():
            check_cancelled()
            await asyncio.wait([wrapped], timeout=.1)
        check_cancelled()
        delivered = True
        return wrapped.result()
    except asyncio.CancelledError:
        future.cancel()
        raise
    finally:
        if not future.done():
            future.cancel()
        elif not delivered:
            _abandon(future)


def acquire_sync(profile):
    future = owner().submit(profile, PRIORITY.get())
    delivered = False
    try:
        while True:
            check_cancelled()
            try:
                ticket = future.result(.1)
                delivered = True
                return ticket
            except concurrent.futures.TimeoutError:
                pass
    finally:
        if not future.done():
            future.cancel()
        elif not delivered:
            _abandon(future)


def _abandon(future):
    # Cancellation after admission but before transport must not leave an
    # unanswered reservation alive forever. Its cost still ages normally.
    if not future.cancelled() and future.exception() is None:
        ticket = future.result()
        for item in (ticket, *ticket.companions):
            owner().finished(item)


def check_cancelled():
    """A synchronous request whose work was stopped (``STOP_REQUEST`` set) is not sent."""
    stop = STOP_REQUEST.get()
    if stop is not None and stop.is_set():
        raise ContractError('cancelled', platform_t("The request was cancelled before sending"))


def shutdown():
    global _owner
    with _lock:
        if _owner is not None:
            _owner.close()
            _owner = None
