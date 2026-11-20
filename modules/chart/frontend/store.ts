/* The Chart frontend's one store: what the backend confirmed (chart documents, workspace objects) plus local
   state (layouts, the pointer, open menus). Chart data streams live in each chart's feed; the store holds the feed. */
import { createStore } from "zustand/vanilla";
import { useStore as useZustand } from "zustand";
import type { ErrorValue, ModuleContext, Subscription } from "@talyx/sdk";
import { post, subscribeChart, subscribeWorkspace } from "./api";
import { ChartFeed } from "./feed";
import type { Magnet } from "./drawings/controller";
import type { OverlayType } from "./drawings/registry";
import { PRICE_PANE } from "./wire";
import type { Alert, AlertDefaults, CatalogEntry, ChartItem, ChartState, Command, Fired, Health, IndicatorRef, MarketProblem, MarketState, Screen, Template, Watchlist, WorkspaceItem } from "./wire";

export interface ChartEntry {
  chart: ChartState | null;
  status: { state: MarketState; problem: MarketProblem | null; since: number } | null;
  indicatorErrors: Record<string, { code: string; message: string; line?: number }>;
  replay: { active: boolean; at: number | null; end: boolean } | null;
  /** The stream ended with an error, or a change was refused; shown on the chart until the next good news. */
  error: string | null;
  feed: ChartFeed;
  /** Bar index under the pointer (null: the newest bar). */
  hover: number | null;
  /** The crosshair's place and price in the price pane (the "+" by the price scale follows it). */
  pricePointer: PricePointer | null;
  selectedDrawing: string | null;
  /** What to bring into view once the bars reach it: a time to centre (go to date, a dated open target) or a range
      (the bottom bar's 1D…All); cleared when shown. */
  focus: Focus | null;
  /** The exchange's time zone from Market's calendar (UTC for 24/7 markets); null until known. */
  exchangeZone: string | null;
  /** The view reaches the oldest loaded bar (the legend then says when no older bars will come). */
  atStart: boolean;
  /** The price scale fits the visible bars (TradingView's Auto); dragging the scale turns it off. */
  autoScale: boolean;
}

/** `seriesId`: wait for that series' bars (a range that also switches the timeframe). */
export type Focus = ({ at: number } | { from: number; to: number | null }) & { seriesId?: string };

export interface SyncOptions { symbol: boolean; interval: boolean; crosshair: boolean; time: boolean; dateRange: boolean }
/** Starred timeframes sit on the top bar; starred chart types head the chart type menu (TradingView). */
export interface Favorites { timeframes: string[]; chartTypes: string[]; indicators: string[] }
export interface Layout { arrangement: string; charts: string[]; active: string; sync: SyncOptions; favorites: Favorites }

export const DEFAULT_FAVORITES: Favorites = { timeframes: ["1m", "5m", "15m", "1h", "4h", "1d", "1w"], chartTypes: ["candles", "line", "area"], indicators: [] };

export interface PanelUi {
  tool: OverlayType | null;
  magnet: Magnet;
  keepDrawing: boolean;
  drawingsLocked: boolean;
  drawingsHidden: boolean;
  rightPanel: "watchlist" | "alerts" | "objects" | null;
  /** The bottom panel's open tab (TradingView's screener and script editor), and its height in pixels. */
  bottomPanel: "screener" | "editor" | null;
  bottomHeight: number;
  /** The indicator open in the editor: one of the user's (editable) or a built-in (its code, read-only). */
  script: IndicatorRef | null;
  /** Width of the right panel in pixels (dragged on its edge). */
  rightWidth: number;
  /** The chart whose replay start is being picked (Replay in the top bar). */
  replayPick: string | null;
}

/** The one overlay a panel has open (a dialog or a large popover); menus keep their own open state. */
export type Overlay =
  | { kind: "symbol"; mode: "symbol" | "compare"; query: string }
  | { kind: "symbol"; mode: "watchlist"; query: string; watchlistId: string }
  | { kind: "indicators" }
  | { kind: "indicatorSettings"; chartId: string; indicatorId: string }
  | { kind: "drawingSettings"; chartId: string; drawingId: string; tab?: "text" }
  | { kind: "alert"; chartId: string; alertId?: string; price?: number; drawingId?: string }
  | { kind: "settings"; chartId: string }
  | { kind: "goto"; chartId: string };

