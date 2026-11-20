"""Heikin Ashi and brick bars (Renko, Kagi, Point & Figure, Line break, Range) from a raw window.

The raw bars stay Market's; this projects them into display bars. A raw row is a tuple
``(time, closeTime, open, high, low, close, volume)`` (the feed's column order); a projected bar
is a tuple indexed by the constants below: display time (strictly increasing), the raw bar that
made it, OHLCV, direction (1 up, -1 down, 0 flat), whether it is confirmed, and the box count of
a Point & Figure column.

The forming raw bar is a provisional branch over the confirmed bars: each update rolls the
previous branch back and feeds the new bar, so it never leaks into the confirmed prefix; a revised
last closed bar rolls back to before that bar the same way. An algorithm only replaces its last
bar and appends, so a rollback restores the length, the last bar and the scalar state, and an
update costs the bars it touches, not the window.
"""
from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import islice
from operator import itemgetter
from typing import Literal

import numpy as np
from scipy.signal import lfilter

MAX_PROJECTED_BARS = 60_000
MAX_STEPS_PER_BAR = 4_096  # box or range units one raw bar may cross (Renko and Range loop per unit)

TIME, SOURCE_TIME, CLOSE_TIME, OPEN, HIGH, LOW, CLOSE, VOLUME, DIRECTION, CONFIRMED, BOXES = range(11)

ProjectionKind = Literal["heikin_ashi", "renko", "kagi", "point_figure", "line_break", "range"]
Raw = tuple  # (time, closeTime, open, high, low, close, volume)
Bar = tuple


class BarProjectionError(ValueError):
    """A projection setting or raw bar is invalid."""


class BarProjectionLimitError(BarProjectionError):
    """The projection would exceed its bounded work."""


@dataclass(frozen=True, slots=True)
class ProjectionSpec:
    """Absolute price units for Renko, Kagi, P&F and Range (ATR or percentage sizing would rewrite
    history and is not offered; a feed with no box size or range ticks of its own fixes a box
    when it opens, ``default_box_size``)."""

    kind: ProjectionKind
    source: Literal["close", "ohlc"] = "close"
    box_size: float | None = None
    reversal_boxes: int = 3
    line_count: int = 3
    range_ticks: int | None = None
    minimum_tick: float = 0.01

    def __post_init__(self) -> None:
        if self.kind in ("renko", "kagi", "point_figure") and self.box_size is None:
            raise BarProjectionError(f"{self.kind} needs a box size")
        if self.kind == "range" and self.box_size is None and self.range_ticks is None:
            raise BarProjectionError("range needs a box size or range ticks")
        if not 1 <= self.reversal_boxes <= 100 or not 1 <= self.line_count <= 100:
            raise BarProjectionError("reversal boxes and line count are between 1 and 100")
        if self.range_ticks is not None and not 1 <= self.range_ticks <= 1_000_000:
            raise BarProjectionError("range ticks are between 1 and 1000000")
        for value in (self.box_size or 1.0, self.minimum_tick):
            if not (math.isfinite(value) and value > 0):
                raise BarProjectionError("box size and minimum tick are positive and finite")

    @property
    def range_size(self) -> float:
        return self.box_size if self.box_size is not None else self.range_ticks * self.minimum_tick


def default_box_size(kind: ProjectionKind, columns: Sequence[Sequence], minimum_tick: float,
                     reversal_boxes: int, length: int = 14) -> float:
    """Renko and P&F boxes of ATR(14) at the window's last bar (Pine's ``ta.atr``; TradingView's
    default), Range bars as tall, Kagi reversing by that much (``reversal_boxes`` boxes); in whole ticks,
    at least one."""
    highs, lows, closes = (np.asarray(column, dtype=np.float64) for column in columns[3:6])
    if not len(closes):
        raise BarProjectionError(f"{kind} sizes its boxes from bars and the series has none")
    previous = np.concatenate((closes[:1], closes[:-1]))
    ranges = np.maximum(highs - lows, np.maximum(abs(highs - previous), abs(lows - previous)))
    atr = ranges[:length].mean()
    if len(ranges) > length:
        atr = lfilter([1 / length], [1, 1 / length - 1], ranges[length:], zi=[atr * (1 - 1 / length)])[0][-1]
    box = atr / reversal_boxes if kind == "kagi" else atr
    return max(1, round(box / minimum_tick)) * minimum_tick


