/* One chart on Lightweight Charts. This folder is the only code that touches the engine.
   - Times are real UTC seconds everywhere; the display zone lives in the horizontal scale.
   - The view reads the bars it is given in place: appends and the forming bar are applied at most once per frame.
   - The engine's visible series hold only a window of the bars around the view (from `firstRow`); the bars before it
     stay on the time scale as whitespace, so logical indexes are bar indexes everywhere. Lightweight Charts walks every
     row of a series on each paint after a data change, so 8 live charts of 10,000 bars would cost ~100 ms a tick.
   - Following the newest bar and wheel zoom anchored on it are done here (the engine anchors zoom at the cursor and stops
     following once the right end is whitespace, engine spike pitfalls 5 and 6). */
import {
  createChartEx, createTextWatermark, CrosshairMode, PriceScaleMode, LineSeries, LineStyle,
  type ITextWatermarkPluginApi, type IPriceLine, type AutoscaleInfo,
  type IChartApiBase, type ISeriesApi, type SeriesType, type LogicalRange, type MouseEventParams, type DeepPartial, type ChartOptionsImpl,
} from "lightweight-charts";
import type { Bars } from "./bars";
import { ZonedTimeScale, type TimeScaleZone } from "./timeScale";
import { TimeMap, upperBound, type FutureBars } from "./timeMap";
import { baselineValue, createMainSeries, isBaseline, mainPoint, type ChartType, type MainSeries } from "./mainSeries";
import { ScaleUnit, seriesFormat, type PriceFormat } from "./precision";
import type { ChartColors } from "./theme";
import { tokenValue } from "@talyx/sdk";
import { DrawingLayer } from "./drawingLayer";
import { countdownText } from "./countdown";
import { PriceLabels, lastValueLabel, type PriceLabel, type SeriesLabels } from "./priceLabels";
import { PaneLayout } from "./paneLayout";
import type { DrawingSurface, PaneHit } from "../drawings/controller";
import type { GeometryBar } from "../drawings/types";

export type ScaleMode = "regular" | "log" | "percent" | "indexed";
export type ScaleSide = "left" | "right";
/** In Percent and Indexed to 100 the price pane's indicator outputs go on this overlay scale, drawn in prices with the
    main scale's mapping, so they are measured from the main series' value as on TradingView (Lightweight Charts measures
    every series from its own first value). Comparisons stay on the main scale, measured from their own. */
export const MEASURED_SCALE = "measured";
export interface AlertLook { color?: string; width?: number; style?: "solid" | "dashed" | "dotted" }
const ALERT_STYLES = { solid: LineStyle.Solid, dashed: LineStyle.Dashed, dotted: LineStyle.Dotted } as const;
const MODES: Record<ScaleMode, PriceScaleMode> = { regular: PriceScaleMode.Normal, log: PriceScaleMode.Logarithmic, percent: PriceScaleMode.Percentage, indexed: PriceScaleMode.IndexedTo100 };

export interface ChartViewOptions {
  zone: TimeScaleZone;
  type: ChartType;
  format: PriceFormat;
  colors: ChartColors;
  magnet: boolean;
  /** The box of the bars (a brick type's), null when they have none. */
  box: number | null;
  panePositions?(tops: Map<string, number>): void;
  snapshot?: boolean;
}

export interface CrosshairAt { index: number | null; time: number | null; x: number; y: number; paneIndex: number }

/** Future whitespace kept ahead of the newest bar so the crosshair and ticks reach into the future. */
const FUTURE_AHEAD = 500;
/** Empty bars right of the newest one (TradingView's right margin). */
const RIGHT_MARGIN = 10;

/** Where the view is, kept across a new window: following the newest bar (`right` bars of margin), else from a time. */
type TimeWindow = { following: boolean; right: number; width: number; from: number | null };
/** Bars the series hold to the left of the view: at least this many, or the view's own width when wider. The window
    moves when the view comes within one margin of its start or is more than four margins past it. */
const WINDOW_MARGIN = 1000;

/** One observer sizes every chart, on the next frame: resizing a canvas inside the observer's callback (the engine's
    own autoSize) changes layout under it and the browser reports a resize loop. */
const sizing = new Map<Element, ChartView>();
let sizes = new Map<ChartView, { width: number; height: number }>();
let sizeFrame = 0;
let resizer: ResizeObserver | null = null;
function observeSize(element: Element, view: ChartView): void {
  resizer ??= new ResizeObserver(entries => {
    for (const entry of entries) { const owner = sizing.get(entry.target); if (owner) sizes.set(owner, entry.contentRect); }
    if (sizeFrame) return;
    sizeFrame = requestAnimationFrame(() => {
      sizeFrame = 0;
      const due = sizes; sizes = new Map();
      for (const [owner, size] of due) owner.chart.resize(Math.floor(size.width), Math.floor(size.height));
    });
  });
  sizing.set(element, view);
  resizer.observe(element);
}
function unobserveSize(element: Element, view: ChartView): void {
  sizing.delete(element); sizes.delete(view);
  resizer?.unobserve(element);
}

