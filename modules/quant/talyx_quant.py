"""What research and strategy code imports as `q`: parameters, the object's data, standard reports and the platform's data and library."""
from __future__ import annotations

import functools
import json
import sys

_overrides = {}
_used = {}
_index = None
_index_bound = False


def _bind_index(index):
    global _index, _index_bound
    _index, _index_bound = index, True
_models = None

# ------------------------------------------------------------------------------------------------ a strategy's own series, numbers and log
# Kept with the run: the series are drawn on the run’s price chart; the log is in simulation time beside the trades.
_strategy = None
_run_settings = {}
# Exit orders a stop decided inside the bar its position opened in: client order id -> (trigger price, that bar's ts_event in ns). The run's fill
# model prices each at its trigger, at that bar (see Strategy._stop_in_entry_bar).
_entry_bar_stops = {}
_plots = {}
_built = {}  # bar types the code asked Nautilus to build from smaller bars: "4 hours" -> "1 hour"
_stats = {}
_log = []
_rebalances = []  # what each rebalance_to call aimed for: {t, targets {instrument id: weight}}
_accounts = {}  # the run's report currency and the daily rates of its other account currencies: {reportCurrency, conversions [{currency, path}]}
_rates = {}  # currency -> (known times in ms, closes in the report currency), read once
_trade_from = None  # a run that reads bars before it trades (a walk-forward window, a year of a sweep): no order is placed before this millisecond
_skipped = []
PLOT_SERIES, LOG_LINES = 24, 20_000


def _bind_strategy(strategy, settings=None, trade_from=None, accounts=None):
    global _strategy, _run_settings, _trade_from, _accounts
    _strategy, _run_settings, _trade_from, _accounts = strategy, dict(settings or {}), trade_from, dict(accounts or {})
    _rates.clear()
    _plots.clear(), _stats.clear(), _log.clear(), _rebalances.clear(), _built.clear(), _skipped.clear()


def _period_name(text):
    """"...-4-HOUR-LAST-INTERNAL" -> "4 hours"."""
    import re
    found = re.search(r"-(\d+)-(SECOND|MINUTE|HOUR|DAY|WEEK|MONTH)-", text if text.endswith("-") else text + "-")
    if not found:
        return text
    count, unit = int(found.group(1)), found.group(2).lower()
    return f"{count} {unit}{'s' if count != 1 else ''}"


def can_short(instrument):
    """Whether this run may sell `instrument` short: perpetuals always; stocks when shorting is allowed in the run settings (a margin account
    that pays borrow); crypto spot never."""
    if type(instrument).__name__ == "InstrumentId" and _strategy is not None:
        instrument = _strategy.cache.instrument(instrument)
    kind = type(instrument).__name__
    if _run_settings.get("shorting") == "off":
        return False
    return kind == "CryptoPerpetual" or kind == "Equity" and _run_settings.get("shorting") == "allowed"


def _now_ms():
    if _strategy is None:
        raise RuntimeError("q.plot, q.stat and q.log are available inside strategy runs.")
    return _strategy.clock.timestamp_ns() // 1_000_000


def plot(name, value, *, pane="price", style="line"):
    """Draw a series of your own: `value` now (simulation time), over the prices (pane="price") or in its own pane (pane="own").
    Kept with the run, drawn over the chart and listed in Results. Up to 24 series."""
    if pane not in ("price", "own") or style not in ("line", "step"):
        raise ValueError('q.plot takes pane="price" or "own", style="line" or "step".')
    series = _plots.get(name)
    if series is None:
        if len(_plots) >= PLOT_SERIES:
            raise ValueError(f"A run draws at most {PLOT_SERIES} series of its own.")
        series = _plots[name] = {"name": str(name), "pane": pane, "style": style, "t": [], "values": []}
    at = _now_ms()
    number = None if value is None else float(value)
    if series["t"] and series["t"][-1] == at:
        series["values"][-1] = number
    else:
        series["t"].append(at)
        series["values"].append(number)


def stat(name, value):
    """A number of your own shown with the run's statistics (the last value set wins)."""
    _stats[str(name)] = None if value is None else float(value)


def log(text, *, kind="Info", instrument=None):
    """One line in the run's log, at the simulation time, beside the orders and trades."""
    if len(_log) < LOG_LINES:
        _log.append({"t": _now_ms(), "instrument": None if instrument is None else str(instrument), "kind": str(kind), "text": str(text)[:500]})


# ------------------------------------------------------------------------------------------------ extra series from Market (funding rate, mark price ...)
_extras = {}        # series name -> {instrument id: parquet path}, frozen into the run folder before the run
_extra_frames = {}  # (name, instrument id) -> (times in ms, values)
_literal_calls = {}


def _bind_extras(extras):
    _extras.clear(), _extra_frames.clear(), _literal_calls.clear()
    _extras.update(extras or {})


def _literal_series_call(frame):
    """Whether the `q.series(...)` call on the caller's line is written with a string literal, as the Host needs to fetch it before the run."""
    import ast
    import linecache
    key = (frame.f_code.co_filename, frame.f_lineno)
    if key not in _literal_calls:
        text = "".join(linecache.getlines(key[0]))
        found = True
        try:
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, ast.Call) and getattr(node, "lineno", 0) <= key[1] <= getattr(node, "end_lineno", 0):
                    function = node.func
                    if (isinstance(function, ast.Attribute) and function.attr == "series") or (isinstance(function, ast.Name) and function.id == "series"):
                        found = bool(node.args) and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
        except SyntaxError:
            pass
        _literal_calls[key] = found
    return _literal_calls[key]


def risk_size(risk_pct, equity, stop_distance, price, max_position):
    """The size of a new position that loses `risk_pct` % of `equity` if it is stopped `stop_distance` away, never more than `max_position`
    (Max position, a share of equity) worth at `price`."""
    if stop_distance <= 0 or price <= 0:
        return 0.0
    return min(risk_pct / 100 * equity / stop_distance, max_position * equity / price)


def fit_size(free, price, margin, fee, room=0.01):
    """The largest size `free` pays for at `price`: its margin (`margin` a share of the price; 1 on a cash account) and the taker `fee` on the
    whole price, with `room` for the fill coming in a little above `price` (the spread, the impact, the next bar's open)."""
    if free <= 0 or price <= 0:
        return 0.0
    return free / (price * (1 + room) * (margin + fee))


class SeriesUnavailable(ValueError):
    """The run has no frozen values for this series and instrument."""


def series(name, instrument=None):
    """An extra series Market has for this instrument (e.g. "funding_rate", "mark_price", "open_interest") up to now: a pandas Series
    indexed by UTC time, newest value last, never later than the current simulation time. Write the name out as a string: TALYX fetches it
    before the run starts."""
    import numpy as np
    import pandas as pd
    if not isinstance(name, str) or not _literal_series_call(sys._getframe(1)):
        raise ValueError('q.series needs the name written out, e.g. q.series("funding_rate"), so TALYX can fetch it before the run')
    if _strategy is None:
        raise RuntimeError("q.series is available inside strategy runs.")
    identifier = str(_strategy.instrument_ids[0] if instrument is None else instrument)
    paths = _extras.get(name) or {}
    if identifier not in paths:
        raise SeriesUnavailable(f"{identifier} has no {name} series in this run")
    if (name, identifier) not in _extra_frames:
        table = pd.read_parquet(paths[identifier]).sort_values("time")
        _extra_frames[(name, identifier)] = (table["time"].to_numpy(dtype="int64"), table["value"].to_numpy(dtype=None if name == "industry" else "float64"))
    times, values = _extra_frames[(name, identifier)]
    stop = int(np.searchsorted(times, _now_ms(), side="right"))
    return pd.Series(values[:stop], index=pd.to_datetime(times[:stop], unit="ms", utc=True), name=name)


class _NotebookModel:
    """A published model read in a notebook: loaded in this kernel from its frozen files, called like the handle a strategy gets."""

    def __init__(self, name, version, state, predictor):
        self.name, self.version, self._state, self._predictor = name, version, state, predictor

    def predict(self, payload):
        return self._predictor(self._state, payload)

    def __repr__(self):
        return f"Model {self.name} v{self.version}"


def model(name, version=None):
    """A library model by name (its newest version unless `version`): in a strategy run the frozen one it declared, in a notebook loaded here from its frozen files."""
    if _models is not None:
        return _models.model(name)
    if _Run.invocation is not None and _Run.invocation.get("research"):
        raise RuntimeError('q.model is available inside strategy runs and notebooks.')
    import time as _t
    began = _t.time()
    found = _channel()("quant.read_library_model", {"name": name, **({"version": version} if version is not None else {})}, timeout=None)
    from native.model_worker import load
    try:
        state, predictor = load({"model": found["model"]})
    except ModuleNotFoundError as error:
        if error.name in ("torch", "torchvision", "torchaudio"):
            raise RuntimeError("This model needs the deep-learning environment; switch the environment at the top") from None
        raise
    _note_loaded(f"q.model({name!r}" + (f", {version}" if version is not None else "") + ")", f"v{found['version']}, trained through {found['trainingCutoff']}", began)
    return _NotebookModel(name, found["version"], state, predictor)


def publish_model(name, artifacts, loader, predictor, training_cutoff):
    """Freeze a trained Python model and its exclusive UTC training boundary."""
    from native.model_publish import publish
    spec = publish(_publisher(), name, artifacts, loader, predictor, training_cutoff)
    _capture(name, 'model', None, model=spec)
    return spec


def _bind_parameters(overrides):
    """Runs pass the panel's values; the code's literals are only the defaults."""
    global _overrides, _used
    _overrides, _used = dict(overrides), {}


def param(name, default, *, min=None, max=None, step=None, label=None, help=None, group=None, show_if=None, unit=None):
    """Declare a tunable value. Only literal arguments are allowed: TALYX reads them without running the code.

    `step` is the panel's step, `help` its hover text, `group` the section it sits in, and `show_if` ({"other": value}) shows it
    only while another setting has that value."""
    if type(default) not in (int, float, bool, str):
        raise TypeError(f"Parameter {name!r} needs a number, bool or string literal default")
    value = _overrides.get(name, default)
    if type(default) is bool:
        if type(value) is not bool:
            raise TypeError(f"Parameter {name!r} needs true or false")
    elif type(default) is str:
        if type(value) is not str:
            raise TypeError(f"Parameter {name!r} needs text")
    else:
        if type(value) is bool or not isinstance(value, (int, float)):
            raise TypeError(f"Parameter {name!r} needs a number")
        value = float(value) if type(default) is float else int(value)
        if value != _overrides.get(name, default):
            raise ValueError(f"Parameter {name!r} needs a whole number")
        if min is not None and value < min or max is not None and value > max:
            raise ValueError(f"Parameter {name!r} must be between {min} and {max}")
    _used[name] = value
    return value


