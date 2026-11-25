import { indicatorLabel } from "../indicatorText";
import { t } from "../i18n";
/* TradingView's status line: symbol, timeframe and exchange with the market state, OHLC and the change under the pointer;
   one row per indicator (name, inputs, values) with show/hide, settings, source code and remove on hover. Data state only when not live. */
import { useState } from "react";
import { useFeedVersion } from "./feedVersion";
import { TxButton, TxIconButton, TxMenu, type ModuleContext } from "@talyx/sdk";
import { formatCompact, formatPrice, formatValue, shownFormat } from "../engine/precision";
import { pineColor, scriptColor } from "../engine/pinePrimitives";
import { commit, openSource, retryChart, useStore, type ChartEntry } from "../store";
import { PRICE_PANE, type CatalogEntry, type Comparison, type Indicator } from "../wire";
import { comparisonColor } from "../engine/theme";
import { timeframeLabel } from "./timeframes";

const STATE_TEXT: Record<string, string> = { connecting: t("Connecting"), delayed: t("Delayed"), history_only: t("History only"), market_closed: t("Market closed"), recovering: t("Reconnecting"), failed: t("Disconnected") };

export function Legend({ ctx, panelId, chartId, onIndicatorSettings, tops }: { ctx: ModuleContext; panelId: string; chartId: string; onIndicatorSettings(id: string): void; tops: Map<string, number> }) {
  const entry = useStore(state => state.charts[chartId]);
  const catalog = useStore(state => state.catalog);
  const [collapsed, setCollapsed] = useState(false);
  const version = useFeedVersion(entry);
  if (!entry?.chart) return null;
  const { document } = entry.chart, feed = entry.feed, bars = feed.bars;
  const count = bars.time.length;
  const index = entry.hover !== null && entry.hover < count ? entry.hover : count - 1;
  const precision = shownFormat(feed.priceFormat, document.display.precision).precision;
  // Chart settings › Status line: each part is shown unless turned off.
  const shows = { title: true, ohlc: true, change: true, volume: true, indicatorArguments: true, indicatorValues: true, ...document.display.statusLine };
  const up = index >= 0 && bars.close[index] >= bars.open[index];
  const previous = index > 0 ? bars.close[index - 1] : null;
  const change = index >= 0 && previous !== null ? bars.close[index] - previous : null;
  const status = entry.status;
  const row = (indicator: Indicator) => <IndicatorRow key={indicator.id} ctx={ctx} panelId={panelId} chartId={chartId} entry={entry} indicator={indicator} index={index} precision={precision}
    arguments={shows.indicatorArguments} values={shows.indicatorValues}
    definition={catalog?.find(item => item.source === indicator.definition.source && item.id === indicator.definition.id)} onSettings={() => onIndicatorSettings(indicator.id)} />;
  // TradingView: an indicator in its own pane has its legend at that pane's top left.
  const panes = document.panes.filter(pane => pane.id !== PRICE_PANE && tops.has(pane.id));
  return <>{panes.map(pane => <div key={pane.id} className="chart-legend chart-pane-legend" style={{ top: `calc(${tops.get(pane.id)}px + var(--tx-space-1-half))` }}>
    {document.indicators.filter(indicator => indicator.paneId === pane.id).map(row)}
  </div>)}<div className="chart-legend" data-version={version} style={{ top: `calc(${tops.get(PRICE_PANE) ?? 0}px + var(--tx-space-1-half))` }}>
    {/* One line; parts that don't fit wrap onto the hidden second line, the last first: volume, change, OHLC, then the title shortens. */}
    <div className="chart-legend-row chart-legend-main chart-legend-fit">
      <span className="chart-legend-head">
        {shows.title && <span className="chart-legend-title">{(document.instrument.name ?? document.instrument.displaySymbol)} · {timeframeLabel(document.series.timeframe!)} · {(document.instrument.venueName ?? document.instrument.venueId)}</span>}
        {status && status.state !== "connecting" && <span className="chart-state-dot" data-state={status.state} title={STATE_TEXT[status.state] ?? t("Live")} />}
      </span>
      {shows.ohlc && index >= 0 && <span className="chart-legend-values" data-direction={up ? "up" : "down"}>
        {([t("O"), t("H"), t("L"), t("C")] as const).map((key, at) => <span key={key}><span className="chart-legend-key">{key}</span>{formatPrice([bars.open, bars.high, bars.low, bars.close][at][index], precision)}</span>)}
      </span>}
      {shows.change && index >= 0 && change !== null && <span className="chart-legend-values" data-direction={up ? "up" : "down"}>{change >= 0 ? "+" : ""}{formatPrice(change, precision)} ({change >= 0 ? "+" : ""}{(change / previous! * 100).toFixed(2)}%)</span>}
      {shows.volume && index >= 0 && bars.volume[index] !== null && <span className="chart-legend-values"><span className="chart-legend-key">{t("Vol")}</span>{formatCompact(bars.volume[index]!)}</span>}
    </div>
    {/* Connecting (before the first live) shows nothing unless an attempt failed. */}
    {status && status.state !== "live" && (status.state !== "connecting" || status.problem) && <div className="chart-legend-row chart-data-state" data-state={status.state}>
      <span>{STATE_TEXT[status.state]}{status.problem ? ` · ${status.problem.message}` : ""}</span>
      {status.state === "failed" && <TxButton variant="quiet" size="sm" onClick={() => retryChart(ctx, chartId)}>{t("Retry")}</TxButton>}
    </div>}
    {entry.atStart && feed.limitReached && <div className="chart-legend-row chart-data-state" title={t("Use a longer timeframe to see further back")}>{t("30,000 bar limit reached")}</div>}
    {entry.error && <div className="chart-legend-row chart-data-state" data-state="failed" role="alert">{entry.error}</div>}
    {document.comparisons.map((comparison, at) => <ComparisonRow key={comparison.id} ctx={ctx} chartId={chartId} entry={entry} comparison={comparison} color={comparison.color ?? comparisonColor(at)} index={index} />)}
    {!collapsed && document.indicators.filter(indicator => indicator.paneId === PRICE_PANE).map(row)}
    {document.indicators.some(indicator => indicator.paneId === PRICE_PANE) && <TxIconButton size="sm" className="chart-legend-toggle" label={collapsed ? t("Show indicator legend") : t("Hide indicator legend")}
      icon={collapsed ? "chevron-down" : "chevron"} onClick={() => setCollapsed(!collapsed)} />}
  </div></>;
}

