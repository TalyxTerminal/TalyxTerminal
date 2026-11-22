import { t } from "../i18n";
/* Layout: TradingView's grid of pictures, one row per chart count (1 to 8), then the sync switches. A larger layout adds
   charts that copy the active chart's symbol and timeframe; a smaller one closes the last charts. */
import { useState } from "react";
import { TxButton, TxPopover, TxSwitch, TxTooltip, type ModuleContext } from "@talyx/sdk";
import { ARRANGEMENTS, type Arrangement } from "../chart/layouts";
import { closeChart, createChart, reportChartError, message, setLayout, store, useStore, type SyncOptions } from "../store";
import { ChartGlyph } from "../chart/icons";

const SYNC: [keyof SyncOptions, string][] = [["symbol", t("Symbol")], ["interval", t("Interval")], ["crosshair", t("Crosshair")], ["time", t("Time")], ["dateRange", t("Date range")]];

async function arrange(ctx: ModuleContext, panelId: string, next: Arrangement): Promise<void> {
  const layout = store.getState().layouts[panelId];
  const kept = layout.charts.slice(0, next.count);
  const added: string[] = [];
  for (let index = kept.length; index < next.count; index += 1) added.push(await createChart(ctx, { copyOf: layout.active }));
  for (const chartId of layout.charts.slice(next.count)) closeChart(chartId);
  const charts = [...kept, ...added];
  setLayout(panelId, current => ({ ...current, arrangement: next.id, charts, active: charts.includes(current.active) ? current.active : charts[0] }));
}

function Picture({ item }: { item: Arrangement }) {
  return <span className="chart-layout-picture" style={{ gridTemplateColumns: item.columns, gridTemplateRows: item.rows }}>
    {item.areas.map((area, index) => <span key={index} style={{ gridArea: area }} />)}
  </span>;
}

export function LayoutMenu({ ctx, panelId }: { ctx: ModuleContext; panelId: string }) {
  const layout = useStore(state => state.layouts[panelId]);
  const [open, setOpen] = useState(false);
  const counts = [...new Set(ARRANGEMENTS.map(item => item.count))];
  return <TxPopover open={open} onOpenChange={setOpen} label={t("Layout")} align="end"
    trigger={<TxButton variant="tool" size="sm" aria-label={t("Layout")}><ChartGlyph name="layout" /></TxButton>}>
    <div className="chart-layouts">
      {counts.map(count => <div key={count} className="chart-layouts-row">
        <span className="chart-layouts-count">{count}</span>
        {ARRANGEMENTS.filter(item => item.count === count).map(item => <TxTooltip key={item.id} label={count === 1 ? t("1 chart") : t("{count} charts", { count })}>
          <TxButton variant="tool" size="sm" aria-label={t("Layout {id}", { id: item.id })} aria-pressed={item.id === layout.arrangement}
            onClick={() => { setOpen(false); arrange(ctx, panelId, item).catch(error => reportChartError(layout.active, message(error))); }}><Picture item={item} /></TxButton>
        </TxTooltip>)}
      </div>)}
      {layout.charts.length > 1 && <div className="chart-layouts-sync">
        <span className="chart-layouts-title">{t("Sync in layout")}</span>
        {SYNC.map(([key, label]) => <TxSwitch key={key} label={label} checked={layout.sync[key]}
          onCheckedChange={checked => setLayout(panelId, current => ({ ...current, sync: { ...current.sync, [key]: checked } }))} />)}
      </div>}
    </div>
  </TxPopover>;
}
