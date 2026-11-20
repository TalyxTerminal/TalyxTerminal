/* A Chart panel is one TradingView layout: one top bar, the drawing toolbar on the left, the charts edge to edge, the
   right rail, the bottom bar; separators are 1px lines. The one overlay a panel has open is rendered here. */
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import type { ModuleContext } from "@talyx/sdk";
import { chartEntry, openOverlay, setLayout, setUi, store, undo, useStore, message, reportChartError } from "../store";
import { ChartCell } from "../chart/ChartCell";
import type { ChartBinding, BindingEvents } from "../chart/binding";
import { arrangement } from "../chart/layouts";
import { timeframeLabel } from "../chart/timeframes";
import type { OverlayType } from "../drawings/registry";
import { TopBar } from "./TopBar";
import { RightPanelSplit, RightRail } from "../rail/RightRail";
import { BottomBar } from "./BottomBar";
import { BottomPanel } from "../bottom/BottomPanel";
import { DrawingToolbar, TOOL_SHORTCUTS } from "./DrawingToolbar";
import { Overlays } from "./Overlays";
import { ChartMenu, type ChartMenuAt } from "./ChartMenu";

const SHORTCUT_TOOLS = Object.fromEntries(Object.entries(TOOL_SHORTCUTS).map(([tool, keys]) => [keys!.toLowerCase(), tool as OverlayType]));

