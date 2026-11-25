import { t } from "../i18n";
/* Create or edit an alert (TradingView's alert dialog): Condition (the symbol's price, an indicator output on the
   chart, or a drawing; against a value or another source), Trigger, Expiration, Message and Notifications. A watchlist
   can stand in for the symbol: one alert watches every member. Delivery is the platform's notifications. */
import { useState } from "react";
import { TxButton, TxCheckbox, TxDialog, TxField, TxInput, TxSelect, TxTextarea, type ModuleContext } from "@talyx/sdk";
import { post } from "../api";
import { chartEntry, message, openOverlay, useStore } from "../store";
import { overlayTool } from "../drawings/registry";
import { timeframeLabel } from "../chart/timeframes";
import type { Alert, AlertSpec, Frequency } from "../wire";
import { ConditionFields, OPS, indicatorSources, type Sources } from "./conditions";

const FREQUENCIES: { value: Frequency; label: string }[] = [
  { value: "once", label: t("Only once") }, { value: "once_per_bar", label: t("Once per bar") },
  { value: "once_per_bar_close", label: t("Once per bar close") }, { value: "once_per_minute", label: t("Once per minute") },
];

/** Drawings an alert can watch: lines and channels. */
export const ALERT_TOOLS: string[] = ["trendline", "ray", "extended_line", "hline", "horizontal_ray", "parallel_channel"];

/** The fields of an alert that are its spec: an update takes these and nothing else. */
const specOf = ({ target, conditions, frequency, expiresAt, message, name, channels }: Alert): AlertSpec =>
  ({ target, conditions, frequency, expiresAt, message, name, channels });

/** The sources this chart offers, keyed for a select. */
function useSources(chartId: string): Sources {
  const entry = useStore(state => state.charts[chartId]);
  const catalog = useStore(state => state.catalog);
  const chart = entry?.chart;
  if (!chart) return new Map();
  const sources: Sources = new Map([["price", { label: chart.document.instrument.displaySymbol, operand: { kind: "price", field: "close" } }], ...indicatorSources(entry, catalog, true)]);
  for (const drawing of chart.document.drawings[chart.drawingGroup] ?? []) if (ALERT_TOOLS.includes(drawing.tool))
    sources.set(`drw:${drawing.id}`, { label: drawing.name ?? overlayTool(drawing.tool).label, operand: { kind: "drawing", chartId, drawingId: drawing.id } });
  return sources;
}

