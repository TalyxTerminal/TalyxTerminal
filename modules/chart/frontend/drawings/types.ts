/* Drawings as the chart document holds them (backend `document.py`): real UTC ms times and prices, never pixels or bar
   indexes. The geometry below works on its own point shape, `{timestamp, value}`, which the layer fills from these. */
import type { OverlayType } from "./registry";

export interface Point { time: number; price: number }

export interface DrawingStyle {
  color?: string | null; lineWidth?: number | null; lineStyle?: "solid" | "dashed" | "dotted" | null;
  fillColor?: string | null; fillOpacity?: number | null; fontSize?: number | null; textColor?: string | null;
  fontWeight?: 400 | 600 | null; fontStyle?: "normal" | "italic" | null;
}

export type TimeUnit = "seconds" | "minutes" | "hours" | "days" | "weeks" | "months";
export interface TimeframeBand { enabled: boolean; from: number; to: number }

export interface Drawing {
  id: string;
  tool: OverlayType;
  points: Point[];
  paneId: string;
  text?: string | null;
  style: DrawingStyle;
  settings: OverlaySettings;
  visibility: Partial<Record<TimeUnit, TimeframeBand>>;
  locked: boolean;
  visible: boolean;
  name?: string | null;
}

/** The geometry's point: a time in ms and a value on the pane's scale. */
export interface OverlayPoint { timestamp: number; value: number }

export interface OverlayLevelStyle {
  value: number; visible?: boolean; color?: string; text?: string; lineWidth?: number; lineStyle?: "solid" | "dashed" | "dotted";
}

/** Tool settings the geometry reads (the document stores them as given). */
export interface OverlaySettings {
  levels?: number[]; levelStyles?: OverlayLevelStyle[]; reverse?: boolean; extendLeft?: boolean; extendRight?: boolean;
  showTrendLine?: boolean; showBackground?: boolean; showPrices?: boolean; showLevels?: boolean; showLabels?: boolean;
  levelLabelFormat?: "ratio" | "percent"; labelPosition?: "left" | "center" | "right"; labelAlignment?: "top" | "bottom"; logLevels?: boolean;
  showPriceRange?: boolean; showPercentChange?: boolean; showBarsRange?: boolean; showDateTimeRange?: boolean; showAngle?: boolean;
  startEnd?: "normal" | "arrow"; endEnd?: "normal" | "arrow"; showMiddlePoint?: boolean; showPriceLabels?: boolean; showDistance?: boolean;
  statsPosition?: "left" | "center" | "right"; textPosition?: "left" | "center" | "right"; showAlwaysStats?: boolean;
  trendLineColor?: string; trendLineWidth?: number; trendLineStyle?: "solid" | "dashed" | "dotted";
  showMiddleLine?: boolean; middleLineColor?: string; middleLineWidth?: number; middleLineStyle?: "solid" | "dashed" | "dotted";
  textVerticalAlignment?: "top" | "middle" | "bottom"; accountSize?: number; lotSize?: number; risk?: number; riskMode?: "percent" | "amount";
  leverage?: number; qtyPrecision?: number; targetColor?: string; stopColor?: string; showStatistics?: boolean; compact?: boolean;
  source?: "open" | "high" | "low" | "close" | "hl2" | "hlc3" | "ohlc4"; upperDeviation?: number; lowerDeviation?: number;
  upperEnabled?: boolean; lowerEnabled?: boolean; showPearson?: boolean;
}

/** One bar as the geometry reads it (regression, anchored VWAP, measure). */
export interface GeometryBar { time: number; open: number; high: number; low: number; close: number; volume: number | null }
