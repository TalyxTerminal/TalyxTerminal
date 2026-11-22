import { t } from "../i18n";
/* The one top bar (TradingView): symbol and compare, timeframes, chart type, indicators, alert, undo and redo; layout,
   settings and snapshot on the right. Saving is the workspace's Save, not here. */
import type { WheelEvent } from "react";
import type { ModuleContext } from "@talyx/sdk";
import { TxButton, TxIcon, TxIconButton, TxTooltip } from "@talyx/sdk";
import { commit, openOverlay, setUi, undo, useStore, message, reportChartError } from "../store";
import { PRICE_PANE, type Indicator, type Pane } from "../wire";
import { TemplateMenu } from "./TemplateMenu";
import { ChartGlyph } from "../chart/icons";
import type { ChartBinding } from "../chart/binding";
import { Timeframes } from "./TimeframeMenu";
import { ChartTypes } from "./ChartTypeMenu";
import { LayoutMenu } from "./LayoutMenu";
import { SnapshotMenu } from "./SnapshotMenu";

/** A bar that does not fit scrolls sideways: the wheel turns it (a touchpad's sideways swipe scrolls it natively). */
export function wheelSideways(event: WheelEvent<HTMLElement>): void {
  if (!event.deltaX) event.currentTarget.scrollLeft += event.deltaY;
}

export function TopBar({ ctx, panelId, chartId, binding }: { ctx: ModuleContext; panelId: string; chartId: string; binding: () => ChartBinding | undefined }) {
  const chart = useStore(state => state.charts[chartId]?.chart);
  const replaying = useStore(state => Boolean(state.charts[chartId]?.replay));
  const picking = useStore(state => state.ui[panelId]?.replayPick === chartId);
  if (!chart) return <div className="chart-topbar" />;
  const { document } = chart;
  const step = (redo: boolean) => undo(ctx, chartId, redo).catch(error => reportChartError(chartId, message(error)));
  return <div className="chart-topbar" role="toolbar" aria-label={t("Chart")} onWheel={wheelSideways}>
    <TxButton variant="tool" size="sm" className="chart-tb-symbol" onClick={() => openOverlay(panelId, { kind: "symbol", mode: "symbol", query: "" })}>
      <TxIcon name="search" />{document.instrument.displaySymbol}
    </TxButton>
    <TxIconButton variant="tool" size="sm" icon="plus" label={t("Compare or add symbol")} onClick={() => openOverlay(panelId, { kind: "symbol", mode: "compare", query: "" })} />
    <span className="chart-tb-divider" />
    <Timeframes ctx={ctx} panelId={panelId} current={document.series.timeframe!} />
    <span className="chart-tb-divider" />
    <ChartTypes ctx={ctx} panelId={panelId} chartId={chartId} current={document.chartType} />
    <span className="chart-tb-divider" />
    <TxButton variant="tool" size="sm" onClick={() => openOverlay(panelId, { kind: "indicators" })}><TxIcon name="function" />{t("Indicators")}</TxButton>
    {/* An indicator template is the chart's indicators with their panes; applying one replaces the chart's. */}
    <TemplateMenu ctx={ctx} chartId={chartId} kind="indicators" tool={t("Indicator templates")}
      content={() => ({ indicators: document.indicators, panes: document.panes.filter(pane => pane.id !== PRICE_PANE) })}
      apply={content => void commit(ctx, chartId, [{ type: "indicators.replace", indicators: content.indicators as Indicator[],
        panes: [...document.panes.filter(pane => pane.id === PRICE_PANE), ...content.panes as Pane[]] }])} />
    <span className="chart-tb-divider" />
    <TxButton variant="tool" size="sm" onClick={() => openOverlay(panelId, { kind: "alert", chartId })}><TxIcon name="bell" />{t("Alert")}</TxButton>
    <TxButton variant="tool" size="sm" aria-pressed={replaying || picking} onClick={() => setUi(panelId, { replayPick: picking ? null : chartId })}><ChartGlyph name="replay" />{t("Replay")}</TxButton>
    <span className="chart-tb-divider" />
    <TxTooltip label={t("Undo (Ctrl+Z)")}><TxIconButton variant="tool" size="sm" icon="undo" label={t("Undo")} disabled={!chart.canUndo} onClick={() => void step(false)} /></TxTooltip>
    <TxTooltip label={t("Redo (Ctrl+Y)")}><TxIconButton variant="tool" size="sm" icon="redo" label={t("Redo")} disabled={!chart.canRedo} onClick={() => void step(true)} /></TxTooltip>
    <span className="chart-tb-spacer" />
    <LayoutMenu ctx={ctx} panelId={panelId} />
    <TxIconButton variant="tool" size="sm" icon="settings" label={t("Settings")} onClick={() => openOverlay(panelId, { kind: "settings", chartId })} />
    <SnapshotMenu ctx={ctx} chartId={chartId} binding={binding} name={document.instrument.displaySymbol} />
  </div>;
}
