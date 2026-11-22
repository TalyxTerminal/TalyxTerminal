"""A factor's Work numbers in one vectorized pass: preprocessing, groups, rank IC by holding days, checks, likeness to the library.

Everything here works on wide arrays (bars × instruments) held in memory, so the research process can keep the data between runs and
answer a changed formula in a second or two. The measures themselves come from `metrics` (one definition for Quant); this file decides
what is measured on which names and days. Rank IC is the mean over days of the Spearman correlation between the value at t and the
return from t + delay to t + delay + holding, over the names that have both (the same as Alphalens' factor_information_coefficient).
"""
from __future__ import annotations

import json
import math
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from . import i18n
from . import metrics as M
from . import styles as S

MAD_SCALE = 1.4826
NEW_LISTING_DAYS = 60
LIQUIDITY_DAYS = 20
DECAY_DAYS = 20
WINDOW_NAMES = 3  # a day needs this many names with a value and a later return to give a rank IC

# Why a member has no value on a day; codes in the stored cross-section, words in the values table.
MISSING = {1: "Not a member that day", 2: "No price that day (halted?)", 3: "Listed under 60 days", 4: "Bottom 20% by dollar volume",
           5: "Not enough prices for the window", 6: "No industry or size", 7: "No value from the code",
           8: "Lost in clipping or scaling: no spread among the names that day"}

DEFAULT_SETTINGS = {"leaveOut": {"halted": True, "newListings": True, "illiquid": False}, "holding": 5, "delay": 1,
                    "grouping": {"method": "equal", "count": 5, "cuts": "-1, 0, 1", "withinIndustry": False},
                    "extremes": "mad3", "scale": "zscore", "neutralize": "industry+size",
                    "costBps": M.DEFAULT_TRADE_BPS, "borrowPct": M.DEFAULT_BORROW_PCT, "heldOutFrom": None,
                    # Alphalens' two options for the returns and the IC: take each day's mean off the returns; take each group's mean off (by $industry)
                    "demeaned": True, "groupAdjust": False}
KNOWN_SETTINGS = {"leaveOut", "holding", "delay", "grouping", "extremes", "scale", "neutralize", "costBps", "borrowPct", "heldOutFrom", "params",
                  "demeaned", "groupAdjust"}


