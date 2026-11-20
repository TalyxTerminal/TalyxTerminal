/** The allocation's Work view: the run shown and its key numbers, this period beside every period, the allocation's own checks and the runs. A failed run shows its reason with the way to
    the setting; a stopped run the periods kept. Everything comes from the run record and its work.json. */
import { sweepSummary } from "./SweepGrid";
import { TxButton, TxChecks, TxFailure, TxIcon, TxKeyNumbers, TxLive, TxRegionHead, TxRunTable, TxStateWord, TxTag, txDelta, type TxCheck, type TxKeyNumber, type TxRunRow } from "@talyx/sdk";
import type { ReactNode } from "react";
import { when } from "../factor/format";
import { mdy, nf, pc, pct, pp, sgn } from "../shared/format";
import type { Parameter } from "../objects";
import type { AllocationRun, Change, Check, Metrics } from "./api";

/** A setting's value in a run's "what changed": a list (the pool's linked assets, the shocks, the limits by group) by how many it holds, as "shocks 3 → 4". */
const shown = (value: unknown): string => Array.isArray(value) ? (value.length ? String(value.length) : "none") : typeof value === "object" && value !== null ? "…" : value === "" || value === null || value === undefined ? "none" : String(value);
/** How a setting reads in a run's "what changed": its label, and its values in words and units, as the draft writes them ("lookback 252d → 126d"). */
export interface ChangeWriter { (change: Change): { label: string; from: string; to: string } | null }
const UNIT: Record<string, string> = { lookback: "d", max_weight: "%", min_weight: "%", threshold: "%", max_turnover: "%", max_tracking: "% a year", min_return: "% a year", risk_cap: "%", cost_multiple: "×", confidence: "%", net: "%", long_cap: "%", short_cap: "%" };
const LABEL: Record<string, string> = { group_limits: "group limits", frequency: "rebalance", cost_multiple: "costs", cost_aware: "cost-aware", objective_parameters: "objective options", covariance_parameters: "estimator options" };
export function changeWriter(declared: Parameter[] = []): ChangeWriter {
  const byName = new Map(declared.map(item => [item.name, item]));
  return change => {
    if (change.from === null || change.from === undefined) return null;   // a run made before the setting existed: nothing it changed
    const item = byName.get(change.name);
    const say = (value: unknown): string => {
      const option = item?.options?.find(entry => entry.value === value);
      if (option) return option.label.toLowerCase().replace(/^./, c => c.toLowerCase());
      if (typeof value === "boolean") return value ? "on" : "off";
      if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)) return mdy(new Date(value + "T00:00:00Z"));   // a day as the app writes days, not 2016-11-12
      const text = shown(value);
      return typeof value === "number" && UNIT[change.name] ? `${text}${UNIT[change.name]}` : text;
    };
    return { label: LABEL[change.name] ?? (item?.label ?? change.name.replace(/_/g, " ")).toLowerCase(), from: say(change.from), to: say(change.to) };
  };
}
const written = (run: AllocationRun, write: ChangeWriter) => run.changes.map(write).filter((item): item is NonNullable<ReturnType<ChangeWriter>> => item !== null);
/** What a run with nothing changed says: a failed one its failure (the first sentence), not "No changes" beside a run that did nothing. */
const unchanged = (run: AllocationRun) => run.state === "failed" && (run.failure?.message ?? run.message) ? (run.failure?.message ?? run.message ?? "").split(/(?<=\.)\s/)[0].replace(/\.$/, "")
  : run.number === 1 ? "First run" : "No changes";
export const changeText = (run: AllocationRun, write: ChangeWriter = changeWriter()): ReactNode => run.runKind === "heldout" ? <>held-out check, scored once</>
  : written(run, write).length === 0 ? <>{unchanged(run)}</> : written(run, write).map((change, index) => <span key={index}>{index ? ", " : ""}{change.label} <em>{change.from} → {change.to}</em></span>);
