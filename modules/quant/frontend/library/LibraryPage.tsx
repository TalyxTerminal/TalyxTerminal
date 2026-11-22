/* The library page (output/quant-design/library.html): one table of everything published, problems first; opening an item narrows the table to a list beside it; its frozen code can open on the right. */
import { t } from "../i18n";
import { useEffect, useMemo, useRef, useState } from "react";
import { TxButton, TxIcon, TxObjectTitle, TxPopover, TxSearch, TxSegmented, TxSkeleton, type ModuleContext } from "@talyx/sdk";
import { useEvent } from "../objects";
import type { ShellState } from "../QuantShell";
import { hm, md } from "./format";
import { libraryApi, type LibraryApi, type LibraryList, type LibraryRow } from "./api";
import { LibraryItem, type TabId } from "./LibraryItem";
import { LibraryTable } from "./LibraryTable";
import { ProductionQueue } from "./Production";
import { CodePane } from "./ItemCode";
import { useCode } from "./useCode";
import { useWantedAuto } from "./wanted";
import "./library.css";

/** The row after (or before) this one in the list; the ends stay where they are. */
export function neighbour(ids: string[], current: string, delta: 1 | -1): string {
  const index = ids.indexOf(current);
  if (ids.length === 0) return current;
  return ids[Math.max(0, Math.min(ids.length - 1, (index < 0 ? 0 : index) + delta))];
}
/** Up to this many items the table leaves most of the page empty: the first of them opens beside it. */
export const FEW_ITEMS = 6;
/** The list's width beside an open item: at least 300, and never so wide that the item has less than 520. */
export const clampSplit = (total: number, wanted: number) => Math.max(300, Math.min(total - 520, wanted));

/** The library's list, for the page and for the navigation; refreshed when anything in the library changes. */
export function useLibrary(ctx: ModuleContext, api: LibraryApi, query = "") {
  const [data, setData] = useState<LibraryList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const asked = useRef(0);  // only the newest read lands: two reloads of one change answer in any order
  const load = () => {
    const ticket = ++asked.current;
    return api.list(query.trim() ? {query: query.trim()} : {}).then(value => {if (ticket === asked.current) {setData(value); setError(null);}},
      reason => {if (ticket === asked.current) setError(String(reason));});
  };
  useEffect(() => {void load();}, [api, query]);
  useEvent(ctx, "quant.library_changed", () => void load(), () => void load());
  return {data, items: data === null ? null : data.items, error, reload: load};
}

const FILTERS: {id: string; label: string; test(row: LibraryRow): boolean}[] = [{id: "all", label: t("All"), test: () => true}, {id: "attn", label: t("Needs attention"), test: row => row.attention},
  {id: "factor", label: t("Factors"), test: row => row.kind === "factor"}, {id: "model", label: t("Models"), test: row => row.kind === "model"}, {id: "allocation", label: t("Allocations"), test: row => row.kind === "allocation"},
  {id: "data", label: t("One-off data"), test: row => row.kind === "data"}];

