"""The chart document and its one command set.

The Chart UI (private routes), AI and other modules (``write_chart``) change a chart only
through these commands. Times are UTC milliseconds; on brick chart types (Renko, Kagi,
Point & Figure, Line break, Range) a drawing point's ``time`` is the brick's display key.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, model_validator

from .models import ResearchLayer

# Tool vocabulary: (point count, "minimum" or "exact", text rule).
TOOLS = {
    "trendline": (2, "exact", "optional"), "info_line": (2, "exact", "optional"), "ray": (2, "exact", "optional"),
    "extended_line": (2, "exact", "optional"), "trend_angle": (2, "exact", "optional"), "hline": (1, "exact", "optional"),
    "horizontal_ray": (1, "exact", "optional"), "vline": (1, "exact", "optional"), "cross_line": (1, "exact", "optional"),
    "arrow": (2, "exact", "optional"), "path": (2, "minimum", "none"), "parallel_channel": (3, "exact", "optional"),
    "price_channel": (3, "exact", "none"), "regression_trend": (2, "exact", "generated"),
    "flat_top_bottom": (3, "exact", "none"), "pitchfork": (3, "exact", "none"), "schiff_pitchfork": (3, "exact", "none"),
    "fib": (2, "exact", "generated"), "fib_extension": (3, "exact", "generated"), "fib_channel": (3, "exact", "generated"),
    "fib_time": (2, "exact", "generated"), "gann_box": (2, "exact", "generated"), "gann_fan": (2, "exact", "generated"),
    "brush": (2, "minimum", "none"), "highlighter": (2, "minimum", "none"), "rect": (2, "exact", "optional"),
    "rotated_rect": (3, "exact", "none"), "ellipse": (2, "exact", "none"), "triangle": (3, "exact", "none"),
    "arc": (3, "exact", "none"), "polyline": (2, "minimum", "none"), "text": (1, "exact", "required"),
    "anchored_text": (2, "exact", "required"), "note": (1, "exact", "required"), "callout": (2, "exact", "required"),
    "comment": (1, "exact", "required"), "price_label": (1, "exact", "generated"), "arrow_marker": (1, "exact", "optional"),
    "xabcd": (5, "exact", "generated"), "abcd": (4, "exact", "generated"), "cypher": (5, "exact", "generated"),
    "head_shoulders": (7, "exact", "generated"), "elliott_impulse": (6, "exact", "generated"),
    "elliott_correction": (4, "exact", "generated"), "elliott_triangle": (6, "exact", "generated"),
    "measure": (2, "exact", "generated"), "date_range": (2, "exact", "generated"), "price_range": (2, "exact", "generated"),
    "date_price_range": (2, "exact", "generated"), "anchored_vwap": (1, "exact", "generated"),
    "forecast": (3, "exact", "generated"), "long_position": (3, "exact", "generated"),
    "short_position": (3, "exact", "generated"),
}
# Tools that read OHLCV over their span belong to the price pane.
PRICE_PANE_TOOLS = {"regression_trend", "anchored_vwap"}
PRICE_PANE = "price"
BRICK_TYPES = ("renko", "kagi", "point_figure", "line_break", "range")
MAX_UNDO = 100

Id = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
Name = Annotated[str, Field(min_length=1, max_length=64)]
Color = Annotated[str, Field(min_length=1, max_length=64)]
Scalar = bool | int | float | str
Json = dict[str, JsonValue]
Direction = Literal[-1, 1]
ChartType = Literal[
    "candles", "hollow_candles", "bars", "line", "line_markers", "step_line", "area", "hlc_area",
    "baseline", "columns", "high_low", "heikin_ashi", *BRICK_TYPES,
]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Point(Model):
    time: int
    price: float


class DrawingStyle(Model):
    color: Color | None = None
    lineWidth: Annotated[float, Field(gt=0, le=20)] | None = None
    lineStyle: Literal["solid", "dashed", "dotted"] | None = None
    fillColor: Color | None = None
    fillOpacity: Annotated[float, Field(ge=0, le=1)] | None = None
    fontSize: Annotated[float, Field(gt=0, le=96)] | None = None
    textColor: Color | None = None
    fontWeight: Literal[400, 600] | None = None
    fontStyle: Literal["normal", "italic"] | None = None


Unit = Literal["seconds", "minutes", "hours", "days", "weeks", "months"]


class TimeframeBand(Model):
    """The drawing shows on timeframes of this unit from ``from_`` to ``to`` (TradingView "Visibility")."""
    enabled: bool
    from_: Annotated[int, Field(ge=1, alias="from")]
    to: Annotated[int, Field(ge=1)]


class Drawing(Model):
    id: Id
    tool: Literal[tuple(TOOLS)]  # type: ignore[valid-type]
    points: Annotated[list[Point], Field(max_length=4096)]
    paneId: Id = PRICE_PANE
    text: Annotated[str, Field(min_length=1, max_length=4000)] | None = None
    style: DrawingStyle = DrawingStyle()
    settings: Json = {}
    visibility: dict[Unit, TimeframeBand] = {}
    locked: bool = False
    visible: bool = True
    name: Name | None = None

    @model_validator(mode="after")
    def tool_rules(self):
        count, kind, text = TOOLS[self.tool]
        if len(self.points) < count or kind == "exact" and len(self.points) != count:
            raise ValueError(f"A {self.tool} drawing needs {'at least ' if kind == 'minimum' else ''}{count} point(s)")
        if text == "required" and self.text is None:
            raise ValueError(f"A {self.tool} drawing needs text")
        if text in {"none", "generated"} and self.text is not None:
            raise ValueError(f"A {self.tool} drawing has no text")
        if self.tool in PRICE_PANE_TOOLS and self.paneId != PRICE_PANE:
            raise ValueError(f"A {self.tool} drawing belongs to the price pane")
        return self


class Pane(Model):
    """``height`` is the pane's stretch factor (the price pane starts at 3, a new pane at 1)."""
    id: Id
    height: Annotated[float, Field(gt=0, le=100)] = 1.0
    collapsed: bool = False


