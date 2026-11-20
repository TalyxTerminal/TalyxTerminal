/* A strategy's Work: one row of key numbers (each with its change against the run it is compared with), the main picture with the checks folded into its head, and what the run says
   when it did nothing, stopped or failed. The tables under it belong to the page. */
import { useState, type ReactNode } from "react";
import { TxChecks, TxIcon, TxKeyNumbers, TxPopover, TxRunTable, TxStateWord, TxTag, txDelta, type TxCheck, type TxKeyNumber, type TxRunColumn, type TxRunRow } from "@talyx/sdk";
import { dayText, mdy, nf, pc, pct, pp, secondsText, sgn, tone, when } from "../factor/format";
import { buttonFor } from "./validation/ChecksColumn";
import type { CheckGo } from "./validation/types";
import type { Check, Stats, StrategyRun, StrategyRunRow, StrategyWork } from "./api";
import type { Parameter } from "../objects";
import { settingName } from "./names";
import { Plot } from "../chart/Plot";

const sign = (value: number, digits: number) => (value > 0 ? "+" : value < 0 ? "−" : "") + nf(Math.abs(value), digits);

/** The six numbers, in the draft's words; a number the run cannot give reads "—", and a short sample says so beside the annual return. */
const sub = (a: number | null, b: number | null) => a === null || b === null ? null : a - b;
export function keyNumbers(work: StrategyWork, go: (section: string) => void): TxKeyNumber[] {
  const held = work.heldOut?.heldOut ?? null, before = work.heldOut?.inSample ?? null;
  // A held-out run is read on its held-out part: the same code and values, scored from the held-out start, against the part before it.
  const stats: Stats = held ? {...work.stats, cagr: held.cagr, maxDrawdown: held.maxDrawdown, sharpe: held.sharpe, overBenchmark: held.overBenchmark, costsAYear: held.costsAYear, trades: held.trades} : work.stats;
  const other = held && before ? {label: `#${work.heldOut!.of}`, whatDiffers: [] as string[], delta: {cagr: sub(held.cagr, before.cagr), maxDrawdown: sub(held.maxDrawdown, before.maxDrawdown), sharpe: sub(held.sharpe, before.sharpe),
    overBenchmark: sub(held.overBenchmark, before.overBenchmark), costsAYear: sub(held.costsAYear, before.costsAYear), trades: sub(held.trades, before.trades)}} as unknown as typeof work.compared : work.compared;
  const delta = (key: string, text: (value: number) => string): ReactNode => {
    const value = other?.delta?.[key];
    // No difference says nothing: six cells of "0.0 pt vs #1" only hide the one that moved.
    if (other && typeof value === "number" && Math.abs(value) < 5e-7) return undefined;
    return other && value !== null && value !== undefined ? txDelta(text(value), other.label) : other ? <em>{other.whatDiffers.length ? other.label : ""}</em> : undefined;
  };
  const tag = work.shortSample ? <TxTag tone="muted">short sample</TxTag> : null;
  const [from, to] = work.span ?? [undefined, undefined];
  const bench = benchNames(work.benchmarkLabel).short;
  const year = `annualized over ${work.annualization} ${work.annualization === 365 ? "days" : "trading days"}`;
  const dash = <span aria-label="not available">—</span>;
  return [
    {label: held ? "CAGR, held out" : "CAGR", value: stats.cagr === null ? dash : pc(stats.cagr, 1), tone: stats.cagr === null ? undefined : stats.cagr >= 0 ? "up" : "down", delta: <>{delta("cagr", value => sgn(value * 100, 1) + " pt")}{tag}</>,
      title: `Total ${pc(stats.totalReturn, 1)}${from && to ? `, ${mdy(from)} – ${mdy(to)}` : ""}`},
    {label: "Max drawdown", value: stats.maxDrawdown === null ? dash : pc(stats.maxDrawdown, 1), delta: delta("maxDrawdown", value => sgn(value * 100, 1) + " pt"), onClick: () => go("equity")},
    {label: "Sharpe", value: stats.sharpe === null ? dash : nf(stats.sharpe, 2), title: year, delta: delta("sharpe", value => sign(value, 2)), onClick: () => go("rolling")},
    {label: `Over ${bench}`, value: stats.overBenchmark === null ? dash : pc(stats.overBenchmark, 1), tone: stats.overBenchmark === null ? undefined : stats.overBenchmark >= 0 ? "up" : "down",
      title: `A year: CAGR minus ${bench} CAGR`, delta: delta("overBenchmark", value => sgn(value * 100, 1) + " pt")},
    {label: "Costs, a year", value: stats.costsAYear === null ? dash : costsText(stats.costsAYear), delta: <>{delta("costsAYear", value => pp(-value, 1))}</>,
      title: costsTitle(work), onClick: () => go("costs")},
    // Orders that were refused (not enough margin, no price) are counted beside the trades; the count opens the orders.
    {label: "Trades", value: String(stats.trades), delta: (moved => <>{moved}{stats.rejected ? <em>{moved ? " · " : ""}{stats.rejected} rejected</em> : null}</>)(delta("trades", value => sign(value, 0))),
      onClick: stats.rejected ? () => go("orders") : undefined},
  ];
}

