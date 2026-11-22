"""Live series (the ``session`` stream): shared upstreams, per-consumer buffers, recovery.

One **upstream** per (source, instrument, kind, live base period, price basis,
session scope, environment) carries the source's live events; every period open on
that instrument shares it (crypto streams one-minute bars; Yahoo is polled at the
series' base period). Closed rows go to sessions at once and are written to storage
in the background.

One **session** per series id turns base rows into the series' rows with the same
adjustment and aggregation as reads (``Data.render``): closed target rows become
``append`` items, the forming one ``update``. One **consumer** per subscription
buffers what its reader has not taken yet: updates are coalesced (latest wins); if
unsent appends pass 500 rows or 1 MB they collapse into one ``replace`` built when
sent. Until the source first goes live a session is ``connecting`` (failed attempts keep
it there, with the problem). Recovery is Market's: a lost connection is ``recovering``; after the source
resubscribes, the gap is filled through ``history`` and the rows go out as append
(or replace when many), then ``live``. A closed row sent before the source's publish
cutoff is provisional: once past it, the upstream fetches it from history and sends a
replace when the final values differ.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from dataclasses import replace as dc_replace

import numpy as np
from talyx_sdk.i18n import t

from vocabulary.identity import timeframe_nanoseconds
from vocabulary.instruments import MarketSeries

from . import bars, calendars
from .common import MarketError, now_ns
from .data import WINDOW_FILL_LIMIT, Fill, Plan
from .routing import LIVE, PRIORITY, in_worker
from .store import SeriesKey

GRACE = 10.0
APPEND_ROWS = 500
APPEND_BYTES = 1_000_000
ROW_BYTES = 120
BACKOFF = (1.0, 2.0, 5.0, 10.0, 30.0, 60.0)
CATCH_UP_REQUESTS = 200
WRITE_EVERY = 2.0
log = logging.getLogger(__name__)
FATAL = {"not_found", "unsupported", "unauthorized", "forbidden"}


def _wire_row(rows: bars.Columns, index: int = -1) -> dict:
    wire = bars.to_wire(bars.take(rows, slice(index, None if index == -1 else index + 1)))
    return {name: values[0] for name, values in wire.items()}


def _row_columns(row: dict) -> bars.Columns:
    return {name: np.asarray([value], dtype=np.int64 if name in ("time", "close_time") else np.float64)
            for name, value in row.items()}


def live_periods(row: dict | None) -> list[str]:
    if not row:
        return []
    if "livePeriods" in row:
        return list(row["livePeriods"])
    return [period["timeframe"] for period in row.get("periods", [])]


# ── consumer ───────────────────────────────────────────────────────────────


class Consumer:
    def __init__(self, session: SeriesSession):
        self.session = session
        self.items: deque[dict] = deque()
        self.event = asyncio.Event()
        self.last_time: int | None = None
        self.pending_rows = 0

    def push(self, item: dict) -> None:
        kind = item["type"]
        if kind == "_replace":
            if "fill" not in item:  # one replace is built at send time; the window fill's outcome stays with it
                item = next((entry for entry in self.items if entry["type"] == "_replace" and "fill" in entry), item)
            self.items.clear()
            self.pending_rows = 0
        elif any(entry["type"] == "_replace" for entry in self.items) and kind == "append":
            return  # the replace built at send time will carry these rows
        elif kind == "update":
            if self.items and self.items[-1]["type"] == "update":
                self.items.pop()
        elif kind == "append":
            self.items = deque(entry for entry in self.items if entry["type"] != "update")
            self.pending_rows += len(item["columns"]["time"])
            if self.pending_rows > APPEND_ROWS or self.pending_rows * ROW_BYTES > APPEND_BYTES:
                self.items = deque(entry for entry in self.items if entry["type"] not in ("append",))
                self.items.appendleft({"type": "_replace"})
                self.pending_rows = 0
                self.event.set()
                return
        elif kind == "status":
            if self.items and self.items[-1]["type"] == "status":
                self.items.pop()
        self.items.append(item)
        self.event.set()

    async def next(self) -> dict:
        while True:
            while not self.items:
                self.event.clear()
                await self.event.wait()
            item = self.items.popleft()
            kind = item["type"]
            if kind == "_replace":
                replace = await self.replace(item)
                times = replace["page"]["columns"]["time"]
                self.last_time = int(times[-1]) if times else None
                return replace
            if kind == "append":
                self.pending_rows = max(0, self.pending_rows - len(item["columns"]["time"]))
                times = [int(t) for t in item["columns"]["time"]]
                keep = [i for i, t in enumerate(times) if self.last_time is None or t > self.last_time]
                if not keep:
                    continue
                if len(keep) != len(times):
                    item = {**item, "columns": {name: [values[i] for i in keep]
                                                for name, values in item["columns"].items()}}
                self.last_time = times[keep[-1]]
            return item

    async def replace(self, item: dict) -> dict:
        page = await self.session.build_replace()
        if "fill" in item and page["fill"]["problem"] is None:
            # The window fill's outcome, for every consumer; a span stored as unavailable says why itself.
            page["fill"] = item["fill"]
        return {"type": "replace", "page": page}


# ── the opening window ─────────────────────────────────────────────────────


class WindowFill:
    """A session's opening replace, shared by bar and stored-observation sessions.

    The first replace is what Market holds, at once, without asking the source (Chart's design: a chart is drawn
    within half a second, its missing tail follows). When anything of the window is missing the page says
    ``fill.state`` "filling" and the window is filled in the background: first the newest source page when the
    newest published bar is missing (every consumer then gets a replace with it, still "filling" when more is
    missing), then the rest (``Data.fill``, its own request bound), and every consumer gets one replace carrying the
    window and the fill's outcome. The fill asks every layer for what the newest page did not give (a refused API
    leaves the files). Replaces after that are what is stored; the tail is the live catch-up's.
    """

    def __init__(self, session):
        self.session = session
        self.task: asyncio.Task | None = None
        self.done = asyncio.Event()  # the window is filled (or needed no filling)
        self.opening = asyncio.Lock()  # consumers opening together: one fill

    async def page(self, extra=None) -> dict:
        session = self.session
        data = session.market.data
        start = getattr(session, "start", None)
        async with self.opening:
            if self.done.is_set():
                return await data.page(session.instrument, session.series, start=start, limit=session.warmup,
                                       fill="none", environment=session.environment, extra=extra)
            try:
                page = await data.page(session.instrument, session.series, start=start, limit=session.warmup,
                                       fill="filling", environment=session.environment, extra=extra)
            except MarketError:
                self.done.set()  # no plan for the series: replaces read what is stored
                raise
            if page["fill"]["state"] != "filling":
                self.done.set()
            elif self.task is None:
                self.task = asyncio.ensure_future(self._fill(start))
                session.market.track_task(self.task)
            return page

    async def _fill(self, start: int | None) -> None:
        session = self.session
        data = session.market.data
        start, end, _latest = data.window(session.instrument, session.series, start, None, None, session.warmup)
        plan = await in_worker(data.plan, session.instrument, session.series, start, environment=session.environment)
        # The newest page first, at the opening's priority: the chart waits for its newest bars. What it does not give
        # (the source failing or refusing it) the window fill asks for again and reports.
        with contextlib.suppress(MarketError):
            newest = await data.fill_newest(plan, start, end, session.environment)
            if newest.state in ("done", "partial") and (await in_worker(data.held, plan, start, end))[0].state == "filling":
                session.broadcast({"type": "_replace"})
        token = PRIORITY.set(LIVE)
        try:
            try:
                result = await data.fill(plan, start, end, limit=WINDOW_FILL_LIMIT)
                if session.series.adjustment in ("split", "split_dividend") and plan.kind == "bars":
                    await data.fill_actions(plan, start, end, WINDOW_FILL_LIMIT, session.environment)
            except MarketError as exc:
                result = Fill("failed", exc.problem())
        finally:
            PRIORITY.reset(token)
        self.done.set()
        session.broadcast({"type": "_replace", "fill": result.wire()})

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(BaseException):
                await self.task


# ── session (one series) ───────────────────────────────────────────────────


class SeriesSession:
    def __init__(self, live: Sessions, key, instrument: dict, series: MarketSeries, plan: Plan,
                 environment: str, warmup: int, start: int | None):
        self.live, self.key = live, key
        self.market = live.market
        self.instrument, self.series, self.plan = instrument, series, plan
        self.environment, self.warmup, self.start = environment, warmup, start
        self.target = series.timeframe
        self.consumers: set[Consumer] = set()
        self.upstream: Upstream | None = None
        self.live_plan: Plan | None = None
        self.period: tuple[int, int] | None = None
        self.period_rows: bars.Columns = bars.empty()
        self.forming: dict | None = None
        self.last_closed: int | None = None
        self.revision: int | None = None
        self.state, self.problem, self.since = "connecting", None, now_ns()
        self._poller: asyncio.Task | None = None
        self.window = WindowFill(self)

    # consumers

    def add(self) -> Consumer:
        consumer = Consumer(self)
        consumer.push({"type": "_replace"})
        consumer.push(self.status_item())
        self.consumers.add(consumer)
        return consumer

    def remove(self, consumer: Consumer) -> None:
        self.consumers.discard(consumer)
        if not self.consumers:
            self.live.session_idle(self)

    def broadcast(self, item: dict) -> None:
        for consumer in tuple(self.consumers):
            consumer.push(item)

    def status_item(self) -> dict:
        return {"type": "status", "state": self.state, "problem": self.problem, "since": str(self.since)}

    def set_status(self, state: str, problem: dict | None = None) -> None:
        if state in ("live", "delayed") and self._market_closed():
            state = "market_closed"
        if (state, problem) == (self.state, self.problem):
            return
        self.state, self.problem, self.since = state, problem, now_ns()
        self.broadcast(self.status_item())

    def _market_closed(self) -> bool:
        calendar = self.instrument["calendarId"]
        if calendars.canonical(calendar) == calendars.CONTINUOUS:
            return False
        now = now_ns()
        opens, closes = calendars.bar_grid(calendar, "1d", now - 3 * 86_400 * 10**9, now + 86_400 * 10**9)
        return not bool(np.any((opens <= now) & (closes > now)))

    # lifecycle

    async def begin(self) -> None:
        upstream = self.live.upstream_for(self)
        if upstream is None:
            self.set_status("history_only")
            self._poller = asyncio.ensure_future(self._poll_history())
            return
        self.upstream = upstream
        self.live_plan = upstream.plan
        await upstream.attach(self)

    async def end(self) -> None:
        if self._poller:
            self._poller.cancel()
            with contextlib.suppress(BaseException):
                await self._poller
        await self.window.stop()
        if self.upstream is not None:
            self.upstream.detach(self)

    async def _poll_history(self) -> None:
        """No live source: fill the tail at each bar close."""
        while True:
            await self.on_filled(fill_self=True)
            step = timeframe_nanoseconds(self.plan.base or self.target) or 86_400 * 10**9
            now = now_ns()
            wait = max(5.0, min(300.0, ((now // step + 1) * step - now) / 1e9 + 2))
            await asyncio.sleep(wait)

    # building items

    def same_base(self) -> bool:
        return self.live_plan is not None and self.live_plan.key == self.plan.key

    def _unwritten(self, plan: Plan) -> bars.Columns | None:
        if self.upstream is not None and plan.key == self.upstream.plan.key:
            return self.upstream.unwritten
        return None

    async def build_replace(self) -> dict:
        try:
            page = await self.window.page(extra=self._unwritten)
        except MarketError as exc:  # the stored rows go out with the source's reason on the status
            self.set_status("failed" if exc.code in FATAL else self.state, exc.problem())
            page = await self.market.data.page(self.instrument, self.series, start=self.start, limit=self.warmup,
                                               fill="none", environment=self.environment, extra=self._unwritten)
        times = page["columns"]["time"]
        if times:
            last = int(times[-1])
            self.last_closed = last if self.last_closed is None else max(self.last_closed, last)
        self.revision = page["revision"]
        page["forming"] = self.forming or page["forming"]
        return page

    def need_from(self) -> int | None:
        """Where this session needs live-base rows from (for the upstream's catch-up): its forming period's start,
        or on its own base the rows after its window; None until that window is filled (the opening replace fills
        it)."""
        now = now_ns()
        step = timeframe_nanoseconds(self.target) or 86_400 * 10**9
        if self.same_base():
            if not self.window.done.is_set():
                return None
            if self.last_closed is not None:
                return self.last_closed + step
            return self.window_start(now)
        start, _end = self.period_of(now)
        return start

    def window_start(self, now: int) -> int:
        if self.start is not None:
            return self.start
        return self.market.data.window_start(self.instrument, self.target, now, self.warmup)

    def period_of(self, time: int) -> tuple[int, int]:
        calendar = self.instrument["calendarId"]
        target = self.target
        step = timeframe_nanoseconds(target)
        if bars.is_intraday(target):
            if calendars.canonical(calendar) == calendars.CONTINUOUS:
                start = time - time % step
                return start, start + step
            opens, closes = calendars.bar_grid(calendar, target, time - 3 * 86_400 * 10**9, time + 1,
                                               self.series.sessionScope)
            index = np.searchsorted(opens, time, "right") - 1
            if index < 0:
                return time, time + step
            return int(opens[index]), int(closes[index])
        start = int(calendars.period_starts(calendar, target, np.asarray([time], dtype=np.int64))[0])
        return start, calendars.period_end(calendar, target, start)

    def _load_period(self, time: int) -> None:
        self.period = self.period_of(time)
        start, end = self.period
        raw = self.market.store.read(self.live_plan.key, start, end)
        unwritten = self.upstream.unwritten if self.upstream else None
        if unwritten is not None and bars.length(unwritten):
            raw = bars.merge(raw, bars.window(unwritten, start, end))
        self.period_rows = raw if raw and bars.length(raw) else bars.empty()

    def _render(self, raw: bars.Columns) -> bars.Columns:
        plan = dc_replace(self.plan, base=self.live_plan.base if self.live_plan else self.plan.base)
        return self.market.data.render(plan, raw)

    def _append(self, rows: bars.Columns) -> None:
        if not bars.length(rows):
            return
        if self.last_closed is not None:
            rows = bars.take(rows, rows["time"] > self.last_closed)
        if not bars.length(rows):
            return
        self.last_closed = int(rows["time"][-1])
        self.broadcast({"type": "append", "columns": bars.to_wire(rows),
                        "revision": self.market.data.revision(self.plan)})

    def _update(self, rows: bars.Columns) -> None:
        if not bars.length(rows):
            return
        row = _wire_row(rows)
        self.forming = row
        self.broadcast({"type": "update", "row": row, "receivedAt": str(now_ns())})

    # events from the upstream

    def on_closed(self, row: dict) -> None:
        if self.same_base():
            self._append(self._render(_row_columns(row)))
            return
        if self.period is None or row["time"] >= self.period[1] or row["time"] < self.period[0]:
            self._load_period(row["time"])
        self.period_rows = bars.merge(self.period_rows, _row_columns(row))
        if row["close_time"] >= self.period[1]:
            self._append(self._render(self.period_rows))
            self.period, self.period_rows, self.forming = None, bars.empty(), None
        else:
            self._update(self._render(self.period_rows))

    def on_forming(self, row: dict) -> None:
        if self.same_base():
            self._update(self._render(_row_columns(row)))
            return
        if self.period is None or row["time"] >= self.period[1] or row["time"] < self.period[0]:
            self._load_period(row["time"])
        rows = bars.merge(bars.window(self.period_rows, None, row["time"]), _row_columns(row))
        self._update(self._render(rows))

    async def on_filled(self, *, fill_self: bool = False) -> None:
        """New stored rows (catch-up after subscribe or reconnect): send what closed since."""
        if not self.window.done.is_set():  # the opening replace carries them
            self._form_from_store()
            return
        data = self.market.data
        now = now_ns()
        start = self.last_closed + 1 if self.last_closed is not None else self.window_start(now)
        end = data.closed_until(self.instrument, self.target, data.published_through(self.plan, now))
        if (fill_self or not self.same_base()) and self.plan.source_id is not None and start < end:
            try:
                await data.fill(self.plan, start, end, limit=CATCH_UP_REQUESTS)
            except MarketError as exc:
                self.problem = exc.problem()
        revision = data.revision(self.plan)
        if self.revision is not None and revision != self.revision and self._adjusted():
            self.revision = revision
            self.broadcast({"type": "_replace"})
            return
        self.revision = revision
        if start < end:
            rows = await in_worker(data.local_rows, self.plan, start, end, self._unwritten(self.plan))
            if rows and bars.length(rows):
                rows = bars.take(rows, rows["close_time"] <= max(end, now))
                if bars.length(rows) > APPEND_ROWS:
                    self.last_closed = int(rows["time"][-1])
                    self.broadcast({"type": "_replace"})
                else:
                    self._append(rows)
        self._form_from_store()

    def _form_from_store(self) -> None:
        """An aggregated series' forming bar from the stored live-base rows of its period."""
        if not self.same_base() and self.live_plan is not None:
            self._load_period(now_ns())
            if bars.length(self.period_rows):
                self._update(self._render(self.period_rows))

    def _adjusted(self) -> bool:
        return self.series.adjustment in ("split", "split_dividend")


# ── upstream (one source live stream) ──────────────────────────────────────


class Upstream:
    def __init__(self, live: Sessions, key, source_id: str, instrument: dict, plan: Plan, environment: str):
        self.live, self.key = live, key
        self.market = live.market
        self.source_id, self.instrument, self.plan, self.environment = source_id, instrument, plan, environment
        self.sessions: set[SeriesSession] = set()
        pointer = self.market.store.pointer(plan.key)
        self.last_closed: int | None = int(pointer["last"]) if pointer and pointer.get("last") is not None else None
        self.unwritten: bars.Columns = bars.empty()
        self.provisional: bars.Columns = bars.empty()  # rows sent before the source's publish cutoff
        self.state, self.problem = "connecting", None
        self.mode = "live"
        self.task: asyncio.Task | None = None
        self.writer: asyncio.Task | None = None
        self.closer: asyncio.Task | None = None
        self.lock = asyncio.Lock()
        self.subscribed = False

    async def attach(self, session: SeriesSession) -> None:
        if self.closer:
            self.closer.cancel()
            self.closer = None
        self.sessions.add(session)
        if self.task is None or self.task.done():
            session.set_status(self.state, self.problem)
            self.task = asyncio.ensure_future(self.run())
            self.writer = asyncio.ensure_future(self.write_loop())
        elif self.subscribed:
            self.live.market.track_task(asyncio.ensure_future(self.join(session)))
        else:
            session.set_status(self.state, self.problem)

    async def join(self, session: SeriesSession) -> None:
        """A session joining a live upstream says live only after its caught-up bars went out."""
        await self.catch_up()
        if session in self.sessions:
            session.set_status(self.state, self.problem)

    def detach(self, session: SeriesSession) -> None:
        self.sessions.discard(session)
        if not self.sessions and self.closer is None:
            self.closer = asyncio.ensure_future(self._close_later())

    async def _close_later(self) -> None:
        await asyncio.sleep(self.live.grace)
        if not self.sessions:
            await self.stop()
            self.live.upstreams.pop(self.key, None)

    async def stop(self) -> None:
        for task in (self.task, self.writer):
            if task is not None:
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
        await self.flush()

    def set_state(self, state: str, problem: dict | None = None) -> None:
        self.state, self.problem = state, problem
        for session in tuple(self.sessions):
            session.set_status(state, problem)

    async def run(self) -> None:
        attempt = 0
        registry = self.market.registry
        target = await in_worker(self.market.data.target, self.instrument, self.environment, self.source_id)
        while True:
            try:
                entry = registry.entry(self.source_id)
                events = entry.source.live(self.source_id, target, self.plan.kind, self.plan.base,
                                           self.plan.series.priceBasis)
                async for event in events:
                    kind = event["type"]
                    if kind == "subscribed":
                        attempt = 0
                        self.mode = "delayed" if event.get("mode") == "delayed" else "live"
                        await self.catch_up()
                        self.subscribed = True
                        self.set_state(self.mode)
                    elif kind == "gap":
                        self.subscribed = False
                        problem = event.get("problem")
                        if problem is not None and problem["code"] in FATAL:  # a reconnect the source refused
                            self.set_state("failed", problem)
                            return
                        self.set_state("recovering", problem)
                    elif kind == "bar":
                        self.on_bar(event["row"], event["closed"])
                raise MarketError("source_unavailable", t("{source_id} ended the live stream", source_id=self.source_id),
                                  source_id=self.source_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a live stream never dies of an unexpected error
                if not isinstance(exc, MarketError):
                    log.exception("Live stream %s failed", self.key)
                    exc = MarketError("source_unavailable", f"{type(exc).__name__}: {exc}"[:300],
                                      source_id=self.source_id)
                self.subscribed = False
                if exc.code in FATAL:
                    self.set_state("failed", exc.problem())
                    return
                self.set_state("connecting" if self.state == "connecting" else "recovering", exc.problem())
                delay = BACKOFF[min(attempt, len(BACKOFF) - 1)]
                if exc.retry_at:
                    delay = max(delay, (exc.retry_at - now_ns()) / 1e9)
                attempt += 1
                await asyncio.sleep(delay)

    def on_bar(self, row: dict, closed: bool) -> None:
        if closed:
            if self.last_closed is not None and row["time"] <= self.last_closed:
                return
            self.last_closed = row["time"]
            self.unwritten = bars.merge(self.unwritten, _row_columns(row))
            for session in tuple(self.sessions):
                session.on_closed(row)
        else:
            if self.last_closed is not None and row["time"] <= self.last_closed:
                return
            for session in tuple(self.sessions):
                session.on_forming(row)

    async def catch_up(self) -> None:
        """Fill the live base from where the sessions need it up to the last closed bar."""
        async with self.lock:
            data = self.market.data
            now = now_ns()
            step = timeframe_nanoseconds(self.plan.base)
            end = data.closed_until(self.instrument, self.plan.base, data.published_through(self.plan, now))
            wanted = [moment for session in self.sessions if (moment := session.need_from()) is not None]
            start = min(wanted) if wanted else end
            if start < end:
                await self.flush()
                rows = self.plan.period.get("requestRows") if self.plan.period else None
                if rows:
                    start = max(start, end - CATCH_UP_REQUESTS * rows * step)
                try:
                    await data.fill(self.plan, start, end, limit=None)
                except MarketError as exc:
                    self.problem = exc.problem()
                pointer = self.market.store.pointer(self.plan.key)
                if pointer and pointer.get("last") is not None:
                    last = int(pointer["last"])
                    self.last_closed = last if self.last_closed is None else max(self.last_closed, last)
        for session in tuple(self.sessions):
            await session.on_filled()

    async def write_loop(self) -> None:
        while True:
            await asyncio.sleep(WRITE_EVERY)
            await self.flush()
            problem = await self.settle()
            if problem is not None and problem["code"] in FATAL:
                self.task.cancel()
                self.set_state("failed", problem)
                return

    async def flush(self) -> None:
        rows = self.unwritten
        if not bars.length(rows):
            return
        # Rows not yet past the publish cutoff are stored but not counted as fetched: once past it, the next read
        # fetches them from history and the final values replace them.
        cutoff = self.market.data.published_through(self.plan, now_ns())
        spans = [(int(a), int(b)) for a, b in zip(rows["time"], rows["close_time"], strict=True) if b <= cutoff]
        def write():  # the revision lists the instrument's stored series (a directory): in the worker too
            revision = self.market.next_revision(self.instrument["instrumentId"])
            self.market.store.write(self.plan.key, rows, fetched=spans, none=[], source=self.source_id,
                                    revision=revision)
        await in_worker(write)
        self.provisional = bars.merge(self.provisional, bars.take(rows, rows["close_time"] > cutoff))
        written = rows["time"]
        if bars.length(self.unwritten):
            self.unwritten = bars.take(self.unwritten, ~np.isin(self.unwritten["time"], written))


    async def settle(self) -> dict | None:
        """Rows sent before the source published them are fetched again once it has (Yahoo's daily bar four hours
        after the close, the Hong Kong closing auction): when the final values differ from what went out, every
        session gets a replace. A fetch that fails keeps the rows for the next settle and returns its problem."""
        rows = self.provisional
        if not bars.length(rows):
            return None
        data = self.market.data
        due = rows["close_time"] <= data.published_through(self.plan, now_ns())
        if not due.any():
            return None
        sent = bars.take(rows, due)
        start, end = int(sent["time"][0]), int(sent["time"][-1]) + 1
        fill = await data.fill(self.plan, start, end, limit=None)
        if fill.problem is not None:
            return fill.problem
        self.provisional = bars.take(self.provisional, ~np.isin(self.provisional["time"], sent["time"]))
        final = await in_worker(self.market.store.read, self.plan.key, start, end)
        if not _same_rows(sent, final):
            for session in tuple(self.sessions):
                session.broadcast({"type": "_replace"})
        return None


def _same_rows(a: bars.Columns, b: bars.Columns) -> bool:
    if not b or bars.length(a) != bars.length(b) or not np.array_equal(a["time"], b["time"]):
        return False
    return all(np.array_equal(a[name], b[name], equal_nan=True) for name in bars.BAR_COLUMNS[2:])


# ── the manager ────────────────────────────────────────────────────────────


class Sessions:
    def __init__(self, market):
        self.market = market
        self.sessions: dict[tuple, SeriesSession] = {}
        self.upstreams: dict[tuple, Upstream] = {}
        self.grace = GRACE

    def open(self, params: dict):
        return self._items(params)

    async def _items(self, params: dict):
        session = await self._session(params)
        consumer = session.add()
        try:
            while True:
                yield await consumer.next()
        finally:
            session.remove(consumer)

    async def _session(self, params: dict) -> SeriesSession:
        data = self.market.data
        instrument = await in_worker(self.market.instrument_record, params["instrument"])
        series = MarketSeries.from_wire(params["series"])
        if series.instrumentId != params["instrument"]["instrumentId"]:
            raise MarketError("invalid_input", t("series.instrumentId does not name the instrument"))
        if instrument["instrumentId"] != series.instrumentId:
            from .data import _rebase
            series = _rebase(series, instrument)
        environment = params.get("environment", "production")
        warmup = int(params.get("warmup", 500))
        if series.capability != "ohlcv":
            from . import live_aux
            if series.capability not in live_aux.OBSERVATIONS:
                raise MarketError("unsupported", t("Live {capability} sessions are not available", capability=series.capability))
            depth = int(params.get("depth") or 0)
            key = (series.seriesId, environment, depth)
            session = self.sessions.get(key)
            if session is None:
                source_id = live_aux.source_for(self.market, instrument, series.capability)
                session = live_aux.AuxSession(self, key, instrument, series, environment, warmup, depth, source_id)
                self.sessions[key] = session
                await session.begin()
            return session
        key = (series.seriesId, environment)
        session = self.sessions.get(key)
        if session is not None:
            return session
        start = int(params["start"]) if params.get("start") else None
        plan = await in_worker(data.plan, instrument, series, start, environment=environment)
        session = self.sessions.get(key)  # another subscriber may have opened it meanwhile
        if session is not None:
            return session
        session = SeriesSession(self, key, instrument, series, plan, environment, warmup, start)
        self.sessions[key] = session
        await session.begin()
        return session

    def series_replaced(self, key) -> None:
        """A stored series was swapped whole (another source): its sessions re-plan and resend a replace."""
        for session in list(self.sessions.values()):
            plan = getattr(session, "plan", None)
            if plan is None or plan.key != key:
                continue
            session.plan = self.market.data.plan(session.instrument, session.series, session.start, environment=session.environment)
            session.revision = None
            session.broadcast({"type": "_replace"})

    def session_idle(self, session: SeriesSession) -> None:
        if self.sessions.get(session.key) is session:
            del self.sessions[session.key]
        self.market.track_task(asyncio.ensure_future(session.end()))

    def upstream_for(self, session: SeriesSession) -> Upstream | None:
        plan = session.plan
        if plan.source_id is None or plan.source_id.startswith("import:"):
            return None
        registry = self.market.registry
        entry = registry.entries.get(plan.source_id)
        if entry is None or "live" not in entry.descriptor["supports"]:
            return None
        row = registry.row(plan.source_id, session.instrument, plan.kind, plan.base)
        if not row or row.get("live", "none") == "none":
            return None
        periods = live_periods(row)
        target = session.target
        if plan.base in periods:
            base = plan.base
        else:
            dividing = [tf for tf in periods if bars.divides(tf, target, session.instrument["calendarId"])]
            if not dividing:
                return None
            base = min(dividing, key=lambda tf: timeframe_nanoseconds(tf) or 0)
        series = session.series
        key = (plan.source_id, session.instrument["instrumentId"], plan.kind, base, series.priceBasis,
               series.sessionScope, session.environment)
        upstream = self.upstreams.get(key)
        if upstream is None:
            store_key = SeriesKey(session.instrument["instrumentId"], series.capability, series.priceBasis,
                                  series.sessionScope, base, environment=session.environment)
            period = next((p for p in registry.periods(plan.source_id, session.instrument, plan.kind)
                           if p["timeframe"] == base), None)
            live_plan = Plan(session.instrument, series, plan.kind, store_key, base, plan.source_id, period)
            upstream = Upstream(self, key, plan.source_id, session.instrument, live_plan, session.environment)
            self.upstreams[key] = upstream
        return upstream

    def forming(self, series: MarketSeries, environment: str) -> dict | None:
        # Keys start with the series id: (series, environment) for bars, plus the book depth for observations.
        for key, session in self.sessions.items():
            if key[:2] == (series.seriesId, environment):
                return session.forming
        return None

    async def close(self) -> None:
        for session in list(self.sessions.values()):
            await session.end()
        for upstream in list(self.upstreams.values()):
            await upstream.stop()
        self.sessions.clear()
        self.upstreams.clear()
