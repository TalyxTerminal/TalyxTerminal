/* Chart's private seam as the frontend reads it: the backend's `document.py` and `wire.py` are the source; times are UTC ms. */
import type { Instrument, SeriesId } from "@talyx/vocab";
import type { Drawing, DrawingStyle, Point, TimeframeBand, TimeUnit } from "./drawings/types";
import type { ChartType } from "./engine/mainSeries";
import type { PriceFormat } from "./engine/precision";
import type { PineMeta } from "./engine/indicatorView";
import type { PineDrawingChanges, PineDrawingSet } from "./engine/pinePrimitives";
import type { FileRef, ResearchLayer } from "../public/ts/types";
import type { Bars } from "./engine/bars";

export const PRICE_PANE = "price";
export const BRICK_TYPES: readonly ChartType[] = ["renko", "kagi", "point_figure", "line_break", "range"];

// ── document ─────────────────────────────────────────────────────────────

export interface Pane { id: string; height: number; collapsed: boolean }
export interface IndicatorRef { source: "builtin" | "user"; id: string }
export interface PlotStyle { visible?: boolean | null; color?: string | null; lineWidth?: number | null; lineStyle?: "solid" | "dashed" | "dotted" | null }
export type Scalar = boolean | number | string;
export interface Indicator { id: string; definition: IndicatorRef; params: Record<string, Scalar>; paneId: string; visible: boolean; name?: string | null; styles: Record<string, PlotStyle> }
export interface Comparison { id: string; instrument: Instrument; series: SeriesId; scale: "same_percent" | "new_left" | "new_right"; color?: string | null; visible: boolean }
export interface Projection { source: "close" | "ohlc"; boxSize?: number | null; reversalBoxes: number; lineCount: number; rangeTicks?: number | null }

export interface Appearance {
  up?: string; down?: string; borderUp?: string; borderDown?: string; wickUp?: string; wickDown?: string;
  background?: string; vertGrid?: boolean; horzGrid?: boolean; watermark?: boolean;
}

/** The frontend's presentation of one chart; the backend stores it and never reads it. */
export interface Display {
  timeZone?: string;
  scale?: { mode?: "regular" | "log" | "percent" | "indexed"; invert?: boolean; side?: "left" | "right" };
  /** Chart settings › Symbol and Canvas: colours that replace the theme's, and what the canvas shows. */
  appearance?: Appearance;
  /** Chart settings › Symbol: decimals shown, instead of Market's price increment. */
  precision?: number | null;
  /** Chart settings › Scales and lines (all shown when left out). */
  scales?: { lastPriceLabel?: boolean; lastPriceLine?: boolean; countdown?: boolean; indicatorLabels?: boolean };
  /** Chart settings › Alerts: how alert lines are drawn (the accent colour, 1px, dashed when left out). */
  alertLine?: { color?: string; width?: number; style?: "solid" | "dashed" | "dotted" };
  /** Chart settings › Status line: what the legend shows (all shown when left out). */
  statusLine?: { title?: boolean; ohlc?: boolean; change?: boolean; volume?: boolean; indicatorArguments?: boolean; indicatorValues?: boolean };
  [key: string]: unknown;
}

export interface ChartDocument {
  id: string;
  instrument: Instrument;
  series: SeriesId;
  chartType: ChartType;
  projection: Projection;
  panes: Pane[];
  indicators: Indicator[];
  drawings: Record<string, Drawing[]>;
  comparisons: Comparison[];
  display: Display;
  layers: SavedLayer[];
}
export type SavedLayer = Omit<ResearchLayer, "markers" | "series"> & { visible: boolean; version: string; symbol: string };

export interface ChartState { chartId: string; revision: number; document: ChartDocument; drawingGroup: string; canUndo: boolean; canRedo: boolean }

