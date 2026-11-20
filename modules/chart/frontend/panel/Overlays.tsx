/* The overlay a panel has open, one at a time (store.overlays). */
import type { ModuleContext } from "@talyx/sdk";
import { useStore } from "../store";
import { SymbolSearch } from "./SymbolSearch";
import { IndicatorsDialog } from "./IndicatorsDialog";
import { GoToDialog } from "./GoToDialog";
import { IndicatorSettings } from "./IndicatorSettings";
import { DrawingSettings } from "./DrawingSettings";
import { AlertDialog } from "./AlertDialog";
import { ChartSettings } from "./ChartSettings";

export function Overlays({ ctx, panelId }: { ctx: ModuleContext; panelId: string }) {
  const overlay = useStore(state => state.overlays[panelId] ?? null);
  if (!overlay) return null;
  switch (overlay.kind) {
    case "symbol": return <SymbolSearch ctx={ctx} panelId={panelId} mode={overlay.mode} query={overlay.query} watchlistId={overlay.mode === "watchlist" ? overlay.watchlistId : undefined} />;
    case "indicators": return <IndicatorsDialog ctx={ctx} panelId={panelId} />;
    case "indicatorSettings": return <IndicatorSettings ctx={ctx} panelId={panelId} chartId={overlay.chartId} indicatorId={overlay.indicatorId} />;
    case "drawingSettings": return <DrawingSettings ctx={ctx} panelId={panelId} chartId={overlay.chartId} drawingId={overlay.drawingId} tab={overlay.tab} />;
    case "alert": return <AlertDialog ctx={ctx} panelId={panelId} chartId={overlay.chartId} alertId={overlay.alertId} price={overlay.price} drawingId={overlay.drawingId} />;
    case "goto": return <GoToDialog panelId={panelId} chartId={overlay.chartId} />;
    case "settings": return <ChartSettings ctx={ctx} panelId={panelId} chartId={overlay.chartId} />;
  }
}
