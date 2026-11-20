/* One chart's stream data, held in place: bars, indicator rows and comparison values grow row by row; the chart view reads
   them directly. Listeners hear what changed so each piece redraws only itself. Document, status and errors go to the store. */
import { appendBars, emptyBars, putBar, truncateBars, type Bars } from "./engine/bars";
import type { IndicatorData } from "./engine/indicatorView";
import { withDrawingChanges } from "./engine/pinePrimitives";
import type { PriceFormat } from "./engine/precision";
import type { ChartItem } from "./wire";

export type FeedEvent =
  | { kind: "bars"; replace: boolean }
  | { kind: "format" }
  /** `from`: the first row that changed (rows may arrive a frame after their bar). */
  | { kind: "indicator"; id: string; rebuild: boolean; drawings: boolean; from: number }
  /** `from`: the first row that changed (a comparison's rows may come after their bars). */
  | { kind: "comparison"; id: string; replace: boolean; from: number };

export interface ComparisonData { value: (number | null)[]; priceFormat: PriceFormat }

type BarsItem = Extract<ChartItem, { type: "bars" }>;
type IndicatorItem = Extract<ChartItem, { type: "indicator" }>;
type ComparisonItem = Extract<ChartItem, { type: "comparison" }>;

export class ChartFeed {
  bars: Bars = emptyBars();
  priceFormat: PriceFormat = { precision: 2, minMove: 0.01 };
  hasMoreBefore = false;
  limitReached = false;
  /** The box of a brick type's bars (Point & figure draws its marks on it); null on other types. */
  boxSize: number | null = null;
  /** Bars arrived for this window yet (a chart shows nothing before its first replace). */
  loaded = false;
  private windowRows = 0;
  get completeWindow(): boolean { return this.loaded && this.bars.time.length >= this.windowRows; }
  readonly indicators = new Map<string, IndicatorData>();
  readonly comparisons = new Map<string, ComparisonData>();
  private readonly listeners = new Set<(event: FeedEvent) => void>();