export const changePlain = (run: AllocationRun, write: ChangeWriter = changeWriter()) => run.runKind === "heldout" ? "held-out check" : written(run, write).length === 0 ? unchanged(run)
  : written(run, write).map(change => `${change.label} ${change.from} → ${change.to}`).join(", ");

/** The seven headline numbers of a whole backtest, with the change against the compared run. */
export function keyNumbers(metrics: Metrics | undefined, compared: AllocationRun | null, onOpen: (tab: string) => void, pending?: string): TxKeyNumber[] {
  const c = compared?.metrics, d = (text: string) => (c ? txDelta(text, `#${compared?.number}`) : undefined);
  const list: [string, (m: Metrics) => ReactNode, (m: Metrics, o: Metrics) => string, string?, string?, string?][] = [
    ["CAGR", x => pc(x.cagr, 2), (x, o) => pp(x.cagr - o.cagr, 2), "summary"], ["Volatility", x => pct(x.vol, 2), (x, o) => pp(x.vol - o.vol, 2), "risk"],
    ["Sharpe", x => x.sharpe == null ? "" : nf(x.sharpe, 2), (x, o) => x.sharpe == null || o.sharpe == null ? "" : sgn(x.sharpe - o.sharpe, 2), "summary", "Over the run's risk-free rate"], ["Max drawdown", x => pc(x.maxDrawdown, 1), (x, o) => pp(x.maxDrawdown - o.maxDrawdown, 1), "summary"],
    ["vs 60/40", x => x.vs6040 == null ? "—" : pp(x.vs6040, 2), (x, o) => pp((x.vs6040 ?? 0) - (o.vs6040 ?? 0), 2), "summary", "Return a year over 60/40, net of costs", "a year"],
    ["vs equal weight", x => x.vsEqualWeight == null ? "—" : pp(x.vsEqualWeight, 2), (x, o) => pp((x.vsEqualWeight ?? 0) - (o.vsEqualWeight ?? 0), 2), "summary", "Return a year over equal weight, net of costs", "a year"],
    ["Turnover", x => pct(x.turnover, 0), (x, o) => pp(x.turnover - o.turnover, 0), "weights", "One-way, a year"],
  ];
  return list.map(([label, value, delta, tab, title, unit]) => {
    if (!metrics) return { label, pending: pending ?? "" };
    // A comparison with no benchmark to compare with (no 60/40 in the pool) is a dash: no colour, no unit.
    const signed = label === "CAGR" ? metrics.cagr : label === "vs 60/40" ? metrics.vs6040 : label === "vs equal weight" ? metrics.vsEqualWeight : undefined;
    return { label, value: value(metrics), unit: signed == null && label.startsWith("vs") ? undefined : unit, title, onClick: tab ? () => onOpen(tab) : undefined,
      delta: c && (label !== "vs 60/40" || metrics.vs6040 != null && c.vs6040 != null) ? d(delta(metrics, c)) : undefined,
      tone: signed == null ? undefined : signed >= 0 ? "up" : "down" };
  });
}

const NOT_RUN: [string, string][] = [["beats_equal", "Beats equal weight, net"], ["binding", "Limits that bind"], ["effective", "Effective assets, largest"], ["solver", "Solver failed or fell back"], ["sensitivity", "Lookback or covariance, one step"]];
const WHERE: Record<string, string> = {beats_equal: "r:summary", binding: "set:limits", effective: "r:weights", solver: "r:weights", sensitivity: "set:risk", history: "set:pool"};
export function allocationChecks(checks: Check[], go: (where: string) => void): TxCheck[] {
  return checks.map(item => ({ name: item.name, value: item.value ?? "", need: item.need, state: item.state === "pass" ? "ok" : item.state === "fail" ? "watch" : "notrun", onClick: () => go(item.at?.fix ? `set:${item.at.fix}` : item.at ? `p:${item.at.date}:${item.at.asset ?? ""}` : WHERE[item.id] ?? "r:summary") }) as TxCheck);
}