/** TradingView's compare row: the symbol in its line's colour and its value under the pointer; show/hide and remove on hover. */
function ComparisonRow({ ctx, chartId, entry, comparison, color, index }: { ctx: ModuleContext; chartId: string; entry: ChartEntry; comparison: Comparison; color: string; index: number }) {
  const data = entry.feed.comparisons.get(comparison.id), value = data?.value[index];
  return <div className="chart-legend-row chart-legend-indicator chart-legend-fit" data-hidden={!comparison.visible}>
    <span className="chart-legend-head"><span className="chart-legend-name" style={{ color }}>{comparison.instrument.displaySymbol}</span></span>
    <span className="chart-legend-actions">
      <TxIconButton size="sm" icon={comparison.visible ? "eye" : "eye-off"} label={comparison.visible ? t("Hide") : t("Show")} onClick={() => void commit(ctx, chartId, [{ type: "comparison.update", id: comparison.id, visible: !comparison.visible }])} />
      <TxIconButton size="sm" icon="close" label={t("Remove")} onClick={() => void commit(ctx, chartId, [{ type: "comparison.delete", id: comparison.id }])} />
    </span>
    {comparison.visible && data && index >= 0 && <span className="chart-legend-values"><span style={{ color }}>{value === null || value === undefined ? "∅" : formatPrice(value, data.priceFormat.precision)}</span></span>}
  </div>;
}

function IndicatorRow({ ctx, panelId, chartId, entry, indicator, index, precision: pricePrecision, arguments: showArguments, values: showValues, definition, onSettings }: {
  ctx: ModuleContext; panelId: string; chartId: string; entry: ChartEntry; indicator: Indicator; index: number; precision: number; arguments: boolean; values: boolean;
  definition: CatalogEntry | undefined; onSettings(): void;
}) {
  const data = entry.feed.indicators.get(indicator.id), error = entry.indicatorErrors[indicator.id];
  const inputs = definition?.inputs.filter(input => !["source", "color", "bool", "time"].includes(input.type))
    .map(input => indicator.params[input.id] ?? input.default).join(" ");
  const plots = (data?.meta ?? []).filter(meta => meta.kind === "plot" && meta.display !== "none" && indicator.styles[meta.id]?.visible !== false);
  const precision = definition?.overlay ? pricePrecision : 2;
  const actions = <span className="chart-legend-actions">
    <TxIconButton size="sm" icon={indicator.visible ? "eye" : "eye-off"} label={indicator.visible ? t("Hide") : t("Show")} onClick={() => void commit(ctx, chartId, [{ type: "indicator.update", id: indicator.id, patch: { visible: !indicator.visible } }])} />
    <TxIconButton size="sm" icon="settings" label={t("Settings")} onClick={onSettings} />
    <TxIconButton size="sm" icon="code" label={t("Source code")} onClick={() => openSource(panelId, indicator.definition)} />
    <TxIconButton size="sm" icon="close" label={t("Remove")} onClick={() => void commit(ctx, chartId, [{ type: "indicator.delete", id: indicator.id }])} />
    <TxMenu label={t("More")} items={[
      { id: "duplicate", label: t("Duplicate"), icon: "copy", onSelect: () => void commit(ctx, chartId, [{ type: "indicator.duplicate", id: indicator.id }]) },
      { id: "up", label: t("Move up"), icon: "arrow-up", onSelect: () => void commit(ctx, chartId, [{ type: "indicator.reorder", id: indicator.id, direction: -1 }]) },
      { id: "down", label: t("Move down"), icon: "arrow-down", onSelect: () => void commit(ctx, chartId, [{ type: "indicator.reorder", id: indicator.id, direction: 1 }]) },
    ]} trigger={<TxIconButton size="sm" icon="more" label={t("More")} />} />
  </span>;
  // TradingView: the actions appear after the name on hover; the values follow and drop first when the chart is narrow.
  return <div className="chart-legend-row chart-legend-indicator chart-legend-fit" data-hidden={!indicator.visible}>
    <span className="chart-legend-head"><span className="chart-legend-name">{indicator.name ?? indicatorLabel(indicator.definition.source, definition?.shortTitle ?? indicator.definition.id)}{showArguments && inputs ? ` ${inputs}` : ""}</span></span>
    {actions}
    {error ? <span className="chart-legend-error" title={error.message}>{error.message}</span>
      : showValues && indicator.visible && index >= 0 && <span className="chart-legend-values">{plots.map(meta => {
        const value = data!.values[meta.id]?.[index];
        const color = indicator.styles[meta.id]?.color ?? pineColor(scriptColor(meta.dynamic ? data!.colors[meta.id]?.[index] : undefined, meta.color)) ?? undefined;
        return <span key={meta.id} style={{ color }}>{value === null || value === undefined ? "∅" : formatValue(value, meta.format, meta.precision ?? precision)}</span>;
      })}</span>}
  </div>;
}

