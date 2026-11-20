/* The look of a drawn Plotly figure beyond colours: the hover read-out, which figures zoom, and where end labels sit. Pure functions so they can be tested;
   Plot in workbench.tsx wires them to the library's events. Nothing here knows a module: it works from the figure's own templates and axes. */

import { paintFigure } from "./paint";

const MINUS = "−";
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** One value by a d3-style spec (`.1%`, `.3f`, `,.0f`, `+.2f`, `.2e`); a spec this cannot read gives the value as it is. */
export function formatSpec(spec: string | undefined, value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  if (typeof value !== "number") return String(value);
  if (!Number.isFinite(value)) return "";
  const match = /^([+\- ])?(,)?(?:\.(\d+))?([f%edg])?$/.exec(spec ?? "");
  if (!spec || !match) return plain(value);
  const [, sign, group, digitsText, kind] = match;
  const digits = digitsText === undefined ? undefined : Number(digitsText);
  const scaled = kind === "%" ? value * 100 : value;
  let body: string;
  if (kind === "e") body = Math.abs(scaled).toExponential(digits ?? 6);
  else if (kind === "d") body = Math.abs(Math.round(scaled)).toString();
  else if (kind === "g") body = String(Number(Math.abs(scaled).toPrecision(digits ?? 6)));
  else body = Math.abs(scaled).toFixed(digits ?? (kind === "f" || kind === "%" ? 6 : 12));
  if (group) { const [whole, rest] = body.split("."); body = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",") + (rest ? `.${rest}` : ""); }
  if (kind === "%") body += "%";
  const rounded = Number(body.replace(/[,%]/g, "")) === 0;
  const prefix = scaled < 0 && !rounded ? MINUS : sign === "+" && scaled > 0 ? "+" : sign === " " && scaled >= 0 ? " " : "";
  return prefix + body;
}
const plain = (value: number) => {
  const text = Number.isInteger(value) ? String(Math.abs(value)) : String(Number(Math.abs(value).toPrecision(5)));
  return (value < 0 ? MINUS : "") + text;
};

/** `2020-03-04` (or with a time) as `Mar 4, 2020`; anything else as it is. */
export function dateText(value: unknown): string {
  const text = String(value);
  const found = /^(\d{4})-(\d{2})-(\d{2})/.exec(text);
  return found ? `${MONTHS[Number(found[2]) - 1]} ${Number(found[3])}, ${found[1]}` : text;
}

export interface HoverRow { colour: string; dashed: boolean; label: string; value: string; weak: boolean; number: number | null }
export interface HoverModel { title: string; rows: HoverRow[] }

/** What a hovered point carries, as Plotly's hover event gives it (the parts this reads). */
export interface HoverPoint {
  x: unknown; y: unknown; z?: unknown; pointIndex?: number; pointNumber?: number;
  data: Record<string, any>; fullData?: Record<string, any>;
}

/** `%{customdata}` or `%{customdata[i]}` of a point, as Plotly reads it: the trace's customdata at the point's index. */
function customOf(point: HoverPoint, name: string): unknown {
  const all = point.data.customdata as unknown[] | undefined;
  const row = all?.[point.pointIndex ?? point.pointNumber ?? 0];
  const at = /\[(\d+)\]/.exec(name);
  return at ? (row as unknown[] | undefined)?.[Number(at[1])] : row;
}

/** `%{text}` of a point, as Plotly reads it: the trace's text at the point's index, or its one text for every point. */
function textOf(point: HoverPoint): unknown {
  const text = point.data.text as unknown;
  return Array.isArray(text) ? text[point.pointIndex ?? point.pointNumber ?? 0] : text;
}

const fill = (template: string, point: HoverPoint, dates: boolean): string => template.replace(/%\{(x|y|z|text|customdata(?:\[\d+\])?)(?::([^}]*))?\}/g, (_, name: string, spec?: string) => {
  const value = name.startsWith("customdata") ? customOf(point, name) : name === "text" ? textOf(point) : point[name as "x" | "y" | "z"];
  return spec ? formatSpec(spec, value) : name === "x" && dates ? dateText(value) : typeof value === "number" ? plain(value) : String(value ?? "");
});

