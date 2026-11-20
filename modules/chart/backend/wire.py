"""Shapes crossing Chart's private seam: route bodies, workspace objects and stream items.

Private routes are ``POST /m/chart/<path>`` with a JSON body (``ROUTES`` below); errors use
the platform's one error format (``conflict`` carries ``details.revision``). The two streams are
WebSockets on private routes (``routes.py``):

- ``chart.chart`` (params ``{"chartId"}``): one chart's data flow, ``ChartItem`` below. The
  first item is always ``document``; then bars, status, indicators and comparisons in the
  order they happen. Every ``bars replace`` is followed by an ``indicator replace`` for each
  indicator instance (or ``indicator_error``). A replace too large for one bridge frame
  arrives as a replace of its first rows followed by appends of the rest.
- ``chart.workspace`` (params ``{}``): the caller's workspace, ``WorkspaceItem`` below. The
  first item is ``snapshot``; changes follow, whoever made them (UI, AI, other modules).

Units: times are UTC epoch milliseconds (integers), prices and values JSON numbers, ``null``
where absent (Pine ``na``). On brick chart types ``time`` is the brick's display key: strictly
increasing milliseconds, ``sourceTime`` is the bar that made it.
"""
from __future__ import annotations

from typing import Annotated, Literal, NotRequired, TypedDict

from pydantic import Field, JsonValue

from vocabulary.python.types import FileRef

from .document import Commands, Id, Json, Model, Name, Scalar
from .models import ResearchWindow

Ms = Annotated[int, Field(ge=0)]
Text = Annotated[str, Field(min_length=1, max_length=4000)]
MAX_WINDOW = 30_000
DEFAULT_WINDOW = 10_000


# ── Charts ──


class ChartState(Model):
    """A chart as committed. ``drawingGroup`` is the key of ``document.drawings`` shown now."""
    chartId: str
    revision: int
    document: dict
    drawingGroup: str
    canUndo: bool
    canRedo: bool


class ChartOpen(Model):
    chartId: Id


class LayerRead(ChartOpen):
    id: str


class LayerHistory(Model):
    instrumentId: str
    timeframe: str
    adjustment: Literal["raw", "split", "split_dividend"]
    window: ResearchWindow


class ChartAdopt(Model):
    """The charts a panel names (a copied workspace's panels name the original's charts)."""
    chartIds: list[Id]


class ChartAdoption(Model):
    charts: dict[str, Id]
    """Each named chart → the chart of this workspace that stands for it (itself, or its copy)."""


class ChartCreate(Model):
    """A new chart on ``instrumentId`` (Market's default series for it on ``timeframe``, read from
    the import set ``importId`` when given: the import is part of the series), or a copy of
    ``copyOf`` (another chart of this workspace) when given. Without an instrument: OKX BTC/USDT
    spot."""
    instrumentId: str | None = None
    timeframe: str | None = None
    importId: str | None = None
    copyOf: Id | None = None


class ChartCommit(Model):
    chartId: Id
    baseRevision: int
    commands: Commands


class ChartTarget(Model):
    chartId: Id


class SavedChart(Model):
    chartId: Id
    revision: int


class ChartSave(Model):
    """The workspace was saved with these charts at these revisions. This workspace's other
    charts are not in the saved layout and are deleted."""
    charts: Annotated[list[SavedChart], Field(max_length=8)]


class Backfill(Model):
    """Extend the window back to ``to`` (a time) or by ``bars`` more bars; exactly one. The
    result arrives on the chart stream as ``bars replace``."""
    chartId: Id
    to: Ms | None = None
    bars: Annotated[int, Field(ge=1, le=MAX_WINDOW)] | None = None


class VisibleRange(Model):
    """The chart's visible range, for the indicators that read it (Pine's ``chart.left_visible_bar_time``
    and ``chart.right_visible_bar_time``): the times of the first and last visible bars, or ``bars``,
    the newest bars when the right edge is on the newest bar (a new bar then moves the range without
    computing again). Exactly one of the two."""
    chartId: Id
    left: Ms | None = None
    right: Ms | None = None
    bars: Annotated[int, Field(ge=1, le=MAX_WINDOW)] | None = None


