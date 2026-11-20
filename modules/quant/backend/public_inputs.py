"""Public inputs shared by the Quant object, run and library actions."""

from __future__ import annotations

from builtins import list as ListType
from typing import Annotated, Any, Literal

from pydantic import Field
from talyx_sdk.declare import Model

Id = Annotated[str, Field(min_length=1)]
Kind = Literal["notebook", "factor", "model", "allocation", "strategy"]
Adjustment = Literal["raw", "split", "split_dividend"]


class Page(Model):
    limit: Annotated[int, Field(ge=1, le=500)] = 25
    """Rows per page."""
    cursor: str | None = None
    """The previous page's nextCursor."""


class ObjectId(Model):
    objectId: Id


class RunId(Model):
    runId: Id


class Index(Model):
    symbol: Id
    """The index, e.g. ^GSPC."""
    at: str | None = None
    """YYYY-MM-DD: that day's members over the whole span; default each day's own members."""


class NotebookSource(Model):
    notebookId: Id
    cells: Annotated[list[Id], Field(min_length=1)]



class Settings(Model):
    name: Annotated[str | None, Field(min_length=1, max_length=120)] = None
    folder: Annotated[str | None, Field(min_length=1, max_length=120)] = None
    """Folder name; null takes the object out of its folder."""
    data: dict[str, Any] | None = Field(
        None,
        description=(
            "Data settings. Instruments, one of: symbols ([\"AAPL\", \"binance:BTC/USDT\"], or {add, remove}),"
            " index ({symbol: \"^GSPC\", at?}: its historical members), list (a research listId),"
            " universe (\"code\": the code picks them; not strategies). timeframe: 1m 5m 15m 30m 1h 4h 1d."
            " start, end: YYYY-MM-DD, both inclusive; no end = latest close. adjustment: raw, split or"
            " split_dividend (strategy prices are fixed)."
        ),
    )
    params: dict[str, Any] | None = Field(
        None,
        description=(
            "Declared params by name (read_object part options lists names, ranges, units); null resets one,"
            " params: null resets all. Standard allocation percentages end in Pct (maxWeightPct: 40 is 40%)."
        ),
    )
    run: dict[str, Any] | None = Field(
        None,
        description=(
            "Run settings (read_object part options): strategy capital, costs {feePct, halfSpreadBps...}, fill,"
            " shorting, benchmark...; factor holding, delay, grouping...; model costBps (a trade), topN. All"
            " kinds: heldOutFrom (YYYY-MM-DD), kept out of runs until validate_held_out."
        ),
    )
    code: Annotated[str | None, Field(min_length=1, max_length=1_000_000)] = None
    """The whole code file, saved as the editor saves it (a formula factor or standard model becomes code); the
    answer's codeErrors is its check. How: platform.ai.guide quant strategy or research."""
    formula: Annotated[str | None, Field(max_length=4000)] = None
    """Formula factors only, e.g. -($close/Ref($close,5)-1)."""
    libraryInputs: dict[str, Id] | None = None
    """Pin library inputs: {<item name>: <versionId>}."""


class NewObject(Settings):
    kind: Kind
    preset: (
        Literal[
            "ranked",
            "rebalance",
            "signal",
            "breakout",
            "crossover",
            "factor:Momentum",
            "factor:Reversal",
            "factor:Volatility",
            "factor:Residual volatility",
            "factor:Beta",
            "factor:Liquidity",
            "factor:Size",
            "model:lgb-alpha158",
            "model:lstm-alpha360",
            "model:ridge-styles",
        ]
        | None
    ) = None
    """Strategy presets: crossover (params fast, slow, shorts), breakout (lookback, stop_atr, trail_atr, risk,
    shorts), ranked or signal (trade a library signal), rebalance (an allocation's weights). At most one of preset,
    copyOf, fromRun, fromVersion, fromNotebook."""
    copyOf: Id | None = None
    """A strategy's objectId: copies its code and settings."""
    fromRun: Id | None = None
    """A factor run starts a model or strategy; a model run a strategy."""
    cell: Annotated[int | None, Field(ge=0)] = None
    """With fromRun: a factor sweep's cell."""
    fromVersion: Id | None = None
    """A library versionId for a new model, strategy, allocation or notebook."""
    fromNotebook: NotebookSource | None = None


class EditObject(ObjectId, Settings):
    expectedVersion: Annotated[int | None, Field(ge=1)] = None
    """The version read_object gave; refused with conflict if the object changed since."""


class ListObjects(Page):
    kind: Kind | None = None
    folder: str | None = None
    query: str | None = None
    """Part of the name."""
    scope: Literal["workspace", "all"] = "workspace"


