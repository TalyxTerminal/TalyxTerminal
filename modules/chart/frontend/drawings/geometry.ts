import type { GeometryBar as Bar } from "./types";
import { getStroke } from "perfect-freehand";
import simplify from "simplify-js";
import { tokenValue } from "@talyx/sdk";
import { overlayLevels, overlayTool, type OverlayType } from "./registry";
import type { OverlayPoint, OverlaySettings } from "./types";
import { positionMetrics } from "./positionMetrics";
import {
  anchoredVwapAvailable,
  buildRegressionResult,
  createAnchoredVwapAccumulator,
  type AnchoredVwapAccumulator,
  type AnchoredVwapPoint,
  type RegressionResult,
} from "./statisticalOverlays";

export interface OverlayPixelPoint {
  x: number;
  y: number;
}

export interface OverlayBounds {
  width: number;
  height: number;
}

export interface OverlayReadout {
  kind: "regression" | "fib" | "gann" | "range" | "anchored_vwap" | "position";
  label: string;
  values: Record<string, number | string | null>;
}

export interface OverlayRuntimeData {
  selected?: boolean;
  toolType: OverlayType;
  text: string | null;
  readout: OverlayReadout | null;
  levels?: readonly number[];
  settings?: OverlaySettings;
  anchorValues?: readonly number[];
  /** Exact value-domain levels, projected through the active pane axis by the adapter. */
  levelValues?: readonly number[];
  projectedLevels?: readonly OverlayPixelPoint[];
  anchoredVwap?: readonly Pick<AnchoredVwapPoint, "dataIndex" | "timestamp" | "value">[];
  regression?: RegressionResult;
  /** Runtime-only pixel projections supplied by the vendor axes; never persisted. */
  projectedCurve?: readonly OverlayPixelPoint[];
  projectedRegression?: {
    base: readonly [OverlayPixelPoint, OverlayPixelPoint];
    upper?: readonly [OverlayPixelPoint, OverlayPixelPoint];
    lower?: readonly [OverlayPixelPoint, OverlayPixelPoint];
  };
}

export interface OverlayGeometryFigure {
  key?: string;
  type: "line" | "polygon" | "rect" | "circle" | "text";
  attrs: Record<string, unknown>;
  styles?: Record<string, unknown>;
}

export function drawingColorToken(name: string): string {
  const value=tokenValue(name);
  const alias=/^var\((--tx-[\w-]+)\)$/.exec(value);
  return alias ? tokenValue(alias[1]) : value;
}

function finite(value: number): number {
  return Number.isFinite(value) ? value : 0;
}

function compact(value: number): string {
  if (!Number.isFinite(value)) return "n/a";
  return Math.abs(value) >= 1000 ? value.toFixed(0) : value.toFixed(2);
}

function elapsedLabel(milliseconds: number): string {
  const absolute = Math.abs(milliseconds);
  const days = absolute / 86_400_000;
  if (days >= 1) return `${days.toFixed(days >= 10 ? 0 : 1)}d`;
  const hours = absolute / 3_600_000;
  if (hours >= 1) return `${hours.toFixed(hours >= 10 ? 0 : 1)}h`;
  return `${(absolute / 60_000).toFixed(0)}m`;
}

function rangeReadout(type: OverlayType, points: readonly OverlayPoint[], bars: readonly Bar[] = [], settings?: OverlaySettings): OverlayReadout {
  const [start, end = start] = points;
  const delta = finite((end?.value ?? 0) - (start?.value ?? 0));
  const percent = start?.value === 0 ? 0 : finite(delta / (start?.value ?? 1) * 100);
  const durationMs = finite((end?.timestamp ?? 0) - (start?.timestamp ?? 0));
  const price = `${delta >= 0 ? "+" : ""}${compact(delta)} (${percent >= 0 ? "+" : ""}${percent.toFixed(2)}%)`;
  const duration = elapsedLabel(durationMs);
  const barsCount = bars.filter(bar => bar.time >= Math.min(start?.timestamp ?? 0, end?.timestamp ?? 0) && bar.time <= Math.max(start?.timestamp ?? 0, end?.timestamp ?? 0)).length;
  const configured = [settings?.showPriceRange ? compact(delta) : null, settings?.showPercentChange ? `${percent.toFixed(2)}%` : null,
    settings?.showBarsRange ? `${barsCount} bars` : null, settings?.showDateTimeRange ? duration : null,
    settings?.showDistance ? `${Math.max(0,barsCount-1)} periods` : null].filter(Boolean).join(" · ");
  const label = configured || (type === "date_range"
    ? duration
    : type === "price_range"
      ? price
      : `${price} · ${duration}`);
  return { kind: "range", label, values: { delta, percent, durationMs, barsCount } };
}

function fibReadout(
  type: OverlayType,
  points: readonly OverlayPoint[],
  levels: readonly number[],
): OverlayReadout {
  const [first, second = first, third = second] = points;
  const base = finite((second?.value ?? 0) - (first?.value ?? 0));
  const anchor = type === "fib_extension" ? third?.value ?? 0 : first?.value ?? 0;
  return {
    kind: "fib",
    label: `${overlayTool(type).label} · Δ ${compact(base)}`,
    values: { base, anchor, ratios: levels.join(",") },
  };
}

