/** The allocation's Work view: the run shown and its key numbers, this period beside every period, the allocation's own checks and the runs. A failed run shows its reason with the way to
    the setting; a stopped run the periods kept. Everything comes from the run record and its work.json. */
import { t } from "../i18n";
import { sweepSummary } from "./SweepGrid";
import { TxButton, TxChecks, TxFailure, TxIcon, TxKeyNumbers, TxLive, TxRegionHead, TxRunTable, TxStateWord, TxTag, txDelta, type TxCheck, type TxKeyNumber, type TxRunRow } from "@talyx/sdk";
import type { ReactNode } from "react";
import { when } from "../factor/format";
import { mdy, nf, pc, pct, pp, sgn } from "../shared/format";
import type { Parameter } from "../objects";
import type { AllocationRun, Change, Check, Metrics } from "./api";

/** A setting's value in a run's "what changed": a list (the pool's linked assets, the shocks, the limits by group) by how many it holds, as "shocks 3 → 4". */
const shown = (value: unknown): string => Array.isArray(value) ? (value.length ? String(value.length) : t("none")) : typeof value === "object" && value !== null ? "…" : value === "" || value === null || value === undefined ? t("none") : String(value);
/** How a setting reads in a run's "what changed": its label, and its values in words and units, as the draft writes them ("lookback 252d → 126d"). */
export interface ChangeWriter { (change: Change): { label: string; from: string; to: string } | null }
const UNIT: Record<string, string> = { lookback: "d", max_weight: "%", min_weight: "%", threshold: "%", max_turnover: "%", max_tracking: t("% a year"), min_return: t("% a year"), risk_cap: "%", cost_multiple: "×", confidence: "%", net: "%", long_cap: "%", short_cap: "%" };
const LABEL: Record<string, string> = { group_limits: t("group limits"), frequency: "rebalance", cost_multiple: "costs", cost_aware: "cost-aware", objective_parameters: t("objective options"), covariance_parameters: t("estimator options") };
export function changeWriter(declared: Parameter[] = []): ChangeWriter {
  const byName = new Map(declared.map(item => [item.name, item]));
  return change => {
    if (change.from === null || change.from === undefined) return null;   // a run made before the setting existed: nothing it changed
    const item = byName.get(change.name);
    const say = (value: unknown): string => {
      const option = item?.options?.find(entry => entry.value === value);
      if (option) return option.label.toLowerCase().replace(/^./, c => c.toLowerCase());
      if (typeof value === "boolean") return value ? t("on") : t("off");
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
  : run.number === 1 ? t("First run") : t("No changes");
export const changeText = (run: AllocationRun, write: ChangeWriter = changeWriter()): ReactNode => run.runKind === "heldout" ? <>{t("held-out check, scored once")}</>
  : written(run, write).length === 0 ? <>{unchanged(run)}</> : written(run, write).map((change, index) => <span key={index}>{index ? ", " : ""}{change.label} <em>{change.from} → {change.to}</em></span>);
export const changePlain = (run: AllocationRun, write: ChangeWriter = changeWriter()) => run.runKind === "heldout" ? t("held-out check") : written(run, write).length === 0 ? unchanged(run)
  : written(run, write).map(change => `${change.label} ${change.from} → ${change.to}`).join(", ");

/** The seven headline numbers of a whole backtest, with the change against the compared run. */
export function keyNumbers(metrics: Metrics | undefined, compared: AllocationRun | null, onOpen: (tab: string) => void, pending?: string): TxKeyNumber[] {
  const c = compared?.metrics, d = (text: string) => (c ? txDelta(text, `#${compared?.number}`) : undefined);
  const list: [string, (m: Metrics) => ReactNode, (m: Metrics, o: Metrics) => string, string?, string?, string?][] = [
    ["CAGR", x => pc(x.cagr, 2), (x, o) => pp(x.cagr - o.cagr, 2), "summary"], [t("Volatility"), x => pct(x.vol, 2), (x, o) => pp(x.vol - o.vol, 2), "risk"],
    [t("Sharpe"), x => x.sharpe == null ? "" : nf(x.sharpe, 2), (x, o) => x.sharpe == null || o.sharpe == null ? "" : sgn(x.sharpe - o.sharpe, 2), "summary", t("Over the run's risk-free rate")], [t("Max drawdown"), x => pc(x.maxDrawdown, 1), (x, o) => pp(x.maxDrawdown - o.maxDrawdown, 1), "summary"],
    [t("vs 60/40"), x => x.vs6040 == null ? "—" : pp(x.vs6040, 2), (x, o) => pp((x.vs6040 ?? 0) - (o.vs6040 ?? 0), 2), "summary", t("Return a year over 60/40, net of costs"), t("a year")],
    [t("vs equal weight"), x => x.vsEqualWeight == null ? "—" : pp(x.vsEqualWeight, 2), (x, o) => pp((x.vsEqualWeight ?? 0) - (o.vsEqualWeight ?? 0), 2), "summary", t("Return a year over equal weight, net of costs"), t("a year")],
    [t("Turnover"), x => pct(x.turnover, 0), (x, o) => pp(x.turnover - o.turnover, 0), "weights", t("One-way, a year")],
  ];
  return list.map(([label, value, delta, tab, title, unit]) => {
    if (!metrics) return { label, pending: pending ?? "" };
    // A comparison with no benchmark to compare with (no 60/40 in the pool) is a dash: no colour, no unit.
    const signed = label === "CAGR" ? metrics.cagr : label === t("vs 60/40") ? metrics.vs6040 : label === t("vs equal weight") ? metrics.vsEqualWeight : undefined;
    return { label, value: value(metrics), unit: signed == null && label.startsWith("vs") ? undefined : unit, title, onClick: tab ? () => onOpen(tab) : undefined,
      delta: c && (label !== t("vs 60/40") || metrics.vs6040 != null && c.vs6040 != null) ? d(delta(metrics, c)) : undefined,
      tone: signed == null ? undefined : signed >= 0 ? "up" : "down" };
  });
}

const NOT_RUN: [string, string][] = [["beats_equal", t("Beats equal weight, net")], ["binding", t("Limits that bind")], ["effective", t("Effective assets, largest")], ["solver", t("Solver failed or fell back")], ["sensitivity", t("Lookback or covariance, one step")]];
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
    const tags = <span className="al-tags">{sweep && <TxTag>{t("Sweep")}</TxTag>}{run.runKind === "heldout" && <TxTag icon="lock">{t("Held-out")}</TxTag>}{onStar && <button type="button" className="al-starbtn" aria-pressed={run.starred} title={run.starred ? t("Kept") : t("Keep")} onClick={event => {event.stopPropagation(); onStar(run);}}><TxIcon name="star" className={run.starred ? "al-star" : undefined} /></button>}</span>;
    const values = ["queued", "preparing", "running"].includes(run.state) && run.progress?.of ? { cagr: <TxLive label={t("{done} of {of} periods", { done: run.progress.done, of: run.progress.of })} value={run.progress.done / run.progress.of} /> } : run.metrics ? metricCells(run.metrics) : {};
    return { id: run.id, selected: run.id === selected, compared: run.id === compared, checked: run.id === compared, cells: { n: sweep ? t("Sweep #{number}", { number: run.number }) : `#${run.number}`, t: when(run.createdAt), chg: sweep ? (sweepSummary(runs, run) ?? t("{count} cells", { count: sweep.length })) : changeText(run, write), ...values, tags,
      // One word for the state, its reason on hover (the backend's generic failure title "stopped" read as a run stopped by hand, beside a second "Failed" tag).
      ck: run.state === "failed" ? <TxStateWord tone="fix" title={run.failure?.message ?? run.message ?? undefined}>{t("Failed")}</TxStateWord> : run.state === "interrupted" || run.state === "cancelled" ? <TxStateWord tone="muted">{t("Stopped")}</TxStateWord> : "" } };
  });
  return <TxRunTable columns={[{ key: "n", label: t.c("run record", "Run"), kind: "id" }, { key: "t", label: t("Time"), kind: "time" }, { key: "chg", label: t("What changed"), kind: "change" }, { key: "cagr", label: "CAGR" }, { key: "vol", label: t("Volatility") },
    { key: "sharpe", label: t("Sharpe"), sortable: true }, { key: "mdd", label: t("Max DD") }, { key: "ew", label: t("vs EW") }, { key: "to", label: t("Turnover") }, { key: "ck", label: "", kind: "text" }, { key: "tags", label: "", kind: "text" }]}
    rows={rows} onRow={onRow} />;
}

