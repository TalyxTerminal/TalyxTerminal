/* One indicator instance on a chart: PyneCore's plot-family meta turned into engine series.
   plot → line / histogram / area series (per-point colours when dynamic); hline → price line; fill → BandFill;
   shape, char, arrow → series markers; bgcolor → BarBackground; barcolor → the main series' bar colours;
   candle, bar → candlestick / bar series; drawings → PineDrawings. Rows line up one to one with the chart's bars;
   an `offset` moves a plot that many bars, into the future through the time map. */
import {
  AreaSeries, BarSeries, CandlestickSeries, HistogramSeries, LineSeries, LineStyle, LineType, createSeriesMarkers,
  type AutoscaleInfo, type IPriceLine, type ISeriesApi, type ISeriesMarkersPluginApi, type SeriesMarker, type SeriesType,
} from "lightweight-charts";
import { tokenValue } from "@talyx/sdk";
import { MEASURED_SCALE, type ChartView } from "./ChartView";
import { VOLUME_FORMAT, seriesFormat, unitFormat, type PriceFormat } from "./precision";
import type { PlotStyle } from "../wire";
import { BandFill, BarBackground, PineDrawings, pineColor, type PineDrawingSet } from "./pinePrimitives";

export interface PineMeta {
  id: string; kind: "plot" | "shape" | "char" | "arrow" | "candle" | "bar" | "bgcolor" | "barcolor" | "hline" | "fill";
  title?: string; display?: string; force_overlay?: boolean; offset?: number; show_last?: number; precision?: number; format?: string;
  style?: string; color?: string; linewidth?: number; trackprice?: boolean; histbase?: number; join?: boolean;
  location?: string; size?: string; text?: string; textcolor?: string; char?: string; colorup?: string; colordown?: string;
  wickcolor?: string; bordercolor?: string; price?: number; linestyle?: string;
  plot1?: string; plot2?: string; hline1?: string; hline2?: string; fillgaps?: boolean; dynamic?: boolean;
}

/** What the chart stream has delivered for one instance, held in place and grown row by row. */
export interface IndicatorData {
  meta: PineMeta[];
  values: Record<string, (number | null)[]>;
  colors: Record<string, unknown[]>;
  drawings: PineDrawingSet | null;
  /** The script's `indicator(behind_chart=…)` (true unless it says otherwise): on the price pane its plots, fills,
      bgcolor and drawings go under the bars. */
  behindChart: boolean;
  /** The script's `indicator(scale=scale.none)`: on the price pane its plots are on a scale of their own that is not
      shown, in the pane's bottom fifth (TradingView's Volume). */
  noScale: boolean;
}

export interface IndicatorPlacement {
  /** Engine pane index. */
  pane: number;
  visible: boolean;
  styles: Record<string, PlotStyle>;
  /** Colours for outputs that name none, from the data palette. */
  palette: string[];
  /** Each plot's last value as a label on the price scale (Chart settings › Scales and lines). */
  labels: boolean;
  /** The symbol's price format: plots on the price pane that name no precision show prices like the bars (TradingView),
      and the price scale reads the same whichever series it takes its format from. */
  format: PriceFormat;
  /** The main price scale's side: every output is on that side (TradingView moves every pane's scale). */
  side: "left" | "right";
  /** The scale the price pane's outputs go on (ChartView.indicatorScale). */
  scale: string;
}

const LINE_STYLES = { solid: LineStyle.Solid, dashed: LineStyle.Dashed, dotted: LineStyle.Dotted } as const;
let ownScales = 0;
/** Past this many rows, a change is set as a whole: a single update walks the whole series (Lightweight Charts 5.3). */
const BULK_ROWS = 8;

interface Output { from(index: number): void; all(): void; dispose(): void; markers?: true }

export class IndicatorView {
  private outputs: Output[] = [];
  private host: ISeriesApi<SeriesType, number> | null = null;
  private markers: ISeriesMarkersPluginApi<number> | null = null;
  private markerRows: SeriesMarker<number>[] = [];
  private drawings: PineDrawings | null = null;
  private background: BarBackground | null = null;
  private hlines: IPriceLine[] = [];
  private applied = 0;
  private stopWindow: (() => void) | null = null;
  /** The hidden scale of a `scale.none` script on the price pane. */
  private readonly ownScale = `own-${++ownScales}`;