export class ChartView implements DrawingSurface {
  readonly chart: IChartApiBase<number>;
  readonly scale: ZonedTimeScale;
  main: MainSeries;
  private readonly units = new Map<string, ScaleUnit>();
  private readonly whitespace: ISeriesApi<"Line", number>;
  private bars: Bars;
  private future: FutureBars = { kind: "continuous", step: 60 };
  timeMap: TimeMap;
  private type: ChartType;
  private barColor: ((index: number) => string | null) | null = null;
  private applied = 0;
  /** First bar index the visible series hold; earlier bars are whitespace on the time scale. */
  firstRow = 0;
  private readonly windowListeners = new Set<() => void>();
  private frame = 0;
  private futureFrom = Number.NaN;
  private readonly frameTasks = new Set<() => void>();
  private readonly crosshairListeners = new Set<(at: CrosshairAt | null) => void>();
  private readonly readoutListeners = new Set<(index: number | null) => void>();
  private readonly rangeListeners = new Set<(range: LogicalRange | null) => void>();
  /** A new window is being put on the scale. Lightweight Charts moves the scale at its next paint, so until then the
      ranges it reports are old indexes over the new bars: they are not the user's and are not passed on, and a window
      that comes meanwhile takes the place asked for (`keep`, or the newest bar), not the one shown. */
  private placing: { keep: TimeWindow | null; frame: number } | null = null;
  private drawingLayers: { layer: DrawingLayer; host: ISeriesApi<SeriesType, number> | null }[] = [];
  private paneIds: string[] = ["price"];
  get pricePane(): number { return this.paneIds.indexOf("price"); }
  private readonly paneLayout: PaneLayout;
  private geometry: { count: number; close: number; bars: GeometryBar[] } | null = null;
  /** Every label on the price scales; the main series' labels are its last price with the countdown, and the alert lines. */
  readonly labels: PriceLabels;
  private mainLabels: SeriesLabels;
  private closeTime: number | null = null;
  private watermark: ITextWatermarkPluginApi<number> | null = null;
  private lastPrice = { label: true, line: true };
  /** The side of the price scale the main series and the price pane's indicators are on. */
  side: ScaleSide = "right";
  /** Value of one price unit for position readouts, when the instrument says it. */
  pointValue: number | null = null;