export type Command =
  | { type: "layer.visible"; id: string; visible: boolean }
  | { type: "layer.delete"; id: string }
  | { type: "drawing.add"; value: Drawing }
  | { type: "drawing.update"; id: string; patch: Partial<Pick<Drawing, "points" | "paneId" | "text" | "style" | "settings" | "visibility" | "locked" | "visible" | "name">> }
  | { type: "drawing.delete"; id: string }
  | { type: "drawing.bulk"; ids?: string[] | null; locked?: boolean; visible?: boolean; remove?: true }
  | { type: "drawing.duplicate"; ids: string[] }
  | { type: "drawing.reorder"; id: string; direction: -1 | 1 }
  | { type: "drawings.replace"; drawings: Drawing[] }
  | { type: "indicator.add"; value: Indicator }
  | { type: "indicator.update"; id: string; patch: Partial<Pick<Indicator, "params" | "paneId" | "visible" | "name" | "styles">> }
  | { type: "indicator.delete"; id: string }
  | { type: "indicator.duplicate"; id: string }
  | { type: "indicator.reorder"; id: string; direction: -1 | 1 }
  | { type: "indicators.replace"; indicators: Indicator[]; panes: Pane[] }
  | { type: "pane.update"; id: string; height?: number; collapsed?: boolean }
  | { type: "pane.move"; id: string; direction: -1 | 1 }
  | { type: "comparison.add"; id: string; instrumentId: string; scale?: Comparison["scale"]; color?: string | null }
  | { type: "comparison.update"; id: string; scale?: Comparison["scale"]; color?: string | null; visible?: boolean }
  | { type: "comparison.delete"; id: string }
  | { type: "display.update"; patch: Record<string, unknown> }
  | { type: "display.replace"; display: Display }
  | { type: "selection.set"; instrumentId?: string; timeframe?: string; priceBasis?: "last" | "mark" | "index"; adjustment?: "raw" | "split" | "split_dividend"; adjustmentAnchor?: "latest" | "earliest" }
  | { type: "chart_type.set"; chartType: ChartType; projection?: Projection };

// ── objects ──────────────────────────────────────────────────────────────

export interface IndicatorInput { id: string; title: string; type: string; default: unknown; min?: number | null; max?: number | null; step?: number | null; options?: unknown[] | null; group?: string | null; tooltip?: string | null }
export interface CatalogEntry { source: "builtin" | "user"; id: string; title: string; shortTitle: string; description: string; category: string; overlay: boolean; digest: string; inputs: IndicatorInput[]; alertConditions: string[]; visibleRange: boolean; error?: string | null; line?: number | null }

export type Operand =
  | { kind: "price"; field: "close" | "open" | "high" | "low" | "hl2" | "hlc3" | "ohlc4" | "volume" }
  | { kind: "indicator"; definition: IndicatorRef; params: Record<string, Scalar>; output: string }
  | { kind: "drawing"; chartId: string; drawingId: string }
  | { kind: "value"; value: number }
  | { kind: "alertcondition"; definition: IndicatorRef; params: Record<string, Scalar>; condition: string };