class ReadObject(ObjectId):
    part: Literal["summary", "options", "code", "dataDefaults", "notebook"] = "summary"
    cursor: str | None = None
    """part code: the previous page's nextCursor."""


class WriteAsCode(ObjectId):
    formula: str | None = None
    """Factor only: the formula to convert instead of the saved one."""


class ResumeRun(RunId):
    override: dict[str, Any] | None = None
    """Model training only: param values for the windows still to train."""
    epoch: Annotated[int | None, Field(ge=0)] = None
    """Neural network only: the epoch of the saved checkpoint to continue from."""


class CellOfRun(RunId):
    cell: Annotated[int | None, Field(ge=0)] = None
    """A sweep cell's number (from the sweep's result or read_run_table cells)."""


class ApplyRun(CellOfRun):
    expectedVersion: Annotated[int | None, Field(ge=1)] = None
    """The object's version read_object gave; refused with conflict if it changed since."""


class ListRuns(Page, ObjectId):
    runKind: Literal["run", "sweep", "walkForward", "heldOut", "combine", "publish"] | None = None
    state: str | None = None
    """A run state, e.g. completed, failed, cancelled."""
    starred: bool | None = None
    batch: str | None = None
    """A batch id: the member runs of one model or allocation batch."""
    scope: Literal["workspace", "all"] = "workspace"


class ReadRun(RunId):
    instrumentId: Id | None = None
    """chartLayer: select a run instrument."""
    around: str | None = None
    """chartLayer: center the price window on this ISO timestamp."""
    includeInstruments: bool = False
    """chartLayer: also return the instrument list and portfolio contributions for a picker."""
    part: str = "summary"
    """summary, or one of the parts the summary lists."""
    compareTo: Id | Annotated[list[Id], Field(min_length=1, max_length=3)] | None = None
    """Another run of the same kind. Strategy chartLayer also accepts up to three comparison run IDs."""
    cursor: str | None = None
    """part code: the previous page's nextCursor."""


class ReadRunTable(Page, RunId):
    table: Literal[
        "trades",
        "orders",
        "rebalances",
        "daily",
        "crossSection",
        "windows",
        "values",
        "periods",
        "cells",
        "log",
    ]
    """One of the tables the run's summary lists."""
    start: str | None = None
    """YYYY-MM-DD, inclusive."""
    end: str | None = None
    """YYYY-MM-DD, inclusive."""
    date: str | None = None
    """YYYY-MM-DD: one day of crossSection, values or rebalances; values without it is the last test day whose
    returns every name has realised."""
    filter: dict[str, Any] | None = None
    """Keys by table: trades side, exit, losing; orders status; rebalances only; cells state; log stream."""
    format: Literal["rows", "file"] = "rows"
    """rows: a page; file: every row as a Parquet file."""


class PlanPublication(RunId):
    name: str | None = None
    """The library item's name; default the run's object name."""


class PublishRun(RunId):
    name: Id
    """The library item's name."""
    autoUpdate: bool = True
    """Add new values each period."""


class ListLibrary(Page):
    query: str | None = None
    """Part of the name."""
    kind: Literal["factor", "model", "allocation", "data"] | None = None
    state: str | None = None
    """An item state, or attention: the items that need a hand."""


class LibraryItem(Model):
    itemId: Id | None = None
    """Give itemId or name."""
    name: Id | None = None
    versionId: Id | None = None
    """The version to show; default the newest producing one."""
    compareTo: Id | None = None
    """Another versionId to compare with."""


class LibraryLog(Page):
    versionId: Id


class LibraryValues(Page):
    name: Id | None = None
    """The library item's name; or give versionId."""
    version: Annotated[int | None, Field(ge=1)] = None
    """With name: the version number; default the newest."""
    versionId: Id | None = None
    symbols: list[Id] | None = None
    """Only these instruments, e.g. ["AAPL", "MSFT"]."""
    field: str | None = None
    """Only this field."""
    start: str | None = None
    """YYYY-MM-DD, inclusive."""
    end: str | None = None
    """YYYY-MM-DD, inclusive."""
    asOf: str | None = None
    """ISO time: values as they were known then; default the end of end."""
    date: str | None = None
    """YYYY-MM-DD: one day's cross section."""
    order: Literal["top", "bottom", "missing"] | None = None
    """Cross section only; default top."""
    format: Literal["rows", "file"] = "rows"
    """rows: a page; file: every row as a Parquet file."""


class ValueRow(Model):
    date: str | None = None
    """YYYY-MM-DD; or give time."""
    time: str | None = None
    """An ISO time with timezone."""
    symbol: Id
    field: Id
    value: float


class PublishValues(Model):
    name: Id
    """The library data item's name."""
    kind: Literal["data"]
    rows: Annotated[list[ValueRow], Field(min_length=1)]


