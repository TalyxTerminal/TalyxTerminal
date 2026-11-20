"""Columnar bar arithmetic: aggregation, adjustment and decimal text.

Columns are numpy arrays: ``time`` and ``close_time`` int64 nanoseconds, prices and
volume float64 (volume NaN where the source has none). There are no per-row Python
loops here; aggregation is a segment reduction, adjustment a cumulative product.
The same aggregation serves stored history and the forming live bar.
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import orjson
import pyarrow as pa
import pyarrow.compute as pc
from talyx_sdk.columns import TextColumn

from vocabulary.identity import normalize_timeframe, timeframe_nanoseconds

from . import calendars
from .common import DAY_NS

BAR_COLUMNS = ("time", "close_time", "open", "high", "low", "close", "volume")
PRICE_COLUMNS = ("open", "high", "low", "close")
Columns = dict[str, np.ndarray]


def empty(columns=BAR_COLUMNS) -> Columns:
    return {name: np.empty(0, dtype=np.int64 if name in ("time", "close_time") else np.float64) for name in columns}


def length(columns: Columns) -> int:
    return len(columns["time"])


def take(columns: Columns, index) -> Columns:
    return {name: values[index] for name, values in columns.items()}


def concat(parts: list[Columns]) -> Columns:
    parts = [part for part in parts if length(part)]
    if not parts:
        return {}
    names = list(parts[0])
    return {name: np.concatenate([part[name] for part in parts]) for name in names}


def merge(old: Columns, new: Columns) -> Columns:
    """Rows of both, ordered by time; a time present in ``new`` replaces the old row."""
    if not old or not length(old):
        return sort_unique(new)
    if not new or not length(new):
        return old
    keep = ~np.isin(old["time"], new["time"])
    return sort_unique(concat([take(old, keep), new]))


def sort_unique(columns: Columns) -> Columns:
    if not columns or not length(columns):
        return columns
    order = np.argsort(columns["time"], kind="stable")
    sorted_columns = take(columns, order)
    times = sorted_columns["time"]
    # Keep the last row per time (later rows win).
    last = np.ones(len(times), dtype=bool)
    last[:-1] = times[1:] != times[:-1]
    return take(sorted_columns, last)


def window(columns: Columns, start: int | None, end: int | None) -> Columns:
    times = columns["time"]
    lo = 0 if start is None else int(np.searchsorted(times, start, "left"))
    hi = len(times) if end is None else int(np.searchsorted(times, end, "left"))
    return {name: values[lo:hi] for name, values in columns.items()}


def inconsistent(columns: Columns) -> np.ndarray:
    """Rows whose high and low do not contain their open and close."""
    return (columns["high"] < np.fmax(columns["open"], columns["close"])) | \
        (columns["low"] > np.fmin(columns["open"], columns["close"]))


def contain(columns: Columns) -> Columns:
    """Open and close as given; high and low widened just enough to contain them."""
    return {**columns, "high": np.fmax(columns["high"], np.fmax(columns["open"], columns["close"])),
            "low": np.fmin(columns["low"], np.fmin(columns["open"], columns["close"]))}


def is_intraday(timeframe: str) -> bool:
    step = timeframe_nanoseconds(timeframe)
    return step is not None and step < DAY_NS


def divides(base: str, target: str, calendar_id: str) -> bool:
    """True when bars of ``target`` are whole groups of ``base`` bars.

    Days and longer are groups of days; on a 24/7 calendar a UTC day is also a group of intraday bars that
    divide it (a venue whose own days are not cut at UTC midnight gives no daily bars).
    """
    base, target = normalize_timeframe(base), normalize_timeframe(target)
    if base == target:
        return True
    b, t = timeframe_nanoseconds(base), timeframe_nanoseconds(target)
    if not is_intraday(target):
        return base == "1d" or (calendars.canonical(calendar_id) == calendars.CONTINUOUS and is_intraday(base)
                                and DAY_NS % b == 0)
    return b is not None and t is not None and b < t and t % b == 0


def aggregate(columns: Columns, base: str, target: str, calendar_id: str, scope: str = "regular") -> Columns:
    """Bars of ``target`` built from ``base`` bars, with the OHLCV reduction.

    Daily and longer periods come from daily bars (on 24/7 markets also from intraday bars,
    by UTC day); intraday buckets follow the calendar's sessions (aligned to the open) or UTC
    for 24/7 markets.
    """
    base, target = normalize_timeframe(base), normalize_timeframe(target)
    if base == target or not length(columns):
        return columns
    times = columns["time"]
    if is_intraday(target):
        step = timeframe_nanoseconds(target)
        if calendars.canonical(calendar_id) == calendars.CONTINUOUS:
            keys = times - times % step
            closes = keys + step
        else:
            opens, ends = calendars.bar_grid(calendar_id, target, int(times[0]) - step, int(times[-1]) + 1, scope)
            if not len(opens):
                return take(columns, slice(0, 0))
            slot = np.searchsorted(opens, times, "right") - 1
            valid = (slot >= 0) & (times < ends[slot])
            columns = take(columns, valid)
            keys, closes = opens[slot[valid]], ends[slot[valid]]
    else:
        keys = calendars.period_starts(calendar_id, target, times)
        # A day or longer closes when its period ends, not at its last base row: one still going on is not closed.
        firsts = np.unique(keys)
        ends = calendars.period_ends(calendar_id, target, firsts)
        closes = ends[np.searchsorted(firsts, keys)]
    return group(columns, keys, closes)


def group(columns: Columns, keys: np.ndarray, closes: np.ndarray) -> Columns:
    """One bar per run of equal ``keys`` (its open time) closing at ``closes``, with the OHLCV reduction."""
    if not length(columns):
        return columns
    times = columns["time"]
    starts = np.flatnonzero(np.concatenate(([True], keys[1:] != keys[:-1])))
    result = {"time": keys[starts], "close_time": closes[starts]}
    ends = np.append(starts[1:], len(times)) - 1
    for name, values in columns.items():
        if name in ("time", "close_time"):
            continue
        if name == "open":
            result[name] = values[starts]
        elif name == "high":
            result[name] = np.fmax.reduceat(values, starts)
        elif name == "low":
            result[name] = np.fmin.reduceat(values, starts)
        elif name == "volume" or name.endswith("volume") or name in ("count", "turnover"):
            present = np.add.reduceat((~np.isnan(values)).astype(np.int64), starts) > 0
            total = np.add.reduceat(np.nan_to_num(values), starts)
            result[name] = np.where(present, clean_sum(total), np.nan)
        else:
            result[name] = values[ends]
    return result


def group_by_count(columns: Columns, size: int) -> Columns:
    """Full groups of size candles aligned to the newest candle. Use each group's actual first open and last close."""
    kept = take(columns, slice(length(columns) % size, None))
    if not length(kept):
        return kept
    keys = np.repeat(kept["time"][::size], size)
    closes = np.repeat(kept["close_time"][size - 1::size], size)
    result = group(kept, keys, closes)
    # An unknown component leaves the group's full volume unknown, including on price-only series.
    result["volume"][np.isnan(kept["volume"]).reshape(-1, size).any(axis=1)] = np.nan
    return result


