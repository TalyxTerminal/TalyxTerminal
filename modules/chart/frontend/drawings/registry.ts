import { t } from "../i18n";
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
  textMode: OverlayTextSemantics;
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
  line({ id: "trendline", group: "lines", label: t("Trend Line"), points: exact(2), textMode: "optional", interaction: "step" }),
  line({ id: "info_line", group: "lines", label: t("Info Line"), points: exact(2), textMode: "optional", interaction: "step", readout: { kind: "range" } }),
  line({ id: "ray", group: "lines", label: t("Ray"), points: exact(2), textMode: "optional", interaction: "step" }),
  line({ id: "extended_line", group: "lines", label: t("Extended Line"), points: exact(2), textMode: "optional", interaction: "step" }),
  line({ id: "trend_angle", group: "lines", label: t("Trend Angle"), points: exact(2), textMode: "optional", interaction: "step" }),
  line({ id: "hline", group: "lines", label: t("Horizontal Line"), points: exact(1), textMode: "optional", interaction: "step" }),
  line({ id: "horizontal_ray", group: "lines", label: t("Horizontal Ray"), points: exact(1), textMode: "optional", interaction: "step" }),
  line({ id: "vline", group: "lines", label: t("Vertical Line"), points: exact(1), textMode: "optional", interaction: "step" }),
  line({ id: "cross_line", group: "lines", label: t("Cross Line"), points: exact(1), textMode: "optional", interaction: "step" }),
  line({ id: "arrow", group: "lines", label: t("Arrow"), points: exact(2), textMode: "optional", interaction: "step" }),
  line({ id: "path", group: "lines", label: t("Path"), points: stream(2), textMode: "none", interaction: "multi_click" }),

  channel({ id: "parallel_channel", group: "channels", label: t("Parallel Channel"), points: exact(3), textMode: "optional", interaction: "step", levels: levels([0, 1]) }),
  channel({ id: "price_channel", group: "channels", label: t("Price Channel"), points: exact(3), textMode: "none" }),
  channel({ id: "regression_trend", group: "channels", label: t("Regression Trend"), points: exact(2), textMode: "generated", readout: { kind: "regression" } }),
  channel({ id: "flat_top_bottom", group: "channels", label: t("Flat Top/Bottom"), points: exact(3), textMode: "none" }),
  channel({ id: "pitchfork", group: "channels", label: t("Pitchfork"), points: exact(3), textMode: "none" }),
  channel({ id: "schiff_pitchfork", group: "channels", label: t("Schiff Pitchfork"), points: exact(3), textMode: "none" }),

  levelProjection({ id: "fib", group: "fib_gann", label: t("Fib Retracement"), points: exact(2), textMode: "generated", readout: { kind: "fib" }, levels: levels([0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]) }),
  levelProjection({ id: "fib_extension", group: "fib_gann", label: t("Trend-Based Fib Extension"), points: exact(3), textMode: "generated", readout: { kind: "fib" }, levels: levels([0, 0.618, 1, 1.618, 2.618]) }),
  levelProjection({ id: "fib_channel", group: "fib_gann", label: t("Fib Channel"), points: exact(3), textMode: "generated", readout: { kind: "fib" }, levels: levels([0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]) }),
  levelProjection({ id: "fib_time", group: "fib_gann", label: t("Trend-Based Fib Time"), points: exact(2), textMode: "generated", readout: { kind: "fib" }, levels: levels([0, 1, 2, 3, 5, 8, 13]) }),
  levelProjection({ id: "gann_box", group: "fib_gann", label: t("Gann Box"), points: exact(2), textMode: "generated", readout: { kind: "gann" }, levels: levels([0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875]) }),
  levelProjection({ id: "gann_fan", group: "fib_gann", label: t("Gann Fan"), points: exact(2), textMode: "generated", readout: { kind: "gann" }, levels: levels([0.125, 0.25, 0.5, 1, 2, 4, 8]) }),

  geometry({ id: "brush", group: "geometry", label: t("Brush"), points: stream(2), textMode: "none", interaction: "continuous" }),
  geometry({ id: "highlighter", group: "geometry", label: t("Highlighter"), points: stream(2), textMode: "none", interaction: "continuous" }),
  geometry({ id: "rect", group: "geometry", label: t("Rectangle"), points: exact(2), textMode: "optional" }),
  geometry({ id: "rotated_rect", group: "geometry", label: t("Rotated Rectangle"), points: exact(3), textMode: "none" }),
  geometry({ id: "ellipse", group: "geometry", label: t("Ellipse"), points: exact(2), textMode: "none" }),
  geometry({ id: "triangle", group: "geometry", label: t("Triangle"), points: exact(3), textMode: "none" }),
  geometry({ id: "arc", group: "geometry", label: t("Arc"), points: exact(3), textMode: "none" }),
  geometry({ id: "polyline", group: "geometry", label: t("Polyline"), points: stream(2), textMode: "none", interaction: "multi_click" }),

  annotation({ id: "text", group: "annotation", label: t("Text"), points: exact(1), textMode: "required" }),
  annotation({ id: "anchored_text", group: "annotation", label: t("Anchored Text"), points: exact(2), textMode: "required" }),
  annotation({ id: "note", group: "annotation", label: t("Note"), points: exact(1), textMode: "required" }),
  annotation({ id: "callout", group: "annotation", label: t("Callout"), points: exact(2), textMode: "required" }),
  annotation({ id: "comment", group: "annotation", label: t("Comment"), points: exact(1), textMode: "required" }),
  annotation({ id: "price_label", group: "annotation", label: t("Price Label"), points: exact(1), textMode: "generated" }),
  annotation({ id: "arrow_marker", group: "annotation", label: t("Arrow Marker"), points: exact(1), textMode: "optional" }),

  pattern({ id: "xabcd", group: "patterns", label: t("XABCD Pattern"), points: exact(5), textMode: "generated" }),
  pattern({ id: "abcd", group: "patterns", label: t("ABCD Pattern"), points: exact(4), textMode: "generated" }),
  pattern({ id: "cypher", group: "patterns", label: t("Cypher Pattern"), points: exact(5), textMode: "generated" }),
  pattern({ id: "head_shoulders", group: "patterns", label: t("Head and Shoulders"), points: exact(7), textMode: "generated" }),
  pattern({ id: "elliott_impulse", group: "patterns", label: t("Elliott Impulse Wave"), points: exact(6), textMode: "generated" }),
  pattern({ id: "elliott_correction", group: "patterns", label: t("Elliott Correction Wave"), points: exact(4), textMode: "generated" }),
  pattern({ id: "elliott_triangle", group: "patterns", label: t("Elliott Triangle Wave"), points: exact(6), textMode: "generated" }),

  measurementPosition({ id: "measure", group: "measurement_position", label: t("Measure"), points: exact(2), textMode: "generated", readout: { kind: "range" } }),
  measurementPosition({ id: "date_range", group: "measurement_position", label: t("Date Range"), points: exact(2), textMode: "generated", readout: { kind: "range" } }),
  measurementPosition({ id: "price_range", group: "measurement_position", label: t("Price Range"), points: exact(2), textMode: "generated", readout: { kind: "range" } }),
  measurementPosition({ id: "date_price_range", group: "measurement_position", label: t("Date & Price Range"), points: exact(2), textMode: "generated", readout: { kind: "range" } }),
  measurementPosition({ id: "anchored_vwap", group: "measurement_position", label: t("Anchored VWAP"), points: exact(1), textMode: "generated", readout: { kind: "anchored_vwap" } }),
  measurementPosition({ id: "forecast", group: "measurement_position", label: t("Forecast"), points: exact(3), textMode: "generated", readout: { kind: "range" } }),
  measurementPosition({ id: "long_position", group: "measurement_position", label: t("Long Position"), points: exact(3), textMode: "generated", readout: { kind: "position" } }),
  measurementPosition({ id: "short_position", group: "measurement_position", label: t("Short Position"), points: exact(3), textMode: "generated", readout: { kind: "position" } }),
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
