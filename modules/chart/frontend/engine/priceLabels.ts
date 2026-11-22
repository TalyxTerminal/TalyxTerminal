/* The labels on the price scales, laid out the way TradingView does: labels near the same price stack apart, keeping
   their order by price, never over each other, and the countdown stays under the last price label. Lightweight Charts
   spreads its own labels but lets them overlap near a pane's edges, so series draw none of their own: each labelled
   series carries a SeriesLabels primitive, and the labels of one pane's scale are placed together here, again only when
   one of them moved or changed. The series are known from attach and forget: the engine's own list of a pane's series
   can't be read while it is adding one, which is when it asks for labels. */
import { MismatchDirection, PriceScaleMode, type IPrimitivePaneRenderer, type IPrimitivePaneView, type ISeriesApi, type ISeriesPrimitive,
  type ISeriesPrimitiveAxisView, type SeriesAttachedParameter, type SeriesType } from "lightweight-charts";

type Series = ISeriesApi<SeriesType, number>;
type Target = Parameters<IPrimitivePaneRenderer["draw"]>[0];

/** A label at a price; `under` is a box attached below it (the countdown). */
export interface PriceLabel { price: number; color: string; under?: { text: string } | null }

/** The series' last value in its own colour, as Lightweight Charts would label it. */
export function lastValueLabel(series: Series): PriceLabel[] {
  const last = series.lastValueData(true);
  return last.noData ? [] : [{ price: last.price, color: last.color }];
}

/** Labels of one series: the last value unless told otherwise (the main series adds its countdown and alert lines). */
export class SeriesLabels implements ISeriesPrimitive<number> {
  series: Series | null = null;
  private request: (() => void) | null = null;
  /** `first`: the series whose labels stay when not all fit (the main series). */
  constructor(private readonly layout: PriceLabels, readonly labels: (series: Series) => PriceLabel[], readonly first: boolean) {}
  attached(param: SeriesAttachedParameter<number>): void { this.series = param.series as Series; this.request = param.requestUpdate; }
  detached(): void { this.series = null; this.request = null; }
  /** Something a label shows changed outside the data (the countdown's second). */
  update(): void { this.request?.(); }
  priceAxisViews(): readonly ISeriesPrimitiveAxisView[] { return this.layout.viewsOf(this); }
  // The countdown is drawn on the scale beside the labels: an axis label is as wide as its own text, and the countdown is
  // as wide as the price label above it.
  private readonly scaleViews: IPrimitivePaneView[] = [{ renderer: () => ({ draw: target => this.layout.drawUnder(this, target) }) }];
  priceAxisPaneViews(): readonly IPrimitivePaneView[] { return this.scaleViews; }
}

export interface Stacked { y: number; height: number; first: boolean; top: number }
interface Block extends Stacked { owner: SeriesLabels; text: string; color: string; under: string | null }
/** A box under a label: the middle of it, its text, colours, and the text of the label above (whose width it takes). */
interface Under { middle: number; text: string; above: string; back: string; color: string }
interface Group { key: string; views: Map<SeriesLabels, ISeriesPrimitiveAxisView[]>; unders: Map<SeriesLabels, Under[]> }

const NONE: ISeriesPrimitiveAxisView[] = [];

export class PriceLabels {
  private readonly groups = new Map<string, Group>();
  private readonly owners = new Map<Series, SeriesLabels>();
  private readonly contrast = new Map<string, { back: string; text: string }>();
  private readonly probe = document.createElement("canvas").getContext("2d")!;
  /** A label's height on the scale: Lightweight Charts draws a primitive's label with the font and 5/6 of it as padding. */
  private readonly height: number;
  /** The space Lightweight Charts gives a label beside its text: the tick and padding on both sides (5 px each at 12 px). */
  private readonly inset: number;

  /** `visibleFrom`: the logical index the visible bars start at; `side`: the main scale's side, where an overlay
      series' labels are shown; `measure`: the series whose scale and first value a series' value is read against. */
  constructor(private readonly fontSize: number, private readonly font: string,
    private readonly chart: { visibleFrom: () => number | null; side: () => string; measure: (series: Series) => Series }) {
    this.height = Math.ceil(fontSize * 11 / 6);
    this.inset = fontSize / 12 * 5;
  }

