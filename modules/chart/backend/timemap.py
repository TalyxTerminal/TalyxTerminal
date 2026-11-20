"""Real time and a chart's logical bar index.

A time inside a bar's period maps to that bar's index plus the elapsed fraction of the period;
a time in a closed-market gap maps to the next bar; past the last closed bar, Market's
``calendar.bar_times`` count on (weekends and holidays take no room); at or past the end of
Market's calendar coverage there is no index.
"""
from __future__ import annotations

from bisect import bisect_right


class TimeMap:
    def __init__(self, times: list[int], close_times: list[int], future: list[int], future_close: list[int],
                 covered_until: int | None):
        self.times = times + future
        self.close_times = close_times + future_close
        self.covered_until = covered_until

    def index(self, time: int) -> float | None:
        if self.covered_until is not None and time >= self.covered_until:
            return None
        position = bisect_right(self.times, time) - 1
        if position < 0:
            return float(time - self.times[0]) / (self.close_times[0] - self.times[0])
        if position == len(self.times) - 1 and time >= self.close_times[position]:
            return None  # past the bars Market's calendar gave
        if time >= self.close_times[position]:
            return float(position + 1)
        return position + (time - self.times[position]) / (self.close_times[position] - self.times[position])


def line_value(points: list[dict], timemap: TimeMap, time: int) -> float | None:
    """A straight line through two drawing points, at ``time`` (null where any index is unknown)."""
    first, second = (timemap.index(point["time"]) for point in points[:2])
    at = timemap.index(time)
    if first is None or second is None or at is None or first == second:
        return None
    slope = (points[1]["price"] - points[0]["price"]) / (second - first)
    return points[0]["price"] + slope * (at - first)