export function LibraryPage({ctx, state, update, onHome, onOpenObject, onOpenVersion, onOpenRun}: {ctx: ModuleContext; state: ShellState; update(patch: Partial<ShellState>): void; onHome(): void;
  onOpenObject(id: string): void; onOpenVersion(versionId: string): void; onOpenRun(objectId: string, runId: string): void}) {
  const api = useMemo(() => libraryApi(ctx), [ctx]);
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const {data, items: read, error, reload} = useLibrary(ctx, api);
  const wanted = useWantedAuto(useMemo(() => (read ?? []).flatMap(row => row.versions), [read]));
  const items = useMemo(() => read === null ? null : read.map(row => ({...row, versions: wanted.apply(row.versions)})), [read, wanted.apply]);
  const [width, setWidth] = useState<number | null>(null);
  const [filled, setFilled] = useState(false);
  const [code, setCode] = useState<{open: boolean; file?: string; line?: number; error?: string}>({open: false});
  const body = useRef<HTMLDivElement>(null);
  const [compact, setCompact] = useState(false);
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setCompact(entry.contentRect.width < 820));
    observer.observe(body.current!);
    return () => observer.disconnect();
  }, []);
  const [queue, setQueue] = useState(false);
  const current = state.itemId ?? null;
  const tab = (state.libraryTab as TabId | undefined) ?? "overview";
  const rows = useMemo(() => {
    const test = FILTERS.find(item => item.id === filter)?.test ?? (() => true);
    return (items ?? []).filter(test).filter(row => !query || row.name.toLowerCase().includes(query.toLowerCase()));
  }, [items, filter, query]);
  const open = current !== null;
  const select = (itemId: string | null) => {update({itemId: itemId ?? undefined, versionId: undefined, libraryTab: undefined}); setCode({open: false});};
  // A few items: the first (problems come first) opens beside the list when the page opens, once; closed, the table stays.
  const arrived = useRef(false);
  useEffect(() => {
    if (arrived.current || items === null) return;
    arrived.current = true;
    if (current === null && items.length > 0 && items.length <= FEW_ITEMS) select(items[0].itemId);
  }, [items]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (document.querySelector("[role=dialog], [data-radix-popper-content-wrapper]")) return;
      const target = event.target as HTMLElement | null;
      if (event.key === "Escape" && open && !target?.closest("input")) {select(null); setFilled(false);}
      if ((event.key === "ArrowDown" || event.key === "ArrowUp") && open && !target?.closest("input, textarea, select, [role=tablist]")) {
        const ids = rows.map(row => row.itemId);
        if (ids.length > 0) {event.preventDefault(); select(neighbour(ids, current!, event.key === "ArrowDown" ? 1 : -1));}
      }
    };
    // Read before a sheet or menu handles the same key: Esc that closes the stop sheet must not also close the item under it.
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, current, rows]);
  const drag = (event: React.MouseEvent) => {
    event.preventDefault();
    const box = body.current!.getBoundingClientRect();
    const move = (moved: MouseEvent) => setWidth(clampSplit(box.width, moved.clientX - box.left));
    const up = () => {window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up);};
    window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
  };
  const toggleAuto = (row: LibraryRow, on: boolean) => {
    const version = row.versions.find(item => item.versionId === row.shownVersionId);
    if (version) void wanted.ask(version.versionId, on, () => api.setAuto(version.versionId, on)).then(reload, reload);
  };
  const archivable = data?.archivable ?? [];
  const lib = <div className={`lb${open ? " lb-open" : ""}${open && filled ? " lb-filled" : ""}`} ref={body} style={open && width !== null ? ({["--lb-w" as string]: `${width}px`} as React.CSSProperties) : undefined}>
    <div className="lb-lpane">
      <div className="lb-tbar">{open ? <><TxButton size="sm" variant="quiet" onClick={() => {select(null); setFilled(false);}}><TxIcon name="arrow-left" />{t("Table")}</TxButton><span className="lb-sp" /><TxSearch value={query} onValueChange={setQuery} placeholder={t("Name")} aria-label={t("Name")} /></>
        : <><TxSegmented label={t("Filter")} value={filter} onChange={setFilter} options={FILTERS.map(item => ({value: item.id, label: `${item.label} ${(items ?? []).filter(item.test).length}`}))} /><TxSearch value={query} onValueChange={setQuery} placeholder={t("Name")} aria-label={t("Name")} /><span className="lb-sp" /></>}</div>
      <div className="lb-tscroll">{items !== null && rows.length === 0 ? <div className="lb-calm">{(items ?? []).length === 0 ? t("Nothing published yet") : t("No items match")}</div>
        : <LibraryTable rows={rows} selected={current} compact={open || compact} onSelect={select} onAuto={toggleAuto} />}
        {!open && archivable.length > 0 && <div className="lb-tablebar"><span className="lb-sp" />
          {archivable.length > 0 && <button type="button" className="lb-link" onClick={() => select(archivable[0].itemId)}>{(archivable.length > 1 ? t("{archivableCount} stopped versions nobody uses", { archivableCount: archivable.length }) : t("{archivableCount} stopped version nobody uses", { archivableCount: archivable.length }))}</button>}</div>}</div>
    </div>
    {open && <div className="lb-gut" onMouseDown={drag} role="separator" aria-orientation="vertical" />}
    {open && <div className="lb-cpane"><LibraryItem ctx={ctx} api={api} itemId={current!} versionId={state.versionId} tab={tab} refresh={data?.items.length ?? 0} handlers={{
      onClose: () => {select(null); setFilled(false);}, onFill: () => setFilled(value => !value), filled, onTab: next => update({libraryTab: next}), onVersion: versionId => update({versionId}),
      onCode: (file, line, message) => setCode({open: true, file, line, error: message}), codeOpen: code.open, onOpenObject, onOpenVersion, onOpenRun, onSelectItem: select}} /></div>}
    {open && code.open && <CodeColumn api={api} itemId={current!} versionId={state.versionId} request={code} onClose={() => setCode({open: false})} />}
  </div>;
  return <div className="lb-page">
    <div className="qx-bar"><TxButton variant="quiet" size="sm" onClick={onHome}><TxIcon name="arrow-left" />{t("Home")}</TxButton>
      <TxObjectTitle name={t("Library")} kind={items === null ? "" : items.length === 1 ? t("1 item") : t("{count} items", { count: items.length })} />
      <span className="tx-spacer" />
      <TxPopover open={queue} onOpenChange={setQueue} label={t("Production")} trigger={<button type="button" className="lb-prod" title={t("Production")}>{data?.nextRound ? <>{t.rich("Next round {at}", { at: <b>{`${md(data.nextRound.at)}, ${hm(data.nextRound.at)}`}</b> })}</> : t("Production")}</button>}>
        <ProductionQueue production={data?.production ?? null} /></TxPopover>
      {open && <TxButton size="sm" variant="quiet" onClick={() => setCode(value => ({open: !value.open}))}><TxIcon name="code" />{code.open ? t("Hide code") : t("Show code")}</TxButton>}</div>
    {error && <div className="qx-page-error" role="alert"><span>{error}</span></div>}
    {lib}
  </div>;
}

function CodeColumn({api, itemId, versionId, request, onClose}: {api: LibraryApi; itemId: string; versionId: string | undefined; request: {file?: string; line?: number; error?: string}; onClose(): void}) {
  const view = useCode(api, itemId, versionId, request.file);
  if (view === null) return <div className="lb-code"><div className="lb-pad"><TxSkeleton width="40%" /></div></div>;
  return <CodePane title={view.title} file={view.file} lines={view.lines} errorLine={request.line ?? null} error={request.error ?? null} frozen={view.frozen} from={view.from} onClose={onClose} />;
}