function gannReadout(
  type: OverlayType,
  points: readonly OverlayPoint[],
  levels: readonly number[],
): OverlayReadout {
  const [first, second = first] = points;
  const timeSpanMs = finite((second?.timestamp ?? 0) - (first?.timestamp ?? 0));
  const priceSpan = finite((second?.value ?? 0) - (first?.value ?? 0));
  return {
    kind: "gann",
    label: `${overlayTool(type).label} · Δ ${compact(priceSpan)} / ${elapsedLabel(timeSpanMs)}`,
    values: { timeSpanMs, priceSpan, ratios: levels.join(",") },
  };
}

function anchoredVwapReadout(accumulator: AnchoredVwapAccumulator): OverlayReadout {
  const latest = accumulator.points[accumulator.points.length - 1];
  const vwap = anchoredVwapAvailable(accumulator) ? latest?.value ?? null : null;
  return {
    kind: "anchored_vwap",
    label: vwap == null ? "AVWAP unavailable" : `AVWAP ${compact(vwap)}`,
    values: {
      vwap,
      cumulativeVolume: latest?.cumulativeVolume ?? 0,
      observations: accumulator.points.length,
      from: accumulator.anchor,
      to: latest?.timestamp ?? accumulator.anchor,
    },
  };
}

export function buildRegressionRuntimeData(result: RegressionResult | null): OverlayRuntimeData {
  const readout: OverlayReadout | null = result
    ? {
        kind: "regression",
        label: result.settings.showPearson && result.pearson !== null
          ? `R ${result.pearson.toFixed(3)}`
          : "",
        values: {
          slope: result.slope,
          intercept: result.intercept,
          sigma: result.sigma,
          pearson: result.pearson,
          source: result.settings.source,
        },
      }
    : null;
  return {
    toolType: "regression_trend",
    text: null,
    readout,
    ...(result ? { regression: result } : {}),
  };
}

function positionReadout(type: "long_position" | "short_position", points: readonly OverlayPoint[], settings?:OverlaySettings, pointValue?:number|null): OverlayReadout {
  const metrics=positionMetrics(type,points,settings,pointValue);
  const ratio=`${type === "long_position" ? "Long" : "Short"} R:R ${metrics.rewardRisk == null ? "n/a" : metrics.rewardRisk.toFixed(2)}`;
  const sized=settings?.accountSize===undefined?"":metrics.quantity===null?" · Quantity unavailable":` · Qty ${metrics.quantity.toFixed(settings.qtyPrecision??2)} · Target ${compact(metrics.targetBalance!)} · Stop ${compact(metrics.stopBalance!)}`;
  return {
    kind: "position",
    label: settings?.compact?ratio:`${ratio} · risk ${compact(metrics.risk)}${sized}`,
    values: {...metrics},
  };
}

export function buildOverlayRuntimeData(
  type: OverlayType,
  points: readonly OverlayPoint[],
  bars: readonly Bar[],
  text?: string,
  settings?: OverlaySettings,
  positionUnitValue?:number|null,
): OverlayRuntimeData {
  let readout: OverlayReadout | null = null;
  const contract = overlayTool(type).readout;
  const levels = overlayLevels(type, settings?.levels);
  const regression = contract?.kind === "regression"
    ? buildRegressionResult(points, bars, settings)
    : null;
  if (regression) readout = buildRegressionRuntimeData(regression).readout;
  else if (contract?.kind === "fib") readout = fibReadout(type, points, levels);
  else if (contract?.kind === "gann") readout = gannReadout(type, points, levels);
  else if (contract?.kind === "range") readout = rangeReadout(type, points, bars, settings);
  else if (["trendline","ray","extended_line","arrow"].includes(type) && (settings?.showPriceRange || settings?.showPercentChange || settings?.showBarsRange || settings?.showDateTimeRange || settings?.showDistance)) readout = rangeReadout(type, points, bars, settings);
  else if (contract?.kind === "anchored_vwap") {
    readout = anchoredVwapReadout(createAnchoredVwapAccumulator(points, bars));
  }
  else if (contract?.kind === "position") readout = positionReadout(type as "long_position" | "short_position", points, settings, positionUnitValue);
  else if (type === "price_label") {
    const value = points[0]?.value ?? null;
    readout = { kind: "range", label: value == null ? "Price unavailable" : compact(value), values: { value } };
  }
  return {
    toolType: type,
    text: text ?? null,
    readout,
    ...(regression ? { regression } : {}),
    ...(levels.length === 0 ? {} : { levels: [...levels] }),
    ...(settings === undefined ? {} : { settings: structuredClone(settings) }),
    ...(settings?.showPriceLabels ? {anchorValues:points.map(point=>point.value)} : {}),
    ...(type === "fib" || type === "fib_extension" ? { levelValues: fibLevelValues(type, points, levels, settings) } : {}),
  };
}

export function fibLevelValues(type: "fib" | "fib_extension", points: readonly OverlayPoint[], levels: readonly number[], settings?: OverlaySettings): number[] {
  const [first, second = first, third = second] = points;
  const start = settings?.reverse ? second?.value ?? 0 : first?.value ?? 0;
  const end = settings?.reverse ? first?.value ?? 0 : second?.value ?? 0;
  const anchor = type === "fib_extension" ? third?.value ?? 0 : start;
  return levels.map(ratio => settings?.logLevels && start > 0 && end > 0 && anchor > 0
    ? anchor * Math.exp(Math.log(end / start) * ratio)
    : anchor + (end - start) * ratio);
}

