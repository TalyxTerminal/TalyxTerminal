/* TradingView's "+" by the price scale: it follows the crosshair's price in the price pane, and a click adds an alert
   at that price. The binding leaves it in place while the pointer moves onto it. */
import { TxIconButton } from "@talyx/sdk";
import { chartEntry, openOverlay, setPricePointer, useStore } from "../store";
import { shownFormat } from "../engine/precision";
import type { ChartBinding } from "./binding";

export function PricePlus({ panelId, chartId, binding }: { panelId: string; chartId: string; binding: () => ChartBinding | null }) {
  const at = useStore(state => state.charts[chartId]?.pricePointer ?? null), view = binding()?.view;
  if (!at || !view) return null;
  const add = () => {
    const entry = chartEntry(chartId), precision = shownFormat(entry.feed.priceFormat, entry.chart!.document.display.precision).precision;
    setPricePointer(chartId, null);
    openOverlay(panelId, { kind: "alert", chartId, price: Number(at.price.toFixed(precision)) });
  };
  return <div className="chart-price-plus" style={{ top: at.y, [view.side]: view.axisWidth() }} onMouseLeave={() => setPricePointer(chartId, null)}>
    <TxIconButton size="sm" icon="plus" label="Add alert at this price" onClick={add} />
  </div>;
}