export interface State {
  charts: Record<string, ChartEntry>;
  overlays: Record<string, Overlay | null>;
  layouts: Record<string, Layout>;
  ui: Record<string, PanelUi>;
  alerts: Record<string, Alert>;
  watchlists: Record<string, Watchlist>;
  screens: Record<string, Screen>;
  templates: Record<string, Template>;
  fired: Fired[];
  alertDefaults: AlertDefaults | null;
  catalog: CatalogEntry[] | null;
  /** The absolute path of the user's indicators folder (from the catalog). */
  indicatorsFolder: string | null;
  health: Health | null;
  /** The workspace stream or the indicator catalog could not be had: watchlists, alerts, templates and the library wait. */
  workspaceError: string | null;
  /** Charts that differ from the revision the workspace was last saved with. */
  unsaved: Record<string, boolean>;
}

export const store = createStore<State>(() => ({ charts: {}, overlays: {}, layouts: {}, ui: {}, alerts: {}, watchlists: {}, screens: {}, templates: {}, fired: [], alertDefaults: null, catalog: null, indicatorsFolder: null, health: null, workspaceError: null, unsaved: {} }));
export const useStore = <T>(select: (state: State) => T): T => useZustand(store, select);

export function newPanelUi(): PanelUi {
  return { tool: null, magnet: "off", keepDrawing: false, drawingsLocked: false, drawingsHidden: false, rightPanel: null, rightWidth: 300, bottomPanel: null, bottomHeight: 300, script: null, replayPick: null };
}

const setChart = (chartId: string, change: (entry: ChartEntry) => Partial<ChartEntry>) =>
  store.setState(state => {
    const entry = state.charts[chartId];
    return entry ? { charts: { ...state.charts, [chartId]: { ...entry, ...change(entry) } } } : {};
  });

export const chartEntry = (chartId: string): ChartEntry => store.getState().charts[chartId];

/** The zone the chart's times are shown in: the one chosen in its display, else the exchange's. */
export function shownZone(entry: ChartEntry | undefined): string {
  const chosen = entry?.chart?.document.display.timeZone ?? "exchange";
  return chosen === "exchange" ? entry?.exchangeZone ?? "UTC" : chosen;
}

/** Newer document wins; commits and the stream both deliver one. */
function takeChart(chartId: string, chart: ChartState): void {
  const entry = store.getState().charts[chartId];
  if (!entry || (entry.chart && entry.chart.revision >= chart.revision)) return;
  // Another series: the old one's bars, rows and data state go at once; the new series' items follow on the stream.
  if (entry.chart && entry.chart.document.series.seriesId !== chart.document.series.seriesId) {
    setChart(chartId, () => ({ status: null, indicatorErrors: {}, hover: null }));
    entry.feed.reset(chart.revision);
  }
  setChart(chartId, () => ({ chart, error: null }));
}

// ── charts ───────────────────────────────────────────────────────────────

/** Each open chart's stream, and the module context (one activation of Chart) that opened it. */
const streams = new Map<string, { ctx: ModuleContext; subscription: Promise<Subscription | null> }>();
const commitQueues = new Map<string, Promise<unknown>>();
/** Changes of a typed field (chart and field) that wait behind another change of their chart. */
const waitingCommits = new Map<string, { commands: Command[]; done: Promise<boolean> }>();

/** A refused subscription (the module stopping, a missing chart) is shown on the chart; there is then nothing to close. */
function openStream(ctx: ModuleContext, chartId: string): void {
  const subscription = subscribeChart(ctx, chartId, item => receive(chartId, item), error => streamEnded(chartId, error))
    .then(value => value, error => { setChart(chartId, () => ({ error: message(error) })); return null; });
  streams.set(chartId, { ctx, subscription });
}