/** A benchmark's names: short in a key or column head ("BTC hold"), long in a subtitle ("BTC buy and hold"). The run's label is "BTC/USDT:USDT hold"
 *  for holding one instrument, otherwise the benchmark's own name. */
/** Costs a year as a loss: −0.8%; a small one keeps two places (−0.04%), and one that rounds to nothing has no sign (it read −0.0%). */
export function costsText(value: number): string {
  const digits = value !== 0 && Math.abs(value) < 0.001 ? 2 : 1;
  const shown = pct(value, digits);
  return (/[1-9]/.test(shown) ? "−" : "") + shown;
}

export function benchNames(label: string | null | undefined): {short: string; long: string} {
  const text = label ?? "";
  const held = /^(.+?) hold$/.exec(text);
  if (!held) return {short: text || "Benchmark", long: text || "Benchmark"};
  const base = held[1].split(/[/:\s]/)[0];
  return {short: `${base} hold`, long: `${base} buy and hold`};
}

/** Each cost as a share of the gross profit. */
function costsTitle(work: StrategyWork): string {
  const costs = work.costs;
  if (!costs.gross) return "";
  return [["Fees", costs.fees], ["Spread", costs.spread], ["Impact", costs.impact], ["Funding", costs.funding], ["Borrow", costs.borrow]].filter(([, value]) => value)
    .map(([name, value]) => `${name} ${pct(Number(value) / costs.gross, 0)} of gross`).join(", ");
}

const stateOf = (check: Check): TxCheck["state"] => check.state === "pass" ? "ok" : check.state === "fail" ? "watch" : "notrun";
/** A check that was not run offers to run it when the backend says how (`go` is an object); the others open what they came from. */
export function checkItems(checks: Check[], go: (target: CheckGo | string) => void): TxCheck[] {
  return checks.map(check => {
    const target = check.go;
    const runnable = check.state === "notrun" && typeof target === "object" && target !== null;
    const click = target === null ? undefined : check.state === "notrun" ? (runnable ? () => go(target) : undefined) : () => go(target);
    return {name: check.name, value: check.detail && check.state !== "notrun" ? `${check.value} · ${check.detail}` : check.value, need: check.need, state: stateOf(check), title: check.title, onClick: click,
      ...(runnable ? {runLabel: buttonFor(check)?.label} : {})};
  });
}

const CHECK_NAMES = ["Held-out Sharpe", "Walk-forward", "Neighbours", "Edge of the grid", "Costs doubled", "Shuffled trades", "Best five", "Deflated Sharpe"];
/** A run in progress, as the draft draws it: the numbers say when they come, the equity so far is drawn where the chart will be, the checks wait for the end. */
export function LiveRun({run, name, kind, benchmark}: {run: NonNullable<StrategyRun["progress"]> | null; name: string; kind: string; benchmark: string}) {
  const later = {value: <span aria-label="not available">—</span>, delta: "when done"};
  const items: TxKeyNumber[] = [{label: "CAGR", ...later}, {label: "Max drawdown", ...later}, {label: "Sharpe", ...later}, {label: `Over ${benchmark}`, ...later}, {label: "Costs, a year", ...later},
    {label: "Trades", value: run ? String(run.tradesSoFar) : "—", delta: "so far"}];
  return <><div className="qx-keys"><TxKeyNumbers items={items} /></div>
    <div className="qx-upper"><div className="qx-blk qx-chartblk"><div className="qx-bh"><h3>{name}</h3><span className="fx-t3 qx-s11">{kind}</span><span className="qx-key"><i />Equity</span></div>
      <div className="qx-mainchart">{run?.figure ? <div className="fx-rch" data-size="l"><Plot figure={run.figure} ariaLabel="Equity so far" /></div> : <p className="fx-empty" role="status">The equity appears as the run goes</p>}</div></div>
      <div className="qx-blk qx-ckcol"><div className="qx-bh"><h3>Checks</h3><span className="fx-t3 qx-s11">after the run</span></div>
        <div className="qx-livechecks">{CHECK_NAMES.map(label => <div key={label}><span>{label}</span><span>—</span></div>)}</div></div></div></>;
}