def checked(columns: Sequence[Sequence]) -> tuple[np.ndarray, ...]:
    """A batch of raw columns as arrays (time, open, high, low, close) after one check over them:
    strictly increasing times, finite prices, high and low around open and close, volume absent
    or non-negative."""
    count = len(columns[0])
    times = np.fromiter(columns[0], np.int64, count)
    opens, highs, lows, closes = (np.fromiter(column, np.float64, count) for column in columns[2:6])
    volumes = np.asarray(columns[6], dtype=np.float64)  # an absent volume (None) becomes NaN
    if count and (times[0] < 0 or not (times[1:] > times[:-1]).all()):
        raise BarProjectionError("raw bars must be strictly time ordered")
    if not (np.isfinite(opens).all() and np.isfinite(highs).all() and np.isfinite(lows).all()
            and np.isfinite(closes).all()):
        raise BarProjectionError("raw prices must be finite")
    if not ((highs >= opens) & (highs >= closes) & (lows <= opens) & (lows <= closes)).all():
        raise BarProjectionError("raw high and low must contain open and close")
    if (volumes < 0).any() or np.isinf(volumes).any() or np.isnan(volumes).sum() != columns[6].count(None):
        raise BarProjectionError("raw volume must be non-negative or absent")
    return times, opens, highs, lows, closes


def path(open_: float, high: float, low: float, close: float) -> tuple:
    """A fixed path through a closed bar (no claim about the order of its ticks)."""
    return (open_, low, high, close) if close >= open_ else (open_, high, low, close)


def priced(columns: Sequence[Sequence], ohlc: bool) -> Iterable[tuple]:
    """(time, close time, the prices the bar passes through) per raw row."""
    time, close_time, open_, high, low, close = columns[:6]
    return zip(time, close_time, map(path, open_, high, low, close) if ohlc else zip(close))


def steps_beyond(prices: tuple, anchor: float, unit: float) -> bool:
    """Whether a bar's price path crosses more than ``MAX_STEPS_PER_BAR`` units."""
    steps = 0.0
    for price in prices:
        steps += abs(price - anchor) / unit
        anchor = price
    return not steps <= MAX_STEPS_PER_BAR


def too_many(bars: list) -> BarProjectionLimitError | None:
    if len(bars) > MAX_PROJECTED_BARS:
        return BarProjectionLimitError(f"the projection exceeds {MAX_PROJECTED_BARS} bars")
    return None


def too_far() -> BarProjectionLimitError:
    return BarProjectionLimitError(f"one raw bar crosses more than {MAX_STEPS_PER_BAR} boxes")


def shown_time(bars: list) -> int:
    """The last display time (-1 before the first bar): a new bar takes its raw bar's time, or one
    millisecond after the last bar when several come from one raw bar."""
    return bars[-1][TIME] if bars else -1


