"""Which source serves what: registry, per-source gates and the ranking rule.

Market never hard-codes a source's limits: each source's ``describe`` declares its
concurrency and requests per minute, and every request waits at that source's gate
(priority order: interactive reads, live gap fills, quotes, fetch jobs). A rate
limit closes the gate until the source's retryAt; waiting is not failing.

Ranking (per market or venue, data kind, daily or intraday): usable (enabled, key
configured, not rejected) > covers it and not recent-only for history > delisted /
point-in-time coverage > deeper history > realtime over delayed (live kinds) >
bulk and wider limits > paid > source id. One series holds one source's rows.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import heapq
import itertools
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from vocabulary.identity import timeframe_nanoseconds

from .common import DAY_NS, MarketError, now_ns, ns_of_date
from .source_settings import SourceSettings

INTERACTIVE, LIVE, QUOTES, JOBS = range(4)
PRIORITY: contextvars.ContextVar[int] = contextvars.ContextVar("market_priority", default=INTERACTIVE)
LIVE_KINDS = {"quotes", "trades", "order_book"}
# Fetch jobs' file and parsing work has its own threads: a download of a thousand series keeps them all busy.
_JOB_WORKERS = ThreadPoolExecutor(max_workers=8, thread_name_prefix="market-jobs")


async def in_worker(func, /, *args, **kwargs):
    """``asyncio.to_thread`` on the workers of the caller's priority: a fetch job's work queues on its own threads,
    so a chart's read or a live session never waits behind a download."""
    pool = _JOB_WORKERS if PRIORITY.get() == JOBS else None
    call = functools.partial(contextvars.copy_context().run, func, *args, **kwargs)
    return await asyncio.get_running_loop().run_in_executor(pool, call)


class Gate:
    """Concurrency, requests per minute and cooldown for one source, by priority."""

    def __init__(self, concurrency: int, rpm: int | None):
        self.concurrency, self.rpm = max(1, concurrency), rpm
        self.active = 0
        self.sent: list[float] = []
        self.cool_until = 0  # ns
        self.cool_reason: str | None = None
        self._waiting: list[tuple[int, int, asyncio.Future]] = []
        self._seq = itertools.count()
        self._wake: asyncio.TimerHandle | None = None

    def configure(self, concurrency: int, rpm: int | None) -> None:
        self.concurrency, self.rpm = max(1, concurrency), rpm
        self._pump()

    async def acquire(self, priority: int) -> None:
        future = asyncio.get_running_loop().create_future()
        heapq.heappush(self._waiting, (priority, next(self._seq), future))
        self._pump()
        try:
            await future
        except asyncio.CancelledError:
            if future.done() and not future.cancelled():
                self.release()
            raise

    def release(self) -> None:
        self.active -= 1
        self._pump()

    def cool(self, until: int, reason: str) -> None:
        if until > self.cool_until:
            self.cool_until, self.cool_reason = until, reason
        self._pump()

    def _pump(self) -> None:
        now = time.monotonic()
        if self.rpm:
            self.sent = [t for t in self.sent if t > now - 60]
        while self._waiting and self.active < self.concurrency:
            if now_ns() < self.cool_until:
                self._schedule((self.cool_until - now_ns()) / 1e9)
                return
            if self.rpm and len(self.sent) >= self.rpm:
                self._schedule(self.sent[0] + 60 - now)
                return
            _priority, _seq, future = heapq.heappop(self._waiting)
            if future.cancelled():
                continue
            self.active += 1
            if self.rpm:
                self.sent.append(now)
            future.set_result(None)

    def _schedule(self, delay: float) -> None:
        if self._wake is not None:
            self._wake.cancel()
        self._wake = asyncio.get_running_loop().call_later(max(delay, 0.01), self._pump)

    @property
    def waiting(self) -> int:
        return sum(1 for item in self._waiting if not item[2].done())


