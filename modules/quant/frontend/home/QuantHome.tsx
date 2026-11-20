/** Quant home: what to handle first (each row ends in its one action), then what runs and waits beside New, then recently changed objects with
    their representative run's key number. Weakening and late items only get a link to the library; Home lists what needs a hand now. */
import { useEffect, useMemo, useState } from "react";
import { TxButton, TxIcon, TxLive, TxObjectTitle, TxSpacer, TxTag, useTxToast, type ModuleContext } from "@talyx/sdk";
import { at as at_, route } from "../types";
import { libraryApi } from "../library/api";
import { ago } from "../library/format";
import { type Preset, type Kind, type ObjectList, type ObjectRow, KINDS, useEvent, useThrottled } from "../objects";
import { ChecksCell, KeyNumberCell, RepCell, whenText } from "./cells";
import "./objects.css";
import "./home.css";

/** One thing to handle (quant_home.attention): why, since when, and the one action that deals with it. */
export interface AttentionItem {
  tone: "fix" | "watch"; label: string; state: string; count?: number; why: string; since: number;
  subject: {kind: "library" | "object"; id: string | null; name: string; version?: number; versionId?: string; itemKind?: string};
  action: {id: string; label: string; versionId?: string; itemId?: string; objectId?: string};
}
export interface QueueJob { subject: {kind: string; id: string | null; name: string; versionId?: string}; what: string; at?: number; state: "running" | "queued"; progress?: number | null; can_stop: boolean; elapsed?: number | string | null; left?: string }
/** How long a job has run, from when it started (the queue gives that moment): "12:40", or "1:02:05" past an hour. */
export function elapsedText(value: number | string | null | undefined, now = Date.now()): string {
  if (typeof value !== "number") return value ?? "";
  const seconds = Math.max(0, Math.round((now - value) / 1000)), two = (n: number) => String(n).padStart(2, "0");
  return seconds >= 3600 ? `${Math.floor(seconds / 3600)}:${two(Math.floor(seconds / 60) % 60)}:${two(seconds % 60)}` : `${Math.floor(seconds / 60)}:${two(seconds % 60)}`;
}

const kindLabel = (kind: string) => (KINDS as Record<string, {label: string}>)[kind]?.label ?? kind.charAt(0).toUpperCase() + kind.slice(1);

export function AttentionSection({items, watch, summary, onAction, onOpen, onLibrary}: {items: AttentionItem[]; watch: number; summary: {onTime: number};
  onAction(item: AttentionItem): void; onOpen(item: AttentionItem): void; onLibrary(): void}) {
  return <section className="qv-home-sec">
    <div className="qv-home-head"><h3>To handle</h3>{items.length > 0 && <b className="qv-home-count">{items.length}</b>}<span className="tx-spacer" />
      {watch > 0 && <button type="button" className="qv-home-link" onClick={onLibrary}>{`${watch} more to watch in the library`}</button>}</div>
    {items.length === 0 ? <div className="qv-home-calm">Nothing to handle{summary.onTime > 0 && <> <span className="qv-home-dim">·</span> <b>{summary.onTime}</b> {summary.onTime === 1 ? "library item" : "library items"} on time</>}</div>
      : <table className="qv-table qv-home-attn"><tbody>{items.map((item, index) => <tr key={index} onClick={() => onOpen(item)}>
        <td className="l"><span className="qv-home-name"><b title={item.subject.name}>{item.subject.name}</b>{item.subject.version ? <TxTag tone="version">{`v${item.subject.version}`}</TxTag> : null}<span className="qv-home-dim">{kindLabel(item.subject.itemKind ?? item.subject.kind)}</span></span></td>
        {/* The state as a word in its tone before why; when it only repeats the action at the row's end, the action alone says it. */}
        <td className="l qv-home-why" title={item.why}>{item.label !== item.action.label && <span className="qv-home-state" data-tone={item.tone}>{item.label + (item.count && item.count > 1 ? ` ×${item.count}` : "")}</span>}{item.why}</td><td className="qv-home-dim" title={new Date(item.since).toLocaleString()}>{ago(item.since)}</td>
        <td className="qv-home-act"><TxButton size="sm" onClick={event => {event.stopPropagation(); onAction(item);}}>{item.action.label}</TxButton></td></tr>)}</tbody></table>}
  </section>;
}