  constructor(readonly element: HTMLElement, private options: ChartViewOptions, bars: Bars) {
    this.paneLayout = new PaneLayout(element, tops => this.options.panePositions?.(tops));
    const { colors } = options;
    this.scale = new ZonedTimeScale(options.zone);
    this.chart = createChartEx<number, ZonedTimeScale>(element, this.scale, {
      width: element.clientWidth, height: element.clientHeight,
      layout: {
        background: { color: colors.background }, textColor: colors.text, fontSize: colors.fontSize, fontFamily: tokenValue("--tx-font"), attributionLogo: false,
        panes: { separatorColor: colors.separator, separatorHoverColor: colors.separator, enableResize: true },
      },
      grid: { vertLines: { color: colors.vertGrid }, horzLines: { color: colors.horzGrid } },
      crosshair: {
        mode: options.magnet ? CrosshairMode.MagnetOHLC : CrosshairMode.Normal,
        vertLine: { color: colors.crosshair, labelBackgroundColor: colors.crosshairLabel, style: 2, width: 1 },
        horzLine: { color: colors.crosshair, labelBackgroundColor: colors.crosshairLabel, style: 2, width: 1 },
      },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.1, bottom: 0.08 } },
      leftPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, rightOffset: options.snapshot ? 0 : RIGHT_MARGIN, minBarSpacing: 0.05,
        enableConflation: false, shiftVisibleRangeOnNewBar: false, fixLeftEdge: true,
        fixRightEdge: !!options.snapshot, lockVisibleTimeRangeOnResize: !!options.snapshot },
      kineticScroll: { mouse: true, touch: true },
      handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: true },
    } as DeepPartial<ChartOptionsImpl<number>>);
    // Half the widest time label ("00:00"): a label whose half would pass an end of the axis is left out.
    const measure = document.createElement("canvas").getContext("2d")!;
    measure.font = `${colors.fontSize}px ${colors.font}`;
    const half = measure.measureText("00:00").width / 2;
    this.scale.fits = time => {
      const x = this.chart.timeScale().timeToCoordinate(time);
      return x === null || (x - half >= 0 && x + half <= this.chart.timeScale().width());
    };
    this.type = options.type;
    this.bars = bars;
    this.timeMap = new TimeMap(bars, this.future);
    this.main = createMainSeries(this.chart, this.type, colors, options.format, this.side);
    this.labels = new PriceLabels(colors.fontSize, colors.font, {
      visibleFrom: () => this.chart.timeScale().getVisibleLogicalRange()?.from ?? null,
      side: () => this.side,
      measure: series => series.options().priceScaleId === MEASURED_SCALE ? this.main : series,
    });
    this.main.applyOptions({ autoscaleInfoProvider: this.mainAutoscale });
    this.mainLabels = this.labels.attach(this.main, this.mainLabelsOf, true);
    // On a scale of its own: a price scale formats its labels with its first series, and this one must never be that.
    this.whitespace = this.chart.addSeries(LineSeries, { visible: false, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null, priceScaleId: "whitespace" });
    this.chart.panes()[0].setStretchFactor(3);
    this.chart.subscribeCrosshairMove(this.crosshair);
    this.chart.timeScale().subscribeVisibleLogicalRangeChange(this.range);
    this.chart.timeScale().subscribeVisibleLogicalRangeChange(this.followWindow);
    element.addEventListener("wheel", this.wheel, { capture: true, passive: false });
    observeSize(element, this);
    element.tabIndex = -1;
    element.style.outline = "none";
    this.setBars(bars, options.box);
    this.attachDrawingLayers();
  }

  dispose(): void {
    this.paneLayout.dispose();
    cancelAnimationFrame(this.frame);
    if (this.placing) cancelAnimationFrame(this.placing.frame);
    unobserveSize(this.element, this);
    this.element.removeEventListener("wheel", this.wheel, { capture: true });
    this.chart.unsubscribeCrosshairMove(this.crosshair);
    this.chart.timeScale().unsubscribeVisibleLogicalRangeChange(this.range);
    this.chart.timeScale().unsubscribeVisibleLogicalRangeChange(this.followWindow);
    this.chart.remove();
  }

  // ── data ──────────────────────────────────────────────────────────────

  /** A whole new window of bars (open, replace, scrolled back) and their box. The view keeps its place in time. */
  setBars(bars: Bars, box: number | null): void {
    const keep = this.keptWindow();
    if (this.placing) cancelAnimationFrame(this.placing.frame);
    // An empty window (another series loading) has no place of its own: the next one opens at the newest bar.
    const placing = { keep: bars.time.length ? keep : null, frame: 0 };
    this.placing = placing;
    this.bars = bars;
    this.options = { ...this.options, box };
    this.timeMap = new TimeMap(bars, this.future);
    const last = bars.time.length - 1;
    const from = !keep ? last - 200 : keep.following || keep.from === null ? last - keep.right - keep.width : this.timeMap.toLogical(keep.from) ?? 0;
    this.firstRow = Math.max(0, Math.floor(from - Math.max(WINDOW_MARGIN, keep?.width ?? 0) * 2));
    this.applied = bars.time.length;
    this.main.setData(this.allPoints());
    if (isBaseline(this.type) && bars.time.length) this.main.applyOptions({ baseValue: { type: "price", price: baselineValue(bars) } });
    this.futureFrom = Number.NaN;
    this.extendFuture();
    // The first bars of a window open at the newest bar; later windows keep the user's place in time. Not animated: a
    // window that comes during the animation would take its passing place for the user's.
    if (keep) this.restoreWindow(keep);
    else if (bars.time.length) this.chart.timeScale().scrollToPosition(RIGHT_MARGIN, false);
    // Lightweight Charts asked for its paint during the calls above, so this frame comes after it.
    placing.frame = requestAnimationFrame(() => { this.placing = null; this.range(this.chart.timeScale().getVisibleLogicalRange()); });
  }

  /** The bars object grew or its last bar changed in place; drawn on the next frame. */
  barsChanged(): void { this.schedule(this.flushBars); }

  /** What follows the loaded bars (Market's bar times, or a fixed step). */
  setFuture(future: FutureBars): void {
    this.future = future;
    this.timeMap = new TimeMap(this.bars, future);
    this.futureFrom = Number.NaN;
    this.extendFuture();
  }

  fitSnapshot(): void {
    if (this.bars.time.length === 0) return;
    const scale = this.chart.timeScale();
    scale.applyOptions({ minBarSpacing: Math.min(0.05, Math.max(1, this.element.clientWidth / 2) / this.bars.time.length) });
    scale.fitContent();
  }

  private point(index: number) {
    const point = mainPoint(this.type, this.bars, index, this.options.colors, this.options.box);
    const color = this.barColor?.(index);
    return color ? { ...point, color, wickColor: color, borderColor: color } : point;
  }

  /** The series' points are the applied rows: bars that came since are the next flush's (setting them here would put
      the series ahead of what the flush updates from). */
  private allPoints() { const points = []; for (let index = this.firstRow; index < this.applied; index++) points.push(this.point(index)); return points; }

  /** The unit the series on this series' price scale share (precision.ts). */
  scaleUnit(series: ISeriesApi<SeriesType, number>): ScaleUnit {
    const key = `${series.getPane().paneIndex()}/${series.options().priceScaleId}`;
    let unit = this.units.get(key);
    if (!unit) { unit = new ScaleUnit(); this.units.set(key, unit); }
    return unit;
  }

  /** The series window follows the view (see the header): set again from a new first row, the indicators with it. */
  private readonly followWindow = (range: LogicalRange | null) => {
    if (!range || this.applied === 0) return;
    const margin = Math.max(WINDOW_MARGIN, range.to - range.from), ahead = range.from - this.firstRow;
    if ((ahead >= margin || this.firstRow === 0) && ahead <= 4 * margin) return;
    this.schedule(this.moveWindow);
  };

  private readonly moveWindow = () => {
    const range = this.chart.timeScale().getVisibleLogicalRange();
    if (!range) return;
    const first = Math.max(0, Math.floor(range.from - 2 * Math.max(WINDOW_MARGIN, range.to - range.from)));
    if (first === this.firstRow) return;
    // Every time stays on the scale throughout (else the view would slide): the whitespace grows before the series give
    // up their first rows, and shrinks after they took them.
    const grow = first > this.firstRow;
    this.firstRow = first;
    const whitespace = () => { this.futureFrom = Number.NaN; this.extendFuture(); };
    if (grow) whitespace();
    this.main.setData(this.allPoints());
    for (const listener of this.windowListeners) listener();
    if (!grow) whitespace();
  };

  /** The series window moved: series that follow the bars set their data again from `firstRow`. */
  onWindow(listener: () => void): () => void { this.windowListeners.add(listener); return () => this.windowListeners.delete(listener); }

  private readonly flushBars = () => {
    const bars = this.bars, count = bars.time.length;
    if (count === 0) return;
    // More than the forming bar and the next: the rest of a window sent in pieces (a replace of its first rows, then
    // appends), or bars missed while the source was away. Set as one window, which keeps the place the replace asked for.
    if (count - this.applied > 1) {
      this.setBars(bars, this.options.box);
      for (const listener of this.windowListeners) listener();
      return;
    }
    const following = this.isFollowing();
    const before = this.applied;
    for (let index = Math.max(this.firstRow, this.applied - 1); index < count; index++) this.main.update(this.point(index));
    this.applied = count;
    if (count > before) {
      this.extendFuture();
      if (following) this.shift(count - before);
    }
  };

  /** Whitespace over the bars before the window and the next future bar times; rebuilt from the newest bar so stale
      points never add positions. */
  private extendFuture(): void {
    const last = this.bars.time[this.bars.time.length - 1];
    if (last === undefined || last === this.futureFrom) return;
    this.futureFrom = last;
    const times: { time: number }[] = [];
    for (let index = 0; index < this.firstRow; index++) times.push({ time: this.bars.time[index] });
    if (!this.options.snapshot) {
      if (this.future.kind === "continuous") for (let step = 1; step <= FUTURE_AHEAD; step++) times.push({ time: last + step * this.future.step });
      else for (let index = upperBound(this.future.open, last), end = times.length + FUTURE_AHEAD; index < this.future.open.length && times.length < end; index++) times.push({ time: this.future.open[index] });
    }
    this.whitespace.setData(times);
  }

  private isFollowing(): boolean {
    const range = this.chart.timeScale().getVisibleLogicalRange();
    return range !== null && range.to >= this.applied - 1;
  }

  private shift(bars: number): void {
    const range = this.chart.timeScale().getVisibleLogicalRange();
    if (range) this.chart.timeScale().setVisibleLogicalRange({ from: range.from + bars, to: range.to + bars });
  }

  /** The place to keep across a change of the series: the one a window being placed asked for, else the view's. */
  private keptWindow(): TimeWindow | null {
    return this.placing ? this.placing.keep : this.applied > 0 ? this.timeWindow() : null;
  }

  private timeWindow(): TimeWindow | null {
    const range = this.chart.timeScale().getVisibleLogicalRange();
    if (!range) return null;
    const right = this.applied - 1 - range.to;
    return { following: this.isFollowing(), right, width: range.to - range.from, from: this.timeMap.toTime(range.from) };
  }

  private restoreWindow(keep: TimeWindow): void {
    const last = this.bars.time.length - 1;
    const from = keep.following || keep.from === null ? last - keep.right - keep.width : this.timeMap.toLogical(keep.from);
    if (from === null) return;
    this.chart.timeScale().setVisibleLogicalRange({ from, to: from + keep.width });
  }

  // ── look ──────────────────────────────────────────────────────────────

  setType(type: ChartType, format: PriceFormat): void {
    if (type === this.type) return;
    this.type = type;
    this.replaceMain(format);
  }

  /** Chart settings › Symbol and Canvas changed colours: the canvas and the main series take them in place
      (the view keeps its range). What hangs on the main series must be attached again by its owners. */
  restyle(colors: ChartColors): void {
    this.options = { ...this.options, colors };
    this.chart.applyOptions({ layout: { background: { color: colors.background } }, grid: { vertLines: { color: colors.vertGrid }, horzLines: { color: colors.horzGrid } } });
    this.replaceMain(this.options.format);
  }

  /** The view keeps its place in time: while the old series is gone its bars leave the time scale, and the engine keeps
      the logical range over what is left, which puts the view on other bars. */
  private replaceMain(format: PriceFormat): void {
    const type = this.type, keep = this.keptWindow();
    const drawing = this.drawingLayers[this.pricePane];
    drawing?.host?.detachPrimitive(drawing.layer);
    if (drawing) drawing.host = null;
    this.removeSeries(this.main);
    this.main = createMainSeries(this.chart, type, this.options.colors, format, this.side);
    if (this.pricePane !== 0) this.main.moveToPane(this.pricePane);
    this.main.applyOptions({ priceLineVisible: this.lastPrice.line, autoscaleInfoProvider: this.mainAutoscale });
    this.mainLabels = this.labels.attach(this.main, this.mainLabelsOf, true);
    this.main.setData(this.allPoints());
    if (keep) this.restoreWindow(keep);
    // Price lines went with the old series.
    this.alertLines = [];
    this.setAlertLines(this.alertPrices);
    this.attachDrawingLayers();
    if (isBaseline(type) && this.bars.time.length) this.main.applyOptions({ baseValue: { type: "price", price: baselineValue(this.bars) } });
  }

  setFormat(format: PriceFormat): void {
    this.options = { ...this.options, format };
    this.main.applyOptions({ priceFormat: seriesFormat(format) });
  }

  /** Pine `barcolor`: a colour per bar index, or none. */
  setBarColors(color: ((index: number) => string | null) | null): void {
    if (color === null && this.barColor === null) return;
    this.barColor = color;
    this.main.setData(this.allPoints());
  }

  /** Bars from `start` drawn again (their colours changed). */
  repaintBars(start: number): void {
    for (let index = Math.max(this.firstRow, start); index < this.applied; index++) this.main.update(this.point(index));
  }

  barCount(): number { return this.applied; }

  /** The document's panes, the price pane first: kept even while empty so indexes stay put; a collapsed pane keeps a sliver. */
  layoutPanes(panes: readonly { id: string; height: number; collapsed: boolean }[]): void {
    this.paneIds = panes.map(pane => pane.id);
    while (this.chart.panes().length < panes.length) {
      // A new pane's scales start from the chart's options, where the main scale's mode, inversion and fit also went:
      // TradingView changes those on the main pane only.
      const index = this.chart.addPane(true).paneIndex();
      for (const side of ["left", "right"]) this.chart.priceScale(side, index).applyOptions({ mode: PriceScaleMode.Normal, invertScale: false, autoScale: true });
    }
    const price = this.main.getPane();
    // swapPanes accepts newly allocated panes before their first paint.
    if (price.paneIndex() !== this.pricePane) this.chart.swapPanes(price.paneIndex(), this.pricePane);
    while (this.chart.panes().length > panes.length) this.chart.removePane(this.chart.panes().length - 1);
    this.chart.panes().forEach((pane, index) => { pane.setPreserveEmptyPane(true); pane.setStretchFactor(panes[index].collapsed ? 0.05 : panes[index].height); });
    this.schedule(this.observePanes);
  }

  /** A logical position's time: the bar's own inside the window, the time map's past it (null past the calendar). */
  timeAtLogical(logical: number): number | null {
    return logical >= 0 && logical < this.bars.time.length ? this.bars.time[logical] : this.timeMap.toTime(logical);
  }

  get font(): string { return this.options.colors.font; }

  // ── drawings ──────────────────────────────────────────────────────────

  /** One drawing layer per pane, on the price series or the first series of an indicator pane; call after panes change. */
  attachDrawingLayers(): void {
    const panes = this.chart.panes();
    while (this.drawingLayers.length < panes.length) this.drawingLayers.push({ layer: this.newLayer(), host: null });
    while (this.drawingLayers.length > panes.length) { const gone = this.drawingLayers.pop()!; gone.host?.detachPrimitive(gone.layer); }
    panes.forEach((pane, index) => {
      const entry = this.drawingLayers[index];
      const host = index === this.pricePane ? this.main : pane.getSeries().find(series => series !== this.whitespace) ?? null;
      if (host === entry.host) return;
      entry.host?.detachPrimitive(entry.layer);
      host?.attachPrimitive(entry.layer);
      entry.host = host;
    });
  }

  private newLayer(): DrawingLayer {
    const view = this;
    return new DrawingLayer({
      timeMap: () => this.timeMap, geometryBars: () => this.geometryBars(), colors: this.options.colors,
      formatTime: time => this.scale.formatHorzItem(this.scale.convertHorzItemToInternal(time)),
      formatPrice: price => this.main.priceFormatter().format(price),
      get pointValue() { return view.pointValue; },
    });
  }

  /** Bars as objects for drawings that read them; rebuilt only when the bars changed. */
  private geometryBars(): GeometryBar[] {
    const bars = this.bars, count = bars.time.length, close = bars.close[count - 1];
    if (this.geometry && this.geometry.count === count && this.geometry.close === close) return this.geometry.bars;
    const list = bars.time.map((time, index) => ({ time, open: bars.open[index], high: bars.high[index], low: bars.low[index], close: bars.close[index], volume: bars.volume[index] }));
    this.geometry = { count, close, bars: list };
    return list;
  }

  layer(pane: number): DrawingLayer | null { return this.drawingLayers[pane]?.host ? this.drawingLayers[pane].layer : null; }
  layers(): DrawingLayer[] { return this.drawingLayers.filter(entry => entry.host).map(entry => entry.layer); }

  /** The price at a height in the price pane. */
  priceAt(y: number): number | null { return this.main.coordinateToPrice(y); }
  /** The right price scale's width (the "+" sits just inside it). */
  axisWidth(): number { return this.chart.priceScale(this.side).width(); }
  /** Whether a client x is on the main price scale (its right-click menu). */
  onPriceScale(clientX: number): boolean {
    const rect = this.element.getBoundingClientRect(), width = this.axisWidth();
    return this.side === "right" ? clientX >= rect.right - width : clientX <= rect.left + width;
  }
  /** The time of the bar under a pane x (replay picks its start this way). */
  timeAtX(x: number): number | null {
    const logical = this.chart.timeScale().coordinateToLogical(x);
    return logical === null ? null : this.timeAtLogical(Math.round(logical));
  }

  paneAt(clientX: number, clientY: number): PaneHit | null {
    const panes = this.chart.panes();
    for (let index = 0; index < panes.length; index++) {
      const hit = this.local(index, clientX, clientY);
      if (hit.y >= 0 && hit.y <= panes[index].getHeight() && hit.x >= 0 && hit.x <= this.chart.timeScale().width()) return hit;
    }
    return null;
  }

  local(pane: number, clientX: number, clientY: number): PaneHit {
    const rect = this.chart.panes()[pane].getHTMLElement()!.getBoundingClientRect();
    return { pane, paneId: this.paneIds[pane], x: clientX - rect.left - this.chart.priceScale("left").width(), y: clientY - rect.top };
  }

  private readonly observePanes = () => {
    this.paneLayout.set(new Map(this.chart.panes().flatMap((pane, index) => {
      const element = pane.getHTMLElement();
      return element ? [[this.paneIds[index], element] as const] : [];
    })));
  };

  setMagnet(on: boolean): void { this.chart.applyOptions({ crosshair: { mode: on ? CrosshairMode.MagnetOHLC : CrosshairMode.Normal } }); }

  /** The main price scale: its mode, auto fit, inversion and side (moving it moves every pane's scale; indicators follow). */
  setScale(change: { mode?: ScaleMode; auto?: boolean; invert?: boolean; side?: ScaleSide }): void {
    if (change.side && change.side !== this.side) {
      this.side = change.side;
      const margins = { scaleMargins: { top: 0.1, bottom: 0.08 } };
      this.chart.applyOptions({ leftPriceScale: { visible: this.side === "left", ...margins }, rightPriceScale: { visible: this.side === "right", ...margins } });
      this.main.applyOptions({ priceScaleId: this.side });
    }
    if (change.mode) this.measured = change.mode === "percent" || change.mode === "indexed";
    this.main.priceScale().applyOptions({
      ...(change.mode ? { mode: MODES[change.mode] } : {}), ...(change.auto === undefined ? {} : { autoScale: change.auto }),
      ...(change.invert === undefined ? {} : { invertScale: change.invert }),
    });
    this.measuredScale();
  }

  private measured = false;
  /** The series on the measured scale and their own fits. */
  private readonly measuredFits = new Map<ISeriesApi<SeriesType, number>, AutoscaleInfo | null>();

  /** The scale the price pane's indicator outputs go on. */
  indicatorScale(): string { return this.measured ? MEASURED_SCALE : this.side; }

  /** A series put on the measured scale: the scale takes the main scale's price range (no margins, the same inversion),
      so a price is at the same height on both; the series' own fit goes into the main scale's. */
  measure(series: ISeriesApi<SeriesType, number>): void {
    this.measuredFits.set(series, null);
    series.applyOptions({ autoscaleInfoProvider: (own: () => AutoscaleInfo | null) => {
      this.measuredFits.set(series, own());
      const height = this.main.getPane().getHeight(), top = this.main.coordinateToPrice(0), bottom = this.main.coordinateToPrice(height);
      return top === null || bottom === null ? null : { priceRange: { minValue: Math.min(top, bottom), maxValue: Math.max(top, bottom) } };
    } });
    this.measuredScale();
  }

  /** Every series leaves the chart through here: the labels and the measured scale know their series. */
  removeSeries(series: ISeriesApi<SeriesType, number>): void {
    this.labels.forget(series);
    this.measuredFits.delete(series);
    this.chart.removeSeries(series);
  }

  private measuredScale(): void {
    if (!this.measuredFits.size) return;
    this.chart.priceScale(MEASURED_SCALE, this.pricePane).applyOptions({ scaleMargins: { top: 0, bottom: 0 }, invertScale: this.main.priceScale().options().invertScale });
  }

  /** The main scale fits the measured indicator outputs too, as it fits them on its own scale otherwise. Their fits are
      from the last frame (the main scale is fitted before overlays), which no one sees. */
  private readonly mainAutoscale = (own: () => AutoscaleInfo | null): AutoscaleInfo | null => {
    const base = own();
    if (!this.measured) return base;
    let range = base?.priceRange ?? null;
    for (const [series, own] of this.measuredFits) {
      const fit = series.options().visible ? own?.priceRange : null;
      if (fit) range = range ? { minValue: Math.min(range.minValue, fit.minValue), maxValue: Math.max(range.maxValue, fit.maxValue) } : fit;
    }
    return range ? { ...base, priceRange: range } : base;
  };

  autoScale(): boolean { return this.main.priceScale().options().autoScale; }

  /** TradingView's Reset chart view: newest bars at the default spacing, price scale back on auto. */
  resetView(): void {
    this.chart.timeScale().applyOptions({ barSpacing: 6, rightOffset: RIGHT_MARGIN });
    this.chart.timeScale().scrollToPosition(RIGHT_MARGIN, false);
    this.setScale({ auto: true });
  }

  // ── view and pointer ──────────────────────────────────────────────────

  /** Wheel zoom keeps the right edge where it is in bars (the newest bar and the right margin when following, the last
      visible bar when scrolled back); sideways wheel scrolls. */
  private readonly wheel = (event: WheelEvent) => {
    event.preventDefault(); event.stopPropagation();
    const time = this.chart.timeScale(), range = time.getVisibleLogicalRange();
    if (!range) return;
    const spacing = time.options().barSpacing, width = time.width();
    if (Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
      const bars = event.deltaX / spacing;
      time.setVisibleLogicalRange({ from: range.from + bars, to: range.to + bars });
      return;
    }
    const factor = Math.exp(-event.deltaY * (event.deltaMode === 1 ? 0.05 : 0.0015));
    // Zooming out stops once every bar and the right margin fit (TradingView); then the left edge is the first bar.
    const fit = (width - 1) / Math.max(1, this.applied + RIGHT_MARGIN);
    const span = (width - 1) / Math.max(Math.min(spacing, fit), 0.05, Math.min(60, spacing * factor));
    const to = Math.max(range.to, span - 0.5);
    time.setVisibleLogicalRange({ from: to - span, to });
  };

  private readonly crosshair = (param: MouseEventParams<number>) => {
    const at = param.point && param.logical !== undefined
      ? { index: param.logical >= 0 && param.logical < this.applied ? Math.round(param.logical) : null, time: param.time ?? null, x: param.point.x, y: param.point.y, paneIndex: param.paneIndex ?? 0 }
      : null;
    for (const listener of this.crosshairListeners) listener(at);
    for (const listener of this.readoutListeners) listener(at?.index ?? null);
  };

  private readonly range = (range: LogicalRange | null) => { if (!this.placing) for (const listener of this.rangeListeners) listener(range); };

  onCrosshair(listener: (at: CrosshairAt | null) => void): () => void { this.crosshairListeners.add(listener); return () => this.crosshairListeners.delete(listener); }
  /** Includes programmatic crosshairs, which never emit a pointer move from Lightweight Charts. */
  onReadout(listener: (index: number | null) => void): () => void { this.readoutListeners.add(listener); return () => this.readoutListeners.delete(listener); }
  onVisibleRange(listener: (range: LogicalRange | null) => void): () => void { this.rangeListeners.add(listener); return () => this.rangeListeners.delete(listener); }

  /** Another chart's crosshair, matched by time to the last bar at or before it. */
  showCrosshairAt(time: number | null): void {
    const index = time === null ? -1 : upperBound(this.bars.time, time) - 1;
    if (index < 0) this.chart.clearCrosshairPosition();
    else this.chart.setCrosshairPosition(this.bars.close[index], this.bars.time[index], this.main);
    for (const listener of this.readoutListeners) listener(index < 0 ? null : index);
  }

  visibleRange(): LogicalRange | null { return this.chart.timeScale().getVisibleLogicalRange(); }

  /** The first and last visible bars (indexes), and whether the view follows the newest bar. Counted on the bars applied
      to the engine: a new bar is in the data a frame before the view follows it. */
  visibleBars(): { from: number; to: number; following: boolean } | null {
    const range = this.chart.timeScale().getVisibleLogicalRange(), last = this.applied - 1;
    if (!range || last < 0) return null;
    const from = Math.min(last, Math.max(0, Math.ceil(range.from)));
    return { from, to: Math.max(from, Math.min(last, Math.floor(range.to))), following: this.isFollowing() };
  }

  /** Show the bars between two times (time range buttons, go to date). */
  showTimes(from: number, to: number): void {
    const start = this.timeMap.toLogical(from), end = this.timeMap.toLogical(to);
    if (start === null || end === null) return;
    this.chart.timeScale().setVisibleLogicalRange({ from: start, to: end + 2 });
  }

  /** Puts a time in the middle of the view, keeping how many bars are shown. */
  centerOn(time: number): void {
    const at = this.timeMap.toLogical(time), range = this.chart.timeScale().getVisibleLogicalRange();
    if (at === null || !range) return;
    const half = (range.to - range.from) / 2;
    this.chart.timeScale().setVisibleLogicalRange({ from: at - half, to: at + half });
  }

  /** The prices active alerts on this symbol watch (TradingView draws each as a dashed line with its label on the scale). */
  setAlertLines(prices: number[]): void {
    this.alertPrices = prices;
    for (const line of this.alertLines) this.main.removePriceLine(line);
    const { color, width, style } = this.alertLook;
    this.alertLines = prices.map(price => this.main.createPriceLine({
      price, color: color ?? this.options.colors.accent, lineWidth: (width ?? 1) as 1, lineStyle: ALERT_STYLES[style ?? "dashed"], axisLabelVisible: false, title: "Alert",
    }));
  }

  /** Chart settings › Alerts: the alert lines' colour, width and style. */
  setAlertLook(look: AlertLook): void {
    if (JSON.stringify(look) === JSON.stringify(this.alertLook)) return;
    this.alertLook = look;
    this.setAlertLines(this.alertPrices);
  }
  private alertLook: AlertLook = {};
  private alertPrices: number[] = [];
  private alertLines: IPriceLine[] = [];

  /** Chart settings › Scales and lines: the main series' last price label on the scale and its line across the chart. */
  setLastPrice(label: boolean, line: boolean): void {
    this.lastPrice = { label, line };
    this.main.applyOptions({ priceLineVisible: line });
    this.mainLabels.update();
  }

  private readonly mainLabelsOf = (series: ISeriesApi<SeriesType, number>): PriceLabel[] => {
    const last = this.lastPrice.label ? lastValueLabel(series) : [], closeTime = this.closeTime;
    if (last.length && closeTime !== null && closeTime > Date.now()) last[0].under = { text: countdownText(closeTime - Date.now()) };
    const alert = this.alertLook.color ?? this.options.colors.accent;
    return [...last, ...this.alertPrices.map(price => ({ price, color: alert }))];
  };

  /** The symbol and interval drawn faintly in the middle of the price pane (Canvas › Watermark), or none. */
  setWatermark(text: string | null): void {
    if (text === null) { this.watermark?.detach(); this.watermark = null; return; }
    const options = { horzAlign: "center" as const, vertAlign: "center" as const, lines: [{ text, color: this.options.colors.watermark, fontSize: 48, fontFamily: tokenValue("--tx-font") }] };
    if (this.watermark) this.watermark.applyOptions(options);
    else this.watermark = createTextWatermark(this.main.getPane(), options);
  }

  /** The countdown under the last price label to this close time (the forming bar's), or none. Called every second. */
  setCountdown(closeTime: number | null): void {
    this.closeTime = closeTime;
    this.mainLabels.update();
  }

  screenshot(): HTMLCanvasElement { return this.chart.takeScreenshot(true); }

  /** Run a task on the next frame, once however often it is asked for. */
  schedule(task: () => void): void {
    this.frameTasks.add(task);
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      const tasks = [...this.frameTasks]; this.frameTasks.clear();
      for (const run of tasks) run();
    });
  }
}
