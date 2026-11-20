import { tokenValue } from "@talyx/sdk";

/* How a figure's traces are painted by what they are (`meta.role`), shared by the chart and by its read-out so a row's colour is its line's colour. */

export interface PlotFigure { data?: unknown[]; layout?: Record<string, unknown> }

/**
 * What a trace is, set by whoever built the figure in `trace.meta.role`; the display colours it by that, not by the library's palette. A line is told apart by
 * its colour, its width and the name at its end, never by dots or dashes (those stay for straight reference lines). Four colours are told apart from the
 * white main line and from each other, also by colour-blind eyes: blue, a dark orange, purple and grey.
 * main: the text colour, the widest line; benchmark: the first blue, the second dark orange, the third purple, any further grey (one without hover is a
 * reference: zero, a mean, a faint dotted line); compare (another run): grey; earlier, the same thing at an earlier moment drawn beside it (as it was
 * published), a dim grey; group, ordered groups (`meta.index` of `meta.of`): as lines cold to warm from the lowest to the highest, the two ends wider, as
 * bars, boxes and heatmap cells (ordered steps such as 1D, 5D, 10D, a size) one blue from deep to light whose deepest end still reads on the panel;
 * watch amber (worth a look, nothing to do yet); part one part of a whole (the groups of an allocation, stacked): the data colour `meta.index` names in
 * `SERIES`, shaded by `meta.shade`; updown the up and down colours by the sign of each value (the user's up colour) or, with `meta.side` "up" or
 * "down", the one colour for the whole trace; drawdown the fall from the last high (underwater): the down colour, a thin line over a faint area of it down
 * from zero; attention orange; detail the text colour at 35% and a thin line: the day-by-day values behind a line (its mean).
 */
export type PlotRole = "main" | "benchmark" | "compare" | "earlier" | "group" | "part" | "updown" | "drawdown" | "attention" | "watch" | "detail";

/** The data colours (`--tx-series-1…`) in order: the parts of one whole (the draft's allocation groups) by `meta.index`, and the lines told apart as series. */
export const SERIES = ["--tx-series-1", "--tx-series-2", "--tx-series-3", "--tx-series-4", "--tx-series-5"] as const;
/** The colour of one part, by `meta.index`; `meta.shade` (-1 to 1) lightens or darkens it to tell apart the members of a group. */
export function partColour(index: number, shade = 0): string {
  const base = tokenValue(SERIES[((Math.round(index) % SERIES.length) + SERIES.length) % SERIES.length]);
  if (!shade) return base;
  const [r, g, b] = rgba(base)!;
  const move = (value: number) => shade < 0 ? value * (1 + shade) : value + (255 - value) * shade;
  return css([move(r), move(g), move(b), 1]);
}

type Rgba = [number, number, number, number];
function rgba(color: string): Rgba | null {
  const value = color.trim();
  const hex = /^#([0-9a-f]{3,8})$/i.exec(value);
  if (hex) {
    const digits = hex[1].length <= 4 ? hex[1].split("").map(part => part + part).join("") : hex[1];
    const parts = digits.match(/../g)!.map(part => parseInt(part, 16));
    return [parts[0], parts[1], parts[2], parts.length > 3 ? parts[3] / 255 : 1];
  }
  const fn = /^rgba?\(([^)]+)\)$/i.exec(value);
  if (fn) {
    const parts = fn[1].split(/[\s,/]+/).filter(Boolean).map(Number);
    return [parts[0], parts[1], parts[2], parts.length > 3 ? parts[3] : 1];
  }
  return null;
}
function luminance([r, g, b]: Rgba): number {
  const channel = (value: number) => { const v = value / 255; return v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; };
  return .2126 * channel(r) + .7152 * channel(g) + .0722 * channel(b);
}
/** The WCAG contrast of two opaque colours. */
function contrast(a: string, b: string): number {
  const x = rgba(a), y = rgba(b);
  if (!x || !y) return 21;
  const [light, dark] = [luminance(x), luminance(y)].sort((p, q) => q - p);
  return (light + .05) / (dark + .05);
}
const css = ([r, g, b, a]: Rgba) => a >= 1 ? `rgb(${Math.round(r)}, ${Math.round(g)}, ${Math.round(b)})` : `rgba(${Math.round(r)}, ${Math.round(g)}, ${Math.round(b)}, ${a})`;
function mix(from: string, to: string, share: number): string {
  const a = rgba(from), b = rgba(to);
  if (!a || !b) return share < .5 ? from : to;
  return css(a.map((value, index) => value + (b[index] - value) * share) as Rgba);
}
function fade(color: string, alpha: number): string {
  const value = rgba(color);
  return value ? css([value[0], value[1], value[2], alpha]) : color;
}