export type ConditionOp = "crossing" | "crossing_up" | "crossing_down" | "greater" | "less" | "entering_channel" | "exiting_channel" | "inside_channel" | "outside_channel" | "moving_up" | "moving_down" | "moving_up_percent" | "moving_down_percent" | "triggered";
export interface Condition { left: Operand; op: ConditionOp; right?: Operand | null; right2?: Operand | null; amount?: number | null; bars?: number | null }
export type Frequency = "once" | "once_per_bar" | "once_per_bar_close" | "once_per_minute";
export interface AlertSpec {
  target: { kind: "series"; instrument: Instrument; series: SeriesId; chartId?: string | null } | { kind: "watchlist"; watchlistId: string; timeframe: string };
  conditions: Condition[]; frequency: Frequency; expiresAt?: number | null; message: string; name?: string | null;
  channels: { toast: boolean; sound: boolean; mobile: boolean };
}
export interface Reason { code: string; message: string }
/** What a new alert's notification starts with; global (Settings › Notifications). Delivery is the platform's. */
export interface AlertDefaults { toast: boolean; sound: boolean }
export interface Alert extends AlertSpec {
  id: string; revision: number; state: "active" | "paused" | "triggered" | "expired"; reason?: Reason | null; source?: Reason | null;
  outdated: boolean; createdAt: number; lastFiredAt?: number | null; fireCount: number;
}
export interface Fired { id: string; alertId: string; at: number; barTime: number; instrumentId: string; symbol: string; message: string; value?: number | null; delivered: Record<string, "sent" | "failed"> }
export interface Section { id: string; name: string; members: { instrument: Instrument }[] }
export interface Watchlist { id: string; name: string; revision: number; sections: Section[] }
export interface ScreenRow { instrument: Instrument; matched: boolean; values: Record<string, number | null>; barTime: number | null; error?: string | null }
export interface ScreenRun { runId: string; state: "running" | "completed" | "cancelled" | "failed"; startedAt: number; finishedAt: number | null; done: number; total: number; rows: ScreenRow[]; error?: string | null }
export interface ScreenSpec { name: string; scope: { kind: "instruments"; instruments: Instrument[] } | { kind: "watchlist"; watchlistId: string }; timeframe: string; conditions: Condition[] }
export interface Screen extends ScreenSpec { id: string; revision: number; lastRun?: ScreenRun | null }
export interface Template { id: string; kind: "indicators" | "chart" | "drawing"; name: string; content: Record<string, unknown> }
/** The indicator processes and Chart's database; anything but ok is shown in the status bar. */
export interface Health {
  pine: { state: "ok" | "starting" | "restarting" | "down"; reason: string | null };
  alertStore: { state: "ok" | "failing"; reason: string | null };
}

// ── routes: path → [body, response] ──────────────────────────────────────

type Empty = Record<string, never>;
export interface Routes {
  "/layers/stored": [{ chartId: string; id: string }, ResearchLayer];
  "/layers/file": [FileRef, ResearchLayer];
  "/layers/history": [Pick<ResearchLayer, "instrumentId" | "adjustment" | "timeframe" | "window">, { bars: Bars; zone: string; priceFormat: PriceFormat }];
  "/charts/open": [{ chartId: string }, ChartState];
  /** Which chart of this workspace stands for each chart a panel names (a copied workspace's panels name the original's). */
  "/charts/adopt": [{ chartIds: string[] }, { charts: Record<string, string> }];
  "/charts/create": [{ instrumentId?: string; timeframe?: string; importId?: string; copyOf?: string }, ChartState];
  "/charts/commit": [{ chartId: string; baseRevision: number; commands: Command[] }, ChartState];
  "/charts/undo": [{ chartId: string }, ChartState];
  "/charts/redo": [{ chartId: string }, ChartState];
  "/charts/save": [{ charts: { chartId: string; revision: number }[] }, Empty];
  "/charts/backfill": [{ chartId: string; to?: number; bars?: number }, { loaded: number; reachedStart: boolean; limitReached: boolean }];
  /** The visible range for indicators that read it: first and last visible bar times, or the newest `bars` while the right edge is on the newest bar. */
  "/charts/view": [{ chartId: string; left: number; right: number } | { chartId: string; bars: number }, Empty];
  "/charts/export": [{ chartId: string; format: "csv" | "json" | "parquet" }, { filename: string; mediaType: string; data: string; rows: number }];
  "/replay/start": [{ chartId: string; at: number }, Empty];
  "/replay/step": [{ chartId: string; bars?: number }, Empty];
  "/replay/stop": [{ chartId: string }, Empty];
  /** `folder`: the absolute path of the user's indicators folder. */
  "/indicators/catalog": [Empty, { folder: string; entries: CatalogEntry[] }];
  "/alerts/create": [{ spec: AlertSpec }, Alert];
  "/alerts/update": [{ alertId: string; revision: number; spec: AlertSpec }, Alert];
  "/alerts/state": [{ alertId: string; revision: number; state: "active" | "paused" }, Alert];
  "/alerts/delete": [{ alertId: string }, Empty];
  "/alerts/log": [{ alertId?: string; before?: number; limit?: number }, { events: Fired[]; next: number | null }];
  "/watchlists/create": [{ name: string; sections?: Section[] }, Watchlist];
  "/watchlists/update": [{ watchlistId: string; revision: number; name?: string; sections?: Section[] }, Watchlist];
  "/watchlists/delete": [{ watchlistId: string }, Empty];
  "/screens/save": [{ screenId?: string; revision?: number; spec: ScreenSpec }, Screen];
  "/screens/run": [{ screenId: string }, Screen];
  "/screens/cancel": [{ screenId: string }, Screen];
  "/screens/delete": [{ screenId: string }, Empty];
  "/templates/save": [{ templateId?: string; kind: Template["kind"]; name: string; content: Record<string, unknown> }, Template];
  "/templates/delete": [{ templateId: string }, Empty];
}

