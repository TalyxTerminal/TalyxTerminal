"""Types of `talyx_quant` (imported as `q`) for the editor: completion and checks of research and strategy code.

What the module gives at run time is in talyx_quant.py; this file only describes it. The formula operators under `ops` are listed
one by one from the formula engine's declarations (native/formula.py).
"""
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Literal, TypeVar, overload

import pandas as pd

_Literal = int | float | bool | str
_S = TypeVar("_S", bound=str)
_Table = pd.DataFrame


# --- settings: literal arguments only, read without running the code ---------------------------------------------------
@overload
def param(name: str, default: bool, *, min: None = None, max: None = None, step: None = None, label: str | None = None, help: str | None = None,
          group: str | None = None, show_if: Mapping[str, _Literal] | None = None) -> bool: ...
@overload
def param(name: str, default: int, *, min: int | float | None = None, max: int | float | None = None, step: int | float | None = None, label: str | None = None,
          help: str | None = None, group: str | None = None, show_if: Mapping[str, _Literal] | None = None) -> int: ...
@overload
def param(name: str, default: float, *, min: int | float | None = None, max: int | float | None = None, step: int | float | None = None, label: str | None = None,
          help: str | None = None, group: str | None = None, show_if: Mapping[str, _Literal] | None = None) -> float: ...
@overload
def param(name: str, default: str, *, min: None = None, max: None = None, step: None = None, label: str | None = None, help: str | None = None,
          group: str | None = None, show_if: Mapping[str, _Literal] | None = None) -> str: ...
def choice(name: str, default: _S, options: Sequence[_S | tuple[_S, str]], *, label: str | None = None, help: str | None = None, group: str | None = None,
           show_if: Mapping[str, _Literal] | None = None) -> _S:
    """One of a fixed list of literal values; `options` is [value, ...] or [(value, label), ...]."""
@overload
def pick(name: str, default: dict[str, Any] | None = None, *, kinds: Sequence[str] = ("factor",), multiple: Literal[False] = False,
         pinned: Literal["version", "run"] = "version", label: str | None = None, help: str | None = None, group: str | None = None,
         show_if: Mapping[str, _Literal] | None = None) -> dict[str, Any] | None: ...
@overload
def pick(name: str, default: list[dict[str, Any]] | None = None, *, kinds: Sequence[str] = ("factor",), multiple: Literal[True],
         pinned: Literal["version", "run"] = "version", label: str | None = None, help: str | None = None, group: str | None = None,
         show_if: Mapping[str, _Literal] | None = None) -> list[dict[str, Any]]: ...
def table(name: str, default: list[dict[str, _Literal]], *, columns: Sequence[str] | None = None, label: str | None = None, help: str | None = None,
          group: str | None = None, show_if: Mapping[str, _Literal] | None = None) -> list[dict[str, _Literal]]:
    """A small table with fixed columns: a list of rows, each a dict of column to value."""
def library_param(name: str, default: str = "", *, label: str | None = None) -> str:
    """Which library item a strategy reads; its values arrive in `on_data`."""
def library_data_type() -> Any:
    """What a strategy subscribes to (`self.subscribe_data(q.library_data_type())`) to receive library values in `on_data`."""


# --- data ---------------------------------------------------------------------------------------------------------------
class ResearchData:
    """The bars of the object's data settings: `bars` has one row per instrument and bar; the fields are tables of dates by instruments."""
    bars: pd.DataFrame
    info: dict[str, Any]
    @property
    def open(self) -> pd.DataFrame: ...
    @property
    def high(self) -> pd.DataFrame: ...
    @property
    def low(self) -> pd.DataFrame: ...
    @property
    def close(self) -> pd.DataFrame: ...
    @property
    def volume(self) -> pd.DataFrame: ...
    @property
    def amount(self) -> pd.DataFrame:
        """Dollar volume, close × volume."""
    @property
    def vwap(self) -> pd.DataFrame:
        """The average of high, low and close."""
    @property
    def size(self) -> pd.DataFrame:
        """Market value (close × shares outstanding), or 30-day dollar volume where there is none."""
    @property
    def shares(self) -> pd.DataFrame:
        """Shares outstanding as of each day, where Market has them."""
    @property
    def industry(self) -> pd.DataFrame:
        """Each instrument's industry on each date (dates by instruments): as known then where Market keeps dated answers (A shares),
        else the source's current classification on every date."""
    @property
    def timeframe(self) -> str: ...
    @property
    def annualization(self) -> float:
        """Bars per year of the instruments' trading calendar."""
    @property
    def instruments(self) -> list[dict[str, Any]]: ...