function levelRow(runtime: OverlayRuntimeData, ratio: number): { visible: boolean; styles?: Record<string, unknown>; text: string } {
  const settings = runtime.settings;
  const row = settings?.levelStyles?.find(candidate => candidate.value === ratio);
  const index = runtime.levels?.indexOf(ratio) ?? -1;
  const value = runtime.levelValues?.[index];
  const ratioLabel = settings?.levelLabelFormat === "percent" ? `${(ratio * 100).toFixed(1)}%` : String(ratio);
  const text = [row?.text, settings?.showLevels === false ? undefined : ratioLabel,
    settings?.showPrices && value !== undefined ? `(${compact(value)})` : undefined].filter(Boolean).join(" ");
  const levelLine=line([],{...(row?.color?{color:row.color}:{}),...figureLineStyle(row?.lineWidth,row?.lineStyle)});
  return { visible: row?.visible !== false, ...(Object.keys(levelLine.styles??{}).length?{styles:levelLine.styles}:{}), text };
}

function levelLabel(runtime: OverlayRuntimeData, ratio: number, left: number, right: number, y: number): OverlayGeometryFigure[] {
  if (runtime.settings?.showLabels === false) return [];
  const row = levelRow(runtime, ratio);
  if (!row.text) return [];
  const position = runtime.settings?.labelPosition ?? "left";
  const x = position === "right" ? right - 4 : position === "center" ? (left + right) / 2 : left + 4;
  return [{ type: "text", attrs: { x, y: y + (runtime.settings?.labelAlignment === "bottom" ? 3 : -3), text: row.text,
    baseline: runtime.settings?.labelAlignment === "bottom" ? "top" : "bottom", align: position === "right" ? "right" : position === "center" ? "center" : "left" }, ...(row.styles?.color ? { styles: {color:row.styles.color} } : {}) }];
}

function horizontalFib(type: "fib" | "fib_extension", a: OverlayPixelPoint, b: OverlayPixelPoint, c: OverlayPixelPoint, bounds: OverlayBounds, runtime: OverlayRuntimeData): OverlayGeometryFigure[] {
  const reverse = runtime.settings?.reverse === true;
  const start = reverse ? b : a;
  const end = reverse ? a : b;
  const anchor = type === "fib_extension" ? c : start;
  const left = runtime.settings?.extendLeft ? 0 : type === "fib_extension" ? c.x : Math.min(a.x, b.x);
  const right = runtime.settings?.extendRight === true || type === "fib_extension" && runtime.settings?.extendRight !== false ? bounds.width : Math.max(a.x, b.x, ...(type === "fib_extension" ? [c.x] : []));
  const figures: OverlayGeometryFigure[] = [];
  let previous: { y: number; styles?: Record<string, unknown> } | null = null;
  (runtime.levels ?? []).forEach((ratio, index) => {
    const row = levelRow(runtime, ratio);
    if (!row.visible) return;
    const y = runtime.projectedLevels?.[index]?.y ?? anchor.y + (end.y - start.y) * ratio;
    // Level colors belong to the line and label. Backgrounds inherit the
    // drawing fill color and opacity instead of becoming opaque level colors.
    if (previous && runtime.settings?.showBackground !== false) figures.push({ type: "rect", key: `background:${ratio}`, attrs: { x: left, y: Math.min(previous.y, y), width: right - left, height: Math.abs(previous.y - y) } });
    figures.push({ ...line([{ x: left, y }, { x: right, y }], row.styles), key: `level:${ratio}` }, ...levelLabel(runtime, ratio, left, right, y));
    previous = { y, styles: row.styles };
  });
  if (runtime.settings?.showTrendLine !== false) figures.push({...line(type === "fib_extension" ? [a, b, c] : [a, b], {...figureLineStyle(runtime.settings?.trendLineWidth,runtime.settings?.trendLineStyle??"dashed"),...(runtime.settings?.trendLineColor?{color:runtime.settings.trendLineColor}:{})}),key:"trend-line"});
  return figures;
}

const line = (coordinates: OverlayPixelPoint[], styles?: Record<string, unknown>): OverlayGeometryFigure => ({
  type: "line",
  attrs: { coordinates },
  ...(styles ? { styles } : {}),
});
const segmentedLines = (
  coordinates: readonly OverlayPixelPoint[],
  styles?: Record<string, unknown>,
): OverlayGeometryFigure[] => coordinates.slice(0, -1).map((point, index) => ({
  ...line([point, coordinates[index + 1]], styles),
  key: `segment:${index}`,
}));
const polygon = (
  coordinates: OverlayPixelPoint[],
  styles?: Record<string, unknown>,
): OverlayGeometryFigure => ({ type: "polygon", attrs: { coordinates }, ...(styles ? { styles } : {}) });
const label = (point: OverlayPixelPoint, text: string): OverlayGeometryFigure => ({
  type: "text",
  attrs: { x: point.x, y: point.y, text, baseline: "bottom" },
});
/** A line's own text, as on TradingView: along the line from `a` to `b`, just above it and always upright, at the end
    or the middle the text position names, running from an end towards the other. */