  constructor(private readonly view: ChartView, private readonly data: IndicatorData, private readonly placement: IndicatorPlacement) {
    this.build();
  }

  /** Rows from `from` on were added or changed in place (a row can come after the bars moved past it). */
  changed(from: number): void { this.dirtyFrom = Math.min(this.dirtyFrom, from); this.view.schedule(this.flush); }
  private dirtyFrom = Infinity;

  /** The drawing snapshot was replaced. */
  drawingsChanged(): void {
    if (!this.placement.visible) return;
    if (this.data.drawings && this.drawings) this.drawings.replace(this.data.drawings);
    else this.rebuild();
  }

  /** New meta, a replace, or other look: everything again. */
  rebuild(): void { this.dispose(); this.build(); }

  /** What hangs on the host goes first, then the series; the host of an indicator pane is one of the outputs (its first
      plot, or the hidden series added for it), so removing the outputs removes it once. */
  dispose(): void {
    if (this.markers) { this.markers.detach(); this.markers = null; }
    if (this.drawings) { this.attachTarget().detachPrimitive(this.drawings); this.drawings = null; }
    if (this.background) { this.attachTarget().detachPrimitive(this.background); this.background = null; }
    for (const line of this.hlines) this.host?.removePriceLine(line);
    this.hlines = [];
    this.stopWindow?.(); this.stopWindow = null;
    for (const output of this.outputs) output.dispose();
    this.outputs = [];
    this.host = null;
    this.firstSeries = null;
    this.view.setBarColors(null);
    this.markerRows = [];
  }

  private readonly flush = () => {
    const rows = this.view.barCount();
    const start = Math.max(this.view.firstRow, Math.min(this.applied - 1, this.dirtyFrom));
    this.dirtyFrom = Infinity;
    for (const output of this.outputs) output.from(start);
    this.applied = rows;
  };

  private paneOf(meta: PineMeta): number { return meta.force_overlay ? this.view.pricePane : this.placement.pane; }

  private shown(meta: PineMeta): boolean {
    return this.placement.visible && meta.display !== "none" && this.placement.styles[meta.id]?.visible !== false;
  }

  /** Pine's `behind_chart` applies to the price pane only. */
  private behind(pane: number): boolean { return pane === this.view.pricePane && this.data.behindChart; }

  /** A series of the price pane goes just under the bars when the script is behind them. The price scale reads its
      format from the lowest series, so price-pane outputs carry the symbol's format unless they name their own. */
  private placed<T extends ISeriesApi<SeriesType, number>>(series: T, pane: number): T {
    if (this.behind(pane)) series.setSeriesOrder(this.view.main.seriesOrder());
    if (this.scaleOf(pane) === MEASURED_SCALE) this.view.measure(series);
    return series;
  }
  private paneFormat(pane: number) { return { priceScaleId: this.scaleOf(pane), ...(pane === this.view.pricePane ? { priceFormat: seriesFormat(this.placement.format) } : {}) }; }

  private scaleOf(pane: number): string { return pane !== this.view.pricePane ? this.placement.side : this.data.noScale ? this.ownScale : this.placement.scale; }

  /** The unit of the host's scale: the price pane's main scale is in prices, never scaled. */
  private hostUnit(): number { return this.host && this.host !== this.view.main ? this.view.scaleUnit(this.host).value : 1; }

  /** The series the indicator's price lines, markers and drawings hang on in its pane. */
  private attachTarget(): ISeriesApi<SeriesType, number> { return this.host ?? this.view.main; }