/** Opens a chart of this workspace: its document and its stream, started together. A chart a previous activation of
    Chart opened (the module was restarted) is opened again: its stream ended with that activation. */
export function openChart(ctx: ModuleContext, chartId: string): void {
  if (store.getState().charts[chartId]) {
    if (streams.get(chartId)?.ctx === ctx) return;
    closeChart(chartId);
  }
  const feed = new ChartFeed();
  store.setState(state => ({ charts: { ...state.charts, [chartId]: { chart: null, status: null, indicatorErrors: {}, replay: null, error: null, feed, hover: null, selectedDrawing: null, focus: null, exchangeZone: null, atStart: false, autoScale: true, pricePointer: null } } }));
  void post(ctx, "/charts/open", { chartId }).then(chart => takeChart(chartId, chart), error => setChart(chartId, () => ({ error: message(error) })));
  openStream(ctx, chartId);
}

/** Whether this activation of Chart opened the chart (a panel of a stopped activation leaves it to the new one). */
export function opensChart(ctx: ModuleContext, chartId: string): boolean { return streams.get(chartId)?.ctx === ctx; }

/** A failed source stays failed until the user retries: the stream opens again. */
export function retryChart(ctx: ModuleContext, chartId: string): void {
  void streams.get(chartId)?.subscription.then(subscription => subscription?.close());
  setChart(chartId, () => ({ error: null, status: null }));
  openStream(ctx, chartId);
}

export function closeChart(chartId: string): void {
  void streams.get(chartId)?.subscription.then(subscription => subscription?.close());
  streams.delete(chartId);
  store.setState(state => { const charts = { ...state.charts }; delete charts[chartId]; return { charts }; });
}

function streamEnded(chartId: string, error?: ErrorValue): void {
  if (error) setChart(chartId, () => ({ error: error.message }));
}

function receive(chartId: string, item: ChartItem): void {
  const entry = store.getState().charts[chartId];
  if (!entry) return;
  // What the stream sends before the document of the series shown now belongs to the old series (see ChartFeed.current).
  if (item.type === "document") entry.feed.streamReached(item.revision);
  else if (item.type !== "replay" && !entry.feed.current) return;
  switch (item.type) {
    case "document": takeChart(chartId, { chartId, revision: item.revision, document: item.document, drawingGroup: item.drawingGroup, canUndo: item.canUndo, canRedo: item.canRedo }); break;
    case "bars": entry.feed.takeBars(item); break;
    case "update": entry.feed.takeUpdate(item); break;
    case "price_format": entry.feed.takePriceFormat(item.priceFormat); break;
    case "status": setChart(chartId, () => ({ status: { state: item.state, problem: item.problem, since: item.since } })); break;
    case "indicator":
      entry.feed.takeIndicator(item);
      if (entry.indicatorErrors[item.instanceId]) setChart(chartId, current => { const errors = { ...current.indicatorErrors }; delete errors[item.instanceId]; return { indicatorErrors: errors }; });
      break;
    case "indicator_error": setChart(chartId, current => ({ indicatorErrors: { ...current.indicatorErrors, [item.instanceId]: { code: item.code, message: item.message, line: item.line } } })); break;
    case "comparison": entry.feed.takeComparison(item); break;
    case "replay": setChart(chartId, () => ({ replay: item.active ? { active: true, at: item.at, end: item.end } : null })); break;
  }
}

/** One group of commands on the chart's current revision; changes of one chart go one after another. A refused change
    is shown on the chart and resolves false. `field` names a value the user is typing: a change of that field still
    waiting its turn takes the newer value instead of queueing one change per keystroke. */
export function commit(ctx: ModuleContext, chartId: string, commands: Command[], field?: string): Promise<boolean> {
  const slot = field === undefined ? null : `${chartId}\n${field}`;
  const waiting = slot === null ? undefined : waitingCommits.get(slot);
  if (waiting) { waiting.commands = commands; return waiting.done; }
  const change = { commands, done: Promise.resolve(true) };
  const previous = commitQueues.get(chartId) ?? Promise.resolve(true);
  change.done = previous.then(async () => {
    if (slot !== null) waitingCommits.delete(slot);
    try {
      takeChart(chartId, await post(ctx, "/charts/commit", { chartId, baseRevision: chartEntry(chartId).chart!.revision, commands: change.commands }));
      return true;
    } catch (error) { setChart(chartId, () => ({ error: message(error) })); return false; }
  });
  if (slot !== null) waitingCommits.set(slot, change);
  commitQueues.set(chartId, change.done);
  return change.done;
}