export function AlertDialog({ ctx, panelId, chartId, alertId, price, drawingId }: { ctx: ModuleContext; panelId: string; chartId: string; alertId?: string; price?: number; drawingId?: string }) {
  const chart = useStore(state => state.charts[chartId]?.chart);
  const existing = useStore(state => alertId ? state.alerts[alertId] : undefined);
  const defaults = useStore(state => state.alertDefaults);
  const watchlists = useStore(state => state.watchlists);
  const sources = useSources(chartId);
  const feed = chartEntry(chartId).feed, last = feed.bars.close[feed.bars.close.length - 1] ?? 0;
  const [spec, setSpec] = useState<AlertSpec | null>(() => existing ? specOf(existing) : (chart ? {
    target: { kind: "series", instrument: chart.document.instrument, series: chart.document.series, chartId },
    conditions: [{ left: drawingId ? { kind: "drawing", chartId, drawingId } : { kind: "price", field: "close" }, op: "crossing", right: drawingId ? { kind: "price", field: "close" } : { kind: "value", value: price ?? last } }],
    frequency: "once", expiresAt: null, message: "", name: null,
    channels: { toast: defaults?.toast ?? true, sound: defaults?.sound ?? true, mobile: false },
  } : null));
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const close = () => openOverlay(panelId, null);
  if (!spec || !chart) return null;
  const condition = spec.conditions[0];
  const shape = OPS.find(item => item.op === condition.op)!;
  const symbol = chart.document.instrument.displaySymbol, timeframe = timeframeLabel(chart.document.series.timeframe!);
  const autoMessage = `${symbol} ${shape.label}`;
  const save = () => {
    setBusy(true); setError(null);
    const body = { ...spec, message: spec.message || autoMessage };
    (existing ? post(ctx, "/alerts/update", { alertId: existing.id, revision: existing.revision, spec: body }) : post(ctx, "/alerts/create", { spec: body }))
      .then(close, failed => { setBusy(false); setError(message(failed)); });
  };
  const watchlistId = spec.target.kind === "watchlist" ? spec.target.watchlistId : "";
  const wall = (ms: number) => new Date(ms - new Date(ms).getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
  return <TxDialog open onOpenChange={open => { if (!open) close(); }} title={existing ? t("Edit alert on {symbol}", { symbol }) : t("Create alert on {symbol}", { symbol })}
    footer={<><span className="chart-alert-error" role={error ? "alert" : undefined}>{error}</span><TxButton size="sm" onClick={close}>{t("Cancel")}</TxButton>
      <TxButton size="sm" variant="primary" loading={busy} onClick={save}>{existing ? t("Save") : t("Create")}</TxButton></>}>
    <div className="chart-form chart-settings-form">
      {Object.keys(watchlists).length > 0 && <TxField label={t("Symbols")} layout="inline">
        <TxSelect aria-label={t("Symbols")} value={watchlistId} onChange={event => setSpec({ ...spec, target: event.target.value
          ? { kind: "watchlist", watchlistId: event.target.value, timeframe: chart.document.series.timeframe! }
          : { kind: "series", instrument: chart.document.instrument, series: chart.document.series, chartId } })}>
          <option value="">{symbol}</option>
          {Object.values(watchlists).map(list => <option key={list.id} value={list.id}>{t("Watchlist: {name}", { name: list.name })}</option>)}
        </TxSelect>
      </TxField>}
      <TxField label={t("Condition")} layout="inline">
        <ConditionFields condition={condition} sources={sources} last={last} onChange={next => setSpec({ ...spec, conditions: [next] })} />
      </TxField>
      <TxField label={t("Interval")} layout="inline"><span className="chart-muted">{t("Same as chart ({timeframe})", { timeframe })}</span></TxField>
      <TxField label={t("Trigger")} layout="inline">
        <TxSelect aria-label={t("Trigger")} value={spec.frequency} onChange={event => setSpec({ ...spec, frequency: event.target.value as Frequency })}>
          {FREQUENCIES.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
        </TxSelect>
      </TxField>
      <TxField label={t("Expiration")} layout="inline"><div className="chart-style-row">
        <TxCheckbox label={t("Open-ended")} checked={!spec.expiresAt} onCheckedChange={open => setSpec({ ...spec, expiresAt: open ? null : Date.now() + 30 * 86_400_000 })} />
        {spec.expiresAt && <TxInput type="datetime-local" aria-label={t("Expires")} value={wall(spec.expiresAt)} onChange={event => { if (event.target.value) setSpec({ ...spec, expiresAt: new Date(event.target.value).getTime() }); }} />}
      </div></TxField>
      <TxField label={t("Alert name")} layout="inline"><TxInput value={spec.name ?? ""} placeholder={autoMessage} onChange={event => setSpec({ ...spec, name: event.target.value || null })} /></TxField>
      <TxField label={t("Message")} layout="inline"><TxTextarea rows={3} value={spec.message} placeholder={autoMessage} onChange={event => setSpec({ ...spec, message: event.target.value })} /></TxField>
      <TxField label={t("Notifications")} layout="inline"><div className="chart-style-row">
        <TxCheckbox label={t("Show popup")} checked={spec.channels.toast} onCheckedChange={toast => setSpec({ ...spec, channels: { ...spec.channels, toast } })} />
        <TxCheckbox label={t("Play sound")} checked={spec.channels.sound} onCheckedChange={sound => setSpec({ ...spec, channels: { ...spec.channels, sound } })} />
        <TxCheckbox label={t("Send to phone")} checked={spec.channels.mobile} onCheckedChange={mobile => setSpec({ ...spec, channels: { ...spec.channels, mobile } })} />
      </div></TxField>
    </div>
  </TxDialog>;
}
