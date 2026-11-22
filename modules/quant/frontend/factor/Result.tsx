/* The selected run on a factor's Work: its key numbers, the groups chart, IC by holding days and the checks; or, in its place,
   the run in progress, a run that stopped, a sweep or a held-out validation. */
import { t } from "../i18n";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  TxButton, TxChecks, TxEmpty, TxFailure, TxHeatmap, TxIcon, TxKeyNumbers, TxLive, TxMenu, TxRegionHead, TxSegmented, TxSkeleton, TxTag, txDelta,
  type TxCheck, type TxKeyNumber, type TxMenuItem,
} from "@talyx/sdk";
import { usable, type FactorCheck, type FactorGo, type FactorNumbers, type FactorRun, type FetchRow, type HeldOutWork, type RunWork, type SweepCell, type SweepWork, type WorkView } from "./api";
import { universeText, type IndexMaterial, type ResearchListSummary } from "../objects";
import { elapsed, mdy, my, nf, pc, pct, pp, secondsText, sgn, tone, when } from "./format";
import { Plot, type PlotFigure } from "../chart/Plot";
import { DataNotes } from "../shared/DataNotes";

export const CHECK_NAMES: [string, string][] = [[t("Significance"), "≥ 3.00"], [t("Like the library"), "< 0.70"], [t("Group size"), "≥ 30"], [t("Every year"), "≥ 75%"], [t("Cost room"), t("≥ 2× cost")],
  [t("Group order"), t("in order")], [t("Future data"), "none"], [t("Coverage drop"), t("under 20%")], [t("Suspicious prices"), "none"]];

export interface ResultHandlers {
  select(runId: string): void;
  go(go: FactorGo): void;
  holding(day: number): void;
  date(ms: number): void;
  retry(run: FactorRun): void;
  line(file: string | null | undefined, line: number): void;
  useSettings(run: FactorRun, cell: number): void;
  create(run: FactorRun, kind: "model" | "strategy", cell?: number): void;
  compare(runId: string): void;
  /** The Publish sheet for this run. */
  publish(run: FactorRun): void;
  /** Open this run in a notebook, its first cell reading it. */
  notebook?(run: FactorRun): void;
}

/** "Every year": the count, after a strip of the whole years, each its rank IC's sign above and its long–short net of cost below. */
function EveryYear({value, years}: {value: string; years: NonNullable<FactorCheck["years"]>}) {
  return <span className="fx-yrs">
    <span className="fx-yrstrip">{years.map(year => <span key={year.year} title={t("{year}: rank IC {rankIc}, L–S net {value}", { year: year.year, rankIc: sgn(year.rankIc, 4), value: year.longShortNet == null ? "—" : pc(year.longShortNet, 1) })}>
      <i className={tone(year.rankIc)} /><i className={tone(year.longShortNet)} /></span>)}</span>{value}</span>;
}

/** The checks as the dense table: the five site-wide thresholds, then the factor's four. */
function checkGroups(checks: FactorCheck[] | null, go: (go: FactorGo) => void): {items: TxCheck[]}[] {
  const items: TxCheck[] = checks === null
    ? CHECK_NAMES.map(([name, need]) => ({name, need, state: "notrun" as const}))
    : checks.map(item => ({name: item.name, value: item.years?.length ? <EveryYear value={item.value} years={item.years} /> : item.value, need: item.need, state: item.state, title: item.title,
      onClick: item.go ? () => go(item.go!) : undefined}));
  return [{items: items.slice(0, 5)}, {items: items.slice(5)}];
}

/** The names a run is of, by the run's own data settings: the index or research list it was started with and how many names it had then (not what the factor's settings are now). */
export function runUniverse(run: FactorRun, lists: ResearchListSummary[] | null, indexes: IndexMaterial[] | null): string {
  const data = run.settings.data;
  if (data.list) {
    const name = lists?.find(item => item.id === data.list)?.name ?? t("Research list");
    const count = run.data?.requested ?? data.symbols?.length;
    return count == null ? name : `${name} (${count})`;
  }
  return universeText(data, lists, indexes) ?? t("Set data");
}
/** The line the held-out confirmation adds when this formula on this data was scored on this held-out period before, in any factor; nothing the first time. */
export const heldOutUsedLine = (uses: number | null | undefined): string | null => uses ? t("This held-out period was already used {count} times for this formula.", { count: uses }) : null;

