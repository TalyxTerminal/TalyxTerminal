"""The chart's visible range for Pine's ``chart.left_visible_bar_time`` and ``chart.right_visible_bar_time``.

PyneCore fixes them to the last 20 bars. In TALYX a script that reads them runs once per chart
showing it, with that chart's range: the chart sends it (``Range`` below) and it goes in the
instance's params under ``VIEW``, so instances on different ranges are different instances
and the same range is shared. The range is either two bar times, or the newest ``bars`` bars
when the chart's right edge is on the newest bar: a new bar then moves both edges without a new
run, as on TradingView, where the chart scrolls with it.

The worker overrides the two names before any script is imported (``install``); each instance's
thread names its range (``use``) and the bars it has seen (``Range.bar``). The Host only reads
``VIEW`` and ``uses`` here, so PyneCore is imported by ``install``, in the worker.
"""
from __future__ import annotations

import bisect
import re
import threading
from pathlib import Path

VIEW = "@visibleRange"  # the params key; never a script input's id
NAMES = re.compile(r"\b(?:left|right)_visible_bar_time\b")
_local = threading.local()
_uses: dict[str, bool] = {}
_lib = None  # pynecore.lib, once installed


class Range:
    """``view``: ``{"left": ms, "right": ms}`` or ``{"bars": n}``; ``times``: the chart's bar times
    so far (the newest-bars range counts bars, not time: a market's closed hours are no bars)."""

    def __init__(self, view: dict, times: list[int]):
        self.view, self.times = view, times

    def bar(self, time: int) -> None:
        if not self.times or time > self.times[-1]:
            self.times.append(time)

    def left(self) -> int:
        if "bars" not in self.view:
            return self.view["left"]
        last = bisect.bisect_right(self.times, int(_lib.last_bar_time)) - 1
        return self.times[max(0, last - self.view["bars"] + 1)]

    def right(self) -> int:
        return int(_lib.last_bar_time) if "bars" in self.view else self.view["right"]


def install() -> None:
    """Once per process, before any script is imported: the names read the thread's range, or
    PyneCore's own without one."""
    global _lib
    from pynecore import lib
    from pynecore.core.module_property import module_property
    from pynecore.lib import chart

    _lib = lib
    left, right = chart.left_visible_bar_time, chart.right_visible_bar_time

    @module_property
    def left_visible_bar_time():
        visible = getattr(_local, "range", None)
        return left() if visible is None else visible.left()

    @module_property
    def right_visible_bar_time():
        visible = getattr(_local, "range", None)
        return right() if visible is None else visible.right()

    chart.left_visible_bar_time, chart.right_visible_bar_time = left_visible_bar_time, right_visible_bar_time


def use(visible: Range | None) -> None:
    """The range of the run this thread starts next; None keeps PyneCore's."""
    _local.range = visible


def uses(digest: str, path: Path, root: Path | None) -> bool:
    """Whether the code reads the visible range: its main file, or any file of its project."""
    if digest not in _uses:
        files = sorted(root.rglob("*.py")) if root is not None else [path]
        _uses[digest] = any(NAMES.search(file.read_text(encoding="utf-8", errors="replace")) for file in files)
    return _uses[digest]
