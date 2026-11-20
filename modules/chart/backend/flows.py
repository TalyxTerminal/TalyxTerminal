"""One chart's data flow to the frontend (``chart.chart`` stream; ).

The flow follows the chart's document: it reads the feed of the chart's series (projected
for Heikin Ashi and brick types, its own window once the user scrolled back), the chart's
indicator instances and comparisons, and puts every change on the stream in order. During a
replay (``replay.py``) the stream carries the replay's bars and indicator rows (``replay``
true) instead of the live ones; stopping sends the live state again. Indicators that read the
chart's visible range run on the range the chart last sent (``set_view``).
"""
from __future__ import annotations

import asyncio
import logging
from bisect import bisect_right

from talyx_sdk import ContractError

from .charts import Charts, Entry
from .document import BRICK_TYPES, ChartDocument, drawing_group
from .feeds import Feed, Hub, ProjectedFeed
from .marketdata import now_ms
from .pine import visible
from .pine.indicators import ALERT_CONDITION, Indicators
from .replay import Replay
from .streams import Outbox

PROJECTED = ("heikin_ashi", *BRICK_TYPES)
DEFAULT_VIEW = {"bars": 200}  # a chart opens on about its newest 200 bars; it sends its own range at once
LOG = logging.getLogger(__name__)


def series_spec(document: ChartDocument) -> tuple:
    """What the chart's feed is: its series, chart type and projection."""
    return (document.series["seriesId"], document.chartType,
            document.projection.model_dump_json() if document.chartType in PROJECTED else None)


def bars_item(feed, op: str, start: int, end: int | None = None, replay: bool = False) -> dict:
    columns = feed.columns
    item = {"type": "bars", "op": op, "replay": replay,
            **{name: columns[name][start:end] for name in ("time", "closeTime", "open", "high", "low", "close",
                                                           "volume")}}
    if feed.projected:
        item["sourceTime"] = columns["sourceTime"][start:end]
    if op == "splice":
        item["start"] = start
    if op == "replace":
        item.update(priceFormat=feed.price_format, hasMoreBefore=feed.has_more_before,
                    limitReached=feed.limit_reached)
        if feed.projected:
            item["boxSize"] = feed.spec.box_size
    return item


class IndicatorBinding:
    def __init__(self, flow: ChartFlow, indicator_id: str, ref: dict, params: dict, replay: bool = False):
        self.flow, self.id, self.ref, self.params, self.replay = flow, indicator_id, ref, params, replay
        self.instance = None
        self.feed = None
        self.view: dict | None = None  # the visible range it runs on, for a script that reads it

    def __call__(self, message: dict) -> None:
        if not self.replay and self.flow.replay is not None:
            return  # the replay's rows are on the stream; stopping it sends these again
        if not self.live():
            return
        if message["op"] == "error":
            item = {"type": "indicator_error", "instanceId": self.id, "code": message["code"],
                    "message": message["message"]}
            if message.get("line"):
                item["line"] = message["line"]
            self.flow.out.put(item)
            return
        item = {"type": "indicator", "instanceId": self.id, "op": message["op"], "replay": self.replay,
                "time": message["time"], "colors": message["colors"],
                "values": {key: value for key, value in message["values"].items()
                           if not key.startswith(ALERT_CONDITION)}}  # alertcondition values are for alerts
        for key in ("meta", "behindChart", "noScale", "drawings", "drawingChanges"):
            if key in message:
                item[key] = message[key]
        self.flow.out.put(item)

    def live(self) -> bool:
        """Still the flow's binding, on the feed the stream shows: a result that comes after an unbind or a switch is
        another series'."""
        flow = self.flow
        bindings, feed = ((flow.replay_bindings, flow.replay.feed if flow.replay is not None else None) if self.replay
                          else (flow.bindings, flow.feed))
        return flow.current and bindings.get(self.id) is self and self.feed is feed

    async def bind(self, feed) -> None:
        self.feed = feed
        indicators, params = self.flow.indicators, self.params
        self.view = self.flow.view if indicators.catalog.reads_view(indicators.catalog.definition(self.ref)) else None
        if self.view is not None:
            params = {**params, visible.VIEW: self.view}
        self.instance = await indicators.attach(feed, self.ref, params, self)

    def unbind(self) -> None:
        if self.instance is not None:
            self.flow.indicators.detach(self.feed, self.instance, self)
            self.instance = None

    def resend(self) -> None:
        """The instance's current output again (after a replay)."""
        instance = self.instance
        if instance is not None and (instance.output is not None or instance.error is not None):
            self(instance.replace() if instance.output is not None else instance.error)


