/* Object tree and data window (TradingView keeps them in one panel, two tabs), for the active chart.
   Object tree: each pane's series, indicators and drawings, with show/hide, lock and remove. Data window: the values of
   the bar under the pointer (the newest bar otherwise) for the symbol and every indicator. */
import { useState } from "react";
import { TxIconButton, TxTabs, type ModuleContext } from "@talyx/sdk";
import { commit, shownZone, useStore, type ChartEntry } from "../store";
import { overlayTool } from "../drawings/registry";
import { formatCompact, formatPrice, formatValue, shownFormat } from "../engine/precision";
import { PRICE_PANE } from "../wire";
import { isDailyOrLonger, timeframeLabel } from "../chart/timeframes";
import { clockText, dateText } from "../engine/timeZone";
import { useFeedVersion } from "../chart/feedVersion";
import { matchesLayer } from "../engine/researchLayer";

export function ObjectsPanel({ ctx, panelId }: { ctx: ModuleContext; panelId: string }) {
  const [tab, setTab] = useState<"tree" | "data">("tree");
  const chartId = useStore(state => state.layouts[panelId]?.active);
  const entry = useStore(state => chartId ? state.charts[chartId] : undefined);
  return <div className="chart-rail-panel">
    <div className="chart-rail-head"><TxTabs value={tab} onChange={setTab} label="Objects" options={[{ value: "tree", label: "Object tree" }, { value: "data", label: "Data window" }]} /></div>
    {entry?.chart && (tab === "tree" ? <ObjectTree ctx={ctx} chartId={chartId!} entry={entry} /> : <DataWindow entry={entry} />)}
  </div>;
}

function ObjectTree({ ctx, chartId, entry }: { ctx: ModuleContext; chartId: string; entry: ChartEntry }) {
  const catalog = useStore(state => state.catalog);
  const { document, drawingGroup } = entry.chart!;
  const drawings = document.drawings[drawingGroup] ?? [];
  const run = (command: Parameters<typeof commit>[2][number]) => void commit(ctx, chartId, [command]);
  return <div className="chart-rail-list" role="tree" aria-label="Object tree">
    {document.panes.map((pane, index) => {
      const indicators = document.indicators.filter(item => item.paneId === pane.id);
      const paneDrawings = drawings.filter(item => item.paneId === pane.id);
      if (pane.id !== PRICE_PANE && !indicators.length && !paneDrawings.length) return null;
      return <div key={pane.id} role="group" aria-label={`Pane ${index + 1}`}>
        <div className="chart-tree-pane">{pane.id === PRICE_PANE ? "Main pane" : `Pane ${index + 1}`}</div>
        {pane.id === PRICE_PANE && <>
          <div className="chart-tree-row" role="treeitem"><span>{document.instrument.displaySymbol}, {timeframeLabel(document.series.timeframe!)}</span></div>
          {document.layers.map(layer => <div key={layer.id} className="chart-tree-row" role="treeitem" data-hidden={!layer.visible || undefined}>
            <span>{layer.label}</span>
            {!matchesLayer(layer, document.instrument.instrumentId, document.series.adjustment) &&
              <span className="chart-tree-layer-owner">{layer.symbol} · {{raw: "unadjusted", split: "split-adjusted", split_dividend: "split and dividend-adjusted"}[layer.adjustment]}</span>}
            <span className="chart-tree-actions">
              <TxIconButton size="sm" icon={layer.visible ? "eye" : "eye-off"} label={layer.visible ? "Hide" : "Show"} onClick={() => run({ type: "layer.visible", id: layer.id, visible: !layer.visible })} />
              <TxIconButton size="sm" icon="trash" label="Remove" onClick={() => run({ type: "layer.delete", id: layer.id })} />
            </span>
          </div>)}
          {document.comparisons.map(item => <div key={item.id} className="chart-tree-row" role="treeitem">
            <span>{item.instrument.displaySymbol}</span>
            <span className="chart-tree-actions">
              <TxIconButton size="sm" icon={item.visible ? "eye" : "eye-off"} label={item.visible ? "Hide" : "Show"} onClick={() => run({ type: "comparison.update", id: item.id, visible: !item.visible })} />
              <TxIconButton size="sm" icon="trash" label="Remove" onClick={() => run({ type: "comparison.delete", id: item.id })} />
            </span>
          </div>)}
        </>}
        {indicators.map(item => <div key={item.id} className="chart-tree-row" role="treeitem" data-hidden={!item.visible || undefined}>
          <span>{item.name ?? catalog?.find(definition => definition.source === item.definition.source && definition.id === item.definition.id)?.shortTitle ?? item.definition.id}</span>
          <span className="chart-tree-actions">
            <TxIconButton size="sm" icon={item.visible ? "eye" : "eye-off"} label={item.visible ? "Hide" : "Show"} onClick={() => run({ type: "indicator.update", id: item.id, patch: { visible: !item.visible } })} />
            <TxIconButton size="sm" icon="trash" label="Remove" onClick={() => run({ type: "indicator.delete", id: item.id })} />
          </span>
        </div>)}
        {paneDrawings.map(item => <div key={item.id} className="chart-tree-row" role="treeitem" data-hidden={!item.visible || undefined}>
          <span>{item.name || overlayTool(item.tool).label}</span>
          <span className="chart-tree-actions">
            <TxIconButton size="sm" icon={item.visible ? "eye" : "eye-off"} label={item.visible ? "Hide" : "Show"} onClick={() => run({ type: "drawing.update", id: item.id, patch: { visible: !item.visible } })} />
            <TxIconButton size="sm" icon="lock" label={item.locked ? "Unlock" : "Lock"} aria-pressed={item.locked} onClick={() => run({ type: "drawing.update", id: item.id, patch: { locked: !item.locked } })} />
            <TxIconButton size="sm" icon="trash" label="Remove" onClick={() => run({ type: "drawing.delete", id: item.id })} />
          </span>
        </div>)}
      </div>;
    })}
  </div>;
}

