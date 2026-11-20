/* One pane's drawings as one series primitive: it paints every drawing of the pane, puts the
   focused drawing's anchors on both axes, and answers hit tests. `updateAllViews` runs several times per data update, so it
   only marks the geometry stale; geometry is built the first time a frame, an axis or a hit test needs it, off-screen
   drawings dropped (spike pitfall 2). */
import type {
  IChartApiBase, IPrimitivePaneRenderer, IPrimitivePaneView, ISeriesApi, ISeriesPrimitive, ISeriesPrimitiveAxisView,
  Logical, PrimitiveHoveredItem, SeriesAttachedParameter, SeriesType,
} from "lightweight-charts";
import type { CanvasRenderingTarget2D } from "fancy-canvas";
import { buildOverlayGeometry, buildOverlayRuntimeData, type OverlayGeometryFigure, type OverlayPixelPoint, type OverlayRuntimeData } from "../drawings/geometry";
import { overlayControlHandles, hitTestOverlayInteractionSurface, type OverlayControlHandle, type OverlayInteractionHit } from "../drawings/interactionGeometry";
import { createAnchoredVwapAccumulator, regressionValueAt } from "../drawings/statisticalOverlays";
import type { Drawing, GeometryBar, Point } from "../drawings/types";
import { logicalToX, type TimeMap } from "./timeMap";
import { withAlpha, type ChartColors } from "./theme";
import { tokenValue } from "@talyx/sdk";

export interface DrawingFocus { selected: string | null; hovered: string | null; hoveredHandle: string | null; dragging: boolean }

export interface DrawingLayerContext {
  timeMap(): TimeMap;
  /** Bars as objects, built only when a drawing reads them (regression, anchored VWAP, ranges). */
  geometryBars(): readonly GeometryBar[];
  formatTime(time: number): string;
  formatPrice(price: number): string;
  colors: ChartColors;
  /** Position size readouts: the value of one price unit, when the instrument says it. */
  pointValue: number | null;
}

interface Surface { drawing: Drawing; pixels: OverlayPixelPoint[]; figures: OverlayGeometryFigure[]; handles: OverlayControlHandle[] }

const READS_BARS = new Set(["regression_trend", "anchored_vwap", "info_line", "measure", "date_range", "date_price_range", "trendline", "ray", "extended_line", "arrow"]);
/** TradingView's default Fibonacci level colours, as the nearest colours of the user palette. */
const LEVEL_COLORS: Record<string, string> = {
  "0": "--tx-user-palette-3", "0.236": "--tx-user-palette-5", "0.382": "--tx-user-palette-6", "0.5": "--tx-user-palette-8", "0.618": "--tx-user-palette-9",
  "0.786": "--tx-user-palette-11", "1": "--tx-user-palette-3", "1.618": "--tx-user-palette-12", "2.618": "--tx-user-palette-5", "3.618": "--tx-user-palette-13",
  "4.236": "--tx-user-palette-14",
};
const LEVEL_TOOLS = new Set(["fib", "fib_extension", "fib_channel", "gann_box", "gann_fan", "fib_time"]);
export const HIT_TOLERANCE = 5;

export class DrawingLayer implements ISeriesPrimitive<number> {
  private chart!: IChartApiBase<number>;
  series!: ISeriesApi<SeriesType, number>;
  private request: () => void = () => undefined;
  private drawings: Drawing[] = [];
  private preview: Drawing | null = null;
  private surfaces: Surface[] = [];
  private stale = true;
  private priceViews: AxisLabel[] = [];
  private timeViews: AxisLabel[] = [];
  readonly focus: DrawingFocus = { selected: null, hovered: null, hoveredHandle: null, dragging: false };
  private readonly view: IPrimitivePaneView;

  constructor(private readonly context: DrawingLayerContext) {
    this.view = { zOrder: () => "top", renderer: () => { this.ensure(); return new DrawingRenderer(this.surfaces, this.focus, context.colors); } };
  }

  attached(param: SeriesAttachedParameter<number>) { this.chart = param.chart; this.series = param.series as ISeriesApi<SeriesType, number>; this.request = param.requestUpdate; }
  detached() {}
  updateAllViews() { this.stale = true; }
  paneViews() { return [this.view]; }
  priceAxisViews() { this.ensure(); return this.priceViews; }
  timeAxisViews() { this.ensure(); return this.timeViews; }