// ── chart stream (`chart.chart`) ─────────────────────────────────────────

export interface BarColumnsItem { time: number[]; closeTime: number[]; open: number[]; high: number[]; low: number[]; close: number[]; volume: (number | null)[]; sourceTime?: number[] }
export type MarketState = "connecting" | "live" | "delayed" | "history_only" | "market_closed" | "recovering" | "failed";
export interface MarketProblem { code: string; message: string; sourceId?: string | null; status?: number | null; retryAt?: string | null }

export type ChartItem =
  | { type: "document"; revision: number; document: ChartDocument; drawingGroup: string; canUndo: boolean; canRedo: boolean }
  | ({ type: "bars"; op: "replace" | "append" | "splice"; replay: boolean; start?: number; priceFormat?: PriceFormat; hasMoreBefore?: boolean; limitReached?: boolean; windowRows?: number; boxSize?: number | null } & BarColumnsItem)
  | { type: "price_format"; priceFormat: PriceFormat }
  | { type: "update"; replay: boolean; bar: { time: number; closeTime: number; open: number; high: number; low: number; close: number; volume: number | null } }
  | { type: "status"; state: MarketState; problem: MarketProblem | null; since: number }
  | { type: "indicator"; instanceId: string; op: "replace" | "append" | "update" | "splice"; replay: boolean; start?: number; meta?: PineMeta[]; time: number[]; values: Record<string, (number | null)[]>; colors: Record<string, unknown[]>; drawings?: PineDrawingSet & { t?: string }; drawingChanges?: PineDrawingChanges; behindChart?: boolean; noScale?: boolean }
  | { type: "indicator_error"; instanceId: string; code: string; message: string; line?: number }
  | { type: "comparison"; comparisonId: string; op: "replace" | "append" | "update"; time: number[]; value: (number | null)[]; priceFormat?: PriceFormat }
  | { type: "replay"; active: boolean; at: number | null; end: boolean };

// ── workspace stream (`chart.workspace`) ─────────────────────────────────

export type WorkspaceItem =
  | { type: "snapshot"; alerts: Alert[]; watchlists: Watchlist[]; screens: Screen[]; templates: Template[]; alertDefaults: AlertDefaults; health: Health }
  | ({ type: "health" } & Health)
  | { type: "alert_defaults"; value: AlertDefaults }
  | { type: "chart"; chartId: string; revision: number; saved: boolean }
  | { type: "alert"; value: Alert } | { type: "watchlist"; value: Watchlist } | { type: "screen"; value: Screen } | { type: "template"; value: Template }
  | { type: "alert_removed" | "watchlist_removed" | "screen_removed" | "template_removed"; id: string }
  | { type: "fired"; event: Fired }
  | { type: "catalog" };

export type { Drawing, DrawingStyle, Point, TimeframeBand, TimeUnit };