// ---------------------------------------------------------------- the runs, newest first
export function RunsTable({ runs, selected, compared, onRow, onStar, write }: { runs: AllocationRun[]; selected: string | null; compared: string | null; onRow: (id: string) => void; onStar?: (run: AllocationRun) => void; write?: ChangeWriter }) {
  const metricCells = (x: Metrics) => ({ cagr: pc(x.cagr, 2), vol: pct(x.vol, 2), sharpe: x.sharpe == null ? "" : nf(x.sharpe, 2), mdd: pc(x.maxDrawdown, 1), ew: x.vsEqualWeight == null ? "" : pp(x.vsEqualWeight, 2), to: pct(x.turnover, 0) });
  // The cells of one sweep are one row: the best of them by Sharpe stands for it, and a click shows that cell.
  const sweeps = new Map<string, AllocationRun[]>();
  for (const run of runs) if (run.sweep) sweeps.set(run.sweep.id, [...(sweeps.get(run.sweep.id) ?? []), run]);
  const standing = (run: AllocationRun) => {
    if (!run.sweep) return run;
    const cells = sweeps.get(run.sweep.id)!;
    const best = cells.filter(cell => cell.metrics).sort((a, b) => (b.metrics!.sharpe ?? -Infinity) - (a.metrics!.sharpe ?? -Infinity))[0] ?? cells[0];
    return run.id === best.id ? { ...best, number: Math.min(...cells.map(cell => cell.number)) } : null;
  };
  const rows: TxRunRow[] = runs.map(standing).filter((run): run is AllocationRun => run !== null).map(run => {
    const sweep = run.sweep ? sweeps.get(run.sweep.id)! : null;
    const tags = <span className="al-tags">{sweep && <TxTag>Sweep</TxTag>}{run.runKind === "heldout" && <TxTag icon="lock">Held-out</TxTag>}{onStar && <button type="button" className="al-starbtn" aria-pressed={run.starred} title={run.starred ? "Kept" : "Keep"} onClick={event => {event.stopPropagation(); onStar(run);}}><TxIcon name="star" className={run.starred ? "al-star" : undefined} /></button>}</span>;
    const values = ["queued", "preparing", "running"].includes(run.state) && run.progress?.of ? { cagr: <TxLive label={`${run.progress.done} of ${run.progress.of} periods`} value={run.progress.done / run.progress.of} /> } : run.metrics ? metricCells(run.metrics) : {};
    return { id: run.id, selected: run.id === selected, compared: run.id === compared, checked: run.id === compared, cells: { n: sweep ? `Sweep #${run.number}` : `#${run.number}`, t: when(run.createdAt), chg: sweep ? (sweepSummary(runs, run) ?? `${sweep.length} cells`) : changeText(run, write), ...values, tags,
      // One word for the state, its reason on hover (the backend's generic failure title "stopped" read as a run stopped by hand, beside a second "Failed" tag).
      ck: run.state === "failed" ? <TxStateWord tone="fix" title={run.failure?.message ?? run.message ?? undefined}>Failed</TxStateWord> : run.state === "interrupted" || run.state === "cancelled" ? <TxStateWord tone="muted">Stopped</TxStateWord> : "" } };
  });
  return <TxRunTable columns={[{ key: "n", label: "Run", kind: "id" }, { key: "t", label: "Time", kind: "time" }, { key: "chg", label: "What changed", kind: "change" }, { key: "cagr", label: "CAGR" }, { key: "vol", label: "Volatility" },
    { key: "sharpe", label: "Sharpe", sortable: true }, { key: "mdd", label: "Max DD" }, { key: "ew", label: "vs EW" }, { key: "to", label: "Turnover" }, { key: "ck", label: "", kind: "text" }, { key: "tags", label: "", kind: "text" }]}
    rows={rows} onRow={onRow} />;
}

