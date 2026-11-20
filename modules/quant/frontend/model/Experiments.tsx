/* The experiment table: one row a training run, a batch folded together, sorted by the test Rank IC; what a run that is not done shows in place of its numbers. */
import { TxButton, TxHeatmap, TxIcon, TxInput, TxMenu, TxRunTable, TxSegmented, TxTag, type TxRunColumn, type TxRunGroup, type TxRunRow } from "@talyx/sdk";
import type { ModelRun, RunList, Running } from "./api";
import { algoLabel, day, minutes, pct, sgn } from "./format";
import { when } from "../factor/format";

export const COLUMNS: TxRunColumn[] = [
  {key: "run", label: "Run", kind: "id"}, {key: "what", label: "Model", kind: "text"},
  {key: "ric", label: "Test Rank IC", sortable: true}, {key: "t", label: "t", sortable: true, hideBelow: 560}, {key: "vsin", label: "vs input", sortable: true, hideBelow: 700},
  {key: "net", label: "L–S net", sortable: true, hideBelow: 640}, {key: "turn", label: "Turnover", hideBelow: 820}, {key: "trte", label: "Train − test", hideBelow: 900},
  {key: "pos", label: "> 0", hideBelow: 960}, {key: "chk", label: "Checks", hideBelow: 760}, {key: "time", label: "Time", hideBelow: 700}, {key: "data", label: "Data to", hideBelow: 1040},
];
const GB = 1024 ** 3;
/** The tiers of "at a time" this machine's memory budget holds, each with what it would use: only these are offered. */
const STEP: Record<string, string> = {check: "Checking", data: "Preparing the data", compute: "Starting"};

export function tiers(running: Running, perRun: number): {count: number; label: string}[] {
  const each = Math.max(perRun, 0.25 * GB), room = running.budget * GB;
  const fit = Math.max(1, Math.min(running.cpus, Math.floor(room / each)));
  return Array.from({length: fit}, (_, index) => index + 1).map(count => ({count, label: count === 1 ? "1 at a time" : `${count} at a time · about ${((count * each) / GB).toFixed(0)} GB${count === fit ? ` of ${running.budget.toFixed(1)} GB` : ""}`}));
}
const num = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value : null;
/** 1st, 2nd, 3rd, 4th, 11th, 21st (it wrote "1th in the queue"). */
export const ordinal = (n: number) => `${n}${n % 100 >= 11 && n % 100 <= 13 ? "th" : ["th", "st", "nd", "rd"][n % 10] ?? "th"}`;
const signed = (value: number | null, text: string) => value === null || value === 0 ? text : <span className={value > 0 ? "tx-up" : "tx-down"}>{text}</span>;

function sortValue(run: ModelRun, key: string) {
  const m = run.metrics;
  if (run.status !== "completed" || !m) return null;
  return key === "ric" ? num(m.ric) : key === "t" ? num(m.t) : key === "vsin" ? num(m.vsInput) : key === "net" ? num(m.lsNet) : null;
}

export function differences(run: ModelRun, base: ModelRun | null): string {
  if (Object.keys(run.values ?? {}).length) return Object.entries(run.values).map(([key, value]) => `${key.replace(/^(lgb|xgb|cat)_/, "")} ${String(value)}`).join(", ");
  if (!base) return "";
  const keys = [...new Set([...Object.keys(run.params), ...Object.keys(base.params)])].filter(key => JSON.stringify(run.params[key]) !== JSON.stringify(base.params[key]) && key !== "inputs");
  return keys.slice(0, 3).map(key => `${key.replace(/^(lgb|xgb|cat)_/, "")} ${String(run.params[key])}`).join(", ");
}