  private build(): void {
    const { meta } = this.data;
    const pane = this.placement.pane;
    const hlines = meta.filter(item => item.kind === "hline" && this.shown(item));
    let colorIndex = 0;
    const nextColor = (item: PineMeta) => this.placement.styles[item.id]?.color ?? pineColor(item.color) ?? this.placement.palette[colorIndex++ % this.placement.palette.length];
    for (const item of meta) {
      if (!this.shown(item)) continue;
      if (item.kind === "plot") this.outputs.push(this.plot(item, nextColor(item)));
      else if (item.kind === "candle" || item.kind === "bar") this.outputs.push(this.candles(item, nextColor(item)));
    }
    if (pane === this.view.pricePane) this.host = this.view.main;
    else {
      const first = this.outputs.length ? this.firstSeries : null;
      this.host = first ?? this.view.chart.addSeries(LineSeries, { visible: false, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, priceScaleId: this.placement.side }, pane);
      const extra = () => { const range = this.extraRange(hlines), unit = this.hostUnit(); return range && { min: range.min / unit, max: range.max / unit }; };
      this.host.applyOptions({ autoscaleInfoProvider: (base: () => AutoscaleInfo | null) => widen(base(), extra()) });
      if (!first) this.outputs.push(this.hidden(this.host as ISeriesApi<"Line", number>, hlines[0]?.price ?? 0));
    }
    for (const item of hlines) {
      this.hlines.push(this.host.createPriceLine({
        price: item.price! / this.hostUnit(), color: this.placement.styles[item.id]?.color ?? pineColor(item.color) ?? this.placement.palette[0],
        lineWidth: (item.linewidth ?? 1) as 1, lineStyle: LINE_STYLES[(item.linestyle ?? "dashed") as keyof typeof LINE_STYLES] ?? LineStyle.Dashed,
        axisLabelVisible: false, title: "",
      }));
    }
    for (const item of meta) if (item.kind === "fill" && this.shown(item)) this.outputs.push(this.fill(item));
    const markerMeta = meta.filter(item => (item.kind === "shape" || item.kind === "char" || item.kind === "arrow") && this.shown(item));
    if (markerMeta.length) this.outputs.push(this.markerOutput(markerMeta));
    // Price lines and absolute markers hang on the host in its scale's unit: placed again when it changes (first to go
    // on dispose, before the series leaving the scale can change it).
    if (pane !== this.view.pricePane) {
      const leave = this.view.scaleUnit(this.host).join(this, () => {
        hlines.forEach((item, index) => this.hlines[index].applyOptions({ price: item.price! / this.hostUnit() }));
        this.outputs.find(output => output.markers)?.all();
      });
      this.outputs.unshift({ from: () => undefined, all: () => undefined, dispose: leave });
    }
    const bg = meta.filter(item => item.kind === "bgcolor" && this.shown(item));
    if (bg.length) {
      this.background = new BarBackground(index => this.colorOf(bg, index), () => this.view.barCount());
      this.attachTarget().attachPrimitive(this.background);
      this.outputs.push({ from: () => this.background?.requestUpdate(), all: () => undefined, dispose: () => undefined });
    }
    const bars = meta.filter(item => item.kind === "barcolor" && this.shown(item));
    if (bars.length) this.outputs.push(this.barColors(bars));
    // A hidden indicator draws nothing: its drawings too.
    if (this.data.drawings && this.placement.visible) {
      this.drawings = new PineDrawings(this.data.drawings, () => this.view.timeMap, this.view.font, this.behind(pane));
      this.attachTarget().attachPrimitive(this.drawings);
    }
    for (const output of this.outputs) output.all();
    this.applied = this.view.barCount();
    // The chart's series window moved: the same rows as the bars.
    this.stopWindow = this.view.onWindow(() => { for (const output of this.outputs) output.all(); });
  }

  private firstSeries: ISeriesApi<SeriesType, number> | null = null;

