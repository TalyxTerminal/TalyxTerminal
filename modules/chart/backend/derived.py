"""Market's derived series read with request.security, in TradingView's spelling.

A script asks for ``request.security(syminfo.tickerid + "_OI", ...)``: the suffix names a Market
series of the symbol before it. ``SUFFIXES`` (point series) and ``PRICE_SUFFIXES`` (the
contract's mark and index price bars, read like any bars) are the whole table (the indicator
authoring doc repeats it). Market's session for such a series sends its stored points (a replace, then an
append per new point, the same rows a read gives later) and the latest live observation
(``update``). ``PointsFeed`` turns them into bars of the script's timeframe on Market's bar grid:
a bar's open is the value carried in from before it unless a point sits at its open, then its
points; a bar with no point is flat at the carried value. Closed bars come from stored points
only, so a bar that closed live equals a later recompute (when the point reached Chart before
its bar closed); the forming bar adds the live observation (open interest).
"""
from __future__ import annotations

import asyncio
import logging
from bisect import bisect_left
from collections.abc import Callable

from vocabulary.identity import timeframe_nanoseconds
from vocabulary.instruments import MarketInstrument, market_data_series

from .marketdata import Market, now_ms
from .wire import DEFAULT_WINDOW

# suffix: (Market capability, value column, whether the live observation shows on the forming bar)
SUFFIXES = {"_OI": ("open_interest", "value", True), "_FR": ("funding_rate", "rate", False)}
PRICE_SUFFIXES = {"_MARK": "mark", "_INDEX": "index"}  # suffix: the bars' price basis
DAY = 86_400_000
LOG = logging.getLogger(__name__)


def derived_of(symbol: str) -> tuple[str, str] | None:
    """("OKX:BTC/USDT:USDT", "_OI") for a suffixed symbol, else None."""
    for suffix in (*SUFFIXES, *PRICE_SUFFIXES):
        if symbol.endswith(suffix):
            return symbol[:-len(suffix)], suffix
    return None


