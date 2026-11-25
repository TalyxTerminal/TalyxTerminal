"""Screens: which symbols' latest closed bar meets every condition.

A run reads each member's latest 10,000 bars from Market (the window alerts and charts use, so
the values agree), computes the indicators the conditions use on them in the Pine worker, and
judges the conditions on the last closed bar the way an alert judges a bar close (crossing
against the bar before). Progress goes out on the workspace stream as the screen with its
run; a run can be cancelled. Indicator code is pinned when the screen is saved.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from contextlib import nullcontext

from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from . import bars as wire_bars
from . import changes
from .alert_rules import MOVING_OPS, Rule, Values
from .alerting import now_ms, operand_name, operands, price, tap_key
from .bars import price_format
from .feeds import syminfo_of
from .marketdata import Market
from .pine.engine import PineEngine
from .pine.indicators import Pins
from .store import Store
from .watchlists import Watchlists
from .wire import DEFAULT_WINDOW

LOG = logging.getLogger(__name__)
PARALLEL = 4  # members computed at once


def check_screen(spec: dict) -> None:
    for condition in spec["conditions"]:
        kinds = [item["kind"] for item in operands(condition)]
        if "drawing" in kinds or "alertcondition" in kinds or condition["op"] == "triggered":
            raise ContractError("invalid_input", t("Screens compare prices, indicator outputs and values"))
        if condition["op"] in MOVING_OPS:
            if condition.get("amount") is None or condition.get("bars") is None:
                raise ContractError("invalid_input", t("Moving conditions take an amount and bars"))
        elif condition["op"].endswith("_channel"):
            if not (condition.get("right") and condition.get("right2")):
                raise ContractError("invalid_input", t("Channel conditions take the two bounds"))
        elif not condition.get("right"):
            raise ContractError("invalid_input", t("This condition takes two operands"))


def legend_name(described: dict, params: dict) -> str:
    """"RSI 14": the short title and the values of the inputs the chart legend shows (not sources,
    colors, switches or times)."""
    shown = [params.get(item["id"], item["default"]) for item in described["inputs"]
             if item["type"] not in ("source", "color", "bool", "time")]
    words = [f"{value:g}" if isinstance(value, float) else str(value) for value in shown]
    return " ".join([described["shortTitle"], *words])


class Screens:
    def __init__(self, store: Store, market: Market, engine: PineEngine, pins: Pins, watchlists: Watchlists,
                 publish: Callable[[str, dict], None]):
        self.store, self.market, self.engine, self.pins = store, market, engine, pins
        self.watchlists, self.publish = watchlists, publish
        self.runs: dict[str, asyncio.Task] = {}
        self.interrupted()

    def interrupted(self) -> None:
        """A run still recorded as running when Chart starts ended with Chart's last process (its task went with it):
        it is closed as failed, with what it found so far, so the screen can be run again."""
        rows = self.store.db.execute("SELECT workspace, record FROM objects WHERE kind = 'screen'").fetchall()
        for workspace, record in rows:
            screen = json.loads(record)["value"]
            run = screen["lastRun"]
            if run is not None and run["state"] == "running":
                self.store.put_object("screen", {"id": screen["id"], "value": {**screen, "lastRun": {
                    **run, "state": "failed", "finishedAt": now_ms(),
                    "error": t("Chart stopped during this run; run it again")}}}, workspace)

    def all(self, workspace: str) -> list[dict]:
        return [record["value"] for record in self.store.objects("screen", workspace)]

    def get(self, workspace: str, screen_id: str) -> dict:
        for value in self.all(workspace):
            if value["id"] == screen_id:
                return value
        raise ContractError("not_found", t("No screen {screen_id} in this workspace", screen_id=screen_id))

    def put(self, workspace: str, screen: dict) -> dict:
        self.store.put_object("screen", {"id": screen["id"], "value": screen}, workspace)
        self.publish(workspace, {"type": "screen", "value": screen})
        return screen

    def save(self, workspace: str, screen_id: str | None, revision: int | None, spec: dict) -> dict:
        check_screen(spec)
        if spec["scope"]["kind"] == "watchlist":
            self.watchlists.get(workspace, spec["scope"]["watchlistId"])
        pins = {tap_key(operand): self.pins.pin(operand["definition"]) for condition in spec["conditions"]
                for operand in operands(condition) if operand["kind"] == "indicator"}
        if screen_id is None:
            screen = {**spec, "id": "sc-" + uuid.uuid4().hex[:12], "revision": 1, "lastRun": None}
        else:
            current = self.get(workspace, screen_id)
            if revision != current["revision"]:
                raise ContractError("conflict", t("The screen changed meanwhile"), {"revision": current["revision"]})
            screen = {**current, **spec, "revision": revision + 1}
        self.store.put_object("screen_pins", {"id": screen["id"], "pins": pins}, workspace)
        return self.put(workspace, screen)

    def delete(self, workspace: str, screen_id: str) -> None:
        self.get(workspace, screen_id)
        self.cancel(workspace, screen_id)
        self.store.delete_object("screen", screen_id)
        self.store.delete_object("screen_pins", screen_id)
        self.publish(workspace, {"type": "screen_removed", "id": screen_id})

    def run(self, workspace: str, screen_id: str) -> dict:
        screen = self.get(workspace, screen_id)
        self.cancel(workspace, screen_id)
        scope = screen["scope"]
        if scope["kind"] == "watchlist":
            listed = self.watchlists.get(workspace, scope["watchlistId"])
            members = [member["instrument"] for section in listed["sections"] for member in section["members"]]
        else:
            members = scope["instruments"]
        run = {"runId": "run-" + uuid.uuid4().hex[:12], "state": "running", "startedAt": now_ms(), "finishedAt": None,
               "done": 0, "total": len(members), "rows": [], "error": None}
        screen = self.put(workspace, {**screen, "lastRun": run})
        self.runs[screen_id] = asyncio.ensure_future(self.work(workspace, screen, members, changes.current_call()))
        return screen

    def cancel(self, workspace: str, screen_id: str) -> dict:
        task = self.runs.pop(screen_id, None)
        screen = self.get(workspace, screen_id)
        if task is not None:
            task.cancel()
            run = {**screen["lastRun"], "state": "cancelled", "finishedAt": now_ms()}
            screen = self.put(workspace, {**screen, "lastRun": run})
        return screen

    async def work(self, workspace: str, screen: dict, members: list[dict], call=None) -> None:
        gate = asyncio.Semaphore(PARALLEL)
        pins = self.store.object("screen_pins", screen["id"])["pins"]
        rows: dict[str, dict] = {}

        async def one(instrument: dict) -> None:
            async with gate:
                try:
                    row = await self.member(screen, instrument, pins, names)
                except ContractError as error:
                    row = {"instrument": instrument, "matched": False, "values": {}, "barTime": None,
                           "error": error.message}
            rows[instrument["instrumentId"]] = row
            current = self.get(workspace, screen["id"])
            ordered = [rows[item["instrumentId"]] for item in members if item["instrumentId"] in rows]
            self.put(workspace, {**current, "lastRun": {**current["lastRun"], "done": len(rows), "rows": ordered}})

        try:
            names = await self.names(screen, pins)
            await asyncio.gather(*(one(instrument) for instrument in members))
            ending = {"state": "completed"}
        except Exception as error:  # noqa: BLE001 -- the screen says the run failed and why
            LOG.exception("Screen %s failed", screen["id"])
            ending = {"state": "failed", "error": str(error)}
        current = self.get(workspace, screen["id"])
        with changes.recording(call) if call else nullcontext():
            self.put(workspace, {**current, "lastRun": {**current["lastRun"], **ending, "finishedAt": now_ms()}})
        self.runs.pop(screen["id"], None)

    async def names(self, screen: dict, pins: dict[str, str]) -> dict[str, str]:
        """Each indicator as the chart legend names it: short title and the inputs it shows."""
        names = {}
        for condition in screen["conditions"]:
            for operand in operands(condition):
                if operand["kind"] == "indicator" and tap_key(operand) not in names:
                    definition, _changed = self.pins.definition(operand["definition"], pins[tap_key(operand)])
                    reply = await self.engine.describe(definition)
                    if "error" in reply:
                        raise ContractError("script_error", f"{operand['definition']['id']}: {reply['error']}")
                    names[tap_key(operand)] = legend_name(reply["ok"], operand["params"])
        return names

    async def member(self, screen: dict, instrument: dict, pins: dict[str, str], names: dict[str, str]) -> dict:
        series = self.market.default_series(instrument, screen["timeframe"])
        page = await self.market.older(instrument, series, now_ms(), DEFAULT_WINDOW)
        columns = wire_bars.columns(page["columns"])
        decimals = page["columns"]["close"][-500:]  # Market's decimal strings: the data's price format
        closed = sum(1 for close in columns["closeTime"] if close <= now_ms())  # the latest closed bar is the last
        columns = {name: values[:closed] for name, values in columns.items()}
        if closed < 2:
            raise ContractError("unavailable", t("Not enough closed bars"))
        outputs = await self.indicators(screen, instrument, series, columns, decimals, pins)
        rule = Rule([{key: item.get(key) for key in ("op", "amount", "bars")} for item in screen["conditions"]],
                    "once_per_bar_close")

        def value(operand: dict | None, index: int):
            if operand is None:
                return None
            if operand["kind"] == "value":
                return operand["value"]
            if operand["kind"] == "price":
                return price({name: columns[name][index] for name in ("open", "high", "low", "close", "volume")},
                             operand["field"])
            return outputs[tap_key(operand)]["values"].get(operand["output"], [None] * closed)[index]

        def at(index: int) -> list[Values]:
            return [Values(*(value(condition.get(side), index) for side in ("left", "right", "right2")))
                    for condition in screen["conditions"]]
        lefts = [[value(condition["left"], index) for index in range(closed - 1)]
                 if condition["op"] in MOVING_OPS else [] for condition in screen["conditions"]]
        rule.rebase(lefts, at(closed - 2))
        last = at(closed - 1)
        matched = rule.observe(columns["time"][-1], True, last, True, now_ms())
        shown = {}
        for condition, values in zip(screen["conditions"], last, strict=True):
            for side, number in (("left", values.left), ("right", values.right), ("right2", values.right2)):
                operand = condition.get(side)
                if operand is not None and operand["kind"] == "indicator":
                    key = tap_key(operand)
                    shown[f"{names[key]} · {outputs[key]['titles'].get(operand['output'], operand['output'])}"] = number
                elif operand is not None and operand["kind"] != "value":
                    shown[operand_name(operand)] = number
        return {"instrument": instrument, "matched": matched, "values": shown, "barTime": columns["time"][-1],
                "error": None}

    async def indicators(self, screen: dict, instrument: dict, series: dict, columns: dict, decimals: list[str],
                         pins: dict[str, str]) -> dict[str, dict]:
        """Each indicator the conditions use, over this member's window: outputs by name."""
        wanted = {tap_key(operand): operand for condition in screen["conditions"] for operand in operands(condition)
                  if operand["kind"] == "indicator"}
        if not wanted:
            return {}
        key = f"screen:{uuid.uuid4().hex[:8]}:{instrument['instrumentId']}"
        syminfo = await syminfo_of(self.market, instrument, series)
        try:  # syminfo.mintick as on the chart (feeds.Feed): Market's increment, else the data's decimals
            increment = await self.market.price_increment(instrument)
        except ContractError as error:
            LOG.warning("No price increment for %s: %s", instrument["instrumentId"], error.message)
            increment = None
        minimum = price_format(increment, decimals)["minMove"]
        syminfo.update(mintick=minimum, pricescale=round(1 / minimum))
        self.engine.set_series(key, syminfo, columns, None)
        attached, outputs = [], {}
        try:
            for name, operand in wanted.items():
                definition, _changed = self.pins.definition(operand["definition"], pins[name])
                done = asyncio.get_running_loop().create_future()

                def listener(message, done=done):
                    if not done.done() and message["op"] in ("replace", "error"):
                        done.set_result(message)
                instance = await self.engine.attach(key, definition, operand["params"], listener)
                attached.append((instance, listener))
                message = await done
                if message["op"] == "error":
                    raise ContractError("script_error", f"{operand['definition']['id']}: {message['message']}")
                outputs[name] = {"values": message["values"],  # and the outputs' titles (Pine's plot titles)
                                 "titles": {meta["id"]: meta.get("title", meta["id"]) for meta in message["meta"]}}
        finally:
            for instance, listener in attached:
                self.engine.detach(instance, listener)
            self.engine.drop_series(key)
        return outputs

    async def close(self) -> None:
        for task in self.runs.values():
            task.cancel()