class BackfillResult(Model):
    """``reachedStart``: Market has nothing older. ``limitReached``: the window is at 30,000 bars;
    ``to`` was not reached when either is true."""
    loaded: int
    reachedStart: bool
    limitReached: bool


class ReplayStart(Model):
    """Replay from the bar at or before ``at``; the window before it is history."""
    chartId: Id
    at: Ms


class ReplayStep(Model):
    chartId: Id
    bars: Annotated[int, Field(ge=1, le=1000)] = 1


class ExportChart(Model):
    chartId: Id
    format: Literal["csv", "json", "parquet"]


class ExportResult(Model):
    """The file to save: bars (time, closeTime, OHLCV) and every indicator output as columns."""
    filename: str
    mediaType: str
    data: str  # base64
    rows: int


# ── Indicators ──


class IndicatorInput(Model):
    """One Pine input. ``type``: int, float, bool, string, source, color, timeframe, symbol, session."""
    id: str
    title: str
    type: str
    default: JsonValue
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: list[JsonValue] | None = None
    group: str | None = None
    tooltip: str | None = None


class CatalogEntry(Model):
    """A built-in (``source: builtin``, ``id`` its name) or user indicator (``id`` the Developer
    project id). ``title`` is the library name (TradingView's for built-ins, "Relative Strength
    Index"), ``shortTitle`` its abbreviation ("RSI"; search matches both), ``category`` a
    TradingView-style group in title case (Trend, Oscillators, Volume, Volatility, Moving
    Averages, ...; "Other" when a user script sets no ``CATEGORY``). ``alertConditions``: titles
    of the script's ``alertcondition`` calls (an alert's ``triggered`` condition). ``digest``
    names the exact code; ``error`` says why a user project cannot load, ``line`` where in the
    script when known. ``visibleRange``: the script reads the chart's visible range, so it runs on
    charts only (not in alerts, screens or reads without a chart)."""
    source: Literal["builtin", "user"]
    id: str
    title: str
    shortTitle: str
    description: str
    category: str
    overlay: bool
    digest: str
    inputs: list[IndicatorInput]
    alertConditions: list[str] = []
    visibleRange: bool = False
    error: str | None = None
    line: int | None = None


class Catalog(Model):
    """``folder``: the absolute path of the user's indicators folder."""
    folder: str
    entries: list[CatalogEntry]


# ── Alerts ──


class PriceOperand(Model):
    kind: Literal["price"]
    field: Literal["close", "open", "high", "low", "hl2", "hlc3", "ohlc4", "volume"] = "close"


class IndicatorOperand(Model):
    """One output of a built-in or user indicator with these params, on the alert's series."""
    kind: Literal["indicator"]
    definition: Annotated[dict[str, str], Field(description="IndicatorRef: {source, id}")]
    params: dict[str, Scalar] = {}
    output: str


class DrawingOperand(Model):
    """A drawing of a chart in this workspace; its geometry is copied into the alert and kept in
    step when the drawing changes."""
    kind: Literal["drawing"]
    chartId: Id
    drawingId: Id


class ValueOperand(Model):
    kind: Literal["value"]
    value: float


class AlertConditionOperand(Model):
    """A Pine ``alertcondition`` of an indicator; used with ``op: triggered``."""
    kind: Literal["alertcondition"]
    definition: dict[str, str]
    params: dict[str, Scalar] = {}
    condition: str


Operand = Annotated[PriceOperand | IndicatorOperand | DrawingOperand | ValueOperand | AlertConditionOperand,
                    Field(discriminator="kind")]
ConditionOp = Literal[
    "crossing", "crossing_up", "crossing_down", "greater", "less",
    "entering_channel", "exiting_channel", "inside_channel", "outside_channel",
    "moving_up", "moving_down", "moving_up_percent", "moving_down_percent", "triggered",
]


