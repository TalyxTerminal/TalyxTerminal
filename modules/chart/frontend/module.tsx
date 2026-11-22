import { t } from "./i18n";
/* Chart's frontend entry: the panel (one TradingView layout of up to 8 charts) and the workspace Save hook.
   A panel's saved state is its layout and the revision of each chart; the charts themselves live in the backend. */
import type { ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { ModuleContextProvider, TxRoot, TxToastProvider, failure, type Json, type ModuleContext } from "@talyx/sdk";
import { post } from "./api";
import { DEFAULT_FAVORITES, createChart, closeChart, focusChart, newPanelUi, openChart, opensChart, openOverlay, store, watchWorkspace, type Layout } from "./store";
import { ChartPanel } from "./panel/ChartPanel";
import { chartStyles } from "./styles";
import { reportHealth } from "./health";
import { LayerChart, type LayerProps } from "./chart/LayerChart";

interface PanelState { version: 1; layout: Layout; revisions: Record<string, number> }

const NO_SYNC = { symbol: false, interval: false, crosshair: true, time: false, dateRange: false };

/** Market's open target: `instrument:<id>[/<timeframe>][@YYYY-MM-DD][;import=<set>]`. */
function parseTarget(objectId: unknown): { instrumentId: string; timeframe?: string; date?: string; importId?: string } | null {
  if (typeof objectId !== "string") return null;
  const match = /^instrument:([^@/;\s]+)(?:\/(\d+[a-zA-Z]+))?(?:@(\d{4}-\d{2}-\d{2}))?(?:;import=([\w.-]+))?$/.exec(objectId);
  return match ? { instrumentId: match[1], timeframe: match[2], date: match[3], importId: match[4] } : null;
}

function isPanelState(value: Json): value is PanelState & Json {
  return typeof value === "object" && value !== null && !Array.isArray(value) && value.version === 1;
}

function objectTarget(input: Json) {
  if (!input || typeof input !== "object" || Array.isArray(input) || typeof input.objectId !== "string") return null;
  return /^(chart|watchlist|alert|screen):(.+)$/.exec(input.objectId);
}

async function initialLayout(ctx: ModuleContext): Promise<Layout> {
  const input = ctx.initialState;
  if (isPanelState(input)) {
    // A copied workspace's panels name the original's charts; its own copies stand for them.
    const { charts } = await post(ctx, "/charts/adopt", { chartIds: input.layout.charts });
    const layout = { ...input.layout, charts: input.layout.charts.map(chartId => charts[chartId]), active: charts[input.layout.active] ?? input.layout.active };
    for (const chartId of layout.charts) openChart(ctx, chartId);
    return layout;
  }
  const object = objectTarget(input);
  if (object?.[1] === "chart") {
    const chartId = object[2];
    openChart(ctx, chartId);
    return { arrangement: "1", charts: [chartId], active: chartId, sync: NO_SYNC, favorites: DEFAULT_FAVORITES };
  }
  const target = parseTarget(typeof input === "object" && input !== null && !Array.isArray(input) ? input.objectId : null);
  const chartId = await createChart(ctx, target ? { instrumentId: target.instrumentId, timeframe: target.timeframe, importId: target.importId } : {});
  // Noon UTC falls inside that day's bar on every exchange calendar.
  if (target?.date) focusChart(chartId, { at: Date.parse(`${target.date}T12:00:00Z`) });
  return { arrangement: "1", charts: [chartId], active: chartId, sync: NO_SYNC, favorites: DEFAULT_FAVORITES };
}

function mountPanel(element: HTMLElement, ctx: ModuleContext) {
  if (!ctx.panelRef) throw failure("invalid_target", t("Chart requires a mounted panel."));
  const panelId = ctx.panelRef.panelId;
  element.style.height = "100%"; element.style.minHeight = "0";
  const root = createRoot(element);
  const render = (body: ReactNode) => root.render(<ModuleContextProvider.Provider value={ctx}>
    <TxRoot overlay={ctx.overlayRoot} style={{ height: "100%", minHeight: 0 }}><TxToastProvider><style>{chartStyles}</style>{body}</TxToastProvider></TxRoot>
  </ModuleContextProvider.Provider>);
  render(<div className="chart-loading">{t("Opening chart…")}</div>);

  let disposed = false;
  const stops: (() => void)[] = [];
  stops.push(watchWorkspace(ctx, (chartId, saved) => {
    if (!saved && store.getState().layouts[panelId]?.charts.includes(chartId)) ctx.panel?.stateChanged();
  }));
  void initialLayout(ctx).then(layout => {
    if (disposed) return;
    const ui = newPanelUi();
    const object = objectTarget(ctx.initialState);
    if (object?.[1] === "watchlist") ui.rightPanel = "watchlist";
    if (object?.[1] === "screen") ui.bottomPanel = "screener";
    if (object?.[1] === "alert") {
      ui.rightPanel = "alerts";
      openOverlay(panelId, { kind: "alert", chartId: layout.active, alertId: object[2] });
    }
    store.setState(state => ({ layouts: { ...state.layouts, [panelId]: layout }, ui: { ...state.ui, [panelId]: ui } }));
    // A new arrangement or chart is the user's change; which chart is active only moves where they look.
    stops.push(store.subscribe((state, previous) => {
      const next = state.layouts[panelId], before = previous.layouts[panelId];
      if (!next || next === before) return;
      const onlyActive = before && next.arrangement === before.arrangement && next.charts === before.charts && next.sync === before.sync && next.favorites === before.favorites;
      ctx.panel?.stateChanged(onlyActive ? { derived: true } : undefined);
    }));
    render(<ChartPanel ctx={ctx} panelId={panelId} />);
  }, error => { if (!disposed) render(<p role="alert" className="chart-loading">{error instanceof Error ? error.message : String(error)}</p>); });

  return {
    captureState: (): Json => {
      const state = store.getState(), layout = state.layouts[panelId];
      if (!layout) return ctx.initialState;
      // A chart still opening keeps the revision it was saved with.
      const saved = isPanelState(ctx.initialState) ? ctx.initialState.revisions : {};
      const revisions = Object.fromEntries(layout.charts.map(chartId => [chartId, state.charts[chartId]?.chart?.revision ?? saved[chartId]]));
      return { version: 1, layout, revisions } as unknown as Json;
    },
    dispose: () => {
      disposed = true;
      for (const stop of stops) stop();
      // The tree goes before what it reads. A panel of a stopped Chart that a restarted one mounted again meanwhile
      // (Retry) leaves the panel's layout and charts to it.
      root.unmount();
      const layout = store.getState().layouts[panelId];
      if (layout?.charts.some(chartId => !opensChart(ctx, chartId))) return;
      for (const chartId of layout?.charts ?? []) closeChart(chartId);
      store.setState(state => {
        const layouts = { ...state.layouts }, ui = { ...state.ui };
        delete layouts[panelId]; delete ui[panelId];
        return { layouts, ui };
      });
    },
  };
}

export async function activate(ctx: ModuleContext): Promise<void> {
  ctx.views.provide("layer_chart", (element, view) => {
    element.style.height = "100%";
    const root = createRoot(element);
    const render = (props: LayerProps) => root.render(<ModuleContextProvider.Provider value={view}>
      <TxRoot overlay={view.overlayRoot} style={{ height: "100%", minHeight: 0 }}><style>{chartStyles}</style>
        <LayerChart ctx={view} {...props} /></TxRoot>
    </ModuleContextProvider.Provider>);
    render(view.props as unknown as LayerProps);
    return { update: props => render(props as unknown as LayerProps), dispose: () => root.unmount() };
  });
  // A verified Save keeps the charts its panels name, at the captured revisions; the backend drops the rest.
  ctx.onDispose(ctx.workspace.onSaved(async saved => {
    const charts = saved.panels.filter(isPanelState).flatMap(panel => Object.entries(panel.revisions).map(([chartId, revision]) => ({ chartId, revision })));
    await post(ctx, "/charts/save", { charts });
  }));
  ctx.panels.provide("main", mountPanel);
  ctx.onDispose(reportHealth(ctx));
}
