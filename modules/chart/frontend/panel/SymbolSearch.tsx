/* Symbol search: TradingView's large search overlay, with Market's search box inside (Market owns the catalog, the
   categories and the rows). Picking switches the chart's symbol, or adds a comparison when opened from "+". */
import { ModuleView, TxDialog, type Json, type ModuleContext } from "@talyx/sdk";
import type { Instrument } from "@talyx/vocab";
import type { ContractTypes } from "../../../market/public/ts/types";
import { commit, message, openOverlay, reportChartError, store, switchSymbol, useStore } from "../store";
import { addToWatchlist } from "../rail/watchlists";
import { comparisonColor, newComparisonColor } from "../engine/theme";

export function SymbolSearch({ ctx, panelId, mode, query, watchlistId }: { ctx: ModuleContext; panelId: string; mode: "symbol" | "compare" | "watchlist"; query: string; watchlistId?: string }) {
  const close = () => openOverlay(panelId, null);
  const current = useStore(state => state.charts[state.layouts[panelId]?.active ?? ""]?.chart?.document.instrument.instrumentId);
  const picked = (instrument: Instrument) => {
    close();
    const layout = store.getState().layouts[panelId];
    if (mode === "watchlist") { void addToWatchlist(ctx, watchlistId!, instrument).catch(error => reportChartError(layout.active, message(error))); return; }
    if (mode === "compare") {
      // TradingView turns the price scale to percent when a symbol is compared.
      const document = store.getState().charts[layout.active].chart!.document, scale = document.display.scale ?? {};
      const color = newComparisonColor(document.comparisons.map((item, at) => item.color ?? comparisonColor(at)));
      void commit(ctx, layout.active, [{ type: "comparison.add", id: `cmp-${crypto.randomUUID().slice(0, 8)}`, instrumentId: instrument.instrumentId, scale: "same_percent", color },
        { type: "display.update", patch: { scale: { ...scale, mode: "percent" } } }]);
      return;
    }
    switchSymbol(ctx, panelId, instrument.instrumentId);
  };
  const props: ContractTypes["view:search_box"] = { query, placeholder: mode === "symbol" ? "Symbol, name or code" : mode === "compare" ? "Compare symbol" : "Add symbol", ...(current ? { currentInstrumentId: current } : {}) };
  return <TxDialog open onOpenChange={open => { if (!open) close(); }} title={mode === "compare" ? "Compare symbol" : mode === "watchlist" ? "Add symbol" : "Symbol search"} size="wide" footer={false}>
    <div className="chart-search">
      <ModuleView context={ctx} id="market.search_box" props={props as unknown as Json}
        on={{ selected: payload => picked((payload as unknown as { instrument: Instrument }).instrument), closed: close }} />
    </div>
  </TxDialog>;
}