class Algorithm:
    """``bars`` and scalar state; ``feed`` runs a batch of raw rows through the projection, with
    the state in locals stored after the batch (a failed batch is discarded or rolled back by the
    projector) and the bars built inline."""

    def __init__(self, spec: ProjectionSpec) -> None:
        self.spec = spec
        self.bars: list[Bar] = []

    def checkpoint(self) -> tuple:
        bars = self.bars
        return len(bars), bars[-1] if bars else None, dict(self.__dict__)

    def rollback(self, checkpoint: tuple) -> None:
        length, last, state = checkpoint
        del self.bars[length:]
        if length:
            self.bars[-1] = last
        self.__dict__.update(state)

    def seed(self, raw: Sequence[Sequence], arrays: tuple[np.ndarray, ...]) -> tuple[tuple, list[list]]:
        """A non-empty window into no bars yet (raw columns, and the checked arrays of them);
        returns the checkpoint before its last row and the bars as columns."""
        count = len(raw[0])
        self.feed([islice(column, count - 1) for column in raw], True)
        before_last = self.checkpoint()
        self.feed([column[-1:] for column in raw], True)
        return before_last, [list(map(itemgetter(index), self.bars)) for index in range(BOXES + 1)]

    def feed(self, columns: Sequence[Sequence], confirmed: bool) -> None:
        """Raw rows as columns (time, closeTime, open, high, low, close, volume)."""
        raise NotImplementedError


class HeikinAshi(Algorithm):
    def seed(self, raw, arrays):
        times, opens, highs, lows, closes = arrays
        close = (opens + highs + lows + closes) / 4
        # open[i] = (open[i-1] + close[i-1]) / 2 from the first bar's midpoint, as the loop computes it
        open_ = lfilter([0.0, 0.5], [1.0, -0.5], close, zi=[(opens[0] + closes[0]) / 2])[0]
        high, low = np.maximum(np.maximum(highs, open_), close), np.minimum(np.minimum(lows, open_), close)
        count = len(times)
        columns = [list(raw[0]), list(raw[0]), list(raw[1]), open_.tolist(), high.tolist(), low.tolist(),
                   close.tolist(), list(raw[6]), np.sign(close - open_).astype(np.int64).tolist(), [True] * count,
                   [None] * count]
        self.bars = bars = list(zip(*columns, strict=True))
        if error := too_many(bars):
            raise error
        last = bars.pop()
        before_last = self.checkpoint()
        bars.append(last)
        return before_last, columns

    def feed(self, columns, confirmed):
        bars = self.bars
        rows = zip(*columns)
        if not bars:
            for time, close_time, o, h, low, c, volume in rows:  # the first bar opens at its own midpoint
                open_, close = (o + c) / 2, (o + h + low + c) / 4
                bars.append((time, time, close_time, open_, max(h, open_, close), min(low, open_, close), close,
                             volume, 1 if close > open_ else -1 if close < open_ else 0, confirmed, None))
                break
        if not bars:
            return
        previous_time, _, _, previous_open, _, _, previous_close, *_ = bars[-1]
        append = bars.append
        for time, close_time, o, h, low, c, volume in rows:
            open_, close = (previous_open + previous_close) / 2, (o + h + low + c) / 4
            previous_time = time if time > previous_time else previous_time + 1
            append((previous_time, time, close_time, open_, max(h, open_, close), min(low, open_, close), close,
                    volume, 1 if close > open_ else -1 if close < open_ else 0, confirmed, None))
            previous_open, previous_close = open_, close
        if error := too_many(bars):
            raise error


def renko_bounds(last: float, direction: int, box: float) -> tuple[float, float]:
    """The prices strictly between which a Renko price makes no brick."""
    return last - (box if direction <= 0 else 2 * box), last + (box if direction >= 0 else 2 * box)


