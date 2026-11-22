"""Pine instances in worker processes, shared by every consumer of the same computation.

Built-ins and plain user projects run in a pool of default worker processes (instances go to
the least loaded one, so windows compute side by side); a project with its own locked
dependencies gets a process of its own. Instances that read a chart's visible range run in one
more process of their environment, started when the first one comes: dragging a chart starts
them again and again, which must not hold up the other indicators. The pool starts with Chart
(``warm``). An instance is one definition with one set of params on one series window (a feed
key); a chart and an alert asking for the same one share it. The Host keeps
each instance's current output, so a consumer that attaches late starts from a replace.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from talyx_sdk.i18n import t

from . import history
from .process import PineProcess
from .visible import VIEW

START_SECONDS = 120.0
STEP_SECONDS = 10.0
RETIRE_SECONDS = 60.0  # a replaced history file stays this long: children of the runs it fed may still read it
POOL = min(4, max(2, (os.cpu_count() or 2) // 2))  # default worker processes


def pool_size(sites: tuple[str, ...]) -> int:
    return POOL if not sites else 1


def view_index(sites: tuple[str, ...]) -> int:
    """The process of the environment for instances that read the visible range: after its pool."""
    return pool_size(sites)


@dataclass(frozen=True)
class Definition:
    """Exact code to run: ``path`` (the main file), ``root`` (a user project's directory, whose
    modules each instance loads for itself), ``sites`` (the project's own dependency
    environment, empty for the default one) and ``digest`` (its content identity)."""
    digest: str
    path: Path
    root: Path | None = None
    sites: tuple[str, ...] = ()


Listener = Callable[[dict], None]


@dataclass(eq=False)
class Instance:
    id: str
    key: str
    definition: Definition
    params: dict
    listeners: list[Listener] = field(default_factory=list)
    output: dict | None = None
    error: dict | None = None
    # seconds allowed for each step sent and not answered yet; the first one's clock runs from
    # ``since``, when the script got to it (bars queued behind a long window wait their turn)
    pending: deque = field(default_factory=deque)
    since: float = 0.0
    needs: set = field(default_factory=set)  # (symbol, timeframe) of request.security
    worker: tuple | None = None  # the key of the worker process it runs in
    run: int = 0  # each start is a new run; messages of an earlier run are dropped

    def apply(self, message: dict) -> dict:
        """Folds a worker message into the kept output; returns the item to hand on."""
        op = message["op"]
        if op == "error":
            self.output, self.error = None, message
            return message
        if op == "replace":
            self.output = {"meta": message["meta"], "behindChart": message["behindChart"],
                           "noScale": message["noScale"], "time": list(message["time"]),
                           "drawings": keyed(message["drawings"]) if "drawings" in message else None,
                           "values": {key: list(value) for key, value in message["values"].items()},
                           "colors": {key: list(value) for key, value in message["colors"].items()},
                           "forming": None}
            self.error = None
            return message
        output = self.output
        if "meta" in message:
            output["meta"] = message["meta"]
        if "drawings" in message:
            output["drawings"] = keyed(message["drawings"])
        for family, change in message.get("drawingChanges", {}).items():
            items = output["drawings"][family]
            items.update((item["id"], item) for item in change["set"])
            for key in change["drop"]:
                del items[key]
        if op == "update":
            output["forming"] = message
            return message
        output["forming"] = None
        count = len(output["time"])
        output["time"].extend(message["time"])
        for store, part in (("values", message["values"]), ("colors", message["colors"])):
            columns = output[store]
            for key in columns.keys() - part.keys():
                columns[key].extend([None] * len(message["time"]))
            for key, value in part.items():
                columns.setdefault(key, [None] * count).extend(value)
        return message

    def replace(self) -> dict:
        """The kept output as one replace item (for a consumer attaching late), copied: the kept
        columns grow with later appends while the item may still wait to be sent."""
        output = self.output
        item = {"op": "replace", "meta": output["meta"], "behindChart": output["behindChart"],
                "noScale": output["noScale"], "time": list(output["time"]),
                "values": {key: list(value) for key, value in output["values"].items()},
                "colors": {key: list(value) for key, value in output["colors"].items()}}
        if output["drawings"] is not None:
            item["drawings"] = {family: list(items.values()) for family, items in output["drawings"].items()}
        return item


def keyed(drawings: dict) -> dict:
    """A drawing snapshot as ``{family: {id: drawing}}``, in drawing order, for the changes that follow it."""
    return {family: {item["id"]: item for item in items} for family, items in drawings.items()}


def retire(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        pass


class Worker:
    def __init__(self, engine: PineEngine, sites: tuple[str, ...], index: int, restarted: bool = False):
        self.engine, self.sites, self.index, self.restarted = engine, sites, index, restarted
        self.key = (sites, index)
        name = ("default" if not sites else uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(sites)).hex[:12]) + \
            (f"-{index}" if index else "")
        self.process = PineProcess(sites, engine.logs / f"pine-{name}.log", engine.logs.parent / "feeds" / name,
                                   engine.logs.parent / "pycache", self.received, self.exited)
        self.series: set[str] = set()
        self.secondaries: set[str] = set()  # feeds for request.security this process has
        self.instances: dict[str, Instance] = {}
        self.stuck: list[Instance] = []
        self.started = asyncio.ensure_future(self.process.start())
        self.started.add_done_callback(lambda _: engine.health_changed())
        self.watchdog = asyncio.ensure_future(self.watch())

    def state(self) -> tuple[str, str | None]:
        if not self.started.done():
            return ("restarting" if self.restarted else "starting"), None
        if self.started.cancelled() or self.started.exception() is not None:
            return "down", str(None if self.started.cancelled() else self.started.exception()) or "did not start"
        return "ok", None

    def received(self, message: dict) -> None:
        instance = self.instances.get(message["id"])
        if instance is None or message.pop("run") != instance.run:
            return  # stopped meanwhile, or written by a run started over since
        if message["op"] == "need":  # PyneCore resolved a request.security context of the script
            need = (message["symbol"], message["timeframe"])
            instance.needs.add(need)
            self.engine.need(self, need, instance)
            return
        if instance.pending:
            instance.pending.popleft()
            instance.since = time.monotonic()
        if message["op"] == "error":
            instance.pending.clear()
        item = instance.apply(message)
        for listener in list(instance.listeners):
            listener(item)
        if message["op"] in ("replace", "error") and self.engine.release_waiting:
            self.engine.release_secondaries()  # it has asked for every feed it reads

    def exited(self) -> None:
        self.engine.restart(self)

    async def watch(self) -> None:
        while True:
            await asyncio.sleep(1)
            now = time.monotonic()
            stuck = [instance for instance in self.instances.values()
                     if instance.pending and instance.since + instance.pending[0] < now]
            if stuck:
                self.stuck = stuck
                self.process.kill()
                return

    def ensure_series(self, key: str) -> None:
        if key not in self.series:
            self.series.add(key)
            self.process.send(self.engine.series_message(key))
            forming = self.engine.feeds[key]["forming"]
            if forming is not None:
                self.process.send({"op": "bar", "series": key, "bar": forming, "closed": False})

    def start(self, instance: Instance) -> None:
        self.ensure_series(instance.key)
        self.instances[instance.id] = instance
        # The earlier run's output stands for no window any more: a consumer attaching now waits for this run.
        instance.output = instance.error = None
        instance.run += 1
        instance.pending.clear()
        instance.pending.append(START_SECONDS)
        instance.since = time.monotonic()
        definition = instance.definition
        self.process.send({"op": "start", "id": instance.id, "run": instance.run, "series": instance.key,
                           "path": str(definition.path), "root": str(definition.root) if definition.root else None,
                           "params": instance.params})

    async def close(self) -> None:
        self.watchdog.cancel()
        try:
            await self.started
        except (OSError, AssertionError, ValueError):
            return  # it never ran (no process, or it died before saying ready)
        await self.process.close()


class PineEngine:
    def __init__(self, logs: Path):
        self.logs = logs
        self.workers: dict[tuple[str, ...], Worker] = {}
        self.instances: dict[tuple, Instance] = {}
        self.feeds: dict[str, dict] = {}
        # request.security: Chart supplies another symbol's or timeframe's bars from a start time
        # (``supply`` starts that, the returned callable ends it); bars go to the workers that asked.
        self.supply: Callable[[str, str, int | None], Callable[[], None]] | None = None
        self.secondaries: dict[tuple[str, str], dict] = {}
        self.supplies: dict[tuple[str, str], tuple[Callable[[], None], int | None]] = {}  # (stop, start)
        self.history = logs / "history"  # secondary histories for the workers (history.py), beside their logs
        shutil.rmtree(self.history, ignore_errors=True)  # what an earlier run left
        # A feed nothing reads any more is let go after this long: an input change starts the
        # new instance before it asks for the feeds it shares with the old one.
        self.grace = 5.0
        self.release_waiting = False  # a release waits for instances still asking for their feeds
        # Chart's health item: each listener gets ``health()`` whenever the pool's state changes.
        self.health_listeners: list[Callable[[dict], None]] = []
        self.closing = False

    # ── series ──

    def series_message(self, key: str) -> dict:
        feed = self.feeds[key]
        return {"op": "series", "series": key, "syminfo": feed["syminfo"], "bars": feed["bars"]}

    def set_series(self, key: str, syminfo: dict, bars: dict, forming: dict | None, *, whole: bool = True) -> None:
        """A feed's window (closed bars as columns) and its forming bar; every instance on it
        recomputes and sends a replace. ``whole`` is False for Market's newest page while it fills
        the rest: shown at once, its instances get no request.security feeds until the whole window."""
        self.feeds[key] = {"syminfo": syminfo, "bars": {name: list(values) for name, values in bars.items()},
                           "forming": forming, "whole": whole}
        for worker in self.workers.values():
            if key in worker.series:
                worker.process.send(self.series_message(key))
                if forming is not None:
                    worker.process.send({"op": "bar", "series": key, "bar": forming, "closed": False})
                for instance in [item for item in worker.instances.values() if item.key == key]:
                    worker.start(instance)

    def bar(self, key: str, bar: dict, closed: bool) -> None:
        """``bar``: {time, open, high, low, close, volume}."""
        feed = self.feeds[key]
        if closed:
            for name in feed["bars"]:
                feed["bars"][name].append(bar[name])
            feed["forming"] = None
        else:
            feed["forming"] = bar
        for worker in self.workers.values():
            if key in worker.series:
                worker.process.send({"op": "bar", "series": key, "bar": bar, "closed": closed})
                for instance in worker.instances.values():
                    if instance.key == key and instance.error is None:
                        if not instance.pending:
                            instance.since = time.monotonic()
                        instance.pending.append(STEP_SECONDS)

    # ── other symbols and timeframes (request.security) ──

    def need(self, worker: Worker, need: tuple[str, str], instance: Instance) -> None:
        """A feed an instance reads, from the start of its chart's window (TradingView: request.security
        covers the chart's bars). A supply that starts later is replaced by one from the earlier start,
        whose history this worker then gets (not the narrower one: the instance would start again
        when it is replaced). On Market's newest page the feed is supplied as its own standard window
        (no start), so the script draws at once; the whole window starts it again from its own first
        bar, and that start replaces the supply."""
        feed = self.feeds[instance.key]
        start = feed["bars"]["time"][0] if feed["whole"] and feed["bars"]["time"] else None
        supplied = self.supplies.get(need)
        if supplied is None or start is not None and (supplied[1] is None or start < supplied[1]):
            if supplied is not None:
                supplied[0]()
            self.supplies[need] = (self.supply(*need, start), start)
            return
        data = self.secondaries.get(need)
        if data is not None and data["file"] is not None and need not in worker.secondaries:
            worker.secondaries.add(need)
            worker.process.send(self.secondary_message(need))

    def secondary_message(self, need: tuple[str, str]) -> dict:
        """The history file and the bars that came after it was written."""
        data = self.secondaries[need]
        columns, count = data["columns"], data["count"]
        tail = [{name: columns[name][index] for name in history.FIELDS} for index in range(count, len(columns["time"]))]
        return {"op": "feed", "symbol": need[0], "timeframe": need[1], "kind": "replace", "file": str(data["file"]),
                "count": count, "tail": tail, "forming": data["forming"]}

    def set_secondary(self, need: tuple[str, str], columns: dict, forming: dict | None) -> None:
        """The whole history of a feed a script reads with request.security (closed bars as columns:
        time, closeTime, open, high, low, close, volume), handed to the workers as a file
        (``history.py``). When it replaces one a worker had, the instances reading it start again,
        with a start's time to recompute."""
        old = self.secondaries.get(need)
        data = self.secondaries[need] = {"columns": columns, "forming": forming, "file": None, "count": 0}
        data["writing"] = asyncio.ensure_future(self.publish(need, data))
        if old is not None:
            self.discard(old)

    async def publish(self, need: tuple[str, str], data: dict) -> None:
        count = len(data["columns"]["time"])
        path = self.history / f"{uuid.uuid4().hex}.bin"
        try:
            await history.write(path, data["columns"], count)
        except asyncio.CancelledError:
            path.unlink(missing_ok=True)
            raise
        except OSError as error:  # the disk: the scripts reading the feed say why
            path.unlink(missing_ok=True)
            self.secondary_failed(need, f"request.security({need[0]!r}, {need[1]!r}): the bars could not be "
                                        f"handed to the scripts: {error}")
            return
        data["file"], data["count"] = path, count
        for worker in self.workers.values():
            changed = need in worker.secondaries
            if changed or any(need in item.needs for item in worker.instances.values()):
                worker.secondaries.add(need)
                worker.process.send(self.secondary_message(need))
            if changed:
                for instance in [item for item in worker.instances.values() if need in item.needs]:
                    worker.start(instance)

    def discard(self, data: dict) -> None:
        """A history no worker is given any more: its writing stops, its file goes a little later
        (one still open in a stopping child on Windows goes with the next start's cleanup)."""
        data["writing"].cancel()
        if data["file"] is not None:
            asyncio.get_running_loop().call_later(RETIRE_SECONDS, retire, data["file"])

    def secondary_bar(self, need: tuple[str, str], bar: dict, closed: bool) -> None:
        data = self.secondaries.get(need)
        if data is None:
            return  # let go or failed: its link goes when the supply's task ends
        if closed:
            for name in history.FIELDS:
                data["columns"][name].append(bar[name])
            data["forming"] = None
        else:
            data["forming"] = bar
        if data["file"] is None:
            return  # the history being written takes it along
        for worker in self.workers.values():
            if need in worker.secondaries:
                worker.process.send({"op": "feed", "symbol": need[0], "timeframe": need[1], "kind": "bar",
                                     "bar": bar, "closed": closed})

    def secondary_failed(self, need: tuple[str, str], message: str) -> None:
        """A feed a script reads cannot be had: the instances waiting for it or reading it fail
        with why (the worker says so for each); asking again supplies it afresh."""
        supplied = self.supplies.pop(need, None)
        if supplied is not None:
            supplied[0]()
        data = self.secondaries.pop(need, None)
        if data is not None:
            self.discard(data)
        for worker in self.workers.values():
            worker.secondaries.discard(need)
            worker.process.send({"op": "feed_failed", "symbol": need[0], "timeframe": need[1], "message": message})

    def release_secondaries(self) -> None:
        """Lets go of the feeds no instance reads, once no instance is still starting (it may yet
        ask for one: PyneCore resolves its contexts while it computes the window)."""
        running = [item for worker in self.workers.values() for item in worker.instances.values()]
        self.release_waiting = any(item.output is None and item.error is None for item in running)
        if self.release_waiting:
            return
        wanted = {need for item in running for need in item.needs}
        for need in [need for need in self.supplies if need not in wanted]:
            self.supplies.pop(need)[0]()
            data = self.secondaries.pop(need, None)
            if data is not None:
                self.discard(data)
            for worker in self.workers.values():
                if need in worker.secondaries:  # a run that asks for it again waits for a new history
                    worker.secondaries.discard(need)
                    worker.process.send({"op": "feed_drop", "symbol": need[0], "timeframe": need[1]})

    def drop_series(self, key: str) -> None:
        del self.feeds[key]
        for worker in self.workers.values():
            if key in worker.series:
                worker.series.discard(key)
                worker.process.send({"op": "drop", "series": key})
                for instance_id in [item.id for item in worker.instances.values() if item.key == key]:
                    del worker.instances[instance_id]
        for share in [share for share, instance in self.instances.items() if instance.key == key]:
            del self.instances[share]

    # ── instances ──

    def worker(self, sites: tuple[str, ...], index: int = 0, restarted: bool = False) -> Worker:
        if (sites, index) not in self.workers:
            self.workers[(sites, index)] = Worker(self, sites, index, restarted)
            self.health_changed()
        return self.workers[(sites, index)]

    def place(self, sites: tuple[str, ...], params: dict) -> Worker:
        """The environment's least loaded process (by instance count), or its visible-range process."""
        if VIEW in params:
            return self.worker(sites, view_index(sites))
        return min((self.worker(sites, index) for index in range(pool_size(sites))),
                   key=lambda worker: len(worker.instances))

    async def warm(self) -> None:
        """The default pool's processes, started side by side before anything is asked."""
        await asyncio.gather(*(self.worker((), index).started for index in range(pool_size(()))))

    async def attach(self, key: str, definition: Definition, params: dict, listener: Listener) -> Instance:
        """Starts (or joins) the instance; the listener receives its items from now on, beginning
        with a replace (or an error)."""
        share = (key, definition, json.dumps(params, sort_keys=True))
        instance = self.instances.get(share)
        if instance is None:
            instance = self.instances[share] = Instance(uuid.uuid4().hex, key, definition, params)
            worker = self.place(definition.sites, params)
            instance.worker = worker.key
            worker.instances[instance.id] = instance  # counts for the next placement at once
            try:
                await worker.started
            except BaseException:
                worker.instances.pop(instance.id, None)
                if self.instances.get(share) is instance:
                    del self.instances[share]
                raise
            worker.start(instance)
        elif instance.output is not None:
            listener(instance.replace())
        elif instance.error is not None:
            listener(instance.error)
        instance.listeners.append(listener)
        return instance

    def detach(self, instance: Instance, listener: Listener) -> None:
        instance.listeners.remove(listener)
        if instance.listeners:
            return
        share = (instance.key, instance.definition, json.dumps(instance.params, sort_keys=True))
        if self.instances.get(share) is instance:
            del self.instances[share]
        worker = self.workers.get(instance.worker)  # a stopped instance's worker may be gone
        if worker is not None and worker.instances.pop(instance.id, None) is not None:
            worker.process.send({"op": "stop", "id": instance.id})
        asyncio.get_running_loop().call_later(self.grace, self.release_secondaries)

    async def describe(self, definition: Definition) -> dict:
        worker = self.worker(definition.sites)
        await worker.started
        return await worker.process.describe(definition.path, definition.root)

    # ── health ──

    def health(self) -> dict:
        """The default pool's state for Chart's health item: ``ok`` (every process up),
        ``starting``, ``restarting`` (a process stopped; its instances are starting again in a
        new one) or ``down`` (a process could not start; ``reason`` says why). ``processes``: each
        process's state and how many instances it runs; the visible-range process once it started."""
        processes = []
        for index in self.default_indexes():
            worker = self.workers.get(((), index))
            state, reason = worker.state() if worker is not None else ("down", "not running")
            processes.append({"state": state, "reason": reason,
                              "instances": len(worker.instances) if worker is not None else 0})
        for state in ("down", "restarting", "starting"):
            worst = [item for item in processes if item["state"] == state]
            if worst:
                return {"state": state, "reason": worst[0]["reason"], "processes": processes}
        return {"state": "ok", "reason": None, "processes": processes}

    def health_changed(self) -> None:
        if self.closing:
            return
        health = self.health()
        for listener in list(self.health_listeners):
            listener(health)

    def default_indexes(self) -> list[int]:
        """The default environment's processes: its pool, and its visible-range process once started."""
        return [*range(pool_size(())), *([view_index(())] if ((), view_index(())) in self.workers else [])]

    def restart_pool(self) -> None:
        """Every default process anew (the health item's restart): a running one is killed (its
        instances say so and start again, as after a crash); one that could not start is
        started again."""
        for index in self.default_indexes():
            worker = self.workers.get(((), index))
            if worker is None:
                self.worker((), index, restarted=True)
            elif worker.started.done() and worker.state()[0] == "ok":
                worker.process.kill()  # restart() follows when it has exited
            elif worker.started.done():  # down: nothing runs in it
                del self.workers[worker.key]
                worker.watchdog.cancel()
                self.worker((), index, restarted=True)

    def restart(self, worker: Worker) -> None:
        """The process died or was killed: its instances say so, then start again in a new one."""
        del self.workers[worker.key]
        worker.watchdog.cancel()
        instances = [instance for instance in worker.instances.values() if instance not in worker.stuck]
        for instance in worker.stuck:
            instance.pending.clear()
            item = instance.apply({"op": "error", "code": "script_error", "line": None,
                                   "message": t("The script did not finish a bar in time and was stopped.")})
            for listener in list(instance.listeners):
                listener(item)
        for instance in instances:
            instance.output = None
            instance.pending.clear()
            for listener in list(instance.listeners):
                listener({"op": "error", "code": "worker_restarted",
                          "message": t("The indicator process stopped; it is starting again."), "line": None})
        if not instances and (worker.sites or worker.index == view_index(worker.sites)):
            return  # a project's or the visible-range process starts again when something asks for it
        replacement = self.worker(worker.sites, worker.index, restarted=True)  # the pool stays whole

        async def again():
            await replacement.started
            for instance in instances:
                if instance.listeners:
                    replacement.start(instance)
        asyncio.ensure_future(again())

    async def close(self) -> None:
        self.closing = True
        for data in self.secondaries.values():
            data["writing"].cancel()
        for worker in list(self.workers.values()):
            await worker.close()
        self.workers.clear()
        shutil.rmtree(self.history, ignore_errors=True)