def choice(name, default, options, *, label=None, help=None, group=None, show_if=None):
    """Declare one of a fixed list of values: `options` is `[value, ...]` or `[(value, label), ...]`, all literals."""
    values = [item[0] if isinstance(item, (list, tuple)) else item for item in options]
    value = _overrides.get(name, default)
    if value not in values:
        raise ValueError(f"Setting {name!r} must be one of {', '.join(map(str, values))}")
    _used[name] = value
    return value


def pick(name, default=None, *, kinds=("factor",), multiple=False, pinned="version", label=None, help=None, group=None, show_if=None):
    """Declare a choice of library items or unpublished research objects of `kinds`. The value is fixed when picked:
    `{"name", "version"}` for a library version or `{"name", "objectId", "runId"}` for one run of an object; a list when `multiple`."""
    value = _overrides.get(name, default if default is not None else ([] if multiple else None))
    if multiple and not isinstance(value, list) or not multiple and value is not None and not isinstance(value, dict):
        raise TypeError(f"Setting {name!r} needs {'a list of picks' if multiple else 'one pick'}")
    _used[name] = value
    return value


def table(name, default, *, columns=None, label=None, help=None, group=None, show_if=None):
    """Declare a small table with fixed columns (such as limits by asset class): the value is a list of rows, each a dict of column to value."""
    value = _overrides.get(name, default)
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise TypeError(f"Setting {name!r} needs a list of rows")
    _used[name] = [dict(row) for row in value]
    return _used[name]


def library_param(name, default="", *, label=None):
    """Declare which library item this strategy reads: the panel offers the library's items, and the run hands the item's values to the strategy
    as library data (`on_data`), each at the moment it became available. The value is the item's name; an empty name means none chosen."""
    if type(default) is not str:
        raise TypeError(f"Parameter {name!r} needs the name of a library item")
    value = _overrides.get(name, default)
    if type(value) is not str:
        raise TypeError(f"Parameter {name!r} needs the name of a library item")
    _used[name] = value
    return value


_lot_rules = {}  # instrument id -> (step, least) of a stock's whole lots, or None: read once from its execution terms


def _lots(instrument):
    from decimal import Decimal
    key = str(instrument.id)
    if key not in _lot_rules:
        info = getattr(instrument, "info", None) or {}
        terms = info.get("execution_conditions") if hasattr(info, "get") else None
        rules = json.loads(terms)["rules"] if terms else None
        _lot_rules[key] = (Decimal(str(rules["increment"])), Decimal(str(rules["minimum"]))) if rules else None
    return _lot_rules[key]


def tradable_quantity(instrument, amount):
    """The most of `amount` an order for this instrument may carry, as its Quantity, or None when that is less than it allows. A stock trades in whole lots (China A shares: at
    least 100, in steps of 100), which the instrument carries in its execution terms; other instruments step by their size increment."""
    from decimal import ROUND_FLOOR, Decimal
    lots = _lots(instrument)
    if lots:
        step, least = lots
        units = (Decimal(str(amount)) / step).to_integral_value(rounding=ROUND_FLOOR) * step
        return instrument.make_qty(float(units), round_down=True) if units >= least and units > 0 else None
    if amount < instrument.size_increment.as_double():
        return None
    return instrument.make_qty(amount, round_down=True)


def library_data_type():
    """What a strategy subscribes to (`self.subscribe_data(q.library_data_type())`) to receive library values in `on_data(data)`: `data.data` is
    the value, with `.instrument_id`, `.field`, `.value`, `.version` and `.ts_event` (the moment it is about); it arrives at `.ts_init` (when it became available)."""
    from native.library_data import data_type
    return data_type()


class _Run:
    """The process's research run: what it was given and what its reports hand over for publishing."""
    publisher = None
    invocation = None
    mode = "run"  # run (standard report), work (a factor's Work numbers), values (only the values a publication would hold)
    cutoff = None  # values mode: the last bar label the code may see
    publications = []
    data = None
    work = None  # work mode: what q.report.factor handed over


def _bind_research(invocation, publisher):
    global _index, _index_bound
    _index, _index_bound = None, False
    run = _Run
    run.invocation, run.publisher, run.mode = invocation, publisher, invocation["mode"]
    run.cutoff, run.publications, run.data, run.work = invocation.get("cutoff"), [], None, None
    if invocation.get("support"):
        sys.path.insert(0, invocation["support"])


def _channel():
    from talyx_call import call
    return call


# --- data -------------------------------------------------------------------------------------------------------------

def _bind_work(panel, invocation):
    """A factor's Work run in the research process: the data is the panel the process keeps; q.report.factor hands its values over."""
    global _index, _index_bound
    _index, _index_bound = None, False
    _Run.invocation, _Run.publisher, _Run.mode = invocation, None, "work"
    _Run.cutoff, _Run.publications, _Run.work = None, [], None
    _Run.data = ResearchData.from_panel(panel)


class ResearchData:
    """The bars the object's data settings selected: one row per instrument and bar, and the same as tables of bars by instruments.

    Besides prices: `industry` (each instrument's industry), `shares` and `size` (market value, or 30-day dollar volume where there is
    none), `amount` (dollar volume, close × volume) and `vwap` (the average of high, low and close). What these lack is listed in the
    run's data limits."""

    readable = False

    def __init__(self, bars, info, extras=None):
        self.bars, self.info = bars, info
        self._wide = {}
        self._extras = extras or {}

    @classmethod
    def from_panel(cls, panel):
        """In a Work run every price the data holds is there, also outside index membership, as a formula sees it; who is tested each
        day is decided by the run settings afterwards."""
        import pandas as pd
        data = cls(None, panel.info)
        for name in ("open", "high", "low", "close", "volume"):
            data._wide[name] = cls._named(pd.DataFrame(panel.fields[name], index=panel.dates, columns=panel.columns))
        for name in ("vwap", "amount", "size"):
            data._wide[name] = cls._named(pd.DataFrame(panel.derived(name), index=panel.dates, columns=panel.columns))
        if panel.shares is not None:
            data._wide["shares"] = cls._named(pd.DataFrame(panel.shares, index=panel.dates, columns=panel.columns))
        data._extras["industry"] = panel.industry_series()
        return data

    @staticmethod
    def _named(table):
        table.index.name, table.columns.name = "date", "asset"
        return table

    @property
    def industry(self):
        """Each instrument's industry as a Series (None where the source has none); today's classification for every day with free sources."""
        found = self._extras.get("industry")
        if found is None:
            raise RuntimeError("These data settings have no industry: Market gives none for these instruments.")
        return found

    def _extra(self, name):
        if name not in self._wide:
            if name == "amount":
                self._wide[name] = self._field("rawClose") * self._field("rawVolume")
            elif name == "vwap":
                self._wide[name] = (self.high + self.low + self.close) / 3
            else:
                raise RuntimeError(f"These data settings have no {name}: Market gives none for these instruments.")
        return self._wide[name]

    amount = property(lambda self: self._extra("amount"))
    vwap = property(lambda self: self._extra("vwap"))
    size = property(lambda self: self._extra("size"))
    shares = property(lambda self: self._extra("shares"))

    @property
    def timeframe(self):
        return self.info["timeframe"]

    @property
    def annualization(self):
        """Bars per year of the selected instruments' trading calendar."""
        return self.info["annualization"]

    @property
    def instruments(self):
        return self.info["instruments"]

    def _field(self, field):
        if field not in self._wide:
            import pandas as pd
            table = self.bars.pivot(index="label", columns="column", values=field).sort_index()
            if 'isMember' in self.bars:
                table = table.where(self.bars.pivot(index='label', columns='column', values='isMember').reindex_like(table))
            table.index = pd.DatetimeIndex(pd.to_datetime(table.index, unit="ms", utc=True), name="date")
            table.columns.name = "asset"
            if self.readable:
                from collections import Counter
                # Reused codes keep the backend's unique labels; attrs always points to Market identity, never to a label.
                instruments = {item["column"]: item for item in self.info["instruments"]}
                names = [instruments[column]["displaySymbol"] for column in table.columns]
                counts = Counter(names)
                shown = [name if counts[name] == 1 else column for name, column in zip(names, table.columns, strict=True)]
                table.attrs["instruments"] = {name: instruments[column]["instrumentId"] for name, column in zip(shown, table.columns, strict=True)}
                table.columns = pd.Index(shown, name="asset")
            self._wide[field] = table
        return self._wide[field]

    open = property(lambda self: self._field("open"))
    high = property(lambda self: self._field("high"))
    low = property(lambda self: self._field("low"))
    close = property(lambda self: self._field("close"))
    volume = property(lambda self: self._field("volume"))


def _load(path, info, cutoff):
    import pandas as pd
    bars = pd.read_parquet(path)
    if cutoff is not None:
        bars = bars[bars["label"] <= cutoff]
    return ResearchData(bars, info)