def depth_start(depth: str, now: int) -> int:
    """A declared depth (ISO duration back from now, or a start date) as an instant."""
    match = re.fullmatch(r"P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?", depth)
    if match and depth != "P":
        years, months, days, hours, minutes = (int(x) if x else 0 for x in match.groups())
        return now - ((years * 365 + months * 30 + days) * DAY_NS + (hours * 60 + minutes) * 60 * 10**9)
    return ns_of_date(date.fromisoformat(depth))


def zone_offset(text: str) -> int:
    """A declared day zone ("+08:00") as nanoseconds east of UTC."""
    sign = -1 if text[0] == "-" else 1
    hours, minutes = text[1:].split(":")
    return sign * (int(hours) * 3600 + int(minutes) * 60) * 10**9


def local_date(text: str, zone: str) -> int:
    """Midnight of a declared date (archives' from and until) in the archive's day zone."""
    return ns_of_date(date.fromisoformat(text)) - zone_offset(zone)


def period_start(period: dict, price_basis: str, now: int) -> int:
    """The earliest instant a declared period reaches for a price basis: its API's depth or its oldest files."""
    starts = [local_date(item["from"], item["dayZone"]) for item in period.get("archives", [])
              if price_basis in item["priceBases"]]
    return min([depth_start(period["depth"], now), *starts])


def duration_ns(text: str | None) -> int:
    if not text:
        return 0
    now = now_ns()  # one clock reading: two drifted the duration by the time between them
    return now - depth_start(text, now)


@dataclass
class SourceEntry:
    source_id: str
    source: Any
    descriptor: dict
    coverage: dict | None = None
    problem: dict | None = None
    gate: Gate = field(default_factory=lambda: Gate(1, None))
    rejected: str | None = None  # "unauthorized" or "unsubscribed"
    test: dict | None = None