export function keyNumbers(numbers: FactorNumbers | null, compared: FactorRun | null, run: FactorRun | null, go: (go: FactorGo) => void, comparison: Record<string, number> = {}): TxKeyNumber[] {
  const holding = run ? run.settings.run.holding : 5;
  if (numbers === null) return [t("Rank IC, {holding}D", { holding }), t("t, Newey–West"), t("Long–short, net"), t("Turnover"), t("Names a day"), t("Most like")].map(label => ({label, value: "—"}));
  const other = compared?.numbers ?? null;
  const vs = compared ? `#${compared.number}` : "";
  const delta = (key: string, text: (value: number) => string): ReactNode =>
    other && comparison[key] != null ? txDelta(text(comparison[key]), vs) : " ";
  const like = numbers.mostLike;
  // With too few names a day there is no daily rank to correlate: the numbers that come from it are not 0, they are not there.
  const few = numbers.days === 0;
  const likeName = like ? `${like.name} ${typeof like.version === "number" ? `v${like.version}` : like.version === "Style" ? "style" : like.version}` : "";
  return [
    {label: t("Rank IC, {holding}D", { holding: numbers.holding }), value: few ? "—" : sgn(numbers.rankIc, 4), tone: few ? undefined : numbers.rankIc != null && numbers.rankIc < 0 ? "down" : numbers.rankIc ? "up" : undefined,
      delta: delta("rankIc", value => sgn(value, 4)), title: t("Mean daily rank IC; click for the series"), onClick: () => go({to: "results", id: "ic"})},
    {label: t("t, Newey–West"), value: few ? "—" : nf(numbers.t, 2), delta: delta("t", value => sgn(value, 2)), title: t("Corrected for overlapping {holding}-day returns; click for the IC", { holding: numbers.holding }), onClick: () => go({to: "results", id: "ic"})},
    {label: t("Long–short, net"), value: pc(numbers.longShortNet, 2), unit: t("a year"), tone: numbers.longShortNet != null && numbers.longShortNet < 0 ? "down" : numbers.longShortNet ? "up" : undefined,
      delta: delta("longShortNet", value => pp(value)), title: t("After {costBps} bps a trade and borrow; click for the costs", { costBps: run?.settings.run.costBps ?? 5 }), onClick: () => go({to: "results", id: "costs"})},
    {label: t("Turnover"), value: pct(numbers.turnover, 0), unit: t("a rebalance"), delta: delta("turnover", value => pp(value, 0)), title: t("Share of each group replaced at a rebalance; click for the turnover"), onClick: () => go({to: "results", id: "turnover"})},
    {label: t("Names a day"), value: nf(numbers.names, 0), unit: numbers.members == null ? undefined : t("of {members} members", { members: nf(numbers.members, 0) }), delta: delta("names", value => sgn(value, 0)),
      title: numbers.members == null ? t("Names with a value on the median day; click for those without")
        : t("Names with a value on the median day, of the members the index held that day (those without a Market instrument or prices included); click for those without"),
      onClick: () => go({to: "tab", id: "values", order: "missing"})},
    {label: like ? t("Most like, {likeName}", { likeName }) : t("Most like"), value: like ? sgn(like.rho, 2) : "—",
      delta: " ",
      title: like ? t("Mean daily rank correlation with {name}, the closest in the library or among the price styles", { name: like.name }) : undefined, onClick: () => go({to: "tab", id: "similar"})},
  ];
}

/** The decay chart with the holding being edited (not run yet) marked. */
function decayFigure(figure: PlotFigure | undefined, holding: number, edited: number | null): PlotFigure | undefined {
  if (!figure || edited === null || edited === holding) return figure;
  const data = (figure.data ?? []).map(item => {
    const trace = item as {meta?: {key?: string}; y?: (number | null)[]};
    if (trace.meta?.key !== "decay") return item;
    return {...trace, y: (trace.y ?? []).map((value, index) => index + 1 === edited ? null : value)};
  });
  const base = (figure.data ?? []).find(item => (item as {meta?: {key?: string}}).meta?.key === "decay") as {x?: string[]; y?: (number | null)[]} | undefined;
  const y = (base?.y ?? []).map((value, index) => index + 1 === edited ? value : null);
  data.push({type: "bar", x: base?.x ?? [], y, name: t("Holding {holding}D, not run", { holding: edited }), showlegend: false, text: y.map(value => value === null ? "" : `${edited}D`), textposition: "outside", constraintext: "none", cliponaxis: false,
    hovertemplate: t("Holding %{x}<br>Rank IC %{y:.4f}<extra></extra>"), meta: {role: "attention", key: "decay:edited"}});
  return {...figure, data};
}

function groupsWithDate(figure: PlotFigure, date: number | null): PlotFigure {
  if (date === null) return figure;
  const day = new Date(date).toISOString().slice(0, 10);
  const layout = figure.layout ?? {};
  return {...figure, layout: {...layout, shapes: [...((layout.shapes as object[]) ?? []), {type: "line", xref: "x", yref: "paper", x0: day, x1: day, y0: 0, y1: 1,
    line: {dash: "dot", width: 1}, meta: {role: "attention"}}]}};
}

export function UseMenu({run, label, onCreate, cell, onNotebook}: {run: FactorRun; label: string; onCreate: ResultHandlers["create"]; cell?: number; onNotebook?: ResultHandlers["notebook"]}) {
  const items: TxMenuItem[] = [
    {id: "model", label: t("New model with {label}", { label }), icon: "flask", onSelect: () => onCreate(run, "model", cell)},
    {id: "strategy", label: t("New strategy with {label}", { label }), icon: "bolt", onSelect: () => onCreate(run, "strategy", cell)},
    ...(onNotebook ? [{id: "notebook", label: t("Open in a notebook"), icon: "book" as const, separated: true, onSelect: () => onNotebook(run)}] : []),
  ];
  return <TxMenu label={t("Use this run")} align="end" items={items} trigger={<TxButton size="sm">{t("Use this run")}<TxIcon name="chevron" className="fx-down" /></TxButton>} />;
}

/** Publishing a finished run into the library; the sheet says what stops it. */
export function PublishButton({run, onPublish}: {run: FactorRun; onPublish: ResultHandlers["publish"]}) {
  if (!usable(run)) return null;
  return <TxButton size="sm" onClick={() => onPublish(run)}><TxIcon name="external" />{t("Publish")}</TxButton>;
}

