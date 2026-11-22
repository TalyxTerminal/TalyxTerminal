"""Alerts: what they watch, when they fire, and telling the user.

An alert watches one series, or every member of a watchlist on one timeframe, while its
workspace is open. Every watched series is one hub feed shared with charts; each (alert,
member) has a ``Rule`` fed with observations: the feed's bars for price and value operands,
or the indicator instances' rows when the alert uses indicators (they follow the bars).
State lives in memory and is written when it changes; a fire is written ("this bar fired")
before the notification goes out, so a restart never repeats it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from bisect import bisect_left
from collections.abc import Callable

from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from .alert_drawings import ALERT_TOOLS, CHANNEL_TOOLS, furthest, geometry, prices
from .alert_rules import CHANNEL_OPS, MOVING_OPS, Rule, Values
from .charts import Charts
from .feeds import Feed, Hub
from .pine.indicators import ALERT_CONDITION, Indicators, Pins
from .store import Store
from .timemap import TimeMap
from .watchlists import Watchlists

LOG = logging.getLogger(__name__)
KEEP_FIRED_MS = 90 * 24 * 3_600_000
MAX_INDICATOR_MEMBERS = 50
OP_WORDS = {"crossing": "Crossing", "crossing_up": "Crossing Up", "crossing_down": "Crossing Down",
            "greater": "Greater Than", "less": "Less Than", "entering_channel": "Entering Channel",
            "exiting_channel": "Exiting Channel", "inside_channel": "Inside Channel",
            "outside_channel": "Outside Channel", "moving_up": "Moving Up", "moving_down": "Moving Down",
            "moving_up_percent": "Moving Up %", "moving_down_percent": "Moving Down %", "triggered": "Triggered"}


def now_ms() -> int:
    return int(time.time() * 1000)


def tap_key(operand: dict) -> str:
    return json.dumps([operand["definition"], operand.get("params", {})], sort_keys=True)


def drawing_key(operand: dict) -> str:
    return f"{operand['chartId']}/{operand['drawingId']}"


def operands(condition: dict) -> list[dict]:
    return [condition[side] for side in ("left", "right", "right2") if condition.get(side)]


def price(bar: dict, field: str) -> float | None:
    if field == "hl2":
        return (bar["high"] + bar["low"]) / 2
    if field == "hlc3":
        return (bar["high"] + bar["low"] + bar["close"]) / 3
    if field == "ohlc4":
        return (bar["open"] + bar["high"] + bar["low"] + bar["close"]) / 4
    return bar[field]


def check_spec(spec: dict, watchlists: Watchlists, workspace: str) -> None:
    """What the wire model cannot say about a spec."""
    for condition in spec["conditions"]:
        op, kinds = condition["op"], [item["kind"] for item in operands(condition)]
        if (op == "triggered") != (kinds == ["alertcondition"]):
            raise ContractError("invalid_input", t("Triggered takes an alertcondition and nothing else; an alertcondition goes with Triggered"))
        if "drawing" in kinds and spec["target"]["kind"] == "watchlist":
            raise ContractError("invalid_input", t("A drawing belongs to one symbol; watchlist alerts use price and indicator conditions"))
        if op in MOVING_OPS:
            if condition.get("amount") is None or condition.get("bars") is None or condition.get("right"):
                raise ContractError("invalid_input", t("{value} takes one operand, an amount and bars", value=OP_WORDS[op]))
        elif op in CHANNEL_OPS:
            channel = (condition.get("right") or {}).get("kind") == "drawing" and not condition.get("right2")
            if not (channel or condition.get("right") and condition.get("right2")):
                raise ContractError("invalid_input", t("{value} takes a channel drawing or the two bounds", value=OP_WORDS[op]))
        elif op != "triggered" and not condition.get("right"):
            raise ContractError("invalid_input", t("{value} takes two operands", value=OP_WORDS[op]))
    target = spec["target"]
    if target["kind"] == "series":
        if target["series"].get("adjustmentAnchor") == "earliest":
            raise ContractError("invalid_input", t("Back-adjusted prices change with corporate actions; price and indicator alerts are not offered on them"))
    else:
        watchlists.get(workspace, target["watchlistId"])


def operand_name(operand: dict | None) -> str:
    if operand is None:
        return ""
    if operand["kind"] == "value":
        return f"{operand['value']:g}"
    if operand["kind"] == "price":
        return operand["field"].capitalize()
    if operand["kind"] == "drawing":
        return "Drawing"
    if operand["kind"] == "alertcondition":
        return operand["condition"]
    return f"{operand['definition']['id']} {operand['output']}"


def default_message(spec: dict, symbol: str) -> str:
    name = operand_name
    parts = []
    for condition in spec["conditions"]:
        words = [name(condition["left"]), OP_WORDS[condition["op"]], name(condition.get("right")),
                 name(condition.get("right2"))]
        if condition["op"] in MOVING_OPS:
            words += [f"{condition['amount']:g}", f"in {condition['bars']} bars"]
        parts.append(" ".join(word for word in words if word))
    return f"{symbol}: " + ", ".join(parts)


class Tap:
    """An indicator instance's rows as an alert reads them: closed rows and the forming one."""

    def __init__(self, watching: Watching, operand: dict):
        self.watching, self.operand = watching, operand
        self.times: list[int] = []
        self.values: dict[str, list] = {}
        self.forming: tuple[int, dict] | None = None
        self.instance = None
        self.definition = None  # the pinned code it runs
        self.ready = False

    def __call__(self, message: dict) -> None:
        op = message["op"]
        if op == "error":
            self.ready = False
            return
        if op == "replace":
            self.times = list(message["time"])
            self.values = {key: list(value) for key, value in message["values"].items()}
            self.forming, self.ready = None, True
            self.watching.rebase()
            return
        if not self.ready:
            return
        if op == "update":
            self.forming = (message["time"][0], {key: value[0] for key, value in message["values"].items()})
            self.watching.observe(message["time"][0], False)
            return
        count = len(self.times)
        self.times.extend(message["time"])
        for key in self.values.keys() | message["values"].keys():
            self.values.setdefault(key, [None] * count).extend(message["values"].get(key, [None] * len(message["time"])))
        self.forming = None
        for bar_time in message["time"]:
            self.watching.observe(bar_time, True)

    def value(self, output: str, bar_time: int, closed: bool) -> float | None | bool:
        """The output at this bar; False when this instance has no row for it yet."""
        if closed:
            index = bisect_left(self.times, bar_time)
            return self.values.get(output, [])[index] if index < len(self.times) and self.times[index] == bar_time \
                else False
        if self.forming is None or self.forming[0] != bar_time:
            return False
        return self.forming[1].get(output)