function lineText(a: OverlayPixelPoint, b: OverlayPixelPoint, text: string, position: OverlaySettings["textPosition"]): OverlayGeometryFigure {
  let angle = Math.atan2(b.y - a.y, b.x - a.x);
  if (angle > Math.PI / 2) angle -= Math.PI; else if (angle <= -Math.PI / 2) angle += Math.PI;
  const [at, towards] = position === "left" ? [a, b] : position === "right" ? [b, a] : [{ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }, null];
  const align = towards === null ? "center" : (towards.x - at.x) * Math.cos(angle) + (towards.y - at.y) * Math.sin(angle) >= 0 ? "left" : "right";
  return { type: "text", attrs: { x: at.x + 4 * Math.sin(angle), y: at.y - 4 * Math.cos(angle), text, align, baseline: "bottom", angle } };
}

function figureLineStyle(width?:number,style?:"solid"|"dashed"|"dotted"):Record<string,unknown> {
  return {...(width===undefined?{}:{size:width}),...(style===undefined?{}:{style:style==="dotted"?"dashed":style,dashedValue:style==="dotted"?[1,3]:[6,4]})};
}

function arrow(start: OverlayPixelPoint, end: OverlayPixelPoint): OverlayGeometryFigure[] {
  const angle = Math.atan2(end.y - start.y, end.x - start.x);
  const size = 9;
  const wing = Math.PI / 7;
  return [
    line([start, end]),
    polygon([
      end,
      { x: end.x - size * Math.cos(angle - wing), y: end.y - size * Math.sin(angle - wing) },
      { x: end.x - size * Math.cos(angle + wing), y: end.y - size * Math.sin(angle + wing) },
    ]),
  ];
}

function freehand(
  coordinates: readonly OverlayPixelPoint[],
  kind: "brush" | "highlighter",
): OverlayGeometryFigure[] {
  if (coordinates.length < 2) return [];
  const simplified = simplify(
    coordinates.map(({ x, y }) => ({ x, y })),
    kind === "highlighter" ? 0.75 : 0.45,
    false,
  );
  const outline = getStroke(
    simplified.map(({ x, y }) => [x, y] as [number, number]),
    {
      size: kind === "highlighter" ? 12 : 3.5,
      thinning: kind === "highlighter" ? 0 : 0.55,
      smoothing: 0.68,
      streamline: 0.48,
      simulatePressure: kind !== "highlighter",
      start: { cap: true, taper: kind === "highlighter" ? 0 : 6 },
      end: { cap: true, taper: kind === "highlighter" ? 0 : 6 },
    },
  );
  if (outline.length < 3) return segmentedLines([...coordinates]);
  return [polygon(
    outline.map(([x, y]) => ({ x, y })),
    kind === "highlighter" ? { color: drawingColorToken("--tx-ember-wash") } : undefined,
  )];
}

function quadraticArc(
  start: OverlayPixelPoint,
  control: OverlayPixelPoint,
  end: OverlayPixelPoint,
): OverlayGeometryFigure[] {
  const points = Array.from({ length: 25 }, (_, index) => {
    const t = index / 24;
    const inverse = 1 - t;
    return {
      x: inverse * inverse * start.x + 2 * inverse * t * control.x + t * t * end.x,
      y: inverse * inverse * start.y + 2 * inverse * t * control.y + t * t * end.y,
    };
  });
  return segmentedLines(points);
}

function labeledPattern(
  coordinates: readonly OverlayPixelPoint[],
  labels: readonly string[],
): OverlayGeometryFigure[] {
  return [
    ...segmentedLines(coordinates),
    ...coordinates.map((point, index) => label(
      { x: point.x + 5, y: point.y - 5 },
      labels[index] ?? String(index + 1),
    )),
  ];
}

function extendedLine(a: OverlayPixelPoint, b: OverlayPixelPoint, bounds: OverlayBounds): OverlayGeometryFigure {
  if (a.x === b.x) return line([{ x: a.x, y: 0 }, { x: a.x, y: bounds.height }]);
  const slope = (b.y - a.y) / (b.x - a.x);
  return line([{ x: 0, y: a.y - slope * a.x }, { x: bounds.width, y: a.y + slope * (bounds.width - a.x) }]);
}

function rayEnd(
  start: OverlayPixelPoint,
  directionPoint: OverlayPixelPoint,
  bounds: OverlayBounds,
): OverlayPixelPoint {
  const dx = directionPoint.x - start.x;
  const dy = directionPoint.y - start.y;
  if (dx === 0 && dy === 0) return directionPoint;
  const candidates = [
    dx > 0 ? (bounds.width - start.x) / dx : dx < 0 ? -start.x / dx : Number.POSITIVE_INFINITY,
    dy > 0 ? (bounds.height - start.y) / dy : dy < 0 ? -start.y / dy : Number.POSITIVE_INFINITY,
  ].filter((value) => Number.isFinite(value) && value >= 1);
  const scale = Math.min(...candidates);
  if (!Number.isFinite(scale)) return directionPoint;
  return { x: start.x + dx * scale, y: start.y + dy * scale };
}