class Condition(Model):
    """``left op right``. Channel ops take a channel drawing as ``right``, or the two bounds as
    ``right`` and ``right2``. Moving ops compare ``left`` with its value ``bars`` closed bars ago
    against ``amount``. ``triggered`` takes an ``alertcondition`` as ``left`` and nothing else."""
    left: Operand
    op: ConditionOp
    right: Operand | None = None
    right2: Operand | None = None
    amount: float | None = None
    bars: Annotated[int, Field(ge=1, le=5000)] | None = None


class SeriesTarget(Model):
    """One series (Market Instrument and SeriesId); ``chartId`` is where it was made from."""
    kind: Literal["series"]
    instrument: Json
    series: Json
    chartId: Id | None = None


class WatchlistTarget(Model):
    """Every member of a watchlist on ``timeframe``, each on Market's default series for it."""
    kind: Literal["watchlist"]
    watchlistId: Id
    timeframe: str


Frequency = Literal["once", "once_per_bar", "once_per_bar_close", "once_per_minute"]


class Channels(Model):
    """Delivered by Chart through the platform's notifications (desktop toast, the platform's
    sound, mobile), with or without a Chart panel open."""
    toast: bool = True
    sound: bool = True
    mobile: bool = False


class AlertDefaults(Model):
    """What a new alert's notification starts with: Chart's settings alertPopup and alertSound (Settings → Chart)."""
    toast: bool = True
    sound: bool = True


class AlertSpec(Model):
    target: Annotated[SeriesTarget | WatchlistTarget, Field(discriminator="kind")]
    conditions: Annotated[list[Condition], Field(min_length=1, max_length=5)]
    frequency: Frequency = "once"
    expiresAt: Ms | None = None
    message: Annotated[str, Field(max_length=2000)] = ""
    name: Name | None = None
    channels: Channels = Channels()


class Reason(Model):
    """Why an alert is not watching, or what its source says; ``message`` is for people."""
    code: str
    message: str


class Alert(AlertSpec):
    """``state``: active (watching), paused (by the user or ``reason``), triggered (a ``once``
    alert that fired), expired. ``source`` is Market's state for the series while active
    (``recovering`` with its reason; ``failed`` pauses the alert). ``outdated``: an indicator it
    uses has changed since; the alert keeps the version it was made with."""
    id: str
    revision: int
    state: Literal["active", "paused", "triggered", "expired"]
    reason: Reason | None = None
    source: Reason | None = None
    outdated: bool = False
    createdAt: Ms
    lastFiredAt: Ms | None = None
    fireCount: int = 0


class AlertCreate(Model):
    spec: AlertSpec


class AlertUpdate(Model):
    alertId: Id
    revision: int
    spec: AlertSpec


class AlertState(Model):
    """``active`` resumes or retries; ``paused`` pauses."""
    alertId: Id
    revision: int
    state: Literal["active", "paused"]


class AlertTargetId(Model):
    alertId: Id


class Fired(Model):
    """One trigger. ``symbol`` is the member's on watchlist alerts. ``delivered``: what the
    platform's notifications reported per channel."""
    id: str
    alertId: str
    at: Ms
    barTime: Ms
    instrumentId: str
    symbol: str
    message: str
    value: float | None = None
    delivered: dict[str, Literal["sent", "failed"]] = {}


class AlertLog(Model):
    alertId: Id | None = None
    before: Ms | None = None
    limit: Annotated[int, Field(ge=1, le=500)] = 100


class AlertLogPage(Model):
    """Newest first; ``next`` continues before it (90 days are kept)."""
    events: list[Fired]
    next: Ms | None


# ── Watchlists ──


class Member(Model):
    instrument: Json


class Section(Model):
    id: Id
    name: Annotated[str, Field(max_length=64)]
    members: list[Member]


class Watchlist(Model):
    """Members in order, grouped in sections (the first one may have an empty name). At most
    200 members in all."""
    id: str
    name: Name
    revision: int
    sections: list[Section]


class WatchlistCreate(Model):
    name: Name
    sections: list[Section] = []


class WatchlistUpdate(Model):
    watchlistId: Id
    revision: int
    name: Name | None = None
    sections: list[Section] | None = None


class WatchlistId(Model):
    watchlistId: Id


# ── Screens ──