export function KeyNumbersRow({work, go, compare}: {work: StrategyWork; go(section: string): void; compare?: ReactNode}) {
  return <div className="qx-keys"><TxKeyNumbers items={keyNumbers(work, go)} />{compare && <div className="qx-compare">{compare}</div>}
    {work.notes.length > 0 && <div className="qx-notes" role="status">{work.notes.map((note, index) => <span key={index}>{note}</span>)}</div>}
    {work.partial && work.stoppedAt && <div className="qx-notes" role="status"><span>{`Stopped at ${mdy(work.stoppedAt)}, simulated; the part so far is kept below. Checks need a full run.`}</span></div>}
  </div>;
}

/** What the run's data did not have (names left out, days without prices, funding the source keeps no history of): in the run's header,
 * beside its period, read when wanted; not a bar between the key numbers and the chart. */
export function DataNotes({notes}: {notes: string[]}) {
  const [open, setOpen] = useState(false);
  if (!notes.length) return null;
  return <TxPopover open={open} onOpenChange={setOpen} label="Data notes" size="standard"
    trigger={<button type="button" className="tx-btn qx-datanotes" data-variant="quiet" data-size="sm">{notes.length === 1 ? "1 data note" : `${notes.length} data notes`}<TxIcon name="chevron" /></button>}>
    <ul className="qx-notelist">{notes.map((note, index) => <li key={index}>{note}</li>)}</ul>
  </TxPopover>;
}

export function ChecksColumn({work, go}: {work: StrategyWork; go(target: CheckGo | string): void}) {
  return <TxChecks scope="in-sample" groups={[{items: checkItems(work.checks, go)}]} />;
}

/** The checks folded into the chart's head, so the chart has the width of the page: the ones not passed are named with their value (each opens what it
 *  asks for), the rest are counted; the whole list opens below. */
export function ChecksStrip({work, go}: {work: StrategyWork; go(target: CheckGo | string): void}) {
  const [open, setOpen] = useState(false);
  const items = checkItems(work.checks, target => {setOpen(false); go(target);});
  if (!items.length) return null;
  const flagged = items.filter(item => item.state === "watch" || item.state === "fix");
  const passed = items.filter(item => item.state === "ok").length, notrun = items.filter(item => item.state === "notrun").length;
  const counts = [passed ? `${passed} passed` : "", notrun ? `${notrun} not run` : ""].filter(Boolean).join(" · ");
  return <div className="qx-ckstrip">
    <TxPopover open={open} onOpenChange={setOpen} label="Checks" size="compact" align="start"
      trigger={<button type="button" className="tx-btn qx-ckall" data-variant="quiet" data-size="sm" aria-label="All checks">Checks<span className="qx-ckcount">{counts}</span><span className="qx-checks-compact">{flagged.length ? `${flagged.length} to review` : counts}</span><TxIcon name="chevron" /></button>}>
      <ChecksColumn work={work} go={target => {setOpen(false); go(target);}} />
    </TxPopover>
    {flagged.map(item => <button key={item.name} type="button" className="qx-ckflag" data-state={item.state} title={item.title} onClick={item.onClick}><i />{item.name}<b>{item.value}</b></button>)}
  </div>;
}

