"""request.security_lower_tf of raw price fields, served in the instance's own process.

PyneCore runs every request.security context in a child process of its own. A lower timeframe
whose expression is only raw price fields (``open``, ``high``, ``low``, ``close``, ``volume``,
``hl2``, ``hlc3``, ``ohlc4``, ``hlcc4``: PyneCore's pass-through contexts, ``ohlcv_fields``)
needs no script run at all, only the feed's bars: here those contexts read the TALYX feed
(``feed.py``) directly, so a run starts no child and reads each chart bar's intrabars by
slicing. Spawning the child and stepping it bar by bar took about three seconds for ten
thousand chart bars; a visible-range indicator runs again on every drag of the chart.

The intrabars of a chart bar are the ones the child would have had: bars opening in the chart
bar that closed by the chart's observation instant (the bar's close; for a developing bar the
current time inside it), then, while the chart is live, the intrabar still open at that instant.
The chart bar ends at Market's ``closeTime``, which the worker hands over with each bar
(``closing``): a month, a quarter or a year is no fixed number of seconds.
"""
from __future__ import annotations

import bisect
import functools
import math
import threading
import time

from pynecore import lib
from pynecore.core import security, security_process
from pynecore.lib.ticker import _split_chart_type
from pynecore.lib.timeframe import _in_seconds

from modules.chart.backend.pine import history
from modules.chart.backend.pine.feed import TalyxFeed

_local = threading.local()
_inject_protocol = security.inject_protocol
_FIELDS = {
    "open": lambda o, h, low, c: o,
    "high": lambda o, h, low, c: h,
    "low": lambda o, h, low, c: low,
    "close": lambda o, h, low, c: c,
    "hl2": lambda o, h, low, c: (h + low) / 2.0,
    "hlc3": lambda o, h, low, c: (h + low + c) / 3.0,
    "ohlc4": lambda o, h, low, c: (o + h + low + c) / 4.0,
    "hlcc4": lambda o, h, low, c: (h + low + 2 * c) / 4.0,
}


def install() -> None:
    """Once per process, before any run: PyneCore hands every run's protocol to the script
    modules through ``inject_protocol``, which then serves these contexts itself."""
    security.inject_protocol = _inject


def quiet_empty_periods() -> None:
    """In the worker and in its security children (which import the worker module): a chart bar with
    no intrabars at all is empty, not a gap to report. PyneCore warned for each one whose next intrabar
    was already past it, so every chart bar before the intrabar feed begins (Market's window of a lower
    timeframe often starts months after the chart's) wrote a log line, tens of thousands a run."""
    collect = security_process._collect_in_period_intrabars

    def collect_quietly(bar_buffer, next_idx, period_end_exclusive, *args, **kwargs):
        if next_idx < len(bar_buffer) and bar_buffer[next_idx].timestamp >= period_end_exclusive:
            return [], next_idx  # what PyneCore returns here, without the warning
        return collect(bar_buffer, next_idx, period_end_exclusive, *args, **kwargs)

    security_process._collect_in_period_intrabars = collect_quietly


def serve(chart: TalyxFeed | None) -> None:
    """The chart feed of the run this thread starts next (its directory and history end)."""
    _local.chart = chart
    _local.closes = {}


def closing(bar_time: int, close_time: int) -> None:
    """The close (Market's ``closeTime``, ms) of a chart bar this thread's run is handed. PyneCore reads the next bar
    before running the script on the current one, so the last few are kept by bar time."""
    closes = _local.closes
    closes[bar_time] = close_time
    if len(closes) > 4:
        del closes[next(iter(closes))]


def _inject(module, signal_fn, write_fn, read_fn, wait_fn, active_security=None, same_context=frozenset()):
    chart = getattr(_local, "chart", None)
    contexts = {sid: context for sid, context in (getattr(module, "__security_contexts__", None) or {}).items()
                if context.get("is_ltf") and context.get("ohlcv_fields")}
    if active_security is None and chart is not None and contexts:
        server = Intrabars(chart, contexts, signal_fn, read_fn, wait_fn)
        signal_fn, read_fn, wait_fn = server.signal, server.read, server.wait
    _inject_protocol(module, signal_fn, write_fn, read_fn, wait_fn, active_security, same_context)


