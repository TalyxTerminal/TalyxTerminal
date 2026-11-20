/* The alerts panel (TradingView): Alerts lists every alert of the workspace with its state, and on hover pause or
   resume, edit and delete; Log lists what fired, newest first. */
import { useEffect, useState } from "react";
import { TxEmpty, TxIconButton, TxTabs, TxTag, type ModuleContext } from "@talyx/sdk";
import { post } from "../api";
import { message, openOverlay, useStore } from "../store";
import type { Alert, Fired } from "../wire";
import { timeframeLabel } from "../chart/timeframes";

const STATES: Record<Alert["state"], string> = { active: "Active", paused: "Paused", triggered: "Triggered", expired: "Expired" };

export function AlertsPanel({ ctx, panelId }: { ctx: ModuleContext; panelId: string }) {
  const [tab, setTab] = useState<"alerts" | "log">("alerts");
  return <div className="chart-rail-panel">
    <div className="chart-rail-head"><TxTabs value={tab} onChange={setTab} label="Alerts" options={[{ value: "alerts", label: "Alerts" }, { value: "log", label: "Log" }]} /></div>
    {tab === "alerts" ? <AlertList ctx={ctx} panelId={panelId} /> : <AlertLog ctx={ctx} />}
  </div>;
}

function AlertList({ ctx, panelId }: { ctx: ModuleContext; panelId: string }) {
  const alerts = useStore(state => state.alerts);
  const watchlists = useStore(state => state.watchlists);
  const active = useStore(state => state.layouts[panelId]?.active);
  const [error, setError] = useState<string | null>(null);
  const list = Object.values(alerts).sort((a, b) => b.createdAt - a.createdAt);
  const fail = (failed: unknown) => setError(message(failed));
  if (!list.length) return <TxEmpty title="No alerts" />;
  return <>
    {error && <p className="chart-alert-error chart-rail-note" role="alert">{error}</p>}
    <div className="chart-rail-list" role="list">
      {list.map(alert => {
        const target = alert.target.kind === "series"
          ? `${alert.target.instrument.displaySymbol}, ${timeframeLabel(alert.target.series.timeframe!)}`
          : `${watchlists[alert.target.watchlistId]?.name ?? "Watchlist"}, ${timeframeLabel(alert.target.timeframe)}`;
        const running = alert.state === "active";
        // A source that failed pauses the alert with its reason; Retry starts it again.
        const failed = alert.state === "paused" && alert.reason;
        return <div key={alert.id} className="chart-alert-item" role="listitem">
          <div className="chart-alert-text">
            <span className="chart-alert-title">{target}</span>
            <span className="chart-muted">{alert.name || alert.message}</span>
            {failed && <span className="chart-alert-error">{alert.reason!.message}</span>}
            {running && alert.source && <span className="chart-muted">{alert.source.message}</span>}
          </div>
          <span className="chart-alert-tags">
            {alert.outdated && <TxTag tone="watch">Uses an older version</TxTag>}
            <TxTag tone={running ? undefined : alert.state === "triggered" ? "watch" : "muted"}>{STATES[alert.state]}</TxTag>
          </span>
          <span className="chart-alert-actions">
            {(alert.state === "active" || alert.state === "paused") && <TxIconButton size="sm" icon={running ? "pause" : failed ? "refresh" : "play"} label={running ? "Pause" : failed ? "Retry" : "Resume"}
              onClick={() => post(ctx, "/alerts/state", { alertId: alert.id, revision: alert.revision, state: running ? "paused" : "active" }).catch(fail)} />}
            <TxIconButton size="sm" icon="edit" label="Edit" onClick={() => openOverlay(panelId, { kind: "alert", chartId: active!, alertId: alert.id })} />
            <TxIconButton size="sm" icon="trash" label="Delete" onClick={() => post(ctx, "/alerts/delete", { alertId: alert.id }).catch(fail)} />
          </span>
        </div>;
      })}
    </div>
  </>;
}

/** The log the backend keeps, then what fires while the panel is open (the workspace stream). */
function AlertLog({ ctx }: { ctx: ModuleContext }) {
  const live = useStore(state => state.fired);
  const [earlier, setEarlier] = useState<Fired[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { post(ctx, "/alerts/log", { limit: 200 }).then(page => setEarlier(page.events), failed => setError(message(failed))); }, [ctx]);
  if (error) return <p className="chart-alert-error chart-rail-note" role="alert">{error}</p>;
  const seen = new Set(live.map(event => event.id));
  const events = [...live, ...(earlier ?? []).filter(event => !seen.has(event.id))].sort((a, b) => b.at - a.at);
  if (!earlier) return <p className="chart-muted chart-rail-note">Loading…</p>;
  if (!events.length) return <TxEmpty title="Nothing fired yet" />;
  return <div className="chart-rail-list" role="list">
    {events.map(event => <div key={event.id} className="chart-alert-item" role="listitem">
      <div className="chart-alert-text">
        <span className="chart-alert-title">{event.symbol}</span>
        <span className="chart-muted">{event.message}</span>
      </div>
      <time className="chart-muted chart-alert-time" dateTime={new Date(event.at).toISOString()}>{new Date(event.at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</time>
    </div>)}
  </div>;
}
