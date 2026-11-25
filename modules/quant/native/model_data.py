"""A model's data set in the research environment: inputs on the run's panel, the label, processing and the walk-forward windows.

Everything is a wide array (bars × names) like the factor engine's: each input becomes one or more feature tables, stacked into one
tensor (bars × names × features). Processing that works within a day (clipping, ranking, z-scores, taking out industry and size,
filling with the day's median) is done once on the whole tensor, since it uses nothing across days; processing that needs statistics
over time (the robust z-score and the median fill over the training segment) is fitted on each window's training segment only.

The label is the return from the next open over `days` bars: open[t + 1 + days] / open[t + 1] - 1, the fill a strategy gets when it
trades on the close's signal. Windows are laid out on the bar calendar in months; between training, validation and test there are
`days` bars, so no training label reaches a later segment. Rows are names that were members that day (index membership is honoured,
a known defect of the old Alpha158 path) and that the data settings do not leave out.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import factor_work as W
from . import metrics as M

DAY = 86_400_000
WARMUP = {"alpha158": 60, "alpha360": 60, "styles": 252}
MIN_NAMES = 5  # fewer names a day than this cannot be ranked against each other
LEAVE_OUT_DEFAULT = {"halted": True, "young": True, "illiquid": False}


class DataError(Exception):
    """A data set that cannot be built: `failure` is what the run shows."""

    def __init__(self, title, message, **extra):
        super().__init__(message)
        self.failure = {"stage": "data", "title": title, "message": message, **extra}


# ---------------------------------------------------------------------------------------------------------------- built-in sets
def qlib_set(name):
    """Qlib's own feature definitions of Alpha158 or Alpha360: (expressions, names)."""
    from qlib.contrib.data.loader import Alpha158DL, Alpha360DL
    return (Alpha158DL if name == "alpha158" else Alpha360DL).get_feature_config()


class _Expressions:
    """Qlib's expressions evaluated by Qlib's operators on the panel's tables in memory (the formula engine's provider)."""

    def __init__(self, panel, columns):
        from . import formula as F
        F._qlib()
        self.memory = F._memory
        self.columns = columns
        self.prefix = f"__m{id(self)}_"
        where = {column: index for index, column in enumerate(columns)}
        index = [panel.columns.index(column) for column in columns]
        for name in ("open", "high", "low", "close", "volume"):
            self.memory.tables[self.prefix + name] = (panel.fields[name][:, index], where)
        self.length = len(panel.labels)

    def evaluate(self, expression):
        from qlib.data import ops
        from qlib.data.base import Feature
        from qlib.utils import parse_field
        text = parse_field(expression).replace("Operators.", "").replace('Feature("', f'Feature("{self.prefix}')
        built = eval(text, {**{name: getattr(ops, name) for name in dir(ops) if not name.startswith("_")}, "Feature": Feature})  # noqa: S307 - Qlib's own definitions
        out = np.full((self.length, len(self.columns)), np.nan)
        for position, column in enumerate(self.columns):
            series = built.load(column, 0, self.length - 1, "day")
            values = series.reindex(pd.RangeIndex(self.length)).to_numpy(dtype=float)
            out[:, position] = values
        out[~np.isfinite(out)] = np.nan
        return out

    def close(self):
        from qlib.data.cache import H
        for name in [name for name in self.memory.tables if name.startswith(self.prefix)]:
            del self.memory.tables[name]
        H["f"].clear()


def builtin_features(panel, name, columns=None, *, only=None):
    """A built-in set on the panel (or on some of its names): {feature name: table}, and the features left out with why. `only` keeps those of the set's
    columns (Qlib's FilterCol, as its network benchmarks keep 20 of Alpha158); names of another set are passed over (`_check_kept` names any no set has).

    Without a VWAP from the source, Qlib's VWAP features are left out rather than kept as empty columns."""
    if name == "styles":
        found = styles(panel)
        return ({key: value for key, value in found.items() if key.split(".", 1)[1] in set(only)} if only else found), []
    expressions, names = qlib_set(name)
    pairs = list(zip(expressions, names, strict=True))
    if only:
        keep = set(only)
        pairs = [(expression, label) for expression, label in pairs if label in keep]
    kept, dropped = [], []
    for expression, label in pairs:
        if "$vwap" in expression:
            dropped.append(label)
        else:
            kept.append((expression, label))
    chosen = list(columns or panel.columns)
    engine = _Expressions(panel, chosen)
    out = {}
    try:
        for expression, label in kept:
            values = engine.evaluate(expression)
            if columns is not None:
                full = np.full(panel.shape, np.nan)
                full[:, [panel.columns.index(column) for column in chosen]] = values
                values = full
            out[f"{'Alpha158' if name == 'alpha158' else 'Alpha360'}.{label}"] = values
    finally:
        engine.close()
    return out, dropped