/** The tick format of a trace's value axis: the figure's own words for how this number reads. */
function axisFormat(layout: Record<string, any>, trace: Record<string, any>, horizontal: boolean): string | undefined {
  const name = horizontal ? trace.xaxis : trace.yaxis;
  const key = (horizontal ? "xaxis" : "yaxis") + (typeof name === "string" && name.length > 1 ? name.slice(1) : "");
  return (layout[key] as { tickformat?: string } | undefined)?.tickformat;
}

/** The read-out of the points under the cursor: a title (the date or category), then one row per point with its colour, name and formatted number.
    Lines are listed from the highest number down; bars keep the figure's order. A row's number reads by its own `hovertemplate`, else the template
    of another trace on the same axis, else the axis's `tickformat`, else as it is. */
export function hoverModel(points: HoverPoint[], figure: { data?: unknown[]; layout?: Record<string, unknown> }, token?: (name: string) => string): HoverModel | null {
  const layout = (figure.layout ?? {}) as Record<string, any>;
  const traces = (figure.data ?? []) as Record<string, any>[];
  // A point's trace as the figure was built has no colours yet: its row takes the colour the chart paints that trace with.
  const painted = token ? (paintFigure(figure, token).data ?? []) as Record<string, any>[] : null;
  const paintedOf = (data: Record<string, any>) => {
    const at = painted ? traces.indexOf(data) : -1;
    return at >= 0 && painted ? painted[at] : data;
  };
  const rows: (HoverRow & { line: boolean })[] = [];
  let title = "";
  const sibling = (trace: Record<string, any>) => traces.find(other => typeof other.hovertemplate === "string" && other.yaxis === trace.yaxis && other.xaxis === trace.xaxis && /%\{[xyz]:[^}]+\}/.test(other.hovertemplate));
  for (const point of points) {
    const data = point.data;
    const type = data.type ?? "scatter";
    if (data.hoverinfo === "skip") continue;
    const horizontal = type === "bar" && data.orientation === "h";
    const dates = type !== "heatmap" && (layout.xaxis?.type === "date" || (typeof point.x === "string" && /^\d{4}-\d{2}-\d{2}/.test(point.x) && layout.xaxis?.type !== "category"));
    const raw = typeof data.hovertemplate === "string" ? data.hovertemplate : null;
    const template = raw ?? sibling(data)?.hovertemplate ?? null;
    const valueName = type === "heatmap" ? "z" : horizontal ? "x" : "y";
    const colour = markerColour(paintedOf(data), point);
    const dashed = Boolean(paintedOf(data).line?.dash && paintedOf(data).line.dash !== "solid");
    const weak = data.meta?.role === "compare";
    const number = typeof point[valueName] === "number" ? (point[valueName] as number) : null;
    const lines: string[] = template ? template.replace(/<extra>[\s\S]*?<\/extra>/g, "").split(/<br\s*\/?>/) : [];
    // A line that is only a `%{customdata}` (no name before it) beside a measured one is the title (a window's "Window 5 · test Aug – Oct"), not a row.
    const bare = (line: string) => /^(<\/?(?:b|i|strong|em)>)*%\{customdata(\[\d+\])?\}(<\/?(?:b|i|strong|em)>)*$/.test(line.trim());
    const valued = (line: string) => new RegExp(`%\\{${valueName}(:[^}]*)?\\}`).test(line);
    const measures = lines.filter(line => valued(line) || /%\{customdata/.test(line) && !(bare(line) && lines.some(valued)) || /%\{[xyz]:[^}]+\}/.test(line) && type === "scatter" && data.mode !== "lines" && lines.length > 1);
    const heads = lines.filter(line => !measures.includes(line));
    const own = raw !== null;
    if (own && heads.length && !title) title = heads.map(line => fill(line.replace(/<\/?(?:b|i|strong|em)>/g, ""), point, dates).replace(new RegExp(`\\s*${String(data.name ?? "\u0000").replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`), "").trim()).filter(Boolean).join(" · ");
    if (measures.length > 1) {
      for (const line of measures) {
        const cut = line.search(/%\{/);
        rows.push({ colour, dashed, weak, number, line: false, label: line.slice(0, cut).trim(), value: fill(line.slice(cut), point, dates).trim() });
      }
      continue;
    }
    let value: string;
    if (measures.length === 1) {
      const line = measures[0], cut = line.search(new RegExp(`%\\{(?:${valueName}|customdata)`));
      value = fill(line.slice(Math.max(0, cut)), point, dates).trim();
    } else if (template && !own) {
      const spec = new RegExp(`%\\{${valueName}:([^}]*)\\}`).exec(template)?.[1] ?? /%\{[xyz]:([^}]*)\}/.exec(template)?.[1];
      value = formatSpec(spec ?? axisFormat(layout, data, horizontal), point[valueName]);
    } else value = formatSpec(axisFormat(layout, data, horizontal), point[valueName]);
    const prefix = measures.length === 1 ? measures[0].slice(0, Math.max(0, measures[0].search(/%\{/))).trim().replace(/\s*:$/, "") : "";
    rows.push({ colour, dashed, weak, number, line: type === "scatter", label: (own ? prefix : "") || (typeof data.name === "string" && data.name ? data.name : prefix), value });
  }
  if (!rows.length) return null;
  if (!title) {
    const first = points[0];
    const horizontal = first.data.type === "bar" && first.data.orientation === "h";
    const at = horizontal ? first.y : first.x;
    title = first.data.type === "heatmap" ? `${dateText(first.x)} ${first.y}` : dateText(at);
    if (typeof at === "number" && !horizontal) title = plain(at);
  }
  if (rows.length > 1 && rows.every(row => row.line)) rows.sort((a, b) => (b.number ?? -Infinity) - (a.number ?? -Infinity));
  return { title, rows: rows.map(({ line: _line, ...row }) => row) };
}

