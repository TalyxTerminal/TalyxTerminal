"""Series windows from Market's session, shared by every chart and alert that reads them.

A feed is one Market session subscription and the window of closed bars it builds:
Market's first replace (up to 10,000 bars; or its newest page while Market fills the rest, then one
replace with the whole window), then appends, the forming bar and status, exactly as Market sends
them. Chart keeps no connection state of its own. A bar counts as realtime only
when it arrives after a ``live`` or ``delayed`` status (bars caught up before that only build
state). A chart that scrolls back gets a feed of its own whose window reaches further back
(``Backfill``); Heikin Ashi and brick chart types read a projected feed over a raw one.

Charts show Market's newest page at once. What needs the whole window (readings, alert indicators,
request.security) waits for it in one place, ``Hub.acquire(whole=True)``: the window is whole once
Market's replace no longer says filling and the feed reached back as far as it was asked to.

Listeners get ``on_replaced(feed)``, ``on_appended(feed, count)`` (raw feeds), ``on_spliced(feed,
start)`` (projected feeds), ``on_forming(feed)`` and ``on_status(feed)``.
"""
from __future__ import annotations

import asyncio
import logging
from bisect import bisect_left
from operator import itemgetter

from talyx_sdk import ContractError

from . import bars as wire_bars
from .bars import price_format
from .marketdata import Market, now_ms, pine_period
from .projection import (
    CLOSE,
    CLOSE_TIME,
    HIGH,
    LOW,
    OPEN,
    SOURCE_TIME,
    TIME,
    VOLUME,
    CausalBarProjector,
    ProjectionSpec,
    default_box_size,
)
from .wire import DEFAULT_WINDOW, MAX_WINDOW

LOG = logging.getLogger(__name__)
COLUMNS = wire_bars.FIELDS


def empty() -> dict:
    return {name: [] for name in COLUMNS}