function channelOffset(
  start: OverlayPixelPoint,
  end: OverlayPixelPoint,
  widthPoint: OverlayPixelPoint,
): OverlayPixelPoint {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const denominator = dx * dx + dy * dy;
  if (denominator === 0) {
    return { x: widthPoint.x - start.x, y: widthPoint.y - start.y };
  }
  const projection = ((widthPoint.x - start.x) * dx + (widthPoint.y - start.y) * dy) /
    denominator;
  const projected = { x: start.x + dx * projection, y: start.y + dy * projection };
  return { x: widthPoint.x - projected.x, y: widthPoint.y - projected.y };
}

function parallelChannel(
  start: OverlayPixelPoint,
  end: OverlayPixelPoint,
  widthPoint: OverlayPixelPoint,
  bounds:OverlayBounds,
  runtime:OverlayRuntimeData,
): OverlayGeometryFigure[] {
  const offset = channelOffset(start, end, widthPoint);
  const upperStart = { x: start.x + offset.x, y: start.y + offset.y };
  const upperEnd = { x: end.x + offset.x, y: end.y + offset.y };
  const extend=(a:OverlayPixelPoint,b:OverlayPixelPoint):OverlayPixelPoint[]=>[runtime.settings?.extendLeft?rayEnd(b,a,bounds):a,runtime.settings?.extendRight?rayEnd(a,b,bounds):b];
  const base=extend(start,end),upper=extend(upperStart,upperEnd);
  const figures:OverlayGeometryFigure[]=runtime.settings?.showBackground===false?[]:[polygon([base[0],base[1],upper[1],upper[0]])];
  for(const ratio of runtime.levels??[0,1]) {
    const row=levelRow(runtime,ratio);if(!row.visible)continue;
    const a={x:start.x+offset.x*ratio,y:start.y+offset.y*ratio},b={x:end.x+offset.x*ratio,y:end.y+offset.y*ratio};
    const coordinates=extend(a,b);
    figures.push({...line(coordinates,row.styles),key:`level:${ratio}`});
    if(runtime.settings?.showLabels===true)figures.push({...label(coordinates[0],row.text),...(row.styles?.color?{styles:{color:row.styles.color}}:{})});
  }
  if(runtime.text){const at=runtime.settings?.textPosition==="left"?start:runtime.settings?.textPosition==="right"?end:{x:(start.x+end.x)/2,y:(start.y+end.y)/2};figures.push(label({x:at.x+offset.x/2,y:at.y+offset.y/2},runtime.text));}
  return figures;
}

function priceChannel(
  start: OverlayPixelPoint,
  end: OverlayPixelPoint,
  widthPoint: OverlayPixelPoint,
): OverlayGeometryFigure[] {
  const offset = channelOffset(start, end, widthPoint);
  const upper = [
    { x: start.x + offset.x, y: start.y + offset.y },
    { x: end.x + offset.x, y: end.y + offset.y },
  ];
  const lower = [
    { x: start.x - offset.x, y: start.y - offset.y },
    { x: end.x - offset.x, y: end.y - offset.y },
  ];
  return [
    line([upper[0], upper[1]]),
    line([start, end], { style: "dashed" }),
    line([lower[0], lower[1]]),
  ];
}

function readoutFigure(runtime: OverlayRuntimeData, point: OverlayPixelPoint): OverlayGeometryFigure[] {
  if(runtime.settings?.showAlwaysStats === false && runtime.selected !== true) return [];
  return runtime.readout ? [label({ x: point.x + 8, y: point.y - 6 }, runtime.readout.label)] : [];
}

function lineDecorations(a:OverlayPixelPoint,b:OverlayPixelPoint,bounds:OverlayBounds,runtime:OverlayRuntimeData):OverlayGeometryFigure[] {
      const at=(position:OverlaySettings["textPosition"])=>position==="left"?a:position==="right"?b:{x:(a.x+b.x)/2,y:(a.y+b.y)/2};
      return [
        ...(runtime.settings?.startEnd === "arrow" ? arrow(b,a).slice(1).map(figure=>({...figure,key:"start-arrow"})) : []),
        ...(runtime.settings?.endEnd === "arrow" ? arrow(a,b).slice(1).map(figure=>({...figure,key:"end-arrow"})) : []),
        ...(runtime.settings?.showMiddlePoint ? [{type:"circle" as const,key:"middle",attrs:{x:(a.x+b.x)/2,y:(a.y+b.y)/2,r:3}}] : []),
        ...(runtime.settings?.showPriceLabels ? [a,b].map((point,index)=>({...label({x:bounds.width-4,y:point.y},compact(runtime.anchorValues?.[index]??0)),key:`price:${index}`,attrs:{x:bounds.width-4,y:point.y,text:compact(runtime.anchorValues?.[index]??0),align:"right",baseline:"bottom"}})) : []),
        ...(runtime.text ? [lineText(a, b, runtime.text, runtime.settings?.textPosition)] : []),
        ...readoutFigure(runtime, runtime.settings?.statsPosition ? at(runtime.settings.statsPosition) : b),
        ...(runtime.settings?.showAngle && (runtime.settings.showAlwaysStats!==false || runtime.selected) ? [label({x:b.x+4,y:b.y-18},`${(Math.atan2(a.y-b.y,b.x-a.x)*180/Math.PI).toFixed(1)}°`)] : [])];
}

