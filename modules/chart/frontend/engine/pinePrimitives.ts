/* Canvas pieces for Pine outputs the engine has no series for:
   - BandFill: `fill()` between two plots or hlines, a custom series so the price scale and conflation include it;
   - BarBackground: `bgcolor()` columns behind the series;
   - PineDrawings: `line`, `label`, `box`, `polyline`, `linefill` and `table` objects of one indicator. */
import {
  customSeriesDefaultOptions, PriceScaleMode,
  type CustomData, type CustomSeriesOptions, type CustomSeriesWhitespaceData, type IChartApiBase, type ICustomSeriesPaneRenderer, type ICustomSeriesPaneView,
  type IPrimitivePaneRenderer, type IPrimitivePaneView, type ISeriesApi, type ISeriesPrimitive, type Logical, type PaneRendererCustomData,
  type PriceToCoordinateConverter, type SeriesAttachedParameter, type SeriesType,
} from "lightweight-charts";
import type { CanvasRenderingTarget2D } from "fancy-canvas";
import { tokenValue } from "@talyx/sdk";
import type { TimeMap } from "./timeMap";

/** The script's na colour in meta records and colour rows (the worker's encoding): that output, or that bar of it, draws
    nothing (Pine). */
export const NA_COLOR = "na";
/** PyneCore writes colours as `#RRGGBBAA` (alpha 255 opaque). Read every frame for every drawing, so each is read once.
    na is no colour. */
const COLORS = new Map<string, string>();
export function pineColor(value: string | null | undefined): string | null {
  if (!value || value === NA_COLOR) return null;
  if (value.length !== 9) return value;
  let color = COLORS.get(value);
  if (color === undefined) {
    const alpha = parseInt(value.slice(7, 9), 16) / 255;
    color = `rgba(${parseInt(value.slice(1, 3), 16)}, ${parseInt(value.slice(3, 5), 16)}, ${parseInt(value.slice(5, 7), 16)}, ${Math.round(alpha * 1000) / 1000})`;
    // Gradients make many colours; the map starts over rather than grow without end.
    if (COLORS.size >= 4096) COLORS.clear();
    COLORS.set(value, color);
  }
  return color;
}

/** The script's colour for one part of an output on one row: the row's own while the colour varies (`row` is its entry in
    the output's colour column: a colour, NA_COLOR, a list of parts, or null), else the meta's (`fallback`). */
export function scriptColor(row: unknown, fallback: string | undefined, part = 0): string | undefined {
  const own = (Array.isArray(row) ? row[part] : row) as string | null | undefined;
  return own ?? fallback;
}

/** What a row of an output is drawn in: nothing (null) where the script's colour is na; else the colour the user chose
    for the output, the whole output in it; else the script's (undefined when it names none). Reset to defaults clears the
    user's, and the script's colours, per bar too, come back. */
export function drawnColor(user: string | null | undefined, script: string | undefined): string | null | undefined {
  if (script === NA_COLOR) return null;
  return user ?? pineColor(script) ?? undefined;
}

// ── fill ─────────────────────────────────────────────────────────────────

export interface BandPoint extends CustomData<number> { upper: number; lower: number; fill: string }
export interface BandOptions extends CustomSeriesOptions { fillColor: string }