class Watching:
    """One alert on one series (a watchlist alert has one per member)."""

    def __init__(self, alerts: Alerts, alert: dict, member: dict, watch: SeriesWatch, fired_bar: int | None,
                 shapes: dict[str, dict]):
        self.alerts, self.alert_id, self.member, self.watch = alerts, alert["id"], member, watch
        self.shapes = shapes  # the alert's copies of its drawings, by "chartId/drawingId"
        self.conditions = alert["conditions"]
        self.rule = Rule([{key: item.get(key) for key in ("op", "amount", "bars")} for item in self.conditions],
                         alert["frequency"], fired_bar=fired_bar)
        self.taps: dict[str, Tap] = {}
        for condition in self.conditions:
            for operand in operands(condition):
                if operand["kind"] in ("indicator", "alertcondition") and tap_key(operand) not in self.taps:
                    self.taps[tap_key(operand)] = Tap(self, operand)

    def operand_value(self, operand: dict, bar: dict | None, bar_time: int, closed: bool):
        kind = operand["kind"]
        if kind == "value":
            return operand["value"]
        if kind == "price":
            return price(bar, operand["field"]) if bar is not None else False
        if kind == "drawing":
            return prices(self.shapes[drawing_key(operand)], self.watch.timemap(), bar_time)
        if kind == "alertcondition":
            return self.taps[tap_key(operand)].value(ALERT_CONDITION + operand["condition"], bar_time, closed)
        return self.taps[tap_key(operand)].value(operand["output"], bar_time, closed)

    def values(self, bar_time: int, closed: bool) -> list[Values] | None:
        """Every condition's operands at this bar; None while one has no value there yet."""
        bar = self.watch.bar(bar_time, closed)
        values = []
        for condition in self.conditions:
            found = [self.operand_value(condition[side], bar, bar_time, closed) if condition.get(side) else None
                     for side in ("left", "right", "right2")]
            if any(value is False for value in found):
                return None
            if isinstance(found[1], list):  # a drawing: a line's price, or a channel's two bounds
                found[1:] = found[1] if len(found[1]) == 2 else [found[1][0], found[2]]
            found = [value[0] if isinstance(value, list) else value for value in found]
            values.append(Values(*found))
        return values

    def furthest(self) -> int | None:
        return max((furthest(shape) for shape in self.shapes.values()), default=None)

    def observe(self, bar_time: int, closed: bool) -> None:
        if any(not tap.ready for tap in self.taps.values()):
            return
        values = self.values(bar_time, closed)
        if values is None:
            return  # an operand's row for this bar comes next
        if self.rule.observe(bar_time, closed, values, self.watch.feed.realtime, now_ms()):
            self.alerts.fire(self, bar_time, values[0].left)

    def from_bars(self) -> bool:
        return not self.taps

    def rebase(self) -> None:
        if any(not tap.ready for tap in self.taps.values()):
            return
        lefts = []
        for condition in self.conditions:
            left = condition["left"]
            if condition["op"] not in MOVING_OPS:
                lefts.append([])
            elif left["kind"] == "price":
                columns = self.watch.feed.columns
                lefts.append([price({name: columns[name][index] for name in ("open", "high", "low", "close", "volume")},
                                    left["field"]) for index in range(len(columns["time"]))])
            elif left["kind"] == "indicator":
                lefts.append(self.taps[tap_key(left)].values.get(left["output"], []))
            else:
                lefts.append([left["value"]])
        times = self.watch.feed.columns["time"]
        self.rule.rebase(lefts, self.values(times[-1], True) if times else None)