// ---------------------------------------------------------------- the Work view
/** Where a failed run stopped, by its failure's stage (it always said "Checked before the first period" whichever step broke). */
export function failedWhere(run: AllocationRun): string {
  const stage = run.failure?.stage;
  if (stage === "settings") return "Checked before the first period; nothing was computed.";
  if (stage === "data") return "Stopped while preparing the prices; nothing was computed.";
  const done = run.progress?.done ?? 0;
  return done > 0 ? `Stopped at period ${done + 1}${run.progress?.of ? ` of ${run.progress.of}` : ""}.` : "Stopped before the first period; nothing was computed.";
}

export interface WorkProps {
  run: AllocationRun | null; metrics: Metrics | undefined; checks: Check[] | undefined; runs: AllocationRun[]; compared: AllocationRun | null; review: ReactNode; period: ReactNode; notes: string[];
  onRow: (id: string) => void; onStar?: (run: AllocationRun) => void; onGo: (where: string) => void; write?: ChangeWriter; held?: ReactNode; lead?: ReactNode;
}
export function Work({ run, metrics, checks, runs, compared, review, period, notes, onRow, onStar, onGo, write, held, lead }: WorkProps) {
  const running = run !== null && ["queued", "preparing", "running", "cancel_requested"].includes(run.state);
  const failed = run?.state === "failed";
  const numbers = keyNumbers(running || failed || !run ? undefined : metrics, compared, tab => onGo(`r:${tab}`), failed ? "not run" : undefined);
  return <div className="qv-scroll al-work">
    {lead}
    {failed && run?.failure && <div className="qv-pad al-failure"><TxFailure title={run.failure.message.startsWith("Limits") ? "Limits can't add up to 100%" : run.failure.title === "stopped" ? run.failure.message : run.failure.title} message={run.failure.title === "stopped" && !run.failure.message.startsWith("Limits") ? "" : run.failure.message}
      context={failedWhere(run)} actions={run.failure.fields.length ? <TxButton size="sm" onClick={() => onGo(run.failure!.fields[0] === "pool" ? "set:pool" : "set:limits")}>Go to {run.failure.fields[0] === "pool" ? "the asset pool" : "Limits"}</TxButton> : undefined} /></div>}
    {held ?? <>
    {run && <TxKeyNumbers items={numbers} />}
    {notes.length > 0 && <div className="qx-notes" role="status">{notes.map((note, index) => <span key={index}>{note}</span>)}</div>}
    {review}
    {period}
    {run && <div className="al-checks"><TxChecks scope="in-sample" groups={[{ items: running || failed || !checks ? NOT_RUN.map(([id, name]) => ({ name, state: "notrun" as const, onClick: () => onGo(WHERE[id]) })) : allocationChecks(checks, onGo) }]} /></div>}</>}
    <TxRegionHead title="Runs" sub={`${runs.length}, in-sample figures`} />
    <div className="al-runs"><RunsTable runs={runs} selected={run?.id ?? null} compared={compared?.id ?? null} onRow={onRow} onStar={onStar} write={write} /></div>
  </div>;
}

/** The strip's actions: keep, the full report, publish. */
export function StripActions({ onResults, onStar, onPublish, onNotebook, starred, inResults }: { onResults: () => void; onStar: () => void; onPublish?: () => void; onNotebook?: () => void; starred?: boolean; inResults?: boolean }) {
  return <>{inResults && <TxButton variant="quiet" size="sm" aria-pressed={starred} onClick={onStar}><TxIcon name="star" />{starred ? "Kept" : "Keep"}</TxButton>}
    {onNotebook && <TxButton variant="quiet" size="sm" onClick={onNotebook}><TxIcon name="book" />Notebook</TxButton>}
    {!inResults && <TxButton variant="quiet" size="sm" onClick={onResults}>Full report<TxIcon name="chevron" /></TxButton>}
    {inResults && onPublish && <TxButton size="sm" onClick={onPublish}><TxIcon name="bookmark" />Publish</TxButton>}</>;
}
