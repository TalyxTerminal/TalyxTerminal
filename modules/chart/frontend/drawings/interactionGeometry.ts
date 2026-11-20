import RBush from "rbush";

import type { OverlayGeometryFigure, OverlayPixelPoint } from "./geometry";
import { overlayInteraction, overlayTool, type OverlayType } from "./registry";

interface FigureIndexItem {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
  figureIndex: number;
}

export interface OverlayGeometryHit {
  figureIndex: number;
  figureKey: string | null;
  distance: number;
}

export interface OverlayControlHandle {
  id: string;
  kind: "anchor" | "derived";
  role: string;
  point: OverlayPixelPoint;
}

export interface OverlayInteractionSurface {
  type: OverlayType;
  figures: readonly OverlayGeometryFigure[];
  handles: readonly OverlayControlHandle[];
  tolerance: number;
}

export type OverlayInteractionHit =
  | { kind: "handle"; handle: OverlayControlHandle; distance: number }
  | { kind: "geometry"; geometry: OverlayGeometryHit };

function pixelPoint(value: unknown): OverlayPixelPoint | null {
  if (!value || typeof value !== "object") return null;
  const point = value as { x?: unknown; y?: unknown };
  return typeof point.x === "number" && Number.isFinite(point.x) &&
      typeof point.y === "number" && Number.isFinite(point.y)
    ? { x: point.x, y: point.y }
    : null;
}

function figureCoordinates(figure: OverlayGeometryFigure): OverlayPixelPoint[] {
  const coordinates = figure.attrs.coordinates;
  return Array.isArray(coordinates)
    ? coordinates.flatMap((value) => {
        const point = pixelPoint(value);
        return point ? [point] : [];
      })
    : [];
}

function figureBounds(figure: OverlayGeometryFigure): Omit<FigureIndexItem, "figureIndex"> | null {
  const coordinates = figureCoordinates(figure);
  if (coordinates.length > 0) {
    return {
      minX: Math.min(...coordinates.map((point) => point.x)),
      minY: Math.min(...coordinates.map((point) => point.y)),
      maxX: Math.max(...coordinates.map((point) => point.x)),
      maxY: Math.max(...coordinates.map((point) => point.y)),
    };
  }
  const x = figure.attrs.x;
  const y = figure.attrs.y;
  if (typeof x !== "number" || typeof y !== "number") return null;
  if (figure.type === "circle") {
    const radius = typeof figure.attrs.r === "number" ? Math.abs(figure.attrs.r) : 0;
    return { minX: x - radius, minY: y - radius, maxX: x + radius, maxY: y + radius };
  }
  if (figure.type === "rect") {
    const width = typeof figure.attrs.width === "number" ? figure.attrs.width : 0;
    const height = typeof figure.attrs.height === "number" ? figure.attrs.height : 0;
    return {
      minX: Math.min(x, x + width),
      minY: Math.min(y, y + height),
      maxX: Math.max(x, x + width),
      maxY: Math.max(y, y + height),
    };
  }
  if (figure.type === "text") {
    const text = typeof figure.attrs.text === "string" ? figure.attrs.text : "";
    return { minX: x, minY: y - 14, maxX: x + Math.max(7, text.length * 7), maxY: y + 3 };
  }
  return { minX: x, minY: y, maxX: x, maxY: y };
}

function pointSegmentDistance(
  point: OverlayPixelPoint,
  start: OverlayPixelPoint,
  end: OverlayPixelPoint,
): number {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const denominator = dx * dx + dy * dy;
  const projection = denominator === 0
    ? 0
    : Math.max(0, Math.min(1, ((point.x - start.x) * dx + (point.y - start.y) * dy) / denominator));
  return Math.hypot(
    point.x - (start.x + projection * dx),
    point.y - (start.y + projection * dy),
  );
}

function pointInPolygon(point: OverlayPixelPoint, coordinates: readonly OverlayPixelPoint[]): boolean {
  let inside = false;
  for (let index = 0, previous = coordinates.length - 1; index < coordinates.length; previous = index++) {
    const currentPoint = coordinates[index];
    const previousPoint = coordinates[previous];
    const intersects = currentPoint.y > point.y !== previousPoint.y > point.y &&
      point.x < (previousPoint.x - currentPoint.x) * (point.y - currentPoint.y) /
        (previousPoint.y - currentPoint.y || Number.EPSILON) + currentPoint.x;
    if (intersects) inside = !inside;
  }
  return inside;
}