def styles(panel):
    """Price styles, version 1: momentum 12-1, reversal 1M, volatility 60D, residual volatility 60D, beta 60D to the names' average,
    liquidity (log dollar volume 20D) and size (log market value, or log 30-day dollar volume where Market gives no shares)."""
    close = pd.DataFrame(panel.fields["close"]).ffill(limit=5)
    returns = pd.DataFrame(panel.derived("returns"))
    market = returns.mean(axis=1)
    out = {}
    with np.errstate(invalid="ignore", divide="ignore"):
        out["Styles.momentum"] = (close.shift(21) / close.shift(252) - 1).to_numpy()
        out["Styles.reversal"] = -(close / close.shift(21) - 1).to_numpy()
        out["Styles.volatility"] = returns.rolling(60, min_periods=40).std().to_numpy()
        cov = returns.apply(lambda col: col.rolling(60, min_periods=40).cov(market))
        var = market.rolling(60, min_periods=40).var()
        beta = cov.div(var, axis=0)
        out["Styles.beta"] = beta.to_numpy()
        residual = returns - beta.mul(market, axis=0)
        out["Styles.residual_volatility"] = residual.rolling(60, min_periods=40).std().to_numpy()
        amount = pd.DataFrame(panel.derived("amount"))
        out["Styles.liquidity"] = np.log(amount.rolling(20, min_periods=10).mean().where(lambda x: x > 0)).to_numpy()
        size = panel.derived("size")
        out["Styles.size"] = np.log(np.where(size > 0, size, np.nan))
    return {key: np.where(np.isfinite(value), value, np.nan) for key, value in out.items()}


STYLE_NAMES = ("momentum", "reversal", "volatility", "beta", "residual_volatility", "liquidity", "size")


# ---------------------------------------------------------------------------------------------------------------- inputs
def kept_columns(text):
    """The setting "Built-in columns" ("KMID, ROC5") as the list of names to keep; empty keeps them all."""
    parts = [part.strip() for part in str(text or "").replace(";", ",").split(",") if part.strip()]
    return [part.split(".", 1)[1] if part.split(".", 1)[0] in ("Alpha158", "Alpha360", "Styles") and "." in part else part for part in parts]   # "Alpha158.KMID" is KMID


def _check_kept(inputs):
    """Every name in "Built-in columns" is a column of one of the built-in sets chosen; a name none has is a data error that names it."""
    builtin = [item for item in inputs if item.source == "builtin"]
    only = builtin[0].only if builtin else []
    if not only:
        return
    have = set()
    for item in builtin:
        have |= set(STYLE_NAMES if item.ref == "styles" else qlib_set(item.ref)[1])
    unknown = [name for name in only if name not in have]
    if unknown:
        raise DataError("a built-in column is not there", f"Built-in columns: {', '.join(unknown[:5])}" + (f" and {len(unknown) - 5} more" if len(unknown) > 5 else "")
                        + f" {'is' if len(unknown) == 1 else 'are'} not a column of {' or '.join(item.name for item in builtin)}.")


@dataclass
class Input:
    """One input row as the run uses it: where its values come from and what it adds."""
    id: str
    name: str
    source: str
    ref: str = ""
    formula: str | None = None
    path: str | None = None
    ready: str = "close"
    only: list = field(default_factory=list)   # a built-in set's columns to keep (Qlib's FilterCol); empty keeps them all
    features: list = field(default_factory=list)
    dropped: list = field(default_factory=list)
    note: str | None = None


def library_table(panel, path, *, opens=None):
    """A library version's values (label, column, value, availableAt) as a table on the panel, and how many bars were known only after
    the next open (the label's start)."""
    rows = pd.read_parquet(path)
    position = {int(label): index for index, label in enumerate(panel.labels)}
    where = {column: index for index, column in enumerate(panel.columns)}
    row = rows["label"].map(position)
    col = rows["column"].map(where)
    keep = row.notna() & col.notna()
    table = np.full(panel.shape, np.nan)
    table[row[keep].astype(int).to_numpy(), col[keep].astype(int).to_numpy()] = rows["value"][keep].to_numpy(dtype=float)
    late = 0
    if "availableAt" in rows and opens is not None:
        index = row[keep].astype(int).to_numpy()
        # the last bar has no next open in the panel (its label lies beyond the data): it cannot be late for a label it does not have
        has_next = index + 1 < len(opens)
        next_open = opens[np.minimum(index + 1, len(opens) - 1)]
        late = int(np.sum((rows["availableAt"][keep].to_numpy() > next_open) & has_next))
    return table, late