  private plot(item: PineMeta, color: string): Output {
    const look = this.placement.styles[item.id] ?? {};
    const style = item.style ?? "line", pane = this.paneOf(item), offset = item.offset ?? 0;
    const width = (look.lineWidth ?? item.linewidth ?? 1) as 1;
    const lineStyle = LINE_STYLES[look.lineStyle ?? "solid"];
    const format = item.precision !== undefined ? { precision: item.precision, minMove: 10 ** -item.precision } : pane === this.view.pricePane ? this.placement.format : null;
    const common = { lastValueVisible: false, priceLineVisible: item.trackprice === true, crosshairMarkerVisible: false, title: "",
      priceScaleId: this.scaleOf(pane), ...(item.format === "volume" ? { priceFormat: VOLUME_FORMAT } : format ? { priceFormat: seriesFormat(format) } : {}) };
    let series: ISeriesApi<SeriesType, number>;
    if (style === "histogram" || style === "columns") series = this.view.chart.addSeries(HistogramSeries, { ...common, color, base: item.histbase ?? 0 }, pane);
    else if (style === "area" || style === "areabr") series = this.view.chart.addSeries(AreaSeries, { ...common, lineColor: color, topColor: color, bottomColor: tokenValue("--tx-clear"), lineWidth: width, lineStyle }, pane);
    else series = this.view.chart.addSeries(LineSeries, {
      ...common, color, lineWidth: width, lineStyle,
      lineType: style.startsWith("stepline") ? LineType.WithSteps : LineType.Simple,
      lineVisible: style !== "circles" && style !== "cross", pointMarkersVisible: style === "circles" || style === "cross" || style === "stepline_diamond",
      pointMarkersRadius: style === "circles" || style === "cross" ? width + 1 : undefined,
    }, pane);
    if (pane === this.view.pricePane && this.data.noScale) this.view.chart.priceScale(this.ownScale, this.view.pricePane).applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    else if (this.placement.labels) this.view.labels.attach(series);
    this.placed(series, pane);
    if (pane === this.placement.pane && !this.firstSeries) this.firstSeries = series;
    const values = this.data.values[item.id], colors = item.dynamic ? this.data.colors[item.id] : undefined;
    const point = (index: number) => {
      const time = this.view.timeAtLogical(index + offset);
      if (time === null) return null;
      const value = values?.[index];
      if (value === null || value === undefined) return { time };
      const dynamic = colors ? pineColor(colors[index] as string | null) : null;
      return dynamic ? { time, value, color: dynamic } : { time, value };
    };
    return this.seriesOutput(series, point, item.show_last);
  }

  private candles(item: PineMeta, color: string): Output {
    const pane = this.paneOf(item);
    const common = { lastValueVisible: false, priceLineVisible: false, ...this.paneFormat(pane) };
    const series = this.placed(item.kind === "candle"
      ? this.view.chart.addSeries(CandlestickSeries, { ...common, upColor: color, downColor: color, wickColor: pineColor(item.wickcolor) ?? color, borderColor: pineColor(item.bordercolor) ?? color }, pane)
      : this.view.chart.addSeries(BarSeries, { ...common, upColor: color, downColor: color }, pane), pane);
    if (pane === this.placement.pane && !this.firstSeries) this.firstSeries = series;
    const column = (part: string) => this.data.values[`${item.id} (${part})`];
    const point = (index: number) => {
      const time = this.view.timeAtLogical(index);
      if (time === null) return null;
      const [open, high, low, close] = ["open", "high", "low", "close"].map(part => column(part)?.[index]);
      return open == null || high == null || low == null || close == null ? { time } : { time, open, high, low, close };
    };
    return this.seriesOutput(series, point, item.show_last);
  }

  private hidden(series: ISeriesApi<"Line", number>, value: number): Output {
    return this.seriesOutput(series, index => { const time = this.view.timeAtLogical(index); return time === null ? null : { time, value }; }, undefined);
  }