def data(symbols=None, timeframe=None, start=None, end=None, *, index=None, list=None, adjustment="split"):  # noqa: A002 - `list` is the argument's name in notebooks
    """The bars of this object's data settings; or, asked for by `symbols` (Market symbol text), `index` (a symbol and optional membership date) or `list` (a research
    list by name), with a timeframe and dates, any bars through the platform. Columns of what you ask for read AAPL; the native ids are in `frame.attrs["instruments"]`."""
    asked = [name for name, value in (("symbols", symbols), ("index", index), ("list", list)) if value is not None]
    if not asked:
        if _Run.invocation is None or not _Run.invocation.get("research"):
            raise RuntimeError("These data settings give no bars: choose symbols or a research list in the data settings, or ask for bars with q.data(symbols, timeframe, start, end).")
        if _Run.data is None:
            research = _Run.invocation["research"]
            _Run.data = _load(research["barsPath"], research["info"], _Run.cutoff)
        return _Run.data
    if len(asked) > 1:
        raise TypeError("q.data takes symbols, index or list, one of them.")
    if timeframe is None or start is None:
        raise TypeError("q.data(symbols, timeframe, start, end) needs timeframe and start.")
    import builtins
    import time as _t
    began = _t.time()
    what = {"symbols": None if symbols is None else [symbols] if isinstance(symbols, str) else builtins.list(symbols), "index": index, "list": list}
    ask = {key: value for key, value in what.items() if value is not None}
    result = _read_bars({**ask, "timeframe": timeframe, "start": start, **({"end": end} if end else {}), "adjustment": adjustment}, what)
    import json as _json
    with open(result["info"]["path"], encoding="utf-8") as stream:
        info = _json.load(stream)
    loaded = _load(result["bars"]["path"], info, None)
    loaded.readable = True
    _note_loaded(f"q.data({', '.join(f'{key}={value!r}' for key, value in ask.items())}, {timeframe!r})", f"{len(info['instruments'])} instruments", began)
    return loaded


def _read_bars(request, what, every=1.0):
    """A bars read that can take minutes (an index of hundreds of names over the network): started, followed once a second in a block that updates in place
    (how many names, how long), and stopped by Interrupt. The platform answers a single call within seconds, so the wait is here, not in one call."""
    import time as _t
    started = _channel()("quant.read_research_bars", request, timeout=None)
    read_id = started["taskId"]
    shown = False

    def show(line):
        try:
            from IPython.display import display
        except ImportError:
            return False
        display({"text/plain": line}, raw=True, display_id="talyx-bars-" + read_id, update=shown)
        return True
    try:
        while True:
            view = _channel()("platform.tasks.get", {"taskId": read_id}, timeout=None)
            if view["state"] == "completed":
                return view["result"]
            if view["state"] in ("failed", "cancelled", "interrupted"):
                error = view.get("error") or {}
                raise RuntimeError(f"{error['code']}: {error['message']}" if error else "The bars read was stopped")
            view = view.get("progress") or {}
            total, done = view.get("total"), view.get("done") or 0
            line = f"Reading bars: {done:,} of {total:,} names" if total else "Reading bars"
            if view.get("waitingFor"):
                line = "Waiting for " + view["waitingFor"]
                if view.get("waitingUntil") is not None:
                    from datetime import UTC, datetime
                    moment = datetime.fromtimestamp(int(view["waitingUntil"]) / 1_000_000_000, UTC)
                    line += " until " + moment.strftime("%Y-%m-%d %H:%M:%S UTC")
            elif view.get("message"):
                line = view["message"] + (f": {done:,} of {total:,} names" if total else "")
            shown = show(line) or shown
            _t.sleep(every)
    except KeyboardInterrupt:
        _channel()("platform.tasks.cancel", {"taskId": read_id}, timeout=None)
        raise
    finally:
        if shown:  # the progress line goes when the read ends (it would stay at "2 of 3 names" above the table)
            show("")


def index_members(index, at, *, as_of=None):
    """Members of an exact index on a trading date or at a timezone-aware moment.

    index is a Market symbol or instrument ID, e.g. "^GSPC".
    Frozen runs use their recorded members; notebook reads default their knowledge cutoff to at.
    Unmapped members remain visible.
    """
    import pandas as pd

    def milliseconds(value):
        moment = pd.Timestamp(_iso(value))
        moment = moment.tz_localize("UTC") if moment.tzinfo is None else moment.tz_convert("UTC")
        return int(moment.value // 1_000_000)

    if not isinstance(index, str):
        raise TypeError("Use an index symbol or instrument ID.")
    stamp = milliseconds(at)
    cutoff = None if as_of is None else milliseconds(as_of)
    research = _Run.invocation["research"] if _Run.invocation is not None and "research" in _Run.invocation else None
    frozen = _index if _index_bound else research["info"]["index"] if research is not None and "index" in research["info"] else None
    if _index_bound and frozen is None:
        raise ValueError("Select a historical index in the backtest data settings before calling q.index_members.")
    if frozen is not None:
        selected = frozen["selection"]
        instrument = selected["instrument"]
        same = index in {
            instrument["instrumentId"], instrument["symbol"], instrument["displaySymbol"],
            instrument["venueId"] + ":" + instrument["symbol"]}
        if not same or (cutoff is not None and cutoff != selected.get("asOf", frozen["until"])) or not frozen["since"] <= stamp <= frozen["until"]:
            raise ValueError("The requested index, cutoff or instant differs from this run's frozen index range.")
        members = next(item["members"] for item in frozen["intervals"] if item["since"] <= stamp <= item["until"])
        rows = [{"symbol": (member["instrument"] or {}).get("instrumentId") or member["code"],
                 "instrumentId": (member["instrument"] or {}).get("instrumentId"),
                 "start": pd.Timestamp(member["effectiveTime"], unit="ms", tz="UTC").date().isoformat(),
                 "end": pd.Timestamp(member["effectiveUntil"], unit="ms", tz="UTC").date().isoformat() if member["effectiveUntil"] is not None else None,
                 "tradable":member["instrument"] is not None and not any(
                    item["instrumentId"] == member["instrument"]["instrumentId"] and item["since"] <= stamp <= item["until"]
                    for item in frozen["exclusions"])} for member in members]
    else:
        rows, cursor = [], None
        while True:
            page = _channel()("market.read_research", {"data":"members", "index":{"symbol":index, "at":_iso(at)},
                "asOf":_iso(as_of if as_of is not None else at), "limit":500, **({"cursor":cursor} if cursor else {})}, timeout=None)
            names = [col["name"] for col in page["columns"]]
            for values in page["rows"]:
                row = dict(zip(names, values, strict=True))
                rows.append({**row, "tradable":row["instrumentId"] is not None})
            cursor = page["nextCursor"]
            if cursor is None:
                break
    return pd.DataFrame(rows, columns=["symbol", "instrumentId", "start", "end", "tradable"])


def _iso(value):
    """Dates remain trading-day labels. A moment must have a time zone."""
    import datetime as dt

    import pandas as pd
    if isinstance(value, str) and len(value) == 10:
        return dt.date.fromisoformat(value).isoformat()
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value,int):
        raise TypeError("Use an ISO date or timezone-aware moment.")
    moment = pd.Timestamp(value)
    if moment.tzinfo is None:
        raise ValueError("A moment must specify a timezone.")
    return moment.isoformat()


def bars(symbol, timeframe, start, end=None):
    """Market OHLCV for one symbol, from start through end inclusive."""
    import pandas as pd
    result = _channel()("market.read_data", {"symbols":[symbol], "timeframe":timeframe, "start":_iso(start),
                        **({"end":_iso(end)} if end else {}), "format":"file"}, timeout=None)
    frame = pd.read_parquet(result["file"]["path"])
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop("time"), format="mixed", utc=True),name="time")
    values = frame[["open","high","low","close","volume"]].astype("float64")
    return values.sort_index()


# --- the library ------------------------------------------------------------------------------------------------------

_values_class = None


def _library_values_class():
    global _values_class
    if _values_class is not None:
        return _values_class
    import pandas as pd

    class LibraryValues(pd.DataFrame):
        """The values `q.library.read` returns: an ordinary table, which a cell shows as the item block (which version, how many rows, each value with the time it became known)."""
        _metadata = ["library_item"]

        @property
        def _constructor(self):
            return pd.DataFrame

        @property
        def run(self):
            """The run that produced this version of the item (its frozen prices, settings and report tables), when one did."""
            identity = (getattr(self, "library_item", None) or {}).get("run_id")
            if not identity:
                raise AttributeError("This version was not made by a run")
            return globals()["run"](run_id=identity)

        @property
        def published(self):
            """The day this version was put in the library, as text (2025-06-30)."""
            stamp = (getattr(self, "library_item", None) or {}).get("published")
            if stamp is None:
                raise AttributeError("This version has no publish date")
            import pandas as pd
            return f"{pd.Timestamp(stamp):%Y-%m-%d}"

    _values_class = LibraryValues
    return LibraryValues


def _library_block(frame, include=None, exclude=None):
    """The block the draft shows for a library item read in a notebook."""
    item = getattr(frame, "library_item", None) or {}
    days = frame["date"].nunique() if "date" in frame else 0
    names = frame["instrumentId"].nunique() if "instrumentId" in frame else 0
    span = ""
    if "date" in frame and len(frame):
        first, last = (frame["date"].min(), frame["date"].max())
        span = f", {first:%b} {first.day}, {first.year} \u2013 {last:%b} {last.day}, {last.year}"
    version = f" v{item['version']}" if item.get("version") else ""
    own = _name_of(frame) or "values"
    rows = [{"name": own, "code": "DataFrame", "text": f"{days:,} days \u00d7 {names:,} names{span}, each value with its name and the time it became known"}]
    # `.run` and `.published` under the name the cell gave the table (`v.run` after `v = q.library.read(...)`); the table's own `.values` is the DataFrame's array,
    # so the values stay in the table itself.
    if item.get("run_id"):
        rows.append({"name": f"{own}.run", "code": "Run", "text": "frozen prices, settings and report tables, as published"})
    if item.get("published") is not None:
        rows.append({"name": f"{own}.published", "code": "str", "text": _day_text(item["published"])})
    return {"application/vnd.talyx.runsummary+json": {"label": "Library item", "title": f"{item.get('name', '')}{version}", "meta": item.get("state") or "", "rows": rows}, "text/plain": f"{item.get('name', '')}{version}: {len(frame):,} rows"}