def run_values(panel, path):
    """An unpublished code factor run's values (its stored raw values, labels and names) on this panel."""
    directory = Path(path)
    meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    raw = np.load(directory / "raw.npy", mmap_mode="r")
    table = np.full(panel.shape, np.nan)
    rows = np.searchsorted(panel.labels, np.asarray(meta["labels"], dtype="int64"))
    found = (rows < len(panel.labels)) & (panel.labels[np.minimum(rows, len(panel.labels) - 1)] == np.asarray(meta["labels"], dtype="int64"))
    columns = {column: index for index, column in enumerate(panel.columns)}
    for source_index, column in enumerate(meta["columns"]):
        if column in columns:
            table[rows[found], columns[column]] = np.asarray(raw[found, source_index], dtype=float)
    return table


def input_tables(panel, inputs, *, code=None, columns=None):
    """{feature name: table} of every input, in input order, and each input with the features it added and what it left out."""
    from . import formula as F
    tables = {}
    context = None
    _check_kept(inputs)
    for item in inputs:
        if item.source == "builtin":
            found, dropped = builtin_features(panel, item.ref, columns, only=item.only)
            item.dropped = dropped
            if dropped:
                item.note = (f"{', '.join(dropped)} {'has' if len(dropped) == 1 else 'have'} no values (the source gives no VWAP) and "
                             f"{'is' if len(dropped) == 1 else 'are'} left out") if len(dropped) < 5 else \
                    f"The {len(dropped)} VWAP features have no values (the source gives no VWAP) and are left out"
        elif item.formula is not None:
            if context is None:
                context = F.Context(panel.tables(), panel.industry)
            try:
                values = F.evaluate(item.formula, context).to_numpy(dtype=float)
            except F.FormulaError as error:
                raise DataError("an input cannot be computed", f"{item.name}: {error.message}", input=item.id) from error
            found = {item.name: values}
        elif item.source in ("library", "value"):
            table, late = library_table(panel, item.path, opens=_open_times(panel))
            found = {item.name: table}
            if late:
                item.ready = "late"
                item.note = f"{late} values became known after the next open (the label's start)"
        elif item.source == "run":
            found = {item.name: run_values(panel, item.path)}
        elif item.source == "code":
            if code is None:
                raise DataError("an input cannot be computed", f"{item.name} is written in code, and this run has no code.", input=item.id)
            found = {item.name: code(item)}
        else:
            raise DataError("an input cannot be computed", f"{item.name}: unknown source {item.source}.", input=item.id)
        clean = {}
        for name, value in found.items():
            value = np.asarray(value, dtype=float)
            if value.shape != panel.shape:
                raise DataError("an input has the wrong shape", f"{name} is {value.shape[0]} × {value.shape[1]}; the data are {panel.shape[0]} × {panel.shape[1]}.",
                                input=item.id)
            value = np.where(np.isfinite(value), value, np.nan)
            unique = name
            while unique in tables or unique in clean:
                unique += "'"
            clean[unique] = value
        item.features = list(clean)
        tables.update(clean)
    return tables


def _open_times(panel):
    """When each bar's open is known (ms): the bar time where the panel knows it, else its label."""
    times = getattr(panel, "open_times", None)
    return times if times is not None else panel.labels


# ---------------------------------------------------------------------------------------------------------------- processing
def _clip(x, scale):
    """Each day's values clipped at `scale` median absolute deviations around the day's median, along the last-but-one axis."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        median = np.nanmedian(x, axis=1, keepdims=True)
        mad = np.nanmedian(np.abs(x - median), axis=1, keepdims=True) * W.MAD_SCALE
    return np.where(np.isnan(x), np.nan, np.clip(x, median - scale * mad, median + scale * mad))


def _pct_rank(x):
    """Each day's values as their percentile rank, rank / count (ties share their mean rank): the formula's CSRank, pandas' `rank(pct=True)`."""
    count = np.sum(~np.isnan(x), axis=1, keepdims=True)
    return np.where(np.isnan(x), np.nan, M.rank_rows(x) / np.maximum(count, 1))