class EditVersion(Model):
    versionId: Id
    producing: bool | None = None
    """false stops production, true resumes it."""
    autoUpdate: bool | None = None
    """Add new values each period automatically."""
    dryRun: bool = False
    """With producing false: show what stopping would affect, change nothing."""


class NewResearchList(Model):
    name: Id
    symbols: list[Id]
    """Market symbols, e.g. AAPL, 600519.SH, binance:BTC/USDT."""


class EditResearchList(Model):
    listId: Id
    name: Id | None = None
    symbols: list[Id] | None = None
    """The whole new member list."""
    add: list[Id] | None = None
    remove: list[Id] | None = None


class ResearchBars(Model):
    symbols: ListType[Id] | None = None
    """Give one of symbols, index or list."""
    index: Index | None = None
    list: Id | None = None
    """A research list's name."""
    timeframe: str
    """1m, 5m, 15m, 30m, 1h, 4h or 1d."""
    start: str
    """YYYY-MM-DD, inclusive."""
    end: str | None = None
    """YYYY-MM-DD, inclusive; default the latest close."""
    adjustment: Adjustment = "split"


class Axis(Model):
    name: Id
    """A declared param, or a run setting the kind sweeps (see start_sweep)."""
    values: Annotated[list[Any], Field(min_length=1, max_length=200)]


class Sweep(ObjectId):
    axes: Annotated[list[Axis], Field(min_length=1, max_length=8)]
    metric: str | None = None
    """Strategy only: the number that ranks the cells; default sharpe."""
    of: Id | None = None
    """Strategy only: a completed backtest whose code and values the cells start from."""


class CheckModel(ObjectId):
    vary: Annotated[list[Axis] | None, Field(max_length=4)] = None
    """The axes of a batch to check, as start_sweep takes them."""


Scalar = str | bool | int | float | None


class TableFilter(Model):
    column: str
    equals: Scalar


class TablePredicate(Model):
    column: str
    operator: Literal["eq", "gte", "lte", "in"]
    value: Scalar | Annotated[list[Scalar], Field(max_length=1000)]


class TableSort(Model):
    column: str
    descending: bool | None = None


class TableQuery(Model):
    name: str
    observation: str | None = None
    filter: TableFilter | None = None
    offset: Annotated[int, Field(ge=0)] | None = None
    limit: Annotated[int, Field(ge=1, le=10_000)] | None = None
    columns: list[str] | None = None
    filters: Annotated[list[TablePredicate], Field(max_length=12)] | None = None
    sort: TableSort | None = None

    def args(self):
        return self.model_dump(exclude_unset=True)


class LibraryVersion(Model):
    versionId: Id


class LibraryReport(LibraryVersion):
    part: Literal["report"] = "report"


class LibrarySource(LibraryVersion):
    part: Literal["source"]
    name: Id | None = None
    """Omit to list the source files."""


class LibraryFile(LibraryVersion):
    part: Literal["file"]
    name: Id


class LibraryTable(LibraryVersion):
    part: Literal["table"]
    query: TableQuery


class ModelFigures(RunId):
    keys: list[Id] | None = None
    compareTo: Id | None = None


class ModelOverlay(Model):
    runIds: Annotated[list[Id], Field(min_length=1, max_length=4)]


class KernelControl(ObjectId):
    op: Literal["status", "stop", "interrupt", "restart"]


class PackageInstall(Model):
    op: Literal["install"]
    environment: Literal["research"] = "research"


class CancelInstall(Model):
    op: Literal["cancel"]
    environment: Literal["research", "deep"] = "research"


class ParameterSource(ObjectId):
    code: str


class EstimateWalk(Sweep):
    kind: Literal["walk"]
    pickOn: Annotated[int, Field(ge=1, le=120)] = 12
    trade: Annotated[int, Field(ge=1, le=60)] = 3
    pickBy: Literal["sharpe", "cagr", "calmar"] = "sharpe"


class ExtractPreview(NotebookSource):
    kind: Literal["strategy", "factor", "allocation", "model"]


class SweepSelection(RunId):
    cell: Annotated[int, Field(ge=0)]


class SavedCell(Model):
    id: Id
    type: Literal["code", "markdown", "raw"]
    source: str
    metadata: dict[str, Any]
    executionCount: int | None = None
    outputs: list[dict[str, Any]]
    attachments: dict[str, dict[str, Any]] | None = None


class NotebookModel(Model):
    cells: list[SavedCell]
    metadata: dict[str, Any]


class SaveNotebook(ObjectId):
    expectedVersion: int | str
    """Document version from read_object part notebook."""
    notebook: NotebookModel