export function QueueSection({jobs, onStop}: {jobs: QueueJob[]; onStop(job: QueueJob): void}) {
  const running = jobs.filter(job => job.state === "running").length, queued = jobs.filter(job => job.state === "queued").length;
  return <section className="qv-home-sec"><div className="qv-home-head"><h3>Running and queued</h3><span className="qv-home-dim">{`${running} running, ${queued} queued`}</span></div>
    <table className="qv-table qv-home-jobs"><tbody>{jobs.map((job, index) => <tr key={index}>
      <td className="l"><span className="qv-home-name"><b>{job.subject.name}</b><span className="qv-home-dim">{kindLabel(job.subject.kind)}</span></span></td>
      <td className="l">{job.state === "running" && typeof job.progress === "number" ? <TxLive label={job.what} value={job.progress} /> : <span className="qv-home-dim">{job.what}</span>}</td>
      <td className="qv-home-dim num">{job.state === "running" ? elapsedText(job.elapsed) : ""}</td><td className="qv-home-dim">{job.left ?? ""}</td>
      <td className="qv-home-act">{job.can_stop ? <TxButton variant="quiet" size="sm" onClick={() => onStop(job)}>{job.state === "queued" ? "Cancel" : "Stop"}</TxButton> : null}</td></tr>)}</tbody></table></section>;
}

const BLANK: Partial<Record<Kind, string>> = {factor: "Blank formula", model: "Standard pipeline", allocation: "Standard pipeline", strategy: "Blank", notebook: "Blank"};
/** New: a column for each kind of object, in the order work flows (a factor feeds a model, a model an allocation or a strategy), each starting blank or
    from what is ready-made for it; what a ready-made one is shows on hover. */
/** A small drawing of what a kind of object gives, in the lines its own pages use: a factor's groups fanning out, a model's rolling windows (train,
    validation, test), an allocation's weights over time, a strategy's equity over its drawdown, a notebook's cells over a chart. */
