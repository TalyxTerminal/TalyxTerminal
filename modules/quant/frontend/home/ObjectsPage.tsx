/** All objects (the navigation expanded): every object by kind, unpublished candidates included, each row with its representative run's key
    numbers and checks; columns sort within a kind; This workspace or All. */
import { useMemo, useState, type ReactNode } from "react";
import { TxButton, TxIcon, TxInput, TxObjectTitle, TxSegmented, TxSpacer } from "@talyx/sdk";
import { nf, pc, pct, sgn } from "../factor/format";
import { KINDS, type Kind, type ObjectRow } from "../objects";
import { ChecksCell, RepCell, whenText } from "./cells";
import "./objects.css";

export interface Column { label: string; value(row: ObjectRow): number | undefined; show(row: ObjectRow): ReactNode }
const signed = (v: number, text: string) => <span className={v >= 0 ? "qv-objects-up" : "qv-objects-down"}>{text}</span>;
const number = (key: string) => (row: ObjectRow) => {const v = row.keyNumbers?.[key]; return typeof v === "number" ? v : undefined;};
const column = (label: string, key: string, show: (v: number, row: ObjectRow) => ReactNode): Column =>
  ({label, value: number(key), show: row => {const v = number(key)(row); return v === undefined ? "" : show(v, row);}});
const horizon = (row: ObjectRow) => typeof row.keyNumbers?.horizon === "string" ? <span className="qv-objects-hz">{row.keyNumbers.horizon}</span> : null;
const COLUMNS: Record<string, Column[]> = {
  factor: [column("Rank IC", "rank_ic", (v, row) => <>{signed(v, sgn(v, 4))}{horizon(row)}</>), column("t", "t", v => nf(v, 2)),
    column("Long–short, net", "long_short_net", v => signed(v, pc(v, 1))), column("Turnover", "turnover", v => pct(v, 0))],
  model: [column("Test Rank IC", "test_rank_ic", (v, row) => <>{signed(v, sgn(v, 4))}{horizon(row)}</>), column("ICIR", "icir", v => nf(v, 2)),
    column("Long–short, net", "long_short_net", v => signed(v, pc(v, 1)))],
  allocation: [column("Sharpe", "sharpe", v => nf(v, 2)), column("Return a year", "return_a_year", v => signed(v, pc(v, 1))), column("Volatility", "volatility", v => pct(v, 1)),
    column("Max drawdown", "max_drawdown", v => <span className="qv-objects-down">{pc(v, 1)}</span>)],
  strategy: [column("Return a year", "return_a_year", v => signed(v, pc(v, 1))), column("Sharpe", "sharpe", v => nf(v, 2)),
    column("Max drawdown", "max_drawdown", v => <span className="qv-objects-down">{pc(v, 1)}</span>), column("Trades", "trades", v => nf(v, 0))],
  notebook: [{label: "Cells", value: row => row.cells ?? undefined, show: row => row.cells ?? ""}],
};
export const columnsFor = (kind: Kind): Column[] => COLUMNS[kind] ?? [];
export interface Sort { label: string; dir: 1 | -1 }
const ORDER: Kind[] = ["factor", "model", "allocation", "strategy", "notebook"];
const PUBLISHED: Kind[] = ["factor", "model", "allocation"];

/** A kind's rows in the column's order; unsorted, the newest change first. An empty value goes last either way. */
export function sortRows(kind: Kind, rows: ObjectRow[], sort: Sort | null): ObjectRow[] {
  if (sort === null) return [...rows].sort((a, b) => b.changedAt - a.changedAt);
  const keys: Record<string, (row: ObjectRow) => string | number | undefined> = {Name: row => row.name.toLowerCase(), Folder: row => row.folder ?? "", Changed: row => row.changedAt,
    Runs: row => row.runsCount, Checks: row => row.checks ? row.checks.passed / row.checks.total : undefined};
  columnsFor(kind).forEach(item => {keys[item.label] = item.value;});
  const key = keys[sort.label] ?? (() => undefined);
  return [...rows].sort((a, b) => {const x = key(a), y = key(b); if (x === undefined && y === undefined) return 0; if (x === undefined) return 1; if (y === undefined) return -1; return (x > y ? 1 : x < y ? -1 : 0) * sort.dir;});
}

