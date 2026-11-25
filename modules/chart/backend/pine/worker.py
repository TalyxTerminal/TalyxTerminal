"""Pine worker process: long-lived stepped PyneCore runners, one per indicator instance.

Run as ``python -m modules.chart.backend.pine.worker FEEDS [site dir ...]`` (FEEDS: where this
worker writes other symbols' and timeframes' bars for ``request.security``, see ``feed.py``; extra
site dirs of a project's own dependency environment come first on ``sys.path``; compiled code goes to
``PYTHONPYCACHEPREFIX``). JSON lines on stdin and stdout; see ``process.py`` for the protocol.
Each instance runs in its own thread on the patched PyneCore (``runner_context``): its code runs
in its own run state and the engine lock serializes instances. Rows go out as one replace after
warmup, then one append per closed bar and one update per forming tick, in order per instance.

Other symbols and timeframes (``request.security``, ``request.security_lower_tf``) are asked
for when PyneCore resolves the context, whether its symbol and timeframe are constants or come
from inputs: it asks the TALYX feed for the symbol's info first (``TalyxFeed``), and the
instance's thread waits there until Chart has written the bars (``Instance.need``). A lower
timeframe of raw price fields is read here instead of in a child (``intrabars.py``).

A script that reads the chart's visible range gets its chart's in its params (``visible.py``);
``last_bar_index`` and ``last_bar_time`` on historical bars are the dataset's last bar, as in Pine.
"""
from __future__ import annotations

import json
import math
import os
import queue
import shutil
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

os.environ["PYNE_SAVE_SCRIPT_TOML"] = "0"

from pynecore import lib  # noqa: E402
from pynecore.core import runner_context, script, viz  # noqa: E402
from pynecore.core.script_runner import LIVE_TRANSITION, ScriptRunner, import_script  # noqa: E402
from pynecore.core.syminfo import SymInfo  # noqa: E402
from pynecore.types.color import Color  # noqa: E402
from pynecore.types.na import NA  # noqa: E402
from pynecore.types.ohlcv import OHLCV  # noqa: E402

from modules.chart.backend.pine import intrabars, visible  # noqa: E402
from modules.chart.backend.pine.feed import (  # noqa: E402
    TalyxFeed,
    TalyxFeedConfig,
    feed_file,
    row_line,
)

intrabars.quiet_empty_periods()  # at import, not in main(): the security children import this module too


def color_param(value) -> str:
    """A colour param as PyneCore's ``Color(text)`` reads it, ``#RRGGBBAA``: #RGB, #RGBA and #RRGGBB are spelled out
    as the settings do (``Color`` reads three or four digits as a number: #F00 became nearly black)."""
    digits = value.removeprefix("#") if isinstance(value, str) else ""
    if len(digits) not in (3, 4, 6, 8) or any(digit not in "0123456789abcdefABCDEF" for digit in digits):
        raise ValueError(f"{value!r} is not a colour; give it as #RRGGBB or #RRGGBBAA")
    if len(digits) <= 4:
        digits = "".join(digit * 2 for digit in digits)
    return "#" + digits.upper().ljust(8, "F")


def install_inputs() -> None:
    """Two of PyneCore's inputs, as Chart takes them: a colour param in any of the forms above (an AI may write #F00),
    and ``input.time`` described as a time (PyneCore's records an int input), so the settings offer a date and time."""
    color_input, time_input = script._Input.color.__func__, script._Input.time.__func__

    def color(cls, defval, *args, _id: str = "", **kwargs):
        if _id in script._old_input_values:
            script._old_input_values[_id] = color_param(script._old_input_values[_id])
        return color_input(cls, defval, *args, _id=_id, **kwargs)

    def time_(cls, defval, *args, _id: str = "", **kwargs):
        value = time_input(cls, defval, *args, _id=_id, **kwargs)
        script.inputs[_id].input_type = "time"
        return value

    script._Input.color = classmethod(color)
    script._Input.time = classmethod(time_)


install_inputs()  # at import too: the security children run the script's inputs as well

_out = threading.Lock()
_imports = threading.Lock()
_STOP = object()
_DRAWING_MODULES = (lib.line, lib.label, lib.box, lib.table, lib.polyline, lib.linefill)