/** Values follow the pointer: the store's hover index, and the feed read in place. */
function DataWindow({ entry }: { entry: ChartEntry }) {
  const catalog = useStore(state => state.catalog);
  useFeedVersion(entry);
  const { document } = entry.chart!, feed = entry.feed, bars = feed.bars, count = bars.time.length;
  const index = entry.hover !== null && entry.hover < count ? entry.hover : count - 1;
  if (index < 0) return null;
  const precision = shownFormat(feed.priceFormat, document.display.precision).precision;
  const previous = index > 0 ? bars.close[index - 1] : null, change = previous === null ? null : bars.close[index] - previous;
  // In the chart's own time zone; daily and longer bars have a date only.
  const zone = shownZone(entry);
  const time = bars.time[index], daily = isDailyOrLonger(document.series.timeframe!);
  const rows: [string, string][] = [
    ["Date", dateText(zone, time)], ...(daily ? [] : [["Time", clockText(zone, time)] as [string, string]]),
    ["Open", formatPrice(bars.open[index], precision)], ["High", formatPrice(bars.high[index], precision)],
    ["Low", formatPrice(bars.low[index], precision)], ["Close", formatPrice(bars.close[index], precision)],
    ["Change", change === null ? "—" : `${change >= 0 ? "+" : ""}${formatPrice(change, precision)} (${(change / previous! * 100).toFixed(2)}%)`],
    ["Volume", bars.volume[index] === null ? "—" : formatCompact(bars.volume[index]!)],
  ];
  return <div className="chart-rail-list chart-data-window">
    <div className="chart-tree-pane">{document.instrument.displaySymbol}, {timeframeLabel(document.series.timeframe!)}</div>
    {rows.map(([label, value]) => <div key={label} className="chart-data-row"><span>{label}</span><span>{value}</span></div>)}
    {document.indicators.map(indicator => {
      const data = feed.indicators.get(indicator.id);
      const definition = catalog?.find(item => item.source === indicator.definition.source && item.id === indicator.definition.id);
      const plots = (data?.meta ?? []).filter(meta => meta.kind === "plot" && meta.display !== "none");
      return <div key={indicator.id}>
        <div className="chart-tree-pane">{indicator.name ?? definition?.shortTitle ?? indicator.definition.id}</div>
        {plots.map(meta => { const value = data!.values[meta.id]?.[index];
          return <div key={meta.id} className="chart-data-row"><span>{meta.title ?? meta.id}</span><span>{value === null || value === undefined ? "∅" : formatValue(value, meta.format, meta.precision ?? (definition?.overlay ? precision : 2))}</span></div>; })}
      </div>;
    })}
  </div>;
}