function figureDistance(figure: OverlayGeometryFigure, point: OverlayPixelPoint): number {
  const coordinates = figureCoordinates(figure);
  if (figure.type === "line") {
    if (coordinates.length < 2) return Number.POSITIVE_INFINITY;
    return Math.min(...coordinates.slice(0, -1).map((start, index) =>
      pointSegmentDistance(point, start, coordinates[index + 1])
    ));
  }
  if (figure.type === "polygon") {
    if (coordinates.length < 3) return Number.POSITIVE_INFINITY;
    if (pointInPolygon(point, coordinates)) return 0;
    return Math.min(...coordinates.map((start, index) =>
      pointSegmentDistance(point, start, coordinates[(index + 1) % coordinates.length])
    ));
  }
  const bounds = figureBounds(figure);
  if (!bounds) return Number.POSITIVE_INFINITY;
  if (figure.type === "circle") {
    const center = { x: Number(figure.attrs.x), y: Number(figure.attrs.y) };
    const radius = typeof figure.attrs.r === "number" ? Math.abs(figure.attrs.r) : 0;
    return Math.max(0, Math.hypot(point.x - center.x, point.y - center.y) - radius);
  }
  const dx = Math.max(bounds.minX - point.x, 0, point.x - bounds.maxX);
  const dy = Math.max(bounds.minY - point.y, 0, point.y - bounds.maxY);
  return Math.hypot(dx, dy);
}

/**
 * RBush narrows candidate figures; the final result always comes from exact primitive
 * distance/containment, so a large bounding box cannot steal a nearby line hit.
 */
export function hitTestOverlayGeometry(
  figures: readonly OverlayGeometryFigure[],
  pointer: OverlayPixelPoint,
  tolerance = 6,
): OverlayGeometryHit | null {
  if (![pointer.x, pointer.y, tolerance].every(Number.isFinite) || tolerance < 0) return null;
  const items = figures.flatMap((figure, figureIndex) => {
    const bounds = figureBounds(figure);
    return bounds ? [{ ...bounds, figureIndex }] : [];
  });
  if (items.length === 0) return null;
  const tree = new RBush<FigureIndexItem>();
  tree.load(items);
  const candidates = tree.search({
    minX: pointer.x - tolerance,
    minY: pointer.y - tolerance,
    maxX: pointer.x + tolerance,
    maxY: pointer.y + tolerance,
  });
  let best: OverlayGeometryHit | null = null;
  for (const candidate of candidates) {
    const figure = figures[candidate.figureIndex];
    const distance = figureDistance(figure, pointer);
    if (distance > tolerance || (best && distance >= best.distance)) continue;
    best = {
      figureIndex: candidate.figureIndex,
      figureKey: figure.key ?? null,
      distance,
    };
  }
  return best;
}

const HANDLE_ROLES: Partial<Record<OverlayType, readonly string[]>> = {
  trendline: ["line start", "line end"],
  info_line: ["line start", "line end"],
  ray: ["ray origin", "ray direction"],
  extended_line: ["line point 1", "line point 2"],
  trend_angle: ["angle origin", "angle direction"],
  hline: ["price"],
  horizontal_ray: ["ray origin"],
  vline: ["time"],
  cross_line: ["time and value"],
  arrow: ["arrow tail", "arrow head"],
  path: ["path start", "path vertex"],
  parallel_channel: ["baseline start", "baseline end", "channel width"],
  price_channel: ["midline start", "midline end", "channel width"],
  regression_trend: ["regression range start", "regression range end"],
  flat_top_bottom: ["baseline start", "baseline end", "channel boundary"],
  pitchfork: ["median origin", "upper pivot", "lower pivot"],
  schiff_pitchfork: ["shifted origin", "upper pivot", "lower pivot"],
  fib: ["range start", "range end"],
  fib_extension: ["impulse start", "impulse end", "projection anchor"],
  fib_channel: ["baseline start", "baseline end", "level offset"],
  fib_time: ["time origin", "time interval"],
  gann_box: ["range start", "range end"],
  gann_fan: ["fan origin", "time and value scale"],
  rect: ["corner start", "corner end"],
  rotated_rect: ["baseline start", "baseline end", "width"],
  ellipse: ["bounds start", "bounds end"],
  triangle: ["vertex A", "vertex B", "vertex C"],
  arc: ["arc start", "control point", "arc end"],
  polyline: ["line start", "line vertex"],
  text: ["text anchor"],
  anchored_text: ["connector anchor", "text anchor"],
  note: ["note anchor"],
  callout: ["callout target", "text anchor"],
  comment: ["comment anchor"],
  price_label: ["price label"],
  arrow_marker: ["arrow marker"],
  long_position: ["entry", "target", "stop"],
  short_position: ["entry", "target", "stop"],
  measure: ["measure start", "measure end"],
  date_range: ["time range start", "time range end"],
  price_range: ["value range start", "value range end"],
  date_price_range: ["range start", "range end"],
  anchored_vwap: ["VWAP start time"],
  forecast: ["forecast origin", "turning point", "forecast target"],
  xabcd: ["X", "A", "B", "C", "D"],
  abcd: ["A", "B", "C", "D"],
  cypher: ["X", "A", "B", "C", "D"],
  head_shoulders: ["left boundary", "left shoulder", "neckline left", "head", "neckline right", "right shoulder", "right boundary"],
  elliott_impulse: ["0", "1", "2", "3", "4", "5"],
  elliott_correction: ["0", "A", "B", "C"],
  elliott_triangle: ["0", "A", "B", "C", "D", "E"],
};