class Intrabars:
    def __init__(self, chart: TalyxFeed, contexts: dict, signal_fn, read_fn, wait_fn):
        self.chart, self.contexts = chart, contexts
        self.signal_fn, self.read_fn, self.wait_fn = signal_fn, read_fn, wait_fn
        self.feeds: dict[str, Feed | None] = {}  # None: PyneCore serves this context
        self.values: dict[str, list] = {}

    def signal(self, sid, symbol=None, timeframe=None, lookahead=None, _scope_id=None):
        if sid not in self.contexts:
            return self.signal_fn(sid, symbol, timeframe, lookahead, _scope_id=_scope_id)
        if sid not in self.feeds:
            self.feeds[sid] = self.open(sid, symbol, timeframe)
        feed = self.feeds[sid]
        if feed is None:
            return self.signal_fn(sid, symbol, timeframe, lookahead, _scope_id=_scope_id)
        self.values[sid] = feed.window()

    def read(self, sid, default=None, _scope_id=None):
        feed = self.feeds.get(sid)
        if feed is None:
            return self.read_fn(sid, default, _scope_id=_scope_id)
        return list(self.values[sid])

    def wait(self, sid, _scope_id=None):
        if self.feeds.get(sid) is None:
            return self.wait_fn(sid, _scope_id=_scope_id)

    def open(self, sid, symbol, timeframe) -> Feed | None:
        """A feed of its own for a lower timeframe of a symbol the TALYX feed supplies; anything
        else (a chart-type symbol, the chart's own timeframe) stays with PyneCore."""
        symbol, timeframe = str(symbol or ""), str(timeframe or "")
        _base, chart_type = _split_chart_type(symbol)
        if chart_type is not None or ":" not in symbol or not timeframe:
            return None
        if not 0 < _in_seconds(timeframe) < _in_seconds(str(lib.syminfo.period)):
            return None
        context = self.contexts[sid]
        feed = TalyxFeed(symbol=symbol, timeframe=timeframe, config=self.chart.config)
        feed.update_symbol_info()  # in the worker: Chart is asked for the bars, and this waits for them
        feed.read()
        return Feed(feed, context["ohlcv_fields"], bool(context.get("ohlcv_tuple")))


class Feed:
    """One context's intrabars: each row's value (a tuple of the fields, or the one field) next
    to the rows of the TALYX feed, the history's from a cache shared by the runs of this process."""

    def __init__(self, feed: TalyxFeed, fields: list[str], is_tuple: bool):
        self.feed, self.fields, self.is_tuple = feed, tuple(fields), is_tuple
        self.rows: list[list] | None = None
        self.times: list[int] = []
        self.closes: list[int] = []
        self.values: list = []

    def sync(self) -> None:
        rows = self.feed.closed
        if rows is not self.rows:
            self.rows = rows
            self.times, self.closes, self.values = [], [], []
            if self.feed.history is not None:
                times, closes, values = _history(*self.feed.history, self.fields, self.is_tuple)
                self.times, self.closes, self.values = list(times), list(closes), list(values)
        for row in rows[len(self.times):]:
            self.times.append(row[1])
            self.closes.append(row[2])
            self.values.append(_value(row, self.fields, self.is_tuple))

    def window(self) -> list:
        start = int(lib._time)
        close = _local.closes[start]
        if lib.barstate.isconfirmed:
            tick = close
        else:
            tick = min(max(int(time.time() * 1000), start), close - 1)
        live = tick > self.feed.config.history_through
        if live:
            self.feed.read()
        self.sync()
        first = bisect.bisect_left(self.times, start)
        end = min(bisect.bisect_right(self.closes, tick, first), bisect.bisect_left(self.times, close, first))
        out = self.values[first:end]
        if live:
            forming = self.feed.forming
            if end < len(self.times) and self.times[end] < min(tick, close):
                out.append(self.values[end])
            elif forming is not None and start <= forming[1] < tick:
                out.append(_value(forming, self.fields, self.is_tuple))
        return out


def _value(row: list, fields: tuple[str, ...], is_tuple: bool):
    _kind, _time, _close_time, o, h, low, c, volume = row
    values = tuple((math.nan if volume is None else volume) if name == "volume" else _FIELDS[name](o, h, low, c)
                   for name in fields)
    return values if is_tuple else values[0]


@functools.lru_cache(maxsize=8)
def _history(path: str, count: int, fields: tuple[str, ...], is_tuple: bool) -> tuple[tuple, tuple, tuple]:
    """Times, close times and values of a history file's rows: the same on every run that reads it."""
    rows = history.read(path, count)
    return (tuple(row[1] for row in rows), tuple(row[2] for row in rows),
            tuple(_value(row, fields, is_tuple) for row in rows))