type Trace = Record<string, unknown> & { type?: string; mode?: string; name?: unknown; hoverinfo?: unknown; fill?: unknown; showlegend?: unknown; meta?: { role?: PlotRole; side?: "up" | "down"; key?: string; index?: number; of?: number; shade?: number; pad?: boolean; inferred?: boolean }; x?: unknown[]; y?: unknown[]; z?: unknown };

/** Line widths by role: the main line reads first; the lines beside it are thinner, the groups between the two ends and the day-by-day detail thinner still. */
export const LINE_WIDTH = { main: 2, benchmark: 1.5, compare: 1.5, group: 1.5, middle: 1, detail: .7, reference: 1, attention: 1.5, series: 1.5, underwater: 1.2 } as const;

/** The colours a line can have beside the white main one, in order (blue, dark orange, purple, grey), as tokens. */
export const LINE_COLOURS = ["--tx-series-1", "--tx-chart-warm", "--tx-user-palette-13", "--tx-user-palette-3"] as const;
/** Ordered groups drawn as lines, cold to warm from the lowest to the highest (stops of one ramp, as tokens). */
export const ORDER_COLOURS = ["--tx-chart-order-1", "--tx-chart-order-2", "--tx-chart-order-3", "--tx-chart-order-4"] as const;

/** A line's name written at its end: a short stroke in the line's colour, then the name in the text colour. */
export function endLabel(name: string, colour: string): string {
  return `<span style="color:${colour}">\u2501</span> ${name}`;
}

const isLine = (trace: Trace) => (trace.type ?? "scatter") === "scatter" && String(trace.mode ?? "lines").includes("lines");
const numbers = (values: unknown): number[] => Array.isArray(values) ? (values as unknown[]).flat(2).filter((value): value is number => typeof value === "number" && Number.isFinite(value)) : [];

/**
 * The role of a trace the backend left unmarked, so no trace is drawn in a library's own colours: a heatmap with values on both sides of zero reads up and
 * down, else as a ramp; a line without hover is a reference; a lone bar series with both signs reads up and down; otherwise the first unmarked trace is the
 * main one and the others are told apart as series.
 */
function inferredRole(trace: Trace, order: number): PlotRole | "series" {
  const kind = trace.type ?? "scatter";
  if (kind === "heatmap") { const values = numbers(trace.z); return values.some(value => value < 0) && values.some(value => value > 0) ? "updown" : "group"; }
  if (trace.hoverinfo === "skip" && isLine(trace)) return "benchmark";
  if (kind === "bar" && order === 0) { const values = numbers(trace.y); if (values.some(value => value < 0) && values.some(value => value > 0)) return "updown"; }
  return order === 0 ? "main" : "series";
}

/** The figure with every trace coloured by its role: the one the backend marked in `meta.role`, or one inferred for a trace it left unmarked
    (`meta.inferred` says so). The figure's own colours and widths never decide the look. */
/** A line, dots or bars with no point that has both its x and y: it draws nothing, so it is left out and the key names only what is drawn. */
export const drawsNothing = (raw: unknown): boolean => {
  const trace = raw as { type?: string; x?: unknown; y?: unknown };
  return ["scatter", "scattergl", "bar"].includes(trace.type ?? "scatter") && Array.isArray(trace.x) && Array.isArray(trace.y)
    && !(trace.x as unknown[]).some((x, at) => x != null && (trace.y as unknown[])[at] != null);
};