function markerColour(data: Record<string, any>, point: HoverPoint): string {
  const marker = data.marker?.color;
  const index = point.pointIndex ?? point.pointNumber ?? 0;
  if (Array.isArray(marker)) return String(marker[index] ?? marker[0] ?? "");
  const type = data.type ?? "scatter";
  const fromLine = data.line?.color;
  if (type === "scatter" && fromLine) return String(fromLine);
  return String(marker ?? fromLine ?? "");
}

/** Only a figure on a time axis zooms and pans (wheel, drag, double click to reset): bars on categories, histograms, heatmaps and number axes stay as drawn. */
export function isZoomable(figure: { data?: unknown[]; layout?: Record<string, unknown> }): boolean {
  const traces = (figure.data ?? []) as Record<string, any>[];
  if (!traces.length) return false;
  const layout = (figure.layout ?? {}) as Record<string, any>;
  const axisType = layout.xaxis?.type;
  if (axisType === "category" || axisType === "linear" || axisType === "log") return false;
  if (traces.some(trace => ["heatmap", "violin", "box", "histogram", "pie", "scatter3d", "surface"].includes(trace.type ?? "")) ) return false;
  if (traces.some(trace => trace.type === "bar" && trace.orientation === "h")) return false;
  if (axisType === "date") return true;
  return traces.some(trace => Array.isArray(trace.x) && typeof trace.x[0] === "string" && /^\d{4}-\d{2}-\d{2}/.test(trace.x[0]));
}

/** Keeps end labels (`positions` are pixel heights, top down) at least `gap` apart, moving them as little as it can; returns the pixel shift of each, in the input's order.
    More labels than fit between `top` and `bottom` stand out past both evenly rather than on each other. */
