/* Quant's charts: a Plotly figure built by the backend, drawn on the panel's surface and read the platform's way (Quant's own: no other module draws
   Plotly). Colours come from paint.ts, the read-out and axes from look.ts, the charts of one section or tab work together through group.ts. */
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { TxSegmented } from "@talyx/sdk";
import { crosshairLook, hoverMarks, hoverModel, isZoomable, plainLayout, spreadLabels, thinLine, axisUnits, type HoverPoint } from "./look";
import { drawOrder, drawsNothing, endLabel, figureKeys, paintFigure, type ChartKey, type PlotFigure } from "./paint";
import { chartGroup, chartSection, offersLog, type ChartGroup, type ChartMember } from "./group";
import "./chart.css";

type PlotlyModule = {
  react(element: HTMLElement, data: unknown[], layout: Record<string, unknown>, config: Record<string, unknown>): Promise<unknown>;
  purge(element: HTMLElement): void;
  Plots: { resize(element: HTMLElement): void };
  relayout(element: HTMLElement, update: Record<string, unknown>): Promise<unknown>;
  restyle(element: HTMLElement, update: Record<string, unknown>, traces: number[]): Promise<unknown>;
  Fx: { hover(element: HTMLElement, event: Record<string, unknown>, subplot?: string): void; unhover(element: HTMLElement): void };
};
/* Plotly is large: it is loaded the first time a chart is shown. */
let plotlyLoad: Promise<PlotlyModule> | undefined;
const loadPlotly = () => plotlyLoad ??= import("plotly.js-dist-min").then(module => ((module as { default?: unknown }).default ?? module) as PlotlyModule);

/** A clicked point: the trace's name and `meta.key`, its x and y (and z for a heatmap cell), and its index. Dates are in `x`. */
export interface PlotPoint { trace?: string; key?: string; x: unknown; y: unknown; z?: unknown; index?: number | number[] }

export type { PlotFigure } from "./paint";

/** A chart's key as the draft draws it beside a section's title: a short line (solid, dashed or dotted) or a small box, and the name. */
function Keys({ keys }: { keys: ChartKey[] }) {
  if (!keys.length) return null;
  return <span className="qc-keys">{keys.map(key => <span key={key.name} className="qc-key" data-weak={key.weak ? "" : undefined}>
    <i data-mark={key.mark} style={key.mark === "box" ? { background: key.colour } : { borderColor: key.colour }} />{key.name}</span>)}</span>;
}

const NO_GROUP = { subscribe: () => () => undefined, snapshot: () => 0 };

/** Changes of the user's up and down colours, so drawn charts follow them. */
function useUpColour(): string {
  const [value, setValue] = useState(() => typeof document === "undefined" ? "" : document.documentElement.dataset.upColor ?? "");
  useEffect(() => {
    if (typeof MutationObserver === "undefined") return;
    const root = document.documentElement;
    const observer = new MutationObserver(() => setValue(root.dataset.upColor ?? ""));
    observer.observe(root, { attributes: true, attributeFilter: ["data-up-color"] });
    return () => observer.disconnect();
  }, []);
  return value;
}

/** Renders a native plotly figure as the library built it, on the platform's surface and type; traces that carry `meta.role`
    are coloured by it and follow the user's up and down colours. The figure fills the box it is put in (a `height` only for a box with none).
    Hovering gives the platform's own read-out; a figure on a time axis zooms with the wheel, pans by dragging and resets on a double click.
    `onSelect` gets the x range of a box selection. */
/** A time written as Plotly writes dates ("2024-01-02", "2024-01-02 13:00"), in UTC milliseconds as Plotly reads it. */
function utcTime(value: unknown): number {
  if (typeof value !== "string") return NaN;
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return Date.parse(`${value}T00:00:00Z`);
  return Date.parse(value.replace(" ", "T") + (/(Z|[+-]\d\d:?\d\d)$/.test(value) ? "" : "Z"));
}

/** The first and last moment the figure's traces have a value at: as written (to give Plotly a range) and in milliseconds; null when no x is a time.
    Days a line carries no value on (a test period that starts years into the data) are not part of it, so the plot is not left empty before them. */
function timeExtent(traces: Record<string, any>[]): { from: unknown; to: unknown; low: number; high: number } | null {
  let out: { from: unknown; to: unknown; low: number; high: number } | null = null;
  for (const trace of traces) {
    if (trace.meta?.pad || trace.hoverinfo === "skip" || !Array.isArray(trace.x)) continue;   // a reference (zero, a band) is drawn across whatever time there is
    const ys = Array.isArray(trace.y) && trace.y.length === trace.x.length ? trace.y as unknown[] : null;
    let first = 0, last = trace.x.length - 1;
    if (ys) {
      while (first <= last && typeof ys[first] !== "number") first++;
      while (last >= first && typeof ys[last] !== "number") last--;
      if (first > last) continue;
    }
    for (const value of [trace.x[first], trace.x[last]]) {
      const time = utcTime(value);
      if (!Number.isFinite(time)) continue;
      if (!out) out = { from: value, to: value, low: time, high: time };
      if (time < out.low) Object.assign(out, { from: value, low: time });
      if (time > out.high) Object.assign(out, { to: value, high: time });
    }
  }
  return out && out.low < out.high ? out : null;
}

/** Writes each read line's name at its end on a time axis (two or more lines; a lone line is named by the title): an end label as the figure's own are,
    so the key leaves those lines out. Lines the figure already labels keep their labels. */
function nameLineEnds(painted: PlotFigure, text: string): void {
  const traces = (painted.data ?? []) as Record<string, any>[];
  const notes = (painted.layout?.annotations ?? []) as Record<string, any>[];
  const named = new Set(notes.filter(item => typeof item.name === "string" && item.showarrow === false).map(item => item.name as string));
  const read = traces.filter(trace => (trace.type ?? "scatter") === "scatter" && String(trace.mode ?? "lines").includes("lines") && typeof trace.name === "string" && trace.name.trim()
    && trace.hoverinfo !== "skip" && !trace.meta?.pad && trace.meta?.role !== "detail" && trace.showlegend !== false && !(trace.fill && trace.fill !== "none")
    && Array.isArray(trace.y) && Array.isArray(trace.x));
  if (read.length < 2) return;
  const whole = timeExtent(read);
  const added: Record<string, unknown>[] = [];
  read.forEach((trace, order) => {
    const key = trace.meta?.key ?? `line:${order}`;
    trace.meta = { ...trace.meta, key };
    if (named.has(key)) return;
    let at = trace.y.length - 1;
    while (at >= 0 && typeof trace.y[at] !== "number") at--;
    if (at < 0) return;
    const label = { name: key, showarrow: false, text: endLabel(String(trace.name).trim(), String(trace.line?.color ?? text)), font: { size: 11, color: text } };
    const end = utcTime(trace.x[at]);
    if (whole && Number.isFinite(end) && end < whole.high - (whole.high - whole.low) * .02) {
      // A line that stops inside the plot (the in-sample part before a held-out one, a compared run over other years) is named just before its end,
      // on the side its own last stretch leaves free: past its end the label would sit on the line that goes on.
      const near = whole.high - whole.low, own: number[] = [];
      trace.x.forEach((x: unknown, index: number) => {
        const value = trace.y[index], time = utcTime(x);
        if (typeof value === "number" && time <= end && time >= end - near * .15) own.push(value);
      });
      const above = own.filter(value => value <= trace.y[at]).length * 2 >= own.length;
      added.push({ ...label, x: trace.x[at], y: trace.y[at], xref: trace.xaxis ?? "x", yref: trace.yaxis ?? "y", xanchor: "right", yanchor: above ? "bottom" : "top", xshift: -4, yshift: above ? 4 : -4 });
      return;
    }
    added.push({ ...label, x: trace.x[at], y: trace.y[at], xref: trace.xaxis ?? "x", yref: trace.yaxis ?? "y", xanchor: "left", yanchor: "middle", xshift: 6 });
  });
  if (added.length) painted.layout = { ...(painted.layout ?? {}), annotations: [...notes, ...added] };
}