_protocol = sys.stdout
FEEDS: Path = Path()
READY: set[str] = set()  # feeds whose history is written
FAILED: dict[str, str] = {}  # feeds Chart could not supply, and why
_feeds = threading.Condition()  # READY and FAILED change under it
MAX_CONTEXTS = 40  # (symbol, timeframe) pairs one script may request: TradingView's limit
SLICE_SECONDS = 0.05  # history runs this long between turns of the other instances
LIVE: set[Instance] = set()  # instances past their history (their real-time bars come first)
NEED_SECONDS = float(os.environ.get("TALYX_PINE_NEED_SECONDS", "60"))  # tests shorten it


class MissingData(Exception):
    """A feed a script requests cannot be had; the message says which and why."""


@contextmanager
def unlocked():
    """The engine lock let go entirely while this thread waits (other instances keep stepping),
    then taken back level by level with the same runner contexts."""
    entered = list(runner_context._stack[1:]) + [runner_context._current] if runner_context._lock._is_owned() else []
    for _ in entered:
        runner_context.release()
    try:
        yield
    finally:
        for context in entered:
            runner_context.acquire(context)


def emit(message: dict, instance: Instance | None = None) -> None:
    """An instance that was stopped says nothing more: a restarted instance keeps its id, and
    its earlier run must not land after the new run's replace (the Host also drops what an earlier
    run of the id wrote before it stopped, by ``run``)."""
    if instance is not None:
        message = {**message, "run": instance.generation}
    line = json.dumps(message, separators=(",", ":"), allow_nan=False)
    with _out:
        if instance is not None and instance.stopped:
            return
        _protocol.write(line + "\n")
        _protocol.flush()


def number(value):
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


# The script's na colour, in meta records and colour rows: that output, or that bar of it, draws nothing (Pine). A null
# in a colour row is a part the script gave no colour for, or a row from before the colour started to vary: the meta's.
NA_COLOR = "na"
META_COLORS = ("color", "textcolor", "colorup", "colordown", "wickcolor", "bordercolor")


def color_channel(value, kind: str):
    """One row of a dynamic colour channel (PyneCore's encoding, but na is ``NA_COLOR``, not null); a gradient fill's
    is [top value, bottom value, top colour, bottom colour]."""
    if isinstance(value, NA):
        return NA_COLOR
    if isinstance(value, tuple):
        if kind == "fill":
            top, bottom, *colors = value
            return [number(top), number(bottom), *(color_channel(part, kind) for part in colors)]
        return [color_channel(part, kind) for part in value]
    return viz.color_str(value)


def drawing_changes(before: dict, after: dict) -> dict:
    """Per family, the drawings made or changed since ``before`` and the ids gone (both ``{family: {id: drawing}}``):
    a script that redraws its newest drawings on each bar sends those, not every drawing it keeps."""
    changes = {}
    for family, items in after.items():
        old = before.get(family, {})
        made = [item for key, item in items.items() if old.get(key) != item]
        gone = [key for key in old if key not in items]
        if made or gone:
            changes[family] = {"set": made, "drop": gone}
    return changes


def candle(bar: dict, closed: bool) -> OHLCV:
    volume = bar["volume"]
    return OHLCV(timestamp=bar["time"], open=bar["open"], high=bar["high"], low=bar["low"], close=bar["close"],
                 volume=float("nan") if volume is None else volume, is_closed=closed)


@contextmanager
def isolated(root: Path | None):
    """Import a script fresh with its project on the path. Modules of the project leave
    ``sys.modules`` afterwards, so the importing instance alone holds its copy (two projects'
    ``utils.py`` never mix, two instances of one project never share module state);
    third-party packages stay shared."""
    with _imports:
        before = set(sys.modules)
        if root is not None:
            sys.path.insert(0, str(root))
        try:
            yield
        finally:
            if root is not None:
                sys.path.remove(str(root))
                for name in set(sys.modules) - before:
                    origin = getattr(sys.modules[name], "__file__", None)
                    if origin and Path(origin).resolve().is_relative_to(root):
                        del sys.modules[name]


def script_line(error: BaseException, path: Path, root: Path | None) -> int | None:
    if isinstance(error, SyntaxError) and error.lineno:
        return error.lineno
    for frame in reversed(traceback.extract_tb(error.__traceback__)):
        if Path(frame.filename).resolve() == path.resolve():
            return frame.lineno
    return None


class Series:
    def __init__(self, syminfo: dict, bars: dict):
        self.syminfo = syminfo
        keys = ("time", "closeTime", "open", "high", "low", "close", "volume")
        self.bars = [dict(zip(keys, values)) for values in zip(*(bars[key] for key in keys))]
        self.forming: dict | None = None
        self.instances: dict[str, Instance] = {}