class InstrumentsScope(Model):
    kind: Literal["instruments"]
    instruments: Annotated[list[Json], Field(min_length=1, max_length=200)]


class WatchlistScope(Model):
    kind: Literal["watchlist"]
    watchlistId: Id


class ScreenSpec(Model):
    """Members whose latest closed bar on ``timeframe`` meets every condition (price, indicator
    and value operands, not drawings)."""
    name: Name
    scope: Annotated[InstrumentsScope | WatchlistScope, Field(discriminator="kind")]
    timeframe: str
    conditions: Annotated[list[Condition], Field(min_length=1, max_length=5)]


class ScreenRow(Model):
    instrument: Json
    matched: bool
    values: dict[str, float | None]
    barTime: Ms | None
    error: str | None = None


class ScreenRun(Model):
    """``state`` running (``done`` of ``total``), completed, cancelled or failed (``error``)."""
    runId: str
    state: Literal["running", "completed", "cancelled", "failed"]
    startedAt: Ms
    finishedAt: Ms | None
    done: int
    total: int
    rows: list[ScreenRow]
    error: str | None = None


class Screen(ScreenSpec):
    id: str
    revision: int
    lastRun: ScreenRun | None = None


class ScreenSave(Model):
    """A new screen without ``screenId``; otherwise a change of that one at ``revision``."""
    screenId: Id | None = None
    revision: int | None = None
    spec: ScreenSpec


class ScreenId(Model):
    screenId: Id


# ── Templates (global, every workspace) ──


class Template(Model):
    """``indicators``: {indicators, panes}; ``chart``: a display; ``drawing``: {tool, style}."""
    id: str
    kind: Literal["indicators", "chart", "drawing"]
    name: Name
    content: Json


class TemplateSave(Model):
    templateId: Id | None = None
    kind: Literal["indicators", "chart", "drawing"]
    name: Name
    content: Json


class TemplateId(Model):
    templateId: Id


class Empty(Model):
    pass


# path, request body, response
ROUTES = {
    "/charts/open": (ChartOpen, ChartState),
    "/layers/stored": (LayerRead, dict),
    "/layers/file": (FileRef, dict),
    "/layers/history": (LayerHistory, dict),
    "/charts/adopt": (ChartAdopt, ChartAdoption),
    "/charts/create": (ChartCreate, ChartState),
    "/charts/commit": (ChartCommit, ChartState),
    "/charts/undo": (ChartTarget, ChartState),
    "/charts/redo": (ChartTarget, ChartState),
    "/charts/save": (ChartSave, Empty),
    "/charts/backfill": (Backfill, BackfillResult),
    "/charts/view": (VisibleRange, Empty),
    "/charts/export": (ExportChart, ExportResult),
    "/replay/start": (ReplayStart, Empty),
    "/replay/step": (ReplayStep, Empty),
    "/replay/stop": (ChartTarget, Empty),
    "/indicators/catalog": (Empty, Catalog),
    "/alerts/create": (AlertCreate, Alert),
    "/alerts/update": (AlertUpdate, Alert),
    "/alerts/state": (AlertState, Alert),
    "/alerts/delete": (AlertTargetId, Empty),
    "/alerts/log": (AlertLog, AlertLogPage),
    "/watchlists/create": (WatchlistCreate, Watchlist),
    "/watchlists/update": (WatchlistUpdate, Watchlist),
    "/watchlists/delete": (WatchlistId, Empty),
    "/screens/save": (ScreenSave, Screen),
    "/screens/run": (ScreenId, Screen),
    "/screens/cancel": (ScreenId, Screen),
    "/screens/delete": (ScreenId, Empty),
    "/templates/save": (TemplateSave, Template),
    "/templates/delete": (TemplateId, Empty),
}


# ── Chart stream items (``chart.chart``) ──


class DocumentItem(TypedDict):
    """The chart as committed: on subscribe and after every change (UI, AI, undo, redo)."""
    type: Literal["document"]
    revision: int
    document: dict          # ChartDocument
    drawingGroup: str
    canUndo: bool
    canRedo: bool


