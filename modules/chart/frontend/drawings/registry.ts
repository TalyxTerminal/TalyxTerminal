export const OVERLAY_GROUPS = [
  "lines",
  "channels",
  "fib_gann",
  "geometry",
  "annotation",
  "patterns",
  "measurement_position",
] as const;

export type OverlayGroup = (typeof OVERLAY_GROUPS)[number];
export type OverlayTextSemantics = "none" | "required" | "optional" | "generated";

export type OverlayPointSemantics =
  | { kind: "exact"; count: number }
  | { kind: "minimum"; count: number };

export type OverlayInteraction = "step" | "continuous" | "multi_click";

export interface OverlayReadoutContract {
  kind: "regression" | "fib" | "gann" | "range" | "anchored_vwap" | "position";
}

export interface OverlayLevelContract {
  defaults: readonly number[];
  minimum: number;
  maximum: number;
  lowerBound: number;
  upperBound: number;
}

export interface OverlayToolDefinition {
  id: string;
  group: OverlayGroup;
  label: string;
  points: OverlayPointSemantics;
  text: OverlayTextSemantics;
  /** Omitted means one click per point. */
  interaction?: OverlayInteraction;
  readout?: OverlayReadoutContract;
  levels?: OverlayLevelContract;
}

const exact = (count: number): OverlayPointSemantics => ({
  kind: "exact",
  count,
});
const stream = (minimum: number): OverlayPointSemantics => ({
  kind: "minimum",
  count: minimum,
});
const levels = (defaults: readonly number[]): OverlayLevelContract => ({
  defaults,
  minimum: 1,
  maximum: 24,
  lowerBound: -16,
  upperBound: 16,
});

const line = <const T extends OverlayToolDefinition>(definition: T) => definition;
const channel = line, levelProjection = line, geometry = line, annotation = line, pattern = line, measurementPosition = line;