  /** Gives a series its labels on the scale (its own last value label must be off). */
  attach(series: Series, labels: (series: Series) => PriceLabel[] = lastValueLabel, first = false): SeriesLabels {
    const owner = new SeriesLabels(this, labels, first);
    series.attachPrimitive(owner);
    this.owners.set(series, owner);
    return owner;
  }

  /** The series is being removed from the chart. */
  forget(series: Series): void { this.owners.delete(series); }

  viewsOf(owner: SeriesLabels): readonly ISeriesPrimitiveAxisView[] { return this.groupOf(owner)?.views.get(owner) ?? NONE; }

  /** The owner's boxes under its labels, drawn as Lightweight Charts draws a label (its rounding and pixel snapping), as
      wide as the label above. */
  drawUnder(owner: SeriesLabels, target: Target): void {
    const unders = this.groupOf(owner)?.unders.get(owner);
    if (!unders) return;
    const left = this.axis(owner.series!) === "left", font = `${this.fontSize}px ${this.font}`;
    const widths = target.useMediaCoordinateSpace(({ context }) => {
      context.font = font;
      return unders.map(under => Math.ceil(context.measureText(under.above).width) + 2 * this.inset + 5);
    });
    target.useBitmapCoordinateSpace(({ context, bitmapSize, horizontalPixelRatio, verticalPixelRatio }) => {
      unders.forEach((under, index) => {
        const width = Math.round(widths[index] * horizontalPixelRatio), radius = 2 * horizontalPixelRatio;
        let height = Math.round(this.fontSize * 11 / 6 * verticalPixelRatio);
        const tick = Math.max(1, Math.floor(verticalPixelRatio));
        if (height % 2 !== tick % 2) height += 1;
        const top = Math.floor(Math.round(under.middle * verticalPixelRatio) - Math.floor(verticalPixelRatio * 0.5) + tick / 2 - height / 2);
        context.fillStyle = under.back;
        context.beginPath();
        context.roundRect(left ? bitmapSize.width - width : 0, top, width, height, left ? [radius, 0, 0, radius] : [0, radius, radius, 0]);
        context.fill();
      });
    });
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      context.font = font;
      context.textBaseline = "middle";
      context.textAlign = left ? "right" : "left";
      for (const under of unders) {
        context.fillStyle = under.color;
        context.fillText(under.text, left ? mediaSize.width - 5 - this.inset : 5 + this.inset, under.middle);
      }
    });
  }

  private groupOf(owner: SeriesLabels): Group | null {
    const series = owner.series;
    if (!series) return null;
    const pane = series.getPane(), index = pane.paneIndex(), scaleId = this.axis(series);
    const blocks: Block[] = [];
    for (const [member, labelled] of this.owners) {
      if (member.getPane().paneIndex() !== index || this.axis(member) !== scaleId || !member.options().visible) continue;
      for (const label of labelled.labels(member)) {
        const y = member.priceToCoordinate(label.price);
        if (y === null) continue;
        blocks.push({ owner: labelled, first: labelled.first, y, top: 0, height: label.under ? this.height * 2 : this.height, text: this.format(this.chart.measure(member), label.price), color: label.color, under: label.under?.text ?? null });
      }
    }
    const height = pane.getHeight(), name = `${index}:${scaleId}`;
    const key = `${height}|${blocks.map(block => `${Math.round(block.y * 2)}|${block.text}|${block.color}|${block.under}`).join("|")}`;
    let group = this.groups.get(name);
    if (group?.key !== key) {
      group = this.place(key, blocks, height);
      this.groups.set(name, group);
    }
    return group;
  }

  private place(key: string, blocks: Block[], height: number): Group {
    const half = this.height / 2, group: Group = { key, views: new Map(), unders: new Map() };
    for (const block of stack(blocks, height, this.height)) {
      const { back, text } = this.colors(block.color), views = group.views.get(block.owner) ?? [];
      views.push(view(block.top + half, block.text, back, text));
      group.views.set(block.owner, views);
      if (block.under === null) continue;
      const unders = group.unders.get(block.owner) ?? [];
      unders.push({ middle: block.top + half * 3, text: block.under, above: block.text, back, color: text });
      group.unders.set(block.owner, unders);
    }
    return group;
  }

  /** The scale whose labels a series' labels are among: its own, or an overlay's on the main scale's side. */
  private axis(series: Series): string {
    const id = series.options().priceScaleId;
    return id === "left" || id === "right" ? id : this.chart.side();
  }

  /** The value as the scale shows it: a price, or in percent or indexed to 100 from the series' first value on screen. */
  private format(series: Series, price: number): string {
    const mode = series.priceScale().options().mode;
    if (mode !== PriceScaleMode.Percentage && mode !== PriceScaleMode.IndexedTo100) return series.priceFormatter().format(price);
    const base = this.firstValue(series);
    if (base === null) return series.priceFormatter().format(price);
    // Lightweight Charts' own conversion and its two decimals for both; its minus sign is as wide as a plus.
    const percent = (base < 0 ? -1 : 1) * 100 * (price - base) / base, value = mode === PriceScaleMode.IndexedTo100 ? percent + 100 : percent;
    return `${value < 0 ? "\u2212" : ""}${Math.abs(value).toFixed(2)}${mode === PriceScaleMode.Percentage ? "%" : ""}`;
  }

  /** The value a percent scale measures this series from: its first bar on screen (Lightweight Charts' own rule). */
  private firstValue(series: Series): number | null {
    const from = this.chart.visibleFrom();
    if (from === null) return null;
    const bar = series.dataByIndex(Math.floor(from), MismatchDirection.NearestRight) as { close?: number; value?: number } | null;
    return bar?.close ?? bar?.value ?? null;
  }

  /** A solid background (labels are opaque) and black or white text, by Lightweight Charts' own contrast rule. */
  private colors(color: string): { back: string; text: string } {
    let found = this.contrast.get(color);
    if (!found) {
      this.probe.fillStyle = color;
      const value = String(this.probe.fillStyle);
      const [r, g, b] = value.startsWith("#") ? [1, 3, 5].map(at => parseInt(value.slice(at, at + 2), 16)) : value.match(/[\d.]+/g)!.slice(0, 3).map(Number);
      found = { back: `rgb(${r}, ${g}, ${b})`, text: 0.199 * r + 0.687 * g + 0.114 * b > 160 ? "black" : "white" }; // i18n-ignore: CSS colors
      this.contrast.set(color, found);
    }
    return found;
  }
}

