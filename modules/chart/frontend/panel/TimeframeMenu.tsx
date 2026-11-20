/* The timeframes: starred ones sit on the top bar, every one in a grouped picker with stars (TradingView). Seconds and
   ticks are left out: Market has no such data. */
import { useState } from "react";
import { TxButton, TxIcon, TxPopover, type ModuleContext } from "@talyx/sdk";
import { commit, store, toggleFavorite, useStore } from "../store";
import { timeframeLabel, timeframeMs } from "../chart/timeframes";
import { StarList } from "./StarList";

export const TIMEFRAME_GROUPS: [string, string[]][] = [
  ["Minutes", ["1m", "3m", "5m", "15m", "30m", "45m"]],
  ["Hours", ["1h", "2h", "3h", "4h"]],
  ["Days", ["1d", "1w", "1M", "3M", "6M", "12M"]],
];

const NAMES: Record<string, string> = { m: "minute", h: "hour", d: "day", w: "week", M: "month" };
const longName = (timeframe: string) => {
  const count = Number(timeframe.slice(0, -1)), unit = timeframe.slice(-1);
  return `${count} ${NAMES[unit]}${count > 1 ? "s" : ""}`;
};

/** The active chart changes timeframe; with interval sync on, every chart of the layout does. False when one refused. */
export async function setTimeframe(ctx: ModuleContext, panelId: string, timeframe: string): Promise<boolean> {
  const layout = store.getState().layouts[panelId];
  const done = await Promise.all((layout.sync.interval ? layout.charts : [layout.active]).map(chartId => commit(ctx, chartId, [{ type: "selection.set", timeframe }])));
  return done.every(Boolean);
}

export function Timeframes({ ctx, panelId, current }: { ctx: ModuleContext; panelId: string; current: string }) {
  const favorites = useStore(state => state.layouts[panelId].favorites.timeframes);
  const [open, setOpen] = useState(false);
  const starred = [...favorites].sort((a, b) => timeframeMs(a) - timeframeMs(b));
  return <>
    {starred.map(timeframe => <TxButton key={timeframe} variant="tool" size="sm" aria-pressed={timeframe === current}
      onClick={() => void setTimeframe(ctx, panelId, timeframe)}>{timeframeLabel(timeframe)}</TxButton>)}
    {!starred.includes(current) && <TxButton variant="tool" size="sm" aria-pressed>{timeframeLabel(current)}</TxButton>}
    <TxPopover open={open} onOpenChange={setOpen} label="Timeframes"
      trigger={<TxButton variant="tool" size="sm" aria-label="Timeframes"><TxIcon name="chevron-down" /></TxButton>}>
      <StarList current={current} onStar={id => toggleFavorite(panelId, "timeframes", id)} onPick={id => { setOpen(false); void setTimeframe(ctx, panelId, id); }}
        groups={TIMEFRAME_GROUPS.map(([name, timeframes]) => ({ name, rows: timeframes.map(id => ({ id, label: longName(id), starred: favorites.includes(id) })) }))} />
    </TxPopover>
  </>;
}