class IndicatorRef(Model):
    """A built-in indicator by name (``rsi``) or a user indicator by its Developer project id."""
    source: Literal["builtin", "user"]
    id: Annotated[str, Field(min_length=1, max_length=256)]


class PlotStyle(Model):
    """User overrides of one Pine output (keyed by the output's id in the indicator's meta)."""
    visible: bool | None = None
    color: Color | None = None
    lineWidth: Annotated[int, Field(ge=1, le=10)] | None = None
    lineStyle: Literal["solid", "dashed", "dotted"] | None = None


class Indicator(Model):
    id: Id
    definition: IndicatorRef
    params: Annotated[dict[str, Scalar], Field(max_length=64)] = {}
    paneId: Id = PRICE_PANE
    visible: bool = True
    name: Name | None = None
    styles: dict[str, PlotStyle] = {}


class Comparison(Model):
    """Another symbol drawn against the main series' bar times."""
    id: Id
    instrument: Json
    series: Json
    scale: Literal["same_percent", "new_left", "new_right"] = "same_percent"
    color: Color | None = None
    visible: bool = True


class Projection(Model):
    """Settings of the brick chart types and Heikin Ashi (``bar_projection``)."""
    source: Literal["close", "ohlc"] = "close"
    boxSize: Annotated[float, Field(gt=0)] | None = None
    reversalBoxes: Annotated[int, Field(ge=1, le=100)] = 3
    lineCount: Annotated[int, Field(ge=1, le=100)] = 3
    rangeTicks: Annotated[int, Field(ge=1, le=1_000_000)] | None = None


class SavedLayer(ResearchLayer):
    visible: bool = True
    version: str
    symbol: str