/** Labels as near their prices (`y`, the middle of a label's first line) as the others allow: overlapping labels gather
    into a run centred on their prices, runs that then meet join, and every run stays inside the pane. When they can't all
    fit, the labels farthest from the first series' (the main series) go. Sets `top` and returns the labels kept, top down. */
export function stack<T extends Stacked>(all: T[], paneHeight: number, labelHeight: number): T[] {
  const anchor = all.find(block => block.first)?.y ?? paneHeight / 2;
  let blocks = all, total = all.reduce((sum, block) => sum + block.height, 0);
  if (total > paneHeight) {
    blocks = [...all].sort((a, b) => Number(b.first) - Number(a.first) || Math.abs(a.y - anchor) - Math.abs(b.y - anchor));
    while (blocks.length > 1 && total > paneHeight) total -= blocks.pop()!.height;
  }
  blocks = [...blocks].sort((a, b) => a.y - b.y);
  for (const block of blocks) block.top = Math.min(Math.max(block.y - labelHeight / 2, 0), paneHeight - block.height);
  const runs: { blocks: T[]; top: number; height: number }[] = [];
  for (const block of blocks) {
    runs.push({ blocks: [block], top: block.top, height: block.height });
    while (runs.length > 1) {
      const last = runs[runs.length - 1], before = runs[runs.length - 2];
      if (before.top + before.height <= last.top) break;
      const joined = [...before.blocks, ...last.blocks];
      let offset = 0, sum = 0;
      for (const item of joined) { sum += item.top - offset; offset += item.height; }
      runs.splice(runs.length - 2, 2, { blocks: joined, height: offset, top: Math.max(0, Math.min(sum / joined.length, paneHeight - offset)) });
    }
  }
  for (const run of runs) {
    let top = run.top;
    for (const block of run.blocks) { block.top = top; top += block.height; }
  }
  return blocks;
}

function view(coordinate: number, text: string, back: string, color: string): ISeriesPrimitiveAxisView {
  return { coordinate: () => coordinate, fixedCoordinate: () => coordinate, text: () => text, textColor: () => color, backColor: () => back, visible: () => true, tickVisible: () => true };
}