  /** The pane's drawings, in z-order; `preview` is a drawing being made or dragged (shown in place of its committed self). */
  show(drawings: Drawing[], preview: Drawing | null = this.preview): void {
    this.drawings = drawings; this.preview = preview; this.redraw();
  }
  setPreview(preview: Drawing | null): void { this.preview = preview; this.redraw(); }
  get previewDrawing(): Drawing | null { return this.preview; }
  drawing(id: string): Drawing | undefined { return this.preview?.id === id ? this.preview : this.drawings.find(item => item.id === id); }
  redraw(): void { this.stale = true; this.request(); }

  get width(): number { return this.chart.timeScale().width(); }
  get height(): number { return this.series.getPane().getHeight(); }
  barSpacing(): number { return this.chart.timeScale().options().barSpacing; }

  toPixel(point: Point): OverlayPixelPoint | null {
    const logical = this.context.timeMap().toLogical(point.time);
    if (logical === null) return null;
    const x = logicalToX(this.chart.timeScale(), logical), y = this.series.priceToCoordinate(point.price);
    return x === null || y === null ? null : { x, y };
  }

  /** A pixel back to a point; the time lands on a whole bar (TradingView anchors to bars). */
  fromPixel(x: number, y: number): Point | null {
    const logical = this.chart.timeScale().coordinateToLogical(x), price = this.series.coordinateToPrice(y);
    if (logical === null || price === null) return null;
    const time = this.context.timeMap().snapTime(logical);
    return time === null ? null : { time, price };
  }

  /** The bar under a pixel, for the OHLC magnet. */
  barAt(x: number): GeometryBar | null {
    const logical = this.chart.timeScale().coordinateToLogical(x);
    if (logical === null) return null;
    const index = Math.round(logical), bars = this.context.geometryBars();
    return index >= 0 && index < bars.length ? bars[index] : null;
  }
  priceToY(price: number): number | null { return this.series.priceToCoordinate(price); }

  /** Topmost drawing under the pointer; the selected drawing's handles win. */
  hit(x: number, y: number): { drawing: Drawing; hit: OverlayInteractionHit } | null {
    this.ensure();
    const point = { x, y };
    const selected = this.surfaces.find(surface => surface.drawing.id === this.focus.selected);
    if (selected) {
      const hit = hitTestOverlayInteractionSurface({ type: selected.drawing.tool, figures: selected.figures, handles: selected.handles, tolerance: 8 }, point);
      if (hit) return { drawing: selected.drawing, hit };
    }
    for (let index = this.surfaces.length - 1; index >= 0; index--) {
      const surface = this.surfaces[index];
      const hit = hitTestOverlayInteractionSurface({ type: surface.drawing.tool, figures: surface.figures, handles: surface.drawing.id === this.focus.hovered ? surface.handles : [], tolerance: HIT_TOLERANCE }, point);
      if (hit) return { drawing: surface.drawing, hit };
    }
    return null;
  }

  hitTest(x: number, y: number): PrimitiveHoveredItem | null {
    const found = this.hit(x, y);
    if (!found) return null;
    return { externalId: found.drawing.id, zOrder: "top", cursorStyle: found.hit.kind === "handle" ? "pointer" : this.focus.dragging ? "grabbing" : "move" };
  }

  private ensure(): void {
    if (!this.stale) return;
    this.stale = false;
    const width = this.width, height = this.height, bounds = { width, height };
    const shown = this.preview ? [...this.drawings.filter(item => item.id !== this.preview!.id), this.preview] : this.drawings;
    this.surfaces = [];
    for (const drawing of shown) {
      if (!drawing.visible) continue;
      const pixels = drawing.points.map(point => this.toPixel(point));
      if (pixels.some(pixel => pixel === null)) continue;
      const placed = pixels as OverlayPixelPoint[];
      const runtime = this.runtime(drawing);
      const figures = buildOverlayGeometry(drawing.tool, placed, bounds, runtime);
      if (!onScreen(figures, placed, width, height)) continue;
      this.surfaces.push({ drawing, pixels: placed, figures, handles: overlayControlHandles(drawing.tool, placed) });
    }
    this.axisViews();
  }