export function spreadLabels(positions: number[], gap: number, top: number, bottom: number): number[] {
  const need = gap * (positions.length - 1);
  if (need > bottom - top) { const middle = (top + bottom) / 2; top = middle - need / 2; bottom = middle + need / 2; }
  const order = positions.map((position, index) => ({ position, index })).sort((a, b) => a.position - b.position);
  const place = order.map(item => item.position);
  for (let pass = 0; pass < 24; pass++) {
    let moved = false;
    for (let at = 1; at < place.length; at++) {
      const overlap = gap - (place[at] - place[at - 1]);
      if (overlap > 0.5) { place[at - 1] -= overlap / 2; place[at] += overlap / 2; moved = true; }
    }
    if (place.length && place[0] < top) { place[0] = top; moved = true; }
    if (place.length && place[place.length - 1] > bottom) { place[place.length - 1] = bottom; moved = true; }
    if (!moved) break;
  }
  // Whatever the passes left too close is pushed down, then back up from the bottom: no two labels ever sit closer than the gap.
  for (let at = 1; at < place.length; at++) place[at] = Math.max(place[at], place[at - 1] + gap);
  for (let at = place.length - 1; at >= 0 && place[at] > (at === place.length - 1 ? bottom : place[at + 1] - gap); at--) place[at] = at === place.length - 1 ? bottom : place[at + 1] - gap;
  const shift = new Array<number>(positions.length).fill(0);
  order.forEach((item, at) => { shift[item.index] = place[at] - item.position; });
  return shift;
}


/** The crosshair of a chart on a time axis: one thin, muted vertical line that follows the cursor. */
export function crosshairLook(token: (name: string) => string) {
  return { showspikes: true, spikemode: "across", spikesnap: "cursor", spikethickness: 1, spikecolor: token("--tx-text-4"), spikedash: "solid" } as const;
}

/** A small mark on each line at the day under the cursor, in the line's own colour (lines only: bars and cells light up on their own). `figure` is the painted one. */
export function hoverMarks(points: HoverPoint[], figure: { data?: unknown[] }): { x: unknown; y: unknown; colour: string }[] {
  const out: { x: unknown; y: unknown; colour: string }[] = [];
  for (const point of points) {
    const data = point.data;
    const type = data.type ?? "scatter";
    if (type !== "scatter" || data.hoverinfo === "skip" || !String(data.mode ?? "lines").includes("lines") || typeof point.y !== "number") continue;
    if (!(figure.data ?? []).includes(data) && !data.line) continue;
    out.push({ x: point.x, y: point.y, colour: String(data.line?.color ?? data.marker?.color ?? "") });
  }
  return out;
}

/** A dense line (an hourly series over years) thinned to about `budget` points for drawing: each bucket keeps its first, lowest, highest and last point,
    so peaks and troughs stay where they are and the line reads crisp rather than as a band. Arrays that run along the points (customdata, text) are
    thinned with them, so the read-out of a kept point is its own. Bars, markers and short lines are left as they are. */
export function thinLine<T extends Record<string, any>>(trace: T, budget = 1600): T {
  const xs = trace.x, ys = trace.y;
  if ((trace.type ?? "scatter") !== "scatter" || !String(trace.mode ?? "lines").includes("lines") || String(trace.mode ?? "").includes("markers")) return trace;
  if (!Array.isArray(xs) || !Array.isArray(ys) || ys.length <= budget * 1.5 || xs.length !== ys.length) return trace;
  const buckets = Math.max(2, Math.floor(budget / 4)), size = ys.length / buckets;
  const keep: number[] = [];
  for (let bucket = 0; bucket < buckets; bucket++) {
    const from = Math.floor(bucket * size), to = Math.min(ys.length, Math.floor((bucket + 1) * size));
    if (to <= from) continue;
    let low = -1, high = -1;
    for (let at = from; at < to; at++) {
      const value = ys[at];
      if (typeof value !== "number") continue;
      if (low < 0 || value < ys[low]) low = at;
      if (high < 0 || value > ys[high]) high = at;
    }
    for (const at of [...new Set([from, low, high, to - 1])].filter(at => at >= 0).sort((a, b) => a - b)) keep.push(at);
  }
  const pick = (values: unknown) => Array.isArray(values) && values.length === ys.length ? keep.map(at => values[at]) : values;
  return { ...trace, x: pick(xs), y: pick(ys), customdata: pick(trace.customdata), text: pick(trace.text), hovertext: pick(trace.hovertext) } as T;
}