let measurer: CanvasRenderingContext2D | null = null;
/** The width in pixels of a text drawn in a CSS font (`11px Inter`). */
function textWidth(text: string, font: string): number {
  measurer ??= document.createElement("canvas").getContext("2d")!;
  measurer.font = font;
  return measurer.measureText(text).width;
}

/** Whether the line piece from (x0, y0) to (x1, y1) passes through the box. */
function segmentMeets(x0: number, y0: number, x1: number, y1: number, left: number, top: number, right: number, bottom: number): boolean {
  if (![x0, y0, x1, y1].every(Number.isFinite) || Math.max(x0, x1) < left || Math.min(x0, x1) > right || Math.max(y0, y1) < top || Math.min(y0, y1) > bottom) return false;
  let low = 0, high = 1;
  const dx = x1 - x0, dy = y1 - y0;
  for (const [p, q] of [[-dx, x0 - left], [dx, right - x0], [-dy, y0 - top], [dy, bottom - y0]]) {
    if (p === 0) { if (q < 0) return false; continue; }
    const k = q / p;
    if (p < 0) { if (k > high) return false; low = Math.max(low, k); } else { if (k < low) return false; high = Math.min(high, k); }
  }
  return true;
}

/** The nearest element above that scrolls its content (null: the page itself). */
function scrollingParent(node: Element): Element | null {
  for (let at = node.parentElement; at; at = at.parentElement) if (/(auto|scroll|overlay)/.test(getComputedStyle(at).overflowY)) return at;
  return null;
}