  private runtime(drawing: Drawing): OverlayRuntimeData {
    const points = drawing.points.map(point => ({ timestamp: point.time, value: point.price }));
    const bars = READS_BARS.has(drawing.tool) ? this.context.geometryBars() : [];
    let runtime = buildOverlayRuntimeData(drawing.tool, points, bars, drawing.text ?? undefined, drawing.settings, this.context.pointValue);
    runtime = { ...runtime, selected: drawing.id === this.focus.selected };
    const scale = this.chart.timeScale();
    if (runtime.levelValues) runtime.projectedLevels = runtime.levelValues.map(value => ({ x: 0, y: this.series.priceToCoordinate(value) ?? 0 }));
    if (drawing.tool === "anchored_vwap") {
      const curve = createAnchoredVwapAccumulator(points, bars).points.flatMap(point => {
        if (point.value === null) return [];
        const x = scale.logicalToCoordinate(point.dataIndex as Logical), y = this.series.priceToCoordinate(point.value);
        return x === null || y === null ? [] : [{ x, y }];
      });
      runtime.projectedCurve = curve;
    }
    const result = runtime.regression;
    if (result) {
      const range = scale.getVisibleLogicalRange();
      const end = result.settings.extendRight && range ? Math.max(result.toIndex, Math.ceil(range.to)) : result.toIndex;
      const segment = (offset: number) => [result.fromIndex, end].map(index => ({
        x: scale.logicalToCoordinate(index as Logical) ?? 0, y: this.series.priceToCoordinate(regressionValueAt(result, index) + offset) ?? 0,
      })) as [OverlayPixelPoint, OverlayPixelPoint];
      runtime.projectedRegression = {
        base: segment(0),
        ...(result.settings.upperEnabled ? { upper: segment(result.sigma * result.settings.upperDeviation) } : {}),
        ...(result.settings.lowerEnabled ? { lower: segment(-result.sigma * result.settings.lowerDeviation) } : {}),
      };
    }
    return runtime;
  }

  /** Every anchor of the focused drawing on both axes, like TradingView; horizontal lines always show their price. */
  private axisViews(): void {
    this.priceViews = []; this.timeViews = [];
    const focus = this.focus.selected ?? this.focus.hovered;
    for (const surface of this.surfaces) {
      const { drawing } = surface, focused = drawing.id === focus;
      const horizontal = drawing.tool === "hline" || drawing.tool === "horizontal_ray";
      if (!focused && !(horizontal && drawing.settings.showPriceLabels !== false)) continue;
      const back = focused ? this.context.colors.accent : drawing.style.color ?? this.context.colors.series[0];
      for (const point of drawing.points) {
        const y = this.series.priceToCoordinate(point.price);
        if (y !== null) this.priceViews.push(new AxisLabel(y, this.context.formatPrice(point.price), back));
      }
      if (focused && !horizontal) for (let index = 0; index < drawing.points.length; index++) {
        this.timeViews.push(new AxisLabel(surface.pixels[index].x, this.context.formatTime(drawing.points[index].time), back));
      }
    }
  }
}

function onScreen(figures: OverlayGeometryFigure[], pixels: OverlayPixelPoint[], width: number, height: number): boolean {
  if (pixels.some(point => point.x >= -50 && point.x <= width + 50 && point.y >= -50 && point.y <= height + 50)) return true;
  // A line crossing the screen with both ends outside still shows: keep anything whose figures reach the visible area.
  for (const figure of figures) {
    const coordinates = (figure.attrs.coordinates as OverlayPixelPoint[] | undefined) ?? [{ x: Number(figure.attrs.x), y: Number(figure.attrs.y) }];
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const point of coordinates) { minX = Math.min(minX, point.x); maxX = Math.max(maxX, point.x); minY = Math.min(minY, point.y); maxY = Math.max(maxY, point.y); }
    if (maxX >= 0 && minX <= width && maxY >= 0 && minY <= height) return true;
  }
  return false;
}

class AxisLabel implements ISeriesPrimitiveAxisView {
  constructor(private readonly at: number, private readonly label: string, private readonly back: string) {}
  coordinate() { return this.at; }
  text() { return this.label; }
  textColor() { return tokenValue("--tx-user-palette-1"); }
  backColor() { return this.back; }
  visible() { return true; }
  tickVisible() { return true; }
}

const DASHES = { dashed: [6, 4], dotted: [1.5, 3] } as const;

class DrawingRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly surfaces: Surface[], private readonly focus: DrawingFocus, private readonly colors: ChartColors) {}

  draw(target: CanvasRenderingTarget2D) {
    target.useMediaCoordinateSpace(({ context }) => {
      context.save();
      context.lineCap = "round"; context.lineJoin = "round";
      for (const surface of this.surfaces) this.paint(context, surface);
      context.restore();
    });
  }

  private paint(context: CanvasRenderingContext2D, { drawing, figures, handles }: Surface) {
    const style = drawing.style;
    const color = style.color ?? this.colors.series[0];
    const width = style.lineWidth ?? (drawing.tool === "fib" || drawing.tool === "fib_extension" ? 1 : 2);
    const fill = style.fillColor ?? color, opacity = style.fillOpacity ?? 0.15;
    const levelled = LEVEL_TOOLS.has(drawing.tool);
    for (const figure of figures) {
      const styles = figure.styles ?? {};
      const level = levelled && figure.key ? /^(?:level|background):(.+)$/.exec(figure.key)?.[1] : undefined;
      const stroke = (styles.color as string | undefined) ?? (level !== undefined && LEVEL_COLORS[level] ? tokenValue(LEVEL_COLORS[level]) : color);
      context.strokeStyle = stroke;
      context.lineWidth = (styles.size as number | undefined) ?? width;
      const dashed = styles.style === "dashed" ? (styles.dashedValue as number[] | undefined) ?? DASHES.dashed : style.lineStyle && style.lineStyle !== "solid" ? DASHES[style.lineStyle] : [];
      context.setLineDash(dashed as number[]);
      switch (figure.type) {
        case "line": {
          const points = figure.attrs.coordinates as OverlayPixelPoint[];
          context.beginPath();
          points.forEach((point, index) => index ? context.lineTo(point.x, point.y) : context.moveTo(point.x, point.y));
          context.stroke();
          break;
        }
        case "polygon": {
          const points = figure.attrs.coordinates as OverlayPixelPoint[];
          context.beginPath();
          points.forEach((point, index) => index ? context.lineTo(point.x, point.y) : context.moveTo(point.x, point.y));
          context.closePath();
          context.fillStyle = (styles.color as string | undefined) ?? withAlpha(fill, drawing.tool === "highlighter" ? 0.35 : opacity);
          context.fill();
          if (drawing.tool !== "brush" && drawing.tool !== "highlighter") context.stroke();
          else if (drawing.tool === "brush") { context.fillStyle = color; context.fill(); }
          break;
        }
        case "rect": {
          const { x, y, width: w, height: h } = figure.attrs as { x: number; y: number; width: number; height: number };
          const backColor = level !== undefined ? (LEVEL_COLORS[level] ? tokenValue(LEVEL_COLORS[level]) : fill) : (styles.color as string | undefined) ?? fill;
          if (styles.style !== "stroke") { context.fillStyle = withAlpha(backColor, level !== undefined ? 0.12 : opacity); context.fillRect(x, y, w, h); }
          if (level === undefined) context.strokeRect(x, y, w, h);
          break;
        }
        case "circle": {
          const { x, y, r } = figure.attrs as { x: number; y: number; r: number };
          context.beginPath(); context.arc(x, y, r, 0, Math.PI * 2); context.fillStyle = color; context.fill();
          break;
        }
        case "text": {
          const { x, y, text, align, baseline, angle } = figure.attrs as { x: number; y: number; text: string; align?: CanvasTextAlign; baseline?: CanvasTextBaseline; angle?: number };
          context.font = `${style.fontStyle === "italic" ? "italic " : ""}${style.fontWeight ?? 400} ${style.fontSize ?? 12}px ${this.colors.font}`;
          context.fillStyle = (styles.color as string | undefined) ?? style.textColor ?? stroke;
          context.textAlign = align ?? "left"; context.textBaseline = baseline ?? "bottom";
          // A line's text turns with the line about its anchor.
          context.save(); context.translate(x, y); context.rotate(angle ?? 0);
          context.fillText(text, 0, 0);
          context.restore();
          break;
        }
      }
    }
    context.setLineDash([]);
    const focused = drawing.id === this.focus.selected || drawing.id === this.focus.hovered;
    if (!focused) return;
    for (const handle of handles) {
      const hot = drawing.id === this.focus.hovered && this.focus.hoveredHandle === handle.id;
      context.beginPath(); context.arc(handle.point.x, handle.point.y, hot ? 6 : 5, 0, Math.PI * 2);
      context.fillStyle = hot ? this.colors.accent : this.colors.handleFill; context.fill();
      context.lineWidth = 1.5; context.strokeStyle = this.colors.accent; context.stroke();
    }
  }
}