def clean_sum(total: np.ndarray) -> np.ndarray:
    """Sums rounded to 12 significant digits (at most 8 decimals): 106259.58799999999 → 106259.588."""
    with np.errstate(divide="ignore", invalid="ignore"):
        magnitude = np.floor(np.log10(np.abs(total)))
    digits = np.clip(11 - np.nan_to_num(magnitude, nan=0.0, posinf=0.0, neginf=0.0), 0, 8).astype(np.int64)
    out = total.copy()
    for count in np.unique(digits):
        mask = digits == count
        out[mask] = np.round(total[mask], int(count))
    return out


# ── adjustment ──────────────────────────────────────────────────────────────

def multipliers(times: np.ndarray, events_time: np.ndarray, split_ratio: np.ndarray, dividend_factor: np.ndarray,
                adjustment: str, anchor: str | None) -> tuple[np.ndarray, np.ndarray]:
    """(price, volume) multipliers that turn raw bars at ``times`` into adjusted ones.

    ``split_ratio`` is new shares per old share at each ex-date (4 for 4:1, 1 when
    none); ``dividend_factor`` is 1 - dividend / previous raw close (1 when none).
    latest (default) keeps the newest price, earliest keeps the first.
    """
    ones = np.ones(len(times))
    if adjustment not in ("split", "split_dividend") or not len(events_time):
        return ones, ones
    order = np.argsort(events_time, kind="stable")
    events_time, split_ratio, dividend_factor = events_time[order], split_ratio[order], dividend_factor[order]
    price_step = 1.0 / split_ratio
    if adjustment == "split_dividend":
        price_step = price_step * dividend_factor
    # Number of events strictly after each bar (ex-date on or before the bar: not after).
    after = len(events_time) - np.searchsorted(events_time, times, "right")
    if anchor == "earliest":
        before = len(events_time) - after
        price_cum = np.concatenate(([1.0], np.cumprod(1.0 / price_step)))
        volume_cum = np.concatenate(([1.0], np.cumprod(1.0 / split_ratio)))
        return price_cum[before], volume_cum[before]
    price_cum = np.concatenate((np.cumprod(price_step[::-1])[::-1], [1.0]))
    volume_cum = np.concatenate((np.cumprod(split_ratio[::-1])[::-1], [1.0]))
    index = len(events_time) - after
    return price_cum[index], volume_cum[index]


