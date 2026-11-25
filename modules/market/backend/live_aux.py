"""Live observation sessions: ticker, order book, trades, funding and open interest.

These are the ``session`` stream for capabilities that are not bars. One session per
(series, environment, book depth) runs the source's live stream and shares it with
every consumer: the first item is a ``replace`` (recent trades for ``trades``, the
latest observation for ticker and book), then ``update`` items carry the newest ticker, book,
funding or open interest (a slow reader only gets the latest), and ``append`` items
carry new trades. Until the source first goes live a stream is ``connecting``. Recovery is
Market's: a lost stream is ``recovering`` and resumes
with backoff; a fatal source error is ``failed``.

Funding and open interest are also stored series: their replace is the latest stored
points (what a latest ``data.read`` page gives), and each point Market stores later
goes out once as an ``append``. When a point is due (the next 5-minute point, the next
funding settlement the live observation names), the session fills the series through
``Data.fill``, whose requests go through the source's request queue like every read,
and retries on the live backoff until the point is stored.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from talyx_sdk.i18n import t

from vocabulary.identity import timeframe_nanoseconds
from vocabulary.instruments import MarketSeries

from . import bars
from .common import MarketError, now_ns, time_range
from .live import BACKOFF, CATCH_UP_REQUESTS, FATAL, Consumer, WindowFill
from .routing import in_worker

log = logging.getLogger(__name__)
OBSERVATIONS = {"ticker", "order_book", "trades", "funding_rate", "open_interest"}
KIND = {"ticker": "ticker", "order_book": "order_book", "trades": "trades", "funding_rate": "funding_rate",
        "open_interest": "open_interest"}
STORED = {"funding_rate", "open_interest"}
TRADE_COLUMNS = ("time", "price", "amount", "side", "id")
MAX_TRADES = 500


class AuxConsumer(Consumer):
    """Trades share timestamps, so appends are passed through as they come."""

    async def next(self) -> dict:
        while True:
            while not self.items:
                self.event.clear()
                await self.event.wait()
            item = self.items.popleft()
            if item["type"] == "_replace":
                return await self.replace(item)
            if item["type"] == "append":
                self.pending_rows = max(0, self.pending_rows - len(item["columns"]["time"]))
            return item


class AuxSession:
    def __init__(self, sessions, key, instrument: dict, series: MarketSeries, environment: str, warmup: int,
                 depth: int, source_id: str):
        self.sessions, self.key = sessions, key
        self.market = sessions.market
        self.instrument, self.series, self.environment = instrument, series, environment
        self.warmup, self.depth, self.source_id = warmup, depth, source_id
        self.capability = series.capability
        self.consumers: set[AuxConsumer] = set()
        self.latest: dict | None = None
        self.trades: list[dict] = []
        self.state, self.problem, self.since = "connecting", None, now_ns()
        self.task: asyncio.Task | None = None
        self.follower: asyncio.Task | None = None
        self.last_stored: int | None = None
        self.observed = asyncio.Event()
        self.forming = None
        self.window = WindowFill(self)

    # consumers (the interface live.Sessions expects)

    def add(self) -> Consumer:
        # Stored points are passed on once each, as for bars; trades share timestamps.
        consumer = Consumer(self) if self.capability in STORED else AuxConsumer(self)
        consumer.push({"type": "_replace"})
        consumer.push(self.status_item())
        self.consumers.add(consumer)
        return consumer

    def remove(self, consumer) -> None:
        self.consumers.discard(consumer)
        if not self.consumers:
            self.sessions.session_idle(self)

    def broadcast(self, item: dict) -> None:
        for consumer in tuple(self.consumers):
            consumer.push(item)

    def status_item(self) -> dict:
        return {"type": "status", "state": self.state, "problem": self.problem, "since": str(self.since)}

    def set_status(self, state: str, problem: dict | None = None) -> None:
        if (state, problem) == (self.state, self.problem):
            return
        self.state, self.problem, self.since = state, problem, now_ns()
        self.broadcast(self.status_item())

    def history_plan(self):
        """The stored series' plan, or None when no source declares its history (then only updates come)."""
        plan = self.market.data.plan(self.instrument, self.series, None, environment=self.environment)
        return plan if plan.period is not None else None

    async def build_replace(self) -> dict:
        if self.capability in STORED:
            if self.history_plan() is None:
                # A live observation (e.g. a predicted funding rate) is never a stored point.
                page = await self.market.data.page(self.instrument, self.series, limit=self.warmup, fill="none",
                                                   environment=self.environment)
                if not page["columns"]["time"]:
                    message = t("No source provides {capability} history for {symbol}", capability=self.capability,
                                symbol=self.instrument["displaySymbol"])
                    page["fill"] = {"state": "failed", "problem": MarketError("unsupported", message).problem()}
            else:
                try:
                    page = await self.window.page()
                except MarketError as exc:
                    self.set_status("failed" if exc.code in FATAL else self.state, exc.problem())
                    page = await self.market.data.page(self.instrument, self.series, limit=self.warmup, fill="none",
                                                       environment=self.environment)
                    page["fill"] = {"state": "partial" if page["columns"]["time"] else "failed",
                                    "problem": exc.problem()}
            times = page["columns"]["time"]
            if times:
                self.last_stored = max(int(times[-1]), self.last_stored or 0)
            return page
        if self.capability == "trades":
            rows = self.trades[-self.warmup:] if self.warmup else []
            columns = {name: [row.get(name) for row in rows] for name in TRADE_COLUMNS}
        elif self.latest is not None:
            columns = {name: [value] for name, value in self.latest.items()}
        else:
            columns = {"time": []}
        times = columns.get("time") or []
        return {"instrumentId": self.instrument["instrumentId"], "seriesId": self.series.seriesId,
                "columns": columns, "forming": None,
                "range": time_range(int(times[0]), int(times[-1]) + 1) if times else None,
                "gaps": [], "hasMoreBefore": False, "nextBefore": None,
                "source": self.market.source_ref(self.source_id), "fill": {"state": "not_needed", "problem": None},
                "revision": 0}

    # lifecycle

    async def begin(self) -> None:
        if self.capability == "trades" and self.warmup:
            with contextlib.suppress(MarketError):
                self.trades = await self._recent_trades()
        self.task = asyncio.ensure_future(self.run())
        if self.capability in STORED and self.history_plan() is not None:
            self.follower = asyncio.ensure_future(self._follow_stored())

    async def end(self) -> None:
        for task in (self.task, self.follower):
            if task is not None:
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
        await self.window.stop()

    async def _next_due(self, plan) -> int:
        """When the next stored point is due: the settlement the live funding names, else one period on."""
        if self.last_stored is None:
            return now_ns()
        if self.capability == "funding_rate":
            await self.observed.wait()
            settles = self.latest.get("nextFundingTime")
            if settles is not None and int(settles) > self.last_stored:
                return int(settles)
        return self.last_stored + timeframe_nanoseconds(plan.base)

    async def _follow_stored(self) -> None:
        """First what was stored or published since the replace's rows, then each point once it is due. A fetch
        the source refuses for good ends following with the series failed; other failures are tried again."""
        attempt, due = 0, None
        await self.window.done.wait()  # the opening replace fills the window first
        while True:
            plan = self.history_plan()
            if due is not None and due > now_ns():
                await asyncio.sleep((due - now_ns()) / 1e9)
            problem = await self._append_stored(plan)
            if problem is not None and problem["code"] in FATAL:
                self.set_status("failed", problem)
                return
            if due is None or (self.last_stored is not None and self.last_stored >= due):
                attempt, due = 0, await self._next_due(plan)
            else:
                await asyncio.sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
                attempt += 1

    async def _append_stored(self, plan) -> dict | None:
        """Fill the series up to the publish cutoff and send the points stored since the last one sent; the fill's
        problem, if any."""
        data = self.market.data
        now = now_ns()
        start = self.last_stored + 1 if self.last_stored is not None else \
            data.window_start(self.instrument, plan.base, now, self.warmup)
        end = data.published_through(plan, now) + 1
        problem = None
        try:
            problem = (await data.fill(plan, start, end, limit=CATCH_UP_REQUESTS)).problem
        except MarketError as exc:
            if exc.code != "fill_too_large":
                raise
            # More requests than a catch-up makes: what is stored still goes out.
        rows = await in_worker(data.local_rows, plan, start, end)
        if rows and bars.length(rows):
            self.last_stored = int(rows["time"][-1])
            self.broadcast({"type": "append", "columns": bars.to_wire(rows), "revision": data.revision(plan)})
        return problem

    async def _recent_trades(self) -> list[dict]:
        entry = self.market.registry.entry(self.source_id)
        recent = getattr(entry.source, "recent_trades", None)
        if recent is None:
            return []
        target = await in_worker(self.market.data.target, self.instrument, self.environment, self.source_id)
        return (await recent(self.source_id, target, self.warmup))[-self.warmup:]

    async def run(self) -> None:
        attempt = 0
        target = await in_worker(self.market.data.target, self.instrument, self.environment, self.source_id)
        while True:
            try:
                entry = self.market.registry.entry(self.source_id)
                events = entry.source.live(self.source_id, target, KIND[self.capability], None, "last", self.depth)
                async for event in events:
                    kind = event["type"]
                    if kind == "subscribed":
                        attempt = 0
                        self.set_status("live")
                    elif kind == "gap":
                        problem = event.get("problem")
                        if problem is not None and problem["code"] in FATAL:  # a reconnect the source refused
                            self.set_status("failed", problem)
                            return
                        self.set_status("recovering", problem)
                    elif kind == "observation":
                        self.set_status("live")
                        self.latest = event["row"]
                        self.observed.set()
                        self.broadcast({"type": "update", "row": event["row"], "receivedAt": str(now_ns())})
                    elif kind == "trades":
                        self.set_status("live")
                        self._on_trades(event["rows"])
                raise MarketError("source_unavailable", t("{source_id} ended the live stream", source_id=self.source_id),
                                  source_id=self.source_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a live stream never dies of an unexpected error
                if not isinstance(exc, MarketError):
                    log.exception("Live %s stream %s failed", self.capability, self.key)
                    exc = MarketError("source_unavailable", f"{type(exc).__name__}: {exc}"[:300],
                                      source_id=self.source_id)
                if exc.code in FATAL:
                    self.set_status("failed", exc.problem())
                    return
                self.set_status("connecting" if self.state == "connecting" else "recovering", exc.problem())
                delay = BACKOFF[min(attempt, len(BACKOFF) - 1)]
                if exc.retry_at:
                    delay = max(delay, (exc.retry_at - now_ns()) / 1e9)
                attempt += 1
                await asyncio.sleep(delay)

    def _on_trades(self, rows: list[dict]) -> None:
        known = {row["id"] for row in self.trades[-MAX_TRADES:] if row.get("id") is not None}
        fresh = [row for row in rows if row.get("id") is None or row["id"] not in known]
        if not fresh:
            return
        self.trades = (self.trades + fresh)[-MAX_TRADES:]
        self.broadcast({"type": "append", "columns": {name: [row.get(name) for row in fresh] for name in TRADE_COLUMNS},
                        "revision": 0})


def source_for(market, instrument: dict, capability: str) -> str:
    """Exchange contracts stream from their venue; other instruments from the best ranked live source."""
    registry = market.registry
    if instrument["identityVersion"] == 1 and instrument["venueId"] in registry.entries:
        return instrument["venueId"]
    ranked = registry.rank(instrument, "quotes" if capability == "ticker" else KIND[capability], history=False)
    if not ranked:
        raise MarketError("unsupported", t("No source streams {capability} for {symbol}", capability=capability, symbol=instrument['symbol']))
    return ranked[0][0]