class ComparisonBinding:
    """A comparison symbol's closes at the main bars' times (the last bar opened at or before)."""

    def __init__(self, flow: ChartFlow, comparison: dict):
        self.flow, self.id, self.comparison = flow, comparison["id"], comparison
        self.feed: Feed | None = None

    def values(self, times: list[int]) -> list[float | None]:
        own = self.feed.columns
        result = []
        for time in times:
            index = bisect_right(own["time"], time) - 1
            result.append(own["close"][index] if index >= 0 else None)
        return result

    def forming_value(self, time: int) -> float | None:
        forming = self.feed.forming
        if forming is not None and forming["time"] <= time:
            return forming["close"]
        return self.values([time])[0]

    def replace(self) -> None:
        if not self.flow.current or self.feed is None or not self.feed.ready.is_set() or not self.flow.shown.ready.is_set():
            return
        main = self.flow.shown.columns
        self.flow.out.put({"type": "comparison", "comparisonId": self.id, "op": "replace", "time": main["time"],
                           "value": self.values(main["time"]), "priceFormat": self.feed.price_format})

    def main_appended(self, start: int) -> None:
        if not self.flow.current or self.feed is None or not self.feed.ready.is_set():
            return
        times = self.flow.shown.columns["time"][start:]
        self.flow.out.put({"type": "comparison", "comparisonId": self.id, "op": "append", "time": times,
                           "value": self.values(times)})

    def latest(self) -> None:
        if not self.flow.current:
            return
        main = self.flow.shown
        time = main.forming["time"] if not main.projected and main.forming else \
            (main.columns["time"][-1] if main.columns["time"] else None)
        if time is not None and self.feed.ready.is_set():
            self.flow.out.put({"type": "comparison", "comparisonId": self.id, "op": "update", "time": [time],
                               "value": [self.forming_value(time)]})

    # listener on the comparison's own feed
    def on_replaced(self, feed) -> None:
        self.replace()

    def on_appended(self, feed, count: int) -> None:
        self.replace()

    def on_forming(self, feed) -> None:
        self.latest()

    def on_spliced(self, feed, start: int) -> None:
        pass

    def on_status(self, feed) -> None:
        pass

    def on_format(self, feed) -> None:
        self.replace()

    async def bind(self) -> None:
        self.feed = await self.flow.hub.acquire(self.comparison["instrument"], self.comparison["series"])
        self.feed.listeners.append(self)
        self.replace()

    async def unbind(self) -> None:
        if self.feed is not None:
            self.feed.listeners.remove(self)
            await self.flow.hub.release(self.feed)
            self.feed = None