export function Experiments({list, runs, selectedId, compareId, ticked, filter, query, sort, byBatch, onFilter, onQuery, onSort, onByBatch, onSelect, onTick, onStar, onNext, onRemove, onResume, onOpen, onCompare, onCombine, view, onView, running, perRun, onConcurrency}: {
  running?: Running | null; perRun: number; onConcurrency(count: number): void;
  view: "table" | "heatmap"; onView(next: "table" | "heatmap"): void;
  list: RunList | null; runs: ModelRun[]; selectedId: string | null; compareId: string | null; ticked: Set<string>; filter: string; query: string; sort: string; byBatch: boolean;
  onFilter(next: string): void; onQuery(next: string): void; onSort(key: string): void; onByBatch(next: boolean): void; onSelect(id: string): void; onTick(id: string, on: boolean): void;
  onStar(run: ModelRun): void; onNext(run: ModelRun): void; onRemove(run: ModelRun): void; onResume(run: ModelRun, alone?: boolean): void; onOpen(id: string): void; onCompare(): void; onCombine(): void;
}) {

  const visible = runs.filter(run => (filter === "all" || (filter === "starred" && run.starred) || (filter === "published" && run.published) || (filter === "failed" && run.status === "failed"))
    && (!query || `#${run.number} ${algoLabel(run.algo)} ${run.note} ${run.label}`.toLowerCase().includes(query.toLowerCase())));
  const finished = visible.filter(run => run.status === "completed").sort((a, b) => (sortValue(b, sort) ?? -1e9) - (sortValue(a, sort) ?? -1e9));
  const ordered = [...finished, ...visible.filter(run => run.status === "running"), ...visible.filter(run => run.status === "queued"), ...visible.filter(run => run.status === "failed" || run.status === "stopped")];
  const varied = [...new Set(ordered.flatMap(run => Object.keys(run.values ?? {})))];
  const base = (id: string | null) => runs.find(run => run.id === id) ?? null;
  const cellsOf = (run: ModelRun): TxRunRow["cells"] => {
    const m = run.metrics ?? {};
    const what = <span className="mx-what"><span>{algoLabel(run.algo)} · {run.label}</span>{differences(run, base(compareId)) && <span className="mx-diff"> {differences(run, base(compareId))}</span>}
      {run.overrideFrom && Object.keys(run.overrides).length > 0 && <span className="mx-t3"> changed from window {run.overrideFrom}: {Object.entries(run.overrides).map(([key, value]) => `${key.replace(/^(lgb|xgb|cat)_/, "")} ${String(value)}`).join(", ")}</span>}
      {run.published && <TxTag tone="version">v{run.published.version}</TxTag>}{run.heldOut && <TxTag tone="muted">Held-out</TxTag>}
      {run.kind === "combine" && <span className="mx-t3"> mean of {run.combinedFrom?.length ?? 0}</span>}</span>;
    const id = <span className="mx-id"><button type="button" className="mx-star" data-on={run.starred ? "" : undefined} aria-label={run.starred ? "Starred" : "Star"} onClick={event => {event.stopPropagation(); onStar(run);}}>
      <TxIcon name="star" /></button>#{run.number}</span>;
    const extra = Object.fromEntries(varied.map(key => [`v:${key}`, run.values && key in run.values ? <b className="mx-num">{String(run.values[key])}</b> : ""]));
    if (run.status !== "completed") {
      // Before its first window a run says the step it is on ("Window … of 27" while it checked or read its data).
      const text = run.status === "running" && (run.state === "preparing" || !run.progress?.window) ? <><span className="tx-spin" /> {run.resumedAfterClose ? "Resumed after Quant closed · " : ""}{STEP[run.stage] ?? "Starting"}{run.stage === "data" && run.progress?.total ? ` · ${run.progress.stage === "facts" ? "facts " : ""}${run.progress.done ?? 0} of ${run.progress.total}${run.progress.waitingFor ? ` · waiting for ${run.progress.waitingFor}` : ""}` : ""}</>
        : run.status === "running" ? <><span className="tx-spin" /> {run.resumedAfterClose ? "Resumed after Quant closed · " : ""}Window {run.progress?.window}{run.windowsTotal ? ` of ${run.windowsTotal}` : ""}{run.progress?.epochs ? ` · epoch ${run.progress.epoch ?? 0}/${run.progress.epochs}${typeof run.progress.validLoss === "number" ? `, loss ${run.progress.validLoss.toFixed(4)}` : ""}` : ""}{run.progress?.left ? ` · about ${minutes(run.progress.left)} left` : ""}</>
        : run.status === "queued" ? <>{typeof run.queue === "object" && run.queue?.position ? `${ordinal(run.queue.position)} in the queue` : "In the queue"} · <button type="button" className="mx-link" onClick={event => {event.stopPropagation(); onNext(run);}}>Run next</button> · <button type="button" className="mx-link" onClick={event => {event.stopPropagation(); onRemove(run);}}>Remove</button></>
        : run.status === "failed" ? <><TxTag tone="fix">Failed</TxTag> {run.failure?.title}{run.windowsDone ? ` · windows 1–${run.windowsDone} kept` : ""} · <button type="button" className="mx-link" onClick={event => {event.stopPropagation(); onResume(run);}}>Resume{run.failure?.resumeFrom ? ` from window ${run.failure.resumeFrom}` : ""}</button></>
        : <><TxTag tone="muted">Stopped</TxTag> window {run.stopped?.window ?? run.windowsDone}{run.windowsTotal ? ` of ${run.windowsTotal}` : ""} · <button type="button" className="mx-link" onClick={event => {event.stopPropagation(); onResume(run);}}>Resume</button></>;
      return {run: id, what: <span className="mx-whatcol">{what}<span className="mx-status">{text}</span></span>, ...extra};
    }
    const net = num(m.lsNet), trte = num(m.trainIc) !== null && num(m.ric) !== null ? (m.trainIc as number) - (m.ric as number) : null;
    return {run: id, what, ...extra, ric: <b className="mx-num">{signed(num(m.ric), sgn(num(m.ric)))}</b>, t: sgn(num(m.t), 2), vsin: sgn(num(m.vsInput)), net: signed(net, pct(net)), turn: num(m.turnover) === null ? "—" : `${((m.turnover as number) * 100).toFixed(0)}%`,
      trte: sgn(trte), pos: m.windows ? `${m.positive}/${m.windows}` : "—", chk: `${run.passed}/${run.passed + run.review}`, time: minutes(run.time), data: day(run.dataTo)};
  };
  const rows: TxRunRow[] = ordered.map(run => ({id: run.id, group: byBatch ? run.batch ?? undefined : undefined, selected: run.id === selectedId, compared: run.id === compareId, checked: ticked.has(run.id), queued: run.status === "queued", cells: cellsOf(run)}));
  const groups: TxRunGroup[] = byBatch ? (list?.batches ?? []).filter(batch => ordered.some(run => run.batch === batch.id)).map(batch => {
    const members = ordered.filter(run => run.batch === batch.id), best = members.filter(run => run.status === "completed").sort((a, b) => (sortValue(b, "ric") ?? -1) - (sortValue(a, "ric") ?? -1))[0];
    return {id: batch.id, title: `Batch ${batch.title || batch.vary.join(" × ")}`, summary: ` · ${when(batch.started)} · ${members.length} groups: ${[["done", "completed"], ["running", "running"], ["queued", "queued"], ["failed", "failed"], ["stopped", "stopped"]]
      .map(([text, status]) => [members.filter(run => run.status === status).length, text] as const).filter(([count]) => count > 0).map(([count, text]) => `${count} ${text}`).join(", ")}${best ? ` · best #${best.number} ${sgn(sortValue(best, "ric"))}` : ""}`};
  }) : [];
  // What changes inside a batch has a column each, after the model's name.
  const nice = (key: string) => key.replace(/^(lgb|xgb|cat)_/, "").replace(/_/g, " ").replace(/^./, letter => letter.toUpperCase());
  const columns = [...COLUMNS.slice(0, 2), ...varied.map(key => ({key: `v:${key}`, label: nice(key), kind: "text" as const})), ...COLUMNS.slice(2)].map(column => ({...column, sorted: column.key === sort}));
  // A batch that varied two settings reads as a grid: the first across, the second down, the test Rank IC in each cell.
  const batch = list?.batches.find(item => runs.find(run => run.id === selectedId)?.batch === item.id) ?? null;
  const members = batch ? runs.filter(run => run.batch === batch.id) : [];
  const grid = batch && batch.vary.length === 2 ? (() => {
    const [a, b] = batch.vary;
    const xs = [...new Set(members.map(run => String(run.values[a])))], ys = [...new Set(members.map(run => String(run.values[b])))];
    const at = (x: string, y: string) => members.find(run => String(run.values[a]) === x && String(run.values[b]) === y);
    return {a, b, xs, ys, at};
  })() : null;
  return <div className="mx-exp">
    <div className="mx-tools"><b className="mx-exptitle">Experiments <span className="mx-t3">{runs.length}</span></b><TxInput className="mx-search" aria-label="Search experiments" placeholder="Search" value={query} onChange={event => onQuery(event.target.value)} />
      <TxSegmented label="Filter" value={filter} options={[{value: "all", label: "All"}, {value: "starred", label: "Starred"}, {value: "published", label: "Published"}, {value: "failed", label: "Failed"}]} onChange={onFilter} />
      {grid && <TxSegmented label="Table or heatmap" value={view} options={[{value: "table", label: "Table"}, {value: "heatmap", label: "Heatmap"}]} onChange={onView} />}
      <TxSegmented label="Grouping" value={byBatch ? "batch" : "flat"} options={[{value: "batch", label: "By batch"}, {value: "flat", label: "Flat"}]} onChange={next => onByBatch(next === "batch")} />
      <span className="tx-spacer" />
      {running && <span className="mx-t3 mx-runstat">{running.running} running, {running.waiting} queued
        <TxMenu label="Runs at a time" align="end" trigger={<TxButton size="sm" variant="quiet">{running.concurrency} at a time</TxButton>}
          items={tiers(running, perRun).map(tier => ({id: String(tier.count), label: tier.label, onSelect: () => onConcurrency(tier.count)}))} />
        · memory {running.memoryUsed.toFixed(1)} of {running.memoryTotal.toFixed(1)} GB</span>}
      {ticked.size >= 2 && <button type="button" className="mx-link" onClick={onCombine}>Combine</button>}{ticked.size >= 1 && <button type="button" className="mx-link" onClick={onCompare}>Compare {ticked.size}</button>}</div>
    <div className="mx-table">{list === null ? null : ordered.length === 0 ? <div className="mx-empty">No experiments yet</div>
      : view === "heatmap" && grid ? <TxHeatmap title="Test Rank IC" axes={`${grid.a} × ${grid.b}`} x={grid.xs} y={grid.ys}
        cells={grid.ys.map(y => grid.xs.map(x => {const run = grid.at(x, y); const value = run?.status === "completed" ? num(run.metrics?.ric) : null;
          return run === undefined ? null : value === null ? {state: "notrun" as const, title: `#${run.number}`} : {value, title: `#${run.number}`};}))}
        format={value => sgn(value)} onCell={(row, column) => {const run = grid.at(grid.xs[column], grid.ys[row]); if (run) onSelect(run.id);}} />
      : <TxRunTable columns={columns} rows={rows} groups={groups} onRow={onSelect} onOpen={onOpen} onCheck={onTick} onSort={onSort} />}</div></div>;
}