export function paintFigure(figure: PlotFigure, token: (name: string) => string): PlotFigure {
  if ((figure.data ?? []).some(drawsNothing)) figure = { ...figure, data: (figure.data ?? []).filter(raw => !drawsNothing(raw)) };
  const text = token("--tx-text"), text2 = token("--tx-text-2"), text3 = token("--tx-text-3"), text4 = token("--tx-text-4"), up = token("--tx-up"), down = token("--tx-down");
  const ember = token("--tx-ember"), watch = token("--tx-watch"), card = token("--tx-card");
  const others = LINE_COLOURS.map(name => token(name));
  const [blue, , , grey] = others;
  const ladder = ORDER_COLOURS.map(name => token(name));
  // One blue from deep to light, through the blue itself: the deep end the blue toward the panel as far as it still reads at a contrast of 3 there,
  // the light end the blue 40% of the way to the text colour. Every step stays a blue, so 1D, 5D and 10D read as one thing growing, not as greys.
  let low = mix(blue, card, .45);
  for (let share = .4; contrast(low, card) < 3 && share > 0; share -= .05) low = mix(blue, card, share);
  const high = mix(blue, text, .4);
  const ramp = (share: number) => { const at = Math.max(0, Math.min(1, share)); return at < .5 ? mix(low, blue, at * 2) : mix(blue, high, (at - .5) * 2); };
  const step = (meta: Trace["meta"]) => meta && typeof meta.index === "number" && typeof meta.of === "number" && meta.of > 1 ? meta.index / (meta.of - 1) : .5;
  /** An ordered group drawn as a line: its place on the cold-to-warm ramp, the lowest group at the cold end. */
  const coldToWarm = (share: number) => {
    const at = Math.max(0, Math.min(1, share)) * (ladder.length - 1), from = Math.min(ladder.length - 2, Math.floor(at));
    return mix(ladder[from], ladder[from + 1], at - from);
  };
  /** A group with a place among others (`meta.index` of `meta.of`); one without is a thin grey line. */
  const ranked = (meta: Trace["meta"]) => Boolean(meta && typeof meta.index === "number" && typeof meta.of === "number" && meta.of > 1);
  const end = (meta: Trace["meta"]) => ranked(meta) && (meta!.index! <= 0 || meta!.index! >= meta!.of! - 1);
  const colours = new Map<string, string>();
  let unmarked = 0, benchmarks = 0;
  const stacked = ["stack", "relative"].includes(String(figure.layout?.barmode ?? ""));
  // A figure whose one read line is the main one (an equity, a cumulative long-short) lays a faint wash under it, down to zero: the line reads as a level.
  const readLines = (figure.data ?? []).filter(raw => { const trace = raw as Trace; return isLine(trace) && !trace.meta?.pad && trace.hoverinfo !== "skip" && trace.meta?.role !== "detail"; }) as Trace[];
  // Only for a level that stays on one side of zero (an equity, a cumulative return): under a line swinging about zero (a rolling Sharpe) it would mislead.
  const levels = readLines.length === 1 ? numbers(readLines[0].y) : [];
  const oneSide = levels.length > 0 && (levels.filter(value => value < 0).length <= levels.length * .15 || levels.filter(value => value > 0).length <= levels.length * .15);
  const alone = oneSide && readLines.length === 1 && (readLines[0].meta?.role ?? "main") === "main" && !(readLines[0].fill && readLines[0].fill !== "none")
    && (figure.data ?? []).every(raw => isLine(raw as Trace) || (raw as Trace).meta?.pad);
  const data = (figure.data ?? []).map(raw => {
    const trace = raw as Trace;
    if (trace.meta?.pad) return trace;
    const order = trace.meta?.role ? -1 : unmarked++;
    const role = trace.meta?.role ?? inferredRole(trace, order);
    const out: Trace = { ...trace };
    const line = { ...(trace.line as object ?? {}) } as Record<string, unknown>;
    const marker = { ...(trace.marker as object ?? {}) } as Record<string, unknown>;
    const kind = trace.type ?? "scatter";
    const bars = kind === "bar" || kind === "histogram" || kind === "waterfall";
    out.meta = { ...(trace.meta ?? {}), role: role === "series" ? "group" : role, ...(trace.meta?.role ? {} : { inferred: true }) };
    if (kind === "heatmap") {
      // The ends are the up and down colours toned toward the panel, so the cells' numbers read in the text colour on every cell.
      out.colorscale = role === "updown" ? [[0, mix(card, down, .78)], [.5, card], [1, mix(card, up, .78)]] : [[0, ramp(0)], [1, ramp(1)]];
      out.textfont = { size: 11, ...(trace.textfont as object ?? {}), color: text };
      if (role === "updown") out.zmid = 0;
      out.xgap = trace.xgap ?? 2; out.ygap = trace.ygap ?? 2;   // cells sit apart on the surface, as the draft's
      out.showscale = trace.showscale ?? false;
      return out;
    }
    const reference = role === "benchmark" && trace.hoverinfo === "skip" && isLine(trace);
    const ordered = bars || kind === "box" || kind === "violin";
    let colour = text;
    if (!reference && role !== "watch" && role !== "attention") line.dash = "solid";   // a series is never dotted or dashed; a threshold keeps its own
    if (reference) { colour = fade(text, .3); line.width = LINE_WIDTH.reference; line.dash = "dot"; }
    else if (role === "benchmark") { colour = others[Math.min(benchmarks++, others.length - 1)]; line.width = LINE_WIDTH.benchmark; }
    else if (role === "compare") { colour = grey; line.width = LINE_WIDTH.compare; }
    else if (role === "earlier") { colour = text4; line.width = LINE_WIDTH.compare; }
    else if (role === "group" && ordered) { colour = ramp(step(trace.meta)); line.width = LINE_WIDTH.group; }
    else if (role === "group") { colour = ranked(trace.meta) ? coldToWarm(step(trace.meta)) : grey; line.width = end(trace.meta) ? LINE_WIDTH.group : LINE_WIDTH.middle; }
    else if (role === "drawdown") { colour = down; line.width = LINE_WIDTH.underwater; out.fill = "tozeroy"; }
    else if (role === "part") { colour = partColour(trace.meta?.index ?? 0, trace.meta?.shade ?? 0); line.width = 0; }
    else if (role === "attention") { colour = ember; line.width = LINE_WIDTH.attention; }
    else if (role === "watch") { colour = watch; line.width = LINE_WIDTH.attention; }
    else if (role === "detail") { colour = fade(text, .16); line.width = LINE_WIDTH.detail; }
    else if (role === "main") { line.width = LINE_WIDTH.main; }
    else if (role === "series") { colour = others[Math.min(order - 1, others.length - 1)]; line.width = LINE_WIDTH.series; }
    const sided = trace.meta?.role === "updown" || role === "updown" ? trace.meta?.side : undefined;
    if (role === "updown") {
      // The value is along x on a horizontal bar (categories on y), along y otherwise.
      const along = trace.orientation === "h" ? trace.x : trace.y;
      const values = Array.isArray(along) ? along : [];
      if (kind === "histogram") marker.color = text;
      else if (sided) marker.color = sided === "up" ? up : down;
      else marker.color = values.map(value => typeof value === "number" && value < 0 ? down : up);
      if (bars && marker.opacity === undefined) marker.opacity = kind === "bar" && values.length > 300 ? .5 : .72;  // a bar a day: faint, so the mean over them reads
      line.color = sided ? (sided === "up" ? up : down) : text2;
    } else {
      line.color = colour;
      // Bars are never a solid white block: the main ones are the text colour at 70%; the ones they are read against (a benchmark's, another series')
      // are their colour at half, so the main bars read first.
      marker.color = bars && role === "main" ? fade(text, .7) : bars && role === "detail" ? fade(text, .18) : bars && (kind === "histogram" || role === "benchmark" || role === "series") ? fade(colour, kind === "histogram" ? .6 : .5) : colour;
    }
    if (bars) marker.line = { ...(marker.line as object ?? {}), width: 0 };
    // Bars a reader counts one by one have softly rounded ends; a bar a day stays square, as a texture.
    const count = Array.isArray(trace.orientation === "h" ? trace.y : trace.x) ? (trace.orientation === "h" ? trace.y : trace.x)!.length : 0;
    if (kind === "bar" && !stacked && count > 0 && count <= 60) marker.cornerradius = 3;
    if (alone && trace === readLines[0]) { out.fill = "tozeroy"; out.fillgradient = { type: "vertical", colorscale: [[0, fade(text, 0)], [1, fade(text, .1)]] }; }
    // A violin is its colour filled at a third, its outline and the box inside in the colour itself.
    if (kind === "violin") { out.fillcolor = fade(colour, .3); line.width = 1; }
    out.line = line;
    out.marker = marker;
    if (role === "part" && isLine(trace) && !trace.fill) out.fill = "tonexty";   // a stacked part is its band
    if (out.fill && out.fill !== "none") out.fillcolor = fade(role === "updown" ? (sided ? (sided === "up" ? up : down) : text) : colour, role === "part" ? .82 : role === "drawdown" ? .14 : role === "group" ? (ranked(trace.meta) ? .14 : .08) : role === "detail" ? .07 : .1);
    if (kind === "bar") out.textfont = { ...(trace.textfont as object ?? {}), color: role === "main" ? text2 : text3 };
    if (trace.meta?.key) colours.set(trace.meta.key, role === "updown" ? (sided ? (sided === "up" ? up : down) : text2) : colour);
    return out;
  });
  const layout = { ...(figure.layout ?? {}) };
  // A line's end label is an annotation whose `name` is the line's key: the name in the text colour after a short stroke in the line's.
  if (Array.isArray(layout.annotations)) layout.annotations = (layout.annotations as Record<string, unknown>[]).map(item => {
    const colour = typeof item.name === "string" && item.showarrow === false ? colours.get(item.name) : undefined;
    if (colour) return { ...item, text: endLabel(String(item.text ?? ""), colour), font: { size: 11, family: token("--tx-font"), ...(item.font as object ?? {}), color: text2 } };
    return { ...item, font: { size: 10.5, family: token("--tx-font-mono"), ...(item.font as object ?? {}), color: text3 } };
  });
  if (Array.isArray(layout.shapes)) layout.shapes = (layout.shapes as Record<string, unknown>[]).map(item => {
    const role = (item.meta as { role?: string } | undefined)?.role;
    // A benchmark line (a mean, zero, the best epoch) is a reference line: thin, faint and dotted unless it says otherwise; Plotly's own grey reads as black.
    if (role === "benchmark" && item.type === "line") return { ...item, line: { color: fade(text, .3), width: LINE_WIDTH.reference, dash: "dot", ...(item.line as object ?? {}) } };
    return role === "group" ? { ...item, fillcolor: token("--tx-white-04"), line: { width: 0 } } : role === "attention" || role === "watch" ? { ...item, line: { ...(item.line as object ?? {}), color: role === "watch" ? watch : ember } } : item;
  });
  return { data, layout };
}