  /** A series fed by `point`, its values in its scale's unit (precision.ts ScaleUnit): the format it was made with while
      the unit is 1, labels multiplied back otherwise. */
  private seriesOutput(series: ISeriesApi<SeriesType, number>, point: (index: number) => { time: number } | null, showLast: number | undefined): Output {
    const first = () => Math.max(this.view.firstRow, showLast === undefined ? 0 : this.view.barCount() - showLast);
    const scale = this.view.scaleUnit(series), format = series.options().priceFormat;
    let unit = 1;
    const use = (next: number) => { if (next !== unit) { unit = next; series.applyOptions({ priceFormat: unitFormat(format, unit) as never }); } };
    // The series' newest time: an earlier row (its values came after the bars moved on) is a historical update.
    let last = -Infinity;
    const all = () => {
      const data: { time: number }[] = [];
      let values = 0, largest = 0;
      for (let index = first(); index < this.view.barCount(); index++) {
        const item = point(index);
        if (item) { data.push(item); if (Object.keys(item).length > 1) { values++; largest = Math.max(largest, magnitude(item)); } }
      }
      use(scale.unitFor(series, largest, true));
      // Only whitespace (rows emptied while a new window loads) is set as no data: the engine's bar colouring reads a
      // real bar for every series it paints.
      series.setData((values ? data.map(item => inUnit(item, unit)) : []) as never);
      last = values && data.length ? data[data.length - 1].time : -Infinity;
    };
    const leave = scale.join(series, all);
    return {
      all,
      // A plot showing only its last bars drops its oldest point as bars come in, so it is set again.
      // Many rows at once (the rest of a replace sent in pieces) are set together: each update walks the whole series.
      // A value that no longer fits the unit sets the series again in a new one.
      from: start => {
        if (showLast !== undefined || this.view.barCount() - start > BULK_ROWS) { all(); return; }
        for (let index = start; index < this.view.barCount(); index++) {
          const item = point(index);
          if (!item) continue;
          if (scale.unitFor(series, magnitude(item), false) !== unit) { all(); return; }
          series.update(inUnit(item, unit) as never, item.time < last);
          last = Math.max(last, item.time);
        }
      },
      dispose: () => { leave(); this.view.removeSeries(series); },
    };
  }

  private fill(item: PineMeta): Output {
    const pane = this.paneOf(item);
    const series = this.placed(this.view.chart.addCustomSeries(new BandFill(), this.paneFormat(pane), pane), pane);
    const level = (plot?: string, hline?: string): ((index: number) => number | null) => {
      if (plot) { const values = this.data.values[plot]; return index => values?.[index] ?? null; }
      const price = this.data.meta.find(meta => meta.id === hline)?.price ?? null;
      return () => price;
    };
    const a = level(item.plot1, item.hline1), b = level(item.plot2, item.hline2);
    const base = this.placement.styles[item.id]?.color ?? pineColor(item.color) ?? tokenValue("--tx-clear");
    const colors = item.dynamic ? this.data.colors[item.id] : undefined;
    const point = (index: number) => {
      const time = this.view.timeAtLogical(index);
      if (time === null) return null;
      const upper = a(index), lower = b(index);
      if (upper === null || lower === null) return { time };
      const channel = colors?.[index];
      const fill = Array.isArray(channel) ? pineColor(channel[2] as string | null) ?? base : pineColor(channel as string | null | undefined) ?? base;
      return { time, upper: Math.max(upper, lower), lower: Math.min(upper, lower), fill };
    };
    return this.seriesOutput(series as unknown as ISeriesApi<SeriesType, number>, point, item.show_last);
  }