class PriceFormat(TypedDict):
    """``precision`` decimals and ``minMove``: Market's price increment, or the data's decimals
    when Market has none or the prices are adjusted."""
    precision: int
    minMove: float


class BarsItem(TypedDict):
    """Closed bars as columns. ``replace``: the whole window (start, the fill to 10,000 bars when
    Market had fewer locally, revision, recovery, switch, backfill, replay start) — drop
    everything and take these. ``append``: newly closed bars after the last one. ``splice``
    (Heikin Ashi and brick chart types): drop bars from index ``start`` on, then append these.
    ``volume`` items may be null. ``sourceTime`` only on projected types. A replace's
    ``priceFormat`` may come from the data until Market's terms arrive (``price_format``)."""
    type: Literal["bars"]
    op: Literal["replace", "append", "splice"]
    replay: bool
    time: list[int]
    closeTime: list[int]
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    volume: list[float | None]
    sourceTime: NotRequired[list[int]]
    start: NotRequired[int]                 # splice
    priceFormat: NotRequired[PriceFormat]   # replace
    hasMoreBefore: NotRequired[bool]        # replace: Market has older bars
    windowRows: NotRequired[int]            # first frame of a window delivered in several pieces
    limitReached: NotRequired[bool]         # replace: the window holds 30,000 bars
    boxSize: NotRequired[float | None]      # replace on projected types: their box, if they have one


class UpdateItem(TypedDict):
    """The forming bar (latest wins), not on brick chart types. Its ``closeTime`` drives the
    countdown."""
    type: Literal["update"]
    replay: bool
    bar: dict   # {time, closeTime, open, high, low, close, volume}


class StatusItem(TypedDict):
    """Market's state of the main series as Market says it: connecting (before the first live),
    live, delayed, history_only, market_closed, recovering (after a loss), failed; ``problem`` is Market's (code, message, sourceId, status,
    retryAt) or null. ``failed`` stays until the user retries (re-open the stream)."""
    type: Literal["status"]
    state: str
    problem: dict | None
    since: int


class IndicatorItem(TypedDict):
    """One indicator instance's outputs, rows aligned one to one with the chart's bars.

    ``replace``: every row of the window; ``append``: rows of newly closed bars; ``update``: the
    forming row (latest wins); ``splice`` as for bars. ``meta`` is PyneCore's plot-family meta
    records (``pynecore.core.viz.serialize_meta``: id, kind plot/shape/char/arrow/candle/bar/
    bgcolor/barcolor/hline/fill, title, style, color, linewidth, ...; ``format`` is the record's
    own or else the script's ``indicator(format=...)``) — on replace, and again whenever a record
    changed. ``values``: per plot id, one value per row. ``colors``: per
    dynamic channel id (a meta with ``dynamic``), one color per row — a hex string, a list for
    multi-color kinds (shape: [color, textcolor]; arrow: [up, down]; candle: [color, wick,
    border]; fill: [top value, bottom value, top color, bottom color] or a color) — or null.
    ``drawings``: PyneCore's live drawing snapshot (``drawings_snapshot``: lines, labels, boxes,
    tables, polylines, linefills) when it changed; x with ``xloc`` ``bar_index`` counts rows of
    this window (0 = first bar; past the last row is the future), ``bar_time`` is a time.
    ``behindChart`` (on replace): the script's ``indicator(behind_chart=...)`` / ``strategy(...)``,
    true unless it says otherwise (Pine v6): its main-pane plots, bgcolor and drawings go behind
    the bars. ``noScale`` (on replace): the script's ``indicator(scale=scale.none)``: its
    main-pane plots are on a scale of their own that is not shown, at the pane's bottom fifth
    (TradingView's Volume)."""
    type: Literal["indicator"]
    instanceId: str
    op: Literal["replace", "append", "update", "splice"]
    replay: bool
    start: NotRequired[int]
    meta: NotRequired[list[dict]]
    behindChart: NotRequired[bool]
    noScale: NotRequired[bool]
    time: list[int]
    values: dict[str, list[float | None]]
    colors: dict[str, list[JsonValue]]
    drawings: NotRequired[dict]


