import { t } from "../i18n";
/* The bottom bar (TradingView): time ranges that also pick a fitting timeframe, go to date, the bottom panel's tabs; on
   the right the clock in the chart's time zone (click to change it) and ADJ for stocks. */
import { useEffect, useState } from "react";
import { TxButton, TxIcon, TxMenu, TxTooltip, type ModuleContext } from "@talyx/sdk";
import { chartEntry, commit, focusChart, openOverlay, shownZone, useStore } from "../store";
import { clockText, zoneLabel } from "../engine/timeZone";
import { setTimeframe } from "./TimeframeMenu";
import { setAuto } from "./ChartMenu";
import type { ChartView } from "../engine/ChartView";
import { BottomTabs } from "../bottom/BottomPanel";
import { wheelSideways } from "./TopBar";

const DAY = 86_400_000;
const RANGES: { label: string; title: string; timeframe: string; from: (now: number) => number | null }[] = [
  { label: "1D", title: t("1 day in 1 minute intervals"), timeframe: "1m", from: now => now - DAY },
  { label: "5D", title: t("5 days in 5 minute intervals"), timeframe: "5m", from: now => now - 5 * DAY },
  { label: "1M", title: t("1 month in 30 minute intervals"), timeframe: "30m", from: now => now - 30 * DAY },
  { label: "3M", title: t("3 months in 1 hour intervals"), timeframe: "1h", from: now => now - 91 * DAY },
  { label: "6M", title: t("6 months in 2 hour intervals"), timeframe: "2h", from: now => now - 182 * DAY },
  { label: "YTD", title: t("Year to day in 1 day intervals"), timeframe: "1d", from: now => Date.UTC(new Date(now).getUTCFullYear(), 0, 1) },
  { label: "1Y", title: t("1 year in 1 day intervals"), timeframe: "1d", from: now => now - 365 * DAY },
  { label: "5Y", title: t("5 years in 1 week intervals"), timeframe: "1w", from: now => now - 5 * 365 * DAY },
  { label: t("All"), title: t("All data in 1 month intervals"), timeframe: "1M", from: () => null },
];
export const ZONES = ["UTC", "America/New_York", "America/Chicago", "America/Los_Angeles", "Europe/London", "Europe/Berlin", "Asia/Shanghai", "Asia/Hong_Kong", "Asia/Tokyo", "Asia/Singapore", "Australia/Sydney"];

export function BottomBar({ ctx, panelId, chartId, view }: { ctx: ModuleContext; panelId: string; chartId: string; view(): ChartView | null }) {
  const document = useStore(state => state.charts[chartId]?.chart?.document);
  const exchangeZone = useStore(state => state.charts[chartId]?.exchangeZone) ?? "UTC";
  const zone = useStore(state => shownZone(state.charts[chartId]));
  const auto = useStore(state => state.charts[chartId]?.autoScale);
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  if (!document) return <div className="chart-bottombar" />;
  const local = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const chosen = document.display.timeZone ?? "exchange";
  const setZone = (timeZone: string) => void commit(ctx, chartId, [{ type: "display.update", patch: { timeZone } }]);
  const range = async (item: (typeof RANGES)[number]) => {
    const from = item.from(Date.now()) ?? 0;
    if (document.series.timeframe !== item.timeframe && !await setTimeframe(ctx, panelId, item.timeframe)) return;
    focusChart(chartId, { from, to: null, seriesId: chartEntry(chartId).chart!.document.series.seriesId });
  };
  const scale = document.display.scale ?? {}, log = scale.mode === "log";
  const adjustable = document.series.adjustment !== "not_applicable" && document.series.adjustment !== "provider_adjusted";
  const adjusted = document.series.adjustment === "split_dividend";
  return <div className="chart-bottombar" onWheel={wheelSideways}>
    {RANGES.map(item => <TxTooltip key={item.label} label={item.title}>
      <TxButton variant="tool" size="sm" onClick={() => void range(item)}>{item.label}</TxButton>
    </TxTooltip>)}
    <span className="chart-tb-divider" />
    <TxTooltip label={t("Go to")}><TxButton variant="tool" size="sm" aria-label={t("Go to date")} onClick={() => openOverlay(panelId, { kind: "goto", chartId })}><TxIcon name="calendar" /></TxButton></TxTooltip>
    <span className="chart-tb-divider" />
    <BottomTabs panelId={panelId} />
    <span className="chart-tb-spacer" />
    <TxMenu label={t("Time zone")} align="end" items={[
      { id: "exchange", label: t("Exchange"), detail: zoneLabel(exchangeZone, now), checked: chosen === "exchange", onSelect: () => setZone("exchange") },
      ...[...new Set([local, ...ZONES])].map((item, index) => ({ id: item, label: item.replace(/_/g, " "), detail: zoneLabel(item, now), separated: index === 0, checked: chosen === item, onSelect: () => setZone(item) })),
    ]} trigger={<TxButton variant="tool" size="sm" className="chart-clock">{clockText(zone, now, true)} ({zoneLabel(zone, now)})</TxButton>} />
    {adjustable && <TxTooltip label={adjusted ? t("Adjusted for dividends: on") : t("Adjusted for dividends: off")}>
      <TxButton variant="tool" size="sm" aria-pressed={adjusted} onClick={() => void commit(ctx, chartId, [{ type: "selection.set", adjustment: adjusted ? "split" : "split_dividend" }])}>{t("ADJ")}</TxButton>
    </TxTooltip>}
    <TxTooltip label={t("Toggle log scale")}><TxButton variant="tool" size="sm" aria-pressed={log}
      onClick={() => void commit(ctx, chartId, [{ type: "display.update", patch: { scale: { ...scale, mode: log ? "regular" : "log" } } }])}>{t("log")}</TxButton></TxTooltip>
    <TxTooltip label={t("Toggle auto scale")}><TxButton variant="tool" size="sm" aria-pressed={auto} disabled={!view()}
      onClick={() => setAuto(view()!, chartId, !auto)}>{t("auto")}</TxButton></TxTooltip>
  </div>;
}