  private markerOutput(items: PineMeta[]): Output {
    const target = this.attachTarget();
    this.markers = createSeriesMarkers(target, []);
    const rowMarkers = (index: number): SeriesMarker<number>[] => items.flatMap((item): SeriesMarker<number>[] => {
      const value = this.data.values[item.id]?.[index];
      if (value === null || value === undefined || value === 0) return [];
      const time = this.view.timeAtLogical(index + (item.offset ?? 0));
      if (time === null) return [];
      const channel = item.dynamic ? this.data.colors[item.id]?.[index] : null;
      const dynamic = Array.isArray(channel) ? channel : [channel];
      if (item.kind === "arrow") {
        const up = value > 0, color = pineColor((up ? dynamic[0] : dynamic[1]) as string | null) ?? pineColor(up ? item.colorup : item.colordown) ?? this.placement.palette[0];
        return [{ time, position: up ? "belowBar" : "aboveBar", shape: up ? "arrowUp" : "arrowDown", color }];
      }
      const color = pineColor(dynamic[0] as string | null) ?? pineColor(item.color) ?? this.placement.palette[0];
      const location = item.location ?? "abovebar";
      const shape = item.kind === "char" ? "circle" : SHAPES[item.style ?? "xcross"] ?? "circle";
      const text = item.kind === "char" ? `${item.char ?? ""}${item.text ? ` ${item.text}` : ""}` : item.text;
      const size = MARKER_SIZES[item.size ?? "auto"] ?? 1;
      if (location === "absolute") return [{ time, position: "atPriceMiddle", price: value / this.hostUnit(), shape, color, text, size }];
      return [{ time, position: location === "belowbar" || location === "bottom" ? "belowBar" : "aboveBar", shape, color, text, size }];
    });
    return {
      markers: true,
      all: () => {
        this.markerRows = [];
        for (let index = this.view.firstRow; index < this.view.barCount(); index++) this.markerRows.push(...rowMarkers(index));
        this.markers!.setMarkers(this.markerRows);
      },
      from: start => {
        const startTime = this.view.timeAtLogical(start) ?? Infinity;
        this.markerRows = this.markerRows.filter(marker => marker.time < startTime);
        for (let index = start; index < this.view.barCount(); index++) this.markerRows.push(...rowMarkers(index));
        this.markers!.setMarkers(this.markerRows);
      },
      dispose: () => undefined,
    };
  }

  private colorOf(items: PineMeta[], index: number): string | null {
    for (let at = items.length - 1; at >= 0; at--) {
      const item = items[at], row = index - (item.offset ?? 0);
      const color = pineColor(this.data.colors[item.id]?.[row] as string | null | undefined);
      if (color) return color;
    }
    return null;
  }

  private barColors(items: PineMeta[]): Output {
    return {
      all: () => this.view.setBarColors(index => this.colorOf(items, index)),
      from: start => this.view.repaintBars(start),
      dispose: () => this.view.setBarColors(null),
    };
  }

  private extraRange(hlines: PineMeta[]): { min: number; max: number } | null {
    const prices = hlines.map(item => item.price!);
    const drawings = this.drawings?.priceRange();
    if (drawings) prices.push(drawings.min, drawings.max);
    return prices.length ? { min: Math.min(...prices), max: Math.max(...prices) } : null;
  }
}

const SHAPES: Record<string, SeriesMarker<number>["shape"]> = {
  xcross: "square", cross: "square", circle: "circle", triangleup: "arrowUp", triangledown: "arrowDown", flag: "square",
  arrowup: "arrowUp", arrowdown: "arrowDown", square: "square", diamond: "square", labelup: "arrowUp", labeldown: "arrowDown",
};
const MARKER_SIZES: Record<string, number> = { tiny: 0.5, small: 0.75, normal: 1, auto: 1, large: 1.5, huge: 2 };

const VALUE_FIELDS = ["value", "open", "high", "low", "close", "upper", "lower"] as const;
type Point = { time: number } & Partial<Record<(typeof VALUE_FIELDS)[number], number>>;
/** The largest magnitude among a point's values. */
function magnitude(item: Point): number { let largest = 0; for (const field of VALUE_FIELDS) if (item[field] !== undefined) largest = Math.max(largest, Math.abs(item[field]!)); return largest; }
/** A point with its values divided by the unit. */
function inUnit<T extends Point>(item: T, unit: number): T {
  if (unit === 1) return item;
  const scaled: Point = { ...item };
  for (const field of VALUE_FIELDS) if (item[field] !== undefined) scaled[field] = item[field]! / unit;
  return scaled as T;
}

function widen(base: AutoscaleInfo | null, extra: { min: number; max: number } | null): AutoscaleInfo | null {
  if (!extra) return base;
  if (!base?.priceRange) return { priceRange: { minValue: extra.min, maxValue: extra.max } };
  return { ...base, priceRange: { minValue: Math.min(base.priceRange.minValue, extra.min), maxValue: Math.max(base.priceRange.maxValue, extra.max) } };
}