  listen(listener: (event: FeedEvent) => void): () => void { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  private emit(event: FeedEvent): void { for (const listener of this.listeners) listener(event); }

  takeBars(item: BarsItem): void {
    const wasComplete = this.completeWindow;
    const rows: Bars = { time: item.time, closeTime: item.closeTime, open: item.open, high: item.high, low: item.low, close: item.close, volume: item.volume };
    if (item.op === "replace") {
      // Indicator and comparison rows line up with the old window: until their own replace for this window arrives, each
      // row stands at its bar's place in the new one (Market's newest page, then its whole window: nothing blinks out), and
      // rows of bars the new window lacks go. Drawings place themselves by bar index and wait. In place: the chart's
      // indicator views hold these objects.
      const places = placesIn(this.bars.time, rows.time);
      for (const data of this.indicators.values()) {
        for (const columns of [data.values, data.colors]) for (const id in columns) columns[id] = moved(columns[id], places);
        data.drawings = null;
      }
      for (const data of this.comparisons.values()) data.value = moved(data.value, places);
      this.bars = rows;
      this.priceFormat = item.priceFormat!;
      this.hasMoreBefore = item.hasMoreBefore === true;
      this.limitReached = item.limitReached === true;
      this.boxSize = item.boxSize ?? null;
      this.loaded = true;
      this.windowRows = item.windowRows ?? rows.time.length;
    } else {
      if (item.op === "splice") truncateBars(this.bars, item.start!);
      appendBars(this.bars, rows);
    }
    this.emit({ kind: "bars", replace: item.op === "replace" || !wasComplete && this.completeWindow });
  }

  /** Market's price increment arrived after the bars: the format changes, the bars stay. */
  takePriceFormat(priceFormat: PriceFormat): void {
    this.priceFormat = priceFormat;
    this.emit({ kind: "format" });
  }

  /** The document revision that brought the series shown now, and the newest one the stream has sent. A commit's answer
      can come before the stream gets there; until it does, the stream's bars, rows and status are the old series'. */
  private seriesSince = 0;
  private streamAt = 0;
  get current(): boolean { return this.streamAt >= this.seriesSince; }
  streamReached(revision: number): void { this.streamAt = revision; }

  /** The chart now shows another series (from this document revision): nothing of the old one stays on screen while the
      new one loads. */
  reset(revision: number): void {
    this.seriesSince = revision;
    this.bars = emptyBars();
    this.hasMoreBefore = false; this.limitReached = false; this.loaded = false;
    for (const data of this.indicators.values()) Object.assign(data, { values: {}, colors: {}, drawings: null });
    for (const data of this.comparisons.values()) data.value = [];
    this.emit({ kind: "bars", replace: true });
  }

  takeUpdate(item: Extract<ChartItem, { type: "update" }>): void {
    putBar(this.bars, item.bar);
    this.emit({ kind: "bars", replace: false });
  }

  takeIndicator(item: IndicatorItem): void {
    let data = this.indicators.get(item.instanceId);
    if (item.op === "replace" || !data) {
      // Every replace names behindChart and noScale; rows without a replace before them have no meta, so they draw nothing.
      const next = { meta: item.meta ?? data?.meta ?? [], values: item.values, colors: item.colors, drawings: item.drawings ?? data?.drawings ?? null,
        behindChart: item.behindChart ?? data?.behindChart ?? false, noScale: item.noScale ?? data?.noScale ?? false };
      // In place, so an indicator view built on this object sees the new rows when it rebuilds.
      if (data) Object.assign(data, next); else this.indicators.set(item.instanceId, next);
      this.emit({ kind: "indicator", id: item.instanceId, rebuild: true, drawings: false, from: 0 });
      return;
    }
    if (item.op === "splice") {
      for (const column of [...Object.values(data.values), ...Object.values(data.colors)]) column.length = item.start!;
    }
    let from = item.op === "splice" ? item.start! : this.bars.time.length;
    for (let row = 0; row < item.time.length; row++) {
      const index = this.rowOf(item.time[row]);
      from = Math.min(from, index);
      for (const [id, values] of Object.entries(item.values)) (data.values[id] ??= [])[index] = values[row];
      for (const [id, colors] of Object.entries(item.colors)) (data.colors[id] ??= [])[index] = colors[row];
    }
    if (item.meta) data.meta = item.meta;
    if (item.drawings) data.drawings = item.drawings;
    if (item.drawingChanges && data.drawings) data.drawings = withDrawingChanges(data.drawings, item.drawingChanges);
    this.emit({ kind: "indicator", id: item.instanceId, rebuild: item.meta !== undefined, drawings: item.drawings !== undefined || item.drawingChanges !== undefined, from });
  }

  takeComparison(item: ComparisonItem): void {
    let from = 0;
    if (item.op === "replace") {
      // The same object stays: a comparison line drawn from it keeps reading it.
      const data = this.comparisons.get(item.comparisonId);
      if (data) Object.assign(data, { value: item.value, priceFormat: item.priceFormat! });
      else this.comparisons.set(item.comparisonId, { value: item.value, priceFormat: item.priceFormat! });
    } else {
      const data = this.comparisons.get(item.comparisonId)!;
      from = this.bars.time.length;
      for (let row = 0; row < item.time.length; row++) {
        const index = this.rowOf(item.time[row]);
        data.value[index] = item.value[row];
        from = Math.min(from, index);
      }
    }
    this.emit({ kind: "comparison", id: item.comparisonId, replace: item.op === "replace", from });
  }

  dropIndicator(id: string): void { this.indicators.delete(id); }

  /** Rows line up one to one with the bars; new rows are at the end. */
  private rowOf(time: number): number {
    const times = this.bars.time;
    for (let index = times.length - 1; index >= 0 && times[index] >= time; index--) if (times[index] === time) return index;
    throw new Error("An indicator row has no bar at its time");
  }
}

/** Where each old bar time stands among the new times (-1: not there); both ascend. */
function placesIn(old: number[], times: number[]): number[] {
  const places: number[] = [];
  let at = 0;
  for (const time of old) {
    while (at < times.length && times[at] < time) at++;
    places.push(times[at] === time ? at : -1);
  }
  return places;
}

function moved<T>(column: T[], places: number[]): T[] {
  const result: T[] = [];
  column.forEach((value, index) => { if (places[index] >= 0) result[places[index]] = value; });
  return result;
}