def adjust(columns: Columns, events_time: np.ndarray, split_ratio: np.ndarray, dividend_factor: np.ndarray,
           adjustment: str, anchor: str | None) -> Columns:
    """Adjusted copy of raw bars (see ``multipliers``)."""
    if adjustment not in ("split", "split_dividend") or not length(columns) or not len(events_time):
        return columns
    price_mult, volume_mult = multipliers(columns["time"], events_time, split_ratio, dividend_factor, adjustment,
                                          anchor)
    result = dict(columns)
    for name in PRICE_COLUMNS:
        if name in result:
            result[name] = round_adjusted(result[name] * price_mult)
    if "volume" in result:
        result["volume"] = np.round(result["volume"] * volume_mult, 4)
    return result


def round_adjusted(values: np.ndarray) -> np.ndarray:
    """Adjusted prices keep 4 decimals, more below 1 so small prices keep 6 significant digits."""
    magnitude = np.floor(np.log10(np.abs(np.where(values == 0, 1.0, values))))
    decimals = np.clip(5 - magnitude, 4, 12).astype(np.int64)
    result = values.copy()
    for digits in np.unique(decimals):
        mask = decimals == digits
        result[mask] = np.round(values[mask], int(digits))
    return result


def unadjust_splits(columns: Columns, events_time: np.ndarray, split_ratio: np.ndarray, decimals: int = 4) -> Columns:
    """Undo a source's split adjustment (Yahoo): prices times, volume divided by the
    cumulative ratio of the splits after each bar."""
    if not length(columns):
        return columns
    if not len(events_time):
        # Still drop the source's float32 noise (416.8599853515625 is 416.86).
        return {**columns, **{name: round_raw(columns[name], decimals) for name in PRICE_COLUMNS if name in columns}}
    order = np.argsort(events_time)
    events_time, split_ratio = events_time[order], split_ratio[order]
    cum = np.concatenate((np.cumprod(split_ratio[::-1])[::-1], [1.0]))
    factor = cum[np.searchsorted(events_time, columns["time"], "right")]
    result = dict(columns)
    for name in PRICE_COLUMNS:
        if name in result:
            result[name] = round_raw(result[name] * factor, decimals)
    if "volume" in result:
        result["volume"] = np.round(result["volume"] / factor)
    return result


def round_raw(values: np.ndarray, decimals: int) -> np.ndarray:
    """``decimals`` places, more below 1 so small prices keep 6 significant digits."""
    with np.errstate(divide="ignore", invalid="ignore"):
        magnitude = np.floor(np.log10(np.abs(np.where((values == 0) | ~np.isfinite(values), 1.0, values))))
    places = np.clip(5 - magnitude, decimals, 12).astype(np.int64)
    result = values.copy()
    for digits in np.unique(places):
        mask = places == digits
        result[mask] = np.round(values[mask], int(digits))
    return result


# ── decimal text ────────────────────────────────────────────────────────────

def decimal_text(values: np.ndarray, text: pa.Array | None = None) -> list[str | None]:
    """Shortest exact decimal text of each float (no exponent); NaN becomes null. ``text`` is the values already cast
    to strings."""
    if not len(values):
        return []
    if text is None:
        text = _cast_text(values)
    result = text.to_pylist()
    exponent = pc.match_substring(text, "e")
    if pc.any(exponent).as_py():
        for i, flag in enumerate(exponent.to_pylist()):
            if flag:
                result[i] = np.format_float_positional(values[i], trim="-")
    return result


