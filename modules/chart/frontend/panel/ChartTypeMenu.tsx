/* Chart type: the current type's glyph on the top bar; starred types beside it; every type in a picker with stars. */
import { useState } from "react";
import { TxButton, TxPopover, TxTooltip, type ModuleContext } from "@talyx/sdk";
import { CHART_TYPES, type ChartType } from "../engine/mainSeries";
import { commit, toggleFavorite, useStore } from "../store";
import { ChartTypeGlyph } from "../chart/icons";
import { StarList } from "./StarList";

export const CHART_TYPE_NAMES: Record<ChartType, string> = {
  bars: "Bars", candles: "Candles", hollow_candles: "Hollow candles", columns: "Columns", line: "Line", line_markers: "Line with markers",
  step_line: "Step line", area: "Area", baseline: "Baseline", high_low: "High-low", heikin_ashi: "Heikin Ashi", renko: "Renko",
  line_break: "Line break", kagi: "Kagi", point_figure: "Point & figure", range: "Range",
};
const GROUPS: ChartType[][] = [
  ["bars", "candles", "hollow_candles"],
  ["columns", "line", "line_markers", "step_line"],
  ["area", "baseline"],
  ["high_low"],
  ["heikin_ashi", "renko", "line_break", "kagi", "point_figure", "range"],
];

export function ChartTypes({ ctx, panelId, chartId, current }: { ctx: ModuleContext; panelId: string; chartId: string; current: ChartType }) {
  const favorites = useStore(state => state.layouts[panelId].favorites.chartTypes);
  const [open, setOpen] = useState(false);
  const pick = (type: string) => void commit(ctx, chartId, [{ type: "chart_type.set", chartType: type as ChartType }]);
  const pinned = CHART_TYPES.filter(type => favorites.includes(type) && type !== current);
  return <>
    <TxPopover open={open} onOpenChange={setOpen} label="Chart type"
      trigger={<TxButton variant="tool" size="sm" aria-label={`Chart type: ${CHART_TYPE_NAMES[current]}`}><ChartTypeGlyph type={current} /></TxButton>}>
      <StarList current={current} onStar={id => toggleFavorite(panelId, "chartTypes", id)} onPick={id => { setOpen(false); pick(id); }}
        groups={GROUPS.map(types => ({ name: null, rows: types.map(id => ({ id, label: CHART_TYPE_NAMES[id], glyph: <ChartTypeGlyph type={id} />, starred: favorites.includes(id) })) }))} />
    </TxPopover>
    {pinned.map(type => <TxTooltip key={type} label={CHART_TYPE_NAMES[type]}>
      <TxButton variant="tool" size="sm" aria-label={CHART_TYPE_NAMES[type]} onClick={() => pick(type)}><ChartTypeGlyph type={type} /></TxButton>
    </TxTooltip>)}
  </>;
}