/** A figure's layout without what the draft never draws inside a chart: the Plotly legend (the key sits beside the section's title) and axis titles
    (the section's title says what is drawn). */
export function plainLayout(layout: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = { ...layout, showlegend: false };
  for (const key of Object.keys(out).filter(name => /^[xy]axis\d*$/.test(name))) {
    const axis = out[key];
    if (axis && typeof axis === "object" && "title" in axis) { const { title: _title, ...rest } = axis as Record<string, unknown>; out[key] = rest; }
  }
  return out;
}

/** What the axes take from the figure's own read-out and data, as the draft writes them: a value axis whose every read-out puts `%` after the number
    reads with `%` too; whole-number rows or columns of a heatmap (years) are labelled once each, never `2,024.5`; bars on a few categories stay narrow
    (at most about 18px a bar) instead of filling the width, and bars lying along a few categories stay as thin for the height. Returns the layout keys to merge. */
export function axisUnits(layout: Record<string, any>, traces: Record<string, any>[], width: number, height = 0): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  const read = traces.filter(trace => typeof trace.hovertemplate === "string" && trace.hoverinfo !== "skip");
  for (const [axis, name] of [["yaxis", "y"], ["xaxis", "x"]] as const) {
    const own = (layout[axis] ?? {}) as Record<string, unknown>;
    if (own.ticksuffix || own.ticktext || (typeof own.tickformat === "string" && own.tickformat.includes("%"))) continue;
    if (read.length && read.every(trace => new RegExp(`%\\{${name}(:[^}]*)?\\}%`).test(trace.hovertemplate))) out[axis] = { ...own, ...(out[axis] as object ?? {}), ticksuffix: "%" };
  }
  for (const trace of traces.filter(item => item.type === "heatmap")) {
    for (const [axis, name] of [["yaxis", "y"], ["xaxis", "x"]] as const) {
      const values = trace[name];
      if (Array.isArray(values) && values.length && values.every((value: unknown) => (typeof value === "number" && Number.isInteger(value)) || (typeof value === "string" && /^\d{4}$/.test(value)))) out[axis] = { ...(layout[axis] ?? {}), ...(out[axis] as object ?? {}), dtick: 1, tickformat: "d" };
    }
  }
  const group = (count: number) => layout.barmode === "overlay" || layout.barmode === "stack" || layout.barmode === "relative" ? 1 : count;
  const bars = traces.filter(trace => trace.type === "bar" && trace.orientation !== "h" && Array.isArray(trace.x));
  if (bars.length && width > 0 && layout.bargap === undefined) {
    const columns = new Set(bars.flatMap(trace => trace.x as unknown[])).size;
    const step = (width * .9) / Math.max(1, columns), wanted = Math.min(step * .7, 18 * group(bars.length));
    if (columns <= 40) out.bargap = Math.max(.15, Math.min(.96, 1 - wanted / step));
  }
  const lying = traces.filter(trace => trace.type === "bar" && trace.orientation === "h" && Array.isArray(trace.y));
  if (lying.length && height > 0 && layout.bargap === undefined) {
    const rows = new Set(lying.flatMap(trace => trace.y as unknown[])).size;
    const step = (height * .85) / Math.max(1, rows), wanted = Math.min(step * .7, 16 * group(lying.length));
    out.bargap = Math.max(.15, Math.min(.9, 1 - wanted / step));
  }
  return out;
}