export class BandFill implements ICustomSeriesPaneView<number, BandPoint, BandOptions> {
  private data: PaneRendererCustomData<number, BandPoint> | null = null;
  renderer(): ICustomSeriesPaneRenderer { return { draw: (target, toY) => this.draw(target, toY) }; }
  update(data: PaneRendererCustomData<number, BandPoint>) { this.data = data; }
  priceValueBuilder(point: BandPoint) { return [point.lower, point.upper, (point.lower + point.upper) / 2]; }
  isWhitespace(point: BandPoint | CustomSeriesWhitespaceData<number>): point is CustomSeriesWhitespaceData<number> { return (point as BandPoint).upper === undefined; }
  defaultOptions(): BandOptions { return { ...customSeriesDefaultOptions, fillColor: tokenValue("--tx-clear"), lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false } as BandOptions; }
  private draw(target: CanvasRenderingTarget2D, toY: PriceToCoordinateConverter) {
    const data = this.data;
    if (!data?.visibleRange) return;
    target.useBitmapCoordinateSpace(({ context, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
      const { from, to } = data.visibleRange!;
      // Only bars inside the visible range carry a current x; the one before it is placed a bar to the left (spike pitfall 4).
      for (let index = Math.max(from, 1); index < to; index++) {
        const a = data.bars[index - 1], b = data.bars[index];
        const first = a.originalData as Partial<BandPoint>, second = b.originalData as Partial<BandPoint>;
        if (first.upper === undefined || second.upper === undefined) continue;
        const ax = index === from ? b.x - data.barSpacing : a.x;
        const au = toY(first.upper), al = toY(first.lower!), bu = toY(second.upper), bl = toY(second.lower!);
        if (au === null || al === null || bu === null || bl === null) continue;
        context.fillStyle = second.fill!;
        context.beginPath(); context.moveTo(ax * hr, au * vr); context.lineTo(b.x * hr, bu * vr); context.lineTo(b.x * hr, bl * vr); context.lineTo(ax * hr, al * vr);
        context.closePath(); context.fill();
      }
    });
  }
}

// ── bgcolor ──────────────────────────────────────────────────────────────

export class BarBackground implements ISeriesPrimitive<number> {
  private chart!: IChartApiBase<number>;
  requestUpdate: () => void = () => undefined;
  constructor(private readonly colorAt: (index: number) => string | null, private readonly count: () => number) {}
  attached(param: SeriesAttachedParameter<number>) { this.chart = param.chart; this.requestUpdate = param.requestUpdate; }
  paneViews(): IPrimitivePaneView[] {
    return [{ zOrder: () => "bottom", renderer: () => ({ draw: () => undefined, drawBackground: target => this.draw(target) }) as IPrimitivePaneRenderer }];
  }
  private draw(target: CanvasRenderingTarget2D) {
    const scale = this.chart.timeScale(), range = scale.getVisibleLogicalRange();
    if (!range) return;
    const spacing = scale.options().barSpacing;
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      const last = Math.min(this.count() - 1, Math.ceil(range.to));
      for (let index = Math.max(0, Math.floor(range.from)); index <= last; index++) {
        const color = this.colorAt(index);
        if (!color) continue;
        const x = scale.logicalToCoordinate(index as Logical);
        if (x === null) continue;
        context.fillStyle = color; context.fillRect(x - spacing / 2, 0, spacing, mediaSize.height);
      }
    });
  }
}

// ── line / label / box / polyline / linefill / table ─────────────────────

export interface PineLine { id: number; x1: number | null; y1: number | null; x2: number | null; y2: number | null; xloc: string; extend: string; color: string | null; style: string; width: number }
export interface PineLabel { id: number; x: number | null; y: number | null; text: string; xloc: string; yloc: string; color: string | null; style: string; textcolor: string | null; size: string; textalign: string }
export interface PineBox { id: number; left: number | null; top: number | null; right: number | null; bottom: number | null; xloc: string; extend: string; border_color: string | null; border_width: number; border_style: string; bgcolor: string | null; text: string; text_color: string | null; text_halign: string; text_valign: string }
export interface PinePolyline { id: number; points: { index: number | null; time: number | null; price: number | null }[]; closed: boolean; xloc: string; line_color: string | null; fill_color: string | null; line_style: string; line_width: number }
export interface PineLinefill { id: number; color: string | null; line1_state: PineLine; line2_state: PineLine }
export interface PineTableCell { col: number; row: number; text: string; text_color: string | null; bgcolor: string | null; text_halign: string; merge?: [number, number, number, number] }
export interface PineTable { id: number; position: string; columns: number; rows: number; bgcolor: string | null; frame_color: string | null; frame_width: number; border_color: string | null; border_width: number; cells: PineTableCell[] }
export interface PineDrawingSet { lines: PineLine[]; labels: PineLabel[]; boxes: PineBox[]; polylines: PinePolyline[]; linefills: PineLinefill[]; tables: PineTable[] }
/** Per family, the drawings made or changed since the last step and the ids gone. */
export type PineDrawingChanges = Partial<{ [Family in keyof PineDrawingSet]: { set: PineDrawingSet[Family]; drop: number[] } }>;