_Adjustment = Literal["raw", "split", "split_dividend"]
@overload
def data() -> ResearchData: ...
@overload
def data(symbols: str | Iterable[str], timeframe: str, start: str, end: str | None = None, *, adjustment: _Adjustment = "split_dividend") -> ResearchData: ...
@overload
def data(*, index: dict[str, Any], timeframe: str, start: str, end: str | None = None, adjustment: _Adjustment = "split_dividend") -> ResearchData: ...
@overload
def data(*, list: str, timeframe: str, start: str, end: str | None = None, adjustment: _Adjustment = "split_dividend") -> ResearchData: ...
def index_members(index: str, at: int | str | pd.Timestamp, *, as_of: int | str | pd.Timestamp | None = None) -> pd.DataFrame:
    """An exact index's members on a trading date or at a moment, known by as_of (default at); unmapped members are kept."""
def bars(symbol: str, timeframe: str, start: str, end: str | None = None) -> pd.DataFrame:
    """Market OHLCV for one symbol, from start through end inclusive, indexed by UTC time."""


# --- the library ----------------------------------------------------------------------------------------------------------
class _Library:
    def read(self, name: str, field: str | None = None, *, version: int | None = None, instrument: str | None = None, start: int | str | pd.Timestamp | None = None,
             end: int | str | pd.Timestamp | None = None, as_of: int | str | pd.Timestamp | None = None) -> pd.DataFrame:
        """Values of a library item; dates are Unix ms, a date string or a Timestamp (UTC when naive); a date alone as `end` keeps that whole day."""
    def publish(self, name: str, frame: pd.DataFrame, kind: str = "data") -> dict[str, Any]: ...
library: _Library


# --- reports --------------------------------------------------------------------------------------------------------------
class ReportView: ...

class _Report:
    def factor(self, factor: pd.DataFrame | pd.Series, prices: pd.DataFrame | None = None, *, name: str = "factor", groups: int = 5,
               periods: Sequence[int] = (1, 5, 10), long_short: bool = True, max_loss: float = 0.35, quick: bool | None = None,
               inputs: Mapping[str, pd.DataFrame] | None = None) -> ReportView | None:
        """Hand over the factor's values (a table of dates by instruments); `inputs` are tables shown beside each value on a day."""
    def model(self, recorder: Any, task: Any = None, *, name: str = "prediction") -> ReportView | None: ...
    def allocation(self, portfolio: Any, *, name: str = "weights") -> ReportView | None: ...
report: _Report


class _Qlib:
    def init(self, source: ResearchData | Run | None = None) -> Any: ...
    def library_lightgbm(self, factors: Sequence[str], *, rounds: int = 100, train_bars: int = 250, valid_bars: int = 60, step: int = 20, gap: int = 2) -> Any: ...
    def alpha158_lightgbm(self, source: ResearchData | None = None, *, rounds: int = 100, train_bars: int = 250, valid_bars: int = 60, step: int = 20,
                          label: str = ..., gap: int = 2) -> Any: ...
    def train_rolling(self, task: Any, *, step: int = 20) -> Any: ...
    def train(self, task: Any) -> Any: ...
qlib: _Qlib


def model(name: str, version: int | None = None) -> Any:
    """A library model: in a strategy run the frozen one it declared, in a notebook loaded in the kernel from its frozen files."""
def publish_model(name: str, artifacts: Any, loader: Callable[..., Any], predictor: Callable[..., Any], training_cutoff: int) -> dict[str, Any]: ...


# --- training a model window by window (a model run) --------------------------------------------------------------------------
class Segment:
    """One segment's rows of a window: `x` (rows × features, processed), `y` (the label), `t` and `i` (bar and name positions)."""
    x: Any
    y: Any
    t: Any
    i: Any
    features: list[str]
    def frame(self) -> pd.DataFrame: ...
    def __len__(self) -> int: ...
class Window:
    """One walk-forward window: its number, spans and its training, validation and test rows (processing fitted on training only)."""
    k: int
    train: Segment
    valid: Segment
    test: Segment
    spans: dict[str, dict[str, int]]
class ModelData:
    features: list[str]
    def windows(self) -> list[Window]:
        """The windows this run trains: those its window cache does not hold yet."""