class Renko(Algorithm):
    def __init__(self, spec):
        super().__init__(spec)
        self.last_close: float | None = None
        self.direction = 0

    def feed(self, columns, confirmed):
        box, ohlc, bars = self.spec.box_size, self.spec.source == "ohlc", self.bars
        push, shown, limit = bars.append, shown_time(bars), MAX_PROJECTED_BARS
        last, direction, reach = self.last_close, self.direction, box * MAX_STEPS_PER_BAR
        down, up = renko_bounds(last, direction, box) if last is not None else (None, None)
        for time, close_time, prices in priced(columns, ohlc):
            if last is None:
                last = prices[0]
                down, up = renko_bounds(last, direction, box)
            if steps_beyond(prices, last, box) if ohlc else abs(prices[0] - last) > reach:
                raise too_far()
            for price in prices:
                if down < price < up:  # no brick
                    continue
                while True:
                    if direction >= 0 and price >= last + box:
                        shown = time if time > shown else shown + 1
                        push((shown, time, close_time, last, last + box, last, last + box, None, 1, confirmed, 1))
                        last, direction = last + box, 1
                    elif direction <= 0 and price <= last - box:
                        shown = time if time > shown else shown + 1
                        push((shown, time, close_time, last, last, last - box, last - box, None, -1, confirmed, 1))
                        last, direction = last - box, -1
                    elif direction == 1 and price <= last - 2 * box:
                        # A reversal brick skips one column: it opens one box from the last close.
                        shown = time if time > shown else shown + 1
                        push((shown, time, close_time, last - box, last - box, last - 2 * box, last - 2 * box, None,
                              -1, confirmed, 1))
                        last, direction = last - 2 * box, -1
                    elif direction == -1 and price >= last + 2 * box:
                        shown = time if time > shown else shown + 1
                        push((shown, time, close_time, last + box, last + 2 * box, last + box, last + 2 * box, None,
                              1, confirmed, 1))
                        last, direction = last + 2 * box, 1
                    else:
                        break
                if len(bars) > limit:
                    raise too_many(bars)
                down, up = renko_bounds(last, direction, box)
        self.last_close, self.direction = last, direction


class Kagi(Algorithm):
    def __init__(self, spec):
        super().__init__(spec)
        self.endpoint: float | None = None
        self.direction = 0

    def feed(self, columns, confirmed):
        reversal, ohlc, bars = self.spec.box_size * self.spec.reversal_boxes, self.spec.source == "ohlc", self.bars
        push, shown = bars.append, shown_time(bars)
        endpoint, direction = self.endpoint, self.direction
        for time, close_time, prices in priced(columns, ohlc):
            if endpoint is None:
                endpoint = prices[0]
            for price in prices:
                origin = endpoint
                if direction == 1 and price > origin or direction == -1 and price < origin:  # the line goes on
                    last = bars[-1]
                    bars[-1] = (last[TIME], time, close_time, last[OPEN], max(last[HIGH], price), min(last[LOW], price),
                                price, None, last[DIRECTION], confirmed, None)
                elif direction == 0 and price != origin:
                    direction = 1 if price > origin else -1
                    shown = time if time > shown else shown + 1
                    push((shown, time, close_time, origin, max(origin, price), min(origin, price), price, None,
                          direction, confirmed, None))
                elif direction == 1 and price <= origin - reversal:
                    direction = -1
                    shown = time if time > shown else shown + 1
                    push((shown, time, close_time, origin, origin, price, price, None, -1, confirmed, None))
                elif direction == -1 and price >= origin + reversal:
                    direction = 1
                    shown = time if time > shown else shown + 1
                    push((shown, time, close_time, origin, price, origin, price, None, 1, confirmed, None))
                else:
                    continue
                endpoint = price
        self.endpoint, self.direction = endpoint, direction
        if error := too_many(bars):
            raise error


