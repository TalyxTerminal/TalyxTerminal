/** The side panel (the draft's): the kernel's variables and what the notebook loaded, or the library to browse, where one click inserts the line that reads an item. */
import { t } from "../i18n";
import { useState, type UIEvent } from "react";
import { TxIcon, TxIconButton, TxInput, TxStateWord } from "@talyx/sdk";
import { TxButton } from "@talyx/sdk";
import type { Loaded, Variable } from "../notebook";
import { TableExplorer, type TableData } from "./TableExplorer";
import "./notebook.css";

export interface LibraryEntry { group: "Factors" | "Models" | "Allocations" | "One-off data"; name: string; variable: string; version: number | null; tone: "fix" | "watch" | "ok" | "muted"; note: string }
export type Side = "vars" | "lib" | null;

const GROUPS = {Factors: t("Factors"), Models: t("Models"), Allocations: t("Allocations"), "One-off data": t("One-off data")};
/** The line that reads an item: always with the version, so the cell says which one it read. */
export const readLine = (entry: LibraryEntry) => `${entry.variable} = q.library.read(${JSON.stringify(entry.name)}${entry.version === null ? "" : `, version=${entry.version}`})`;
export function sizeText(bytes: number | null): string {
  if (bytes === null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return t("{value} KB", { value: Math.round(bytes / 1024) });
  return `${(bytes / 1024 ** 2).toFixed(bytes < 10 * 1024 ** 2 ? 1 : 0)} MB`;
}

const ROW = 28, OVERSCAN = 12;
/** Which of `count` equal rows to draw for a scroll position: all of them while there are few, else the visible ones and a margin (a thousand variables stay light). */
export function visibleRows(count: number, scrollTop: number, viewport = 520): { from: number; to: number } {
  if (count <= 200) return { from: 0, to: count };
  const from = Math.max(0, Math.floor(scrollTop / ROW) - OVERSCAN);
  return { from, to: Math.min(count, from + Math.ceil(viewport / ROW) + 2 * OVERSCAN) };
}

export function SidePanel({ side, variables, loaded, opened, library, kernelLine, onInsert, onOpenVariable, onCloseVariable }: {
  side: Exclude<Side, null>; variables: Variable[]; loaded: Loaded[]; opened: { name: string; table: TableData | null } | null; library: LibraryEntry[]; kernelLine: string;
  onInsert(line: string): void; onOpenVariable(name: string): void; onCloseVariable(): void;
}) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("");
  const shown = variables.filter(item => item.name.toLowerCase().includes(filter.toLowerCase()));
  const [top, setTop] = useState(0);
  const window_ = visibleRows(shown.length, top);
  return <aside className="qv-notebook-side">{side === "vars" ? <>
    <div className="tx-regionhead"><h3>{t("Variables")}</h3><span className="tx-regionsub">{variables.length}</span><span className="tx-spacer" />
      {variables.length > 20 && <span className="qv-notebook-search"><TxIcon name="search" /><TxInput value={filter} placeholder={t("Name")} onChange={event => setFilter(event.target.value)} /></span>}</div>
    <div className="qv-scroll" onScroll={(event: UIEvent<HTMLDivElement>) => setTop(event.currentTarget.scrollTop)}><table className="qv-table"><tbody>
      {window_.from > 0 && <tr aria-hidden="true" style={{height: window_.from * ROW}}><td colSpan={4} /></tr>}
      {shown.slice(window_.from, window_.to).map(item =>
      <tr key={item.name} title={item.fromCell === null ? undefined : t("From cell [{fromCell}]", { fromCell: item.fromCell })} onClick={() => onOpenVariable(item.name)}>
        <td className="l qv-notebook-var">{item.name}</td><td className="l qv-notebook-dim">{item.type}</td><td>{item.shape ?? ""}</td><td className="qv-notebook-dim">{sizeText(item.size)}</td></tr>)}</tbody></table>
      {opened !== null && <div className="qv-notebook-opened"><div className="tx-regionhead"><h3>{opened.name}</h3><span className="tx-spacer" /><TxButton variant="quiet" size="sm" onClick={onCloseVariable}><TxIcon name="close" />{t.c("action", "Close")}</TxButton></div>
        {opened.table === null ? <p className="qv-notebook-dim">{t("Not a table")}</p> : <TableExplorer table={opened.table} />}</div>}
      {loaded.length > 0 && <><div className="tx-regionhead"><h3>{t("Loaded")}</h3><span className="tx-regionsub">{loaded.length}</span></div>
        {loaded.map((item, index) => <div key={index} className="qv-notebook-loaded"><code>{item.call}</code><span>{item.what}</span><small>{item.at}, {item.seconds} s</small></div>)}</>}
      {/* When it started and what it holds; its environment is in the toolbar's dropdown and status already. */}
      {kernelLine && <><div className="tx-regionhead"><h3>{t("Kernel")}</h3></div><div className="qv-notebook-loaded"><span>{kernelLine}</span></div></>}</div></>
    : <><div className="tx-regionhead"><h3>{t("Library")}</h3><span className="tx-regionsub">{library.length}</span><span className="tx-spacer" />
        <span className="qv-notebook-search"><TxIcon name="search" /><TxInput value={query} placeholder={t("Name")} onChange={event => setQuery(event.target.value)} /></span></div>
      <div className="qv-scroll">{Object.entries(GROUPS).map(([group, label]) => {
        const items = library.filter(entry => entry.group === group && entry.name.toLowerCase().includes(query.toLowerCase()));
        return items.length ? <div key={group}><div className="qv-notebook-libgroup">{label}</div>{items.map(entry =>
          <button key={entry.name} type="button" className="qv-notebook-libitem" onClick={() => onInsert(readLine(entry))}>
            <TxStateWord tone={entry.tone}>{entry.name}</TxStateWord><span className="qv-notebook-dim">{entry.note}</span><span className="qv-notebook-insert">{t("Insert")}</span></button>)}</div> : null;
      })}</div></>}</aside>;
}

export function Rail({ side, onSide }: { side: Side; onSide(next: Side): void }) {
  return <nav className="qv-notebook-rail">
    <TxIconButton label={t("Variables")} icon="list" tone={side === "vars" ? "active" : "default"} onClick={() => onSide(side === "vars" ? null : "vars")} />
    <TxIconButton label={t("Library")} icon="bookmark" tone={side === "lib" ? "active" : "default"} onClick={() => onSide(side === "lib" ? null : "lib")} />
  </nav>;
}

/** Library items from `list_library`, in the groups the panel shows. */
export function libraryEntries(listing: unknown): LibraryEntry[] {
  const items = (listing as { items?: { name: string; variable: string; kind: string; state: string; versions?: { version: number; state?: string }[] }[] }).items ?? [];
  const group = (kind: string): LibraryEntry["group"] => kind === "factor" ? "Factors" : kind === "model" ? "Models" : kind === "allocation" ? "Allocations" : "One-off data";
  return items.map(item => {
    const live = (item.versions ?? []).filter(version => version.state !== "stopped");
    const newest = (live.length ? live : item.versions ?? []).reduce<number | null>((best, version) => best === null || version.version > best ? version.version : best, null);
    const tone = ["fail", "miss", "attention"].includes(item.state) ? "fix" : ["drift", "weak", "late"].includes(item.state) ? "watch" : item.state === "one_off" || item.state === "stopped" ? "muted" : "ok";
    return { group: group(item.kind === "data" ? "data" : item.kind), name: item.name, variable: item.variable, version: item.state === "one_off" ? null : newest, tone, note: newest === null ? "" : item.state === "one_off" ? t("one-off") : `v${newest}` };
  });
}