// Product order is intentional: it is the toolbar/catalog order and the serialized
// vocabulary authority (the backend's TOOLS lists the same ids).
export const OVERLAY_TOOL_REGISTRY = [
  line({ id: "trendline", group: "lines", label: "Trend Line", points: exact(2), text: "optional", interaction: "step" }),
  line({ id: "info_line", group: "lines", label: "Info Line", points: exact(2), text: "optional", interaction: "step", readout: { kind: "range" } }),
  line({ id: "ray", group: "lines", label: "Ray", points: exact(2), text: "optional", interaction: "step" }),
  line({ id: "extended_line", group: "lines", label: "Extended Line", points: exact(2), text: "optional", interaction: "step" }),
  line({ id: "trend_angle", group: "lines", label: "Trend Angle", points: exact(2), text: "optional", interaction: "step" }),
  line({ id: "hline", group: "lines", label: "Horizontal Line", points: exact(1), text: "optional", interaction: "step" }),
  line({ id: "horizontal_ray", group: "lines", label: "Horizontal Ray", points: exact(1), text: "optional", interaction: "step" }),
  line({ id: "vline", group: "lines", label: "Vertical Line", points: exact(1), text: "optional", interaction: "step" }),
  line({ id: "cross_line", group: "lines", label: "Cross Line", points: exact(1), text: "optional", interaction: "step" }),
  line({ id: "arrow", group: "lines", label: "Arrow", points: exact(2), text: "optional", interaction: "step" }),
  line({ id: "path", group: "lines", label: "Path", points: stream(2), text: "none", interaction: "multi_click" }),

  channel({ id: "parallel_channel", group: "channels", label: "Parallel Channel", points: exact(3), text: "optional", interaction: "step", levels: levels([0, 1]) }),
  channel({ id: "price_channel", group: "channels", label: "Price Channel", points: exact(3), text: "none" }),
  channel({ id: "regression_trend", group: "channels", label: "Regression Trend", points: exact(2), text: "generated", readout: { kind: "regression" } }),
  channel({ id: "flat_top_bottom", group: "channels", label: "Flat Top/Bottom", points: exact(3), text: "none" }),
  channel({ id: "pitchfork", group: "channels", label: "Pitchfork", points: exact(3), text: "none" }),
  channel({ id: "schiff_pitchfork", group: "channels", label: "Schiff Pitchfork", points: exact(3), text: "none" }),

  levelProjection({ id: "fib", group: "fib_gann", label: "Fib Retracement", points: exact(2), text: "generated", readout: { kind: "fib" }, levels: levels([0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]) }),
  levelProjection({ id: "fib_extension", group: "fib_gann", label: "Trend-Based Fib Extension", points: exact(3), text: "generated", readout: { kind: "fib" }, levels: levels([0, 0.618, 1, 1.618, 2.618]) }),
  levelProjection({ id: "fib_channel", group: "fib_gann", label: "Fib Channel", points: exact(3), text: "generated", readout: { kind: "fib" }, levels: levels([0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]) }),
  levelProjection({ id: "fib_time", group: "fib_gann", label: "Trend-Based Fib Time", points: exact(2), text: "generated", readout: { kind: "fib" }, levels: levels([0, 1, 2, 3, 5, 8, 13]) }),
  levelProjection({ id: "gann_box", group: "fib_gann", label: "Gann Box", points: exact(2), text: "generated", readout: { kind: "gann" }, levels: levels([0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875]) }),
  levelProjection({ id: "gann_fan", group: "fib_gann", label: "Gann Fan", points: exact(2), text: "generated", readout: { kind: "gann" }, levels: levels([0.125, 0.25, 0.5, 1, 2, 4, 8]) }),

  geometry({ id: "brush", group: "geometry", label: "Brush", points: stream(2), text: "none", interaction: "continuous" }),
  geometry({ id: "highlighter", group: "geometry", label: "Highlighter", points: stream(2), text: "none", interaction: "continuous" }),
  geometry({ id: "rect", group: "geometry", label: "Rectangle", points: exact(2), text: "optional" }),
  geometry({ id: "rotated_rect", group: "geometry", label: "Rotated Rectangle", points: exact(3), text: "none" }),
  geometry({ id: "ellipse", group: "geometry", label: "Ellipse", points: exact(2), text: "none" }),
  geometry({ id: "triangle", group: "geometry", label: "Triangle", points: exact(3), text: "none" }),
  geometry({ id: "arc", group: "geometry", label: "Arc", points: exact(3), text: "none" }),
  geometry({ id: "polyline", group: "geometry", label: "Polyline", points: stream(2), text: "none", interaction: "multi_click" }),

  annotation({ id: "text", group: "annotation", label: "Text", points: exact(1), text: "required" }),
  annotation({ id: "anchored_text", group: "annotation", label: "Anchored Text", points: exact(2), text: "required" }),
  annotation({ id: "note", group: "annotation", label: "Note", points: exact(1), text: "required" }),
  annotation({ id: "callout", group: "annotation", label: "Callout", points: exact(2), text: "required" }),
  annotation({ id: "comment", group: "annotation", label: "Comment", points: exact(1), text: "required" }),
  annotation({ id: "price_label", group: "annotation", label: "Price Label", points: exact(1), text: "generated" }),
  annotation({ id: "arrow_marker", group: "annotation", label: "Arrow Marker", points: exact(1), text: "optional" }),

  pattern({ id: "xabcd", group: "patterns", label: "XABCD Pattern", points: exact(5), text: "generated" }),
  pattern({ id: "abcd", group: "patterns", label: "ABCD Pattern", points: exact(4), text: "generated" }),
  pattern({ id: "cypher", group: "patterns", label: "Cypher Pattern", points: exact(5), text: "generated" }),
  pattern({ id: "head_shoulders", group: "patterns", label: "Head and Shoulders", points: exact(7), text: "generated" }),
  pattern({ id: "elliott_impulse", group: "patterns", label: "Elliott Impulse Wave", points: exact(6), text: "generated" }),
  pattern({ id: "elliott_correction", group: "patterns", label: "Elliott Correction Wave", points: exact(4), text: "generated" }),
  pattern({ id: "elliott_triangle", group: "patterns", label: "Elliott Triangle Wave", points: exact(6), text: "generated" }),

  measurementPosition({ id: "measure", group: "measurement_position", label: "Measure", points: exact(2), text: "generated", readout: { kind: "range" } }),
  measurementPosition({ id: "date_range", group: "measurement_position", label: "Date Range", points: exact(2), text: "generated", readout: { kind: "range" } }),
  measurementPosition({ id: "price_range", group: "measurement_position", label: "Price Range", points: exact(2), text: "generated", readout: { kind: "range" } }),
  measurementPosition({ id: "date_price_range", group: "measurement_position", label: "Date & Price Range", points: exact(2), text: "generated", readout: { kind: "range" } }),
  measurementPosition({ id: "anchored_vwap", group: "measurement_position", label: "Anchored VWAP", points: exact(1), text: "generated", readout: { kind: "anchored_vwap" } }),
  measurementPosition({ id: "forecast", group: "measurement_position", label: "Forecast", points: exact(3), text: "generated", readout: { kind: "range" } }),
  measurementPosition({ id: "long_position", group: "measurement_position", label: "Long Position", points: exact(3), text: "generated", readout: { kind: "position" } }),
  measurementPosition({ id: "short_position", group: "measurement_position", label: "Short Position", points: exact(3), text: "generated", readout: { kind: "position" } }),
] as const satisfies readonly OverlayToolDefinition[];

export type OverlayType = (typeof OVERLAY_TOOL_REGISTRY)[number]["id"];
type OverlayTool = (typeof OVERLAY_TOOL_REGISTRY)[number];

const TOOL_BY_ID = Object.fromEntries(
  OVERLAY_TOOL_REGISTRY.map((tool) => [tool.id, tool]),
) as Record<OverlayType, OverlayTool>;

export function overlayTool(type: OverlayType): OverlayToolDefinition {
  return TOOL_BY_ID[type];
}

export function overlayInteraction(type: OverlayType): OverlayInteraction {
  return overlayTool(type).interaction ?? "step";
}

function validOverlayLevels(type: OverlayType, value: unknown): value is number[] {
  const contract = overlayTool(type).levels;
  if (!contract || !Array.isArray(value)) return false;
  if (value.length < contract.minimum || value.length > contract.maximum) return false;
  if (!value.every((level) =>
    typeof level === "number" && Number.isFinite(level) &&
    level >= contract.lowerBound && level <= contract.upperBound
  )) return false;
  return new Set(value).size === value.length;
}

export function overlayLevels(type: OverlayType, configured?: readonly number[]): readonly number[] {
  const contract = overlayTool(type).levels;
  if (!contract) {
    if (configured !== undefined) {
      throw new Error(`Overlay '${type}' does not support configurable levels`);
    }
    return [];
  }
  if (configured === undefined) return contract.defaults;
  if (!validOverlayLevels(type, configured)) {
    throw new Error(`Overlay '${type}' has invalid registry-bounded levels`);
  }
  return configured;
}