export function VsPicker({run, compared, runs, onCompare, missing = []}: {run: FactorRun; compared: FactorRun | null; runs: FactorRun[]; onCompare(runId: string): void; missing?: number[]}) {
  if (compared === null) return null;
  const items: TxMenuItem[] = runs.filter(item => usable(item) && item.id !== run.id).map(item => ({id: item.id, label: `#${item.number}`, detail: (item.formula ?? "").slice(0, 40), checked: item.id === compared.id, onSelect: () => onCompare(item.id)}));
  // The two runs' data end on different days: written once here, as the run strip writes a run's own end.
  const otherEnd = compared.data?.dataTo != null && run.data?.dataTo != null && compared.data.dataTo !== run.data.dataTo ? mdy(compared.data.dataTo).replace(/, \d{4}$/, "") : null;
  return <TxMenu label={t("Run to compare with")} items={items} trigger={<button type="button" className="tx-compare" title={t("Differences use dates available in both runs")}>{t.rich("vs {number}", { number: <b>#{compared.number}</b> })}
    <span className="fx-t3">{missing.length ? t("{missing} kept no daily numbers: run again to compare", { missing: missing.map(number => `#${number}`).join(t(" and ")) }) : t("common dates")}</span>{otherEnd && <span className="fx-t3">{t("data to {otherEnd}", { otherEnd })}</span>}<TxIcon name="chevron" /></button>} />;
}

/** The completed run: key numbers, groups and decay charts, checks. */
export function NormalBlock({run, numbers, checks, figures, notes, compared, runs, editedHolding, valuesDate, head, handlers, comparison, comparisonMissing}: {
  run: FactorRun; numbers: FactorNumbers; checks: FactorCheck[]; figures: RunWork["figures"]; notes: string[]; compared: FactorRun | null; runs: FactorRun[];
  editedHolding: number | null; valuesDate: number | null; head?: ReactNode; handlers: ResultHandlers; comparison?: Record<string, number>; comparisonMissing?: number[];
}) {
  const voided = checks.some(item => item.id === "future_data" && item.state === "watch");
  const groups = useMemo(() => groupsWithDate(figures.groups, valuesDate), [figures.groups, valuesDate]);
  const decay = useMemo(() => decayFigure(figures.decay, numbers.holding, editedHolding), [figures.decay, numbers.holding, editedHolding]);
  const comparedRun = compared && compared.id !== run.id && usable(compared) && !voided ? compared : null;
  return <>
    {head ?? <div className="fx-lhead"><b>#{run.number}</b>{run.version !== null && <TxTag tone="version">v{run.version}</TxTag>}<span>{when(run.createdAt)}</span>
      <span className="fx-t3">{secondsText(run.seconds)}</span>{run.resultsProgress != null && <span className="fx-livetag"><TxLive label={t("Results")} value={run.resultsProgress} /></span>}
      <VsPicker run={run} compared={comparedRun} runs={runs} onCompare={handlers.compare} missing={comparisonMissing} /><DataNotes notes={notes} />
      <span className="tx-spacer" /><UseMenu run={run} label={`#${run.number}`} onCreate={handlers.create} onNotebook={handlers.notebook} /><PublishButton run={run} onPublish={handlers.publish} /></div>}
    {numbers.days === 0 && <div className="fx-note" role="status"><span className="fx-dot" /><span>{t("Too few names a day for a cross-sectional test: {names}", { names: nf(numbers.names, 0) })}</span></div>}
    <div className="fx-stats" data-void={voided ? "" : undefined}><TxKeyNumbers columns={6} items={keyNumbers(numbers, comparedRun, run, handlers.go, comparison)} /></div>
    <div className="fx-frow">
      <div className="fx-fsec fx-gsec"><TxRegionHead title={t("Groups, cumulative")} sub={t("{holding}D holding · groups before costs, L–S net of {costBps} bps", { holding: numbers.holding, costBps: nf(run.settings.run.costBps, 0) })} />
        <div className="fx-ch"><Plot figure={groups} ariaLabel={t("Groups, cumulative")} onPoint={point => {if (typeof point.x === "string") handlers.date(Date.parse(point.x.slice(0, 10) + "T00:00:00Z"));}} /></div></div>
      <div className="fx-fsec fx-dsec"><TxRegionHead title={t("IC by holding days")} />
        <div className="fx-ch">{decay ? <Plot figure={decay} ariaLabel={t("IC by holding days")} onPoint={point => {const day = parseInt(String(point.x), 10); if (day) handlers.holding(day);}} /> : null}</div></div>
      <ChecksSection checks={checks} go={handlers.go} />
    </div>
  </>;
}

/** The height the checks need beside the charts: a box shorter than this (the divider dragged down) cannot show them all. */
export const CHECKS_NEED = 330;
/** Whether the checks fold to one line for this height of the box they would have as the result's right column. */
export const checksFold = (height: number) => height > 0 && height < CHECKS_NEED;

/** Wide and tall: the checks are the result's right column. A narrow panel (under 1000 px, in the styles) or a box under CHECKS_NEED high (the divider dragged down):
 *  one line under the charts that opens them all. The box is the row the charts share, which does not change when the checks fold. */
function ChecksSection({checks, go}: {checks: FactorCheck[] | null; go: (go: FactorGo) => void}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {if (open) box.current?.scrollIntoView({block: "nearest"});}, [open]);
  useEffect(() => {
    const row = box.current?.parentElement;
    if (!row || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => row.toggleAttribute("data-short", checksFold(row.clientHeight)));
    observer.observe(row);
    return () => observer.disconnect();
  }, []);
  const review = checks?.filter(item => item.state === "watch").length ?? 0;
  return <div ref={box} className="fx-fsec fx-csec" data-open={open ? "" : undefined}>
    <button type="button" className="fx-ckfold" aria-expanded={open} onClick={() => setOpen(!open)}>
      <b>{t("Checks")}</b>{checks === null ? <span className="fx-t3">{t("not run yet")}</span> : review ? <span className="fx-watch">{t("{review} to review", { review })}</span> : <span className="fx-t3">{t("all pass")}</span>}
      <span className="tx-spacer" /><span className="fx-t3">{open ? t("Hide") : t("Show all")}</span><TxIcon name="chevron" className="fx-ckchev" /></button>
    <TxChecks layout="table" groups={checkGroups(checks, go)} />
  </div>;
}

/** Before the first run: one sentence, no empty frame of numbers, charts and checks. Running stays in the top bar. */
export function NewBlock() {
  return <TxEmpty title={t("No runs yet")} />;
}

// ---------------------------------------------------------------- a run in progress, a run that stopped while fetching prices
const RESULT: Record<FetchRow["result"], [string, string]> = {fetched: [t("Fetched"), "fx-t2"], partial: [t("Fetched, partly"), "fx-t2"], none: [t("No prices"), "fx-watch"], failed: [t("Not fetched"), "fx-attn"], waiting: [t("Waiting"), "fx-t4"]};

export function FetchTable({rows, running, label}: {rows: FetchRow[]; running: boolean; label: string}) {
  const [filter, setFilter] = useState<"problems" | "fetched" | "waiting" | "all">("problems");
  const problems = rows.filter(row => ["none", "partial", "failed"].includes(row.result)), fetched = rows.filter(row => row.result === "fetched" || row.result === "partial"), waiting = rows.filter(row => row.result === "waiting");
  const list = {problems, fetched, waiting, all: rows}[filter];
  const options = [{value: "problems" as const, label: t("Problems {problemsCount}", { problemsCount: problems.length })}, {value: "fetched" as const, label: t("Fetched {fetchedCount}", { fetchedCount: fetched.length })},
    ...(running ? [{value: "waiting" as const, label: t("Waiting {waitingCount}", { waitingCount: waiting.length })}] : []), {value: "all" as const, label: t("All {rowsCount}", { rowsCount: rows.length })}];
  return <>
    <div className="fx-vbar"><b className="fx-sub2">{t("Prices by name")}</b><span className="fx-t3">{label}</span><span className="tx-spacer" />
      <span className="fx-small"><TxSegmented label={t("Show")} value={filter} options={options} onChange={setFilter} /></span></div>
    <div className="fx-pad"><table className="fx-g"><thead><tr><th className="l">{t("Name")}</th><th className="l">{t("Sector")}</th><th className="l">{t("Result")}</th><th className="l">{t("Reason")}</th><th>{t("Bars")}</th><th>{t("From")}</th></tr></thead>
      <tbody>{list.length === 0 && <tr><td className="l fx-t3" colSpan={6}>{filter === "problems" ? (running ? t("No problems so far") : t("No problems")) : t("None")}</td></tr>}
        {list.slice(0, 600).map(row => <tr key={row.instrumentId}><td className="l fx-tk">{row.ticker}</td><td className="l fx-t2">{row.sector ?? ""}</td>
        <td className={`l ${RESULT[row.result][1]}`}>{RESULT[row.result][0]}</td><td className="l fx-t2">{row.reason ?? ""}</td><td>{row.bars ? nf(row.bars, 0) : ""}</td><td className="fx-t2">{row.from ?? ""}</td></tr>)}</tbody></table></div>
  </>;
}

const STEP_LABELS: Record<string, string> = {code: t("Save the code"), data: t("Fetch prices"), facts: t("Fetch industry and shares"), environment: t("Start the research process"), compute: t("Compute values"), numbers: t("Groups, IC, checks"), future: t("Future data check")};
export const stepLabel = (name: string) => STEP_LABELS[name] ?? name;

function Stage({state, name, what, side}: {state: "on" | "done" | "fail" | "next"; name: string; what?: ReactNode; side?: ReactNode}) {
  return <div className="fx-stg" data-state={state}><span className="fx-si">{state === "on" ? <span className="tx-spin" /> : state === "done" ? <TxIcon name="check" /> : <i />}</span>
    <b>{name}</b><span className="fx-what">{what}</span><span className="tx-spacer" />{side}</div>;
}

/** The stages of a run that is going (or stopped while fetching): prices name by name, values, the numbers, then Results. */
function Stages({run, rows, failed}: {run: FactorRun; rows: FetchRow[]; failed: boolean}) {
  const steps = new Map((run.progress?.steps ?? []).map(step => [step.name, step]));
  const data = steps.get("data"), facts = steps.get("facts"), compute = steps.get("compute"), numbers = steps.get("numbers");
  const none = rows.filter(row => row.result === "none").length;
  const done = data?.done ?? rows.filter(row => row.result !== "waiting").length, total = data?.total ?? rows.length;
  const seconds = data?.seconds ?? (data?.startedAt ? (Date.now() - data.startedAt) / 1000 : 0);
  // The time left is the current count's own (one estimate over four counts went 0:43 -> 7:23 -> 0:09).
  const inPhase = data?.phaseStartedAt ? (Date.now() - data.phaseStartedAt) / 1000 : seconds;
  const left = data && data.state === "running" && done > 0 && total > done ? inPhase / done * (total - done) : null;
  const counted = {names: t("names checked"), prices: t("names fetched"), export: t("series exported"), checking: t("names' prices checked")}[data?.phase ?? "prices"];
  const state = (step: typeof data, fail = false): "on" | "done" | "fail" | "next" => !step ? "next" : step.state === "done" ? "done" : step.state === "failed" || fail ? "fail" : "on";
  return <div className="fx-stages">
    {run.progress?.queue ? <Stage state="on" name={t("Queued")} what={<span>{run.progress.queue === 1 ? t("next in Quant's queue") : t("{queue} in Quant's queue", { queue: run.progress.queue })}</span>} /> : null}
    <Stage state={failed ? "fail" : state(data)} name={t("Fetch prices")} what={<>{total ? (failed ? t.rich("{done} of {total} {counted}, stopped", { done: <span className="fx-num">{done}</span>, total, counted }) : t.rich("{done} of {total} {counted} through Market", { done: <span className="fx-num">{done}</span>, total, counted })) : failed ? t("stopped") : t("through Market")}
      {none > 0 && <><span className="fx-t4">·</span><span className="fx-watch">{t("{none} without prices", { none })}</span></>}{data?.detail && !failed && <span className="fx-t3">{data.detail}</span>}</>}
      side={!failed && data && data.state === "running" ? <><span className="fx-bar"><i style={{width: `${total ? Math.round(done / total * 100) : 0}%`}} /></span>
        <span className="fx-num fx-t3">{elapsed(seconds)}</span>{left !== null && <span className="fx-t3">{t("about {left} left", { left: elapsed(left) })}</span>}</> : data?.seconds ? <span className="fx-num fx-t3">{elapsed(data.seconds)}</span> : null} />
    {facts && <Stage state={state(facts)} name={t("Industry and shares")} what={facts.state === "running" ? <>{t.rich("{done} of {total}", { done: <span className="fx-num">{facts.done ?? 0}</span>, total: facts.total ?? total })}<span className="fx-t3">{t("from Market's research facts")}</span></> : null}
      side={facts.seconds ? <span className="fx-num fx-t3">{elapsed(facts.seconds)}</span> : facts.startedAt ? <span className="fx-num fx-t3">{elapsed((Date.now() - facts.startedAt) / 1000)}</span> : null} />}
    <Stage state={failed ? "next" : state(compute)} name={t("Compute values")} what={failed ? <span className="fx-t4">{t("not run")}</span> : null} side={compute?.seconds ? <span className="fx-num fx-t3">{secondsText(compute.seconds)}</span> : null} />
    <Stage state={failed ? "next" : state(numbers)} name={t("Groups, IC, checks")} what={failed ? <span className="fx-t4">{t("not run")}</span> : numbers?.total ? <span>{t.rich("{done} of {total}", { done: <span className="fx-num">{numbers.done ?? 0}</span>, total: numbers.total })}</span> : null} />
    <Stage state="next" name={t("Results")} />
  </div>;
}

function dataLabel(run: FactorRun, universe: string): string {  // `universe` is this run's own
  // Every run but held-out validation stops the day before the held-out period; validation scores only that period.
  const data = run.settings.data, held = data.heldOutFrom ? Date.parse(data.heldOutFrom + "T00:00:00Z") : null;
  const since = run.kind === "heldout" && held !== null ? held : data.since ? Date.parse(data.since) : null;
  const until = run.kind !== "heldout" && held !== null ? held - 86_400_000 : data.until ? Date.parse(data.until) : null;
  return `${universe}, ${since !== null ? my(since) : ""} – ${until !== null ? my(until) : ""}`;
}

export function RunningBlock({run, rows, universe: universeOf, last, handlers}: {run: FactorRun; rows: FetchRow[]; universe(run: FactorRun): string; last: FactorRun | null; handlers: ResultHandlers}) {
  const fetching = !run.progress?.steps.some(step => step.name === "compute");
  const universe = universeOf(run);
  return <>
    <div className="fx-lhead"><b>#{run.number}</b><span>{t("started {createdAt}", { createdAt: when(run.createdAt).split(", ")[1] })}</span><span className="fx-t3">{dataLabel(run, universe)}</span><span className="tx-spacer" />
      {last && <TxButton size="sm" variant="quiet" onClick={() => handlers.select(last.id)}>{t("Show #{number}", { number: last.number })}</TxButton>}</div>
    <Stages run={run} rows={rows} failed={false} />
    {(fetching || rows.some(row => row.result !== "fetched")) && rows.length > 0 && <FetchTable rows={rows} running label={t("{universe}, {rowsCount} names", { universe, rowsCount: rows.length })} />}
  </>;
}

export function FailedBlock({run, rows, universe: universeOf, last, handlers, below}: {run: FactorRun; rows: FetchRow[]; universe(run: FactorRun): string; last: FactorRun | null; handlers: ResultHandlers; below?: ReactNode}) {
  const failure = run.failure!;
  const universe = universeOf(run);
  const lead = <div className="fx-lhead"><b>#{run.number}</b><span>{when(run.createdAt)}</span>
    {run.endedAt && <span className="fx-t3">{t("stopped after {endedAt}", { endedAt: elapsed((run.endedAt - run.createdAt) / 1000) })}</span>}<span className="fx-t3">{dataLabel(run, universe)}</span>
    <span className="tx-spacer" />{last && <TxButton size="sm" variant="quiet" onClick={() => handlers.select(last.id)}>{t("Show #{number}", { number: last.number })}</TxButton>}</div>;
  if (failure.stage === "data") {
    const retry = failure.retry ?? rows.filter(row => row.result === "failed" || row.result === "waiting").length;
    return <>{lead}
      <TxFailure tone="watch" title={`#${run.number} ${failure.title}`} message={failure.message}
        actions={retry > 0 ? <TxButton size="sm" onClick={() => handlers.retry(run)}><TxIcon name="refresh" />{t("Retry {retry} names", { retry })}</TxButton> : undefined} />
      <Stages run={run} rows={rows} failed />
      {rows.length > 0 && <FetchTable rows={rows} running={false} label={t("{universe}, {rowsCount} names", { universe, rowsCount: rows.length })} />}
    </>;
  }
  if (failure.stage === "code" && failure.line) {
    const trace = (failure.traceback ?? "").split("\n").filter(Boolean).map(text => ({text, mine: Boolean(failure.file && text.includes(failure.file))}));
    return <>{lead}
      <TxFailure title={`#${run.number} ${failure.title}`} message={failure.message} code context={below ? t("Showing #{number}, the run before.", { number: String(last?.number) }) : t("before any values were computed")}
        location={{label: `${failure.file ?? "factor.py"}:${failure.line}`, onOpen: () => handlers.line(failure.file, failure.line!)}} trace={trace.length ? trace : undefined}
        actions={<TxButton size="sm" onClick={() => handlers.line(failure.file, failure.line!)}><TxIcon name="code" />{t("Go to line {line}", { line: failure.line })}</TxButton>} />
      {below}
    </>;
  }
  return <>{lead}<TxFailure title={`#${run.number} ${failure.title}`} message={failure.message} code={failure.stage === "code"} context={below ? t("Showing #{number}, the run before.", { number: String(last?.number) }) : undefined} />{below}</>;
}

export function StoppedBlock({run, last, handlers}: {run: FactorRun; last: FactorRun | null; handlers: ResultHandlers}) {
  return <>
    <div className="fx-lhead"><b>#{run.number}</b><span>{when(run.createdAt)}</span><TxTag tone="muted">{run.state === "interrupted" ? t("Interrupted") : t("Stopped")}</TxTag><span className="tx-spacer" />
      {last && <TxButton size="sm" variant="quiet" onClick={() => handlers.select(last.id)}>{t("Show #{number}", { number: last.number })}</TxButton>}</div>
    <TxFailure tone="watch" title={`#${run.number} ${run.state === "interrupted" ? t("was interrupted when Quant closed") : t("was stopped")}`} message={run.message ?? undefined} />
  </>;
}
// ---------------------------------------------------------------- sweep: two settings against one measure, the table, one combination below
const MEASURES = [{value: "net", label: t("L–S net")}, {value: "ic", label: t("Rank IC")}, {value: "t", label: "t"}, {value: "turnover", label: t("Turnover")}] as const;
type Measure = typeof MEASURES[number]["value"];
const measureOf = (cell: SweepCell, measure: Measure) => ({net: cell.numbers.longShortNet, ic: cell.numbers.rankIc, t: cell.numbers.t, turnover: cell.numbers.turnover}[measure]);
const AXIS: Record<string, (value: unknown) => string> = {holding: value => `${value}D`, groups: value => t("{count} groups", { count: String(value) }), costBps: value => t("{value} bps", { value: String(value) }), delay: value => t("delay {delay}", { delay: String(value) })};
const axisText = (name: string, value: unknown) => (AXIS[name] ?? (item => `${name} ${item}`))(value);
export const comboText = (cell: SweepCell, axes: {name: string}[]) => axes.map(axis => axisText(axis.name, cell.combo[axis.name])).join(" × ");

export function SweepBlock({run, work, cell, onCell, compared, runs, valuesDate, editedHolding, handlers}: {
  run: FactorRun; work: SweepWork; cell: number; onCell(index: number): void; compared: FactorRun | null; runs: FactorRun[]; valuesDate: number | null; editedHolding: number | null; handlers: ResultHandlers;
}) {
  const [measure, setMeasure] = useState<Measure>("net");
  const axes = work.axes, cells = work.cells;
  const chosen = cells[Math.min(cell, cells.length - 1)];
  const format = (value: number) => measure === "net" ? pc(value, 1) : measure === "ic" ? sgn(value, 4) : measure === "t" ? nf(value, 2) : pct(value, 0);
  const [rowsAxis, columnsAxis] = axes.length >= 2 ? [axes[0], axes[1]] : [axes[0], null];
  const index = (r: number, c: number) => cells.findIndex(item => item.combo[rowsAxis.name] === rowsAxis.values[r] && (columnsAxis === null || item.combo[columnsAxis.name] === columnsAxis.values[c]));
  const heat = rowsAxis && axes.length <= 2 ? rowsAxis.values.map((_, r) => (columnsAxis ? columnsAxis.values : [null]).map((__, c) => {
    const found = cells[index(r, c)];
    const value = found ? measureOf(found, measure) : null;
    return found && value != null ? {value, title: comboText(found, axes)} : null;
  })) : null;
  const best = cells.reduce((top, item, at) => (item.numbers.longShortNet ?? -Infinity) > (cells[top].numbers.longShortNet ?? -Infinity) ? at : top, 0);
  const position = (at: number): [number, number] | undefined => {
    const item = cells[at];
    if (!item || !rowsAxis) return undefined;
    return [rowsAxis.values.indexOf(item.combo[rowsAxis.name] as never), columnsAxis ? columnsAxis.values.indexOf(item.combo[columnsAxis.name] as never) : 0];
  };
  const sub = <div className="fx-lhead fx-sub"><b className="fx-combo">{comboText(chosen, axes)}</b><span className="fx-t3">{t("one of {cellsCount} in #{number}", { cellsCount: cells.length, number: run.number })}</span><span className="tx-spacer" />
    <TxButton size="sm" onClick={() => handlers.useSettings(run, cells.indexOf(chosen))}>{t("Use these settings")}</TxButton>
    <UseMenu run={run} label={`#${run.number} (${comboText(chosen, axes)})`} onCreate={handlers.create} cell={cells.indexOf(chosen)} onNotebook={handlers.notebook} /></div>;
  return <>
    <div className="fx-lhead"><b>#{run.number}</b><TxTag>{t("Sweep")}</TxTag><span>{when(run.createdAt)}</span><span className="fx-t3">{t("{cellsCount} runs, {seconds}", { cellsCount: cells.length, seconds: secondsText(run.seconds) })}</span>
      <span className="fx-t3">{axes.map(axis => `${axis.name === "costBps" ? "cost" : axis.name} ${axis.values.join(", ")}`).join(" × ")}</span><span className="tx-spacer" />
      <span className="fx-small"><TxSegmented label={t("Measure")} value={measure} options={[...MEASURES]} onChange={setMeasure} /></span></div>
    <div className="fx-swgrid">
      {heat && <div className="fx-swh"><TxHeatmap title="" x={columnsAxis ? columnsAxis.values.map(value => axisText(columnsAxis.name, value)) : [MEASURES.find(item => item.value === measure)!.label]}
        y={rowsAxis.values.map(value => axisText(rowsAxis.name, value))} cells={heat} format={format} selected={position(cells.indexOf(chosen))} best={position(best)}
        onCell={(r, c) => {const at = index(r, c); if (at >= 0) onCell(at);}} /></div>}
      <div className="fx-swt"><table className="fx-g"><thead><tr>{axes.map(axis => <th key={axis.name} className="l">{axis.name === "costBps" ? t("Cost") : axis.name[0].toUpperCase() + axis.name.slice(1)}</th>)}
        <th>{t("Rank IC")}</th><th>t</th><th>{t("L–S net")}</th><th>{t("Turnover")}</th><th>{t("Names a group")}</th><th className="l">{t("Checks")}</th></tr></thead>
        <tbody>{cells.map((item, at) => <tr key={at} data-selected={item === chosen ? "" : undefined} onClick={() => onCell(at)}>
          {axes.map(axis => <td key={axis.name} className="l">{axis.name === "groups" ? String(item.combo[axis.name]) : axisText(axis.name, item.combo[axis.name])}</td>)}
          <td className={tone(item.numbers.rankIc)}>{sgn(item.numbers.rankIc, 4)}</td><td className={item.numbers.t != null && item.numbers.t < 3 ? "fx-watch" : undefined}>{nf(item.numbers.t, 2)}</td>
          <td className={tone(item.numbers.longShortNet)}>{pc(item.numbers.longShortNet, 2)}</td><td>{pct(item.numbers.turnover, 0)}</td>
          <td className={item.numbers.groupSize < 30 ? "fx-watch" : "fx-t2"}>{item.numbers.groupSize}</td>
          <td className="l">{item.review ? <span className="fx-watch">{t("{review} to review", { review: item.review })}</span> : <span className="fx-t3">{t("all pass")}</span>}</td></tr>)}</tbody></table></div>
    </div>
    <div className="fx-swsel"><NormalBlock run={{...run, settings: {...run.settings, run: chosen.settings}}} numbers={chosen.numbers} checks={chosen.checks} figures={chosen.figures} notes={work.notes}
      compared={compared} runs={runs} editedHolding={editedHolding} valuesDate={valuesDate} head={sub} handlers={handlers} /></div>
  </>;
}

// ---------------------------------------------------------------- held-out validation
export function HeldOutBlock({run, work, source, handlers}: {run: FactorRun; work: HeldOutWork; source: FactorRun | null; handlers: ResultHandlers}) {
  const inside = source?.numbers ?? null, held = work.numbers;
  const row = (label: ReactNode, numbers: FactorNumbers | null, selected = false) => <tr data-selected={selected ? "" : undefined}><td className="l">{label}</td>
    <td className={tone(numbers?.rankIc)}>{sgn(numbers?.rankIc, 4)}</td><td>{nf(numbers?.t, 2)}</td><td className={tone(numbers?.longShortNet)}>{pc(numbers?.longShortNet, 2)}</td>
    <td>{nf(numbers?.sharpe, 2)}</td><td>{numbers?.maxDrawdown == null ? "" : pc(-Math.abs(numbers.maxDrawdown), 1)}</td><td>{nf(numbers?.names, 0)}</td></tr>;
  const sourceData = source?.settings.data;
  return <>
    <div className="fx-lhead"><b>#{run.number}</b><TxTag icon="lock">{t("Held-out")}</TxTag><span>{when(run.createdAt)}</span><span className="fx-t3">{secondsText(run.seconds)}</span>
      <span className="fx-t3">{t("#{ofNumber} scored once on {from} – {to}", { ofNumber: run.ofNumber ?? "", from: my(work.from), to: my(work.to) })}</span><span className="tx-spacer" />
      {source && <UseMenu run={source} label={`#${source.number}`} onCreate={handlers.create} onNotebook={handlers.notebook} />}</div>
    <div className="fx-pad fx-hot"><table className="fx-g"><thead><tr><th className="l" /><th>{t("Rank IC, {holding}D", { holding: held.holding })}</th><th>{t("t, Newey–West")}</th><th>{t("Long–short, net")}</th><th>{t("Sharpe, net")}</th><th>{t("Max drawdown")}</th><th>{t("Names a day")}</th></tr></thead>
      <tbody>{row(sourceData?.since ? t.rich("In-sample, {start} – {from} {run}", { start: my(Date.parse(sourceData.since)), from: my(work.from - 86_400_000), run: <span className="fx-t3">#{run.ofNumber}</span> }) : t.rich("In-sample {run}", { run: <span className="fx-t3">#{run.ofNumber}</span> }), inside)}
        {row(<>{t.rich("Held out, {from} – {to} {days}", { from: my(work.from), to: my(work.to), days: <span className="fx-t3">{t("{days} days", { days: nf(held.days, 0) })}</span> })}</>, held, true)}</tbody></table></div>
    <div className="fx-frow fx-two">
      <div className="fx-fsec fx-gsec"><TxRegionHead title={t("Long–short, net, cumulative")} sub={t("in-sample, then held out")} />
        <div className="fx-ch"><Plot figure={work.figures.heldout} ariaLabel={t("Long–short, net, cumulative")} /></div></div>
      <div className="fx-fsec"><TxRegionHead title={t("Held out by year")} />
        <table className="fx-g"><thead><tr><th className="l">{t("Year")}</th><th>{t("Rank IC")}</th><th>t</th><th>{t("L–S net")}</th></tr></thead>
          <tbody>{work.years.map(year => <tr key={year.year}><td className="l">{year.year}{year.partial && <span className="fx-t3"> {[year.from ? t("from {year}", { year: year.from }) : "", year.to ? t("to {year}", { year: year.to }) : ""].filter(Boolean).join(" ")}</span>}</td>
            <td className={tone(year.rankIc)}>{sgn(year.rankIc, 4)}</td><td className="fx-t2">{nf(year.t, 2)}</td><td className={tone(year.longShortNet)}>{pc(year.longShortNet, 1)}</td></tr>)}</tbody></table></div>
    </div>
  </>;
}

/** The selected run's place on Work, whatever its state. */
export function ResultArea({run, view, runs, rows, compared, universe, cell, onCell, editedHolding, valuesDate, handlers, fallback}: {
  run: FactorRun | null; view: WorkView | null; runs: FactorRun[]; rows: FetchRow[]; compared: FactorRun | null; universe(run: FactorRun): string;
  cell: number; onCell(index: number): void; editedHolding: number | null; valuesDate: number | null; handlers: ResultHandlers;
  /** The run before, with its Work: shown under a failure that is not about the data. */
  fallback: {run: FactorRun; work: RunWork} | null;
}) {
  if (run === null) return <NewBlock />;
  const last = runs.find(item => item.number < run.number && usable(item)) ?? null;
  if (["queued", "preparing", "running", "cancel_requested"].includes(run.state)) return <RunningBlock run={run} rows={rows} universe={universe} last={last} handlers={handlers} />;
  if (run.state === "failed" && run.failure) {
    const below = run.failure.stage !== "data" && fallback !== null && fallback.run.id !== run.id
      ? <NormalBlock run={fallback.run} numbers={fallback.work.numbers} checks={fallback.work.checks} figures={fallback.work.figures} notes={fallback.work.notes} compared={null} runs={runs}
        editedHolding={editedHolding} valuesDate={valuesDate} handlers={handlers} /> : undefined;
    return <FailedBlock run={run} rows={rows} universe={universe} last={last} handlers={handlers} below={below} />;
  }
  if (run.state !== "completed") return <StoppedBlock run={run} last={last} handlers={handlers} />;
  const work = view !== null && view.run.id === run.id ? view.work : null;
  if (work === null) return <div className="fx-lhead"><b>#{run.number}</b><TxSkeleton width="30%" /></div>;
  if ("kind" in work && work.kind === "sweep") return <SweepBlock run={run} work={work} cell={cell} onCell={onCell} compared={compared} runs={runs} valuesDate={valuesDate} editedHolding={editedHolding} handlers={handlers} />;
  if ("kind" in work && work.kind === "heldout") return <HeldOutBlock run={run} work={work} source={runs.find(item => item.id === run.of) ?? null} handlers={handlers} />;
  const normal = work as RunWork;
  return <NormalBlock run={run} numbers={normal.numbers} checks={normal.checks} figures={normal.figures} notes={normal.notes} comparison={normal.comparison} comparisonMissing={normal.comparisonMissing} compared={compared} runs={runs}
    editedHolding={editedHolding} valuesDate={valuesDate} handlers={handlers} />;
}