class ChartDocument(Model):
    """One chart. ``instrument`` and ``series`` are Market's vocabulary Instrument and SeriesId.

    ``drawings`` holds every drawing group of this chart; the one shown is
    ``drawings[drawing_group(document)]`` (per instrument and price basis, and per chart
    type on brick charts). ``display`` is the frontend's presentation (colors, scales,
    time zone, ...); the backend stores it and does not read it.
    """
    id: Id
    instrument: Json
    series: Json
    chartType: ChartType = "candles"
    projection: Projection = Projection()
    panes: list[Pane] = [Pane(id=PRICE_PANE, height=3.0)]
    indicators: Annotated[list[Indicator], Field(max_length=64)] = []
    drawings: dict[str, Annotated[list[Drawing], Field(max_length=2000)]] = {}
    comparisons: Annotated[list[Comparison], Field(max_length=8)] = []
    display: Json = {}
    layers: list[SavedLayer] = []

    def for_view(self) -> dict:
        """Series points are read once per layer version, independently of document and price updates."""
        value = self.model_dump(mode="json", by_alias=True, exclude={"layers"})
        value["layers"] = [layer.model_dump(mode="json", by_alias=True, exclude={"series", "markers"})
                           for layer in self.layers]
        return value

    @model_validator(mode="after")
    def consistent(self):
        panes = [pane.id for pane in self.panes]
        if not panes or panes[0] != PRICE_PANE or len(set(panes)) != len(panes):
            raise ValueError("Panes start with the price pane and are unique")
        for kind, ids in (("indicator", [item.id for item in self.indicators]),
                          ("comparison", [item.id for item in self.comparisons]),
                          ("layer", [item.id for item in self.layers]),
                          *((f"drawing in {key}", [item.id for item in group]) for key, group in self.drawings.items())):
            if len(ids) != len(set(ids)):
                raise ValueError(f"Each {kind} id is unique")
        if any(item.paneId not in panes for item in self.indicators) or any(
                item.paneId not in panes for group in self.drawings.values() for item in group):
            raise ValueError("Indicators and drawings sit on existing panes")
        return self


def group_key(series: dict, chart_type: str) -> str:
    group = f"{series['instrumentId']}:{series['priceBasis']}"
    return f"{group}:{chart_type}" if chart_type in BRICK_TYPES else group


def drawing_group(document: ChartDocument) -> str:
    return group_key(document.series, document.chartType)


# ── Commands ──


class DrawingPatch(Model):
    """Fields replace; ``text: null`` removes the text."""
    points: list[Point] | None = None
    paneId: Id | None = None
    text: str | None = None
    style: DrawingStyle | None = None
    settings: Json | None = None
    visibility: dict[Unit, TimeframeBand] | None = None
    locked: bool | None = None
    visible: bool | None = None
    name: Name | None = None


class DrawingAdd(Model):
    type: Literal["drawing.add"]
    value: Drawing


class DrawingUpdate(Model):
    type: Literal["drawing.update"]
    id: Id
    patch: DrawingPatch


class DrawingDelete(Model):
    type: Literal["drawing.delete"]
    id: Id


class DrawingBulk(Model):
    """Exactly one of ``locked``, ``visible``, ``remove`` applies to ``ids`` (every drawing shown when null)."""
    type: Literal["drawing.bulk"]
    ids: list[Id] | None = None
    locked: bool | None = None
    visible: bool | None = None
    remove: Literal[True] | None = None


class DrawingDuplicate(Model):
    type: Literal["drawing.duplicate"]
    ids: Annotated[list[Id], Field(min_length=1)]


class DrawingReorder(Model):
    """Moves a drawing one step up (+1, drawn later) or down (-1) in the z-order."""
    type: Literal["drawing.reorder"]
    id: Id
    direction: Direction


class DrawingsReplace(Model):
    """The drawing group shown now, from another chart of the layout (drawing sync)."""
    type: Literal["drawings.replace"]
    drawings: list[Drawing]