export function ChartPanel({ ctx, panelId }: { ctx: ModuleContext; panelId: string }) {
  const layout = useStore(state => state.layouts[panelId]);
  const bindings = useRef(new Map<string, ChartBinding>()).current;
  const syncing = useRef(false);
  const current = useRef(layout);
  current.current = layout;
  const shape = arrangement(layout.arrangement);
  const [menu, setMenu] = useState<ChartMenuAt | null>(null);
  // Bindings keep the events they were made with, so these read the layout through a ref.
  const events = useMemo(() => (chartId: string): BindingEvents => ({
    openDrawing: (drawingId, tab) => openOverlay(panelId, { kind: "drawingSettings", chartId, drawingId, tab }),
    drawingMenu: (drawingId, x, y) => setMenu({ chartId, x, y, drawingId }),
    chartMenu: (x, y, price) => setMenu({ chartId, x, y, price }),
    scaleMenu: (x, y) => setMenu({ chartId, x, y, scale: true }),
    toolDone: () => { if (!store.getState().ui[panelId].keepDrawing) setUi(panelId, { tool: null }); },
    crosshair: time => {
      if (syncing.current || !current.current.sync.crosshair) return;
      syncing.current = true;
      for (const [id, binding] of bindings) if (id !== chartId) binding.view?.showCrosshairAt(time);
      syncing.current = false;
    },
  }), []);
  const document = useStore(state => state.charts[layout.active]?.chart?.document);
  const workspaceError = useStore(state => state.workspaceError);
  // The current view: the active chart's symbol, timeframe and visible range; refreshed before Agent reads it.
  useEffect(() => {
    if (!document) return;
    const report = () => {
      const binding = bindings.get(layout.active), range = binding?.view?.visibleRange();
      const times = chartEntry(layout.active).feed.bars.time;
      const first = range ? Math.max(0, Math.ceil(range.from)) : 0;
      const last = range ? Math.min(times.length - 1, Math.floor(range.to)) : -1;
      const from = first <= last ? times[first] : null;
      const to = first <= last ? times[last] : null;
      const visible = from !== null && to !== null ? [`Visible ${new Date(from).toISOString()} to ${new Date(to).toISOString()}`] : [];
      ctx.view.report({
        summary: `${document.instrument.displaySymbol} · ${timeframeLabel(document.series.timeframe!)} · ${(document.instrument.venueName ?? document.instrument.venueId)}`,
        refs: { chartId: layout.active, chartType: document.chartType, instrumentId: document.instrument.instrumentId,
          symbol: document.instrument.identityVersion === 2 ? document.instrument.symbol : document.instrument.venueId + ":" + document.instrument.symbol,
          timeframe: document.series.timeframe!, priceBasis: document.series.priceBasis,
          ...(document.series.adjustment && document.series.adjustment !== "not_applicable" ? {adjustment:document.series.adjustment} : {}),
          ...(document.series.adjustmentAnchor ? {anchor:document.series.adjustmentAnchor} : {}),
          ...(document.series.sessionScope && document.series.sessionScope !== "continuous" ? {sessionScope:document.series.sessionScope} : {}),
          ...(from !== null && to !== null ? { from, to } : {}) },
        facts: visible,
        // The chart on screen, as the open target (the manifest's) that leads here.
        openTargets: [{ kind: "instrument", key: `instrument:${document.instrument.instrumentId}/${document.series.timeframe!}`, label: document.instrument.displaySymbol },
          ...layout.charts.map(id => ({ kind: "chart", key: `chart:${id}`, label: chartEntry(id)?.chart?.document.instrument.displaySymbol ?? "Chart" }))],
      });
    };
    report();
    return ctx.view.onFlush(report);
  }, [document, layout.active, layout.charts]);

  // TradingView's keys on the chart: typing opens symbol search; Ctrl+Z / Ctrl+Y; Alt+letter arms a drawing tool; Esc disarms.
  const onKey = (event: KeyboardEvent) => {
    const target = event.target as HTMLElement;
    if (target.closest("input, textarea, [contenteditable], [role=dialog], [role=menu]")) return;
    const active = current.current.active;
    const keys = `${event.altKey ? "alt+" : ""}${event.shiftKey ? "shift+" : ""}${event.key.toLowerCase()}`;
    if ((event.ctrlKey || event.metaKey) && (event.key === "z" || event.key === "y")) {
      event.preventDefault();
      void undo(ctx, active, event.key === "y" || event.shiftKey).catch(error => reportChartError(active, message(error)));
    } else if (SHORTCUT_TOOLS[keys]) {
      event.preventDefault();
      setUi(panelId, { tool: SHORTCUT_TOOLS[keys] });
    } else if (event.key === "Escape" && store.getState().ui[panelId].tool) setUi(panelId, { tool: null });
    else if (!event.ctrlKey && !event.metaKey && !event.altKey && /^[a-z0-9]$/i.test(event.key)) {
      event.preventDefault();
      openOverlay(panelId, { kind: "symbol", mode: "symbol", query: event.key });
    }
  };
  return <div className="chart-panel" onKeyDown={onKey}>
    <TopBar ctx={ctx} panelId={panelId} chartId={layout.active} binding={() => bindings.get(current.current.active)} />
    {workspaceError && <p className="chart-alert-error chart-panel-note" role="alert">Watchlists, alerts and indicators are unavailable: {workspaceError}</p>}
    <div className="chart-body">
      <RightPanelSplit ctx={ctx} panelId={panelId}>
        <div className="chart-main">
          <div className="chart-main-row">
            <DrawingToolbar ctx={ctx} panelId={panelId} />
            <div className="chart-grid" style={{ gridTemplateColumns: shape.columns, gridTemplateRows: shape.rows }} data-count={layout.charts.length}>
              {layout.charts.map((chartId, index) => <div key={chartId} className="chart-grid-cell" style={{ gridArea: shape.areas[index] }}>
                <ChartCell ctx={ctx} panelId={panelId} chartId={chartId} active={layout.charts.length > 1 && chartId === layout.active}
                  onActivate={() => { if (current.current.active !== chartId) setLayout(panelId, value => ({ ...value, active: chartId })); }}
                  events={events(chartId)} bindings={bindings}
                  onIndicatorSettings={(id, indicatorId) => openOverlay(panelId, { kind: "indicatorSettings", chartId: id, indicatorId })} />
              </div>)}
            </div>
          </div>
          <BottomBar ctx={ctx} panelId={panelId} chartId={layout.active} view={() => bindings.get(current.current.active)?.view ?? null} />
          <BottomPanel ctx={ctx} panelId={panelId} />
        </div>
      </RightPanelSplit>
      <RightRail panelId={panelId} />
    </div>
    <Overlays ctx={ctx} panelId={panelId} />
    {menu && <ChartMenu ctx={ctx} panelId={panelId} at={menu} onClose={() => setMenu(null)} view={chartId => bindings.get(chartId)?.view ?? null} />}
  </div>;
}