# ---------------------------------------------------------------------------------------------------------------- data
class Panel:
    """The run's data as wide arrays: one row per bar label, one column per instrument, with membership, industry and size."""

    def __init__(self, labels, columns, fields, member, info, *, industry=None, shares=None, library=None, notes=None):
        self.labels = np.asarray(labels, dtype="int64")
        self.columns = list(columns)
        self.fields = fields  # name -> float array (T, N): open, high, low, close, volume
        self.member = member
        self.info = info
        self.shares = shares
        self.library = library or {}  # name -> {"version", "values": array (T, N)}
        self.notes = list(notes or [])
        self.dates = pd.to_datetime(self.labels, unit="ms", utc=True)
        # Each name's industry on each bar (dates × names, None where unknown), or None: Market's industry as known on that date
        # where it keeps dated answers, else its current one on every date. One label per name is the same on every date.
        self.industry = industry_frame(industry, self.dates, self.columns)
        self._derived = {}
        self._cache = {}

    def cached(self, key, compute):
        """What depends only on the data (and the key), computed once while the process keeps this panel."""
        if key not in self._cache:
            self._cache[key] = compute()
        return self._cache[key]

    @property
    def shape(self):
        return self.fields["close"].shape

    @property
    def annualization(self):
        return self.info["annualization"]

    @property
    def bars_a_day(self):
        """Bars in one trading day of this data (1 for daily bars, 24 for hourly crypto, about 7 for hourly stocks): thresholds written in days
        count this many bars a day."""
        return self.cached("barsADay", lambda: max(1, round(len(self.labels) / max(1, len(np.unique(self.labels // 86_400_000))))))

    @classmethod
    def from_bars(cls, bars, info, *, industry=None, shares=None, library=None, notes=None):
        """From the long table research runs freeze (label, column, open … volume, isMember for index members)."""
        columns = [item["column"] for item in info["instruments"]]
        labels = np.sort(bars["label"].unique())
        rows = np.searchsorted(labels, bars["label"].to_numpy())
        where = {name: index for index, name in enumerate(columns)}
        cols = bars["column"].map(where).to_numpy()
        keep = ~pd.isna(cols)
        rows, cols = rows[keep], cols[keep].astype(int)
        shape = (len(labels), len(columns))
        fields = {}
        for name in ("open", "high", "low", "close", "volume", "rawClose", "rawVolume"):
            array = np.full(shape, np.nan)
            array[rows, cols] = bars[name].to_numpy(dtype=float)[keep]
            fields[name] = array
        if "isMember" in bars:
            member = np.zeros(shape, dtype=bool)
            member[rows, cols] = bars["isMember"].to_numpy(dtype=bool)[keep]
        else:
            # A chosen symbol belongs from its first bar to its last; a missing bar in between is a halt, not a departure.
            have = ~np.isnan(fields["close"])
            first = np.where(have.any(axis=0), have.argmax(axis=0), shape[0])
            last = np.where(have.any(axis=0), shape[0] - 1 - have[::-1].argmax(axis=0), -1)
            index = np.arange(shape[0])[:, None]
            member = (index >= first[None, :]) & (index <= last[None, :])
        return cls(labels, columns, fields, member, info, industry=industry, shares=shares, library=library, notes=notes)

    def truncate(self, end):
        """The same data up to (not including) the bar label `end`: what a run sees when the held-out period starts there."""
        keep = self.labels < end
        if keep.all():
            return self
        # Kept, with what it has computed (styles, sort orders): every run with this held-out start reads the same truncated data.
        return self.cached(("truncated", int(end)), lambda: self._truncated(int(keep.sum())))

    def _truncated(self, cut):
        panel = Panel(self.labels[:cut], self.columns, {k: v[:cut] for k, v in self.fields.items()}, self.member[:cut], self.info,
                      industry=None if self.industry is None else self.industry.iloc[:cut], shares=None if self.shares is None else self.shares[:cut],
                      library={k: {**v, "values": v["values"][:cut]} for k, v in self.library.items()}, notes=self.notes)
        return panel

    def derived(self, name):
        """Fields computed from bars: average price, dollar volume, market value (close × shares) or, without shares, 30-day dollar volume."""
        if name in self._derived:
            return self._derived[name]
        f = self.fields
        if name == "vwap":
            value = (f["high"] + f["low"] + f["close"]) / 3
        elif name == "amount":
            value = f["rawClose"] * f["rawVolume"]
        elif name == "size":
            if self.shares is not None:
                value = f["rawClose"] * self.shares
            else:
                value = pd.DataFrame(self.derived("amount")).rolling(30 * self.bars_a_day, min_periods=10 * self.bars_a_day).mean().to_numpy()
        elif name == "shares":
            value = self.shares if self.shares is not None else np.full(self.shape, np.nan)
        elif name == "returns":
            # A halted name holds its last price until it trades again, so its first bar back carries the whole move over the halt; after
            # its last price (delisted) it has no return: a book holding it is out at its last close and earns nothing after (returns_of
            # counts the missing return as 0, as forward returns hold the last close, M.held).
            frame = pd.DataFrame(f["close"])
            close = frame.ffill().where(frame.bfill().notna()).to_numpy()
            with np.errstate(invalid="ignore", divide="ignore"):
                value = np.vstack([np.full((1, close.shape[1]), np.nan), close[1:] / close[:-1] - 1])
            value[~np.isfinite(value)] = np.nan
        else:
            raise KeyError(name)
        self._derived[name] = value
        return value

    def frame(self, array):
        return pd.DataFrame(array, index=self.dates, columns=self.columns)

    def tables(self):
        """Every field a formula reads, as wide tables by `$name`."""
        out = {f"${name}": self.frame(self.fields[name]) for name in ("open", "high", "low", "close", "volume")}
        for name in ("vwap", "amount", "size"):
            out[f"${name}"] = self.frame(self.derived(name))
        if self.shares is not None:
            out["$shares"] = self.frame(self.shares)
        for name, item in self.library.items():
            out[f"$lib.{name}"] = self.frame(item["values"])
        return out

    def industry_codes(self, unknown=None):
        """Each cell's industry as an integer (T, N) and the names: a cell without one is -1, or the code of `unknown` when given."""
        if self.industry is None:
            return None
        return self.cached(("industry-codes", unknown), lambda: label_codes(self.industry, unknown))


def industry_frame(industry, dates, columns):
    """An industry as a table of dates × names: a table as it is, one label per name on every date, None stays None."""
    if industry is None:
        return None
    if isinstance(industry, pd.DataFrame):
        return pd.DataFrame(industry.to_numpy(dtype=object), index=dates, columns=columns)
    array = np.asarray(industry, dtype=object)
    if array.ndim == 1:
        array = np.broadcast_to(array, (len(dates), len(columns)))
    return pd.DataFrame(np.array(array, dtype=object), index=dates, columns=columns)


def label_codes(industry, unknown=None):
    """(codes (T, N) int, names) of a table of labels: None is -1, or the code of `unknown` when given."""
    cells = industry.to_numpy(dtype=object) if isinstance(industry, pd.DataFrame) else np.asarray(industry, dtype=object)
    if unknown is not None:
        cells = np.where(pd.isna(cells), unknown, cells)
    codes, names = pd.factorize(cells.ravel())
    return codes.reshape(cells.shape).astype(np.int32), list(names)


def industry_segments(codes):
    """Runs of bars on which every name keeps its industry: [(first bar, end bar, the codes on them)]; industries change on few dates, so
    work by industry runs over a few column sets."""
    change = np.flatnonzero((codes[1:] != codes[:-1]).any(axis=1)) + 1 if len(codes) > 1 else np.asarray([], dtype=int)
    bounds = [0, *change.tolist(), len(codes)]
    return [(first, end, codes[first]) for first, end in zip(bounds[:-1], bounds[1:], strict=True) if end > first]


def industry_table(labels, columns, histories):
    """Each name's industry on each bar from its history (`histories`: {column: [[from ms | None, until ms | None, name], ...]}, oldest
    first): a bar takes the row whose span holds its label; None where no row does (before the first known one, or after the last
    ended). A name given as one label (data frozen by TALYX 0.1, which kept only the current classification) has it on every bar."""
    labels = np.asarray(labels, dtype="int64")
    table = np.full((len(labels), len(columns)), None, dtype=object)
    for index, column in enumerate(columns):
        history = histories.get(column)
        if isinstance(history, str):
            table[:, index] = history
            continue
        for start, end, name in history or ():
            inside = np.ones(len(labels), dtype=bool)
            if start is not None:
                inside &= labels >= start
            if end is not None:
                inside &= labels < end
            table[inside, index] = name
    return table


def library_table(labels, columns, rows, clocks):
    """A library factor's values on the bars: each value at the first bar of its name, at or after its own, whose close is known
    (`clocks`, the bars' knownAt) when the value was available; a value filled in late enters late, one available after the data ends
    not at all, and a newer value on the same bar wins over an older late one. `rows`: column, label, availableAt, value."""
    table = np.full((len(labels), len(columns)), np.nan)
    where = {column: index for index, column in enumerate(columns)}
    for column, found in rows.sort_values("label", kind="stable").groupby("column", sort=False):
        index = where.get(column)
        if index is None:
            continue
        bars = np.flatnonzero(np.isfinite(clocks[:, index]))
        known = np.maximum.accumulate(clocks[bars, index])
        first = np.maximum(np.searchsorted(bars, np.searchsorted(labels, found["label"].to_numpy(), side="left"), side="left"),
                           np.searchsorted(known, found["availableAt"].to_numpy(), side="left"))
        inside = first < len(bars)
        target = pd.Series(found["value"].to_numpy(dtype=float)[inside], index=bars[first[inside]])
        target = target[~target.index.duplicated(keep="last")]  # sorted by label: the newest value of a bar wins
        table[target.index.to_numpy(), index] = target.to_numpy()
    return table


def shares_table(labels, columns, observations, *, clocks=None):
    """Raw shares known by each bar: observations are (announcement time ms, shares); before the first stays missing."""
    table = np.full((len(labels), len(columns)), np.nan)
    for index, column in enumerate(columns):
        found = observations.get(column)
        if not found:
            continue
        days = np.asarray([item[0] for item in found], dtype="int64")
        values = np.asarray([item[1] for item in found], dtype=float)
        order = np.argsort(days)
        days, values = days[order], values[order]
        at = labels if clocks is None else clocks[:, index]
        position = np.searchsorted(days, at, side="right") - 1
        known = (position >= 0) & np.isfinite(at)
        table[known, index] = values[position[known]]
    return table


def members_a_day(labels, index):
    """How many names the index held on each bar, those without a Market instrument or prices included (None for a list of symbols)."""
    if index is None:
        return None
    intervals = sorted(index["intervals"], key=lambda item: item["since"])
    starts = np.asarray([item["since"] for item in intervals], dtype="int64")
    counts = np.asarray([len({index["members"][position]["assetId"] for position in item["members"]}) for item in intervals] + [0])
    ends = np.asarray([item["until"] for item in intervals], dtype="int64")
    at = np.searchsorted(starts, labels, side="right") - 1
    inside = (at >= 0) & (labels <= ends[np.clip(at, 0, None)])
    return np.where(inside, counts[np.where(inside, at, -1)], 0)


# ---------------------------------------------------------------------------------------------------------------- settings
def known_settings(settings):
    """What an object saved long ago may still hold in its run settings, without the keys this version does not know (nested ones too)."""
    out = {}
    for key, value in (settings or {}).items():
        if key not in KNOWN_SETTINGS:
            continue
        out[key] = {sub: item for sub, item in value.items() if sub in DEFAULT_SETTINGS[key]} if isinstance(value, dict) and isinstance(DEFAULT_SETTINGS.get(key), dict) else value
    return out


def settings_of(settings):
    """The run settings with every default filled in and the numbers checked."""
    merged = json.loads(json.dumps(DEFAULT_SETTINGS))
    for key, value in (settings or {}).items():
        if key not in KNOWN_SETTINGS:
            raise ValueError(f"Unknown run setting: {key}")
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            for sub in value:
                if sub not in merged[key]:
                    raise ValueError(f"Unknown run setting: {key}.{sub}")
            merged[key].update(value)
        else:
            merged[key] = value
    merged["holding"] = int(merged["holding"])
    merged["delay"] = int(merged["delay"])
    merged["grouping"]["count"] = int(merged["grouping"]["count"])
    if not 1 <= merged["holding"] <= 60:
        raise ValueError("Holding is 1 to 60 days")
    if merged["delay"] not in (0, 1):
        raise ValueError("Delay is 0 or 1 day")
    if not 2 <= merged["grouping"]["count"] <= 50:
        raise ValueError("Groups is 2 to 50")
    return merged


# ---------------------------------------------------------------------------------------------------------------- universe and preprocessing
def eligibility(panel, leave_out):
    """Who may have a value each day, and why each other member may not (codes in MISSING)."""
    close, volume, member = panel.fields["close"], panel.fields["volume"], panel.member
    reason = np.zeros(panel.shape, dtype=np.int8)
    reason[~member] = 1
    no_price = member & (np.isnan(close) | (np.nan_to_num(volume, nan=0.0) <= 0))
    if leave_out.get("halted", True):
        reason[no_price & (reason == 0)] = 2
    else:
        reason[member & np.isnan(close) & (reason == 0)] = 2
    if leave_out.get("newListings", True):
        have = ~np.isnan(close)
        count = np.cumsum(have, axis=0)
        # A name priced on the first bar of the data was listed before it; only later starts are new listings.
        started_before = have[0][None, :]
        young = (count < NEW_LISTING_DAYS * panel.bars_a_day) & ~started_before
        reason[young & (reason == 0)] = 3
    if leave_out.get("illiquid", False):
        dollar = pd.DataFrame(panel.derived("amount")).rolling(LIQUIDITY_DAYS * panel.bars_a_day, min_periods=5 * panel.bars_a_day).mean().to_numpy()
        dollar = np.where(reason == 0, dollar, np.nan)
        rank = pd.DataFrame(dollar).rank(axis=1, pct=True).to_numpy()
        reason[(rank <= 0.2) & (reason == 0)] = 4
    return reason


def _row_mad_clip(x):
    with np.errstate(invalid="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            median = np.nanmedian(x, axis=1)[:, None]
            mad = np.nanmedian(np.abs(x - median), axis=1)[:, None] * MAD_SCALE
    low, high = median - 3 * mad, median + 3 * mad
    return np.where(np.isnan(x), np.nan, np.clip(x, low, high))


def _row_pct_clip(x):
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        low, high = np.nanpercentile(x, 1, axis=1)[:, None], np.nanpercentile(x, 99, axis=1)[:, None]
    return np.where(np.isnan(x), np.nan, np.clip(x, low, high))


def _row_zscore(x):
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        mean = np.nanmean(x, axis=1)[:, None]
        std = np.nanstd(x, axis=1, ddof=1)[:, None]
    with np.errstate(invalid="ignore", divide="ignore"):
        return (x - mean) / std


def preprocess(raw, panel, settings, reason):
    """Raw values to the values that are tested: leave out who may not have one, clip extremes, scale, take out industry and size.
    Returns the values and the updated reasons."""
    x = np.where(reason == 0, raw, np.nan)
    reason = reason.copy()
    reason[(reason == 0) & np.isnan(x)] = 5
    had = ~np.isnan(x)
    if settings["extremes"] == "mad3":
        x = _row_mad_clip(x)
    elif settings["extremes"] == "pct1":
        x = _row_pct_clip(x)
    if settings["scale"] == "zscore":
        x = _row_zscore(x)
    elif settings["scale"] == "rank":
        x = M.rank_rows(x) / np.maximum(np.sum(~np.isnan(x), axis=1), 1)[:, None]
    reason[had & np.isnan(x) & (reason == 0)] = 8
    neutral = settings["neutralize"]
    if neutral != "none":
        from .formula import neutralize
        industry = panel.industry
        if industry is None:
            label = "Industry + size" if neutral == "industry+size" else "Industry"
            raise ValueError(f"Neutralize {label} needs an industry for each name, and Market gives none for these names")
        size = None
        if neutral == "industry+size":
            value = panel.derived("size")
            with np.errstate(invalid="ignore", divide="ignore"):
                size = pd.DataFrame(np.log(np.where(value > 0, value, np.nan)), index=panel.dates, columns=panel.columns)
        frame = pd.DataFrame(x, index=panel.dates, columns=panel.columns)
        before = ~np.isnan(x)
        x = neutralize(frame, industry, size).to_numpy(copy=True)  # writable under copy-on-write pandas too (held-out scoring blanks early days in place)
        reason[before & np.isnan(x) & (reason == 0)] = 6
    return x, reason


# ---------------------------------------------------------------------------------------------------------------- groups and returns
def group_of(values, settings, industry=None):
    """Each name's group each day (1 = lowest value), 0 where it has none."""
    grouping = settings["grouping"]
    count = grouping["count"]
    method = grouping["method"]
    out = np.zeros(values.shape, dtype=np.int16)
    have = ~np.isnan(values)

    def equal(x, n):
        ranks = M.rank_rows(x)
        size = np.sum(~np.isnan(x), axis=1)[:, None].astype(float)
        with np.errstate(invalid="ignore"):
            g = np.floor((ranks - 1) * n / size) + 1
        return np.where(np.isnan(x), 0, g).astype(np.int16)

    if method == "cuts":
        cuts = sorted(float(item) for item in str(grouping["cuts"]).split(",") if item.strip())
        out = np.where(have, np.digitize(np.nan_to_num(values), cuts) + 1, 0).astype(np.int16)
        return out
    if method == "sign":
        half = max(1, count // 2)
        negative = np.where(values < 0, values, np.nan)
        positive = np.where(values >= 0, values, np.nan)
        out = equal(negative, half)
        upper = equal(positive, count - half)
        return np.where(upper > 0, upper + half, out).astype(np.int16)
    if grouping.get("withinIndustry"):
        if industry is None:
            raise ValueError("Grouping inside each industry needs an industry for each name, and Market gives none for these names")
        # Each day inside each industry of that day (names without one together).
        codes = label_codes(industry, "__none__")[0]
        rows = values.shape[0]
        key = (codes + np.arange(rows)[:, None] * (int(codes.max()) + 1)).ravel()
        flat = pd.Series(values.ravel())
        ranks = flat.groupby(key).rank(method="average").to_numpy().reshape(values.shape)
        size = flat.notna().groupby(key).transform("sum").to_numpy(dtype=float).reshape(values.shape)
        with np.errstate(invalid="ignore", divide="ignore"):
            g = np.floor((ranks - 1) * count / size) + 1
        return np.where(np.isnan(values), 0, g).astype(np.int16)
    return equal(values, count)


def group_count(groups, settings):
    method = settings["grouping"]["method"]
    if method == "cuts":
        return len([item for item in str(settings["grouping"]["cuts"]).split(",") if item.strip()]) + 1
    return settings["grouping"]["count"]


def _held_fast(weights, holding, delay):
    cumulative = np.cumsum(np.vstack([np.zeros((1, weights.shape[1])), weights]), axis=0)
    total = len(weights)
    s = np.arange(total)
    hi = s - delay - 1
    lo = np.maximum(hi - holding + 1, 0)
    held = cumulative[np.clip(hi + 1, 0, total)] - cumulative[lo]
    held[hi < 0] = 0.0
    return held / holding


def equal_weights(mask):
    count = mask.sum(axis=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(mask, 1.0 / np.maximum(count, 1), 0.0)


def returns_of(panel, groups, count, settings, values, codes=None):
    """Daily returns of each group relative to what the settings say (nothing for Raw, all names with a value for Demeaned, each name's
    industry among them for Group neutral, with `codes`), and the long–short (top minus bottom) gross, cost, borrow and net of the book itself.
    Each day's positions are held `holding` days in overlapping tranches; trading costs fall on the change of the held weights."""
    holding, delay = settings["holding"], settings["delay"]
    returns = np.nan_to_num(panel.derived("returns"))
    universe = _held_fast(equal_weights(~np.isnan(values)), holding, delay)
    if codes is not None and settings.get("groupAdjust"):
        # Each name against the held names of its own industry that day (`codes`: each name's industry each day, or one per name).
        codes = np.broadcast_to(codes, returns.shape)
        base = np.zeros_like(returns)
        for code in np.unique(codes):
            inside = codes == code
            weight = (universe * inside).sum(axis=1)
            with np.errstate(invalid="ignore", divide="ignore"):
                mean = np.where(weight > 0, (universe * returns * inside).sum(axis=1) / weight, 0.0)
            base = np.where(inside, mean[:, None], base)
    elif settings.get("demeaned", True):
        base = (universe * returns).sum(axis=1)[:, None]
    else:
        base = 0.0
    relative = np.zeros((len(returns), count))
    for g in range(1, count + 1):
        held = _held_fast(equal_weights(groups == g), holding, delay)
        relative[:, g - 1] = (held * (returns - base)).sum(axis=1)
    top = _held_fast(equal_weights(groups == count), holding, delay)
    bottom = _held_fast(equal_weights(groups == 1), holding, delay)
    book = top - bottom
    gross = (book * returns).sum(axis=1)
    traded = np.abs(np.diff(book, axis=0, prepend=np.zeros((1, book.shape[1])))).sum(axis=1)
    cost = settings["costBps"] / 1e4 * traded
    borrow = M.borrow_per_bar(settings["borrowPct"], panel.annualization) * bottom.sum(axis=1)
    active = book.any(axis=1)
    net = gross - cost - borrow
    for series in (gross, net, cost, borrow, traded):
        series[~active] = np.nan
    relative[~active] = np.nan
    return {"relative": relative, "gross": gross, "net": net, "cost": cost, "borrow": borrow, "traded": traded, "active": active}


# ---------------------------------------------------------------------------------------------------------------- numbers
def group_demeaned(forward, values, codes):
    """Each day's forward returns less the mean of the names in the same group that day (Alphalens' group_adjust): the names with a value and a return count."""
    out = np.array(forward, dtype=float, copy=True)
    codes = np.broadcast_to(codes, out.shape)
    counted = np.where(np.isfinite(values) & np.isfinite(forward), forward, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for code in np.unique(codes):
            inside = codes == code
            out = np.where(inside, forward - np.nanmean(np.where(inside, counted, np.nan), axis=1, keepdims=True), out)
    return out


def rank_ic(values, close, holding, delay, *, ranked=None, codes=None, basis=None, panel=None):
    """The rank IC each day, and the forward returns it used (the raw ones: with `codes`, the IC is against returns less their group's mean).

    With the values' `basis` (an `M.RankBasis`) and the `panel` whose closes these are, the returns' sort order is kept with the data
    (it depends on nothing else), so a run sorts only its own values: the same numbers, without sorting the returns of every holding again."""
    forward = M.forward_returns(close, holding, delay)
    if codes is None and basis is not None and panel is not None:
        theirs = panel.cached(("forward-rank-basis", holding, delay), lambda: M.RankBasis(forward))
        return M.rank_correlation_on(values, basis, forward, theirs, WINDOW_NAMES), forward
    against = forward if codes is None else group_demeaned(forward, values, codes)
    return M.rank_correlation(values, against, WINDOW_NAMES), forward


def _pool():
    global _POOL
    if _POOL is None:
        import os
        from concurrent.futures import ThreadPoolExecutor
        _POOL = ThreadPoolExecutor(max_workers=max(2, min(8, os.cpu_count() or 2)), thread_name_prefix="factor-work")
    return _POOL


_POOL = None


def rank_ics(values, close, holdings, delay, codes=None, *, basis=None, panel=None):
    """The daily rank IC series and forward returns for each holding, in parallel threads (ranking releases the interpreter lock)."""
    jobs = {h: _pool().submit(rank_ic, values, close, h, delay, codes=codes, basis=basis, panel=panel) for h in holdings}
    return {h: job.result() for h, job in jobs.items()}


def turnover_of(groups, count, holding):
    top, bottom = groups == count, groups == 1
    shares = [M.replaced_share(top, holding), M.replaced_share(bottom, holding)]
    found = [M.mean(item) for item in shares]
    found = [item for item in found if item is not None]
    return float(np.mean(found)) if found else None


def _iso(dates):
    return [stamp.strftime("%Y-%m-%d") for stamp in dates]


def groups_figure(dates, relative, net, *, key="factor.groups", compared=None, compared_label=None, step=1):
    """Each group's cumulative return against all names, and the long–short net of costs; the end of each line names it."""
    keep = np.arange(0, len(dates), step)
    if len(dates) and keep[-1] != len(dates) - 1:
        keep = np.append(keep, len(dates) - 1)
    x = _iso(dates[keep])
    count = relative.shape[1]
    traces, annotations = [], []
    for g in range(count):
        path = M.compounded(relative[:, g])[keep]
        traces.append({"type": "scatter", "mode": "lines", "x": x, "y": [round(float(v), 6) for v in path], "name": f"Q{g + 1}",
                       "meta": {"role": "group", "key": f"group:{g + 1}", "index": g, "of": count},
                       "hovertemplate": f"Q{g + 1} %{{y:+.1%}}<extra></extra>"})
        if count <= 10 or g in (0, count - 1) or (g + 1) % 5 == 0:
            annotations.append({"x": x[-1], "y": float(path[-1]), "text": f"Q{g + 1}", "name": f"group:{g + 1}", "showarrow": False, "xanchor": "left", "xshift": 4})
    ls = M.compounded(net)[keep]
    traces.append({"type": "scatter", "mode": "lines", "x": x, "y": [round(float(v), 6) for v in ls], "name": "L–S net",
                   "meta": {"role": "main", "key": "ls-net"}, "hovertemplate": "L–S net %{y:+.1%}<extra></extra>"})
    annotations.append({"x": x[-1], "y": float(ls[-1]), "text": "L–S", "name": "ls-net", "showarrow": False, "xanchor": "left", "xshift": 4})
    if compared is not None:
        traces.append({"type": "scatter", "mode": "lines", "x": compared["x"], "y": compared["y"], "name": compared_label or "Compared",
                       "meta": {"role": "compare", "key": "ls-net:compared"}, "hovertemplate": f"{compared_label or 'Compared'} %{{y:+.1%}}<extra></extra>"})
    return {"data": traces, "layout": {"meta": {"key": key}, "showlegend": False, "hovermode": "x unified", "annotations": annotations,
                                       "margin": {"l": 8, "r": 44, "t": 8, "b": 22}, "yaxis": {"tickformat": ".0%", "side": "right"},
                                       "xaxis": {"type": "date"}}}


def decay_figure(values, holding, *, key="factor.decay"):
    """Rank IC for holding 1 to 20 days as bars; the bar of the holding this run used is the main one. Clicking a bar picks that holding."""
    days = [f"{day}D" for day in range(1, len(values) + 1)]
    rest = [None if day == holding else (None if value is None else round(value, 6)) for day, value in zip(range(1, len(values) + 1), values, strict=True)]
    chosen = [round(value, 6) if day == holding and value is not None else None for day, value in zip(range(1, len(values) + 1), values, strict=True)]
    hover = "Holding %{x}<br>Rank IC %{y:+.4f}<extra></extra>"
    # No key: the title says rank IC and the run's holding is written over its bar at the top of the plot, as the draft draws it; on the bar's own top it
    # sat on the taller bars beside it.
    return {"data": [{"type": "bar", "x": days, "y": rest, "name": "Rank IC", "showlegend": False, "meta": {"role": "detail", "key": "decay"}, "hovertemplate": hover},
                     {"type": "bar", "x": days, "y": chosen, "name": f"Holding {holding}D", "showlegend": False, "meta": {"role": "main", "key": "decay:holding"}, "hovertemplate": hover}],
            "layout": {"meta": {"key": key}, "barmode": "overlay", "showlegend": False, "bargap": 0.28,
                       "annotations": [{"x": f"{holding}D", "y": 1, "yref": "paper", "yanchor": "bottom", "text": f"{holding}D", "showarrow": False}] if holding <= len(values) else [],
                       "margin": {"l": 8, "r": 40, "t": 16, "b": 22}, "yaxis": {"side": "right", "tickformat": ".3f"},
                       "xaxis": {"type": "category", "tickvals": ["1D", "5D", "10D", "15D", "20D"]}}}


# ---------------------------------------------------------------------------------------------------------------- checks
def _fmt(value, digits=2):
    return "—" if value is None else f"{value:,.{digits}f}"


def checks_of(numbers, details, settings, *, formula, future=None, library=None):
    """The five site-wide thresholds on in-sample numbers, then the factor's own four: group order, future data, coverage drop, suspicious prices."""
    T = M.THRESHOLDS
    out = []

    def add(identity, name, value, need, passed, title, go):
        out.append({"id": identity, "name": i18n.t(name), "value": value, "need": need,
                    "state": "notrun" if passed is None else "ok" if passed else "watch", "title": title, "go": go})

    t = numbers["t"]
    add("significance", T["significance"]["name"], _fmt(t), T["significance"]["need"], None if t is None else M.passes("significance", t),
        i18n.t("t, Newey–West, for overlapping {days}-day returns", days=settings["holding"]), {"to": "results", "id": "ic"})
    like = numbers.get("mostLikeLibrary")
    if library:
        rho = abs(like["rho"]) if like else None
        add("library", T["library"]["name"], _fmt(rho), T["library"]["need"], None if rho is None else M.passes("library", rho),
            i18n.t("Closest: {name}", name=like["name"]) if like else i18n.t("No library factor shares days and names with this run"), {"to": "tab", "id": "similar"})
    else:
        # with nothing published there is nothing for the factor to repeat: the check passes and says why, not "Not run"
        add("library", T["library"]["name"], i18n.t("Library empty"), T["library"]["need"], True, i18n.t("Nothing is published in the library yet"), {"to": "tab", "id": "similar"})
    size = numbers.get("groupSize")
    add("group_size", T["group_size"]["name"], _fmt(size, 0), T["group_size"]["need"], None if size is None else M.passes("group_size", size),
        i18n.t("Names in the smallest group, median day"), {"to": "setting", "id": "groups"})
    years = [item for item in details["years"] if item["whole"]]
    sign = math.copysign(1, numbers["rankIc"]) if numbers["rankIc"] else 0
    same = sum(1 for item in years if sign and math.copysign(1, item["rankIc"]) == sign)
    share = same / len(years) if years else None
    add("every_year", T["every_year"]["name"], i18n.t("{same} of {years}", same=same, years=len(years)) if years else i18n.t("no whole year"), T["every_year"]["need"],
        None if share is None else M.passes("every_year", share), i18n.t("Whole years whose rank IC has the sign of the whole period; each year: rank IC above, long–short net of cost below"), {"to": "results", "id": "ic"})
    # Each whole year's IC and long–short net beside the count: whether the years that held also made money after cost.
    out[-1]["years"] = [{"year": item["year"], "rankIc": item["rankIc"], "longShortNet": item["longShortNet"]} for item in years]
    breakeven = numbers.get("breakEven")
    cost = settings["costBps"]
    add("cost_room", T["cost_room"]["name"], "—" if breakeven is None else i18n.t("loses before costs") if breakeven < 0 else i18n.t("{value} bps", value=f"{breakeven:,.1f}"), i18n.t("≥ {value} bps", value=f"{2 * cost:g}"),
        None if breakeven is None else M.passes("cost_room", breakeven, cost), i18n.t("Break-even cost a trade, against twice the {cost} bps assumed", cost=f"{cost:g}"),
        {"to": "results", "id": "costs"})
    means = details["groupMeans"]
    direction = 1 if numbers["rankIc"] is None or numbers["rankIc"] >= 0 else -1
    broken = next((k for k in range(1, len(means)) if means[k] is not None and means[k - 1] is not None and (means[k] - means[k - 1]) * direction < 0), None)
    add("group_order", i18n.mark("Group order"), i18n.t("in order") if broken is None else f"Q{broken} {'>' if direction > 0 else '<'} Q{broken + 1}", i18n.t("in order"),
        broken is None, i18n.t("Mean return rises from Q1 to the top group") if direction > 0 else i18n.t("Mean return falls from Q1 to the top group"), {"to": "results", "id": "returns"})
    if formula:
        add("future_data", i18n.mark("Future data"), "none", "none", True, i18n.t("A formula reads only earlier days: shifts into later days are refused"), None)
    elif future is None:
        add("future_data", i18n.mark("Future data"), "", "none", None, i18n.t("Recomputed at 3 dates without later prices after the numbers"), None)
    else:
        add("future_data", i18n.mark("Future data"), future["value"], "none", future["passed"], future["title"],
            {"to": "code", "line": future["line"]} if future.get("line") else None)
    drop = details["coverage"]
    add("coverage_drop", i18n.mark("Coverage drop"), "—" if drop["drop"] is None else f"{drop['drop'] * 100:.0f}%", i18n.t("under 20%"),
        None if drop["drop"] is None else drop["drop"] < 0.2,
        i18n.t("Worst day against the usual count of names: {day}", day=drop["day"]) if drop["day"] else i18n.t("Names with a value each day after the warm-up"),
        {"to": "tab", "id": "values", "order": "missing", **({"date": drop["label"]} if drop.get("label") else {})})
    flags = details["flags"]
    add("suspicious_prices", i18n.mark("Suspicious prices"), str(len(flags)), "none", not flags,
        flags[0]["text"] if flags else i18n.t("No daily move of -50% or +100% on normal dollar volume"), {"to": "flag"} if flags else None)
    return out


def coverage_of(dates, names):
    """The worst day's share of names missing against the median day, after the warm-up (until the count first reaches 80% of the median)."""
    counts = np.asarray(names, dtype=float)
    if not len(counts) or not counts.any():
        return {"drop": None, "day": None}
    usual = float(np.median(counts[counts > 0]))
    start = int(np.argmax(counts >= 0.8 * usual))
    tail = counts[start:]
    if not len(tail) or usual <= 0:
        return {"drop": None, "day": None}
    worst = int(np.argmin(tail)) + start
    return {"drop": max(0.0, 1 - counts[worst] / usual), "day": f"{dates[worst].strftime('%b')} {dates[worst].day}, {dates[worst].year}",
            "label": int(dates[worst].value // 1_000_000)}


def suspicious(panel, limit=50):
    """Daily moves of -50% or worse, or +100% or more, on dollar volume within half to twice its 20-day median: more like a missed split than news."""
    returns = panel.derived("returns")
    amount = panel.derived("amount")
    day = panel.bars_a_day
    try:
        import bottleneck
        usual = np.vstack([np.full((1, amount.shape[1]), np.nan), bottleneck.move_median(amount, 20 * day, min_count=10 * day, axis=0)[:-1]])
    except ImportError:  # pragma: no cover - the research environment has bottleneck
        usual = pd.DataFrame(amount).rolling(20 * day, min_periods=10 * day).median().shift(1).to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        normal = (amount >= 0.5 * usual) & (amount <= 2 * usual)
    hits = np.argwhere(((returns <= -0.5) | (returns >= 1.0)) & normal & panel.member)
    out = []
    for t, i in hits[:limit]:
        move = returns[t, i]
        day = panel.dates[t]
        wording = (i18n.mark("{symbol} fell {change}% on {month} {day}, {year} on normal dollar volume") if move < 0
                   else i18n.mark("{symbol} rose {change}% on {month} {day}, {year} on normal dollar volume"))
        out.append({"t": int(t), "i": int(i), "label": int(panel.labels[t]),
                    "text": i18n.t(wording, symbol=panel.columns[i], change=f"{abs(move) * 100:.0f}",
                                   month=day.strftime(i18n.t("%b")), day=day.day, year=day.year)})
    return out


# ---------------------------------------------------------------------------------------------------------------- the library
def similar(values, groups, count, panel, *, holding, delay, close, basis=None):
    """Each library factor's mean daily rank correlation with these values, the share of names both put in their top group, and its own rank IC.

    The library factors' and the styles' sort orders are kept with the data (they do not depend on the run); with the values' `basis` a run
    sorts only its own values once for all of them."""
    basis = basis if basis is not None else M.RankBasis(values)

    def correlation(key, other):
        theirs = panel.cached(key, lambda: M.RankBasis(other))
        return M.mean(M.rank_correlation_on(values, basis, other, theirs, 10))

    def one(name, item):
        other = item["values"]
        rho = correlation(("library-rank-basis", name, item.get("version")), other)
        if rho is None:
            return None
        theirs = panel.cached(("library-groups", name, count), lambda: group_of(other, {"grouping": {"method": "equal", "count": count, "withinIndustry": False}}))
        top_mine, top_theirs = groups == count, theirs == count
        both = (top_mine & top_theirs).sum(axis=1)
        mine = top_mine.sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            overlap = np.where(mine > 0, both / np.maximum(mine, 1), np.nan)
        ic = panel.cached(("library-ic", name, holding, delay), lambda: M.mean(rank_ic(other, close, holding, delay)[0]))
        return {"name": name, "version": item.get("version", ""), "rho": rho, "topOverlap": M.finite(np.nanmedian(overlap)) if np.isfinite(overlap).any() else None,
                "rankIc": ic, "usedBy": item.get("usedBy", [])}

    jobs = [_pool().submit(one, name, item) for name, item in (panel.library or {}).items()]
    # A style without the data it needs has no values (it is left out below); anything else that goes wrong stops the run and says so.
    found = S.price_styles(panel)
    styled = {name: _pool().submit(correlation, ("style-rank-basis", S.VERSION, name), other) for name, other in found.items()}
    out = [item for item in (job.result() for job in jobs) if item is not None]
    # The price styles are compared in the same list, from the one definition Results' Style tab uses (styles.py); a style that cannot be computed is left out here.
    for name, job in styled.items():
        rho = job.result()
        if rho is not None:
            out.append({"name": name, "version": "Style", "rho": rho, "topOverlap": None, "rankIc": None, "usedBy": [], "style": True})
    out.sort(key=lambda entry: -abs(entry["rho"]))
    return out


# ---------------------------------------------------------------------------------------------------------------- one evaluation
class Evaluation:
    """The values of one factor on one panel with one set of settings, and everything Work shows about them."""

    def __init__(self, panel, raw, settings, *, formula=True, future=None, score_from=None, step=1, with_decay=True, with_similar=True, shared=None):
        """`shared` carries what another evaluation of the same values with the same preprocessing and delay already computed
        (a sweep over holding, groups or costs): the processed values, the reasons and the decay."""
        self.panel, self.settings = panel, settings
        self.raw = np.asarray(raw, dtype=float)
        self.raw[~np.isfinite(self.raw)] = np.nan
        if shared is not None:
            self.after, self.reason, self.eligible = shared["after"], shared["reason"], shared.get("eligible")
        else:
            leave = settings["leaveOut"]
            # Who may have a value each day, before preprocessing (Results preprocesses the same values another way from it).
            self.eligible = panel.cached(("eligibility", bool(leave.get("halted", True)), bool(leave.get("newListings", True)), bool(leave.get("illiquid", False))),
                                         lambda: eligibility(panel, leave))
            self.after, self.reason = preprocess(self.raw, panel, settings, self.eligible)
        if score_from is not None:
            # Held-out scoring: values before the start do not count, but everything after it has its full history.
            early = panel.labels < score_from
            self.after[early] = np.nan
        self.count = settings["grouping"]["count"]
        self.groups = group_of(self.after, settings, panel.industry)
        self.count = group_count(self.groups, settings)
        close = panel.fields["close"]
        h, d = settings["holding"], settings["delay"]
        known = shared["decay"] if shared is not None and shared.get("decay") is not None else None
        holdings = sorted({h, *(range(1, DECAY_DAYS + 1) if with_decay and known is None else ())})
        codes = None
        if settings.get("groupAdjust"):
            if panel.industry is None:
                raise ValueError("Group neutral needs an industry for each name, and Market gives none for these names")
            codes = panel.industry_codes("Unknown")[0]
        # The values sorted once, for the rank IC of every holding and the comparison with the library and the styles.
        self.basis = M.RankBasis(self.after)
        found = rank_ics(self.after, close, holdings, d, codes, basis=self.basis, panel=panel)
        self.ic, self.forward = found[h]
        self.decay = known if known is not None else [M.mean(found[day][0]) for day in range(1, DECAY_DAYS + 1)] if with_decay else None
        self.returns = returns_of(panel, self.groups, self.count, settings, self.after, codes)
        names = np.sum(~np.isnan(self.after), axis=1)
        valued = names > 0
        members = members_a_day(panel.labels, panel.info.get("index"))
        sizes = np.stack([np.sum(self.groups == g, axis=1) for g in range(1, self.count + 1)], axis=1)
        smallest = sizes.min(axis=1)
        net, gross, traded = self.returns["net"], self.returns["gross"], self.returns["traded"]
        A = panel.annualization
        # The long-short book (top minus bottom) pays for itself: its return is already a return over cash, so its Sharpe takes no rate
        # off (design.md, risk-free rate; over the T-bill it took the rate off twice, 0.3 where 0.8 was right). Every number of a
        # factor is of that book or of no money at all, so a factor has no risk-free setting; the record says what Sharpe is over.
        risk_measure = {"basis": "zero", "label": i18n.t("none: a long–short book that pays for itself is already a return over cash")}
        self.similar = similar(self.after, self.groups, self.count, panel, holding=h, delay=d, close=close, basis=self.basis) if with_similar else []
        like = self.similar[0] if self.similar else None
        like_library = next((item for item in self.similar if not item.get("style")), None)
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            if codes is not None:  # Alphalens: by group, less each group's mean
                demeaned = group_demeaned(self.forward, self.after, codes)
            elif settings.get("demeaned", True):
                demeaned = self.forward - np.nanmean(np.where(np.isnan(self.after), np.nan, self.forward), axis=1, keepdims=True)
            else:
                demeaned = self.forward
            group_means = []
            for g in range(1, self.count + 1):
                cells = np.where(self.groups == g, demeaned, np.nan)
                group_means.append(M.mean(np.nanmean(cells, axis=1)) if np.isfinite(cells).any() else None)
        self.numbers = {
            "rankIc": M.mean(self.ic), "t": M.newey_west_t(self.ic, max(h - 1, 0)),
            "longShortNet": M.annual_mean(net, A), "longShortGross": M.annual_mean(gross, A),
            "turnover": turnover_of(self.groups, self.count, h), "names": int(np.median(names[valued])) if valued.any() else 0,
            "members": None if members is None or not valued.any() else int(np.median(members[valued])),
            "mostLike": None if like is None else {"name": like["name"], "version": like["version"], "rho": like["rho"]},
            "mostLikeLibrary": None if like_library is None else {"name": like_library["name"], "version": like_library["version"], "rho": like_library["rho"]},
            "breakEven": M.break_even_bps(gross, traded), "groupSize": int(np.median(smallest[valued])) if valued.any() else 0,
            "sharpe": M.sharpe(net, A), "riskFree": risk_measure, "maxDrawdown": M.max_drawdown(net[np.isfinite(net)]),
            "days": int(np.sum(np.isfinite(self.ic))), "groups": self.count, "holding": h,
        }
        years = M.signal_years(panel.dates, self.ic, net, A, h)
        flags = panel.cached("suspicious", lambda: suspicious(panel))
        self.details = {"years": years, "groupMeans": group_means, "coverage": coverage_of(panel.dates, names), "flags": flags,
                        "names": names}
        self.checks = checks_of(self.numbers, self.details, settings, formula=formula, future=future, library=panel.library)
        self.step = step

    def shared(self):
        """What a sweep's next combination with the same preprocessing and delay can reuse."""
        return {"after": self.after, "reason": self.reason, "decay": self.decay, "eligible": self.eligible}

    def figures(self, compared=None, compared_label=None):
        out = {"groups": groups_figure(self.panel.dates, self.returns["relative"], self.returns["net"], compared=compared,
                                       compared_label=compared_label, step=self.step)}
        if self.decay is not None:
            out["decay"] = decay_figure(self.decay, self.settings["holding"])
        return out

    def summary(self):
        """The numbers a run record keeps (and the runs table shows)."""
        return {"numbers": self.numbers, "checks": self.checks, "decay": self.decay, "years": self.details["years"],
                "groupMeans": self.details["groupMeans"], "similar": self.similar, "flags": self.details["flags"][:20]}

    def ls_path(self):
        """The cumulative long–short net, for drawing it beside another run."""
        keep = np.arange(0, len(self.panel.dates), max(1, self.step))
        path = M.compounded(self.returns["net"])[keep]
        return {"x": _iso(self.panel.dates[keep]), "y": [round(float(v), 6) for v in path]}

    def store_daily(self, path):
        """Each day's rank IC, long–short net, top and bottom groups' turnover and names with a value, to compare two runs on the days both have."""
        holding = self.settings["holding"]
        np.savez(path, labels=self.panel.labels, rankIc=self.ic, longShortNet=self.returns["net"], names=self.details["names"],
                 top=M.replaced_share(self.groups == self.count, holding), bottom=M.replaced_share(self.groups == 1, holding))

    def store(self, directory, inputs=None):
        """The cross-section of every day, for the values table: read one day at a time, never loaded whole."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        np.save(directory / "raw.npy", self.raw.astype(np.float32))
        np.save(directory / "after.npy", self.after.astype(np.float32))
        np.save(directory / "group.npy", self.groups.astype(np.int16))
        np.save(directory / "missing.npy", self.reason.astype(np.int8))
        np.save(directory / "next.npy", self.forward.astype(np.float32))
        names = []
        for index, (name, table) in enumerate((inputs or {}).items()):
            np.save(directory / f"input-{index}.npy", np.asarray(table, dtype=np.float32))
            names.append(name)
        flags = [{"t": item["t"], "i": item["i"], "text": item["text"]} for item in self.details["flags"]]
        industries = None
        if self.panel.industry is not None:
            codes, industries = self.panel.industry_codes()
            np.save(directory / "industry.npy", codes.astype(np.int16))
        meta = {"labels": self.panel.labels.tolist(), "columns": self.panel.columns, "industries": industries,
                "inputs": names, "holding": self.settings["holding"], "delay": self.settings["delay"], "groups": self.count, "flags": flags,
                "names": [int(item) for item in self.details["names"]]}
        (directory / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


def held_out(panel, raw, settings, start, *, formula=True):
    """The run scored once on the held-out period: the same values and settings, counted only from `start`."""
    evaluation = Evaluation(panel, raw, settings, formula=formula, score_from=start, with_decay=False, with_similar=False)
    return evaluation


def sweep_axes(settings, sweep):
    """The settings of each combination of a sweep: `sweep` lists each axis's name and values (holding, groups, costBps, delay)."""
    import itertools
    axes = [(axis["name"], list(axis["values"])) for axis in sweep]
    cells = []
    for combo in itertools.product(*(values for _, values in axes)):
        cell = json.loads(json.dumps(settings))
        for (name, _), value in zip(axes, combo, strict=True):
            if name == "groups":
                cell["grouping"]["count"] = int(value)
            elif name in ("holding", "delay"):
                cell[name] = int(value)
            elif name == "costBps":
                cell[name] = float(value)
            else:
                cell.setdefault("params", {})[name] = value
        cells.append((dict(zip([name for name, _ in axes], combo, strict=True)), settings_of(cell)))
    return cells