export function Plot({ figure, ariaLabel, height, keepTitle = false, onPoint, onRange, onSelect }: {
  figure: PlotFigure; ariaLabel: string; height?: number; keepTitle?: boolean;
  onPoint?(point: PlotPoint): void; onRange?(range: [unknown, unknown] | null): void; onSelect?(range: [unknown, unknown] | null): void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const element = useRef<HTMLDivElement>(null);
  const tip = useRef<HTMLDivElement>(null);
  const shade = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const handlers = useRef({ onPoint, onRange, onSelect }); handlers.current = { onPoint, onRange, onSelect };
  const settle = useRef<() => void>(() => undefined);
  const colours = useUpColour();
  const cross = useRef<HTMLDivElement>(null);
  const marks = useRef<HTMLDivElement>(null);
  // The section this chart sits in: its head carries the key of its charts; those on a time axis move together with the others of the section, or of the
  // wider area the page links (see group.ts).
  const [section, setSection] = useState<{ group: ChartGroup; link: ChartGroup; slot: Element | null } | null>(null);
  useEffect(() => {
    const found = root.current ? chartSection(root.current) : null;
    if (found) setSection({ group: chartGroup(found.scope), link: chartGroup(found.link), slot: found.slot });
  }, []);
  const group = section?.group ?? null, link = section?.link ?? null;
  useSyncExternalStore(group?.subscribe ?? NO_GROUP.subscribe, group?.snapshot ?? NO_GROUP.snapshot, NO_GROUP.snapshot);
  const groupNow = useRef(group); groupNow.current = group;
  const linkNow = useRef(link); linkNow.current = link;
  const member = useRef<ChartMember>({ linked: false, keys: [], scales: false, range: () => undefined, preview: () => undefined, cursor: () => undefined });
  useEffect(() => group ? group.join(member.current) : undefined, [group]);
  useEffect(() => link && link !== group ? link.join(member.current) : undefined, [link, group]);
  const [ownLog, setOwnLog] = useState(false);
  const log = group ? group.log : ownLog;
  const [own, setOwn] = useState<{ keys: ChartKey[]; scales: boolean }>({ keys: [], scales: false });
  // A chart is drawn only when it is near the screen (a page of charts draws those in view, the others as they are scrolled to); its box keeps its size meanwhile.
  // One that is far when its figure changes is cleared rather than left showing the figure before.
  const [near, setNear] = useState(typeof IntersectionObserver === "undefined");
  useEffect(() => {
    const holder = root.current;
    if (!holder || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(entries => setNear(entries[entries.length - 1].isIntersecting), { root: scrollingParent(holder), rootMargin: "100% 0px" });
    observer.observe(holder);
    return () => observer.disconnect();
  }, []);
  const [shown, setShown] = useState<{ figure: PlotFigure; keepTitle: boolean; colours: string; log: boolean } | null>(null);
  useEffect(() => setShown(current => current && current.figure === figure && current.keepTitle === keepTitle && current.colours === colours && current.log === log ? current
    : near ? { figure, keepTitle, colours, log } : null), [near, figure, keepTitle, colours, log]);
  useEffect(() => {
    const node = element.current;
    if (!node) return;
    if (!shown) {
      member.current.linked = false;
      if ((node as any)._fullLayout) void loadPlotly().then(Plotly => Plotly.purge(node));
      return;
    }
    const { figure, keepTitle, log } = shown;
    let active = true;
    const style = getComputedStyle(node);
    const token = (name: string) => style.getPropertyValue(name).trim();
    const drawn = paintFigure(figure, token);
    const painted = { ...drawn, data: (drawn.data ?? []).map(trace => thinLine(trace as Record<string, any>)) };
    const zoomable = isZoomable(painted) && !handlers.current.onRange && !handlers.current.onSelect;
    if (zoomable && !keepTitle) nameLineEnds(painted, style.getPropertyValue("--tx-text-2").trim());
    // The key leaves the plot: it is drawn beside the section's title (or over the chart's top left when there is no title).
    const keys = keepTitle ? [] : figureKeys(painted);
    const scales = !keepTitle && offersLog(figure);
    const slotted = Boolean(root.current && chartSection(root.current)?.slot);
    const ends = ((painted.layout?.annotations ?? []) as Record<string, any>[]).filter(item => typeof item.name === "string" && item.showarrow === false)
      .map(item => String((painted.data as Record<string, any>[] ?? []).find(trace => trace.meta?.key === item.name)?.name ?? "")).filter(Boolean);
    Object.assign(member.current, { linked: zoomable, keys, scales, ends });
    groupNow.current?.changed();
    setOwn({ keys, scales });
    const nativeLayout = keepTitle ? { ...(painted.layout ?? {}) } : plainLayout(painted.layout ?? {});
    delete nativeLayout.template;
    delete nativeLayout.width;
    delete nativeLayout.height;
    const titleText = (value: unknown) => typeof value === "string" ? value : (value as { text?: string } | undefined)?.text ?? "";
    const figureTitle = titleText(nativeLayout.title);
    if (!keepTitle) delete nativeLayout.title;
    const traces = (painted.data ?? []) as Record<string, any>[];
    const horizontal = traces.some(trace => trace.type === "bar" && trace.orientation === "h");
    const dated = zoomable;
    // A chart of markers (a QQ plot, a scatter) zooms by a box on both axes; double click shows it whole again.
    const boxZoom = !zoomable && !handlers.current.onRange && !handlers.current.onSelect && !horizontal && traces.length > 0
      && traces.some(trace => (trace.type ?? "scatter") === "scatter" && String(trace.mode ?? "lines").split("+").every(part => part === "markers") && trace.hoverinfo !== "skip")
      && traces.every(trace => (trace.type ?? "scatter") === "scatter");
    const hasEndLabels = Array.isArray(nativeLayout.annotations) && (nativeLayout.annotations as Record<string, any>[]).some(item => typeof item.name === "string" && item.showarrow === false);
    const axisLook = { gridcolor: token("--tx-white-045"), zerolinecolor: token("--tx-line-3"), linecolor: token("--tx-line"), tickcolor: token("--tx-line-2"), ticks: "", automargin: true, showline: false };
    const xLook = { ...axisLook, showgrid: horizontal, showline: true, tickfont: { family: token("--tx-font"), size: 11, color: token("--tx-text-3") }, fixedrange: !zoomable,
      ...(dated ? { tickformatstops: [{ dtickrange: [null, 86400000 * 28], value: "" }, { dtickrange: [86400000 * 28, 86400000 * 360], value: "%b %Y" }, { dtickrange: [86400000 * 360, null], value: "%Y" }] } : {}) };
    const yLook = { ...axisLook, showgrid: !horizontal, side: "right", tickfont: { family: horizontal ? token("--tx-font") : token("--tx-font-mono"), size: horizontal ? 11 : 10.5, color: token("--tx-text-3") }, fixedrange: true };  // category names read in the interface font
    // Axis titles that repeat the figure title, or run longer than the axis, add nothing.
    for (const key of Object.keys(nativeLayout).filter(name => /^[xy]axis\d*$/.test(name))) {
      const axisLayout = { ...(nativeLayout[key] as Record<string, unknown>) };
      const text = titleText(axisLayout.title);
      // No axis titles ("Date", "Equity"): the section's title says what is drawn.
      if (!keepTitle || (text && (text === figureTitle || text.length > 36))) delete axisLayout.title;
      if (key === "xaxis" && horizontal) axisLayout.side = axisLayout.side ?? "top";
      // Qlib's own figures tilt their dates 45°; they turn only when they would overlap.
      if (key.startsWith("x")) delete axisLayout.tickangle;
      nativeLayout[key] = { ...(key.startsWith("x") ? xLook : yLook), ...axisLayout, ...(axisLayout.range ? { autorange: false } : {}), fixedrange: key.startsWith("x") ? !(zoomable || boxZoom) : !boxZoom };
    }
    const heat = traces.some(trace => trace.type === "heatmap" || trace.type === "violin");
    // A heatmap whose cells are too narrow for their numbers shows the colour only; the read-out gives the number.
    const across = Math.max(0, (root.current?.clientWidth ?? 0) - 80);
    for (const trace of traces) {
      if (trace.type !== "heatmap" || !(trace.texttemplate || trace.text)) continue;
      const columns = Array.isArray(trace.z) && Array.isArray(trace.z[0]) ? (trace.z[0] as unknown[]).length : 1;
      const sample = Array.isArray(trace.text) ? String((trace.text as unknown[]).flat(2).find(value => value !== null && value !== "") ?? "") : "-0.000";
      if (across / Math.max(1, columns) < sample.length * 6.5 + 6) { delete trace.texttemplate; delete trace.text; }
    }
    const lines = traces.every(trace => (trace.type ?? "scatter") === "scatter" && !String(trace.mode ?? "lines").split("+").every(part => part === "markers"));
    // Points that stand alone (a scatter of markers) read one at a time; lines and bars on a time or category axis read together at the cursor's x.
    const alone = traces.some(trace => (trace.type ?? "scatter") === "scatter" && String(trace.mode ?? "lines").split("+").every(part => part === "markers") && trace.hoverinfo !== "skip");
    const hovermode = horizontal ? "y" : heat || alone ? "closest" : "x";
    // Charts read along one axis (bars, lines on categories or numbers) read at the pointer every frame too, by the nearest column (row, for bars lying
    // down); cells, violins and lone points keep Plotly's own reading.
    const ownMode: "x" | "y" | null = zoomable ? null : horizontal ? "y" : !heat && !alone ? "x" : null;
    const spike = !horizontal && !heat && !alone && !zoomable && !ownMode ? crosshairLook(token) : {};
    const mark = (nativeLayout.xaxis as Record<string, unknown> | undefined) ?? {};
    // A line's end labels sit just past the last point: the value axis on the right steps aside by the longest label's width as drawn, so the time axis
    // still ends at the data's last day and the axis's numbers stand the same short way after the labels on every chart.
    const endLabels = hasEndLabels && dated ? (nativeLayout.annotations as Record<string, any>[]).filter(item => typeof item.name === "string" && item.showarrow === false && item.yref !== "paper" && item.xanchor !== "right") : [];
    const labelRoom = endLabels.length ? Math.ceil(Math.max(...endLabels.map(item => textWidth(String(item.text ?? "").replace(/<[^>]+>/g, ""), `${item.font?.size ?? 11}px ${token("--tx-font")}`) + (item.xshift ?? 0)))) + 16 : 0;
    const padded = drawOrder(traces);
    // The marks of the day under the cursor, one on each line, are dots laid over the plot: moving them never redraws the chart.
    const marksShown = lines && !horizontal;
    const layout: Record<string, unknown> = {
      template: { layout: { xaxis: xLook, yaxis: yLook, scene: { xaxis: axisLook, yaxis: axisLook, zaxis: axisLook }, polar: { bgcolor: token("--tx-clear") }, coloraxis: { colorbar: { outlinewidth: 0 } } } },
      colorway: [10, 6, 9, 12, 7, 11, 14, 8, 13, 15].map(index => token(`--tx-user-palette-${index}`)),
      xaxis: { ...xLook, ...spike }, yaxis: yLook,
      ...nativeLayout,
      paper_bgcolor: token("--tx-clear"),
      plot_bgcolor: token("--tx-clear"),
      font: { family: token("--tx-font"), size: 11, color: token("--tx-text-3") },
      hovermode,
      // Lines and bars read at the cursor's x anywhere in the plot: a click anywhere in a column picks that column's bar or day.
      hoverdistance: hovermode === "closest" ? 40 : -1,
      spikedistance: -1,
      // A new figure for the same chart (data refreshed, another run, another page) keeps the range the user is looking at.
      uirevision: String((figure.layout?.meta as { key?: unknown } | undefined)?.key ?? ariaLabel),
      // A time chart zooms to the span dragged across it (the value axis stays fitted); its time axis pans when dragged.
      dragmode: zoomable || boxZoom ? "zoom" : handlers.current.onSelect || handlers.current.onRange ? (nativeLayout.dragmode ?? "zoom") : false,
      legend: { orientation: "h", x: 0, xanchor: "left", y: 1.02, yanchor: "bottom", bgcolor: token("--tx-clear"), ...(nativeLayout.legend as object ?? {}), font: { size: 11, color: token("--tx-text-2") } },
      margin: { l: 8, r: 8, t: keepTitle ? 44 : 10, b: 8, ...(nativeLayout.margin as object ?? {}), ...(keepTitle ? { l: 52, r: 16 } : {}), ...(!keepTitle && !slotted && (keys.length || scales) ? { t: 26 } : {}) },
      autosize: true,
      // Plotly's own hover box and axis labels are never shown: the read-out is the platform's.
      hoverlabel: { bgcolor: "rgba(0,0,0,0)", bordercolor: "rgba(0,0,0,0)", font: { color: "rgba(0,0,0,0)" } },
    };
    if (!keepTitle) { layout.showlegend = false; Object.assign(layout, axisUnits(layout, traces, root.current?.clientWidth ?? 0, root.current?.clientHeight ?? 0)); }
    // On a category axis Plotly places an annotation by the category's number: one written as the category's name ("2024") would stretch the axis to 2024.
    if ((layout.xaxis as { type?: string } | undefined)?.type === "category" && Array.isArray(layout.annotations)) {
      const names: string[] = [];
      for (const trace of traces) for (const value of (Array.isArray(trace.x) ? trace.x : [])) if (!names.includes(String(value))) names.push(String(value));
      layout.annotations = (layout.annotations as Record<string, unknown>[]).map(item => (item.xref ?? "x") === "x" && typeof item.x === "string" && names.includes(item.x) ? { ...item, x: names.indexOf(item.x) } : item);
    }
    if (labelRoom) layout.yaxis = { ...(layout.yaxis as object), ticklabelstandoff: labelRoom };
    // The time axis is the data's own time: end labels are annotations, which Plotly would widen it for, and days no line has a value on (a rolling window
    // filling up, a test period starting years in) would leave the plot empty there and out of step with the section's other charts. Bars keep Plotly's
    // half a bar of room at each end.
    const whole = zoomable ? timeExtent(traces) : null;
    if (whole && (labelRoom || !traces.some(trace => trace.type === "bar")) && !(nativeLayout.xaxis as { range?: unknown } | undefined)?.range) layout.xaxis = { ...(layout.xaxis as object), range: [whole.from, whole.to], autorange: false };
    if (scales && log) layout.yaxis = { ...(layout.yaxis as object), type: "log" };   // the figure's own ticks are values, so they stand on a log axis too
    if (layout.xaxis && typeof layout.xaxis === "object") layout.xaxis = { ...(layout.xaxis as object), ...spike, ...mark.type ? {} : {} };
    const model = { data: painted.data ?? [], layout: nativeLayout };
    const pointMarks = (points: HoverPoint[]) => {
      const layer = marks.current;
      if (!layer) return;
      const dots: { left: number; top: number; colour: string }[] = [];
      if (marksShown) for (const point of points) {
        const { xaxis, yaxis } = point as HoverPoint & { xaxis?: any; yaxis?: any };
        if (!xaxis || !yaxis) continue;
        for (const found of hoverMarks([point], model)) {
          const left = xaxis.d2p(found.x), top = yaxis.d2p(found.y);
          if (left >= 0 && left <= xaxis._length && top >= 0 && top <= yaxis._length) dots.push({ left: xaxis._offset + left, top: yaxis._offset + top, colour: found.colour });
        }
      }
      while (layer.children.length < dots.length) layer.append(document.createElement("i"));
      Array.from(layer.children as HTMLCollectionOf<HTMLElement>).forEach((dot, at) => {
        const place = dots[at];
        if (!place) { dot.style.display = "none"; return; }
        Object.assign(dot.style, { display: "block", left: `${place.left}px`, top: `${place.top}px`, background: place.colour });
      });
    };
    const reset = () => { pointMarks([]); for (const part of [tip.current, shade.current, cross.current]) if (part) part.style.display = "none"; };
    // Linked to the other charts of the section (or the linked area): a range or a cursor given by one of them is applied here, without being passed on again.
    let echoes = 0, dragging = false;
    member.current.range = range => {
      if (!zoomable || !active || dragging) return;   // the chart being dragged leads; it is never moved under the pointer
      echoes++;
      void loadPlotly().then(Plotly => {
        if (!active) return;
        unpreview();
        return Plotly.relayout(node, range ? { "xaxis.range[0]": range[0], "xaxis.range[1]": range[1], ...fitY(range[0], range[1]) } : { "xaxis.autorange": true });
      });
    };
    member.current.preview = range => { if (zoomable && active && !dragging) preview(range[0], range[1]); };
    // The read-out shown for a point list; set once the chart is drawn.
    let pointTimer: ReturnType<typeof setTimeout> | undefined;
    let showAt: ((points: HoverPoint[], linked: boolean, mouse?: MouseEvent) => void) | null = null;
    const times = new Map<Record<string, any>, number[]>();
    /** The index of the time nearest a target: by halving on times in order (a time series), else by reading them all. */
    const nearestIndex = (read: number[], target: number): number => {
      if (!read.length) return -1;
      let ordered = true;
      for (let at = 1; at < read.length && ordered; at += Math.max(1, Math.floor(read.length / 64))) if (!(read[at] >= read[at - 1])) ordered = false;
      if (!ordered) { let best = -1, gap = Infinity; for (let at = 0; at < read.length; at++) { const away = Math.abs(read[at] - target); if (away < gap) { gap = away; best = at; } } return best; }
      let low = 0, high = read.length - 1;
      while (low < high) { const middle = (low + high) >> 1; if (read[middle] < target) low = middle + 1; else high = middle; }
      return low > 0 && Math.abs(read[low - 1] - target) <= Math.abs(read[low] - target) ? low - 1 : low;
    };
    member.current.cursor = x => {
      if (!zoomable || !active) return;
      if (x === null || drawnAt) { reset(); return; }
      const full = (node as any)._fullLayout;
      if (!full?.xaxis || !full?.yaxis || !showAt) return;
      // The point of each read line nearest the day the linked chart is on (each line's times read once, not on every move).
      const target = full.xaxis.d2c(x), points: HoverPoint[] = [];
      for (const trace of traces) {
        const xs = trace.x as unknown[] | undefined, ys = trace.y as unknown[] | undefined;
        if (trace.hoverinfo === "skip" || trace.meta?.pad || !Array.isArray(xs) || !Array.isArray(ys) || !xs.length) continue;
        let read = times.get(trace);
        if (!read) { read = xs.map(value => full.xaxis.d2c(value) as number); times.set(trace, read); }
        const best = nearestIndex(read, target);
        if (best >= 0 && typeof ys[best] === "number") points.push({ x: xs[best], y: ys[best], pointIndex: best, data: trace, xaxis: full.xaxis, yaxis: full.yaxis } as HoverPoint);
      }
      if (points.length) showAt(points, true); else reset();
    };
    let frame = 0;
    const share = (range: [unknown, unknown] | null) => { cancelAnimationFrame(frame); frame = requestAnimationFrame(() => linkNow.current?.range(member.current, range)); };
    const leave = () => { reset(); linkNow.current?.cursor(member.current, null); };
    const box = root.current!;
    // End labels keep apart: each moves as little as it must from where its line ends.
    let applied = "";
    const spreadEnds = () => {
      const full = (node as any)._fullLayout, y = full?.yaxis, notes = full?.annotations as Record<string, any>[] | undefined;
      if (!y || !notes?.length || !y.d2p) return;
      const ends = notes.map((item, index) => ({ item, index })).filter(({ item }) => typeof item.name === "string" && item.showarrow === false && item.yref !== "paper" && item.xanchor !== "right");
      if (ends.length < 2) return;
      const where = ends.map(({ item }) => y.d2p(item.y));
      const shifts = spreadLabels(where, 15, 0, y._length);
      const key = shifts.map(value => Math.round(value)).join(",");
      if (key === applied) return;
      applied = key;
      const patch: Record<string, number> = {};
      ends.forEach(({ index }, at) => { patch[`annotations[${index}].yshift`] = -Math.round(shifts[at]); });
      void loadPlotly().then(Plotly => active && (Plotly as any).relayout(node, patch));
    };
    // Zooming in on time shows the values of the window: the vertical range follows what is in it.
    let fitting = false;
    /** The x range (in milliseconds) kept inside the data's time: zooming and panning never go past it; null when it already is inside. */
    const keptInside = (from: number, to: number): [number, number] | null => {
      if (!whole) return null;
      const { low, high } = whole;
      if (to - from >= high - low) return from <= low + 1 && to >= high - 1 && to - from <= (high - low) * 1.0001 ? null : [low, high];
      if (from >= low - 1 && to <= high + 1) return null;   // a millisecond either way is the same range written out and read back
      return from < low ? [low, low + (to - from)] : [high - (to - from), high];
    };
    /** The same as the relayout that sets it. */
    const inside = (from: number, to: number): Record<string, unknown> | null => {
      const x = (node as any)._fullLayout?.xaxis, kept = x?.l2r ? keptInside(from, to) : null;
      if (!kept) return null;
      return kept[0] === whole!.low && kept[1] === whole!.high ? { "xaxis.range[0]": whole!.from, "xaxis.range[1]": whole!.to } : { "xaxis.range[0]": x.l2r(kept[0]), "xaxis.range[1]": x.l2r(kept[1]) };
    };
    const fixedY = Boolean((nativeLayout.yaxis as { range?: unknown } | undefined)?.range);
    /** The value axis stretched by dragging it: it stays as set until a double click on it (or on the plot) fits it again. */
    let stretched = false;
    const logY = scales && log;
    // Each trace's times in milliseconds, read once: fitting the value axis runs on every notch of the wheel.
    const timesOf = new Map<Record<string, any>, Float64Array>();
    /** The value range of what lies between two times (milliseconds) as the value axis writes it (powers of ten on a log axis); null when nothing is there. */
    const fitRange = (from: number, to: number): [number, number] | null => {
      if (!zoomable || fixedY || stretched) return null;
      let low = Infinity, high = -Infinity;
      for (const trace of traces) {
        const xs = trace.x as unknown[] | undefined, ys = trace.y as unknown[] | undefined;
        if (!Array.isArray(xs) || !Array.isArray(ys) || trace.meta?.pad || trace.fill === "tonexty" && trace.hoverinfo === "skip") continue;
        let times = timesOf.get(trace);
        if (!times) { times = Float64Array.from(xs, utcTime); timesOf.set(trace, times); }
        for (let at = 0; at < times.length; at++) {
          const time = times[at], value = ys[at];
          if (time >= from && time <= to && typeof value === "number") { if (value < low) low = value; if (value > high) high = value; }
        }
        if (trace.type === "bar") { low = Math.min(low, 0); high = Math.max(high, 0); }
      }
      if (low > high) return null;
      if (logY) { if (low <= 0) return null; low = Math.log10(low); high = Math.log10(high); }
      const pad = (high - low || Math.abs(high) || 1) * .06;
      return [low - pad, high + pad];
    };
    /** The value range of what lies between two x values, as the relayout that sets it (null when nothing is there); asked with the x change itself, so the chart draws once. */
    const fitY = (start: unknown, end: unknown): Record<string, unknown> | null => {
      const range = fitRange(utcTime(start), utcTime(end));
      return range ? { "yaxis.range": range, "yaxis.autorange": false } : null;
    };
    // While the wheel turns, the chart is not laid out on every notch (a few thousand points cost a frame or more): as Plotly's own scroll zoom does, the
    // drawn data layers move and stretch to the new x range and its fitted value range, inside the plot's clip, and the chart is laid out once when the
    // wheel stops. The section's other charts do the same. `drawnAt` is the ranges the drawn layers were laid out for.
    let drawnAt: { x: [number, number]; y: [number, number] } | null = null;
    const layersOf = (plot: any): Element[] => Array.from((plot?.plot?.node?.() as Element | undefined)?.children ?? []);
    const preview = (from: number, to: number) => {
      const full = (node as any)._fullLayout, x = full?.xaxis, y = full?.yaxis;
      if (!x?.range || !y?.range || !(to > from)) return;
      if (!drawnAt) {
        drawnAt = { x: [x.r2l(x.range[0]), x.r2l(x.range[1])], y: [y.r2l(y.range[0]), y.r2l(y.range[1])] };
        reset();
      }
      const [x0, x1] = drawnAt.x, [y0, y1] = drawnAt.y, fitted = fitRange(from, to) ?? drawnAt.y;
      const sx = (x1 - x0) / (to - from), tx = (x0 - from) * x._length / (to - from);
      const sy = (y1 - y0) / (fitted[1] - fitted[0]), ty = (fitted[1] - y1) * y._length / (fitted[1] - fitted[0]);
      for (const plot of Object.values(full._plots ?? {}) as any[]) {
        if (plot.xaxis !== x) continue;
        const own = plot.yaxis === y;
        const move = `matrix(${sx},0,0,${own ? sy : 1},${tx},${own ? ty : 0})`;
        for (const layer of layersOf(plot)) layer.setAttribute("transform", move);
      }
    };
    const unpreview = () => {
      if (!drawnAt) return;
      drawnAt = null;
      for (const plot of Object.values((node as any)._fullLayout?._plots ?? {})) for (const layer of layersOf(plot)) layer.removeAttribute("transform");
    };
    // Where the drawn traces lie, in pixels of this box; read again only when an axis moves.
    let pixels: { key: string; marks: { kind: "line" | "points" | "bars"; xs: Float64Array; ys: Float64Array; base: number }[] } | null = null;
    const drawnPixels = () => {
      const full = (node as any)._fullLayout;
      if (!full?.xaxis || !full?.yaxis) return [];
      const key = Object.keys(full).filter(name => /^[xy]axis\d*$/.test(name)).map(name => `${full[name].range}:${full[name]._length}:${full[name]._offset}`).join("|");
      if (pixels?.key === key) return pixels.marks;
      const marks: NonNullable<typeof pixels>["marks"] = [];
      for (const trace of traces) {
        const xs = trace.x as unknown[] | undefined, ys = trace.y as unknown[] | undefined;
        if (trace.meta?.pad || trace.visible === false || !Array.isArray(xs) || !Array.isArray(ys)) continue;
        const ax = full[`xaxis${String(trace.xaxis ?? "x").slice(1)}`], ay = full[`yaxis${String(trace.yaxis ?? "y").slice(1)}`];
        if (!ax?.d2p || !ay?.d2p) continue;
        const px = new Float64Array(xs.length), py = new Float64Array(xs.length);
        for (let at = 0; at < xs.length; at++) { const value = ys[at]; px[at] = ax._offset + ax.d2p(xs[at]); py[at] = typeof value === "number" ? ay._offset + ay.d2p(value) : NaN; }
        marks.push({ kind: trace.type === "bar" ? "bars" : String(trace.mode ?? "lines").includes("lines") ? "line" : "points", xs: px, ys: py, base: ay._offset + ay.d2p(0) });
      }
      pixels = { key, marks };
      return marks;
    };
    /** How much of the drawn data a box would hide: the points and line pieces in it, bars counted heavier. */
    const hides = (left: number, top: number, width: number, height: number) => {
      const right = left + width, bottom = top + height;
      let count = 0;
      for (const mark of drawnPixels()) {
        const { xs, ys } = mark;
        for (let at = 0; at < xs.length; at++) {
          const x = xs[at], y = ys[at];
          if (mark.kind === "bars") { if (x >= left - 4 && x <= right + 4 && Math.max(y, mark.base) >= top && Math.min(y, mark.base) <= bottom) count += 4; }
          else if (x >= left && x <= right && y >= top && y <= bottom) count++;
          else if (mark.kind === "line" && at > 0 && segmentMeets(xs[at - 1], ys[at - 1], x, y, left, top, right, bottom)) count++;
        }
      }
      return count;
    };
    /** The read-out's place: on the side of the plot away from the cursor, at the top or the bottom, whichever hides less of the lines (so the
        read-out stays clear of the cursor); beside the cursor only when the plot is too narrow for that. Never over the point being read. */
    const readoutPlace = (first: any, x: number, y: number | null, width: number, height: number): { left: number; top: number } | null => {
      const ax = first?.xaxis, ay = first?.yaxis;
      if (!ax || !ay || !Number.isFinite(x)) return null;
      // Up and down it may use the whole plot area: plots stacked on one time axis share one read-out.
      const size = (node as any)._fullLayout._size;
      const left = ax._offset, right = ax._offset + ax._length, top = size.t, bottom = size.t + size.h;
      const far = x > (left + right) / 2 ? left + 8 : right - 8 - width;
      const column = far <= x + 14 && x - 14 <= far + width ? (x + 14 + width <= right ? x + 14 : x - 14 - width) : far;
      let best: { left: number; top: number; cost: number } | null = null;
      for (const row of [top + 4, Math.max(top + 4, bottom - 4 - height)]) {
        const cost = hides(column, row, width, height) + (y !== null && row <= y && y <= row + height && column <= x && x <= column + width ? 1e6 : 0);
        if (!best || cost < best.cost) best = { left: column, top: row, cost };
      }
      return best;
    };
    const fit = (event: Record<string, unknown>) => {
      if (!zoomable || fitting || fixedY || stretched || event["yaxis.range"] !== undefined) return;
      const reset = event["xaxis.autorange"] === true || event.autosize === true;
      const patch = reset ? { "yaxis.autorange": true } : event["xaxis.range[0]"] !== undefined ? fitY(event["xaxis.range[0]"], event["xaxis.range[1]"]) : null;
      if (patch) { fitting = true; void loadPlotly().then(Plotly => (Plotly as any).relayout(node, patch)).finally(() => { fitting = false; }); }
    };
    settle.current = () => { applied = ""; spreadEnds(); };
    // The x range the wheel has reached (milliseconds) and the wait for it to stop.
    let wheeled: [number, number] | null = null, wheelStop = 0;
    const wheelEnd = () => {
      clearTimeout(wheelStop);
      const range = wheeled, x = (node as any)._fullLayout?.xaxis;
      wheeled = null;
      if (!range || !x?.l2r) return;
      const ends = range[0] === whole?.low && range[1] === whole?.high ? [whole.from, whole.to] : [x.l2r(range[0]), x.l2r(range[1])];
      void loadPlotly().then(Plotly => {
        if (!active) return;
        unpreview();
        return Plotly.relayout(node, { "xaxis.range[0]": ends[0], "xaxis.range[1]": ends[1], ...fitY(ends[0], ends[1]) });
      });
    };
    const wheel = (event: WheelEvent) => {
      if (!event.ctrlKey || !active) return;
      event.preventDefault();
      const x = (node as any)._fullLayout?.xaxis;
      if (!x?.range || !x.p2l) return;
      const [from, to] = wheeled ?? [x.r2l(x.range[0]), x.r2l(x.range[1])];
      // The pointer's time on the range shown now (the drawn axis is still at the range the gesture started from).
      const where = from + (event.clientX - node.getBoundingClientRect().left - x._offset) / x._length * (to - from);
      if (![from, to, where].every(Number.isFinite)) return;
      const scale = Math.exp(Math.max(-1, Math.min(1, event.deltaY * .002)));
      const low = where - (where - from) * scale, high = where + (to - where) * scale;
      wheeled = keptInside(low, high) ?? [low, high];
      if (!drawnAt) linkNow.current?.cursor(member.current, null);
      preview(wheeled[0], wheeled[1]);
      linkNow.current?.preview(member.current, wheeled);
      clearTimeout(wheelStop);
      wheelStop = window.setTimeout(wheelEnd, 120);
    };
    // A chart drawn after its section was zoomed (scrolled to later, or given the next run's figure) opens on the section's time, when its data covers it.
    const shared = zoomable && whole ? linkNow.current?.shown ?? null : null;
    if (shared && whole && utcTime(shared[0]) >= whole.low - 1 && utcTime(shared[1]) <= whole.high + 1) {
      layout.xaxis = { ...(layout.xaxis as object), range: [...shared], autorange: false };
      const fitted = fitY(shared[0], shared[1]);
      if (fitted) layout.yaxis = { ...(layout.yaxis as object), range: fitted["yaxis.range"], autorange: false };
    }
    void loadPlotly().then(async Plotly => {
      if (!active) return;
      await Plotly.react(node, padded, layout, { responsive: false, displaylogo: false, displayModeBar: false, showTips: false, scrollZoom: false, doubleClickDelay: 300, doubleClick: zoomable || boxZoom || handlers.current.onSelect ? "reset" : false });
      if (!active) return;
      const events = node as unknown as { on?(name: string, handler: (event: any) => void): void; removeAllListeners?(name: string): void };
      for (const name of ["plotly_click", "plotly_doubleclick", "plotly_relayout", "plotly_relayouting", "plotly_selected", "plotly_deselect", "plotly_hover", "plotly_unhover"]) events.removeAllListeners?.(name);
      events.on?.("plotly_doubleclick", () => clearTimeout(pointTimer));
      events.on?.("plotly_click", event => {
        const point = event?.points?.[0];
        if (!point || !handlers.current.onPoint) return;
        clearTimeout(pointTimer);
        pointTimer = setTimeout(() => handlers.current.onPoint?.({ trace: point.data?.name, key: point.data?.meta?.key, x: point.x, y: point.y, z: point.z, index: point.pointIndex ?? point.pointNumber }), 310);
      });
      events.on?.("plotly_hover", event => {
        if (drawnAt || zoomable || ownMode) return;   // a time chart's cursor is its own (below), drawn every frame   // the drawn layers are moved by the wheel: Plotly's points are where the axes were
        const points = (event?.points ?? []) as HoverPoint[];
        if (points[0]) linkNow.current?.cursor(member.current, points[0].x);
        show(points, false, event?.event as MouseEvent | undefined);
      });
      // The wheel scrolls the page; with Ctrl held it zooms the time around the pointer.
      if (zoomable) node.addEventListener("wheel", wheel, { passive: false });
      showAt = show;
      function show(points: HoverPoint[], linked: boolean, mouse?: MouseEvent) {
        const read = hoverModel(points, model);
        const out = tip.current;
        if (!read || !out) { reset(); return; }
        pointMarks(points);
        out.replaceChildren();
        const head = document.createElement("div"); head.className = "qc-tip-h"; head.textContent = read.title; out.append(head);
        for (const row of read.rows) {
          const line = document.createElement("div"); line.className = "qc-tip-r"; if (row.weak) line.dataset.weak = "";
          const name = document.createElement("span"), dot = document.createElement("i"), value = document.createElement("b");
          dot.style.background = row.colour; dot.style.color = row.colour; if (row.dashed) dot.dataset.dashed = "";
          name.append(dot, row.label); value.textContent = row.value; line.append(name, value); out.append(line);
        }
        out.style.display = "block";
        const area = box.getBoundingClientRect();
        let at = { x: (mouse?.clientX ?? area.left) - area.left, y: (mouse?.clientY ?? area.top) - area.top };
        const first = points[0] as any, line = cross.current;
        if (line) line.style.display = "none";
        const across = first?.xaxis && first?.yaxis && first.data?.orientation !== "h" && !heat && !alone;
        if (across) {
          // On a time or category axis the read-out sits beside the crosshair, at the top of the plot, on the side with room: it never covers the lines.
          const left = first.xaxis._offset + first.xaxis.d2p(first.x);
          if (Number.isFinite(left)) at = { x: left, y: first.yaxis._offset };
          // Shown because a linked chart is hovered: the crosshair is drawn here at the same day.
          if (linked && line && Number.isFinite(left)) Object.assign(line.style, { display: "block", left: `${left}px`, top: `${first.yaxis._offset}px`, height: `${first.yaxis._length}px` });
        }
        const place = across || alone ? readoutPlace(first, at.x, alone ? at.y : null, out.offsetWidth, out.offsetHeight) : null;
        if (place) Object.assign(out.style, { left: `${Math.max(0, Math.min(area.width - out.offsetWidth, place.left))}px`, top: `${Math.max(0, place.top)}px` });
        else {
          const left = at.x + 14 + out.offsetWidth > area.width ? at.x - 14 - out.offsetWidth : at.x + 14;
          out.style.left = `${Math.max(0, left)}px`;
          out.style.top = `${Math.max(0, Math.min(area.height - out.offsetHeight, at.y - out.offsetHeight / 2))}px`;
        }
        // Bars on categories get the whole column lit.
        const column = shade.current;
        if (column) {
          column.style.display = "none";
          if (first?.data?.type === "bar" && first.xaxis && first.yaxis) {
            const across = first.data.orientation === "h" ? first.yaxis : first.xaxis, plot = first.data.orientation === "h" ? first.xaxis : first.yaxis;
            const centre = across.d2p(first.data.orientation === "h" ? first.y : first.x);
            const xs = first.data.orientation === "h" ? first.data.y : first.data.x;
            const step = across._categories?.length ? across._length / across._categories.length
              : Array.isArray(xs) && typeof xs[1] === "number" && typeof xs[0] === "number" ? Math.abs(across.d2p(xs[1]) - across.d2p(xs[0])) : 0;
            if (Number.isFinite(centre) && step > 0) {
              if (first.data.orientation === "h") Object.assign(column.style, { display: "block", left: `${plot._offset}px`, width: `${plot._length}px`, top: `${across._offset + centre - step / 2}px`, height: `${step}px` });
              else Object.assign(column.style, { display: "block", top: `${plot._offset}px`, height: `${plot._length}px`, left: `${across._offset + centre - step / 2}px`, width: `${step}px` });
            }
          }
        }
      }
      events.on?.("plotly_unhover", () => { if (!zoomable && !ownMode) reset(); });
      events.on?.("plotly_relayouting", event => {
        if (event?.["xaxis.range[0]"] !== undefined) share([event["xaxis.range[0]"], event["xaxis.range[1]"]]);
      });
      events.on?.("plotly_relayout", given => {
        // A double click back to the first range writes it as one array: read as its two ends, as zooming and panning write them, so it is shared and fitted too.
        const event = Array.isArray(given?.["xaxis.range"]) ? { ...given, "xaxis.range[0]": given["xaxis.range"][0], "xaxis.range[1]": given["xaxis.range"][1] } : given;
        if (given?.["xaxis.autorange"] !== undefined || Array.isArray(given?.["xaxis.range"])) stretched = false;   // a double click: everything fitted again
        const x = (node as any)._fullLayout?.xaxis;
        if (event?.["xaxis.range[0]"] !== undefined && x?.r2l) {
          const patch = inside(x.r2l(event["xaxis.range[0]"]), x.r2l(event["xaxis.range[1]"]));
          if (patch) { void loadPlotly().then(Plotly => active && Plotly.relayout(node, patch)); return; }
        }
        fit(event ?? {});
        const moved = event?.["xaxis.autorange"] !== undefined || event?.["xaxis.range[0]"] !== undefined;
        const echo = moved && echoes > 0;   // only a change of x answers a range given by the group
        if (echo) echoes--;
        else if (!moved) { /* the value axis fitted, end labels moved: nothing to share */ }
        else if (event?.["xaxis.autorange"] === true) share(null);
        else if (event?.["xaxis.range[0]"] !== undefined) share([event["xaxis.range[0]"], event["xaxis.range[1]"]]);
        if (!handlers.current.onRange) return;
        if (event?.["xaxis.autorange"]) handlers.current.onRange(null);
        else if (event?.["xaxis.range[0]"] !== undefined) handlers.current.onRange([event["xaxis.range[0]"], event["xaxis.range[1]"]]);
      });
      events.on?.("plotly_selected", event => {
        const range = event?.range?.x;
        handlers.current.onSelect?.(Array.isArray(range) ? [range[0], range[1]] : null);
      });
      events.on?.("plotly_deselect", () => handlers.current.onSelect?.(null));
      spreadEnds();
      if (zoomable && active) { member.current.right = (node as any)._fullLayout?._size?.r ?? 0; groupNow.current?.changed(); }
    }).catch(failure => { if (active) setFailed(String(failure)); });
    // The time charts of a section end at the same x: one whose value axis or end labels need less room takes the widest one's right margin.
    const alignRight = () => {
      const size = (node as any)._fullLayout?._size, wanted = groupNow.current?.right() ?? 0;
      if (!active || !zoomable || !size || wanted <= size.r + 1) return;
      void loadPlotly().then(Plotly => active && (Plotly as any).relayout(node, { "margin.r": wanted }));
    };
    const stopAlign = groupNow.current?.subscribe(alignRight);
    // A drag or a double click while the wheel is still settling starts from where the wheel left the chart.
    const press = () => { wheelEnd(); dragging = true; }, release = () => { dragging = false; };
    // The cursor of a time chart follows the pointer every frame (Plotly's own hover runs at most every 50 ms): the line, the marks on the lines and the
    // read-out here, and the same day on the linked charts. Over the value axis the pointer stretches it instead.
    let pointer = { x: 0, y: 0, buttons: 0 }, pointerFrame = 0;
    let stretch: { from: number; range: [number, number] } | null = null;
    const where = () => {
      const full = (node as any)._fullLayout, x = full?.xaxis, y = full?.yaxis;
      if (!x?.p2d || !y?.range) return null;
      const rect = node.getBoundingClientRect(), px = pointer.x - rect.left - x._offset, py = pointer.y - rect.top - y._offset;
      return { x, y, px, py, inPlot: px >= 0 && px <= x._length && py >= 0 && py <= y._length, onAxis: px > x._length && py >= 0 && py <= y._length };
    };
    const follow = () => {
      cancelAnimationFrame(pointerFrame);
      pointerFrame = requestAnimationFrame(() => {
        const at = where();
        if (!at || !active) return;
        box.style.cursor = zoomable && (at.onAxis || stretch) && !fixedY ? "ns-resize" : "";
        if (stretch) {
          const [low, high] = stretch.range, middle = (low + high) / 2, half = (high - low) / 2 * Math.exp((pointer.y - stretch.from) * .006);
          stretched = true;
          void loadPlotly().then(Plotly => active && Plotly.relayout(node, { "yaxis.range": [middle - half, middle + half], "yaxis.autorange": false }));
          return;
        }
        if (!at.inPlot || pointer.buttons || drawnAt) { reset(); if (zoomable) linkNow.current?.cursor(member.current, null); return; }
        if (ownMode) {
          const points = pointsAt(at.px, at.py, at.x, at.y);
          // Lines get the crosshair; bars light their column instead.
          if (points.length && showAt) showAt(points, !points.some(point => point.data.type === "bar"), { clientX: pointer.x, clientY: pointer.y } as MouseEvent); else reset();
          return;
        }
        const day = at.x.p2d(at.px);
        member.current.cursor(day);
        linkNow.current?.cursor(member.current, day);
      });
    };
    const move = (event: PointerEvent) => { pointer = { x: event.clientX, y: event.clientY, buttons: event.buttons }; if (zoomable || ownMode) follow(); };
    // Each read trace's positions along the axis read, in pixels; laid out again only when that axis moves.
    const placed = new Map<Record<string, any>, { key: string; at: Float64Array }>();
    /** The points of the column (or row) nearest the pointer: every read trace's value there, as Plotly's x (or y) reading gives them. */
    const pointsAt = (px: number, py: number, x: any, y: any): HoverPoint[] => {
      const axis = ownMode === "y" ? y : x, position = ownMode === "y" ? py : px, key = `${axis.range}:${axis._length}`;
      const found: { trace: Record<string, any>; index: number; gap: number }[] = [];
      for (const trace of traces) {
        const along = (ownMode === "y" ? trace.y : trace.x) as unknown[] | undefined;
        if (trace.hoverinfo === "skip" || trace.meta?.pad || trace.visible === false || !Array.isArray(along) || !along.length) continue;
        let spot = placed.get(trace);
        if (!spot || spot.key !== key) { spot = { key, at: Float64Array.from(along, value => axis.d2p(value)) }; placed.set(trace, spot); }
        let index = -1, gap = Infinity;
        for (let at = 0; at < spot.at.length; at++) { const away = Math.abs(spot.at[at] - position); if (away < gap) { gap = away; index = at; } }
        if (index >= 0) found.push({ trace, index, gap });
      }
      if (!found.length) return [];
      const nearest = Math.min(...found.map(item => item.gap));
      return found.filter(item => item.gap <= nearest + 1 && typeof (ownMode === "y" ? item.trace.x : item.trace.y)?.[item.index] === "number")
        .map(({ trace, index }) => ({ x: trace.x[index], y: trace.y[index], pointIndex: index, data: trace, xaxis: x, yaxis: y }) as HoverPoint);
    };
    const down = (event: PointerEvent) => {
      pointer = { x: event.clientX, y: event.clientY, buttons: event.buttons };
      const at = zoomable && !fixedY ? where() : null;
      if (!at?.onAxis) return;
      event.preventDefault();
      stretch = { from: event.clientY, range: [Number(at.y.range[0]), Number(at.y.range[1])] };
      box.setPointerCapture(event.pointerId);
      reset();
    };
    const up = (event: PointerEvent) => { if (!stretch) return; stretch = null; if (box.hasPointerCapture(event.pointerId)) box.releasePointerCapture(event.pointerId); };
    const double = (event: MouseEvent) => {
      pointer = { ...pointer, x: event.clientX, y: event.clientY };
      const at = zoomable ? where() : null;
      if (!at?.onAxis || !stretched) return;
      stretched = false;
      const x = at.x, patch = fitY(x.range[0], x.range[1]) ?? { "yaxis.autorange": true };
      void loadPlotly().then(Plotly => active && Plotly.relayout(node, patch));
    };
    box.addEventListener("mouseleave", leave);
    box.addEventListener("pointerdown", press);
    box.addEventListener("pointerdown", down);
    box.addEventListener("pointermove", move);
    box.addEventListener("pointerup", up);
    box.addEventListener("dblclick", double);
    window.addEventListener("pointerup", release);
    return () => {
      stopAlign?.();
      active = false; clearTimeout(pointTimer); cancelAnimationFrame(frame); cancelAnimationFrame(pointerFrame); clearTimeout(wheelStop); node.removeEventListener("wheel", wheel);
      box.removeEventListener("mouseleave", leave); box.removeEventListener("pointerdown", press); box.removeEventListener("pointerdown", down); box.removeEventListener("pointermove", move);
      box.removeEventListener("pointerup", up); box.removeEventListener("dblclick", double); window.removeEventListener("pointerup", release); box.style.cursor = "";
    };
  }, [shown]);
  // The chart is as big as its box: when the box changes (a divider dragged, a panel resized) the chart is laid out again.
  useEffect(() => {
    const holder = root.current, node = element.current;
    if (!holder || !node || typeof ResizeObserver === "undefined") return;
    let frame = 0, last = "";
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const size = `${holder.clientWidth}x${holder.clientHeight}`;
        if (size === last) return;
        last = size;
        // A box with no height of its own (its parent sizes to content) gets the figure's own, or a usual one.
        if (holder.clientHeight < 8 && holder.clientWidth > 0) { holder.style.height = `${height ?? 340}px`; return; }
        if ((node as any)._fullLayout) void loadPlotly().then(Plotly => { Plotly.Plots.resize(node); settle.current(); });
      });
    });
    observer.observe(holder);
    return () => { cancelAnimationFrame(frame); observer.disconnect(); };
  }, [height]);
  useEffect(() => {
    const node = element.current;
    return () => { if (node) void loadPlotly().then(Plotly => Plotly.purge(node)).catch(() => undefined); };
  }, []);
  if (failed) return <p className="qc-plot-note" role="alert">This chart could not be drawn. {failed}</p>;
  if ((figure.data ?? []).length > 0 && (figure.data ?? []).every(drawsNothing)) return <p className="qc-plot-note">No values to draw.</p>;
  const slot = section?.slot ?? null;
  const scale = (value: boolean, change: (log: boolean) => void) => <TxSegmented label="Value scale" value={value ? "log" : "linear"} onChange={next => change(next === "log")}
    options={[{ value: "linear", label: "Linear" }, { value: "log", label: "Log" }]} />;
  const head = group && slot ? (group.members[0] === member.current ? createPortal(<>
    <Keys keys={group.keys()} />{group.scales() && scale(group.log, next => group.setLog(next))}</>, slot) : null) : null;
  return <div ref={root} className="qc-plot" role="img" aria-label={ariaLabel} style={height ? { height } : undefined}>
    <div ref={element} className="qc-plot-area" /><div ref={shade} className="qc-plot-shade" /><div ref={cross} className="qc-plot-cross" /><div ref={marks} className="qc-plot-marks" /><div ref={tip} className="qc-plot-tip" />
    {!slot && (own.keys.length > 0 || own.scales) && <div className="qc-plot-keys"><Keys keys={own.keys} />{own.scales && scale(ownLog, setOwnLog)}</div>}
    {head}
  </div>;
}