/** `set` with `changes` folded in, as a new set: a changed drawing keeps its place, a new one goes on top. */
export function withDrawingChanges(set: PineDrawingSet, changes: PineDrawingChanges): PineDrawingSet {
  const next = { ...set };
  for (const [family, change] of Object.entries(changes) as [keyof PineDrawingSet, { set: { id: number }[]; drop: number[] }][]) {
    const items = new Map<number, { id: number }>(next[family].map(item => [item.id, item]));
    for (const item of change.set) items.set(item.id, item);
    for (const id of change.drop) items.delete(id);
    (next[family] as { id: number }[]) = [...items.values()];
  }
  return next;
}

const TEXT_SIZES: Record<string, number> = { tiny: 9, small: 10, normal: 12, auto: 12, large: 15, huge: 20 };
const DASH: Record<string, number[]> = { dashed: [6, 4], dotted: [1, 3] };

export class PineDrawings implements ISeriesPrimitive<number> {
  private chart!: IChartApiBase<number>;
  private series!: ISeriesApi<SeriesType, number>;
  requestUpdate: () => void = () => undefined;
  /** `behind`: Pine's `behind_chart` on the price pane, the drawings under the bars (tables stay on top). */
  constructor(private set: PineDrawingSet, private readonly timeMap: () => TimeMap, private readonly font: string, private readonly behind: boolean) {}
  attached(param: SeriesAttachedParameter<number>) { this.chart = param.chart; this.series = param.series as ISeriesApi<SeriesType, number>; this.requestUpdate = param.requestUpdate; }
  replace(set: PineDrawingSet) { this.set = set; this.requestUpdate(); }

  /** Prices the drawings reach, for the price scale of a pane that has nothing else. */
  priceRange(): { min: number; max: number } | null {
    const prices = [...this.set.lines.flatMap(line => [line.y1, line.y2]), ...this.set.boxes.flatMap(box => [box.top, box.bottom]), ...this.set.labels.map(label => label.y)]
      .filter((price): price is number => price !== null);
    return prices.length ? { min: Math.min(...prices), max: Math.max(...prices) } : null;
  }

  paneViews(): IPrimitivePaneView[] {
    if (!this.behind) return [{ zOrder: () => "normal", renderer: () => ({ draw: target => { this.draw(target); this.drawTables(target); } }) }];
    return [
      { zOrder: () => "bottom", renderer: () => ({ draw: () => undefined, drawBackground: target => this.draw(target) }) as IPrimitivePaneRenderer },
      { zOrder: () => "normal", renderer: () => ({ draw: target => this.drawTables(target) }) },
    ];
  }

  /** `bar_index` counts rows of the window, which are the logical positions; `bar_time` goes through the time map. */
  private x(x: number | null, xloc: string): number | null {
    if (x === null) return null;
    const logical = xloc === "bar_time" ? this.timeMap().toLogical(x) : x;
    return logical === null ? null : this.toX(logical);
  }
  private y(price: number | null): number | null { return price === null ? null : this.toY(price); }