class SeriesWatch:
    """A watched series: one hub feed, the alerts on it."""

    def __init__(self, alerts: Alerts, feed: Feed):
        self.alerts, self.feed = alerts, feed
        self.watchings: list[Watching] = []
        self.future: tuple[list[int], list[int]] = ([], [])  # Market's bar times past the window (drawings)
        self.covered: int | None = None
        feed.listeners.append(self)

    async def ensure_future(self, until: int) -> None:
        """Bar times up to ``until`` past the last closed bar, from Market's calendar."""
        await self.feed.ready.wait()
        if self.future[0] and self.future[0][-1] >= until:
            return
        closes = self.feed.columns["closeTime"]
        start = closes[-1] if closes else now_ms()
        opens, ends, self.covered = await self.alerts.hub.market.bar_times(
            self.feed.instrument, self.feed.series, start, until + 1)
        self.future = (opens, ends)

    def timemap(self) -> TimeMap:
        columns = self.feed.columns
        last = columns["time"][-1] if columns["time"] else -1
        opens, ends = self.future
        keep = [index for index, time in enumerate(opens) if time > last]
        future, future_close = [opens[index] for index in keep], [ends[index] for index in keep]
        forming = self.feed.forming
        if forming is not None and forming["time"] > last and (not future or future[0] != forming["time"]):
            future, future_close = [forming["time"], *future], [forming["closeTime"], *future_close]
        return TimeMap(columns["time"], columns["closeTime"], future, future_close, self.covered)

    def bar(self, bar_time: int, closed: bool) -> dict | None:
        if not closed:
            forming = self.feed.forming
            return forming if forming is not None and forming["time"] == bar_time else None
        times = self.feed.columns["time"]
        index = bisect_left(times, bar_time)
        if index == len(times) or times[index] != bar_time:
            return None
        return {name: self.feed.columns[name][index] for name in ("time", "open", "high", "low", "close", "volume")}

    def on_replaced(self, feed) -> None:
        for watching in list(self.watchings):
            if watching.from_bars():
                watching.rebase()

    def on_appended(self, feed, count: int) -> None:
        times = feed.columns["time"][-count:]
        for watching in list(self.watchings):
            if watching.from_bars():
                for bar_time in times:
                    watching.observe(bar_time, True)

    def on_forming(self, feed) -> None:
        if feed.forming is not None:
            for watching in list(self.watchings):
                if watching.from_bars():
                    watching.observe(feed.forming["time"], False)

    def on_spliced(self, feed, start: int) -> None:
        pass

    def on_format(self, feed) -> None:
        pass

    def on_status(self, feed) -> None:
        self.alerts.source_changed(self)

    def close(self) -> None:
        self.feed.listeners.remove(self)