export function overlayControlHandles(
  type: OverlayType,
  coordinates: readonly OverlayPixelPoint[],
): OverlayControlHandle[] {
  const continuous = overlayInteraction(type) === "continuous";
  const anchors = continuous && coordinates.length > 2
    ? [coordinates[0], coordinates[coordinates.length - 1]]
    : [...coordinates];
  const roles = HANDLE_ROLES[type] ?? [];
  const handles: OverlayControlHandle[] = anchors.map((point, index) => ({
    id: `anchor:${continuous && index === 1 ? coordinates.length - 1 : index}`,
    kind: "anchor" as const,
    role: continuous ? (index === 0 ? "stroke start" : "stroke end") : roles[index] ?? (overlayTool(type).points.kind === "minimum" ? `vertex ${index + 1}` : `point ${index + 1}`),
    point: { ...point },
  }));
  if (["rect", "ellipse", "date_price_range"].includes(type) && coordinates.length >= 2) {
    handles.push({
      id: "derived:center",
      kind: "derived",
      role: "move",
      point: {
        x: (coordinates[0].x + coordinates[1].x) / 2,
        y: (coordinates[0].y + coordinates[1].y) / 2,
      },
    });
  }
  return handles;
}

export function hitTestOverlayInteractionSurface(
  surface: OverlayInteractionSurface,
  pointer: OverlayPixelPoint,
): OverlayInteractionHit | null {
  if (![pointer.x, pointer.y].every(Number.isFinite)) return null;
  let nearestHandle: OverlayInteractionHit | null = null;
  for (const handle of surface.handles) {
    const distance = Math.hypot(pointer.x - handle.point.x, pointer.y - handle.point.y);
    if (distance > surface.tolerance ||
        (nearestHandle?.kind === "handle" && distance >= nearestHandle.distance)) continue;
    nearestHandle = { kind: "handle", handle, distance };
  }
  if (nearestHandle) return nearestHandle;
  const geometry = hitTestOverlayGeometry(surface.figures, pointer, surface.tolerance);
  return geometry ? { kind: "geometry", geometry } : null;
}

/** Constraints are applied in the target pane's pixel space, then converted by
 * its real axes. Numeric/time anchors themselves are never guessed here. */
export function constrainDrawingPointer(type:OverlayType,anchor:OverlayPixelPoint|undefined,pointer:OverlayPixelPoint,shiftKey:boolean):OverlayPixelPoint {
  if(!shiftKey||!anchor)return {...pointer};
  const dx=pointer.x-anchor.x,dy=pointer.y-anchor.y;
  if(type==="rect"||type==="ellipse") {const side=Math.max(Math.abs(dx),Math.abs(dy));return {x:anchor.x+Math.sign(dx||1)*side,y:anchor.y+Math.sign(dy||1)*side};}
  if(["trendline","info_line","ray","extended_line","trend_angle","arrow"].includes(type)){const step=Math.PI/4,angle=Math.round(Math.atan2(dy,dx)/step)*step,radius=Math.hypot(dx,dy);return {x:anchor.x+Math.cos(angle)*radius,y:anchor.y+Math.sin(angle)*radius};}
  return {...pointer};
}

/** TradingView's one-click Long and Short Position, in pane pixels so the defaults follow the chart in view: entry where
    clicked, target and stop a tenth of the pane's height away (the target above for a long, below for a short), the box
    20 bars wide. Gives the target and the stop. */
export function positionPixels(type:"long_position"|"short_position",entry:OverlayPixelPoint,paneHeight:number,barSpacing:number):[OverlayPixelPoint,OverlayPixelPoint] {
  const away=paneHeight/10*(type==="long_position"?-1:1),x=entry.x+20*barSpacing;
  return [{x,y:entry.y+away},{x,y:entry.y-away}];
}