export function KindTable({kind, rows, sort, onSort, onOpen, showWorkspace}: {kind: Kind; rows: ObjectRow[]; sort: Sort | null; onSort(sort: Sort): void; onOpen(row: ObjectRow): void; showWorkspace: boolean}) {
  const columns = columnsFor(kind), notebook = kind === "notebook", library = PUBLISHED.includes(kind);
  const th = (label: string, left?: boolean, title?: string) => <th key={label} className={left ? "l qv-objects-sort" : "qv-objects-sort"} title={title} aria-sort={sort?.label === label ? (sort.dir > 0 ? "ascending" : "descending") : undefined}
    onClick={() => onSort({label, dir: sort?.label === label ? (sort.dir === 1 ? -1 : 1) : label === "Name" || label === "Folder" ? 1 : -1})}>{label}{sort?.label === label ? (sort.dir > 0 ? " ↑" : " ↓") : ""}</th>;
  return <section className="qv-objects-group"><div className="qv-objects-grouphead"><b>{KINDS[kind].plural}</b><span>{`${rows.length}${library ? ` · ${rows.filter(row => !row.library).length} not published` : ""}`}</span></div>
    <table className="qv-table qv-objects-table"><thead><tr>{th("Name", true)}{th("Folder", true)}{showWorkspace && <th className="l">Workspace</th>}
      {notebook ? <th className="l">Reads</th> : <>{library && <th className="l">In library</th>}<th className="l" title="Published run; else the starred one; else the latest successful">Representative run</th></>}
      {columns.map(item => th(item.label))}{!notebook && th("Checks", false, "Checks passed on the representative run")}{!notebook && th("Runs")}{th("Changed")}</tr></thead>
      <tbody>{sortRows(kind, rows, sort).map(row => <tr key={row.id} onClick={() => onOpen(row)}>
        <td className="l qv-objects-name">{row.name}</td><td className="l qv-objects-dim">{row.folder ?? ""}</td>
        {showWorkspace && <td className="l qv-objects-dim">{row.inThisWorkspace ? "This workspace" : row.workspace ?? ""}</td>}
        {notebook ? <td className="l qv-objects-dim">{row.reads ?? ""}</td> : <>{library && <td className="l">{row.library ? <span className="qv-objects-dim2">{row.library.text}</span> : <span className="qv-objects-dim">candidate</span>}</td>}<td className="l"><RepCell o={row} /></td></>}
        {columns.map(item => <td key={item.label}>{item.show(row)}</td>)}{!notebook && <td><ChecksCell o={row} /></td>}{!notebook && <td className="qv-objects-dim">{row.runsCount}</td>}
        <td className="qv-objects-dim">{whenText(row.changedAt)}</td></tr>)}</tbody></table></section>;
}

export function ObjectsPage({rows, all, onAll, onOpen, onBack, onLibrary, initialSort}: {rows: ObjectRow[]; all: boolean; onAll(value: boolean): void; onOpen(row: ObjectRow): void;
  onBack(): void; onLibrary(): void; initialSort?: {kind: Kind; label: string; direction: 1 | -1}}) {
  const [kind, setKind] = useState<"all" | Kind>("all");
  const [query, setQuery] = useState("");
  const [sorts, setSorts] = useState<Partial<Record<Kind, Sort>>>(initialSort ? {[initialSort.kind]: {label: initialSort.label, dir: initialSort.direction}} : {});
  const pool = useMemo(() => rows.filter(row => row.name.toLowerCase().includes(query.toLowerCase())), [rows, query]);
  const kinds = kind === "all" ? ORDER : [kind];
  return <div className="qv-home-page">
    <div className="qx-bar"><TxButton variant="quiet" size="sm" onClick={onBack}><TxIcon name="arrow-left" />Home</TxButton><TxObjectTitle name="All objects" kind={String(pool.length)} />
      <TxSegmented label="Kind" value={kind} onChange={setKind} options={[{value: "all", label: "All"}, ...ORDER.map(item => ({value: item, label: `${KINDS[item].plural} ${pool.filter(row => row.kind === item).length}`}))]} />
      <span className="qv-objects-search"><TxIcon name="search" /><TxInput value={query} placeholder="Name" aria-label="Name" onChange={event => setQuery(event.target.value)} /></span><TxSpacer />
      <TxSegmented label="Scope" value={all ? "all" : "here"} onChange={value => onAll(value === "all")} options={[{value: "here", label: "This workspace"}, {value: "all", label: "All"}]} />
      <TxButton variant="quiet" size="sm" onClick={onLibrary}><TxIcon name="bookmark" />Library</TxButton></div>
    <div className="qv-scroll">{kinds.map(item => {
      const own = pool.filter(row => row.kind === item);
      return own.length ? <KindTable key={item} kind={item} rows={own} sort={sorts[item] ?? null} showWorkspace={all} onSort={sort => setSorts(current => ({...current, [item]: sort}))} onOpen={onOpen} /> : null;
    })}</div>
  </div>;
}