class IndicatorErrorItem(TypedDict):
    """This instance cannot compute; the others go on. ``code``: warmup (not enough bars),
    script_error (with ``line``), missing_data, worker_restarted (it will come back with a
    replace), not_found (the definition is gone)."""
    type: Literal["indicator_error"]
    instanceId: str
    code: str
    message: str
    line: NotRequired[int]


class ComparisonItem(TypedDict):
    """A comparison symbol's close at each main bar time (the last close at or before it),
    rows aligned with the chart's bars; ``replace`` / ``append`` / ``update`` as for bars.
    ``priceFormat`` (on replace) is the comparison symbol's own, by the same rule as bars."""
    type: Literal["comparison"]
    comparisonId: str
    op: Literal["replace", "append", "update"]
    time: list[int]
    value: list[float | None]
    priceFormat: NotRequired[PriceFormat]


class ReplayItem(TypedDict):
    """Replay started (``active``), moved, or stopped (live bars and indicators follow as a
    replace). ``at`` is the last replayed bar's time; ``end`` when no bar is left to replay."""
    type: Literal["replay"]
    active: bool
    at: int | None
    end: bool


class PriceFormatItem(TypedDict):
    """The main series' price format changed (Market's increment arrived after the bars)."""
    type: Literal["price_format"]
    priceFormat: PriceFormat


ChartItem = DocumentItem | BarsItem | UpdateItem | StatusItem | PriceFormatItem | IndicatorItem \
    | IndicatorErrorItem | ComparisonItem | ReplayItem


# ── Workspace stream items (``chart.workspace``) ──


class PineHealth(TypedDict):
    """The indicator processes: ``ok``, ``starting``, ``restarting`` (a process stopped; its
    indicators start again in a new one) or ``down`` (a process could not start; ``reason`` says
    why). The public action ``restart_indicators`` starts them all anew."""
    state: Literal["ok", "starting", "restarting", "down"]
    reason: str | None


class AlertStoreHealth(TypedDict):
    """``failing``: the last write to Chart's database (alerts, triggers, charts) raised,
    ``reason`` is the error; ``ok`` again after the next write succeeds."""
    state: Literal["ok", "failing"]
    reason: str | None


class Health(TypedDict):
    pine: PineHealth
    alertStore: AlertStoreHealth


class SnapshotItem(TypedDict):
    """First item: this workspace's alerts, watchlists, screens, the global templates, the
    global alert defaults and Chart's health."""
    type: Literal["snapshot"]
    alerts: list[dict]
    watchlists: list[dict]
    screens: list[dict]
    templates: list[dict]
    alertDefaults: dict  # AlertDefaults
    health: Health


class ChartChangedItem(TypedDict):
    """A chart of this workspace changed (by anyone); ``saved`` is false while it differs from
    the revision the workspace was last saved with."""
    type: Literal["chart"]
    chartId: str
    revision: int
    saved: bool


class ObjectItem(TypedDict):
    """An object was created or changed (the whole object), or removed (``id`` only)."""
    type: Literal["alert", "watchlist", "screen", "template"]
    value: dict


class RemovedItem(TypedDict):
    type: Literal["alert_removed", "watchlist_removed", "screen_removed", "template_removed"]
    id: str


class AlertDefaultsItem(TypedDict):
    """The alert defaults changed (sent to every workspace)."""
    type: Literal["alert_defaults"]
    value: dict  # AlertDefaults


class FiredItem(TypedDict):
    type: Literal["fired"]
    event: dict   # Fired


class HealthItem(Health):
    """Chart's health changed (sent to every workspace)."""
    type: Literal["health"]


class CatalogChangedItem(TypedDict):
    """A user indicator was saved or deleted by an action (sent to every workspace): read the catalog again."""
    type: Literal["catalog"]


WorkspaceItem = SnapshotItem | ChartChangedItem | ObjectItem | RemovedItem | AlertDefaultsItem | FiredItem \
    | HealthItem | CatalogChangedItem
__all__ = ["ROUTES", "ChartItem", "WorkspaceItem", "Json", "Id", "Name"]
