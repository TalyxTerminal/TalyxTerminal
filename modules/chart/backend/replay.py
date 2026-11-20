"""Bar replay on one chart: the chart's window up to a chosen bar, then one bar at
a time on request.

A replay has its own feed over a copy of the chart's window, so its indicator instances are its
own (they compute the history before the chosen bar in full, then step bar by bar as in real
time) and the live ones, alerts included, keep running untouched. Heikin Ashi and brick types
project the replayed bars as they come. Stopping drops the replay's feed and instances.
"""
from __future__ import annotations

import asyncio
import uuid
from bisect import bisect_right

from .feeds import COLUMNS, Feed, ProjectedFeed


class ReplayFeed:
    """A raw feed's closed bars up to the replay's position; ``step`` reveals the next ones."""
    projected = False

    def __init__(self, base: Feed, at: int):
        self.base = base
        self.key = f"replay:{uuid.uuid4().hex[:12]}:{base.key}"
        self.instrument, self.series, self.syminfo = base.instrument, base.series, dict(base.syminfo)
        self.price_format = base.price_format
        self.all = {name: list(base.columns[name]) for name in COLUMNS}
        self.shown = max(1, bisect_right(self.all["time"], at))
        self.columns = {name: values[:self.shown] for name, values in self.all.items()}
        self.forming = None
        self.status = None
        self.realtime = True
        self.has_more_before = False
        self.limit_reached = False
        self.listeners: list = []
        self.users = 0
        self.ready = asyncio.Event()
        self.ready.set()
        self.whole = asyncio.Event()  # the base's window when the replay started
        if base.whole.is_set():
            self.whole.set()

    @property
    def raw(self) -> ReplayFeed:
        return self

    def step(self, count: int) -> int:
        count = min(count, len(self.all["time"]) - self.shown)
        if count:
            for name, values in self.all.items():
                self.columns[name].extend(values[self.shown:self.shown + count])
            self.shown += count
            for listener in list(self.listeners):
                listener.on_appended(self, count)
        return count


class Replay:
    """The replay's feed as the chart shows it (projected for Heikin Ashi and brick types)."""

    def __init__(self, base: Feed, at: int, chart_type: str, projection: dict | None):
        self.raw = ReplayFeed(base, at)
        self.feed = self.raw
        if projection is not None:
            self.feed = ProjectedFeed(None, self.raw.key + f"|{chart_type}", self.raw, chart_type, projection)

    async def open(self) -> None:
        if self.feed is not self.raw:
            await self.feed.open()

    def close(self) -> None:
        if self.feed is not self.raw:
            self.raw.listeners.remove(self.feed)