class Instance(threading.Thread):
    def __init__(self, instance_id: str, generation: int, series: Series, path: Path, root: Path | None,
                 params: dict):
        super().__init__(daemon=True, name=instance_id)
        params = dict(params)
        view = params.pop(visible.VIEW, None)  # the chart's visible range, for a script that reads it
        self.instance_id, self.generation, self.series, self.path, self.root, self.params = \
            instance_id, generation, series, path, root, params  # generation: the Host's run of the id
        self.queue: queue.SimpleQueue = queue.SimpleQueue()
        self.history = list(series.bars)
        self.forming = series.forming  # the real-time bar the run starts with, if the market is open
        self.range = None if view is None else visible.Range(
            view, [bar["time"] for bar in [*self.history, *([self.forming] if self.forming else [])]])
        self.rows: list[dict] = []
        self.colors: list[dict] = []
        self.times: list[int] = []
        self.drawings = None
        self.behind_chart = True  # Pine's default for indicator() and strategy()
        self.no_scale = False  # indicator(scale=scale.none): its own scale, not shown
        self.format = "inherit"  # the script's indicator(format=...), which plots without their own take
        self.precision: int | None = None
        self.context = None
        self.needs: set[str] = set()  # other (symbol, timeframe) feeds, "SYMBOL@TF"
        self.stopped = False
        self.queued = 0  # real-time bars handed to this instance (by the command thread) ...
        self.done = 0  # ... and handled (by this thread): the difference is waiting

    # Rows and colors are read in this runner's run state, after the runner yielded or before
    # the feed hands it the next item.
    def read_row(self, values: dict):
        self.rows.append({key: number(value) for key, value in values.items()})
        self.colors.append({key: color_channel(value, lib._plot_meta[key].kind) for key, value in lib._viz_dyn.items()})

    def meta_record(self, meta) -> dict:
        """A meta record as sent: PyneCore's, with the script's na colours as ``NA_COLOR`` (PyneCore drops them like
        colours not given), and ``format`` its own, else the script's (Pine's format.inherit)."""
        record = viz.serialize_meta(meta)
        for field in META_COLORS:
            if isinstance(getattr(meta, field), NA):
                record[field] = NA_COLOR
        if record.get("format", "inherit") == "inherit":
            record["format"] = self.format
        if record.get("precision") is None and self.precision is not None:
            record["precision"] = self.precision
        return record

    def columns(self, op: str) -> dict:
        keys = list(dict.fromkeys(key for row in self.rows for key in row))
        channels = list(dict.fromkeys(key for row in self.colors for key in row))
        message = {"id": self.instance_id, "op": op, "time": self.times,
                   "values": {key: [row.get(key) for row in self.rows] for key in keys},
                   "colors": {key: [row.get(key) for row in self.colors] for key in channels}}
        if op == "replace":
            message["behindChart"] = self.behind_chart
            message["noScale"] = self.no_scale
        if op == "replace" or lib._plot_meta_new:
            message["meta"] = [self.meta_record(meta) for meta in lib._plot_meta.values()]
            lib._plot_meta_new.clear()
        if any(module._registry for module in _DRAWING_MODULES) or self.drawings:
            snapshot = viz.drawings_snapshot()
            del snapshot["t"]
            drawings = {family: {item["id"]: item for item in items} for family, items in snapshot.items()}
            if op == "replace" or self.drawings is None:
                message["drawings"] = snapshot
            elif drawings != self.drawings:
                message["drawingChanges"] = drawing_changes(self.drawings, drawings)
            self.drawings = drawings
        self.rows, self.colors, self.times = [], [], []
        return message

    def feed(self):
        """History in slices: this thread keeps the engine lock across bars for ``SLICE_SECONDS``
        (no run-state swap per bar), then lets the other instances take a turn, so a new window
        delays live rows elsewhere by one slice at most. Live bars release it after each bar."""
        held, until = False, 0.0
        try:
            for bar in self.history:
                if self.stopped:
                    return
                if held and time.monotonic() > until:
                    runner_context.release()
                    held = False
                    self.give_way()
                elif not held:
                    runner_context.acquire(self.context)
                    held, until = True, time.monotonic() + SLICE_SECONDS
                intrabars.closing(bar["time"], bar["closeTime"])
                yield candle(bar, True)
        finally:
            if held:
                runner_context.release()
        self.history = None
        LIVE.add(self)
        yield LIVE_TRANSITION
        if self.forming is not None:
            # TradingView's dataset ends with the real-time bar: its barstate.islast output (drawings)
            # is in the replace, its row follows as the update.
            bar, self.forming = self.forming, None
            if self.range is not None:
                self.range.bar(bar["time"])
            intrabars.closing(bar["time"], bar["closeTime"])
            yield candle(bar, False)
            with runner_context.inside(self.context):
                replace = self.columns("replace")
                self.times.append(bar["time"])
                self.read_row(lib._plot_data)
                update = self.columns("update")
            emit(replace, self)  # written outside the engine lock: other instances keep stepping
            emit(update, self)
        else:
            with runner_context.inside(self.context):
                replace = self.columns("replace")
            emit(replace, self)
        while True:
            item = self.queue.get()
            if item is _STOP:
                return
            bar, closed = item
            if self.range is not None:
                self.range.bar(bar["time"])
            intrabars.closing(bar["time"], bar["closeTime"])
            yield candle(bar, closed)
            with runner_context.inside(self.context):
                if not closed:
                    self.times.append(bar["time"])
                    self.read_row(lib._plot_data)
                message = self.columns("append" if closed else "update")
            emit(message, self)
            self.done += 1

    def give_way(self) -> None:
        """Between history slices: real-time bars waiting elsewhere are handled first (the lock
        is not fair, so this thread would otherwise take it straight back), for one slice at most."""
        until = time.monotonic() + SLICE_SECONDS
        while time.monotonic() < until and any(other.queued != other.done for other in list(LIVE)):
            time.sleep(0.001)

    def run(self):
        bars = self.feed()
        try:
            info = self.series.syminfo
            syminfo = SymInfo(prefix=info["prefix"], description=info["description"], ticker=info["ticker"],
                              currency=info["currency"], basecurrency=info["basecurrency"], period=info["period"],
                              type=info["type"], mintick=info["mintick"], pricescale=info["pricescale"],
                              pointvalue=1, mincontract=1, opening_hours=[], session_starts=[], session_ends=[],
                              timezone=info["timezone"])
            history_through = self.history[-1]["closeTime"] if self.history else 0
            provider = TalyxFeed(config=TalyxFeedConfig(directory=str(FEEDS), history_through=history_through))
            provider.history_ends_dataset = self.forming is None  # a closed market: its last bar is islast
            # Pine's last_bar_index and last_bar_time on historical bars: the dataset's last bar (the real-time
            # one when the market is open); PyneCore's live run would report each bar as its own last.
            last = {}
            if self.history:
                final = self.forming or self.history[-1]
                last = {"last_bar_time": final["time"],
                        "last_bar_index": len(self.history) - (0 if self.forming else 1)}
            intrabars.serve(provider)
            visible.use(self.range)
            with isolated(self.root):
                # Children warm up over the chart's whole window, not PyneCore's default 500 bars.
                start = datetime.fromtimestamp(self.history[0]["time"] / 1000, UTC).replace(tzinfo=None) \
                    if self.history else None
                runner = ScriptRunner(self.path, bars, syminfo, inputs=self.params, live=True,
                                      lossless_prices=True, lossless_volume=True, chart_provider_name="talyx",
                                      chart_provider_instance=provider, time_from=start, **last)
                self.behind_chart = bool(runner.script.behind_chart)
                self.no_scale = runner.script.scale is lib.scale.none
                self.format = str(runner.script.format)
                self.precision = runner.script.precision
                # Every context's child follows the chart's observations (TALYX's patch).
                modules = [runner.script_module, *(sys.modules[main.__module__] for _title, main
                                                  in script._registered_libraries if main.__module__ in sys.modules)]
                for module in modules:
                    for context in (getattr(module, "__security_contexts__", None) or {}).values():
                        context["authoritative_observations"] = True
                self.prefetch(runner, syminfo)
            self.context = runner._talyx_context
            for result in runner.run_iter():
                with runner_context.inside(self.context):
                    self.times.append(result[0].timestamp)
                    self.read_row(result[1])
        except BaseException as error:  # a script's error ends this instance only
            emit({"id": self.instance_id, "op": "error",
                  "code": "missing_data" if isinstance(error, MissingData) else "script_error",
                  "message": str(error)[:2000] if isinstance(error, MissingData)
                  else f"{type(error).__name__}: {error}"[:2000],
                  "line": script_line(error, self.path, self.root)}, self)
        finally:
            bars.close()  # here, not when the collector gets to it in another thread: a run that stopped holds no lock
            LIVE.discard(self)

    def prefetch(self, runner: ScriptRunner, syminfo: SymInfo) -> None:
        """Pairs known before the run are asked for at once, so Chart gets them ready side by
        side; PyneCore resolves contexts one after another and each waits in ``need``."""
        # A hint only: correctness never depends on it (``need`` asks again); it saves seconds when Market is slow.
        known = runner.list_data_requirements(chart_symbol=f"{syminfo.prefix}:{syminfo.ticker}",
                                              chart_tf=str(syminfo.period))
        pairs = [*known.same_symbol_other_tf, *known.cross_symbol]
        if len({(item.symbol, item.timeframe) for item in pairs}) > MAX_CONTEXTS:
            return  # the run stops at the limit in ``need``
        for item in pairs:
            if f"{item.symbol}@{item.timeframe}" not in READY:
                emit({"id": self.instance_id, "op": "need", "symbol": item.symbol, "timeframe": item.timeframe}, self)

    def need(self, symbol: str, timeframe: str) -> None:
        """Another symbol's or timeframe's bars, when PyneCore resolves a context: Chart is asked
        and this instance's thread waits until they are written, Chart says why it cannot
        supply them, or the time runs out."""
        key = f"{symbol}@{timeframe}"
        if key not in self.needs and len(self.needs) >= MAX_CONTEXTS:
            raise MissingData(f"request.security({symbol!r}, {timeframe!r}): a script can request at most "
                              f"{MAX_CONTEXTS} symbol and timeframe pairs")
        self.needs.add(key)
        with _feeds:
            FAILED.pop(key, None)
        emit({"id": self.instance_id, "op": "need", "symbol": symbol, "timeframe": timeframe}, self)
        deadline = time.monotonic() + NEED_SECONDS
        with unlocked(), _feeds:
            while key not in READY:
                if self.stopped:
                    raise MissingData("stopped")
                if key in FAILED:
                    raise MissingData(FAILED[key])
                if time.monotonic() >= deadline:
                    raise MissingData(f"request.security({symbol!r}, {timeframe!r}): Market sent no bars within "
                                      f"{NEED_SECONDS:.0f} seconds")
                _feeds.wait(deadline - time.monotonic())

    def stop(self):
        with _out:
            self.stopped = True
        LIVE.discard(self)
        self.queue.put(_STOP)
        with _feeds:
            _feeds.notify_all()