class Fitted:
    algorithm: str
    def predict(self, x: Any) -> Any: ...
def model_data(inputs: Sequence[Mapping[str, Any]] | None = None, *, leave_out: Mapping[str, Any] | None = None, processing: Mapping[str, Any] | None = None,
               label: Mapping[str, Any] | None = None, split: Mapping[str, Any] | None = None) -> ModelData:
    """The run's data set from its inputs, processing, label (from the next open) and walk-forward split."""
def fit(algorithm: str, window: Window, params: Mapping[str, Any] | None = None, *, seed: int = 42, reproducible: bool = True) -> Fitted:
    """Train one window with an algorithm of the research environment (lightgbm, xgboost, catboost, linear, ridge, lasso, nnls, double_ensemble)."""
def fit_network(net_class: type, window: Window, params: Mapping[str, Any] | None = None, *, seed: int = 42, reproducible: bool = True, **net_kwargs: Any) -> Fitted:
    """Train your own torch.nn.Module (net_class(**net_kwargs)) on one window in the deep learning environment: Adam, squared error, early stopping, saved every epoch.
    The network gets rows as [N, features x look back] and gives one number a row. params: epochs, stop, batch, lr, lookback, device."""
def record(window: Window, model: Any, *, predictions: Any = None) -> None:
    """Keep one window: its model in the window cache, its numbers, importance and test predictions."""
def predict(scores: pd.DataFrame | pd.Series) -> None:
    """Whole-period predictions from code that trains its own way, without windows."""
_F = TypeVar("_F", bound=Callable[..., Any])
@overload
def feature(function: _F) -> _F: ...
@overload
def feature(*, ready: Literal["close", "next_open", "next_close"] = "close") -> Callable[[_F], _F]: ...


# --- strategies -------------------------------------------------------------------------------------------------------------
from nautilus_trader.trading.strategy import (
    Strategy as _Base,  # unresolved in the research environment, which has no Nautilus
)

class Strategy(_Base):
    """Base for strategies written in TALYX: the data settings supply `instrument_ids` and `bar_types`."""
    instrument_ids: list[Any]
    bar_types: list[Any]
    failure: Any
    cache: Any
    clock: Any
    log: Any
    portfolio: Any
    order_factory: Any
    strategy_id: Any
    def __init__(self) -> None: ...
    @property
    def instrument_id(self) -> Any: ...
    @property
    def bar_type(self) -> Any: ...
    def last_close(self, instrument_id: Any) -> float | None: ...
    def position_size(self, instrument_id: Any) -> float: ...
    def affordable(self, instrument_id: Any, price: float) -> float: ...
    def equity(self) -> float: ...
    @property
    def max_position(self) -> float: ...
    def open_orders(self) -> list[Any]: ...
    def close(self, instrument_id: Any = None, reason: str = "signal") -> None: ...
    def order_target_weight(self, instrument_id: Any, weight: float, reason: str = "rebalance") -> Any: ...
    def rebalance_to(self, weights: Mapping[Any, float], *, tag: str = "rebalance") -> list[Any]: ...
    def universe(self, at: int | None = None) -> list[Any]: ...
    def is_tradable(self, instrument_id: Any, at: int | None = None) -> bool: ...
    def reports(self, engine: Any, frame: Any) -> dict[str, Any]: ...
    def submit_order(self, order: Any, position_id: Any = None, client_id: Any = None, params: Any = None) -> None: ...
    def cancel_all_orders(self, instrument_id: Any, order_side: Any = ..., client_id: Any = None, params: Any = None) -> None: ...
    def close_all_positions(self, instrument_id: Any, position_side: Any = ..., client_id: Any = None, tags: Any = None, time_in_force: Any = ...,
                            reduce_only: bool = True, params: Any = None) -> None: ...
    def subscribe_bars(self, bar_type: Any, client_id: Any = None, await_partial: bool = False, update_catalog: Any = None, params: Any = None) -> None: ...
    def subscribe_data(self, data_type: Any, instrument_id: Any = None, client_id: Any = None, update_catalog: Any = None, params: Any = None) -> None: ...