def _rank(x):
    """The rank processing, Qlib's CSRankNorm: each day's percentile rank less one half, times 3.46 (about unit spread)."""
    out = np.empty_like(x)
    if x.ndim == 2:
        return (_pct_rank(x) - 0.5) * 3.46
    for f in range(x.shape[2]):
        out[:, :, f] = _rank(x[:, :, f])
    return out


def _zscore(x):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        mean = np.nanmean(x, axis=1, keepdims=True)
        std = np.nanstd(x, axis=1, ddof=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(std > 0, (x - mean) / std, 0.0 * x)


def _day_median(x):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        median = np.nanmedian(x, axis=1, keepdims=True)
    return np.where(np.isnan(x), np.broadcast_to(median, x.shape), x)


def _industry_median(x, industry):
    """Missing values filled with the median of the names in the same industry that day, then the day's median."""
    if industry is None:
        return _day_median(x)
    from .factor_work import industry_segments, label_codes
    out = x.copy()
    for first, end, codes in industry_segments(label_codes(industry, "__none__")[0]):
        for code in np.unique(codes):
            index = np.flatnonzero(codes == code)
            part = x[first:end, index]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                median = np.nanmedian(part, axis=1, keepdims=True)
            out[first:end, index] = np.where(np.isnan(part), np.broadcast_to(median, part.shape), part)
    return _day_median(out)


def _take_out(x, panel, what):
    """The residual of each feature after industry dummies and/or log size, each day."""
    from .formula import neutralize
    industry = panel.industry if "industry" in what else None
    size = None
    if "size" in what:
        value = panel.derived("size")
        with np.errstate(invalid="ignore", divide="ignore"):
            size = pd.DataFrame(np.log(np.where(value > 0, value, np.nan)), index=panel.dates, columns=panel.columns)
    # What is asked to be taken out must be there: without it the residual would be the raw input or, for a size with no values, zero.
    if "industry" in what and (industry is None or not industry.notna().to_numpy().any()):
        raise DataError("cannot take out industry", "Taking out industry needs the names' industries; Market gives none for this pool.")
    if "size" in what and not np.isfinite(size.to_numpy()).any():
        raise DataError("cannot take out size", "Taking out size needs the names' market values; Market gives no share counts for this pool.")
    if industry is not None:
        from .formula import industry_codes
        industry = industry_codes(industry, pd.DataFrame(x[:, :, 0], index=panel.dates, columns=panel.columns))  # coded once for every feature
    out = np.empty_like(x)
    for f in range(x.shape[2]):
        frame = pd.DataFrame(x[:, :, f], index=panel.dates, columns=panel.columns)
        out[:, :, f] = neutralize(frame, industry, size).to_numpy()
    return out


TRAIN_FITTED = ("robust", "zscore_train", "minmax")


def _day_mean(x):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        mean = np.nanmean(x, axis=1, keepdims=True)
    return np.where(np.isnan(x), np.nan_to_num(mean), x)


def warmup_of(inputs, reach):
    """The bars before the first one that the data needs: what the built-in sets need, and what the label's rolling windows take back."""
    return max([WARMUP.get(item.ref, 0) for item in inputs if item.source == "builtin"] + [int(reach["back"])])


def process_day(x, panel, settings):
    """The processing of inputs within each day, on the whole tensor (bars × names × features): extremes, a day's scale, taking out
    industry and size, then the missing values the day can fill. Statistics over time are fitted per window (`fitted`)."""
    if settings["extremes"] in ("mad3", "mad5"):
        x = _clip(x, 3.0 if settings["extremes"] == "mad3" else 5.0)
    if settings["scale"] == "rank":
        x = _rank(x)
    elif settings["scale"] == "zscore":
        x = _zscore(x)
    elif settings["scale"] == "tanh":
        x = np.tanh(x - 1)  # Qlib's TanhProcess, after the extremes are clipped
    if settings.get("takeOut"):
        x = _take_out(x, panel, settings["takeOut"])
    fills_later = settings["scale"] in TRAIN_FITTED
    if settings["missing"] == "median":
        x = _day_median(x)
    elif settings["missing"] == "mean":
        x = _day_mean(x)
    elif settings["missing"] == "zero" and not fills_later:
        x = np.where(np.isnan(x), 0.0, x)  # Qlib's Fillna(0)
    elif settings["missing"] == "industry":
        x = _industry_median(x, panel.industry)
    return x.astype(np.float32)


def fit_window(train_rows, settings):
    """What the processing learns from a window's training rows only: each feature's median and scaled MAD (robust z-score), mean and spread
    (z-score), lowest and highest (min–max), or its median (filling missing values). Returns None when nothing is fitted."""
    if settings["scale"] not in TRAIN_FITTED and settings["missing"] != "train_median":
        return None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        median = np.nanmedian(train_rows, axis=0)
        mad = np.nanmedian(np.abs(train_rows - median), axis=0) * W.MAD_SCALE
        mean, std = np.nanmean(train_rows, axis=0), np.nanstd(train_rows, axis=0)
        low, high = np.nanmin(train_rows, axis=0), np.nanmax(train_rows, axis=0)
    flat = low == high  # Qlib's MinMaxNorm leaves a constant column as it is
    constant = std == 0  # and ZScoreNorm too
    return {"median": np.nan_to_num(median).astype(np.float64), "mad": np.where(mad > 0, mad, 1.0).astype(np.float64),
            "mean": np.where(constant, 0.0, np.nan_to_num(mean)).astype(np.float64), "std": np.where(constant | ~np.isfinite(std), 1.0, std).astype(np.float64),
            "low": np.where(flat, 0.0, np.nan_to_num(low)).astype(np.float64), "span": np.where(flat, 1.0, np.nan_to_num(high - low, nan=1.0)).astype(np.float64)}


def apply_fitted(rows, fitted, settings):
    if fitted is None:
        return rows
    out = rows.astype(np.float64)
    if settings["missing"] == "train_median":
        out = np.where(np.isnan(out), fitted["median"], out)
    if settings["scale"] == "robust":
        out = np.clip((out - fitted["median"]) / fitted["mad"], -3, 3)
    elif settings["scale"] == "zscore_train":
        out = (out - fitted["mean"]) / fitted["std"]
    elif settings["scale"] == "minmax":
        out = (out - fitted["low"]) / fitted["span"]
    if settings["scale"] in TRAIN_FITTED and settings["missing"] in ("train_median", "median", "mean", "zero", "industry"):
        out = np.where(np.isnan(out), 0.0, out)
    return out.astype(np.float32)


QUICK_LABEL = re.compile(r"^Ref\(\$open, -(\d+)\) / Ref\(\$open, -1\) - 1$")
FORMS = {"raw": "{}", "demean": "CSDemean({})", "rank": "CSRank({})"}


def quick_formula(days, form="rank"):
    """The label the preparation's quick choices stand for: the return from the next open over `days` bars, as raw, less the mean or ranked."""
    return FORMS[form].format(f"Ref($open, -{int(days) + 1}) / Ref($open, -1) - 1")


def aux_formulas(label_formula, aux):
    """TCTS's auxiliary labels: the formulas written in the settings, or when none are, the main label's own return one and two bars further out in the same form;
    None when the algorithm takes none (`aux` None), an empty list when none can be derived (the main label is not a plain return over some days)."""
    if aux is None:
        return None
    lines = [item.strip() for item in (aux.splitlines() if isinstance(aux, str) else aux) if str(item).strip()]
    if lines:
        return lines
    core, form = split_form(label_formula)
    found = QUICK_LABEL.match(core.strip())
    if not found:
        return []
    days = int(found.group(1)) - 1
    return [FORMS[form].format(quick_formula(days + extra, "raw")) for extra in (1, 2)]


def label_formula(label):
    """The label formula of a run's settings; a run from before labels were formulas has `days` and `form`."""
    if label.get("formula"):
        return label["formula"]
    return quick_formula(label.get("days", 5), label.get("form", "rank"))


def split_form(source):
    """(the part that is a return or another value, 'rank' | 'demean' | 'raw'): a formula wrapped in CSRank or CSDemean is that form of its inside,
    which `process_label` applies as the formula would (CSRank is the pandas percentile rank of each day, bit for bit)."""
    text = source.strip()
    for name, form in (("CSRank", "rank"), ("CSDemean", "demean")):
        if text.startswith(name + "(") and text.endswith(")"):
            inner = text[len(name) + 1:-1]
            if inner.count("(") == inner.count(")"):
                return inner.strip(), form
    return text, "raw"


def held(frame, close=None):
    """Prices held over a halt until the name trades again, as the factor's returns hold them: its first bar back carries the whole move over the
    halt. After its last price (delisted, or the source has none after) the name is out at its last close (`close`, else its own last price) and earns
    nothing after, as the factor's forward returns hold it (metrics.held): a label reaching past it is the move to that price, not missing."""
    inside = frame.bfill().notna()
    last = (frame if close is None else close.where(frame.notna())).ffill()  # the close of its last bar
    return frame.ffill().where(inside, last)


def label_of(panel, source):
    """The raw label: the formula evaluated on the panel (open prices held over a halt, `held`), values that are not numbers missing.
    A number of days is the quick return from the next open. The return from the next open (the standard label) is computed directly: the same
    arithmetic as the formula's, without the formula engine."""
    if isinstance(source, (int, np.integer)):
        source = quick_formula(int(source), "raw")
    opens = held(pd.DataFrame(panel.fields["open"]), pd.DataFrame(panel.fields["close"])).to_numpy()
    quick = QUICK_LABEL.match(source.strip())
    if quick:
        days = int(quick.group(1)) - 1
        out = np.full(panel.shape, np.nan)
        start, end = 1, 1 + days
        if days >= 1 and end < len(opens):
            with np.errstate(invalid="ignore", divide="ignore"):
                out[:len(opens) - end] = opens[end:] / opens[start:len(opens) - days] - 1
        out[~np.isfinite(out)] = np.nan
        return out
    from . import formula as F
    tables = dict(panel.tables())
    tables["$open"] = held(tables["$open"], tables["$close"])
    tables["$close"] = held(tables["$close"])
    values = F.evaluate(source, F.Context(tables, panel.industry), ahead=True).to_numpy(dtype=float)
    values[~np.isfinite(values)] = np.nan
    return values


def weights_of(panel, label, rows):
    """The weight of each row: None for equal weights; the square root of the market value (the day's mean 1); or a formula that reads only the past
    and the present. A negative or missing weight counts as 0. Returns the table and the share of rows weighing 0."""
    kind = label.get("weight", "equal")
    if kind == "equal":
        return None, 0.0
    if kind == "size":
        with np.errstate(invalid="ignore"):
            value = np.sqrt(np.where(panel.derived("size") > 0, panel.derived("size"), np.nan))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            value = value / np.nanmean(value, axis=1, keepdims=True)
    else:
        from . import formula as F
        source = (label.get("weightFormula") or "").strip()
        if not source:
            raise DataError("needs a weight formula", "Sample weight is a formula; write it.")
        try:
            value = F.evaluate(source, F.Context(dict(panel.tables()), panel.industry)).to_numpy(dtype=float)
        except F.FormulaError as error:
            raise DataError("a weight cannot be computed", "Sample weight: " + ("A weight reads only past and current bars" if getattr(error, "kind", "") == "future" else error.message)) from error
    value = np.where(np.isfinite(value) & (value > 0), value, 0.0)
    counted = rows.sum()
    return value, float(((value == 0) & rows).sum() / counted) if counted else 0.0


def process_label(raw, settings, form):
    y = raw.copy()
    if form == "demean":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            y = y - np.nanmean(y, axis=1, keepdims=True)
    if settings["extremes"] in ("mad3", "mad5"):
        y = _clip(y, 3.0 if settings["extremes"] == "mad3" else 5.0)
    if form == "rank":
        y = _pct_rank(y)
    if settings["scale"] == "rank":
        y = _rank(y)
    elif settings["scale"] == "zscore":
        y = _zscore(y)
    return y


# ---------------------------------------------------------------------------------------------------------------- who is in the data
def rows_of(panel, leave_out):
    """Which names may be a row each day: members with a price, minus what the data settings leave out. "Listed under 1 year" is a calendar year from the
    name's first price (a crypto pair trading every day and a stock alike); a name priced on the data's first bar was listed before it and is never young."""
    close, volume = panel.fields["close"], panel.fields["volume"]
    keep = panel.member & ~np.isnan(close)
    if leave_out.get("halted", True):
        keep &= np.nan_to_num(volume, nan=0.0) > 0
    if leave_out.get("young", True) and len(close):
        have = ~np.isnan(close)
        first = np.minimum(have.argmax(axis=0), len(have) - 1)
        listed = pd.to_datetime(panel.labels[first], unit="ms") + pd.DateOffset(years=1)
        a_year_on = np.asarray(listed.as_unit("ms").asi8, dtype="int64")
        keep &= (panel.labels[:, None] >= a_year_on[None, :]) | have[0][None, :]
    if leave_out.get("illiquid", False):
        dollar = pd.DataFrame(panel.derived("amount")).rolling(20, min_periods=5).mean().to_numpy()
        keep &= np.nan_to_num(dollar, nan=0.0) >= float(leave_out.get("minDollar", 5e6))
    return keep


# ---------------------------------------------------------------------------------------------------------------- windows
def _months_before(stamp, months):
    moment = pd.Timestamp(stamp, unit="ms", tz="UTC")
    return int((moment - pd.DateOffset(months=months)).value // 1_000_000)


def _months_after(stamp, months):
    moment = pd.Timestamp(stamp, unit="ms", tz="UTC")
    return int((moment + pd.DateOffset(months=months)).value // 1_000_000)


def _month_start(stamp):
    moment = pd.Timestamp(stamp, unit="ms", tz="UTC")
    return int(pd.Timestamp(year=moment.year, month=moment.month, day=1, tz="UTC").value // 1_000_000)


@dataclass
class Window:
    k: int
    train: tuple
    valid: tuple
    test: tuple
    rows: int = 0
    flag: str | None = None

    def spans(self, labels):
        """Each segment as {from, to} bar labels (ms)."""
        return {name: {"from": int(labels[a]), "to": int(labels[b])} for name, (a, b) in (("train", self.train), ("valid", self.valid), ("test", self.test))}


def layout(labels, split, ahead, *, warmup, held_out=None, last_label=None):
    """The walk-forward windows on the bar calendar: positions (first, last) of train, valid and test, `ahead - 1` bars apart.

    `split` holds trainMonths, validMonths, everyMonths, mode (rolling or expanding) and firstTest (YYYY-MM-DD or empty: the first
    month with room for training and validation). No segment reaches the held-out period, and the last labels end before it."""
    labels = np.asarray(labels, dtype="int64")
    count = len(labels)
    gap = int(ahead) - 1  # `ahead` is how many bars after its own the label reads (6 for the return over 5 bars from the next open)
    end = count - 1
    if held_out is not None:
        end = min(end, int(np.searchsorted(labels, held_out)) - 1)
    # A sample's label reads `ahead` bars later: those must exist and stay outside the held-out period.
    labelled = end - int(ahead)
    if last_label is not None:
        labelled = min(labelled, last_label)
    first_row = min(max(int(warmup), 0), count - 1)

    def at(stamp):
        return int(np.searchsorted(labels, stamp))

    train_months, valid_months, every = int(split["trainMonths"]), int(split["validMonths"]), int(split["everyMonths"])
    if split.get("firstTest"):
        test_start = at(int(pd.Timestamp(split["firstTest"], tz="UTC").value // 1_000_000))
    else:
        # The first month that starts after the warm-up plus a whole training and validation segment.
        start = _months_after(int(labels[first_row]), train_months + valid_months)
        month = _month_start(start)
        test_start = at(month if month >= start else _months_after(month, 1))
    windows = []
    while test_start <= labelled:
        next_start = at(_months_after(labels[test_start], every))
        # The last test period runs to the newest bar: its predictions exist before their labels do.
        test_end = min(next_start - 1, end)
        valid_end = test_start - gap - 1
        valid_start = max(at(_months_before(labels[max(valid_end, 0)], valid_months) + 1), 0)
        train_end = valid_start - gap - 1
        if split.get("mode", "rolling") == "rolling":
            train_start = max(at(_months_before(labels[max(train_end, 0)], train_months) + 1), first_row)
        else:
            train_start = first_row
        if train_end - train_start < 10 or valid_end - valid_start < 2 or test_end < test_start:
            if not windows:
                test_start = next_start
                continue
            break
        windows.append(Window(len(windows) + 1, (train_start, train_end), (valid_start, valid_end), (test_start, test_end)))
        test_start = next_start
    return windows


# ---------------------------------------------------------------------------------------------------------------- the data set
class ModelData:
    """The data set of one run: the tensor of processed inputs, the label, the rows, and the windows."""

    def __init__(self, panel, inputs, settings, *, code=None, columns=None, held_out=None):
        self.panel, self.settings, self.inputs = panel, settings, inputs
        from . import formula as F
        self.label_formula = label_formula(settings["label"])
        self.reach = F.reach(F.parse(self.label_formula, ahead=True), self.label_formula)
        self.aux = aux_formulas(self.label_formula, settings["label"].get("aux"))
        # no segment may reach another through any label it trains on: the gap follows the one that reads furthest
        self.gap_ahead = max([self.reach["ahead"]] + [F.reach(F.parse(item, ahead=True), item)["ahead"] for item in self.aux or []])
        self.horizon = self.reach["ahead"] - self.reach["start"]["bar"]  # bars from where the label starts to the furthest it reads
        self.days = self.horizon
        tables = input_tables(panel, inputs, code=code, columns=columns)
        if not tables:
            raise DataError("has no inputs", "Add at least one input: a library factor, a factor run, a built-in set or a Market series.")
        self.features = list(tables)
        raw = np.stack([tables[name] for name in self.features], axis=2).astype(np.float32)
        self.coverage_raw = raw
        self.rows = rows_of(panel, settings.get("leaveOut") or LEAVE_OUT_DEFAULT)
        names_a_day = self.rows.sum(axis=1)
        if names_a_day.max(initial=0) < MIN_NAMES:
            shown = ", ".join(panel.columns[:3]) + ("…" if len(panel.columns) > 3 else "")
            raise DataError("needs more names",
                            f"A model ranks names against each other each day, and these data have at most {int(names_a_day.max(initial=0))} "
                            f"{'name' if names_a_day.max(initial=0) == 1 else 'names'} a day ({shown}). Choose at least {MIN_NAMES}: an index, a research list "
                            "with more members or more symbols.")
        masked = np.where(self.rows[:, :, None], raw, np.nan)
        self.x = process_day(masked, panel, settings["processing"]["inputs"])
        self.weights, self.zero_weight = weights_of(panel, settings["label"], self.rows)
        core, form = split_form(self.label_formula)
        self.label_raw = label_of(panel, core)
        self.y = process_label(np.where(self.rows, self.label_raw, np.nan), settings["processing"]["label"], form)
        warm = warmup_of(inputs, self.reach)
        self.warmup = int(warm)
        self.held_out = held_out
        self.windows = layout(panel.labels, settings["split"], self.gap_ahead, warmup=self.warmup, held_out=held_out)
        if not self.windows:
            span = f"{_day(panel.labels[0])} – {_day(panel.labels[-1])}"
            raise DataError("has no window", f"The data ({span}) are too short for {settings['split']['trainMonths']} months of training, "
                                             f"{settings['split']['validMonths']} of validation and one test period after the warm-up; choose a longer period or shorter segments.")

    # rows of a segment -------------------------------------------------------------------------------------------------------
    def segment(self, span, *, labelled=True):
        """(row positions t, column positions i) of a segment's rows; with `labelled`, only rows with a label."""
        first, last = span
        keep = self.rows[first:last + 1].copy()
        if labelled:
            keep &= ~np.isnan(self.y[first:last + 1])
        if self.settings["processing"]["inputs"]["missing"] == "drop":
            keep &= ~np.isnan(self.x[first:last + 1]).any(axis=2)
        t, i = np.nonzero(keep)
        return t + first, i

    def matrices(self, window):
        """Training, validation and test rows of one window, with the processing fitted on its training rows."""
        out = {}
        settings = self.settings["processing"]["inputs"]
        train_t, train_i = self.segment(window.train)
        fitted = fit_window(self.x[train_t, train_i], settings)
        for name, span, labelled in (("train", window.train, True), ("valid", window.valid, True), ("test", window.test, False)):
            t, i = (train_t, train_i) if name == "train" else self.segment(span, labelled=labelled)
            x = apply_fitted(self.x[t, i], fitted, settings)
            out[name] = {"t": t, "i": i, "x": x, "y": self.y[t, i], **({"w": self.weights[t, i]} if self.weights is not None and name != "test" else {})}
        out["fitted"] = fitted
        return out

    def describe(self):
        return {"features": self.features, "inputs": [{"id": item.id, "name": item.name, "source": item.source, "features": item.features,
                                                        "dropped": item.dropped, "note": item.note, "ready": item.ready} for item in self.inputs],
                "days": self.days, "reach": self.reach, "label": self.label_formula, "warmup": self.warmup, "windows": len(self.windows)}


def _day(label):
    moment = pd.Timestamp(int(label), unit="ms", tz="UTC")
    return f"{moment.strftime('%b')} {moment.day}, {moment.year}"


def settings_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:20]


def months_label(months):
    if months % 12 == 0:
        years = months // 12
        return f"{years} year" + ("s" if years != 1 else "")
    return f"{months} month" + ("s" if months != 1 else "")


def finite(value):
    return None if value is None or not math.isfinite(float(value)) else float(value)