ALERT_CONDITION = "alertcondition:"  # row key prefix of an alertcondition's value (1, 0, or null for na)


def alertcondition(condition=None, title=None, message=None):
    """PyneCore's ``alertcondition`` records nothing; here each bar's value goes into the row,
    so an alert can use it (TradingView: an alert on the condition)."""
    if lib._lib_semaphore:
        return
    lib._plot_data[ALERT_CONDITION + (title or "Alert")] = None if lib.is_na(condition) else int(bool(condition))


def input_value(value):
    """An input's default or option as JSON, in the form PyneCore takes it back as a param: a colour as
    ``#RRGGBBAA`` (``Color(text)``), an enum member as its value (``Enum(value)``)."""
    if isinstance(value, Color):
        return viz.color_str(value)
    if isinstance(value, Enum):
        return value.value
    return value


def describe(path: Path, root: Path | None) -> dict:
    with isolated(root):
        # PyneCore collects inputs in one module dict and clears it in the indicator decorator; a script whose
        # request.security reads history (``close[1]``) leaves its inputs there after that, and the next script described
        # in this process listed them as its own.
        script.inputs.clear()
        declaration = import_script(path).main.script

    def options(value):
        # An enum input without options offers every member (Pine's input.enum)
        choices = value.options or (list(type(value.defval)) if value.input_type == "enum" else None)
        return [input_value(item) for item in choices] if choices else None

    return {"title": declaration.title, "shortTitle": declaration.shorttitle or declaration.title,
            "overlay": bool(declaration.overlay),
            "inputs": [{"id": key.removesuffix("__global__"), "title": value.title or key, "type": value.input_type,
                        "default": input_value(value.defval), "min": value.minval, "max": value.maxval,
                        "step": value.step, "options": options(value), "group": value.group,
                        "tooltip": value.tooltip} for key, value in declaration.inputs.items()]}