class PointFigure(Algorithm):
    def __init__(self, spec):
        super().__init__(spec)
        self.endpoint: float | None = None
        self.direction = 0

    def feed(self, columns, confirmed):
        box, reversal, ohlc, bars = self.spec.box_size, self.spec.reversal_boxes, self.spec.source == "ohlc", self.bars
        push, shown = bars.append, shown_time(bars)
        end, direction = self.endpoint, self.direction
        floor = math.floor
        for time, close_time, prices in priced(columns, ohlc):
            if end is None:
                end = prices[0]
            for price in prices:
                if direction == 1 and price >= end + box or direction == -1 and price <= end - box:  # the column grows
                    boxes = floor(abs(price - end) / box + 1e-12)
                    end = end + direction * boxes * box
                    last = bars[-1]
                    bars[-1] = (last[TIME], time, close_time, last[OPEN], max(last[HIGH], end), min(last[LOW], end),
                                end, None, last[DIRECTION], confirmed, last[BOXES] + boxes)
                    continue
                if direction == 0:
                    boxes = floor(abs(price - end) / box + 1e-12)
                    if not boxes:
                        continue
                    direction, origin = (1 if price > end else -1), end
                    end = origin + direction * boxes * box
                elif direction == 1 and price <= end - reversal * box:
                    direction, origin = -1, end - box
                    boxes = floor((origin - price) / box + 1e-12) + 1
                    end = origin - (boxes - 1) * box
                elif direction == -1 and price >= end + reversal * box:
                    direction, origin = 1, end + box
                    boxes = floor((price - origin) / box + 1e-12) + 1
                    end = origin + (boxes - 1) * box
                else:
                    continue
                shown = time if time > shown else shown + 1
                push((shown, time, close_time, origin, max(origin, end), min(origin, end), end, None, direction,
                      confirmed, boxes))
        self.endpoint, self.direction = end, direction
        if error := too_many(bars):
            raise error


class LineBreak(Algorithm):
    def __init__(self, spec):
        super().__init__(spec)
        self.previous_close: float | None = None

    def feed(self, columns, confirmed):
        count, bars = self.spec.line_count, self.bars
        push, shown = bars.append, shown_time(bars)
        rows = zip(columns[0], columns[1], columns[5])
        if not bars:  # the first line, from the first different close
            previous = self.previous_close
            for time, close_time, close in rows:
                if previous is not None and close != previous:
                    shown = time if time > shown else shown + 1
                    push((shown, time, close_time, previous, max(previous, close), min(previous, close), close, None,
                          1 if close > previous else -1, confirmed, None))
                    break
                previous = close
            self.previous_close = previous
        if bars:  # a close beyond the last ``count`` lines draws a line
            window = bars[-count:]
            highs = deque(map(itemgetter(HIGH), window), maxlen=count)
            lows = deque(map(itemgetter(LOW), window), maxlen=count)
            high_break, low_break = max(highs), min(lows)
            for time, close_time, close in rows:
                if close > high_break:
                    origin = highs[-1]
                    shown = time if time > shown else shown + 1
                    push((shown, time, close_time, origin, close, origin, close, None, 1, confirmed, None))
                    highs.append(close)
                    lows.append(origin)
                    high_break, low_break = close, min(lows)
                elif close < low_break:
                    origin = lows[-1]
                    shown = time if time > shown else shown + 1
                    push((shown, time, close_time, origin, origin, close, close, None, -1, confirmed, None))
                    highs.append(origin)
                    lows.append(close)
                    high_break, low_break = max(highs), close
        if error := too_many(bars):
            raise error


class Range(Algorithm):
    def __init__(self, spec):
        super().__init__(spec)
        self.open: float | None = None
        self.high: float | None = None
        self.low: float | None = None

    def feed(self, columns, confirmed):
        size, ohlc, bars = self.spec.range_size, self.spec.source == "ohlc", self.bars
        push, shown, limit = bars.append, shown_time(bars), MAX_PROJECTED_BARS
        origin, high, low, reach = self.open, self.high, self.low, size * MAX_STEPS_PER_BAR

        def intrabar_prices():
            for time, close_time, prices in priced(columns, True):
                if steps_beyond(prices, prices[0] if origin is None else origin, size):
                    raise too_far()
                for price in prices:
                    yield time, close_time, price

        ticks = intrabar_prices() if ohlc else zip(columns[0], columns[1], columns[5])
        for time, close_time, price in ticks:
            if origin is None:
                origin = high = low = price
            if price > high:
                high = price
            elif price < low:
                low = price
            else:
                continue
            if not ohlc and abs(price - origin) > reach:
                raise too_far()
            # A completed range consumes its width from the opposite edge. The new extreme stays put.
            while high - low >= size:
                if price == high:
                    bottom, low = low, low + size
                    close, direction, top = low, 1, low
                else:
                    top, high = high, high - size
                    close, direction, bottom = high, -1, high
                shown = time if time > shown else shown + 1
                push((shown, time, close_time, origin, top, bottom, close, None, direction, confirmed, None))
                origin = close
                if len(bars) > limit:
                    raise too_many(bars)
        self.open, self.high, self.low = origin, high, low