class IndicatorAdd(Model):
    """A ``paneId`` that does not exist yet creates that pane below the others."""
    type: Literal["indicator.add"]
    value: Indicator


class IndicatorPatch(Model):
    params: dict[str, Scalar] | None = None
    paneId: Id | None = None
    visible: bool | None = None
    name: Name | None = None
    styles: dict[str, PlotStyle] | None = None


class IndicatorUpdate(Model):
    type: Literal["indicator.update"]
    id: Id
    patch: IndicatorPatch


class IndicatorDelete(Model):
    type: Literal["indicator.delete"]
    id: Id


class IndicatorDuplicate(Model):
    type: Literal["indicator.duplicate"]
    id: Id


class IndicatorReorder(Model):
    type: Literal["indicator.reorder"]
    id: Id
    direction: Direction


class IndicatorsReplace(Model):
    """Indicators and panes from another chart (indicator sync) or an indicator template."""
    type: Literal["indicators.replace"]
    indicators: list[Indicator]
    panes: list[Pane]


class PaneUpdate(Model):
    type: Literal["pane.update"]
    id: Id
    height: Annotated[float, Field(gt=0, le=100)] | None = None
    collapsed: bool | None = None


class PaneMove(Model):
    """Moves an indicator pane up (-1) or down (+1); the price pane moves too."""
    type: Literal["pane.move"]
    id: Id
    direction: Direction


class ComparisonAdd(Model):
    """``instrumentId`` is resolved to the instrument and its default series on the main timeframe."""
    type: Literal["comparison.add"]
    id: Id
    instrumentId: str
    scale: Literal["same_percent", "new_left", "new_right"] = "same_percent"
    color: Color | None = None


class ComparisonUpdate(Model):
    type: Literal["comparison.update"]
    id: Id
    scale: Literal["same_percent", "new_left", "new_right"] | None = None
    color: Color | None = None
    visible: bool | None = None


class ComparisonDelete(Model):
    type: Literal["comparison.delete"]
    id: Id


class LayerSet(Model):
    type: Literal["layer.set"]
    layer: ResearchLayer


class LayerVisible(Model):
    type: Literal["layer.visible"]
    id: str
    visible: bool


class LayerDelete(Model):
    type: Literal["layer.delete"]
    id: str


class DisplayUpdate(Model):
    """Top-level keys replace; null removes."""
    type: Literal["display.update"]
    patch: Json


class DisplayReplace(Model):
    """A chart settings template applied."""
    type: Literal["display.replace"]
    display: Json


class SelectionSet(Model):
    """Switch symbol, timeframe or price series. Omitted fields keep the current choice;
    a new instrument takes Market's default price basis, adjustment and session for it.
    Indicators stay and recompute; the drawings shown become the new group's."""
    type: Literal["selection.set"]
    instrumentId: str | None = None
    timeframe: str | None = None
    priceBasis: Literal["last", "mark", "index"] | None = None
    adjustment: Literal["raw", "split", "split_dividend"] | None = None
    adjustmentAnchor: Literal["latest", "earliest"] | None = None


class ChartTypeSet(Model):
    type: Literal["chart_type.set"]
    chartType: ChartType
    projection: Projection | None = None


Command = Annotated[
    DrawingAdd | DrawingUpdate | DrawingDelete | DrawingBulk | DrawingDuplicate | DrawingReorder | DrawingsReplace
    | IndicatorAdd | IndicatorUpdate | IndicatorDelete | IndicatorDuplicate | IndicatorReorder | IndicatorsReplace
    | PaneUpdate | PaneMove | ComparisonAdd | ComparisonUpdate | ComparisonDelete
    | LayerSet | LayerVisible | LayerDelete | DisplayUpdate | DisplayReplace | SelectionSet | ChartTypeSet,
    Field(discriminator="type"),
]
Commands = Annotated[list[Command], Field(min_length=1, max_length=500)]
COMMANDS = TypeAdapter(Commands)