def main() -> None:
    global FEEDS
    sys.setswitchinterval(0.0005)
    lib.alertcondition = alertcondition  # before any script imports it
    visible.install()
    intrabars.install()
    FEEDS = Path(sys.argv[1])
    shutil.rmtree(FEEDS, ignore_errors=True)  # an earlier process's feeds: this one is handed each again
    FEEDS.mkdir(parents=True, exist_ok=True)
    for site in reversed(sys.argv[2:]):
        sys.path.insert(0, site)
    ready = READY
    series: dict[str, Series] = {}
    instances: dict[str, Instance] = {}
    # The protocol moves off the standard handles: on Windows a pending read on the stdin pipe
    # blocks every child process creation (request.security children would hang), and scripts'
    # prints never reach the protocol.
    global _protocol
    commands = os.fdopen(os.dup(0), "r", encoding="utf-8")
    _protocol = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    emit({"op": "ready"})
    for line in commands:
        message = json.loads(line)
        op = message["op"]
        if op == "series":
            old = series.pop(message["series"], None)
            for instance in old.instances.values() if old else ():
                instance.stop()
                instances.pop(instance.instance_id, None)
            series[message["series"]] = Series(message["syminfo"], message["bars"])
        elif op == "start":  # a new instance, or one starting again (the old run stops)
            old = instances.pop(message["id"], None)
            if old is not None:
                old.series.instances.pop(old.instance_id, None)
                old.stop()
            root = Path(message["root"]).resolve() if message["root"] else None
            target = series[message["series"]]
            instance = Instance(message["id"], message["run"], target, Path(message["path"]), root, message["params"])
            target.instances[instance.instance_id] = instances[instance.instance_id] = instance
            instance.start()
        elif op == "bar":
            target = series[message["series"]]
            bar, closed = message["bar"], message["closed"]
            if closed:
                target.bars.append(bar)
                target.forming = None
            else:
                target.forming = bar
            for instance in target.instances.values():
                instance.queued += 1
                instance.queue.put((bar, closed))
        elif op == "stop":
            instance = instances.pop(message["id"])
            instance.series.instances.pop(instance.instance_id)
            instance.stop()
        elif op == "drop":
            for instance in series.pop(message["series"]).instances.values():
                instances.pop(instance.instance_id)
                instance.stop()
        elif op == "feed":
            # Another symbol's or timeframe's bars: the whole history ("replace": the Host's history
            # file, then the bars after it) or one bar.
            symbol, timeframe = message["symbol"], message["timeframe"]
            path, key = feed_file(FEEDS, symbol, timeframe), f"{symbol}@{timeframe}"
            if message["kind"] == "replace":
                lines = [["h", message["file"], message["count"]], *(row_line("c", bar) for bar in message["tail"])]
                if message["forming"] is not None:
                    lines.append(row_line("f", message["forming"]))
                temporary = path.with_suffix(".tmp")
                temporary.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
                temporary.replace(path)  # instances reading a replaced history are started again by the Host
                with _feeds:
                    ready.add(key)
                    _feeds.notify_all()
            else:
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(row_line("c" if message["closed"] else "f", message["bar"])) + "\n")
        elif op == "feed_drop":
            # The Host let the feed go (its file stays until a new history replaces it): a run that asks
            # for it again waits for that history.
            with _feeds:
                ready.discard(f"{message['symbol']}@{message['timeframe']}")
        elif op == "feed_failed":
            # Chart cannot supply a feed: instances waiting for it fail with why, running ones too.
            key = f"{message['symbol']}@{message['timeframe']}"
            with _feeds:
                ready.discard(key)
                FAILED[key] = message["message"]
                _feeds.notify_all()
            for instance in [item for item in instances.values() if key in item.needs]:
                emit({"id": instance.instance_id, "op": "error", "code": "missing_data", "message": message["message"],
                      "line": None}, instance)
                instance.stop()
        elif op == "describe":
            root = Path(message["root"]).resolve() if message["root"] else None
            try:
                reply = {"reply": message["request"], "ok": describe(Path(message["path"]), root)}
            except BaseException as error:  # the script cannot load; say why
                reply = {"reply": message["request"], "error": f"{type(error).__name__}: {error}"[:2000],
                         "line": script_line(error, Path(message["path"]), root)}
            emit(reply)


if __name__ == "__main__":
    main()