class Registry:
    def __init__(self, root: Path):
        self.preferences = SourceSettings(root)
        self.saved = self.preferences.saved
        self.entries: dict[str, SourceEntry] = {}
        self.listeners: list = []

    def add(self, source, *, source_ids=None) -> None:
        for source_id in source_ids or source.source_ids:
            self.entries[source_id] = SourceEntry(source_id, source, source.descriptor)
            saved = self.saved.get(source_id, {})
            source.settings.update(saved.get("settings", {}))

    def remove_module_sources(self, prefix: str) -> None:
        for source_id, entry in list(self.entries.items()):
            if getattr(entry.source, "id", "").startswith(prefix):
                del self.entries[source_id]

    def entry(self, source_id: str) -> SourceEntry:
        entry = self.entries.get(source_id)
        if entry is None:
            raise MarketError("not_found", f"No data source {source_id}")
        return entry

    def enabled(self, source_id: str) -> bool:
        return self.saved.get(source_id, {}).get("enabled", True)

    def set_enabled(self, source_id: str, value: bool, call=None) -> None:
        self.entry(source_id)
        self.preferences.write(source_id, "enabled", {"enabled"}, {"enabled": value}, call)

    def set_settings(self, source_id: str, values: dict, call=None) -> None:
        self.preferences.write(source_id, "settings", values.keys(), values, call)

    def usable(self, entry: SourceEntry) -> bool:
        if not self.enabled(entry.source_id) or entry.rejected or entry.coverage is None:
            return False
        if entry.descriptor["credentials"] == "required" and not entry.source.key_configured:
            return False
        return True

    async def refresh(self, source_id: str) -> None:
        entry = self.entry(source_id)
        try:
            entry.coverage = await entry.source.describe(source_id)
            entry.problem = None
        except MarketError as exc:
            entry.coverage, entry.problem = None, exc.problem()
            if exc.code == "unauthorized":
                entry.rejected = "unauthorized"
            return
        entry.gate.configure(entry.coverage["concurrency"], entry.coverage["requestsPerMinute"])

    # ── ranking ─────────────────────────────────────────────────────────────

    def rank(self, instrument: dict, kind: str, timeframe: str | None = None, *, history: bool = True,
             include_unusable: bool = False) -> list[tuple[str, dict]]:
        """Sources able to serve this instrument and kind, best first, with the matching coverage row."""
        daily = timeframe is None or not _intraday(timeframe)
        candidates = []
        now = now_ns()
        for source_id, entry in self.entries.items():
            usable = self.usable(entry)
            if not usable and not include_unusable:
                continue
            coverage = entry.coverage if entry.coverage is not None else (entry.descriptor.get("withKey") or None)
            if coverage is None:
                continue
            for item in coverage["rows"]:
                if item["kind"] != kind or not _in_scope(item["scope"], instrument):
                    continue
                periods = [p for p in item.get("periods", []) if (timeframe is None or not _intraday(p["timeframe"])) == daily]
                if kind == "bars" and not periods:
                    continue
                if history and item.get("historyRole") == "recent_only" and kind not in LIVE_KINDS:
                    continue
                first = min((period_start(p, "last", now) for p in periods), default=now)
                live = {"realtime": 2, "delayed": 1, "none": 0}[item["live"]]
                key = (
                    0 if usable else 1,
                    0 if item.get("delisted") or item.get("pointInTime") else 1,
                    first,
                    -live if kind in LIVE_KINDS or not history else 0,
                    0 if "bulk" in entry.descriptor["supports"] else 1,
                    -(coverage.get("requestsPerMinute") or 10**6),
                    0 if entry.descriptor.get("paid") else 1,
                    source_id,
                )
                candidates.append((key, source_id, item))
                break
        candidates.sort(key=lambda value: value[0])
        return [(source_id, item) for _key, source_id, item in candidates]

    def periods(self, source_id: str, instrument: dict, kind: str = "bars") -> list[dict]:
        """Every period the source declares for this kind and instrument (a source may declare daily and intraday
        periods in separate rows, e.g. with different publish delays)."""
        entry = self.entry(source_id)
        return [period for item in (entry.coverage or {}).get("rows", [])
                if item["kind"] == kind and _in_scope(item["scope"], instrument) for period in item.get("periods", [])]

    def row(self, source_id: str, instrument: dict, kind: str, timeframe: str | None = None) -> dict | None:
        """The coverage row declaring this kind for the instrument; with ``timeframe``, the row declaring that period."""
        entry = self.entry(source_id)
        for item in (entry.coverage or {}).get("rows", []):
            if item["kind"] == kind and _in_scope(item["scope"], instrument) and (
                    timeframe is None or any(p["timeframe"] == timeframe for p in item.get("periods", []))):
                return item
        return None

    async def call(self, source_id: str, method: str, *args, priority: int | None = None):
        """One source request through the source's gate; rate limits close the gate."""
        entry = self.entry(source_id)
        priority = PRIORITY.get() if priority is None else priority
        from talyx_sdk import rest_queue
        token = rest_queue.PRIORITY.set({INTERACTIVE: rest_queue.INTERACTIVE, LIVE: rest_queue.LIVE,
                                         QUOTES: rest_queue.LIVE, JOBS: rest_queue.BULK}[priority])
        await entry.gate.acquire(priority)
        try:
            return await getattr(entry.source, method)(source_id, *args)
        except MarketError as exc:
            if exc.code == "rate_limited":
                entry.gate.cool(exc.retry_at or now_ns() + 60 * 10**9, exc.message)
            if exc.code == "unauthorized":
                entry.rejected = "unauthorized"
            if exc.code == "forbidden" and entry.descriptor["credentials"] == "required":
                # The key works but its plan does not cover this: rank the next source until settings change.
                entry.rejected = "unsubscribed"
            raise
        finally:
            entry.gate.release()
            rest_queue.PRIORITY.reset(token)


def _intraday(timeframe: str) -> bool:
    step = timeframe_nanoseconds(timeframe)
    return step is not None and step < DAY_NS


def _in_scope(scope: dict, instrument: dict) -> bool:
    if instrument["identityVersion"] == 2:
        if "markets" not in scope or instrument["venueId"] not in scope["markets"]:
            return False
    elif "venues" not in scope or instrument["venueId"] not in scope["venues"]:
        return False
    if scope.get("assetClasses") and instrument["assetClass"] not in scope["assetClasses"]:
        return False
    if scope.get("marketTypes") and instrument["marketType"] not in scope["marketTypes"]:
        return False
    return True