export function KindArt({kind}: {kind: Kind}) {
  const wave = (seed: number, at: number) => Math.sin(at * .9 + seed * 1.7) * 2.2 + Math.sin(at * 2.3 + seed) * 1.1;
  const path = (points: [number, number][]) => points.map(([x, y], index) => `${index ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  const steps = Array.from({length: 25}, (_, index) => index);
  let body: React.ReactNode = null;
  if (kind === "factor") {
    // Five groups from one start: the lowest falls, the highest rises, the middle ones between (cold to warm, as the groups chart draws them).
    const colours = ["var(--tx-chart-order-1)", "var(--tx-chart-order-2)", "var(--tx-chart-order-3)", "var(--tx-chart-order-4)", "var(--tx-text)"];
    body = colours.map((colour, group) => <path key={group} d={path(steps.map(at => [20 + at * 8.3, 46 - (group - 2) * at * .62 + wave(group, at) * (at / 24)]))}
      stroke={colour} strokeWidth={group === 4 ? 1.6 : 1.2} fill="none" strokeLinecap="round" />);
  } else if (kind === "model") {
    // Rolling windows: each row trains on a span, validates on the next, tests on the one after; the next row starts later.
    body = [0, 1, 2, 3].map(row => { const x = 18 + row * 22, y = 19 + row * 15;
      return <g key={row}><rect x={x} y={y} width={92} height={8} rx={2} fill="var(--tx-white-08)" /><rect x={x + 94} y={y} width={22} height={8} rx={2} fill="var(--tx-white-20)" />
        <rect x={x + 118} y={y} width={22} height={8} rx={2} fill="var(--tx-text-2)" /></g>; });
  } else if (kind === "allocation") {
    // Three weights stacked to the whole, each period a little different.
    // Bands from edge to edge of the cover; only the lines between them are drawn.
    const flat = (y: number) => steps.map(at => [20 + at * 8.3, y] as [number, number]);
    const edge = (band: number) => steps.map(at => [20 + at * 8.3, 16 + band * 20 + wave(band + 3, at) * 1.6] as [number, number]);
    const fills = ["var(--tx-series-1)", "var(--tx-series-2)", "var(--tx-series-3)"];
    body = <>{fills.map((fill, band) => { const top = band === 0 ? flat(16) : edge(band), bottom = band === 2 ? flat(76) : edge(band + 1);
      return <path key={band} d={`${path(top)} ${path([...bottom].reverse()).replace(/^M/, "L")} Z`} fill={fill} fillOpacity={.26} />; })}
      {[1, 2].map(band => <path key={`e${band}`} d={path(edge(band))} stroke={fills[band]} strokeOpacity={.8} strokeWidth={1.2} fill="none" />)}</>;
  } else if (kind === "strategy") {
    // Equity rising with its setbacks; under it, how far each day stood below the last high.
    let high = -Infinity;
    const equity = steps.map(at => { const value = at * 1.5 + wave(7, at) * 2.4; return [20 + at * 8.3, 52 - value] as [number, number]; });
    const under = equity.map(([x, y]) => { high = Math.max(high, 52 - y); return [x, 62 + (high - (52 - y)) * 2.2] as [number, number]; });
    body = <><path d={`M20 62 ${path(under).replace(/^M/, "L")} L219 62 Z`} fill="var(--tx-down)" fillOpacity={.22} />
      <path d={path(equity)} stroke="var(--tx-text)" strokeWidth={1.6} fill="none" strokeLinejoin="round" strokeLinecap="round" /></>;
  } else if (kind === "notebook") {
    // Cells of code, and the chart one of them drew.
    body = <>{[[118, 0], [86, 1], [134, 2], [64, 3]].map(([width, row]) => <rect key={row} x={20} y={16 + row * 11} width={width} height={5} rx={2.5} fill={row === 1 ? "var(--tx-white-20)" : "var(--tx-white-10)"} />)}
      {[10, 18, 13, 24, 20, 30, 27].map((height, index) => <rect key={index} x={164 + index * 8} y={76 - height * 1.6} width={5} height={height * 1.6} rx={1.5} fill="var(--tx-text-3)" />)}</>;
  }
  return <svg className="qv-home-art" viewBox="0 0 240 90" aria-hidden="true" focusable="false">{body}</svg>;
}

export function NewSection({kinds, presets, counts = {}, onCreate}: {kinds: Kind[]; presets: Preset[]; counts?: Partial<Record<Kind, number>>; onCreate(kind: Kind, preset?: string): void}) {
  const order = (["factor", "model", "allocation", "strategy", "notebook"] as Kind[]).filter(kind => kinds.includes(kind));
  return <section className="qv-home-sec qv-home-new"><div className="qv-home-head"><h3>New</h3></div>
    <div className="qv-home-kinds" style={{["--qv-kinds" as string]: order.length}}>{order.map(kind => {
      const own = presets.filter(preset => preset.kind === kind);
      // Each kind a cover with a drawing of what it gives; the cover starts a blank one, as the first row under it does.
      return <div key={kind} className="qv-home-kind">
        <div className="qv-home-cover"><KindArt kind={kind} /><TxButton variant="row" aria-label={`New ${KINDS[kind].label.toLowerCase()}`} onClick={() => onCreate(kind)} /></div>
        <h4><span>{KINDS[kind].label}</span>{counts[kind] ? <span className="qv-home-dim">{counts[kind]}</span> : null}</h4>
        <TxButton variant="row" size="sm" onClick={() => onCreate(kind)}><TxIcon name="plus" />{BLANK[kind]}</TxButton>
        {own.length > 0 && <ul aria-label={`Ready-made ${KINDS[kind].plural.toLowerCase()}`}>{own.map(preset => <li key={preset.id}>
          <TxButton variant="row" size="sm" title={preset.description} onClick={() => onCreate(kind, preset.id)}><span className="qv-home-dim">{preset.label}</span></TxButton></li>)}</ul>}
      </div>;
    })}</div></section>;
}

export function RecentSection({items, total, onOpen, onAll}: {items: ObjectRow[]; total: number; onOpen(row: ObjectRow): void; onAll(): void}) {
  return <section className="qv-home-sec"><div className="qv-home-head"><h3>Recently changed</h3><span className="tx-spacer" /><button type="button" className="qv-home-link" onClick={onAll}>{total === 1 ? "All objects" : `All ${total} objects`}</button></div>
    <table className="qv-table qv-home-recent"><thead><tr><th className="l">Name</th><th className="l">Kind</th><th className="l" title="Published run; else the starred one; else the latest successful">Representative run</th><th className="l">Key number</th><th title="Checks passed">Checks</th><th>Changed</th></tr></thead>
      <tbody>{items.map(row => <tr key={row.id} onClick={() => onOpen(row)}><td className="l"><span className="qv-home-name"><b>{row.name}</b>{["factor", "model", "allocation"].includes(row.kind) && !row.library && <TxTag tone="muted">candidate</TxTag>}</span></td>
        <td className="l qv-home-dim">{KINDS[row.kind].label}</td><td className="l"><RepCell o={row} /></td><td className="l"><KeyNumberCell o={row} /></td><td><ChecksCell o={row} /></td><td className="qv-home-dim">{whenText(row.changedAt)}</td></tr>)}</tbody></table></section>;
}

/* ---------------------------------------------------------------- the page: what the backend says about now */

export function QuantHome({ctx, list, presets, onOpen, onCreate, onOpenObjects, onOpenLibrary, onOpenVersion}: {ctx: ModuleContext; list: ObjectList | null; presets: Preset[];
  onOpen(id: string): void; onCreate(kind: Kind, preset?: string): void; onOpenObjects(): void; onOpenLibrary(itemId?: string, versionId?: string): void; onOpenVersion(versionId: string): void}) {
  const toast = useTxToast();
  const api = useMemo(() => libraryApi(ctx), [ctx]);
  const [attention, setAttention] = useState<{items: AttentionItem[]; watchInLibrary: number} | null>(null);
  const [queue, setQueue] = useState<{jobs: QueueJob[]} | null>(null);
  const [onTime, setOnTime] = useState(0);
  const [tick, setTick] = useState(0);
  const load = () => {
    void route<{items: AttentionItem[]; watchInLibrary: number}>(ctx, "GET", "/quant/attention").then(setAttention, () => undefined);
    void route<{jobs: QueueJob[]}>(ctx, "GET", "/quant/queue").then(setQueue, () => undefined);
    void api.list().then(value => setOnTime(value.items.filter(item => item.state === "ok").length), () => undefined);
  };
  useEffect(load, [tick]);
  useEffect(() => {const timer = setInterval(() => setTick(value => value + 1), 6000); return () => clearInterval(timer);}, []);
  useEvent(ctx, "quant.library_changed", () => setTick(value => value + 1));
  // Runs report several times a second while they go: home reads again once a second at most.
  const runsChanged = useThrottled(() => setTick(value => value + 1));
  useEvent(ctx, "quant.object_run_changed", runsChanged);
  const said = (action: Promise<unknown>, title: string) => action.then(() => {toast({title}); setTick(value => value + 1);}, reason => toast({title: reason instanceof Error ? reason.message : String(reason), tone: "failed"}));
  const open = (item: AttentionItem) => {
    if (item.subject.kind === "library") onOpenLibrary(item.subject.id ?? undefined, item.subject.versionId); else if (item.subject.id) onOpen(item.subject.id);
  };
  const act = (item: AttentionItem) => {
    const subject = item.subject, action = item.action;
    if ((action.id === "retry" || action.id === "check_source") && subject.versionId) void said(api.retry(subject.versionId), `Retrying ${subject.name} v${subject.version ?? ""} now`);
    else if (action.id === "open_input" && subject.versionId) onOpenVersion(subject.versionId);
    else if (action.id === "review_period" && action.objectId) onOpen(action.objectId);  // the review is in the allocation's Work
    else if (action.id === "republish" && action.objectId) onOpen(action.objectId);  // a failure a retry cannot mend: run it there and publish again
    else if (action.id === "switch_version" && action.versionId && subject.id) void said(route(ctx, "POST", at_`/objects/${subject.id}/library-version`, {versionId: action.versionId}), `${subject.name} now pins the newer version; run it again to see the difference`);
    else if (subject.versionId) onOpenVersion(subject.versionId); else open(item);
  };
  const stop = (job: QueueJob) => {
    if (job.subject.kind === "library" && job.subject.versionId) void said(api.cancel(job.subject.versionId), `Cancelled the update of ${job.subject.name}`);
    else if (job.subject.id) void said(route(ctx, "POST", at_`/object-runs/${job.subject.id}/cancel`), `Stopped ${job.subject.name}`);
  };
  const all = (list?.items ?? []) as ObjectRow[];
  const recent = useMemo(() => all.filter(row => row.inThisWorkspace !== false).sort((a, b) => (b.changedAt ?? b.updatedAt) - (a.changedAt ?? a.updatedAt)).slice(0, 10), [list]);
  const kinds = (list?.kinds ?? []).filter(item => item.creatable).map(item => item.kind);
  const counts = Object.fromEntries((list?.kinds ?? []).map(item => [item.kind, item.count])) as Partial<Record<Kind, number>>;
  return <div className="qv-home-page">
    <div className="qx-bar"><TxButton variant="quiet" size="sm" onClick={onOpenObjects}><TxIcon name="list" />All objects</TxButton><TxObjectTitle name="Quant" kind="Home" /><TxSpacer />
      <TxButton variant="quiet" size="sm" onClick={() => onOpenLibrary()}><TxIcon name="bookmark" />Library</TxButton></div>
    <div className="qv-scroll qv-home">
      <AttentionSection items={attention?.items ?? []} watch={attention?.watchInLibrary ?? 0} summary={{onTime}} onAction={act} onOpen={open} onLibrary={() => onOpenLibrary()} />
      {(queue?.jobs ?? []).length > 0 && <QueueSection jobs={queue!.jobs} onStop={stop} />}
      <NewSection kinds={kinds} presets={presets} counts={counts} onCreate={onCreate} />
      {recent.length > 0 && <RecentSection items={recent} total={all.filter(row => row.inThisWorkspace !== false).length} onOpen={row => onOpen(row.id)} onAll={onOpenObjects} />}
    </div>
  </div>;
}