class ChartFlow:
    def __init__(self, charts: Charts, hub: Hub, indicators: Indicators, workspace: str, chart_id: str):
        self.charts, self.hub, self.indicators = charts, hub, indicators
        self.workspace, self.chart_id = workspace, chart_id
        self.out = Outbox()
        self.feed: Feed | ProjectedFeed | None = None
        self.feed_spec: tuple | None = None
        # The spec of the newest document on the stream. Its document goes out at once and its feed follows, so until
        # the feed is that spec's, what the old feed sends is another series' and stays off the stream.
        self.wanted_spec: tuple | None = None
        self.extended = False
        self.bindings: dict[str, IndicatorBinding] = {}
        self.comparisons: dict[str, ComparisonBinding] = {}
        self.lock = asyncio.Lock()
        self.unlisten = None
        self.document: ChartDocument | None = None
        self.following: asyncio.Task | None = None
        self.replay: Replay | None = None
        self.replay_bindings: dict[str, IndicatorBinding] = {}
        self.view: dict = DEFAULT_VIEW
        self.viewing: asyncio.Task | None = None

    @property
    def current(self) -> bool:
        return self.feed is not None and self.feed_spec == self.wanted_spec

    @property
    def shown(self):
        """The feed whose bars are on the stream: the replay's while replaying."""
        return self.replay.feed if self.replay is not None else self.feed

    def start(self) -> None:
        """The document goes out at once; bars, indicators and comparisons follow as they arrive."""
        entry = self.charts.entry(self.workspace, self.chart_id)
        self.wanted_spec = series_spec(entry.document)
        self.out.put(self.document_item(entry))
        self.unlisten = self.charts.listen(self.chart_id, self.document_changed)
        self.following = self.spawn(self.follow(entry.document))

    def document_item(self, entry: Entry) -> dict:
        return {"type": "document", "revision": entry.revision,
                "document": entry.document.for_view(),
                "drawingGroup": drawing_group(entry.document), "canUndo": bool(entry.undo),
                "canRedo": bool(entry.redo)}

    def document_changed(self, entry: Entry, previous: ChartDocument) -> None:
        self.wanted_spec = series_spec(entry.document)
        self.out.put(self.document_item(entry))
        self.following = self.spawn(self.follow(entry.document))

    def spawn(self, work) -> asyncio.Task:
        """Following runs beside the stream; a failure shows as the series' ``failed`` status."""
        async def run():
            try:
                await work
            except ContractError as error:
                self.out.put({"type": "status", "state": "failed", "since": now_ms(),
                              "problem": {"code": error.code, "message": error.message}})
            except Exception as error:  # noqa: BLE001 -- the reader must hear why nothing comes
                LOG.exception("Chart %s could not follow its document", self.chart_id)
                self.out.put({"type": "status", "state": "failed", "since": now_ms(),
                              "problem": {"code": "unavailable", "message": str(error)}})
        return asyncio.create_task(run())

    # ── following the document ──

    async def follow(self, document: ChartDocument) -> None:
        async with self.lock:
            self.document = document
            spec = series_spec(document)
            if spec != self.feed_spec:
                self.end_replay(announce=True)  # another series or chart type ends the replay
                if spec[0] != (self.feed_spec or (None,))[0]:
                    self.extended = False  # a new series starts from the shared window
                await self.switch_feed(document, spec)
            await self.follow_indicators(document)
            if self.replay is not None:
                await self.follow_indicators(document, replay=True)
            await self.follow_comparisons(document)

    async def switch_feed(self, document: ChartDocument, spec: tuple) -> None:
        """The old feed is let go of whether or not the new one opens: a failed switch leaves no feed,
        and the next document switches from none. It is released after the new one is held, which
        may share its raw feed."""
        old, self.feed, self.feed_spec = self.feed, None, None
        for binding in self.bindings.values():
            binding.unbind()
        self.bindings.clear()
        if old is not None:
            old.listeners.remove(self)
        try:
            self.feed = await self.hub.acquire(
                document.instrument, document.series, window_of=self.chart_id if self.extended else None,
                chart_type=document.chartType,
                projection=document.projection.model_dump() if document.chartType in PROJECTED else None)
        finally:
            if old is not None:
                await self.hub.release(old)
        self.feed_spec = spec
        self.feed.listeners.append(self)
        if self.feed.ready.is_set():
            self.on_replaced(self.feed)
        if self.feed.status is not None:
            self.on_status(self.feed)
        base = self.feed.raw
        await base.retry()  # opening a chart on a failed series is the user's retry

    async def follow_indicators(self, document: ChartDocument, replay: bool = False) -> None:
        bindings, feed = (self.replay_bindings, self.replay.feed) if replay else (self.bindings, self.feed)
        wanted = {item.id: (item.definition.model_dump(), item.params) for item in document.indicators}
        for indicator_id in [key for key, binding in bindings.items()
                             if wanted.get(key) != (binding.ref, binding.params)]:
            bindings.pop(indicator_id).unbind()
        for indicator_id, (ref, params) in wanted.items():
            if indicator_id not in bindings:
                binding = bindings[indicator_id] = IndicatorBinding(self, indicator_id, ref, params, replay)
                try:
                    await binding.bind(feed)
                except ContractError as error:  # a definition that is gone fails only itself
                    self.out.put({"type": "indicator_error", "instanceId": indicator_id, "code": error.code,
                                  "message": error.message})

    async def follow_comparisons(self, document: ChartDocument) -> None:
        wanted = {item.id: item.model_dump() for item in document.comparisons}
        for comparison_id in [key for key, binding in self.comparisons.items()
                              if key not in wanted or wanted[key]["series"] != binding.comparison["series"]]:
            await self.comparisons.pop(comparison_id).unbind()
        for comparison_id, comparison in wanted.items():
            if comparison_id not in self.comparisons:
                binding = ComparisonBinding(self, comparison)
                await binding.bind()
                self.comparisons[comparison_id] = binding

    # ── replay ──

    async def start_replay(self, at: int) -> None:
        async with self.lock:
            if not self.current:
                raise ContractError("unavailable", "The chart has no current feed")
            base = self.feed.raw
            await base.wait_whole(latest_rows=1)
            if not base.columns["time"]:
                raise ContractError("unavailable", "There are no closed bars to replay")
            if at < base.columns["time"][0]:
                raise ContractError("invalid_input", "Backfill the chart to the replay date first")
            self.end_replay()
            document = self.document
            replay = Replay(base, at, document.chartType,
                            document.projection.model_dump() if document.chartType in PROJECTED else None)
            await replay.open()
            replay.feed.listeners.append(ReplayListener(self))
            self.replay = replay
            self.out.put(bars_item(replay.feed, "replace", 0, replay=True))
            self.out.put(self.replay_item())
            for binding in self.comparisons.values():
                binding.replace()
            await self.follow_indicators(document, replay=True)

    async def step_replay(self, count: int) -> None:
        async with self.lock:
            if self.replay is None:
                raise ContractError("conflict", "The chart is not replaying")
            self.replay.raw.step(count)
            self.out.put(self.replay_item())

    async def stop_replay(self) -> None:
        async with self.lock:
            if self.replay is not None:
                self.end_replay(announce=True)
                if self.feed.ready.is_set():
                    self.on_replaced(self.feed)
                for binding in self.bindings.values():
                    binding.resend()

    def end_replay(self, announce: bool = False) -> None:
        for binding in self.replay_bindings.values():
            binding.unbind()
        self.replay_bindings.clear()
        if self.replay is not None:
            self.replay.close()
            self.replay = None
            if announce:
                self.out.put(self.replay_item())

    def replay_item(self) -> dict:
        if self.replay is None:
            return {"type": "replay", "active": False, "at": None, "end": False}
        raw = self.replay.raw
        return {"type": "replay", "active": True, "at": raw.columns["time"][-1],
                "end": raw.shown == len(raw.all["time"])}

    # ── scrolling back ──

    def set_view(self, view: dict) -> None:
        """The chart's visible range: ``{"left", "right"}`` bar times, or ``{"bars"}``, the newest bars.
        The indicators that read it start again on it; while the chart is dragged only the newest range
        runs (an instance on an older one is stopped when the next starts)."""
        self.view = view
        if self.viewing is None or self.viewing.done():
            self.viewing = self.spawn(self.follow_view())

    async def follow_view(self) -> None:
        async with self.lock:
            while True:
                view = self.view
                for binding in [*self.bindings.values(), *self.replay_bindings.values()]:
                    if binding.view is not None and binding.view != view and binding.instance is not None:
                        binding.unbind()
                        await binding.bind(binding.feed)
                if self.view == view:
                    return

    async def backfill(self, to: int | None, bars: int | None) -> dict:
        async with self.lock:
            if not self.current:
                raise ContractError("unavailable", "The chart has no current feed")
            base = self.feed.raw
            await base.wait_whole(latest_rows=1)
            if not base.has_more_before or base.limit_reached:  # nothing older: no window of its own, no resend
                return {"loaded": 0, "reachedStart": not base.has_more_before, "limitReached": base.limit_reached}
            if not self.extended:
                self.extended = True
                await self.switch_feed(self.document, self.feed_spec)
                await self.follow_indicators(self.document)
            base = self.feed.raw
            await base.wait_whole(latest_rows=1)
            return await base.backfill(to=to, bars=bars)

    # ── feed events ──

    def on_replaced(self, feed) -> None:
        if self.replay is not None or not self.current:
            return
        self.out.put(bars_item(feed, "replace", 0))
        for binding in self.comparisons.values():
            binding.replace()
        self.on_forming(feed)  # the window's forming bar goes out before rows computed from it

    def on_appended(self, feed, count: int) -> None:
        if self.replay is not None or not self.current:
            return
        start = len(feed.columns["time"]) - count
        self.out.put(bars_item(feed, "append", start))
        for binding in self.comparisons.values():
            binding.main_appended(start)

    def on_spliced(self, feed, start: int) -> None:
        if self.replay is None and self.current:
            self.out.put(bars_item(feed, "splice", start))

    def on_forming(self, feed) -> None:
        if self.replay is None and self.current and not feed.projected and feed.forming is not None:
            self.out.put({"type": "update", "replay": False, "bar": feed.forming})
            for binding in self.comparisons.values():
                binding.latest()

    def on_status(self, feed) -> None:
        if self.current:
            self.out.put(feed.status)

    def on_format(self, feed) -> None:
        if self.current:
            self.out.put({"type": "price_format", "priceFormat": feed.price_format})

    async def close(self) -> None:
        if self.following is not None:
            self.following.cancel()
        async with self.lock:
            if self.unlisten is not None:
                self.unlisten()
            self.end_replay()
            for binding in self.bindings.values():
                binding.unbind()
            for binding in self.comparisons.values():
                await binding.unbind()
            if self.feed is not None:
                self.feed.listeners.remove(self)
                await self.hub.release(self.feed)


class ReplayListener:
    """The replay feed's bars on the stream, marked as replay."""

    def __init__(self, flow: ChartFlow):
        self.flow = flow

    def on_replaced(self, feed) -> None:
        if self.flow.current:
            self.flow.out.put(bars_item(feed, "replace", 0, replay=True))

    def on_appended(self, feed, count: int) -> None:
        if not self.flow.current:
            return
        start = len(feed.columns["time"]) - count
        self.flow.out.put(bars_item(feed, "append", start, replay=True))
        for binding in self.flow.comparisons.values():
            binding.main_appended(start)

    def on_spliced(self, feed, start: int) -> None:
        if self.flow.current:
            self.flow.out.put(bars_item(feed, "splice", start, replay=True))

    def on_forming(self, feed) -> None:
        pass

    def on_status(self, feed) -> None:
        pass

    def on_format(self, feed) -> None:
        pass