def _instant_ms(value, *, whole_day=False):
    """A moment given as Unix ms, a date or datetime string, a Timestamp or a datetime, as UTC Unix ms (naive values are UTC).
    `whole_day`: a date without a time of day means the end of that day, so an `end` of "2026-11-03" keeps that day's values."""
    import datetime as dt

    import pandas as pd
    if isinstance(value, bool):
        raise TypeError(f"Expected a date, got {value!r}")
    if isinstance(value, int):
        return value
    date_only = (isinstance(value, str) and ":" not in value) or (isinstance(value, dt.date) and not isinstance(value, dt.datetime))
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Not a date: {value!r} (give Unix ms, a date such as \"2026-01-31\" or a pandas Timestamp)") from error
    if stamp is pd.NaT:
        raise ValueError(f"Not a date: {value!r}")
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
    if whole_day and date_only:
        stamp = stamp.normalize() + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1)
    return int(stamp.value // 1_000_000)


class _Library:
    def read(self, name, field=None, *, version=None, symbols=None, start=None, end=None, as_of=None):
        """Read library values by their availability time; dates are inclusive."""
        import pandas as pd
        args = {"name":name, "format":"file"}
        for key,value in (("field",field),("version",version),("symbols",symbols)):
            if value is not None:
                args[key] = value
        for key,value in (("start",start),("end",end),("asOf",as_of)):
            if value is not None:
                args[key] = _iso(value)
        result = _channel()("quant.read_library_values", args, timeout=None)
        frame = _library_values_class()(pd.read_parquet(result["file"]["path"]))
        for key in ("date","availableAt","recordedAt"):
            frame[key] = pd.to_datetime(frame[key],utc=True)
        meta = result["meta"]
        frame.library_item = {"name":name,"version":meta["version"],"state":meta["state"],"run_id":meta["producedByRun"],
                              "published":meta["publishedAt"]}
        return frame

    def publish(self, name, frame, kind="data"):
        """Publish dated symbol, field and value rows."""
        clock = "date" if "date" in frame else "time"
        rows = frame[[clock,"symbol","field","value"]].to_dict("records")
        for row in rows:
            row[clock] = _iso(row[clock])
        return _channel()("quant.publish_library_values", {"name":name,"kind":kind,"rows":rows},timeout=None)


library = _Library()


def _milliseconds(values):
    import pandas as pd
    stamps = pd.to_datetime(values)
    stamps = stamps.dt.tz_localize("UTC") if stamps.dt.tz is None else stamps.dt.tz_convert("UTC")
    return stamps.map(lambda stamp: int(stamp.timestamp() * 1000))


_loaded = []  # what this notebook has read: {call, what, at, seconds}, shown in the side panel


def _note_loaded(call, what, started):
    import time as _t
    _loaded.append({"call": call, "what": what, "at": _t.strftime("%H:%M"), "seconds": round(_t.time() - started, 1)})


# --- reading a finished run -----------------------------------------------------------------------------------------

class Run:
    """A finished (or failed) run of an object, read by reference: its tables are the parquet files the run left and are read when first asked for.

    `run.state`, `run.settings`, `run.number`, `run.failure`; `run.files` lists the tables by name, `run.<name>` (or `run["<name>"]`) reads one."""

    def __init__(self, found):
        self._found = found
        self.state, self.settings, self.number, self.failure = found["state"], found.get("settings"), found.get("number"), found.get("failure")
        self.object, self.kind, self.id, self.data_to = found["object"], found["kind"], found["runId"], found.get("dataTo")
        self._paths = {path.rsplit("/", 1)[-1].removesuffix(".parquet"): location for path, location in found["paths"].items() if path.endswith(".parquet")}
        # What a run keeps as JSON at its top level (a factor's `work`, its `ls` series) reads as the dict it holds.
        self._json = {path.rsplit("/", 1)[-1].removesuffix(".json"): location for path, location in found["paths"].items() if path.endswith(".json") and path.count("/") <= 1 and not path.endswith("progress.json")}
        self.files = sorted({*self._paths, *self._json})
        self._frozen = found.get("data")

    @property
    def data(self):
        """The prices this run read, frozen as they were (a ResearchData, the same as q.data() gives inside the run)."""
        if not self._frozen:
            raise AttributeError(f"Run #{self.number} did not keep its prices")
        if getattr(self, "_data", None) is None:
            import json as _json
            with open(self._frozen["infoPath"], encoding="utf-8") as stream:   # by file: an index's information runs to tens of MB
                info = _json.load(stream)
            self._data = _load(self._frozen["barsPath"], info, None)
        return self._data

    def __getitem__(self, name):
        import pandas as pd
        if name in self._json and name not in self._paths:
            import json as _json
            with open(self._json[name], encoding="utf-8") as handle:
                return _json.load(handle)
        if name not in self._paths:
            if self.state == "failed":
                raise AttributeError(f"Run #{self.number} failed before its report; run.{name} is empty")
            raise KeyError(f"This run has no table named {name}; run.files lists them.")
        return pd.read_parquet(self._paths[name])

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError as error:
            raise AttributeError(str(error.args[0])) from None

    def __repr__(self):
        return f"Run #{self.number} of {self.object} ({self.kind}, {self.state}); tables: {', '.join(self.files) or 'none'}"

    def _headline(self):
        """The few numbers the run is known by, in the draft's wording: a factor's Rank IC, t and long\u2013short net from its Work numbers, a backtest's CAGR, Sharpe and drawdown, a model's the same as a factor's."""
        def signed(value, digits=4, percent=False):
            text = f"{abs(value) * 100:.1f}%" if percent else f"{abs(value):.{digits}f}"
            return ("+" if value >= 0 else "\u2212") + text
        numbers = (self["work"].get("numbers") if "work" in self._json else None) or self._found.get("metrics") or {}
        parts = []
        if numbers.get("rankIc") is not None:
            horizon = numbers.get("holding")
            parts.append(f"Rank IC{f' {horizon}D' if horizon else ''} {signed(numbers['rankIc'])}")
            if numbers.get("t") is not None:
                parts.append(f"t {numbers['t']:.2f}")
        if numbers.get("longShortNet") is not None:
            parts.append(f"long\u2013short net {signed(numbers['longShortNet'], percent=True)}")
        for key, label in (("cagr", "CAGR"), ("sharpe", "Sharpe"), ("maxDrawdown", "max drawdown")):
            if isinstance(numbers.get(key), (int, float)):
                shown = format(numbers[key], ".2f").replace("-", "\u2212") if key == "sharpe" else signed(numbers[key], percent=True)
                parts.append(f"{label} {shown}")
        return ", ".join(parts)

    def _repr_mimebundle_(self, include=None, exclude=None):
        """The block the draft shows for a run: which run, then what each attribute holds (tables with their size, read from the file's header only)."""
        rows = []
        import re as _re
        # how each table is reached in code: run.bars, or run["library-values"] for a name that is not a Python name
        reach = lambda name: f"run.{name}" if name.isidentifier() else f"run[{name!r}]"  # noqa: E731
        numbered = sorted((name for name in self.files if _re.fullmatch(r"report-\d+", name)), key=lambda name: int(name.split("-")[1]))  # a strategy's report is a long run of numbered tables: one row for all of them
        for name in self.files:
            if name in numbered:
                if name == min(numbered, key=lambda item: self.files.index(item)):
                    rows.append({"name": f"{reach(numbered[0])} … {reach(numbered[-1])}", "code": "DataFrame", "text": f"{len(numbered)} report tables"})
                continue
            if name in self._paths:
                import pyarrow.parquet as pq
                header = pq.ParquetFile(self._paths[name]).metadata   # a table the run wrote: one that cannot be read is an error, shown as one
                rows.append({"name": reach(name), "code": "DataFrame", "text": f"{header.num_rows:,} rows × {header.num_columns} columns"})
            else:
                rows.append({"name": reach(name), "code": "dict", "text": ""})
        if self.settings:
            rows.insert(0, {"name": "run.settings", "code": "dict", "text": ", ".join(f"{key} {value}" for key, value in list(self.settings.items())[:6] if isinstance(value, (str, int, float)))})
        headline = self._headline()
        if headline:
            rows.append({"name": "Headline", "code": "", "text": headline})
        bundle = {"title": f"#{self.number} of {self.object}" if self.number is not None else f"of {self.object}", "meta": f"{self.kind} · {self.state}" + (f" · data to {_day_text(self.data_to)}" if self.data_to else ""),
                  "rows": rows, **({"failure": self.failure if isinstance(self.failure, str) else str(self.failure)} if self.failure else {})}
        return {"application/vnd.talyx.runsummary+json": bundle, "text/plain": repr(self)}


def run(run_id):
    """Read the frozen research material for one run."""
    return Run(_channel()("quant.read_run_materials", {"runId":run_id}, timeout=None))


class Backtest:
    """A one-line backtest of a strategy, run as a run of that strategy tagged Notebook. `partial` is true when it was stopped before the end."""

    def __init__(self, run_id, number, view, partial):
        self.run_id, self.number, self.partial, self.state = run_id, number, partial, view.get("state")
        self._run = None
        self._view = view

    @property
    def run(self):
        if self._run is None:
            self._run = globals()["run"](run_id=self.run_id)
        return self._run

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self.run, name)

    def __repr__(self):
        return f"Backtest run #{self.number} ({self.state}{', stopped early' if self.partial else ''})"


def _name_of(value):
    """The name the cell's own code gave a table (`ex` in `data=ex`), so the block can say what the backtest read; None when it has no plain name."""
    shell = sys.modules["IPython"].get_ipython() if "IPython" in sys.modules else None
    if shell is None:   # called outside a notebook (a script, a test): no cell names it
        return None
    names = [key for key, item in shell.user_ns.items() if item is value and not key.startswith("_")]
    return names[0] if names else None


def _day_text(milliseconds):
    """A simulated moment (Unix ms) or a day the Host sends as 2026-11-06, as the block writes it: May 13, 2019."""
    import pandas as pd
    try:
        milliseconds = int(milliseconds)
    except (TypeError, ValueError):
        try:
            moment = pd.Timestamp(str(milliseconds)[:10])
        except (TypeError, ValueError):
            return str(milliseconds)[:10]
        return f"{moment:%b} {moment.day}, {moment.year}"
    moment = pd.Timestamp(int(milliseconds), unit="ms")
    return f"{moment:%b} {moment.day}, {moment.year}"


def _equity_figure(points):
    """The equity so far as the notebook draws it: the account's value against what it began with (one point a step), read out as `bt +2.1%`."""
    if not points or len(points) < 2:
        return None
    import pandas as pd
    from native import figures
    days = pd.to_datetime([point[0] for point in points], unit="ms", utc=True)
    trace = figures.line(figures.iso(days.tz_localize(None)), [point[1] for point in points], "main", "backtest_equity", "bt",
                         hovertemplate=figures.hover(None, ("bt", "%{y:+.1%}")))
    return figures.figure("backtest_equity", [trace], yaxis={"tickformat": "+.0%"}, height=110)