  /** The pane's scales as straight lines, read once a frame. The engine's price conversion reads the whole price scale
      on every call, which hundreds of boxes (a volume profile) made the largest cost of a frame; and its time
      conversion places whole bars only, while a Pine time falls anywhere inside a bar. A log scale is not a line and
      keeps the engine's conversion. */
  private toX: (logical: number) => number = () => 0;
  private toY: (price: number) => number | null = () => null;
  private measure(height: number): boolean {
    const scale = this.chart.timeScale(), origin = scale.logicalToCoordinate(0 as Logical), spacing = scale.options().barSpacing;
    if (origin === null) return false;
    this.toX = logical => origin + logical * spacing;
    const top = this.series.coordinateToPrice(0), bottom = this.series.coordinateToPrice(height);
    if (top === null || bottom === null) return false;
    if (this.series.priceScale().options().mode === PriceScaleMode.Logarithmic || top === bottom) this.toY = price => this.series.priceToCoordinate(price);
    else this.toY = price => (price - top) / (bottom - top) * height;
    return true;
  }

  private draw(target: CanvasRenderingTarget2D) {
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      if (!this.measure(mediaSize.height)) return;
      context.save();
      context.textBaseline = "middle";
      for (const fill of this.set.linefills) this.linefill(context, fill, mediaSize.width);
      for (const box of this.set.boxes) this.box(context, box, mediaSize.width, mediaSize.height);
      for (const line of this.set.lines) this.line(context, line, mediaSize.width);
      for (const polyline of this.set.polylines) this.polyline(context, polyline);
      for (const label of this.set.labels) this.label(context, label);
      context.restore();
    });
  }

  private drawTables(target: CanvasRenderingTarget2D) {
    if (!this.set.tables.length) return;
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      context.save();
      context.textBaseline = "middle";
      for (const table of this.set.tables) this.table(context, table, mediaSize);
      context.restore();
    });
  }

  private ends(line: PineLine, width: number): [number, number, number, number] | null {
    let x1 = this.x(line.x1, line.xloc), x2 = this.x(line.x2, line.xloc);
    let y1 = this.y(line.y1), y2 = this.y(line.y2);
    if (x1 === null || x2 === null || y1 === null || y2 === null) return null;
    const slope = x2 === x1 ? 0 : (y2 - y1) / (x2 - x1);
    if (line.extend === "right" || line.extend === "both") { const to = x2 >= x1 ? width : 0; y2 = y1 + slope * (to - x1); x2 = to; }
    if (line.extend === "left" || line.extend === "both") { const to = x2 >= x1 ? 0 : width; y1 = y2 + slope * (to - x2); x1 = to; }
    return [x1, y1, x2, y2];
  }

  private line(context: CanvasRenderingContext2D, line: PineLine, width: number) {
    const ends = this.ends(line, width), color = pineColor(line.color);
    if (!ends || !color) return;
    const [x1, y1, x2, y2] = ends;
    context.strokeStyle = color; context.lineWidth = line.width; context.setLineDash(DASH[line.style] ?? []);
    context.beginPath(); context.moveTo(x1, y1); context.lineTo(x2, y2); context.stroke();
    context.setLineDash([]);
    const head = (fromX: number, fromY: number, toX: number, toY: number) => {
      const angle = Math.atan2(toY - fromY, toX - fromX), size = 6 + line.width * 2;
      context.fillStyle = color; context.beginPath(); context.moveTo(toX, toY);
      context.lineTo(toX - size * Math.cos(angle - 0.45), toY - size * Math.sin(angle - 0.45));
      context.lineTo(toX - size * Math.cos(angle + 0.45), toY - size * Math.sin(angle + 0.45)); context.closePath(); context.fill();
    };
    if (line.style === "arrow_right" || line.style === "arrow_both") head(x1, y1, x2, y2);
    if (line.style === "arrow_left" || line.style === "arrow_both") head(x2, y2, x1, y1);
  }

  private linefill(context: CanvasRenderingContext2D, fill: PineLinefill, width: number) {
    const a = this.ends(fill.line1_state, width), b = this.ends(fill.line2_state, width), color = pineColor(fill.color);
    if (!a || !b || !color) return;
    context.fillStyle = color; context.beginPath();
    context.moveTo(a[0], a[1]); context.lineTo(a[2], a[3]); context.lineTo(b[2], b[3]); context.lineTo(b[0], b[1]); context.closePath(); context.fill();
  }

  /** Profiles are drawn as hundreds of boxes: one off the screen costs two coordinate reads and nothing else. */
  private box(context: CanvasRenderingContext2D, box: PineBox, width: number, height: number) {
    let left = this.x(box.left, box.xloc), right = this.x(box.right, box.xloc);
    if (left === null || right === null) return;
    if (left > right) [left, right] = [right, left];
    if (box.extend === "left" || box.extend === "both") left = 0;
    if (box.extend === "right" || box.extend === "both") right = width;
    if (right < 0 || left > width) return;
    const top = this.y(box.top), bottom = this.y(box.bottom);
    if (top === null || bottom === null || Math.max(top, bottom) < 0 || Math.min(top, bottom) > height) return;
    const background = pineColor(box.bgcolor), border = pineColor(box.border_color);
    if (background) { context.fillStyle = background; context.fillRect(left, top, right - left, bottom - top); }
    if (border && box.border_width > 0) {
      context.strokeStyle = border; context.lineWidth = box.border_width; context.setLineDash(DASH[box.border_style] ?? []);
      context.strokeRect(left + 0.5, top + 0.5, right - left, bottom - top); context.setLineDash([]);
    }
    const text = pineColor(box.text_color);
    if (box.text && text) {
      context.fillStyle = text; context.font = `12px ${this.font}`;
      context.textAlign = box.text_halign === "left" ? "left" : box.text_halign === "right" ? "right" : "center";
      const x = box.text_halign === "left" ? left + 4 : box.text_halign === "right" ? right - 4 : (left + right) / 2;
      const y = box.text_valign === "top" ? Math.min(top, bottom) + 9 : box.text_valign === "bottom" ? Math.max(top, bottom) - 9 : (top + bottom) / 2;
      context.fillText(box.text, x, y);
    }
  }

  private polyline(context: CanvasRenderingContext2D, polyline: PinePolyline) {
    const points = polyline.points.map(point => [this.x(polyline.xloc === "bar_time" ? point.time : point.index, polyline.xloc), this.y(point.price)]);
    if (points.length < 2 || points.some(([x, y]) => x === null || y === null)) return;
    context.beginPath();
    points.forEach(([x, y], index) => index ? context.lineTo(x!, y!) : context.moveTo(x!, y!));
    if (polyline.closed) context.closePath();
    const fill = pineColor(polyline.fill_color), stroke = pineColor(polyline.line_color);
    if (fill) { context.fillStyle = fill; context.fill(); }
    if (stroke) { context.strokeStyle = stroke; context.lineWidth = polyline.line_width; context.setLineDash(DASH[polyline.line_style] ?? []); context.stroke(); context.setLineDash([]); }
  }

  private label(context: CanvasRenderingContext2D, label: PineLabel) {
    const x = this.x(label.x, label.xloc);
    if (x === null || label.y === null) return;
    const index = label.xloc === "bar_time" ? Math.round(this.timeMap().toLogical(label.x!) ?? -1) : label.x!;
    const price = label.yloc === "abovebar" ? this.barPrice(index, "high") : label.yloc === "belowbar" ? this.barPrice(index, "low") : label.y;
    const y = this.y(price);
    if (y === null) return;
    const size = TEXT_SIZES[label.size] ?? 12, background = pineColor(label.color), text = pineColor(label.textcolor) ?? tokenValue("--tx-user-palette-1");
    context.font = `${size}px ${this.font}`;
    const lines = label.text.split("\n"), textWidth = Math.max(...lines.map(line => context.measureText(line).width));
    const boxWidth = textWidth + 12, boxHeight = lines.length * (size + 3) + 6;
    const style = label.style.replace(/^label_/, "");
    const direction = style === "up" || label.yloc === "belowbar" ? 1 : style === "down" || label.yloc === "abovebar" ? -1 : 0;
    let left = x - boxWidth / 2, top = y - boxHeight / 2;
    if (style === "left") left = x + 6; else if (style === "right") left = x - boxWidth - 6;
    else if (direction !== 0) top = direction > 0 ? y + 6 : y - 6 - boxHeight;
    if (background && style !== "none" && style !== "text_outline") {
      context.fillStyle = background; context.beginPath();
      context.roundRect(left, top, boxWidth, boxHeight, 3); context.fill();
      if (direction !== 0 && style !== "center") { context.beginPath(); context.moveTo(x, y); context.lineTo(x - 5, direction > 0 ? top : top + boxHeight); context.lineTo(x + 5, direction > 0 ? top : top + boxHeight); context.closePath(); context.fill(); }
    }
    context.fillStyle = text; context.textAlign = label.textalign === "left" ? "left" : label.textalign === "right" ? "right" : "center";
    const textX = context.textAlign === "left" ? left + 6 : context.textAlign === "right" ? left + boxWidth - 6 : left + boxWidth / 2;
    lines.forEach((line, row) => context.fillText(line, textX, top + 3 + (size + 3) * row + (size + 3) / 2));
  }

  private barPrice(index: number, field: "high" | "low"): number | null {
    const data = this.series.dataByIndex(index) as { high?: number; low?: number; value?: number } | null;
    return data ? (data[field] ?? data.value ?? null) : null;
  }

  private table(context: CanvasRenderingContext2D, table: PineTable, size: { width: number; height: number }) {
    context.font = `12px ${this.font}`;
    const widths = Array.from({ length: table.columns }, () => 0), height = 20;
    for (const cell of table.cells) widths[cell.col] = Math.max(widths[cell.col], context.measureText(cell.text).width + 12);
    const total = widths.reduce((sum, width) => sum + width, 0), tall = table.rows * height;
    const [vertical, horizontal] = table.position.split("_");
    const left = horizontal === "left" ? 8 : horizontal === "right" ? size.width - total - 8 : (size.width - total) / 2;
    const top = vertical === "top" ? 8 : vertical === "bottom" ? size.height - tall - 8 : (size.height - tall) / 2;
    const background = pineColor(table.bgcolor);
    if (background) { context.fillStyle = background; context.fillRect(left, top, total, tall); }
    const offsets = widths.map((_, column) => widths.slice(0, column).reduce((sum, width) => sum + width, 0));
    for (const cell of table.cells) {
      const merge = cell.merge;
      if (merge && (merge[0] !== cell.col || merge[1] !== cell.row)) continue;
      const cellWidth = merge ? widths.slice(merge[0], merge[2] + 1).reduce((sum, width) => sum + width, 0) : widths[cell.col];
      const cellHeight = merge ? (merge[3] - merge[1] + 1) * height : height;
      const x = left + offsets[cell.col], y = top + cell.row * height, fill = pineColor(cell.bgcolor);
      if (fill) { context.fillStyle = fill; context.fillRect(x, y, cellWidth, cellHeight); }
      context.fillStyle = pineColor(cell.text_color) ?? tokenValue("--tx-user-palette-1");
      context.textAlign = cell.text_halign === "left" ? "left" : cell.text_halign === "right" ? "right" : "center";
      context.fillText(cell.text, context.textAlign === "left" ? x + 6 : context.textAlign === "right" ? x + cellWidth - 6 : x + cellWidth / 2, y + cellHeight / 2);
    }
    const border = pineColor(table.frame_color);
    if (border && table.frame_width > 0) { context.strokeStyle = border; context.lineWidth = table.frame_width; context.strokeRect(left + 0.5, top + 0.5, total, tall); }
  }
}