/** Remove objects (the drawing toolbar and the chart menu): the shown drawing group, every indicator with its panes,
    or both. */
export function removeObjects(ctx: ModuleContext, chartId: string, what: "drawings" | "indicators" | "all"): Promise<boolean> {
  const chart = chartEntry(chartId).chart!, document = chart.document;
  return commit(ctx, chartId, [
    ...(what !== "indicators" && (document.drawings[chart.drawingGroup] ?? []).length ? [{ type: "drawing.bulk" as const, remove: true as const }] : []),
    ...(what !== "drawings" && document.indicators.length ? [{ type: "indicators.replace" as const, indicators: [], panes: document.panes.filter(pane => pane.id === PRICE_PANE) }] : []),
  ]);
}

/** The active chart shows this symbol; with symbol sync on, every chart of the layout follows (TradingView's layout sync). */
export function switchSymbol(ctx: ModuleContext, panelId: string, instrumentId: string): void {
  const layout = store.getState().layouts[panelId];
  for (const chartId of layout.sync.symbol ? layout.charts : [layout.active]) void commit(ctx, chartId, [{ type: "selection.set", instrumentId }]);
}

export async function undo(ctx: ModuleContext, chartId: string, redo = false): Promise<void> {
  takeChart(chartId, await post(ctx, redo ? "/charts/redo" : "/charts/undo", { chartId }));
}

export async function createChart(ctx: ModuleContext, input: { instrumentId?: string; timeframe?: string; importId?: string; copyOf?: string }): Promise<string> {
  const chart = await post(ctx, "/charts/create", input);
  openChart(ctx, chart.chartId);
  takeChart(chart.chartId, chart);
  return chart.chartId;
}

export function setHover(chartId: string, hover: number | null): void {
  if (chartEntry(chartId)?.hover !== hover) setChart(chartId, () => ({ hover }));
}

export interface PricePointer { y: number; price: number }
export function setPricePointer(chartId: string, pointer: PricePointer | null): void {
  const current = chartEntry(chartId)?.pricePointer ?? null;
  if (current?.y !== pointer?.y || current?.price !== pointer?.price) setChart(chartId, () => ({ pricePointer: pointer }));
}

export function selectDrawing(chartId: string, id: string | null): void { setChart(chartId, () => ({ selectedDrawing: id })); }

export function setAtStart(chartId: string, atStart: boolean): void { if (chartEntry(chartId)?.atStart !== atStart) setChart(chartId, () => ({ atStart })); }

export function setAutoScale(chartId: string, autoScale: boolean): void { if (chartEntry(chartId).autoScale !== autoScale) setChart(chartId, () => ({ autoScale })); }

export function setExchangeZone(chartId: string, zone: string): void { setChart(chartId, () => ({ exchangeZone: zone })); }

export function focusChart(chartId: string, focus: Focus | null): void { setChart(chartId, () => ({ focus })); }

export function reportChartError(chartId: string, text: string): void { setChart(chartId, () => ({ error: text })); }

// ── layouts and panel state ──────────────────────────────────────────────

export function setLayout(panelId: string, change: (layout: Layout) => Layout): void {
  store.setState(state => ({ layouts: { ...state.layouts, [panelId]: change(state.layouts[panelId]) } }));
}

export function toggleFavorite(panelId: string, key: keyof Favorites, id: string): void {
  setLayout(panelId, layout => {
    const list = layout.favorites[key];
    return { ...layout, favorites: { ...layout.favorites, [key]: list.includes(id) ? list.filter(item => item !== id) : [...list, id] } };
  });
}