def _signal_file(data):
    """Values for a backtest to read in place of its library signal: a DataFrame of `datetime`, `instrument`, `value` rows, or a wide one (dates down, instruments across).
    Written beside the kernel's files; the backend copies it into the run."""
    import tempfile

    frame = data
    if {"date", "instrumentId", "value"} <= set(frame.columns):
        frame = frame.rename(columns={"date": "datetime", "instrumentId": "instrument"})
    if not {"datetime", "instrument", "value"} <= set(frame.columns):
        wide = frame.copy()
        wide.index.name = "datetime"
        frame = wide.stack().rename("value").reset_index().rename(columns={"level_1": "instrument"})
        if "instrument" not in frame.columns:
            frame.columns = ["datetime", "instrument", "value"]
    required = ["datetime", "instrument", "value"]
    frame = frame[[*required, *(["availableAt"] if "availableAt" in frame else [])]].dropna(subset=required)
    handle = tempfile.NamedTemporaryFile(prefix="talyx-signal-", suffix=".parquet", delete=False)
    handle.close()
    frame.to_parquet(handle.name, index=False)
    return handle.name


def backtest(strategy, params=None, *, data=None, signal=None, every=1.0):
    """Backtest a strategy by name with its own settings (and `params` over its parameters), showing progress; Interrupt stops it and keeps the part so far.
    `data` is a table of values the strategy reads in place of its library signal (`signal=` names which library input when it has several)."""
    import time
    request = {"strategy": strategy, **({"params": params} if params else {})}
    if data is not None:
        request["signal"] = {"param": signal, "path": _signal_file(data)}
    started = _channel()("quant.start_notebook_backtest", request, timeout=None)
    run_id, number = started["runId"], started["number"]
    given = _name_of(data) if data is not None else None
    shown = False
    began = time.time()
    try:
        while True:
            view = _channel()("quant.read_run", {"runId": run_id}, timeout=None)
            progress = view.get("progress") or {}
            steps = [{"name": "Environment", "done": view["state"] not in ("preparing", "queued")}, {"name": "Prices", "done": bool(progress)},
                     {"name": "Simulating " + _day_text(progress["simulatedTo"]) if progress.get("simulatedTo") else "Simulating", "now": bool(progress), "done": view["state"] == "completed"}, {"name": "Report", "done": view["state"] == "completed"}]
            elapsed = time.time() - began
            fraction = progress.get("value")
            left = None if not fraction else round(elapsed * (1 - fraction) / fraction)
            bundle = {"strategy": strategy, "steps": steps, "state": view["state"], "value": fraction, "elapsed": round(elapsed), "left": left, "runId": run_id, "number": number, **({"signal": given} if given else {})}
            if view["state"] == "completed":
                bundle["since"], bundle["until"] = (view.get("tested") or {}).get("start"), (view.get("tested") or {}).get("end")
                bundle["objectId"] = view.get("objectId")
                bundle["stats"] = {key: value for key, value in (view.get("numbers") or {}).items() if isinstance(value, (int, float)) and not isinstance(value, bool)}
                bundle["ranFor"] = round(elapsed)
            if view["state"] == "failed":
                bundle["failure"] = (view.get("failure") or {}).get("message") or "The backtest failed"
            curve = _equity_figure(progress.get("equity"))
            if curve is not None:
                bundle["equity"] = curve
            try:
                from IPython.display import display
                if shown:
                    display({"application/vnd.talyx.backtest+json": bundle, "text/plain": f"Backtest of {strategy}: {view['state']}"}, raw=True, display_id="talyx-backtest-" + run_id, update=True)
                else:
                    display({"application/vnd.talyx.backtest+json": bundle, "text/plain": f"Backtest of {strategy}: {view['state']}"}, raw=True, display_id="talyx-backtest-" + run_id)
                    shown = True
            except ImportError:
                pass
            if view["state"] in ("completed", "failed", "cancelled", "stopped"):
                _note_loaded(f"q.backtest({strategy!r})", f"run #{number} of the strategy, {view['state']}", began)
                return Backtest(run_id, number, view, partial=False)
            time.sleep(every)
    except KeyboardInterrupt:
        _channel()("quant.cancel_run", {"runId": run_id}, timeout=None)
        view = _channel()("quant.read_run", {"runId": run_id}, timeout=None)
        print(f"Stopped at {(view.get('progress') or {}).get('simulatedTo', 'the start')}, simulated; the part so far is in the result")
        return Backtest(run_id, number, view, partial=True)


# --- reports ----------------------------------------------------------------------------------------------------------

class ReportView:
    """A standard report; in a notebook the cell shows it with the same report components as a run's Report tab."""

    def __init__(self, index, root):
        self.index, self.root = index, root

    def _repr_mimebundle_(self, include=None, exclude=None):
        """The report as the cell shows it: the key numbers, a few small tables and up to six figures, all inside the output itself (the report's folder dies with the
        kernel's directory). Over the output limit the figures are dropped and the key numbers stay."""
        import json as _json
        from pathlib import Path
        root = Path(self.root)
        artifacts = {item["name"]: item for item in self.index.get("artifacts", [])}
        figures, tables = [], []
        for name, item in artifacts.items():
            path = root / name
            if item.get("kind") == "plotly" and len(figures) < 6 and path.is_file():
                figures.append({"title": item.get("title"), "figure": _json.loads(path.read_text(encoding="utf-8"))})
            elif item.get("kind") == "table" and len(tables) < 3 and path.is_file() and item.get("rows", 0) <= 40:
                import pandas as pd
                frame = pd.read_parquet(path)
                tables.append({"title": item.get("title"), "columns": list(frame.columns), "rows": _json.loads(frame.head(12).to_json(orient="values", date_format="iso"))})
        total_figures = sum(1 for item in artifacts.values() if item.get("kind") == "plotly")
        bundle = {"sections": len(self.index["sections"]), "metrics": self.index.get("metrics", {}), "tables": tables, "figures": figures, "moreFigures": max(0, total_figures - len(figures))}
        if len(_json.dumps(bundle, allow_nan=False, default=str)) > 1_500_000:
            bundle = {**bundle, "figures": [], "dropped": "Figures dropped: over the 2 MB output limit"}
        return {"application/vnd.talyx.report+json": bundle, "text/plain": f"TALYX report with {len(self.index['sections'])} sections"}


def _publisher():
    """The run's publisher; outside a run (a notebook cell) a report goes to a temporary folder."""
    if _Run.publisher is None:
        import tempfile

        from native.publish import Publisher
        _Run.publisher = Publisher({"runId": "notebook", "output": tempfile.mkdtemp(prefix="talyx-report-")})
    return _Run.publisher


def _capture(name, kind, values=None, **details):
    """Remember what a publication would hold; a standard-report run writes it beside the report."""
    research = _Run.invocation.get('research') if _Run.invocation is not None else None
    if values is not None and research is not None and 'index' in research['info']:
        source = data().bars
        priced = source[source['isMember'] & source['close'].notna()][['label','column']].rename(columns={'label':'datetime','column':'instrument'})
        values = values.merge(priced, on=['datetime','instrument'], how='inner', validate='many_to_one')
    _Run.publications.append({"name": name, "kind": kind, "values": values, "details": details})
    if _Run.mode == "values":
        return
    publisher = _publisher()
    item = {"name": name, "kind": kind, **details}
    if values is not None:
        path = publisher.root / "publication" / (name + ".parquet")
        path.parent.mkdir(parents=True, exist_ok=True)
        values.to_parquet(path, index=False)
        item.update(path=path.relative_to(publisher.root).as_posix(), rows=len(values))
    publisher.index.setdefault("publications", []).append(item)
    publisher.write()


def _long(table, field):
    """A table of bars by instruments as datetime (bar label in milliseconds), instrument, field and value."""
    import pandas as pd
    table = table.rename_axis(index="datetime", columns="instrument")
    long = table.stack(future_stack=True).dropna().rename("value").reset_index()
    index = pd.DatetimeIndex(long["datetime"])
    index = index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")
    return pd.DataFrame({"datetime": index.as_unit("ms").asi8,
                         "instrument": long["instrument"].astype(str), "field": field, "value": long["value"].astype(float)})


class _Report:
    def factor(self, factor, prices=None, *, name="factor", groups=None, settings=None, inputs=None):
        """The standard factor report. `inputs` ({name: table}) are the intermediate tables the values-on-a-day table shows beside each value.

        In a Work run the values and inputs are handed over and the run settings (holding, groups, preprocessing, costs) decide the test. Anywhere else
        (a notebook) the report measures it the same way: `settings` are the run settings (the object page's defaults where left out; `groups` sets
        their group count), on the bars of q.data() or of what q.data(...) returned when that is given as `prices`, or on a prices table (dates by assets,
        closes only)."""
        if _Run.mode == "work":
            _Run.work = {"factor": factor, "inputs": dict(inputs or {}), "name": name}
            return None
        from native import reports
        series = reports.index_pairs(factor, name)
        research = _Run.invocation.get('research') if _Run.invocation is not None else None
        if research is not None and 'index' in research['info']:
            table = series.unstack('asset')
            series = reports.index_pairs(table.where(data().close.reindex_like(table).notna()), name)
        values = _long(series.unstack("asset"), name)
        _capture(name, "factor", values, field=name)
        if _Run.mode == "values":
            return None
        settings = dict(settings or {})
        if groups is not None:
            settings["grouping"] = {**settings.get("grouping", {}), "count": int(groups)}
        table = series.unstack("asset")
        source = None
        if prices is None or isinstance(prices, ResearchData) or (_Run.data is not None and prices is _Run.data.close):
            source = prices if isinstance(prices, ResearchData) else data()
            if source.bars is None:
                raise ValueError("Give the prices as a table of dates by assets: these data have no bars to measure the factor on.")
            if source.readable:
                # Columns read AAPL; the measured bars keep the frozen columns.
                ids, column_of = source.close.attrs.get("instruments", {}), {item["instrumentId"]: item["column"] for item in source.info["instruments"]}
                table = table.rename(columns=lambda shown: column_of.get(ids.get(shown), shown))
        panel = reports.panel_of(source, None if source is not None else prices, None if source is not None else _annualization(reports.check_prices(prices)))
        publisher = _publisher()
        reports.factor(publisher, table, panel, settings, volumes=source is not None)
        return ReportView(publisher.index, publisher.root)

    def model(self, recorder, task=None, *, name="prediction"):
        """The standard model report of a trained Qlib Recorder; the prediction and the trained model become publishable."""
        from native import reports
        prediction = recorder.load_object("pred.pkl")
        from native import qlib_helpers
        score = qlib_helpers.original_names(prediction.iloc[:, 0].unstack("instrument").rename_axis(index="datetime"))
        _capture(name, "prediction", _long(score, "score"), field="score")
        if _Run.mode == "values":
            return None
        publisher = _publisher()
        trained_task = recorder.load_object('task') if task is None else task
        spec = qlib_helpers.freeze_model(publisher, name + '-model', recorder, trained_task)
        _capture(name + '-model', 'model', None, model=spec)
        reports.model(publisher, recorder, trained_task)
        return ReportView(publisher.index, publisher.root)

    def allocation(self, portfolio, *, name="weights"):
        """The standard allocation report of a skfolio Portfolio, MultiPeriodPortfolio or Population; the weights at each rebalance become publishable."""
        import pandas as pd
        from native import reports
        from skfolio import MultiPeriodPortfolio, Portfolio
        members = list(portfolio.portfolios) if isinstance(portfolio, MultiPeriodPortfolio) else [portfolio] if isinstance(portfolio, Portfolio) else None
        if members is not None:
            weights = pd.DataFrame({member.observations[0]: member.weights_dict for member in members}).T
            weights.index = pd.DatetimeIndex(weights.index)
            _capture(name, "allocation", _long(weights, "weight"), field="weight")
        if _Run.mode == "values":
            return None
        publisher = _publisher()
        reports.allocation(publisher, portfolio)
        return ReportView(publisher.index, publisher.root)