class Notifier:
    """The platform's notifications, per channel of an alert."""

    def __init__(self, ctx):
        self.ctx = ctx

    async def deliver(self, channels: dict, title: str, body: str, notification_id: str) -> dict[str, str]:
        delivered = {}
        for channel, send in (("toast", lambda: self.ctx.call("platform.notifications.toast",
                                                               {"title": title, "message": body})),
                              ("sound", self.sound),
                              ("mobile", lambda: self.mobile(title, body, notification_id))):
            if channels.get(channel):
                try:
                    await send()
                    delivered[channel] = "sent"
                except (ContractError, RuntimeError) as error:  # the log shows which channel failed
                    LOG.warning("Alert notification by %s failed: %s", channel, error)
                    delivered[channel] = "failed"
        return delivered

    @staticmethod
    async def sound() -> None:
        """The alert sound, played by Chart's own process on this computer."""
        import winsound
        await asyncio.to_thread(winsound.MessageBeep, winsound.MB_ICONASTERISK)

    async def mobile(self, title: str, body: str, notification_id: str) -> None:
        channels = [item["id"] for item in await self.ctx.call("platform.notifications.channels", {})
                    if item["enabled"] and item["configured"]]
        if not channels:
            raise RuntimeError("No mobile notification channel is set up")
        await self.ctx.call("platform.notifications.send", {"notificationId": notification_id,
                                                            "channelIds": channels, "title": title, "body": body})