# --- a strategy's own series, numbers, log and Market's extra series ---------------------------------------------------------
def plot(name: str, value: float | None, *, pane: Literal["price", "own"] = "price", style: Literal["line", "step"] = "line") -> None: ...
def stat(name: str, value: float | None) -> None: ...
def log(text: str, *, kind: str = "Info", instrument: Any = None) -> None: ...
def can_short(instrument: Any) -> bool: ...
def tradable_quantity(instrument: Any, amount: float) -> Any | None:
    """The most of `amount` an order for this instrument may carry, as its Quantity, or None when that is less than it allows (whole lots for stocks, the size increment otherwise)."""
def risk_size(risk_pct: float, equity: float, stop_distance: float, price: float, max_position: float) -> float: ...
def fit_size(free: float, price: float, margin: float, fee: float, room: float = ...) -> float: ...
def series(name: str, instrument: Any = None) -> pd.Series: ...


# --- the formula's operators for code: the same as the one-line formula ---------------------------------------------------
class _Ops:
    def Ref(self, x: _Table | pd.Series | float, n: int) -> _Table:
        """Ref(x, n): x n days earlier; n must be 1 or more"""
    def Mean(self, x: _Table | pd.Series | float, n: int) -> _Table:
        """Mean(x, n): average over the last n days"""
    def Sum(self, x: _Table | pd.Series | float, n: int) -> _Table:
        """Sum(x, n): sum over the last n days"""
    def Std(self, x: _Table | pd.Series | float, n: int) -> _Table:
        """Std(x, n): standard deviation over the last n days"""
    def Var(self, x: _Table | pd.Series | float, n: int) -> _Table:
        """Var(x, n): variance over the last n days"""
    def Skew(self, x: _Table | pd.Series | float, n: int) -> _Table:
        """Skew(x, n): skew over the last n days"""
    def Kurt(self, x: _Table | pd.Series | float, n: int) -> _Table:
        """Kurt(x, n): kurtosis over the last n days"""
    def Max(self, x: _Table | pd.Series | float, n: int) -> _Table:
        """Max(x, n): highest over the last n days"""
    def Min(self, x: _Table | pd.Series | float, n: int) -> _Table:
        """Min(x, n): lowest over the last n days"""
    def Med(self, x: _Table | pd.Series | float, n: int) -> _Table:
        """Med(x, n): median over the last n days"""
    def Mad(self, x: _Table | pd.Series | float, n: int) -> _Table:
        """Mad(x, n): mean absolute deviation over the last n days"""
    def Slope(self, x: _Table | pd.Series | float, n: int) -> _Table:
        """Slope(x, n): slope of a line through the last n days"""
    def Rsquare(self, x: _Table | pd.Series | float, n: int) -> _Table:
        """Rsquare(x, n): R squared of that line"""
    def Resi(self, x: _Table | pd.Series | float, n: int) -> _Table:
        """Resi(x, n): residual of that line"""
    def Rank(self, x: _Table | pd.Series | float, n: int) -> _Table:
        """Rank(x, n): rank of today among the last n days, 0 to 1"""
    def Count(self, x: _Table | pd.Series | float, n: int) -> _Table:
        """Count(x, n): days with a value in the last n"""
    def EMA(self, x: _Table | pd.Series | float, n: int) -> _Table:
        """EMA(x, n): exponential average with span n"""
    def WMA(self, x: _Table | pd.Series | float, n: int) -> _Table:
        """WMA(x, n): linearly weighted average"""
    def IdxMax(self, x: _Table | pd.Series | float, n: int) -> _Table:
        """IdxMax(x, n): days since the highest in the last n"""
    def IdxMin(self, x: _Table | pd.Series | float, n: int) -> _Table:
        """IdxMin(x, n): days since the lowest in the last n"""
    def Delta(self, x: _Table | pd.Series | float, n: int) -> _Table:
        """Delta(x, n): x minus x n days earlier"""
    def Quantile(self, x: _Table | pd.Series | float, n: int, q: float) -> _Table:
        """Quantile(x, n, q): the q quantile over the last n days"""
    def Corr(self, x: _Table | pd.Series | float, y: _Table | pd.Series | float, n: int) -> _Table:
        """Corr(x, y, n): correlation of x and y over the last n days"""
    def Cov(self, x: _Table | pd.Series | float, y: _Table | pd.Series | float, n: int) -> _Table:
        """Cov(x, y, n): covariance of x and y over the last n days"""
    def CSRank(self, x: _Table | pd.Series | float) -> _Table:
        """CSRank(x): rank across names each day, 0 to 1"""
    def CSZScore(self, x: _Table | pd.Series | float) -> _Table:
        """CSZScore(x): z-score across names each day"""
    def CSDemean(self, x: _Table | pd.Series | float) -> _Table:
        """CSDemean(x): minus the mean across names each day"""
    def IndRank(self, x: _Table | pd.Series | float) -> _Table:
        """IndRank(x): rank inside each industry each day, 0 to 1"""
    def Neutralize(self, x: _Table, industry: pd.DataFrame | pd.Series | None = None, size: _Table | None = None) -> _Table:
        """Neutralize(x, [$industry], [y]): residual after industry dummies and log size each day"""
    def Abs(self, x: _Table | pd.Series | float) -> _Table:
        """Abs(x): absolute value"""
    def Log(self, x: _Table | pd.Series | float) -> _Table:
        """Log(x): natural log"""
    def Sign(self, x: _Table | pd.Series | float) -> _Table:
        """Sign(x): -1, 0 or 1"""
    def Power(self, x: _Table | pd.Series | float, q: float) -> _Table:
        """Power(x, q): x to the power q"""
    def If(self, x: _Table | pd.Series | float, y: _Table | pd.Series | float, z: _Table | pd.Series | float) -> _Table:
        """If(x, y, z): y where x holds, else z"""
    def Greater(self, x: _Table | pd.Series | float, y: _Table | pd.Series | float) -> _Table:
        """Greater(x, y): the larger of x and y"""
    def Less(self, x: _Table | pd.Series | float, y: _Table | pd.Series | float) -> _Table:
        """Less(x, y): the smaller of x and y"""
    def Gt(self, x: _Table | pd.Series | float, y: _Table | pd.Series | float) -> _Table:
        """Gt(x, y): 1 where x > y"""
    def Ge(self, x: _Table | pd.Series | float, y: _Table | pd.Series | float) -> _Table:
        """Ge(x, y): 1 where x ≥ y"""
    def Lt(self, x: _Table | pd.Series | float, y: _Table | pd.Series | float) -> _Table:
        """Lt(x, y): 1 where x < y"""
    def Le(self, x: _Table | pd.Series | float, y: _Table | pd.Series | float) -> _Table:
        """Le(x, y): 1 where x ≤ y"""
    def Eq(self, x: _Table | pd.Series | float, y: _Table | pd.Series | float) -> _Table:
        """Eq(x, y): 1 where x = y"""
    def Ne(self, x: _Table | pd.Series | float, y: _Table | pd.Series | float) -> _Table:
        """Ne(x, y): 1 where x ≠ y"""
    def And(self, x: _Table | pd.Series | float, y: _Table | pd.Series | float) -> _Table:
        """And(x, y): both hold"""
    def Or(self, x: _Table | pd.Series | float, y: _Table | pd.Series | float) -> _Table:
        """Or(x, y): either holds"""
    def Not(self, x: _Table | pd.Series | float) -> _Table:
        """Not(x): does not hold"""
    def ChangeInstrument(self, name: str, x: _Table | pd.Series | float) -> _Table:
        """ChangeInstrument('…', x): x computed on another instrument of this data, such as an index or benchmark"""
    def TResample(self, x: _Table | pd.Series | float, name: str) -> _Table:
        """TResample(x, '…'): x on a slower calendar (1w, 1m), carried forward once each period has closed"""
ops: _Ops


class Run:
    state: str
    settings: dict | None
    number: int | None
    failure: dict | None
    object: str
    kind: str
    id: str
    data_to: str | None
    @property
    def data(self) -> ResearchData: ...
    files: list[str]
    def __getitem__(self, name: str) -> pd.DataFrame: ...
    def __getattr__(self, name: str) -> pd.DataFrame: ...

def run(name: str | None = None, number: int | None = None, *, kind: str | None = None, run_id: str | None = None) -> Run: ...

class Backtest:
    run_id: str
    number: int | None
    partial: bool
    state: str | None
    run: Run
    def __getattr__(self, name: str) -> pd.DataFrame: ...

def backtest(strategy: str, params: dict | None = None, *, data: pd.DataFrame | None = None, signal: str | None = None, every: float = 1.0) -> Backtest: ...

def allocate(settings: dict[str, Any], *, estimator: Any = None) -> Any:
    """An allocation's walk-forward run: the standard flow from `settings`, or `estimator` (a skfolio optimization or a Pipeline ending in one) fitted at every rebalance."""