/** The order a painted figure's traces are drawn in: the main lines and the drawdown last, over the lines they are read against (a compared run
    with the same values would hide the main one). A figure that stacks its traces (`tonext…`) keeps its order, which is its meaning. */
export function drawOrder<T>(data: readonly T[]): T[] {
  const traces = data as unknown as Trace[];
  if (traces.some(trace => typeof trace.fill === "string" && trace.fill.startsWith("tonext"))) return [...data];
  const top = (trace: Trace) => (trace.meta?.role === "main" && isLine(trace) && !(trace.fill && trace.fill !== "none")) || trace.meta?.role === "drawdown";
  return [...data].sort((a, b) => Number(top(a as unknown as Trace)) - Number(top(b as unknown as Trace)));
}

/** One entry of a chart's key: the trace's name and how it is drawn (a line, dashed, dotted, or a box for bars and areas). */
export interface ChartKey { name: string; colour: string; mark: "line" | "dash" | "dot" | "box"; weak?: boolean }

/** The key of a painted figure, as the draft draws it beside the section title instead of a legend in the plot: every named trace that is read
    (not a reference line, not the faint day-by-day detail, not a heatmap), once per name. Fewer than two entries make no key: the title says it. */
export function figureKeys(painted: PlotFigure): ChartKey[] {
  const out: ChartKey[] = [];
  // A line whose end is labelled with its name (an annotation named by its key) needs no key entry.
  const labelled = new Set(((painted.layout?.annotations ?? []) as { name?: unknown; showarrow?: unknown }[]).filter(item => typeof item.name === "string" && item.showarrow === false).map(item => item.name as string));
  for (const raw of painted.data ?? []) {
    const trace = raw as Trace & { line?: { color?: string; dash?: string }; marker?: { color?: unknown } };
    const kind = trace.type ?? "scatter";
    const name = typeof trace.name === "string" ? trace.name.trim() : "";
    if (!name || trace.meta?.pad || trace.hoverinfo === "skip" || trace.showlegend === false || trace.meta?.role === "detail" || kind === "heatmap" || (trace.meta?.key && labelled.has(trace.meta.key)) || out.some(item => item.name === name)) continue;
    const bars = kind === "bar" || kind === "histogram" || kind === "waterfall" || kind === "violin" || kind === "box";
    const area = isLine(trace) && trace.fill && trace.fill !== "none" && (trace.meta?.role === "group" || trace.meta?.role === "part");
    // Bars coloured by sign (up and down) are keyed in the neutral line colour, not whichever sign came first.
    const markerColour = Array.isArray(trace.marker?.color) ? trace.line?.color ?? (trace.marker!.color as unknown[]).find(value => typeof value === "string") : trace.marker?.color;
    const colour = String((bars || area ? markerColour ?? trace.line?.color : trace.line?.color ?? markerColour) ?? "");
    const dash = trace.line?.dash;
    out.push({ name, colour, mark: bars || area ? "box" : dash === "dot" ? "dot" : dash && dash !== "solid" ? "dash" : "line", ...(trace.meta?.role === "compare" ? { weak: true } : {}) });
  }
  return out.length > 1 ? out : [];
}