export function openOverlay(panelId: string, overlay: Overlay | null): void {
  store.setState(state => ({ overlays: { ...state.overlays, [panelId]: overlay } }));
}

export function setUi(panelId: string, change: Partial<PanelUi>): void {
  store.setState(state => ({ ui: { ...state.ui, [panelId]: { ...state.ui[panelId], ...change } } }));
}

/** TradingView's Source code: the indicator's code in the editor tab of the bottom panel. */
export function openSource(panelId: string, script: IndicatorRef): void {
  setUi(panelId, { bottomPanel: "editor", script });
}

// ── workspace objects ────────────────────────────────────────────────────

type ChartChanged = (chartId: string, saved: boolean) => void;
/** The workspace stream of one activation of Chart, with every open panel's listener. */
let workspace: { ctx: ModuleContext; listeners: Set<ChartChanged>; stream: Promise<Subscription | null> } | null = null;

/** The workspace stream runs while any Chart panel of this activation is open; a restarted Chart opens its own. */
export function watchWorkspace(ctx: ModuleContext, onChartChanged: ChartChanged): () => void {
  if (workspace?.ctx !== ctx) {
    const listeners = new Set<ChartChanged>();
    const failed = (error: unknown) => { store.setState({ workspaceError: message(error) }); return null; };
    store.setState({ workspaceError: null });
    const stream = subscribeWorkspace(ctx, item => {
      if (item.type === "catalog") void refreshCatalog(ctx).catch(failed);
      else takeWorkspace(item, listeners);
    }, error => { if (error) failed(new Error(error.message)); }).then(value => value, failed);
    workspace = { ctx, listeners, stream };
    void refreshCatalog(ctx).catch(failed);
  }
  const mine = workspace;
  mine.listeners.add(onChartChanged);
  return () => {
    mine.listeners.delete(onChartChanged);
    if (mine.listeners.size) return;
    void mine.stream.then(subscription => subscription?.close());
    if (workspace === mine) workspace = null;
  };
}

/** The indicator catalog again (a user indicator was saved or added). */
export async function refreshCatalog(ctx: ModuleContext): Promise<void> {
  const catalog = await post(ctx, "/indicators/catalog", {});
  store.setState({ catalog: catalog.entries, indicatorsFolder: catalog.folder });
}

const byId = <T extends { id: string }>(items: T[]) => Object.fromEntries(items.map(item => [item.id, item]));
type Keyed = "alerts" | "watchlists" | "screens" | "templates";
const COLLECTIONS: Record<string, Keyed> = { alert: "alerts", watchlist: "watchlists", screen: "screens", template: "templates" };

function takeWorkspace(item: Exclude<WorkspaceItem, { type: "catalog" }>, listeners: Set<ChartChanged>): void {
  switch (item.type) {
    case "snapshot": store.setState({ alerts: byId(item.alerts), watchlists: byId(item.watchlists), screens: byId(item.screens), templates: byId(item.templates), alertDefaults: item.alertDefaults, health: item.health }); return;
    case "health": store.setState({ health: { pine: item.pine, alertStore: item.alertStore } }); return;
    case "alert_defaults": store.setState({ alertDefaults: item.value }); return;
    case "chart":
      store.setState(state => ({ unsaved: { ...state.unsaved, [item.chartId]: !item.saved } }));
      for (const listener of listeners) listener(item.chartId, item.saved);
      return;
    // A fire comes again once its notifications are delivered: the newer copy replaces it.
    case "fired": store.setState(state => ({ fired: [item.event, ...state.fired.filter(event => event.id !== item.event.id)].slice(0, 500) })); return;
    case "alert": case "watchlist": case "screen": case "template": {
      const key = COLLECTIONS[item.type];
      store.setState(state => ({ [key]: { ...state[key], [item.value.id]: item.value } }) as Partial<State>);
      return;
    }
    default: {
      const key = COLLECTIONS[item.type.replace("_removed", "")];
      store.setState(state => { const next = { ...state[key] }; delete next[item.id]; return { [key]: next } as Partial<State>; });
    }
  }
}

export function message(error: unknown): string { return error instanceof Error ? error.message : String(error); }