class Alerts:
    def __init__(self, store: Store, hub: Hub, indicators: Indicators, pins: Pins, watchlists: Watchlists,
                 charts: Charts, publish: Callable[[str, dict], None], notifier: Notifier):
        self.store, self.hub, self.indicators, self.watchlists = store, hub, indicators, watchlists
        self.pins = pins
        self.charts = charts
        charts.changed.append(self.chart_changed)
        self.publish, self.notifier = publish, notifier
        self.open: set[str] = set()
        self.records: dict[str, tuple[str, dict]] = {}  # alert id → (workspace, alert), loaded lazily per workspace
        self.loaded: set[str] = set()
        self.watches: dict[str, SeriesWatch] = {}
        self.running: dict[str, list[Watching]] = {}
        self.starting: dict[str, asyncio.Task] = {}
        self.expiry: dict[str, asyncio.TimerHandle] = {}
        self.sending: set[asyncio.Task] = set()
        watchlists.changed.append(self.watchlist_changed)
        store.prune_fired(now_ms() - KEEP_FIRED_MS)

    # ── records ──

    def load(self, workspace: str) -> None:
        if workspace not in self.loaded:
            for record in self.store.objects("alert", workspace):
                self.records[record["id"]] = (workspace, record["value"])
            self.loaded.add(workspace)

    def all(self, workspace: str) -> list[dict]:
        self.load(workspace)
        return [alert for owner, alert in self.records.values() if owner == workspace]

    def get(self, workspace: str, alert_id: str) -> dict:
        self.load(workspace)
        owner, alert = self.records.get(alert_id, (None, None))
        if owner != workspace:
            raise ContractError("not_found", t("No alert {alert_id} in this workspace", alert_id=alert_id))
        return alert

    def put(self, workspace: str, alert: dict) -> dict:
        self.records[alert["id"]] = (workspace, alert)
        self.store.put_object("alert", {"id": alert["id"], "value": alert}, workspace)
        self.publish(workspace, {"type": "alert", "value": alert})
        return alert

    def checked_revision(self, workspace: str, alert_id: str, revision: int) -> dict:
        alert = self.get(workspace, alert_id)
        if revision != alert["revision"]:
            raise ContractError("conflict", t("The alert changed meanwhile"), {"revision": alert["revision"]})
        return alert

    # ── routes ──

    def create(self, workspace: str, spec: dict) -> dict:
        check_spec(spec, self.watchlists, workspace)
        shapes = self.capture(workspace, spec)
        self.load(workspace)
        alert_id = "al-" + uuid.uuid4().hex[:12]
        self.store.put_object("alert_drawings", {"id": alert_id, "shapes": shapes}, workspace)
        self.store.put_object("alert_pins", {"id": alert_id, "pins": self.pin(spec)}, workspace)
        alert = self.put(workspace, {**spec, "id": alert_id, "revision": 1, "state": "active",
                                     "reason": None, "source": None, "outdated": False, "createdAt": now_ms(),
                                     "lastFiredAt": None, "fireCount": 0})
        self.follow(workspace, alert)
        return alert

    def update(self, workspace: str, alert_id: str, revision: int, spec: dict) -> dict:
        current = self.checked_revision(workspace, alert_id, revision)
        check_spec(spec, self.watchlists, workspace)
        shapes = self.capture(workspace, spec)
        self.stop(alert_id)
        self.store.delete_object("alert_fired_bar", alert_id)
        self.store.put_object("alert_drawings", {"id": alert_id, "shapes": shapes}, workspace)
        self.store.put_object("alert_pins", {"id": alert_id, "pins": self.pin(spec)}, workspace)
        alert = self.put(workspace, {**current, **spec, "revision": revision + 1, "state": "active", "reason": None,
                                     "source": None, "outdated": False})
        self.follow(workspace, alert)
        return alert

    def set_state(self, workspace: str, alert_id: str, revision: int, state: str) -> dict:
        current = self.checked_revision(workspace, alert_id, revision)
        self.stop(alert_id)
        alert = self.put(workspace, {**current, "revision": revision + 1, "state": state, "reason": None,
                                     "source": None})
        self.follow(workspace, alert, retry=state == "active")
        return alert

    def delete(self, workspace: str, alert_id: str) -> None:
        self.get(workspace, alert_id)
        self.stop(alert_id)
        del self.records[alert_id]
        self.store.delete_object("alert", alert_id)
        self.store.delete_object("alert_fired_bar", alert_id)
        self.store.delete_object("alert_drawings", alert_id)
        self.store.delete_object("alert_pins", alert_id)
        self.publish(workspace, {"type": "alert_removed", "id": alert_id})

    def log(self, workspace: str, alert_id: str | None, before: int | None, limit: int) -> dict:
        events = self.store.fired(workspace, alert_id, before, limit)
        return {"events": events, "next": events[-1]["at"] if len(events) == limit else None}

    # ── watching while the workspace is open ──

    def workspace_opened(self, workspace: str) -> None:
        self.open.add(workspace)
        for alert in self.all(workspace):
            self.follow(workspace, alert)

    def workspace_closed(self, workspace: str) -> None:
        self.open.discard(workspace)
        for alert in self.all(workspace):
            self.stop(alert["id"])

    def follow(self, workspace: str, alert: dict, retry: bool = False) -> None:
        if workspace not in self.open or alert["state"] != "active" or alert["id"] in self.running \
                or alert["id"] in self.starting:
            return
        if alert["expiresAt"] is not None:
            left = alert["expiresAt"] - now_ms()
            if left <= 0:
                self.put(workspace, {**alert, "state": "expired", "revision": alert["revision"] + 1})
                return
            self.expiry[alert["id"]] = asyncio.get_running_loop().call_later(
                left / 1000, self.expire, workspace, alert["id"])
        self.starting[alert["id"]] = asyncio.ensure_future(self.start(workspace, alert, retry))

    async def members(self, workspace: str, alert: dict) -> list[tuple[dict, dict]]:
        target = alert["target"]
        if target["kind"] == "series":
            return [(target["instrument"], target["series"])]
        listed = self.watchlists.get(workspace, target["watchlistId"])
        instruments = [member["instrument"] for section in listed["sections"] for member in section["members"]]
        uses_indicators = any(operand["kind"] in ("indicator", "alertcondition") for condition in alert["conditions"]
                              for operand in operands(condition))
        if uses_indicators and len(instruments) > MAX_INDICATOR_MEMBERS:
            raise ContractError("invalid_input", t("An alert with indicator conditions watches at most {MAX_INDICATOR_MEMBERS} symbols; this watchlist has {value}", MAX_INDICATOR_MEMBERS=MAX_INDICATOR_MEMBERS, value=len(instruments)))
        return [(instrument, self.hub.market.default_series(instrument, target["timeframe"]))
                for instrument in instruments]

    async def start(self, workspace: str, alert: dict, retry: bool) -> None:
        """Watch every member not watched yet and stop watching those that left (a watchlist
        alert follows its list without starting over)."""
        alert_id = alert["id"]
        watchings = self.running.setdefault(alert_id, [])
        fired_bars = (self.store.object("alert_fired_bar", alert_id) or {}).get("bars", {})
        shapes = self.store.object("alert_drawings", alert_id)["shapes"]
        try:
            members = await self.members(workspace, alert)
            wanted = {instrument["instrumentId"] for instrument, _series in members}
            for watching in [item for item in watchings if item.member["instrumentId"] not in wanted]:
                self.drop(watching)
            watched = {watching.member["instrumentId"] for watching in watchings}
            added = []
            for instrument, series in members:
                if instrument["instrumentId"] in watched:
                    continue
                watch = self.watches.get(series["seriesId"])
                if watch is None:
                    feed = await self.hub.acquire(instrument, series)
                    watch = self.watches.get(series["seriesId"])
                    if watch is None:
                        watch = self.watches[series["seriesId"]] = SeriesWatch(self, feed)
                    else:
                        await self.hub.release(feed)
                watching = Watching(self, alert, instrument, watch, fired_bars.get(instrument["instrumentId"]), shapes)
                watch.watchings.append(watching)
                watchings.append(watching)
                added.append(watching)
                if retry:
                    await watch.feed.retry()
                if watching.from_bars() and watch.feed.ready.is_set():
                    watching.rebase()  # otherwise the feed's replace does it
            pins = self.store.object("alert_pins", alert_id)["pins"]
            codes = {key: self.pins.definition(tap.operand["definition"], pins[key])
                     for watching in added for key, tap in watching.taps.items()}
            for watching in added:
                for key, tap in watching.taps.items():
                    tap.definition = codes[key][0]
            outdated = any(changed for _definition, changed in codes.values())
            if codes and outdated != self.records[alert_id][1]["outdated"]:
                self.put(workspace, {**self.records[alert_id][1], "outdated": outdated})
            await asyncio.gather(*(self.attach(tap) for watching in added for tap in watching.taps.values()),
                                 *(watching.watch.ensure_future(watching.furthest()) for watching in added
                                   if watching.shapes))
            self.source_changed_for(alert_id)
        except ContractError as error:
            self.stop(alert_id)
            self.pause(workspace, alert_id, error.code, error.message)
        finally:
            self.starting.pop(alert_id, None)

    async def attach(self, tap: Tap) -> None:
        """The instance starts once Market's window is whole (an indicator on its newest page alone
        could cross on values its full history does not have); its replace sets the baseline. The
        members' windows are waited for side by side."""
        await tap.watching.watch.feed.raw.wait_whole()
        tap.instance = await self.indicators.attach(tap.watching.watch.feed, tap.operand["definition"],
                                                       tap.operand["params"], tap, tap.definition)

    def stop(self, alert_id: str) -> None:
        task = self.starting.pop(alert_id, None)
        if task is not None:
            task.cancel()
        handle = self.expiry.pop(alert_id, None)
        if handle is not None:
            handle.cancel()
        for watching in list(self.running.pop(alert_id, [])):
            self.drop(watching)

    def drop(self, watching: Watching) -> None:
        for tap in watching.taps.values():
            if tap.instance is not None:
                self.indicators.detach(watching.watch.feed, tap.instance, tap)
        running = self.running.get(watching.alert_id)
        if running is not None and watching in running:
            running.remove(watching)
        watch = watching.watch
        watch.watchings.remove(watching)
        if not watch.watchings:
            watch.close()
            del self.watches[watch.feed.series["seriesId"]]
            asyncio.ensure_future(self.hub.release(watch.feed))

    def pause(self, workspace: str, alert_id: str, code: str, message: str) -> None:
        alert = self.records[alert_id][1]
        self.put(workspace, {**alert, "state": "paused", "revision": alert["revision"] + 1,
                             "reason": {"code": code, "message": message}, "source": None})

    def expire(self, workspace: str, alert_id: str) -> None:
        self.stop(alert_id)
        alert = self.records[alert_id][1]
        self.put(workspace, {**alert, "state": "expired", "revision": alert["revision"] + 1, "source": None})

    def watchlist_changed(self, workspace: str, watchlist_id: str) -> None:
        """Members came or went (or the list is gone): its alerts follow at once."""
        for alert in self.all(workspace):
            if alert["target"]["kind"] == "watchlist" and alert["target"]["watchlistId"] == watchlist_id \
                    and alert["state"] == "active":
                if not any(item["id"] == watchlist_id for item in self.watchlists.all(workspace)):
                    self.stop(alert["id"])
                    self.pause(workspace, alert["id"], "watchlist_deleted", "The watchlist was deleted")
                elif alert["id"] in self.running:
                    task = self.starting.pop(alert["id"], None)
                    if task is not None:
                        task.cancel()
                    self.starting[alert["id"]] = asyncio.ensure_future(self.start(workspace, alert, False))

    def pin(self, spec: dict) -> dict[str, str]:
        """The digest of every indicator the conditions use; its code is kept."""
        return {tap_key(operand): self.pins.pin(operand["definition"]) for condition in spec["conditions"]
                for operand in operands(condition) if operand["kind"] in ("indicator", "alertcondition")}

    # ── drawings ──

    def capture(self, workspace: str, spec: dict) -> dict[str, dict]:
        """Copies of the drawings the conditions use, by "chartId/drawingId"."""
        shapes = {}
        for condition in spec["conditions"]:
            for side in ("left", "right", "right2"):
                operand = condition.get(side)
                if not operand or operand["kind"] != "drawing":
                    continue
                document = self.charts.entry(workspace, operand["chartId"]).document
                drawing = next((item for items in document.drawings.values() for item in items
                                if item.id == operand["drawingId"]), None)
                if drawing is None:
                    raise ContractError("not_found", t("No drawing {drawingId} on chart {chartId}", drawingId=operand['drawingId'], chartId=operand['chartId']))
                if drawing.tool not in ALERT_TOOLS:
                    raise ContractError("invalid_input", t("Alerts are not offered on a {tool} drawing", tool=drawing.tool))
                channel = condition["op"] in CHANNEL_OPS and side == "right" and not condition.get("right2")
                if channel != (drawing.tool in CHANNEL_TOOLS):
                    raise ContractError("invalid_input", "Channel conditions take a channel drawing; other "
                                                         "conditions take a line" if channel else
                                        "A channel drawing goes with channel conditions")
                shapes[drawing_key(operand)] = geometry(drawing.model_dump(by_alias=True))
        return shapes

    def chart_changed(self, workspace, chart_id, document) -> None:
        """A drawing an alert uses moved: the alert's copy follows in the same commit; it is gone
        (deleted, undone, the workspace not saved): the alert pauses and says so."""
        drawings = {item.id: item for items in document.drawings.values() for item in items} if document else {}
        for alert in self.all(workspace):
            stored = self.store.object("alert_drawings", alert["id"])
            keys = [key for key in stored["shapes"] if key.startswith(chart_id + "/")]
            if not keys:
                continue
            missing = [key for key in keys if key.split("/", 1)[1] not in drawings]
            if missing:
                if alert["state"] == "active":
                    self.stop(alert["id"])
                    self.pause(workspace, alert["id"], "drawing_deleted", "The drawing was deleted")
                continue
            for key in keys:
                stored["shapes"][key] = geometry(drawings[key.split("/", 1)[1]].model_dump(by_alias=True))
            self.store.put_object("alert_drawings", stored, workspace)
            for watching in self.running.get(alert["id"], []):
                watching.shapes.update(stored["shapes"])
                asyncio.ensure_future(watching.watch.ensure_future(watching.furthest()))

    # ── Market's state of the watched series ──

    def source_changed(self, watch: SeriesWatch) -> None:
        for alert_id in {watching.alert_id for watching in watch.watchings}:
            self.source_changed_for(alert_id)

    def source_changed_for(self, alert_id: str) -> None:
        """Failed pauses the alert (the user retries); recovering shows on it while it waits;
        connecting (before the first live) is only waiting and shows nothing."""
        workspace, alert = self.records[alert_id]
        statuses = [watching.watch.feed.status for watching in self.running.get(alert_id, [])
                    if watching.watch.feed.status is not None]
        failed = next((status for status in statuses if status["state"] == "failed"), None)
        if failed is not None:
            self.stop(alert_id)
            problem = failed["problem"] or {}
            self.pause(workspace, alert_id, "source_failed", problem.get("message") or "The market data source failed")
            return
        recovering = next((status for status in statuses if status["state"] == "recovering"), None)
        source = None if recovering is None else {
            "code": "recovering", "message": (recovering["problem"] or {}).get("message") or "Market is recovering the data"}
        if source != alert["source"]:
            self.put(workspace, {**alert, "source": source})

    # ── firing ──

    def fire(self, watching: Watching, bar_time: int, value: float | None) -> None:
        workspace, alert = self.records[watching.alert_id]
        member = watching.member
        fired_at = now_ms()
        stored = self.store.object("alert_fired_bar", alert["id"]) or {"id": alert["id"], "bars": {}}
        stored["bars"][member["instrumentId"]] = bar_time
        symbol = member["displaySymbol"]
        message = alert["message"] or default_message(alert, symbol)
        if alert["target"]["kind"] == "watchlist" and alert["message"]:
            message = f"{symbol}: {message}"
        event = {"id": "fi-" + uuid.uuid4().hex[:12], "alertId": alert["id"], "at": fired_at, "barTime": bar_time,
                 "instrumentId": member["instrumentId"], "symbol": symbol, "message": message, "value": value,
                 "delivered": {}}
        once = alert["frequency"] == "once"
        alert = {**alert, "lastFiredAt": fired_at, "fireCount": alert["fireCount"] + 1,
                 **({"state": "triggered", "revision": alert["revision"] + 1} if once else {})}
        # The event, deduplication marker and count describe the same firing.
        with self.store.transaction():
            self.store.put_object("alert_fired_bar", stored, workspace)
            self.store.add_fired(workspace, event, default_message(alert, symbol))
            self.store.put_object("alert", {"id": alert["id"], "value": alert}, workspace)
        self.records[alert["id"]] = (workspace, alert)
        self.publish(workspace, {"type": "alert", "value": alert})
        self.publish(workspace, {"type": "fired", "event": event})
        if once:
            self.stop(alert["id"])
        task = asyncio.ensure_future(self.notify(workspace, alert, event))
        self.sending.add(task)
        task.add_done_callback(self.sending.discard)

    async def notify(self, workspace: str, alert: dict, event: dict) -> None:
        title = alert["name"] or f"Alert on {event['symbol']}"
        event["delivered"] = await self.notifier.deliver(alert["channels"], title, event["message"], event["id"])
        self.store.update_fired(event)
        self.publish(workspace, {"type": "fired", "event": event})

    async def close(self) -> None:
        for alert_id in list(self.running) + list(self.starting):
            self.stop(alert_id)
        for task in list(self.sending):
            await task