function straightLineAnnotation(a:OverlayPixelPoint,b:OverlayPixelPoint,runtime:OverlayRuntimeData,bounds:OverlayBounds):OverlayGeometryFigure[] {
  const at=runtime.settings?.textPosition==="left"?a:runtime.settings?.textPosition==="right"?b:{x:(a.x+b.x)/2,y:(a.y+b.y)/2};
  return [...(runtime.text?[label({x:at.x+4,y:at.y-4},runtime.text)]:[]),...(runtime.settings?.showPriceLabels?[{...label({x:bounds.width-4,y:a.y},compact(runtime.anchorValues?.[0]??0)),key:"price:0"}]:[])];
}

export function buildOverlayGeometry(
  type: OverlayType,
  coordinates: readonly OverlayPixelPoint[],
  bounds: OverlayBounds,
  runtime: OverlayRuntimeData,
): OverlayGeometryFigure[] {
  const a = coordinates[0] ?? { x: 0, y: 0 };
  const b = coordinates[1] ?? a;
  const c = coordinates[2] ?? b;
  const stream = [...coordinates];
  switch (type) {
    // Vendor-backed tools still participate in this pure geometry contract. KLineCharts
    // owns their live gesture, while these figures are the single source for previews,
    // precise hit tests, and golden behavior tests outside the vendor instance.
    case "trendline": {
      const start = runtime.settings?.extendLeft ? rayEnd(b, a, bounds) : a;
      const end = runtime.settings?.extendRight ? rayEnd(a, b, bounds) : b;
      return [line([start,end]),...lineDecorations(a,b,bounds,runtime)];
    }
    case "ray": return [line([a, rayEnd(a, b, bounds)]),...lineDecorations(a,b,bounds,runtime)];
    case "hline": return [line([{ x: 0, y: a.y }, { x: bounds.width, y: a.y }]),...straightLineAnnotation({x:0,y:a.y},{x:bounds.width,y:a.y},runtime,bounds)];
    case "vline": return [line([{ x: a.x, y: 0 }, { x: a.x, y: bounds.height }]),...straightLineAnnotation({x:a.x,y:0},{x:a.x,y:bounds.height},runtime,bounds)];
    case "parallel_channel": return parallelChannel(a, b, c,bounds,runtime);
    case "price_channel": return priceChannel(a, b, c);
    case "extended_line": return [extendedLine(a, b, bounds),...lineDecorations(a,b,bounds,runtime)];
    case "horizontal_ray": return [line([a, { x: bounds.width, y: a.y }]),...straightLineAnnotation(a,{x:bounds.width,y:a.y},runtime,bounds)];
    case "cross_line": return [line([{ x: 0, y: a.y }, { x: bounds.width, y: a.y }]), line([{ x: a.x, y: 0 }, { x: a.x, y: bounds.height }]),...straightLineAnnotation({x:0,y:a.y},{x:bounds.width,y:a.y},runtime,bounds)];
    case "arrow": return [...arrow(a, b),...lineDecorations(a,b,bounds,runtime)];
    case "info_line": return [line([runtime.settings?.extendLeft?rayEnd(b,a,bounds):a,runtime.settings?.extendRight?rayEnd(a,b,bounds):b]),...lineDecorations(a,b,bounds,runtime)];
    case "trend_angle": {
      const degrees = Math.atan2(a.y - b.y, b.x - a.x) * 180 / Math.PI;
      return [line([runtime.settings?.extendLeft ? rayEnd(b,a,bounds) : a, runtime.settings?.extendRight ? rayEnd(a,b,bounds) : b]), ...(runtime.settings?.showAngle === false ? [] : [label({ x: a.x + 8, y: a.y - 8 }, `${degrees.toFixed(1)}°`)]),...(runtime.text?[lineText(a,b,runtime.text,runtime.settings?.textPosition)]:[])];
    }
    case "path":
    case "polyline": return segmentedLines(stream);
    case "brush": return freehand(stream, "brush").map((figure) => ({ ...figure, key: "path" }));
    case "highlighter": return freehand(stream, "highlighter").map((figure) => ({ ...figure, key: "path" }));
    case "regression_trend": {
      const projected = runtime.projectedRegression;
      if (!projected) return [];
      const figures = [line([...projected.base])];
      if (projected.upper) figures.push(line([...projected.upper]));
      if (projected.lower) figures.push(line([...projected.lower]));
      return runtime.readout?.label
        ? figures.concat(readoutFigure(runtime, projected.base[1]))
        : figures;
    }
    case "flat_top_bottom": return [polygon([{ x: a.x, y: a.y }, { x: b.x, y: a.y }, { x: b.x, y: c.y }, { x: a.x, y: c.y }])];
    case "pitchfork": {
      const midpoint = { x: (b.x + c.x) / 2, y: (b.y + c.y) / 2 };
      const delta = { x: midpoint.x - a.x, y: midpoint.y - a.y };
      return [line([a, midpoint]), line([b, { x: b.x + delta.x, y: b.y + delta.y }]), line([c, { x: c.x + delta.x, y: c.y + delta.y }])];
    }
    case "schiff_pitchfork": {
      const origin = { x: (a.x + b.x) / 2, y: a.y };
      const midpoint = { x: (b.x + c.x) / 2, y: (b.y + c.y) / 2 };
      const delta = { x: midpoint.x - origin.x, y: midpoint.y - origin.y };
      return [
        line([origin, midpoint]),
        line([b, { x: b.x + delta.x, y: b.y + delta.y }]),
        line([c, { x: c.x + delta.x, y: c.y + delta.y }]),
      ];
    }
    case "fib": return horizontalFib(type, a, b, c, bounds, runtime);
    case "fib_extension": return horizontalFib(type, a, b, c, bounds, runtime);
    case "fib_channel": {
      const offset = { x: c.x - a.x, y: c.y - a.y };
      return (runtime.levels ?? []).flatMap((ratio) => {
        const row = levelRow(runtime, ratio); if (!row.visible) return [];
        const value = runtime.settings?.reverse ? 1 - ratio : ratio;
        const start = { x: a.x + offset.x * value, y: a.y + offset.y * value };
        const end = { x: b.x + offset.x * value, y: b.y + offset.y * value };
        return [line([runtime.settings?.extendLeft ? rayEnd(end, start, bounds) : start, runtime.settings?.extendRight ? rayEnd(start, end, bounds) : end], row.styles), ...levelLabel(runtime, ratio, start.x, end.x, start.y)];
      });
    }
    case "fib_time": {
      const width = b.x - a.x;
      return (runtime.levels ?? []).flatMap((ratio) => {
        const row = levelRow(runtime, ratio); if (!row.visible) return [];
        const x = (runtime.settings?.reverse ? b.x : a.x) + width * ratio * (runtime.settings?.reverse ? -1 : 1);
        return [line([{ x, y: 0 }, { x, y: bounds.height }], row.styles), ...(runtime.settings?.showLabels === false ? [] : [{ ...label({ x: x + 4, y: 18 }, row.text), ...(row.styles?.color ? { styles: {color:row.styles.color} } : {}) }])];
      });
    }
    case "gann_box": {
      const left = Math.min(a.x, b.x); const right = Math.max(a.x, b.x); const top = Math.min(a.y, b.y); const bottom = Math.max(a.y, b.y);
      const figures: OverlayGeometryFigure[] = [{ type: "rect", attrs: { x: left, y: top, width: right - left, height: bottom - top } }];
      for (const level of runtime.levels ?? []) {
        const row = levelRow(runtime, level); if (!row.visible) continue;
        const ratio = runtime.settings?.reverse ? 1 - level : level;
        figures.push(line([{ x: left + (right - left) * ratio, y: top }, { x: left + (right - left) * ratio, y: bottom }], row.styles));
        figures.push(line([{ x: left, y: top + (bottom - top) * ratio }, { x: right, y: top + (bottom - top) * ratio }], row.styles));
        figures.push(...levelLabel(runtime, level, left, right, top + (bottom - top) * ratio));
      }
      return figures.concat(readoutFigure(runtime, b));
    }
    case "gann_fan": return (runtime.levels ?? []).flatMap((ratio) => {
      const row = levelRow(runtime, ratio); if (!row.visible) return [];
      const start = runtime.settings?.reverse ? b : a;
      const end = runtime.settings?.reverse ? a : b;
      const target = { x: end.x, y: start.y + (end.y - start.y) * ratio };
      return [line([runtime.settings?.extendLeft ? rayEnd(target, start, bounds) : start, runtime.settings?.extendRight ? rayEnd(start, target, bounds) : target], row.styles), ...levelLabel(runtime, ratio, start.x, target.x, target.y)];
    });
    case "rect": {
      const left=runtime.settings?.extendLeft?0:Math.min(a.x,b.x),right=runtime.settings?.extendRight?bounds.width:Math.max(a.x,b.x),top=Math.min(a.y,b.y),bottom=Math.max(a.y,b.y);
      const figures:OverlayGeometryFigure[]=[{type:"rect",attrs:{x:left,y:top,width:right-left,height:bottom-top},...(runtime.settings?.showBackground===false?{styles:{style:"stroke"}}:{})}];
      if(runtime.settings?.showMiddleLine)figures.push({...line([{x:left,y:(top+bottom)/2},{x:right,y:(top+bottom)/2}],{...figureLineStyle(runtime.settings.middleLineWidth,runtime.settings.middleLineStyle),...(runtime.settings.middleLineColor?{color:runtime.settings.middleLineColor}:{})}),key:"middle-line"});
      if(runtime.text){const alignment=runtime.settings?.textPosition??"center",vertical=runtime.settings?.textVerticalAlignment??"middle";figures.push({...label({x:alignment==="left"?left+4:alignment==="right"?right-4:(left+right)/2,y:vertical==="top"?top+4:vertical==="bottom"?bottom-4:(top+bottom)/2},runtime.text),attrs:{...label(a,runtime.text).attrs,x:alignment==="left"?left+4:alignment==="right"?right-4:(left+right)/2,y:vertical==="top"?top+4:vertical==="bottom"?bottom-4:(top+bottom)/2,align:alignment,baseline:vertical==="top"?"top":vertical==="bottom"?"bottom":"middle"}});}
      return figures;
    }
    case "rotated_rect": {
      const dx = b.x - a.x; const dy = b.y - a.y; const length = Math.hypot(dx, dy) || 1;
      const width = ((c.x - a.x) * -dy + (c.y - a.y) * dx) / length;
      const offset = { x: -dy / length * width, y: dx / length * width };
      return [polygon([a, b, { x: b.x + offset.x, y: b.y + offset.y }, { x: a.x + offset.x, y: a.y + offset.y }])];
    }
    case "ellipse": {
      const center = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      const rx = Math.abs(b.x - a.x) / 2; const ry = Math.abs(b.y - a.y) / 2;
      return [polygon(Array.from({ length: 32 }, (_, index) => ({ x: center.x + rx * Math.cos(index / 32 * Math.PI * 2), y: center.y + ry * Math.sin(index / 32 * Math.PI * 2) })))];
    }
    case "triangle": return [polygon([a, b, c])];
    case "arc": return quadraticArc(a, b, c);
    case "text": return [label(a, runtime.text ?? "")];
    case "anchored_text": return [line([a, b]), label(b, runtime.text ?? "")];
    case "note": return [{ type: "circle", attrs: { x: a.x, y: a.y, r: 5 } }, label({ x: a.x + 9, y: a.y }, runtime.text ?? "")];
    case "callout": return [...arrow(b, a), label(b, runtime.text ?? "")];
    case "comment": return [
      { type: "circle", attrs: { x: a.x, y: a.y, r: 7 } },
      label({ x: a.x + 11, y: a.y - 1 }, runtime.text ?? ""),
    ];
    case "price_label": return [line([{ x: a.x - 12, y: a.y }, { x: a.x + 12, y: a.y }]), label({ x: a.x + 14, y: a.y }, runtime.readout?.label ?? "Price")];
    case "arrow_marker": return [...arrow({ x: a.x, y: a.y + 14 }, a), ...(runtime.text ? [label({ x: a.x + 8, y: a.y }, runtime.text)] : [])];
    case "xabcd": return [...(runtime.settings?.showBackground===false?[]:[polygon(stream.slice(0,3)),polygon(stream.slice(2,5))]),...labeledPattern(stream,["X","A","B","C","D"])];
    case "abcd": return labeledPattern(stream, ["A", "B", "C", "D"]);
    case "cypher": return [...(runtime.settings?.showBackground===false?[]:[polygon(stream.slice(0,3)),polygon(stream.slice(2,5))]),...labeledPattern(stream,["X","A","B","C","D"])];
    case "head_shoulders": return labeledPattern(stream, ["L", "LS", "N", "H", "N", "RS", "R"]);
    case "elliott_impulse": return labeledPattern(stream, ["0", "1", "2", "3", "4", "5"]);
    case "elliott_correction": return labeledPattern(stream, ["0", "A", "B", "C"]);
    case "elliott_triangle": return labeledPattern(stream, ["0", "A", "B", "C", "D", "E"]);
    case "measure": return [line([a, b]), ...readoutFigure(runtime, b)];
    case "date_range": return [line([{ x: a.x, y: b.y }, b]), line([{ x: a.x, y: b.y - 6 }, { x: a.x, y: b.y + 6 }]), line([{ x: b.x, y: b.y - 6 }, { x: b.x, y: b.y + 6 }]), ...readoutFigure(runtime, b)];
    case "price_range": return [line([{ x: b.x, y: a.y }, b]), line([{ x: b.x - 6, y: a.y }, { x: b.x + 6, y: a.y }]), line([{ x: b.x - 6, y: b.y }, { x: b.x + 6, y: b.y }]), ...readoutFigure(runtime, b)];
    case "date_price_range": return [{ type: "rect", attrs: { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), width: Math.abs(b.x - a.x), height: Math.abs(b.y - a.y) } }, ...readoutFigure(runtime, b)];
    case "anchored_vwap": {
      const curve = runtime.projectedCurve ?? [];
      return curve.length < 2 ? [] : [{ ...line([...curve]), key: "avwap-curve" }];
    }
    case "forecast": return [line([a, b]), ...arrow(b, c), ...readoutFigure(runtime, c)];
    case "long_position":
    case "short_position": {
      const targetColor = runtime.settings?.targetColor ?? drawingColorToken("--tx-up");
      const stopColor = runtime.settings?.stopColor ?? drawingColorToken("--tx-down");
      const left = Math.min(a.x, b.x, c.x); const right = Math.max(a.x, b.x, c.x);
      return [
        { type: "rect", attrs: { x: left, y: Math.min(a.y, b.y), width: right - left, height: Math.abs(b.y - a.y) }, styles: { color: targetColor } },
        { type: "rect", attrs: { x: left, y: Math.min(a.y, c.y), width: right - left, height: Math.abs(c.y - a.y) }, styles: { color: stopColor } },
        line([{ x: left, y: a.y }, { x: right, y: a.y }]),
        ...(runtime.settings?.showStatistics!==false?readoutFigure(runtime, { x: right, y: a.y-(runtime.settings?.showPriceLabels?20:0) }):[]),
        ...(runtime.settings?.showPriceLabels?[["Entry",a],["Target",b],["Stop",c]].map(([name,point],index)=>label({x:right,y:(point as OverlayPixelPoint).y},`${name} ${compact(Number(runtime.readout?.values[["entry","target","stop"][index]]??0))}`)):[]),
      ];
    }
    default: return [];
  }
}