ALGORITHMS: dict[str, type[Algorithm]] = {"heikin_ashi": HeikinAshi, "renko": Renko, "kagi": Kagi,
                                          "point_figure": PointFigure, "line_break": LineBreak, "range": Range}


class CausalBarProjector:
    """The confirmed bars and the forming bar's provisional branch over them. ``bars`` is what
    is shown; ``confirmed`` counts its confirmed bars (the provisional branch may have taken
    over the last one)."""

    def __init__(self, spec: ProjectionSpec) -> None:
        self.spec = spec
        self.algorithm = ALGORITHMS[spec.kind](spec)
        self.last_time: int | None = None  # of the last confirmed raw row
        self.before_last: tuple | None = None  # checkpoint before that row (a revision of it rolls back)
        self.branch: tuple | None = None  # (checkpoint before the forming row, the forming row)

    @property
    def bars(self) -> list[Bar]:
        return self.algorithm.bars

    @property
    def confirmed(self) -> int:
        bars = self.algorithm.bars
        if self.branch is None:
            return len(bars)
        length = self.branch[0][0]
        return length - 1 if length and not bars[length - 1][CONFIRMED] else length

    def snapshot(self) -> tuple[Bar, ...]:
        return tuple(self.algorithm.bars)

    def seed(self, columns: Sequence[Sequence]) -> list[list]:
        """A new window as raw columns (time, closeTime, open, high, low, close, volume); returns
        the bars as columns (``TIME`` to ``BOXES``). The projector is unchanged when the window is
        invalid or too large."""
        arrays = checked(columns)
        algorithm = ALGORITHMS[self.spec.kind](self.spec)
        if len(arrays[0]):
            before_last, bars = algorithm.seed(columns, arrays)
            last_time = columns[0][-1]
        else:
            before_last, bars, last_time = None, [[] for _ in range(BOXES + 1)], None
        self.algorithm, self.last_time, self.before_last, self.branch = algorithm, last_time, before_last, None
        return bars

    def update(self, row: Raw, closed: bool) -> tuple[int, list[Bar]]:
        """A closed or forming raw row: the bars from the returned index on are replaced by the
        returned ones. On an error nothing changes."""
        columns = tuple(zip(row))
        checked(columns)
        last_time = self.last_time
        if last_time is not None and (row[0] < last_time or not closed and row[0] == last_time):
            raise BarProjectionError("raw updates must follow the confirmed bars")
        algorithm, branch = self.algorithm, self.branch
        # back to before the revised bar (Market revised the last closed bar), or the forming one
        back = self.before_last if closed and row[0] == last_time else branch[0] if branch is not None else None
        length = back[0] if back is not None else len(algorithm.bars)
        start = max(0, length - 1)  # nothing before the bar at the checkpoint can change
        before, state = algorithm.bars[start:], dict(algorithm.__dict__)
        if back is not None:
            algorithm.rollback(back)
        self.branch = None
        try:
            checkpoint = algorithm.checkpoint()
            algorithm.feed(columns, closed)
        except BarProjectionError:  # back to what was shown, the forming branch included
            del algorithm.bars[start:]
            algorithm.bars.extend(before)
            algorithm.__dict__.update(state)
            self.branch = branch
            raise
        if closed:
            self.last_time, self.before_last = row[0], checkpoint
        else:
            self.branch = (checkpoint, row)
        after = self.algorithm.bars
        common = start
        for previous, current in zip(before, after[start:], strict=False):
            if previous != current:
                break
            common += 1
        return common, after[common:]