def ns_text(values: np.ndarray) -> list[str]:
    return pc.cast(pa.array(values, type=pa.int64()), pa.string()).to_pylist()


def parse_decimal(values) -> np.ndarray:
    """Decimal strings (or None) to float64."""
    return np.array([math.nan if value is None else float(value) for value in values], dtype=np.float64)


def _cast_text(values: np.ndarray) -> pa.Array:
    if values.dtype == np.int64:
        return pc.cast(pa.array(values, type=pa.int64()), pa.string())
    return pc.cast(pa.array(values, from_pandas=True), pa.string())


def _encoded_text(values: np.ndarray) -> TextColumn | None:
    """The column as one JSON array of decimal strings, straight from orjson's shortest round-trip numbers (the same
    digits Arrow and Python write); None when that is not possible (an exponent, an infinity)."""
    integer = values.dtype == np.int64
    missing = 0 if integer else int(np.isnan(values).sum())
    if not integer and missing < len(values) and np.isinf(values).any():
        return None
    raw = orjson.dumps(np.ascontiguousarray(values), option=orjson.OPT_SERIALIZE_NUMPY)
    if not integer and b"e" in raw:
        return None
    text = b'["' + raw[1:-1].replace(b",", b'","') + b'"]'
    if not integer:
        text = text.replace(b'.0"', b'"')  # 60000.0 is written 60000, as a decimal
        if missing:
            text = text.replace(b'"null"', b"null")
    return TextColumn(text.decode("ascii"))


def _column_text(values: np.ndarray, encoded: bool) -> list | TextColumn:
    """One column as text: the shortest decimal, written out digit by digit where Arrow used an exponent."""
    if encoded and len(values) and (column := _encoded_text(values)) is not None:
        return column
    text = _cast_text(values)
    if values.dtype != np.int64 and pc.any(pc.match_substring(text, "e")).as_py():
        listed = decimal_text(values, text)
        return TextColumn.from_arrow(pa.array(listed, pa.string())) if encoded else listed
    return TextColumn.from_arrow(text) if encoded else text.to_pylist()


def _short_text(values: np.ndarray) -> list:
    """A few values (a live row) as the same text, without Arrow: each Arrow call lets go of the GIL and, on the
    event loop while workers parse and write, waits up to a switch interval (5 ms) to get it back."""
    if values.dtype == np.int64:
        return [str(value) for value in values.tolist()]
    return [None if math.isnan(value) else np.format_float_positional(value, trim="-") for value in values.tolist()]


SHORT = 16
_texts = ThreadPoolExecutor(max_workers=4, thread_name_prefix="bars-text")


def to_wire(columns: Columns, *, encoded: bool = False) -> dict[str, list | TextColumn]:
    """Columns as vocabulary text: times as ns strings, numbers as decimals. ``encoded``: bar columns each as one
    ``TextColumn`` (what a public answer sends: orjson writes them, a third of a millisecond a column), else lists
    (Arrow formats long columns side by side, letting go of the GIL)."""
    names = list(columns)
    encoded = encoded and set(names) == set(BAR_COLUMNS)  # only bars are declared as encoded columns
    keys = ["closeTime" if name == "close_time" else name for name in names]
    if not encoded and all(len(values) <= SHORT and values.dtype in (np.int64, np.float64)
                           for values in columns.values()):
        texts = [_short_text(values) for values in columns.values()]
    elif not encoded and sum(len(values) for values in columns.values()) >= 20_000:
        texts = list(_texts.map(_column_text, columns.values(), [encoded] * len(names)))
    else:
        texts = [_column_text(values, encoded) for values in columns.values()]
    return dict(zip(keys, texts, strict=True))


def from_wire(columns: dict[str, list]) -> Columns:
    out = {}
    for key, values in columns.items():
        name = "close_time" if key == "closeTime" else key
        if name in ("time", "close_time") or name.endswith("Time") or name.endswith("_time"):
            out[name] = np.array([int(v) for v in values], dtype=np.int64)
        else:
            out[name] = parse_decimal(values)
    return out