report = _Report()


class _Ops:
    """The formula's operators for code: `ops.Ref(close, 5)`, `ops.CSRank(x)`, `ops.Neutralize(x, data.industry, data.size)` on tables of
    dates by instruments. The same implementation as the one-line formula, so a formula turned into code gives the same values."""

    def __getattr__(self, name):
        from native import formula
        if name not in formula.OPERATORS:
            raise AttributeError(f"talyx_quant.ops has no {name}; the operators are " + ", ".join(formula.OPERATORS))

        def call(*args):
            industry = None
            if name == "IndRank" and _Run.invocation is not None:
                industry = data().industry
            return formula.apply(name, *args, industry=industry)
        call.__name__ = name
        call.__doc__ = formula.OPERATORS[name].signature + ": " + formula.OPERATORS[name].help
        return call


ops = _Ops()


def _annualization(prices):
    """Bars per year observed in the prices themselves, for a notebook that brought its own prices."""
    span = (prices.index[-1] - prices.index[0]).total_seconds() / (365.25 * 86400)
    return max(1, round((len(prices) - 1) / span))


class _Qlib:
    """Qlib set up on the object's bars: a provider, the experiment store, a ready LightGBM-on-Alpha158 task and rolling retraining."""

    def init(self, source=None):
        from native import qlib_helpers
        return qlib_helpers.init(_Run.invocation, data() if source is None else source.data if isinstance(source, Run) else source)

    def library_lightgbm(self, factors, *, rounds=100, train_bars=250, valid_bars=60, step=20, gap=2):
        """Train on frozen library factor values available by each bar's actual close."""
        from native import qlib_helpers
        return qlib_helpers.library_lightgbm(_Run.invocation, factors, rounds=rounds,
            train_bars=train_bars, valid_bars=valid_bars, step=step, gap=gap)

    def alpha158_lightgbm(self, source=None, *, rounds=100, train_bars=250, valid_bars=60, step=20, label="Ref($close, -2) / Ref($close, -1) - 1", gap=2):
        """`gap` bars separate the windows: at least as many as the label looks ahead."""
        from native import qlib_helpers
        return qlib_helpers.alpha158_lightgbm(data() if source is None else source, rounds=rounds, train_bars=train_bars, valid_bars=valid_bars,
                                              step=step, label=label, gap=gap)

    def train_rolling(self, task, *, step=20):
        """Retrain on everything before each window of `step` bars and predict that window; returns the summary Recorder."""
        from native import qlib_helpers
        return qlib_helpers.train_rolling(_Run.invocation, task, step=step)

    def train(self, task):
        """Train one task and return its Recorder."""
        from native import qlib_helpers
        return qlib_helpers.train(_Run.invocation, task)


qlib = _Qlib()


# --- allocation -------------------------------------------------------------------------------------------------------

def allocate(settings, *, estimator=None):
    """An allocation's walk-forward run. With only `settings` (the standard flow's values) TALYX builds the skfolio estimator they describe; with `estimator`, any
    unfitted skfolio optimization (or a Pipeline ending in one) is fitted afresh at every rebalance on the lookback days before it and held from the next close.
    Each period's weights are kept as the run's `weights` publication."""
    from native import allocation_run
    return allocation_run.allocate(settings, estimator)


# --- models -----------------------------------------------------------------------------------------------------------

def model_data(inputs=None, *, leave_out=None, processing=None, label=None, split=None):
    """A model's data set in a model run: the inputs on the data settings' names and days, processed (statistics over time fitted on each
    window's training segment only), the label from the next open, and the walk-forward windows. Without arguments the model's
    preparation decides each part. `.windows()` gives the windows this run trains; each has `.train`, `.valid` and `.test` rows (`.x`,
    `.y`, `.frame()`)."""
    from native import model_run
    return model_run.current().model_data(inputs, leave_out=leave_out, processing=processing, label=label, split=split)


def fit(algorithm, window, params=None, *, seed=42, reproducible=True):
    """Train one window with one of the algorithms the research environment runs (lightgbm, xgboost, catboost, linear, ridge, lasso, nnls,
    double_ensemble); `params` are that algorithm's own settings. Early stopping uses the window's validation rows."""
    from native import model_run
    return model_run.current().fit(algorithm, window, params, seed=seed, reproducible=reproducible)


def fit_network(net_class, window, params=None, *, seed=42, reproducible=True, **net_kwargs):
    """Train a network you wrote (a `torch.nn.Module` subclass, built as `net_class(**net_kwargs)`) on one window, in the deep learning
    environment: Adam on the squared error, epoch by epoch with early stopping on the validation rows, every epoch saved so a stopped window
    carries on from it. The network is given each row as `[N, features × look back]` (features outside, time from old to new, so
    `x.reshape(N, features, look_back)` is the same row) and gives one number a row. `params`: epochs, stop, batch, lr, lookback, device.
    Returns a model for `q.record(window, model)`."""
    from native import model_run
    return model_run.current().fit_network(net_class, net_kwargs, window, params or {}, seed=seed, reproducible=reproducible)


def record(window, model, *, predictions=None):
    """Keep one trained window: its model, its numbers by segment and its test predictions (`model.predict(window.test.x)`, or
    `predictions`). A run that never calls it has only whole-period numbers (see `predict`)."""
    from native import model_run
    return model_run.current().record(window, model, predictions=predictions)


def predict(scores):
    """Whole-period predictions from a model that trains its own way, without windows: a table of dates by names."""
    from native import model_run
    return model_run.current().predict(scores)


def feature(function=None, *, ready="close"):
    """Mark a function of the data (`def f(data) -> table`) as a model input written in code. `ready` says when its value is known:
    at the bar's close (default), at the next open, or at the next close (later than the label's start: the preparation flags it)."""
    if ready not in ("close", "next_open", "next_close"):
        raise ValueError("ready is close, next_open or next_close")

    def mark(found):
        found._talyx_feature = True
        found._talyx_ready = ready
        return found
    return mark(function) if function is not None else mark


