"""Chart's public types (inputs of its actions). The contract is generated from these; edit here, then run the generator."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, RootModel
from talyx_sdk.declare import Model

from vocabulary.python.types import FileRef


class AlertFilter(Model):
    alertId: str | None = None
    instrumentId: str | None = None


class AlertOpenTarget(Model):
    module: Literal["chart"]
    panelType: Literal["main"]
    objectId: str


class AlertFired(Model):
    alertId: str
    instrumentId: str
    triggeredAt: str
    barOpen: str
    condition: str | None = Field(description="A readable description of the condition, absent on older log entries")
    openTarget: AlertOpenTarget


class ReadChartInput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    view: Literal["list", "one", "workspace", "templates", "layer"]
    layerId: Annotated[str | None, Field(min_length=1)] = None
    """view=layer: the saved research snapshot to read from chartId."""
    templateId: str | None = None
    chartId: Annotated[str | None, Field(min_length=1)] = None
    cursor: str | None = None
    """
    nextCursor of the previous page
    """
    limit: Annotated[int | None, Field(ge=1, le=200)] = None


class IndicatorRequest(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    name: Annotated[str, Field(min_length=1)]
    params: dict[str, Any] | None = None
    id: Annotated[str | None, Field(min_length=1)] = None
    source: Literal["builtin", "user"] | None = None


class ColumnName(RootModel[str]):
    root: Annotated[str, Field(min_length=1)]


class ReadDataInput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    chartId: Annotated[str | None, Field(min_length=1)] = None
    symbol: Annotated[str | None, Field(min_length=1)] = None
    timeframe: Annotated[str | None, Field(min_length=2)] = None
    """
    1m 5m 15m 1h 4h 1d 1w 1mo ...
    """
    indicators: Annotated[list[IndicatorRequest] | None, Field(max_length=10)] = None
    columns: Annotated[list[ColumnName] | None, Field(max_length=64)] = None
    limit: Annotated[int | None, Field(ge=1, le=500)] = None
    cursor: str | None = None
    """
    nextCursor of the previous page
    """


class DrawingPoint(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    time: Annotated[str, Field(min_length=1)]
    price: float


class ResearchWindow(Model):
    start: str = Field(alias="from", description="First bar open in the snapshot, ISO-8601 UTC.")
    to: str = Field(description="Inclusive end of the snapshot, ISO-8601 UTC.")


class ResearchPane(Model):
    key: str
    title: str
    position: Literal["above", "below"]


class ResearchPoint(Model):
    time: str = Field(description="ISO-8601 UTC. A point belongs to the last bar opened at or before this time.")
    value: float | None


class ResearchSeries(Model):
    key: str
    name: str
    pane: str = Field(description="price or a declared pane key.")
    style: Literal["line", "step", "area"]
    role: Literal["main", "benchmark", "compare", "data"]
    points: list[ResearchPoint]


class ResearchMarker(Model):
    time: str = Field(description="Actual fill time, ISO-8601 UTC.")
    side: Literal["buy", "sell"]
    price: float
    quantity: float


class ResearchLayer(Model):
    id: str = Field(min_length=1, description="Stable caller-supplied snapshot id.")
    label: str
    instrumentId: str
    timeframe: str
    adjustment: Literal["raw", "split", "split_dividend"]
    window: ResearchWindow
    panes: list[ResearchPane]
    series: list[ResearchSeries]
    markers: list[ResearchMarker]


class ChartChange(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["selection", "chart_type", "indicator", "drawing", "comparison", "layer"]
    layer: ResearchLayer | None = None
    id: Annotated[str | None, Field(min_length=1)] = None
    symbol: Annotated[str | None, Field(min_length=1)] = None
    timeframe: Annotated[str | None, Field(min_length=2)] = None
    """
    1m 5m 15m 1h 4h 1d 1w 1mo ...
    """
    priceBasis: Literal["last", "mark", "index"] | None = None
    adjustment: Literal["raw", "split", "split_dividend"] | None = None
    chartType: Literal["candles", "hollow_candles", "bars", "line", "line_markers", "step_line", "area", "hlc_area", "baseline", "columns", "high_low", "heikin_ashi", "renko", "kagi", "point_figure", "line_break", "range"] | None = (
        None
    )
    name: Annotated[str | None, Field(min_length=1)] = None
    params: dict[str, Any] | None = None
    paneId: Annotated[str | None, Field(min_length=1)] = None
    label: Annotated[str | None, Field(min_length=1)] = None
    visible: bool | None = None
    tool: Annotated[str | None, Field(min_length=1)] = None
    points: list[DrawingPoint] | None = None
    text: Annotated[str | None, Field(min_length=1)] = None
    style: dict[str, Any] | None = None
    settings: dict[str, Any] | None = None
    locked: bool | None = None
    scale: Literal["same_percent", "new_left", "new_right"] | None = None
    color: Annotated[str | None, Field(min_length=1)] = None


class ChartEdit(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    op: Literal["edit"]
    chartId: Annotated[str, Field(min_length=1)]
    baseRevision: int
    changes: Annotated[list[ChartChange], Field(max_length=100, min_length=1)] | None = None
    commands: Annotated[list[dict[str, Any]], Field(min_length=1, max_length=500)] | None = None


class IndicatorId(RootModel[str]):
    root: Annotated[str, Field(min_length=1)]


class DrawingId(RootModel[str]):
    root: Annotated[str, Field(min_length=1)]


class ComparisonId(RootModel[str]):
    root: Annotated[str, Field(min_length=1)]


class ChartRemove(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    op: Literal["remove"]
    chartId: Annotated[str, Field(min_length=1)]
    baseRevision: int
    commands: Annotated[list[dict[str, Any]], Field(min_length=1, max_length=500)] | None = None
    indicators: Annotated[list[IndicatorId] | None, Field(max_length=500)] = None
    drawings: Annotated[list[DrawingId] | None, Field(max_length=500)] = None
    comparisons: Annotated[list[ComparisonId] | None, Field(max_length=500)] = None
    layers: list[str] | None = Field(default=None, description="Research layer ids to remove.")


class CreateIndicator(Model):
    name: str
    source: Literal["builtin", "user"] = "builtin"
    params: dict[str, Any] = {}


class ChartCreate(Model):
    op: Literal["create"]
    symbol: str | None = Field(default=None, min_length=1)
    timeframe: str | None = Field(default=None, min_length=1)
    importId: str | None = Field(default=None, min_length=1)
    copyOf: str | None = Field(default=None, min_length=1)
    indicators: list[CreateIndicator] = Field(default_factory=list, max_length=100)
    layers: list[ResearchLayer | FileRef] = Field(default_factory=list)


class ChartChangeResult(Model):
    layerIds: list[str] | None = None
    """For create with research layers, the stable layer IDs on the committed chart."""
    chartId: str
    revision: int
    added: dict[str, list[str]] | None = None
    openTarget: dict[str, str] | None = None


class WriteChartInput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    operation: ChartCreate | ChartEdit | ChartRemove


class ExportedChart(Model):
    filename: str
    mediaType: str
    rows: int
    file: FileRef


class ReplayBegin(Model):
    op: Literal["start"]
    chartId: str
    at: str = Field(description="ISO-8601 date or time in the chart's loaded window")


class ReplayAdvance(Model):
    op: Literal["step"]
    chartId: str
    bars: int = Field(default=1, ge=1, le=1000)


class ReplayEnd(Model):
    op: Literal["stop"]
    chartId: str


class ReplayInput(Model):
    operation: ReplayBegin | ReplayAdvance | ReplayEnd


class BackfillChart(Model):
    chartId: str
    to: str | None = Field(default=None, description="ISO-8601 date or time, instead of bars")
    bars: int | None = Field(default=None, ge=1, le=30000)


class TemplateSave(Model):
    op: Literal["save"]
    chartId: str
    templateId: str | None = None
    name: str = Field(min_length=1, max_length=64)
    kind: Literal["indicators", "chart", "drawing"]
    drawingId: str | None = None


class TemplateApply(Model):
    op: Literal["apply"]
    chartId: str
    baseRevision: int
    templateId: str
    drawingId: str | None = None


class TemplateDelete(Model):
    op: Literal["delete"]
    templateId: str


class WriteTemplate(Model):
    operation: TemplateSave | TemplateApply | TemplateDelete


class ReadIndicatorInput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    view: Literal["list", "one"]
    name: Annotated[str | None, Field(min_length=1)] = None
    source: Literal["builtin", "user"] | None = None
    query: str | None = None
    category: Annotated[str | None, Field(min_length=1)] = None
    cursor: str | None = None
    """
    nextCursor of the previous page
    """
    limit: Annotated[int | None, Field(ge=1, le=300)] = None


class IndicatorSave(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    op: Literal["create", "update"]
    name: Annotated[str, Field(pattern=r"^[^/\\:]+(/[^/\\:]+)?\.py$")]
    """
    The file in the user's indicators folder: chan.py (an indicator of one file), or chan/main.py and chan/helpers.py
    (an indicator of a folder, named chan).
    """
    content: Annotated[str, Field(min_length=1, max_length=1_000_000)]
    """
    The file's whole text.
    """


class IndicatorDelete(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    op: Literal["delete"]
    name: Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[^/\\:]+$")]
    """
    A user indicator's name as read_indicator lists it: chan.py, or chan for a folder (deleted whole).
    """


class WriteIndicatorInput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    operation: IndicatorSave | IndicatorDelete


class IndicatorCheck(Model):
    title: str
    shortTitle: str
    overlay: bool
    inputs: list[dict[str, Any]]
    alertConditions: list[str]
    error: str | None = Field(description="Why the indicator does not load; null when it loads.")
    line: int | None = Field(description="Line of the error in the indicator's code, when known.")


class IndicatorWritten(Model):
    name: str = Field(description="The indicator's name, as read_indicator, read_data and write_chart take it.")
    file: str | None = Field(description="The file written; null after a delete.")
    check: IndicatorCheck | None = Field(description="How Chart loads the indicator now; null after a delete.")


class ReadAlertInput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    view: Literal["list", "one", "log"]
    alertId: Annotated[str | None, Field(min_length=1)] = None
    state: Literal["active", "paused", "triggered", "expired"] | None = None
    cursor: str | None = None
    """
    nextCursor of the previous page
    """
    limit: Annotated[int | None, Field(ge=1, le=100)] = None


class AlertTarget(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    chartId: Annotated[str | None, Field(min_length=1)] = None
    symbol: Annotated[str | None, Field(min_length=1)] = None
    timeframe: Annotated[str | None, Field(min_length=2)] = None
    """
    1m 5m 15m 1h 4h 1d 1w 1mo ...
    """
    watchlistId: Annotated[str | None, Field(min_length=1)] = None


class Operand(Model):
    """
    price {field: close|open|high|low|hl2|hlc3|ohlc4|volume}; indicator {name, params, output} (output is the output id read_data lists); drawing {chartId, drawingId}; value {value}; alertcondition {name, params, condition} (with op triggered).
    """

    model_config = ConfigDict(extra="allow")

    kind: Literal["price", "indicator", "drawing", "value", "alertcondition"]


class AlertCondition(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    left: Operand
    """
    price {field: close|open|high|low|hl2|hlc3|ohlc4|volume}; indicator {name, params, output} (output is the output id read_data lists); drawing {chartId, drawingId}; value {value}; alertcondition {name, params, condition} (with op triggered).
    """
    op: Literal["crossing", "crossing_up", "crossing_down", "greater", "less", "entering_channel", "exiting_channel", "inside_channel", "outside_channel", "moving_up", "moving_down", "moving_up_percent", "moving_down_percent", "triggered"]
    right: Operand | None = None
    """
    price {field: close|open|high|low|hl2|hlc3|ohlc4|volume}; indicator {name, params, output} (output is the output id read_data lists); drawing {chartId, drawingId}; value {value}; alertcondition {name, params, condition} (with op triggered).
    """
    right2: Operand | None = None
    """
    price {field: close|open|high|low|hl2|hlc3|ohlc4|volume}; indicator {name, params, output} (output is the output id read_data lists); drawing {chartId, drawingId}; value {value}; alertcondition {name, params, condition} (with op triggered).
    """
    amount: float | None = None
    bars: Annotated[int | None, Field(ge=1, le=5000)] = None


class AlertChannels(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    toast: bool | None = None
    sound: bool | None = None
    mobile: bool | None = None


class AlertCreate(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    op: Literal["create"]
    target: AlertTarget
    conditions: Annotated[list[AlertCondition], Field(max_length=5, min_length=1)]
    frequency: Literal["once", "once_per_bar", "once_per_bar_close", "once_per_minute"] | None = (
        None
    )
    expiresAt: str | None = None
    """
    ISO-8601; omit for never
    """
    message: Annotated[str | None, Field(max_length=2000)] = None
    name: Annotated[str | None, Field(max_length=64)] = None
    channels: AlertChannels | None = None


class AlertConditionUpdate(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    left: Operand
    """
    price {field: close|open|high|low|hl2|hlc3|ohlc4|volume}; indicator {name, params, output} (output is the output id read_data lists); drawing {chartId, drawingId}; value {value}; alertcondition {name, params, condition} (with op triggered).
    """
    op: Literal["crossing", "crossing_up", "crossing_down", "greater", "less", "entering_channel", "exiting_channel", "inside_channel", "outside_channel", "moving_up", "moving_down", "moving_up_percent", "moving_down_percent", "triggered"]
    right: Operand | None = None
    """
    price {field: close|open|high|low|hl2|hlc3|ohlc4|volume}; indicator {name, params, output} (output is the output id read_data lists); drawing {chartId, drawingId}; value {value}; alertcondition {name, params, condition} (with op triggered).
    """
    right2: Operand | None = None
    """
    price {field: close|open|high|low|hl2|hlc3|ohlc4|volume}; indicator {name, params, output} (output is the output id read_data lists); drawing {chartId, drawingId}; value {value}; alertcondition {name, params, condition} (with op triggered).
    """
    amount: float | None = None
    bars: Annotated[int | None, Field(ge=1, le=5000)] = None


class AlertUpdate(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    op: Literal["update"]
    alertId: Annotated[str, Field(min_length=1)]
    revision: int
    target: AlertTarget | None = None
    conditions: Annotated[list[AlertConditionUpdate], Field(max_length=5, min_length=1)] | None = None
    frequency: Literal["once", "once_per_bar", "once_per_bar_close", "once_per_minute"] | None = (
        None
    )
    expiresAt: str | None = None
    """
    ISO-8601; omit for never
    """
    message: Annotated[str | None, Field(max_length=2000)] = None
    name: Annotated[str | None, Field(max_length=64)] = None
    channels: AlertChannels | None = None


class AlertPause(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    op: Literal["pause"]
    alertId: Annotated[str, Field(min_length=1)]
    revision: int


class AlertResume(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    op: Literal["resume"]
    alertId: Annotated[str, Field(min_length=1)]
    revision: int


class AlertDelete(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    op: Literal["delete"]
    alertId: Annotated[str, Field(min_length=1)]


class WriteAlertInput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    operation: AlertCreate | AlertUpdate | AlertPause | AlertResume | AlertDelete


class ReadWatchlistInput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    view: Literal["list", "one"]
    watchlistId: Annotated[str | None, Field(min_length=1)] = None
    cursor: str | None = None
    """
    nextCursor of the previous page
    """
    limit: Annotated[int | None, Field(ge=1, le=200)] = None


class SymbolName(RootModel[str]):
    root: Annotated[str, Field(min_length=1)]


class WatchlistSection(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    name: Annotated[str | None, Field(max_length=64)] = None
    symbols: list[SymbolName] | None = None


class WatchlistCreate(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    op: Literal["create"]
    name: Annotated[str, Field(max_length=64, min_length=1)]
    sections: list[WatchlistSection] | None = None


class WatchlistSectionUpdate(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    name: Annotated[str | None, Field(max_length=64)] = None
    symbols: list[SymbolName] | None = None


class WatchlistAdd(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    symbol: Annotated[str, Field(min_length=1)]
    section: Annotated[str | None, Field(max_length=64)] = None


class WatchlistRemove(RootModel[str]):
    root: Annotated[str, Field(min_length=1)]


class WatchlistUpdate(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    op: Literal["update"]
    watchlistId: Annotated[str, Field(min_length=1)]
    revision: int
    name: Annotated[str | None, Field(max_length=64, min_length=1)] = None
    sections: list[WatchlistSectionUpdate] | None = None
    add: list[WatchlistAdd] | None = None
    remove: list[WatchlistRemove] | None = None


class WatchlistDelete(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    op: Literal["delete"]
    watchlistId: Annotated[str, Field(min_length=1)]


class WriteWatchlistInput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    operation: WatchlistCreate | WatchlistUpdate | WatchlistDelete


class ReadScreenInput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    view: Literal["list", "one"]
    screenId: Annotated[str | None, Field(min_length=1)] = None
    matchedOnly: bool | None = None
    cursor: str | None = None
    """
    nextCursor of the previous page
    """
    limit: Annotated[int | None, Field(ge=1, le=200)] = None


class ScreenScope(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    watchlistId: Annotated[str | None, Field(min_length=1)] = None
    symbols: Annotated[list[SymbolName] | None, Field(max_length=200, min_length=1)] = None


class ScreenCondition(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    left: Operand
    """
    price {field: close|open|high|low|hl2|hlc3|ohlc4|volume}; indicator {name, params, output} (output is the output id read_data lists); drawing {chartId, drawingId}; value {value}; alertcondition {name, params, condition} (with op triggered).
    """
    op: Literal["crossing", "crossing_up", "crossing_down", "greater", "less", "entering_channel", "exiting_channel", "inside_channel", "outside_channel", "moving_up", "moving_down", "moving_up_percent", "moving_down_percent", "triggered"]
    right: Operand | None = None
    """
    price {field: close|open|high|low|hl2|hlc3|ohlc4|volume}; indicator {name, params, output} (output is the output id read_data lists); drawing {chartId, drawingId}; value {value}; alertcondition {name, params, condition} (with op triggered).
    """
    right2: Operand | None = None
    """
    price {field: close|open|high|low|hl2|hlc3|ohlc4|volume}; indicator {name, params, output} (output is the output id read_data lists); drawing {chartId, drawingId}; value {value}; alertcondition {name, params, condition} (with op triggered).
    """
    amount: float | None = None
    bars: Annotated[int | None, Field(ge=1, le=5000)] = None


class ScreenSave(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    op: Literal["save"]
    screenId: Annotated[str | None, Field(min_length=1)] = None
    revision: int | None = None
    name: Annotated[str, Field(max_length=64, min_length=1)]
    scope: ScreenScope
    timeframe: Annotated[str, Field(min_length=2)]
    """
    1m 5m 15m 1h 4h 1d 1w 1mo ...
    """
    conditions: Annotated[list[ScreenCondition], Field(max_length=5, min_length=1)]


class ScreenRun(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    op: Literal["run"]
    screenId: Annotated[str, Field(min_length=1)]


class ScreenDelete(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    op: Literal["delete"]
    screenId: Annotated[str, Field(min_length=1)]


class WriteScreenInput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    operation: ScreenSave | ScreenRun | ScreenDelete


class RestartIndicatorsInput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