class PointsFeed:
    """Bars of one timeframe from a Market point series; listeners as for ``feeds.Feed``
    (``on_replaced``, ``on_appended``, ``on_forming``). ``failed`` hears why the series stopped."""

    status = None  # failures go to ``failed``

    def __init__(self, market: Market, instrument: dict, suffix: str, timeframe: str,
                 failed: Callable[[str], None]):
        capability, self.column, self.observes = SUFFIXES[suffix]
        self.market, self.instrument, self.failed = market, instrument, failed
        self.series = market_data_series(MarketInstrument.from_wire(instrument), capability).to_dict()
        self.grid_series = market.default_series(instrument, timeframe)
        self.length = (timeframe_nanoseconds(timeframe) or 31 * DAY * 1_000_000) // 1_000_000
        self.times: list[int] = []  # stored points, ms
        self.values: list[float] = []
        self.observed: tuple[int, float] | None = None
        self.opens: list[int] = []  # Market's bar grid from the window's start
        self.opens_from = 0
        self.closes: list[int] = []
        self.next = 0  # grid index of the first bar not closed yet
        self.columns = {name: [] for name in ("time", "closeTime", "open", "high", "low", "close", "volume")}
        self.forming: dict | None = None
        self.listeners: list = []
        self.ready = asyncio.Event()
        self.whole = asyncio.Event()  # as feeds.Feed: Market's window is whole
        self.subscription = None
        self.task: asyncio.Task | None = None
        self.timer: asyncio.TimerHandle | None = None
        self.lock = asyncio.Lock()  # Market's items and the closing timer take turns

    async def open(self) -> None:
        self.subscription = await self.market.subscribe(self.instrument, self.series, DEFAULT_WINDOW)
        self.task = asyncio.create_task(self.run())

    async def run(self) -> None:
        async for delivered in self.subscription:
            await self.guarded(self.take(delivered))

    async def tick(self) -> None:
        await self.guarded(self.advance())

    async def guarded(self, work) -> None:
        async with self.lock:
            try:
                await work
            except Exception as error:  # noqa: BLE001 -- the scripts reading the series say why
                LOG.exception("Derived series %s stopped", self.series["seriesId"])
                self.failed(str(error))

    async def take(self, item: dict) -> None:
        if item["type"] == "status" and item["state"] == "failed":
            self.failed(item["problem"]["message"] if item["problem"] else "Market stopped the series")
        elif item["type"] == "replace":
            self.times, self.values = [], []
            self.add(item["page"]["columns"])
            await self.rebuild(item["page"]["fill"]["state"] != "filling")
        elif item["type"] == "append":
            self.add(item["columns"])
            await self.advance()
        elif item["type"] == "update":  # funding's (a predicted rate) only tells the time
            if self.observes and item["row"].get(self.column) is not None:
                self.observed = (int(item["row"]["time"]) // 1_000_000, float(item["row"][self.column]))
            await self.advance()

    def add(self, columns: dict) -> None:
        for time, value in zip(columns["time"], columns[self.column], strict=True):
            time = int(time) // 1_000_000
            if value is not None and (not self.times or time > self.times[-1]):
                self.times.append(time)
                self.values.append(float(value))

    # ── bars ──

    async def grid_through(self, time: int) -> None:
        """Market's bars from the window's start until one closes after ``time``."""
        while not self.closes or self.closes[-1] <= time:
            start = self.closes[-1] if self.closes else self.opens_from
            end = start + min(366 * DAY, max(DAY, self.length * 2_000))
            opens, closes, _covered = await self.market.bar_times(self.instrument, self.grid_series, start, end)
            if not opens:
                raise LookupError(f"Market's calendar has no bars after {start}")
            self.opens += opens
            self.closes += closes

    def bar(self, index: int, live: bool) -> dict | None:
        opens, closes = self.opens[index], self.closes[index]
        first, end = bisect_left(self.times, opens), bisect_left(self.times, closes)
        values = self.values[first:end]
        if first > 0 and (not values or self.times[first] > opens):
            values = [self.values[first - 1], *values]
        if live and self.observed is not None and opens <= self.observed[0] < closes:
            values.append(self.observed[1])
        if not values:
            return None
        return {"time": opens, "closeTime": closes, "open": values[0], "high": max(values), "low": min(values),
                "close": values[-1], "volume": None}

    async def rebuild(self, whole: bool) -> None:
        now = now_ms()
        self.opens, self.closes = [], []
        self.columns = {name: [] for name in self.columns}
        if not self.times:
            self.next, self.forming = 0, None
        else:
            self.opens_from = max(self.times[0], now - DEFAULT_WINDOW * self.length) - self.length
            await self.grid_through(now)
            self.next = 0
            self.close_through(now)
            self.forming = self.bar(self.next, True)
        if whole:
            self.whole.set()
        else:
            self.whole.clear()
        self.ready.set()
        self.notify("on_replaced")
        self.schedule()

    async def advance(self) -> None:
        if not self.opens:
            await self.rebuild(self.whole.is_set())
            return
        now = now_ms()
        await self.grid_through(now)
        count = self.close_through(now)
        if count:
            self.notify("on_appended", count)
        self.forming = self.bar(self.next, True)
        self.notify("on_forming")
        self.schedule()

    def close_through(self, now: int) -> int:
        start = len(self.columns["time"])
        while self.closes[self.next] <= now:
            bar = self.bar(self.next, False)
            if bar is not None:
                for name, value in bar.items():
                    self.columns[name].append(value)
            self.next += 1
        return len(self.columns["time"]) - start

    def schedule(self) -> None:
        """The forming bar closes on time, whether or not Market sends anything then."""
        if self.timer is not None:
            self.timer.cancel()
        if self.opens:
            delay = max(0.0, (self.closes[self.next] - now_ms()) / 1000)
            self.timer = asyncio.get_running_loop().call_later(delay, lambda: asyncio.ensure_future(self.tick()))

    def notify(self, event: str, *args) -> None:
        for listener in list(self.listeners):
            getattr(listener, event)(self, *args)

    async def close(self) -> None:
        if self.timer is not None:
            self.timer.cancel()
        if self.task is not None:
            self.task.cancel()
        if self.subscription is not None:
            await self.subscription.close()