def __getattr__(name):
    if name != "Strategy":
        raise AttributeError(name)
    from nautilus_trader.model import StrategyId
    from nautilus_trader.trading import Strategy, StrategyConfig

    class TalyxStrategy(Strategy):
        """Base for strategies written in TALYX: the data settings supply `instrument_ids` and `bar_types`."""

        def __init__(self):
            super().__init__(StrategyConfig(strategy_id=StrategyId("TALYX-001")))
            self.instrument_ids = []
            self.bar_types = []
            self.failure = None
            self._universe_key = None
            self._universe_ids = ()
            self._liquidation_orders = {}  # instrument id -> the liquidation stop this strategy keeps there
            self._recent_bars = {}  # instrument id -> the last two bars seen: [(ts_event, high, low)]
            self._stops_checked = set()  # positions whose opening bar was checked against their stop
            self._halted = None  # the run's (prices, ids of the closes sent for halted names), bound when it runs
            self._halted_checked = None  # the moment halted names were last looked for
            self._halted_closing = {}  # instrument -> the order closing it at its next price
            self._ledger = None  # the run's cost ledger, bound when it runs: its funding and borrow entries change the balance
            self._balances = {}  # (venue, currency) -> (when it was read, the account's total balance)
            self._balance_changed = -1  # the moment a fill, funding or borrow last changed a balance
            self._entries_seen = 0
            self._fills = 0
            self._liquidation_seen = None  # what the liquidation stops were last set from
            self._perpetual_ids = (None, [])
            self._crowded = False  # a venue holds more than one instrument

        def subscribe_bars(self, bar_type, *args, **kwargs):
            """Nautilus' own subscription, noting a bar type built from smaller bars so the run can say which periods it built."""
            text = str(bar_type)
            if "INTERNAL@" in text:
                built, source = text.split("@")[0], text.split("@")[1]
                _built[_period_name(built)] = _period_name(source)
            return super().subscribe_bars(bar_type, *args, **kwargs)

        def warming_up(self):
            """Whether the run is still only warming its indicators up (before its `tradeFrom`): no order is placed, and the log says so once."""
            if _trade_from is None or self.clock.timestamp_ns() // 1_000_000 >= _trade_from:
                return False
            if not _skipped:
                _skipped.append(True)
                log("Skip: warm-up (orders are placed from the start of the period)", kind="Skip")
            return True

        def submit_order(self, order, *args, **kwargs):
            """Nautilus' own, except that nothing is placed while the run warms up."""
            if self.warming_up():
                return None
            return super().submit_order(order, *args, **kwargs)

        @property
        def instrument_id(self):
            return self.instrument_ids[0]

        @property
        def bar_type(self):
            return self.bar_types[0]

        def reports(self, engine, frame):
            return {}

        def last_close(self, instrument_id):
            """The close of the newest bar the strategy has seen for this instrument, or None before its first bar."""
            bar_type = next(item for item in self.bar_types if item.instrument_id == instrument_id)
            bar = self.cache.bar(bar_type)
            return None if bar is None else float(bar.close)

        def position_size(self, instrument_id):
            """What the strategy holds of this instrument in base units: more than zero long, less than zero short."""
            return sum(float(str(position.signed_qty)) for position in self.cache.positions_open(instrument_id=instrument_id))

        def affordable(self, instrument_id, price):
            """The largest new position in this instrument the account's free balance pays for at `price`, its margin and taker fee
            included: an entry sized above it is refused by the venue whole."""
            instrument = self.cache.instrument(instrument_id)
            account = self.cache.account_for_venue(instrument.id.venue)
            currency = getattr(instrument, "settlement_currency", None) or instrument.quote_currency
            free = float(account.balance_free(currency).as_double())
            margin = 1.0 if str(account.account_type).endswith("CASH") else float(getattr(instrument, "margin_init", 0) or 0) or 1.0
            fee = (_run_settings.get("costs") or {}).get("feePct")
            fee = float(fee) / 100 if fee is not None else float((getattr(instrument, "info", None) or {}).get("taker_fee", 0) or 0)
            return fit_size(free, price, margin, fee)

        def equity(self):
            """The accounts' value in the first instrument's quote currency: each venue's cash, plus every open position's worth (a spot position at the
            last close; a margin position's open profit or loss from its own entry, so the two sides of a hedge each count). An account in another
            currency is converted at the run's latest known daily rate, as the report converts it."""
            first = self.cache.instrument(self.instrument_ids[0]).quote_currency
            total, venues = 0.0, {}  # venue -> whether its account is a margin account, read once a call (an account read is slow)
            for instrument_id in self.instrument_ids:
                instrument = self.cache.instrument(instrument_id)
                venue, currency = instrument.id.venue, instrument.quote_currency
                value = 0.0
                if venue not in venues:
                    venues[venue] = "MARGIN" in str(self.cache.account_for_venue(venue).account_type)
                    value += self._balance(instrument, currency)
                close = self.last_close(instrument_id)
                # A margin account's cash does not fall when it buys, so only the open profit or loss counts; a cash account paid for what it holds.
                margin = type(instrument).__name__ == "CryptoPerpetual" or venues[venue]
                for position in self.cache.positions_open(instrument_id=instrument_id):
                    size = float(str(position.signed_qty))
                    value += size * (close - float(position.avg_px_open)) if margin else size * close
                total += value if str(currency) == str(first) else value * self._rate(str(currency)) / self._rate(str(first))
            return total

        def _rate(self, currency):
            """One unit of `currency` in the run's report currency at the latest observed close (1 for the report currency)."""
            import numpy as np
            import pandas as pd
            if currency == _accounts.get("reportCurrency"):
                return 1.0
            if currency not in _rates:
                found = next((item for item in _accounts.get("conversions") or [] if item["currency"] == currency), None)
                if found is None:
                    raise ValueError(f"equity() adds accounts in {currency} and {_accounts.get('reportCurrency')}; the run has no {currency} rate to convert with.")
                table = pd.read_parquet(found["path"]).sort_values("time")
                _rates[currency] = (table["time"].astype("int64").to_numpy(), table["close"].astype(float).to_numpy())
            times, closes = _rates[currency]
            index = int(np.searchsorted(times, self.clock.timestamp_ns() // 1_000_000, side="right")) - 1
            if index < 0:
                raise ValueError(f"No known {currency} conversion at this simulation time.")
            return float(closes[index])

        def open_orders(self):
            """This strategy's orders that have not finished, those still on their way to the venue included (not the liquidation stop TALYX
            keeps for a leveraged perpetual)."""
            orders = self.cache.orders_inflight(strategy_id=self.strategy_id) + self.cache.orders_open(strategy_id=self.strategy_id)
            return [order for order in orders if "exit:liquidation" not in [str(tag) for tag in order.tags or []]]

        def __init_subclass__(cls, **kwargs):
            """After the author's `on_bar` and `on_order_filled`, the liquidation stop is brought in line with the position and the balance, and a
            name an order found halted is closed at its next price when the run says so."""
            super().__init_subclass__(**kwargs)
            for name in ("on_bar", "on_order_filled"):
                original = cls.__dict__.get(name)
                if original is None:
                    continue

                def make(original, name=name):
                    @functools.wraps(original)
                    def wrapped(self, *args, **kw):
                        if name == "on_order_filled":
                            self._filled()
                        result = original(self, *args, **kw)
                        if name == "on_bar" and args:
                            self._stop_in_entry_bar(args[0])
                        self._sync_liquidation()
                        self._close_halted()
                        return result
                    return wrapped
                setattr(cls, name, make(original))

        def on_order_filled(self, event):
            self._filled()
            self._sync_liquidation()

        def _balance(self, instrument, currency=None):
            """The total balance of the instrument's venue account in `currency` (its settlement currency by default). Read from the account
            again only when a fill, a funding or a borrow may have changed it: at that moment, and once at the next."""
            currency = currency or getattr(instrument, "settlement_currency", None) or instrument.quote_currency
            now = self.clock.timestamp_ns()
            entries = len(self._ledger.entries) if self._ledger is not None else 0
            if entries != self._entries_seen:
                self._entries_seen, self._balance_changed = entries, now
            key = (str(instrument.id.venue), str(currency))
            kept = self._balances.get(key)
            if kept is None or kept[0] <= self._balance_changed:
                kept = (now, float(str(self.cache.account_for_venue(instrument.id.venue).balance_total(currency).as_decimal())))
                self._balances[key] = kept
            return kept[1]

        def _filled(self):
            self._fills += 1
            self._balance_changed = self.clock.timestamp_ns()

        def _stop_in_entry_bar(self, bar):
            """an order sent on a bar reaches the venue after the next bar's prices have gone by, so a stop placed when the entry fills at
            that bar's open only works from the bar after. When the bar the position opened in went through its stop, the position is closed
            now with an order the fill model prices at the stop, in that bar: as the venue would have, the stop being there from the fill on."""
            from nautilus_trader.model import OrderSide
            instrument_id = bar.bar_type.instrument_id
            recent = self._recent_bars.setdefault(instrument_id, [])
            recent.append((int(bar.ts_event), float(bar.high), float(bar.low)))
            del recent[:-2]
            for position in self.cache.positions_open(instrument_id=instrument_id):
                key = str(position.id) + "@" + str(position.ts_opened)
                opened = next((item for item in recent if item[0] == int(position.ts_opened)), None)
                if key in self._stops_checked or opened is None:
                    continue
                self._stops_checked.add(key)
                long = float(str(position.signed_qty)) > 0
                orders = self.cache.orders_inflight(instrument_id=instrument_id, strategy_id=self.strategy_id) + self.cache.orders_open(instrument_id=instrument_id, strategy_id=self.strategy_id)
                for order in orders:
                    tags = [str(tag) for tag in order.tags or []]
                    if "exit:liquidation" in tags or str(order.order_type).rsplit(".", 1)[-1] != "STOP_MARKET" or (order.side == OrderSide.SELL) != long:
                        continue
                    trigger = float(order.trigger_price.as_double())
                    if opened[2] <= trigger if long else opened[1] >= trigger:
                        self.cancel_order(order.client_order_id)
                        close = self.order_factory.market(instrument_id, order.side, order.quantity, reduce_only=True, tags=tags)
                        _entry_bar_stops[str(close.client_order_id)] = (trigger, opened[0])
                        self.submit_order(close)
                    break

        def _close_halted(self):
            """Halted names: close at next price. Once a moment, a held name whose peers have a bar and it has none is halted: its position is
            closed with a market order (tag exit:halted) that waits for the name's next bar and fills at that bar's price by the run's fill
            timing."""
            if _run_settings.get("halted") != "close" or self._halted is None:
                return
            now = self.clock.timestamp_ns()
            if now == self._halted_checked:
                return
            self._halted_checked = now
            from nautilus_trader.model import OrderSide
            prices, closing = self._halted
            for position in self.cache.positions_open():
                name = str(position.instrument_id)
                pending = self._halted_closing.get(name)
                if not prices.halted_at(name, now) or (pending is not None and not self.cache.order(pending).is_closed):
                    continue
                size = float(str(position.signed_qty))
                order = self.order_factory.market(position.instrument_id, OrderSide.SELL if size > 0 else OrderSide.BUY,
                                                  self.cache.instrument(position.instrument_id).make_qty(abs(size)), reduce_only=True, tags=["exit:halted"])
                self._halted_closing[name] = order.client_order_id
                closing.add(str(order.client_order_id))
                self.submit_order(order, position_id=position.id)

        def _maintenance_rate(self):
            return float((_run_settings.get("exchange") or {}).get("maintenancePct", 0.5)) / 100

        def _perpetuals(self):
            ids = tuple(self.instrument_ids)
            if self._perpetual_ids[0] != ids:
                self._perpetual_ids = (ids, [item for item in ids if type(self.cache.instrument(item)).__name__ == "CryptoPerpetual"])
            return self._perpetual_ids[1]

        def liquidation_price(self, instrument_id):
            """Where a leveraged perpetual position is closed out: the price at which the account's equity (balance, plus the open profit of the other
            positions at their last close, plus this one's) falls to the account's maintenance margin (the rate times every position's value, the
            others' at their last close). None when there is no position or it cannot be reached."""
            held = self.position_size(instrument_id)
            if not held:
                return None
            maintenance = self._maintenance_rate()
            instrument = self.cache.instrument(instrument_id)
            equity = self._balance(instrument)
            for other in self._perpetuals():
                close = self.last_close(other)
                if other != instrument_id and close is not None and self.cache.instrument(other).id.venue == instrument.id.venue:
                    for item in self.cache.positions_open(instrument_id=other):
                        size = float(str(item.signed_qty))
                        equity += size * (close - float(item.avg_px_open)) - maintenance * abs(size) * close
            average = float(self.cache.positions_open(instrument_id=instrument_id)[0].avg_px_open)
            price = (average - equity / held) / (1 - maintenance) if held > 0 else (average + equity / -held) / (1 + maintenance)
            return price if price > 0 else None

        def _under_maintenance(self):
            """The venues whose account, its perpetual positions valued at their last close, has equity at or under its maintenance margin. Notes
            whether a venue holds more than one instrument, whose liquidation prices then move with each other's closes."""
            maintenance, found = self._maintenance_rate(), {}
            for instrument_id in self._perpetuals():
                positions = self.cache.positions_open(instrument_id=instrument_id)
                instrument = self.cache.instrument(instrument_id)
                venue = instrument.id.venue
                if venue not in found:
                    found[venue] = [self._balance(instrument), 0.0, 0]
                if not positions:
                    continue
                close = self.last_close(instrument_id)
                for item in positions:
                    size = float(str(item.signed_qty))
                    found[venue][0] += size * (close - float(item.avg_px_open))
                    found[venue][1] += maintenance * abs(size) * close
                found[venue][2] += 1
            self._crowded = any(held > 1 for _, _, held in found.values())
            return {venue for venue, (equity, margin, held) in found.items() if held and equity <= margin}

        def _sync_liquidation(self):
            """Liquidation, as a venue does it on one margin account. Each leveraged perpetual position keeps one reduce-only stop-market order at its
            liquidation price (tag exit:liquidation), moved when the positions or the balance change and filled by the venue's bar matching (at the
            open when the bar gaps through it), so a position is closed inside the bar that reaches the price. An account whose equity is at or
            under its maintenance margin at a close (positions falling together, or a stop that could not be moved because the price had passed
            it) has every position closed out at the next price. In Hedge mode, whose two sides have no one price to stop at, only that check
            applies."""
            exchange = _run_settings.get("exchange") or {}
            if not exchange.get("liquidation"):
                return
            from nautilus_trader.model import OrderSide
            under = self._under_maintenance()
            perpetuals = self._perpetuals()
            orders = {item: self.cache.order(self._liquidation_orders[item]) for item in perpetuals if item in self._liquidation_orders}
            # The stops follow the positions, the balance and their own orders, and the other instruments' closes on a venue holding more than one:
            # with none of these changed since the last time, they stand.
            seen = (self._fills, tuple(value for _, value in self._balances.values()), frozenset(under),
                    tuple(order is None or order.is_closed for order in orders.values()), self.clock.timestamp_ns() if self._crowded else None)
            if seen == self._liquidation_seen:
                return
            self._liquidation_seen = seen
            for instrument_id in perpetuals:
                instrument = self.cache.instrument(instrument_id)
                order = orders.get(instrument_id)
                mine = [order] if order is not None and not order.is_closed else []
                if instrument.id.venue in under:
                    if mine and str(mine[0].order_type).rsplit(".", 1)[-1] != "MARKET":
                        self.cancel_order(mine[0].client_order_id)
                    elif mine:
                        continue  # being closed out already
                    for position in self.cache.positions_open(instrument_id=instrument_id):
                        size = float(str(position.signed_qty))
                        close = self.order_factory.market(instrument_id, OrderSide.SELL if size > 0 else OrderSide.BUY, instrument.make_qty(abs(size)),
                                                          reduce_only=True, tags=["exit:liquidation"])
                        self._liquidation_orders[instrument_id] = close.client_order_id
                        self.submit_order(close, position_id=position.id)
                    continue
                if mine and str(mine[0].order_type).rsplit(".", 1)[-1] == "MARKET":
                    continue
                price = self.liquidation_price(instrument_id) if exchange.get("positionMode") != "hedge" else None
                if price is None:
                    for order in mine:
                        self.cancel_order(order.client_order_id)
                    continue
                held = self.position_size(instrument_id)
                target, quantity = instrument.make_price(price), instrument.make_qty(abs(held))
                if mine:
                    current = mine[0]
                    if abs(float(current.trigger_price) - float(target)) < float(instrument.price_increment) / 2 and float(current.quantity) == float(quantity):
                        continue
                    if (current.side == OrderSide.SELL) == (held > 0):
                        self.modify_order(current.client_order_id, quantity=quantity, trigger_price=target)
                        continue
                    self.cancel_order(current.client_order_id)
                stop = self.order_factory.stop_market(instrument_id, OrderSide.SELL if held > 0 else OrderSide.BUY, quantity, target, trigger_type=self.stop_trigger,
                                                      reduce_only=True, tags=["exit:liquidation"])
                self._liquidation_orders[instrument_id] = stop.client_order_id
                self.submit_order(stop)

        @property
        def max_position(self):
            """The largest position as a share of equity in this run's settings (Max position)."""
            return float(_run_settings.get("maxPosition", 1))

        @property
        def stop_trigger(self):
            """The price a stop is triggered by in this run's settings (last, mark or index), as a Nautilus TriggerType for stop orders."""
            from nautilus_trader.model import TriggerType
            return {"last": TriggerType.LAST_PRICE, "mark": TriggerType.MARK_PRICE, "index": TriggerType.INDEX_PRICE}[
                (_run_settings.get("exchange") or {}).get("stopTrigger", "last")]

        def close(self, instrument_id=None, reason="signal"):
            """Close the position in one instrument (all when left out); `reason` is the exit shown with each trade (stop, trail, signal ...)."""
            tags = ["exit:" + str(reason)]
            for item in [instrument_id] if instrument_id is not None else self.instrument_ids:
                if self.cache.positions_open(instrument_id=item):
                    self.close_all_positions(item, tags=tags)

        def order_target_weight(self, instrument_id, weight, reason="rebalance"):
            """Trade one instrument to `weight` of the account's equity (negative is short) with a market order; returns the order or None (in Hedge
            mode, the last of the orders it takes)."""
            close = self.last_close(instrument_id)
            if close is None or close <= 0 or self.warming_up():
                return None
            instrument = self.cache.instrument(instrument_id)
            change = float(weight) * self.equity() / close - self.position_size(instrument_id)
            if abs(change) < float(instrument.size_increment) / 2:
                return None
            orders = self._trade(instrument_id, change, ["entry:" + str(reason)])
            return orders[-1] if orders else None

        def _trade(self, instrument_id, change, tags):
            """Market orders that change what is held of one instrument by `change` (base units). One order in One-way mode. In Hedge mode an order
            without a position opens a new one beside the old, so a position on the side away from the target is closed, and the rest is taken
            from, or added to, the positions on the target's side: the gross held is never more than the target asks."""
            from nautilus_trader.model import OrderSide
            instrument = self.cache.instrument(instrument_id)
            step = float(instrument.size_increment) / 2
            if (_run_settings.get("exchange") or {}).get("positionMode") != "hedge":
                quantity = instrument.make_qty(abs(change))
                if quantity.as_double() <= 0:
                    return []
                order = self.order_factory.market(instrument_id, OrderSide.BUY if change > 0 else OrderSide.SELL, quantity, tags=tags)
                self.submit_order(order)
                return [order]
            positions = [(position, float(str(position.signed_qty))) for position in self.cache.positions_open(instrument_id=instrument_id)]
            target = sum(size for _, size in positions) + change
            orders = []

            def send(size, position=None, reduce=False):
                quantity = instrument.make_qty(abs(size))
                if quantity.as_double() <= 0:
                    return 0.0
                order = self.order_factory.market(instrument_id, OrderSide.BUY if size > 0 else OrderSide.SELL, quantity, reduce_only=reduce, tags=tags)
                self.submit_order(order, **({"position_id": position.id} if position is not None else {}))
                orders.append(order)
                return quantity.as_double() * (1 if size > 0 else -1)

            kept = []
            for position, size in positions:
                if abs(target) < step or (size > 0) != (target > 0):
                    send(-size, position, reduce=True)  # the side away from the target: closed
                else:
                    kept.append([position, size])
            rest = target - sum(size for _, size in kept)
            for item in kept:  # the target's side holds more than asked: take it off these positions
                if abs(rest) < step or (rest > 0) == (target > 0):
                    break
                part = -min(abs(item[1]), abs(rest)) if item[1] > 0 else min(abs(item[1]), abs(rest))
                rest -= send(part, item[0], reduce=True)
            if abs(rest) >= step and abs(target) >= step:
                send(rest, kept[0][0] if kept else None)  # added to the position on the target's side, or a new one
            return orders

        def rebalance_to(self, weights, *, tag="rebalance"):
            """Trade to target weights of the account's equity, `{instrument id: weight}` (negative is short; instruments left out are not touched).
            Sells go first, then buys; a name with no price now (halted) is refused by the venue and stays as it is. Returns the orders."""
            if self.warming_up():
                return []
            equity = self.equity()
            _rebalances.append({"t": _now_ms(), "targets": {str(key): float(weight) for key, weight in weights.items()}})
            plan = []
            for key, weight in weights.items():
                instrument_id = next((item for item in self.instrument_ids if str(item) == str(key)), None)
                if instrument_id is None:
                    raise ValueError(f"{key} is not one of this strategy's instruments.")
                close = self.last_close(instrument_id)
                if close is None or close <= 0:
                    continue
                change = float(weight) * equity / close - self.position_size(instrument_id)
                instrument = self.cache.instrument(instrument_id)
                if abs(change) < float(instrument.size_increment) / 2:
                    continue  # less than one step of size: nothing to trade
                plan.append((instrument_id, change))
            orders = []
            for instrument_id, change in sorted(plan, key=lambda item: item[1] > 0):
                orders.extend(self._trade(instrument_id, change, ["entry:" + str(tag)]))
            return orders

        def universe(self, at=None):
            """Members with prices at this UTC millisecond; explicit symbol lists retain every symbol."""
            if _index is None:
                return list(self.instrument_ids)
            from native.index_data import eligible
            moment = self.clock.timestamp_ns() // 1_000_000 if at is None else at
            key = id(_index), moment
            if self._universe_key != key:
                self._universe_ids = tuple(item for item in self.instrument_ids if eligible(_index, str(item), moment))
                self._universe_key = key
            return list(self._universe_ids)

        def is_tradable(self, instrument_id, at=None):
            """False before the name's first bar (listed during the run) and for the disclosed missing-price intervals of an index member."""
            if self.last_close(instrument_id) is None:
                return False
            if _index is None:
                return True
            from native.index_data import can_trade
            moment = self.clock.timestamp_ns() // 1_000_000 if at is None else at
            return can_trade(_index, str(instrument_id), moment)

    globals()["Strategy"] = TalyxStrategy
    return TalyxStrategy