// ---------------------------------------------------------------- the Work view
/** Where a failed run stopped, by its failure's stage (it always said "Checked before the first period" whichever step broke). */
export function failedWhere(run: AllocationRun): string {
  const stage = run.failure?.stage;
  if (stage === "settings") return t("Checked before the first period; nothing was computed.");
  if (stage === "data") return t("Stopped while preparing the prices; nothing was computed.");
  const done = run.progress?.done ?? 0;
  return done > 0 ? (run.progress?.of ? t("Stopped at period {period} of {total}.", { period: done + 1, total: run.progress.of }) : t("Stopped at period {period}.", { period: done + 1 })) : t("Stopped before the first period; nothing was computed.");
}

export interface WorkProps {
  run: AllocationRun | null; metrics: Metrics | undefined; checks: Check[] | undefined; runs: AllocationRun[]; compared: AllocationRun | null; review: ReactNode; period: ReactNode; notes: string[];
  onRow: (id: string) => void; onStar?: (run: AllocationRun) => void; onGo: (where: string) => void; write?: ChangeWriter; held?: ReactNode; lead?: ReactNode;
}
export function Work({ run, metrics, checks, runs, compared, review, period, notes, onRow, onStar, onGo, write, held, lead }: WorkProps) {
  const running = run !== null && ["queued", "preparing", "running", "cancel_requested"].includes(run.state);
  const failed = run?.state === "failed";
  const numbers = keyNumbers(running || failed || !run ? undefined : metrics, compared, tab => onGo(`r:${tab}`), failed ? t("not run") : undefined);
  return <div className="qv-scroll al-work">
    {lead}
    {failed && run?.failure && <div className="qv-pad al-failure"><TxFailure title={run.failure.message.startsWith("Limits") ? t("Limits can't add up to 100%") : run.failure.title === "stopped" ? run.failure.message : run.failure.title} message={run.failure.title === "stopped" && !run.failure.message.startsWith("Limits") ? "" : run.failure.message}
      context={failedWhere(run)} actions={run.failure.fields.length ? <TxButton size="sm" onClick={() => onGo(run.failure!.fields[0] === "pool" ? "set:pool" : "set:limits")}>{(run.failure.fields[0] === "pool" ? t("Go to the asset pool") : t("Go to Limits"))}</TxButton> : undefined} /></div>}
    {held ?? <>
    {run && <TxKeyNumbers items={numbers} />}
    {notes.length > 0 && <div className="qx-notes" role="status">{notes.map((note, index) => <span key={index}>{note}</span>)}</div>}
    {review}
    {period}
    {run && <div className="al-checks"><TxChecks scope={t("in-sample")} groups={[{ items: running || failed || !checks ? NOT_RUN.map(([id, name]) => ({ name, state: "notrun" as const, onClick: () => onGo(WHERE[id]) })) : allocationChecks(checks, onGo) }]} /></div>}</>}
    <TxRegionHead title={t("Runs")} sub={t("{runsCount}, in-sample figures", { runsCount: runs.length })} />
    <div className="al-runs"><RunsTable runs={runs} selected={run?.id ?? null} compared={compared?.id ?? null} onRow={onRow} onStar={onStar} write={write} /></div>
  </div>;
}

/** The strip's actions: keep, the full report, publish. */
export function StripActions({ onResults, onStar, onPublish, onNotebook, starred, inResults }: { onResults: () => void; onStar: () => void; onPublish?: () => void; onNotebook?: () => void; starred?: boolean; inResults?: boolean }) {
  return <>{inResults && <TxButton variant="quiet" size="sm" aria-pressed={starred} onClick={onStar}><TxIcon name="star" />{starred ? t("Kept") : t("Keep")}</TxButton>}
    {onNotebook && <TxButton variant="quiet" size="sm" onClick={onNotebook}><TxIcon name="book" />{t("Notebook")}</TxButton>}
    {!inResults && <TxButton variant="quiet" size="sm" onClick={onResults}>{t("Full report")}<TxIcon name="chevron" /></TxButton>}
    {inResults && onPublish && <TxButton size="sm" onClick={onPublish}><TxIcon name="bookmark" />{t("Publish")}</TxButton>}</>;
}