// ---------------------------------------------------------------- the runs table
const RUN_COLUMNS: TxRunColumn[] = [
  {key: "n", label: "#", kind: "id"}, {key: "when", label: "Time", kind: "time"}, {key: "changes", label: "Change", kind: "change", hideBelow: 520},
  {key: "total", label: "Return", kind: "number", hideBelow: 620}, {key: "cagr", label: "CAGR", kind: "number"}, {key: "sharpe", label: "Sharpe", kind: "number"}, {key: "dd", label: "Max DD", kind: "number", hideBelow: 460},
  {key: "trades", label: "Trades", kind: "number", hideBelow: 560}, {key: "costs", label: "Costs/yr", kind: "number", hideBelow: 680}, {key: "state", label: "", kind: "text"},
];
/** What a sweep's row in the runs table says: how many combinations, how far it got when stopped, and the best one described (not chosen). */
export function sweepLine(sweep: NonNullable<StrategyRunRow["sweep"]>, kind: string, state?: string): string {
  const head = kind === "walk" ? (sweep.windows ? `Walk-forward, ${sweep.windows} ${sweep.windows === 1 ? "window" : "windows"}` : "Walk-forward") : `${sweep.total} combinations`;
  const stopped = sweep.done < sweep.total && state !== undefined && ["cancelled", "interrupted"].includes(state) ? `Stopped, ${sweep.completed} of ${sweep.total}` : null;
  // A walk-forward's row carries its stitched result in the number columns; the best combination over the whole period is a sweep's question, not its.
  const best = sweep.best && kind !== "walk" ? `best ${sweep.best.metric === "sharpe" ? "Sharpe" : sweep.best.metric} ${nf(sweep.best.value, 2)} at ${Object.values(sweep.best.params).map(value => value !== null && typeof value === "object" ? String((value as {since: string}).since).slice(0, 4) : String(value)).join(" · ")}` : null;
  return [head, stopped, best].filter(Boolean).join(", ");
}
const lower = (text: string) => text.charAt(0).toLowerCase() + text.slice(1);
/** One change in the runs table, as the draft writes it: the setting's label in lower case, then the values ("lookback 24 → 48"). */
const days = (value: unknown) => typeof value === "boolean" ? (value ? "on" : "off") : value === null || value === undefined || value === "" ? "none"
  : String(value).replace(/\d{4}-\d{2}-\d{2}/g, day => dayText(day));
const what = (change: StrategyRunRow["changes"][number], parameters?: Parameter[]) => `${lower(settingName(change.what, parameters))} ${change.from === null || change.from === undefined ? "" : days(change.from) + " → "}${days(change.to)}`;

export function runRows(runs: StrategyRunRow[], selected: string | null, compared: string | null, ticked: Set<string>, parameters?: Parameter[]): TxRunRow[] {
  return runs.map(run => ({id: run.id, selected: run.id === selected, compared: run.id === compared, checked: ticked.has(run.id), queued: run.state === "queued", cells: {
    // The number stands alone in its column so the numbers line up; what kind of run it is leads its change.
    n: <>{run.starred ? "★ " : ""}#{run.number}</>,
    when: when(run.createdAt), changes: <>{run.tags.includes("notebook") && <TxTag tone="muted">Notebook</TxTag>}{run.kind !== "backtest" && <TxTag tone="muted">{run.kind === "heldout" ? "Held-out" : run.kind}</TxTag>}
      {run.sweep ? sweepLine(run.sweep, run.kind, run.state) : run.changes.length ? run.changes.map(change => what(change, parameters)).join(", ") : run.number === 1 ? "first run" : "no changes"}</>,
    total: run.stats?.totalReturn == null ? "" : <span className={tone(run.stats.totalReturn)}>{pc(run.stats.totalReturn, 1)}</span>,
    costs: run.stats?.costsAYear == null ? "" : (run.stats.costsAYear ? "−" : "") + pct(run.stats.costsAYear, 1),
    cagr: run.stats?.cagr == null ? "" : <span className={tone(run.stats.cagr)}>{pc(run.stats.cagr, 1)}</span>, sharpe: run.stats?.sharpe == null ? "" : nf(run.stats.sharpe, 2),
    dd: run.stats?.maxDrawdown == null ? "" : pc(run.stats.maxDrawdown, 1), trades: run.stats ? String(run.stats.trades) : "",
    state: run.state === "completed" ? "" : run.state === "failed" ? <span className="qx-failrow" title={run.failure?.error ?? undefined}><TxStateWord tone="fix">{run.failure?.line ? `Failed at line ${run.failure.line}` : "Failed"}</TxStateWord>
      {run.failure?.error && <span className="qx-failwhy">{run.failure.error.split("\n")[0]}</span>}</span>
      : <TxStateWord tone={run.state === "cancelled" || run.state === "interrupted" ? "muted" : "ok"}>{run.state === "interrupted" ? "Interrupted" : run.state === "cancelled" ? "Stopped" : "Running"}</TxStateWord>,
  }}));
}

export function RunsView({runs, selected, compared, ticked, parameters, onSelect, onOpen, onTick}: {runs: StrategyRunRow[]; selected: string | null; compared: string | null; ticked: Set<string>; parameters?: Parameter[];
  onSelect(id: string): void; onOpen(id: string): void; onTick(id: string, on: boolean): void}) {
  return <TxRunTable columns={RUN_COLUMNS} rows={runRows(runs, selected, compared, ticked, parameters)} onRow={onSelect} onOpen={onOpen} onCheck={onTick} />;
}

export const heldLine = (run: StrategyRun) => run.heldOut ? "Held-out only" : run.settings.data.heldOutFrom ? `Held out from ${dayText(run.settings.data.heldOutFrom)}` : null;
export { secondsText };