class Feed:
    projected = False

    def __init__(self, hub: Hub, key: str, instrument: dict, series: dict, start: int | None, cap: int = MAX_WINDOW,
                 reach: int | None = None):
        self.hub, self.key, self.instrument, self.series = hub, key, instrument, series
        self.start = start  # a scrolled-back window reaches back to here before it goes out
        self.reach = reach  # a request.security window reaches back to here after its bars went out
        self.cap = cap  # the most bars it holds
        self.columns = empty()
        self.forming: dict | None = None
        self.status: dict | None = None
        self.realtime = False
        self.price_format = {"precision": 2, "minMove": 0.01}
        self.has_more_before = False
        self.limit_reached = False
        self.syminfo: dict = {}
        self.listeners: list = []
        self.users = 0
        self.ready = asyncio.Event()
        self.subscription = None
        self.task: asyncio.Task | None = None
        self.increment: str | None = None
        self.terms: asyncio.Task | None = None
        self.whole = asyncio.Event()  # Market's replace no longer says "filling", and the window reached back
        self.news = asyncio.Event()  # set (and renewed) at each replace and status: ``wait_whole`` looks again

    @property
    def raw(self) -> Feed:
        """The feed of Market's bars (a projected feed's base)."""
        return self

    async def open(self) -> None:
        """Subscribes at once; the price increment (Market's terms, maybe a source request) follows,
        and a format it changes goes out on its own (``on_format``), not with the bars again."""
        self.syminfo = await syminfo_of(self.hub.market, self.instrument, self.series)
        self.set_format(self.price_format)
        self.terms = asyncio.create_task(self.read_increment())
        await self.subscribe()

    async def read_increment(self) -> None:
        try:
            self.increment = await self.hub.market.price_increment(self.instrument)
        except ContractError as error:  # the format read from the data stands
            LOG.warning("No price increment for %s: %s", self.key, error.message)
            return
        if self.ready.is_set():
            changed = price_format(self.increment, self.columns["close"][-500:])
            if changed != self.price_format:
                self.set_format(changed)
                self.notify("on_format")

    def set_format(self, value: dict) -> None:
        self.price_format = value
        self.syminfo.update(mintick=value["minMove"], pricescale=round(1 / value["minMove"]))

    async def subscribe(self) -> None:
        self.subscription = await self.hub.market.subscribe(self.instrument, self.series, DEFAULT_WINDOW)
        self.task = asyncio.create_task(self.run())

    async def run(self) -> None:
        try:
            async for delivered in self.subscription:
                await self.take(delivered)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 -- the chart shows why the series stopped
            LOG.exception("Series %s stopped", self.key)
            problem = {"code": getattr(error, "code", "unavailable"), "message": str(error)}
            await self.take({"type": "status", "state": "failed", "problem": problem, "since": str(now_ms() * 1_000_000)})

    async def take(self, item: dict) -> None:
        kind = item["type"]
        if kind == "replace":
            await self.replace(item["page"])
        elif kind == "append":
            self.append(item["columns"])
        elif kind == "update":
            self.forming = wire_bars.row(item["row"])
            self.notify("on_forming")
        else:
            self.status = {"type": "status", "state": item["state"], "problem": item["problem"],
                           "since": int(item["since"]) // 1_000_000}
            self.realtime = item["state"] in ("live", "delayed")
            self.renew()
            self.notify("on_status")

    def renew(self) -> None:
        self.news.set()
        self.news = asyncio.Event()

    async def wait_whole(self, *, latest_rows: int | None = None) -> None:
        """Wait for the complete window, or enough recent closed bars for a bounded raw read."""
        while not self.whole.is_set():
            if self.status is not None and self.status["state"] == "failed":
                problem = self.status["problem"] or {}
                raise ContractError(problem.get("code", "unavailable"),
                                    problem.get("message", "Market stopped the series"), execution="failed")
            if latest_rows is not None and len(self.columns["time"]) >= latest_rows:
                return
            await self.news.wait()

    async def replace(self, page: dict) -> None:
        market = page["columns"]
        # Up to 10,000 rows of decimal text: parsed in a worker thread, not on the Host's event loop.
        columns = await asyncio.to_thread(wire_bars.columns, market)
        whole = page["fill"]["state"] != "filling"
        if self.ready.is_set() and whole == self.whole.is_set() and (kept := self.extended_by(columns)) is not None:
            # Market merged a catch-up into a replace: the bars we have stand, the new ones append.
            self.append_columns({name: values[kept:] for name, values in columns.items()})
            self.forming = wire_bars.row(page["forming"]) if page["forming"] else None
            self.notify("on_forming")
            return
        self.columns = columns
        self.forming = wire_bars.row(page["forming"]) if page["forming"] else None
        self.set_format(price_format(self.increment, market["close"][-500:]))
        self.has_more_before = page["hasMoreBefore"]
        self.limit_reached = False
        # A scrolled-back window reaches back to its start before it goes out; Market's newest page goes
        # out as it is.
        if whole and self.start is not None:
            await self.reach_back(self.start)
        if whole:
            self.whole.set()
        else:
            self.whole.clear()
        self.ready.set()
        self.renew()
        self.notify("on_replaced")
        # A request.security window (a long chart's intrabars) goes out with what Market's window holds,
        # so the newest sessions draw at once, then once more reaching back to the chart's first bar (a
        # cold symbol's months of minutes take Market half a minute).
        if whole and self.reach is not None and await self.reach_back(self.reach):
            self.renew()
            self.notify("on_replaced")

    async def reach_back(self, to: int) -> int:
        """Older bars back to ``to``; the bars Market sent stand when it cannot give them."""
        try:
            return (await self.extend(to=to))["loaded"]
        except ContractError as error:
            LOG.warning("Could not reach %s back to %d: %s", self.key, to, error.message)
            return 0

    def extended_by(self, new: dict) -> int | None:
        """How many leading rows of a Market replace repeat our window's last bars unchanged, when
        it only adds bars after them; None when it changes anything we have."""
        old = self.columns["time"]
        if not old or not new["time"]:
            return None
        start = bisect_left(old, new["time"][0])
        overlap = len(old) - start
        if start == len(old) or overlap > len(new["time"]):
            return None
        if any(self.columns[name][start:] != new[name][:overlap] for name in COLUMNS):
            return None
        return overlap

    def append(self, market: dict) -> None:
        self.append_columns(wire_bars.columns(market))

    def append_columns(self, added: dict) -> None:
        last = self.columns["time"][-1] if self.columns["time"] else -1
        keep = [index for index, time in enumerate(added["time"]) if time > last]
        for name in COLUMNS:
            self.columns[name].extend(added[name][index] for index in keep)
        if self.forming is not None and self.columns["time"] and self.forming["time"] <= self.columns["time"][-1]:
            self.forming = None
        if keep:
            self.notify("on_appended", len(keep))

    async def extend(self, *, to: int | None = None, bars: int | None = None) -> dict:
        """Older bars from Market until the window starts at or before ``to`` or grew by ``bars``,
        Market has nothing older, or the window holds its cap (30,000 bars for a chart)."""
        loaded = 0
        while self.columns["time"] and len(self.columns["time"]) < self.cap and self.has_more_before:
            if to is not None and self.columns["time"][0] <= to or bars is not None and loaded >= bars:
                break
            limit = min(10_000, self.cap - len(self.columns["time"]), *([bars - loaded] if bars is not None else []))
            first = self.columns["time"][0]
            page = await self.hub.market.older(self.instrument, self.series, first, limit)
            older = await asyncio.to_thread(wire_bars.columns, page["columns"])
            if not self.columns["time"] or self.columns["time"][0] != first:
                continue  # a replace (Market's whole window) or another reach came meanwhile: ask from its first bar
            if not older["time"]:
                self.has_more_before = False
                break
            for name in COLUMNS:
                self.columns[name][:0] = older[name]
            loaded += len(older["time"])
            self.has_more_before = page["hasMoreBefore"]
        self.limit_reached = len(self.columns["time"]) >= self.cap
        self.start = self.columns["time"][0] if self.columns["time"] else self.start
        return {"loaded": loaded, "reachedStart": not self.has_more_before, "limitReached": self.limit_reached}

    async def backfill(self, *, to: int | None, bars: int | None) -> dict:
        result = await self.extend(to=to, bars=bars)
        if result["loaded"]:
            self.notify("on_replaced")
        return result

    async def retry(self) -> None:
        """Market stops a failed series; the user's retry subscribes again."""
        if self.status is not None and self.status["state"] == "failed":
            await self.close_subscription()
            self.status = None
            await self.subscribe()

    def notify(self, event: str, *args) -> None:
        for listener in list(self.listeners):
            getattr(listener, event)(self, *args)

    async def close_subscription(self) -> None:
        if self.task is not None:
            self.task.cancel()
        if self.subscription is not None:
            await self.subscription.close()

    async def close(self) -> None:
        if self.terms is not None:
            self.terms.cancel()
        await self.close_subscription()


class ProjectedFeed:
    """Heikin Ashi or brick bars over a raw feed (``projection.py``). Every change is a splice:
    bars from ``start`` on are replaced. Pine reads the confirmed bars: on brick types the last
    confirmed brick is still growing, so it runs as the forming bar."""
    projected = True
    forming = None  # the forming raw bar is part of the projected bars

    def __init__(self, hub: Hub | None, key: str, base: Feed, kind: str, projection: dict):
        self.hub, self.key, self.base, self.kind = hub, key, base, kind
        box_size = projection["boxSize"]
        if box_size is None and (kind in ("renko", "kagi", "point_figure") or kind == "range" and projection["rangeTicks"] is None):
            box_size = default_box_size(kind, [base.columns[name] for name in COLUMNS], base.price_format["minMove"],
                                        projection["reversalBoxes"])
        self.spec = ProjectionSpec(kind=kind, source=projection["source"], box_size=box_size,
                                   reversal_boxes=projection["reversalBoxes"], line_count=projection["lineCount"],
                                   range_ticks=projection["rangeTicks"], minimum_tick=base.price_format["minMove"])
        self.listeners: list = []
        self.users = 0
        self.columns = empty()
        self.columns["sourceTime"] = []
        self.confirmed = 0
        self.projector = CausalBarProjector(self.spec)
        self.ready = asyncio.Event()

    # The raw feed's state and identity.
    @property
    def raw(self) -> Feed:
        return self.base

    @property
    def instrument(self) -> dict:
        return self.base.instrument

    @property
    def series(self) -> dict:
        return self.base.series

    @property
    def syminfo(self) -> dict:
        return self.base.syminfo

    @property
    def status(self) -> dict | None:
        return self.base.status

    @property
    def price_format(self) -> dict:
        return self.base.price_format

    @property
    def has_more_before(self) -> bool:
        return self.base.has_more_before

    @property
    def limit_reached(self) -> bool:
        return self.base.limit_reached

    async def open(self) -> None:
        self.base.listeners.append(self)
        if self.base.ready.is_set():
            self.on_replaced(self.base)

    def on_replaced(self, base: Feed) -> None:
        seeded = self.projector.seed([base.columns[name] for name in COLUMNS])
        self.columns = {name: seeded[index] for name, index in PROJECTED_COLUMNS.items()}
        if base.forming is not None:
            self.splice(*self.projector.update(raw_row(base.forming), False))
        self.confirmed = self.projector.confirmed
        self.ready.set()
        self.notify("on_replaced")

    def splice(self, start: int, bars: list) -> None:
        for name, index in PROJECTED_COLUMNS.items():
            column = self.columns[name]
            del column[start:]
            column.extend(map(itemgetter(index), bars))
        self.confirmed = self.projector.confirmed

    def step(self, row: tuple, closed: bool) -> None:
        start, bars = self.projector.update(row, closed)
        self.splice(start, bars)
        self.notify("on_spliced", start)

    def on_appended(self, base: Feed, count: int) -> None:
        columns = [base.columns[name][-count:] for name in COLUMNS]
        for row in zip(*columns, strict=True):
            self.step(row, True)

    def on_forming(self, base: Feed) -> None:
        if base.forming is not None:
            self.step(raw_row(base.forming), False)

    def on_status(self, base: Feed) -> None:
        self.notify("on_status")

    def on_format(self, base: Feed) -> None:
        self.notify("on_format")

    def notify(self, event: str, *args) -> None:
        for listener in list(self.listeners):
            getattr(listener, event)(self, *args)

    async def close(self) -> None:
        self.base.listeners.remove(self)
        await self.hub.release(self.base)


# projected bar fields (projection.py) as the stream's columns
PROJECTED_COLUMNS = {"time": TIME, "closeTime": CLOSE_TIME, "open": OPEN, "high": HIGH, "low": LOW, "close": CLOSE,
                     "volume": VOLUME, "sourceTime": SOURCE_TIME}


def raw_row(bar: dict) -> tuple:
    return tuple(bar[name] for name in COLUMNS)


async def syminfo_of(market: Market, instrument: dict, series: dict) -> dict:
    """Pine's syminfo of a series, in TradingView's form so scripts write
    request.security("OKX:ETHUSDT", ...); mintick and pricescale follow the price format."""
    return {"prefix": instrument["venueId"].upper(),
            "description": instrument.get("name") or instrument["displaySymbol"],
            "ticker": instrument["displaySymbol"], "currency": instrument.get("quote") or instrument.get("currency")
            or "", "basecurrency": instrument.get("base") or "", "period": pine_period(series["timeframe"]),
            "type": instrument["assetClass"], "timezone": await market.timezone(instrument["calendarId"])}


class Hub:
    def __init__(self, market: Market):
        self.market = market
        self.feeds: dict[str, Feed | ProjectedFeed] = {}
        self.locks: dict[str, asyncio.Lock] = {}

    async def acquire(self, instrument: dict, series: dict, *, window_of: str | None = None, start: int | None = None,
                      cap: int = MAX_WINDOW, chart_type: str = "candles", projection: dict | None = None,
                      whole: bool = False, latest_rows: int | None = None) -> Feed | ProjectedFeed:
        """``window_of``: a chart's own window, reaching back as far as the standard one does when it
        starts. ``start``: a window that reaches back to that time once Market's is whole (``cap`` bars
        at most), for request.security. ``whole``: returns once the window is whole (raises Market's
        problem when it fails the series first); otherwise at once, Market's newest page coming first."""
        feed = await self.take(instrument, series, window_of, start, cap, chart_type, projection)
        if whole or latest_rows is not None:
            try:
                await feed.raw.wait_whole(latest_rows=latest_rows)
            except BaseException:
                await self.release(feed)
                raise
        return feed

    async def take(self, instrument: dict, series: dict, window_of: str | None, start: int | None, cap: int,
                   chart_type: str, projection: dict | None) -> Feed | ProjectedFeed:
        """The feed with one more user; one being opened when its opener is cancelled is not kept."""
        key = series["seriesId"] + (f"@{window_of}" if window_of else "") + (f"@from{start}" if start is not None else "")
        if chart_type in ("heikin_ashi", "renko", "kagi", "point_figure", "line_break", "range"):
            base = await self.take(instrument, series, window_of, None, MAX_WINDOW, "candles", None)
            projected_key = f"{key}|{chart_type}|" + ",".join(f"{k}={projection[k]}" for k in sorted(projection))
            try:
                async with self.locks.setdefault(projected_key, asyncio.Lock()):
                    feed = self.feeds.get(projected_key)
                    if feed is None:
                        await base.ready.wait()
                        feed = self.feeds[projected_key] = ProjectedFeed(self, projected_key, base, chart_type,
                                                                         projection)
                        await feed.open()  # holds the base from now on
                        feed.users += 1
                        return feed
                    feed.users += 1
            except BaseException:
                await self.release(base)
                raise
            await self.release(base)  # the projected feed holds it already
            return feed
        async with self.locks.setdefault(key, asyncio.Lock()):
            feed = self.feeds.get(key)
            if feed is None:
                feed = Feed(self, key, instrument, series, None, cap, reach=start)
                if window_of:
                    standard = self.feeds.get(series["seriesId"])
                    if standard is not None and standard.ready.is_set():
                        feed.start = standard.columns["time"][0] if standard.columns["time"] else None
                try:
                    await feed.open()
                except BaseException:
                    await feed.close()
                    raise
                self.feeds[key] = feed
            feed.users += 1
            return feed

    async def release(self, feed: Feed | ProjectedFeed) -> None:
        feed.users -= 1
        if feed.users == 0 and self.feeds.get(feed.key) is feed:
            del self.feeds[feed.key]
            await feed.close()

    async def close(self) -> None:
        for feed in list(self.feeds.values()):
            if isinstance(feed, Feed):
                await feed.close()
        self.feeds.clear()
