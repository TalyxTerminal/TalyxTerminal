import { t } from "../i18n";
/* The project view other modules embed: the project's file tree (closed until asked for), tabs of the files opened in it and one editor.
   It shares Developer's document model with the full editor; the embedding module caches no text, it only hears `changed`. */
import { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { TxButton, TxIcon, TxIconButton, TxTabs, type Json, type ModuleContext } from "@talyx/sdk";
import type { ContractTypes } from "../../public/ts/types";
import { privateRequest } from "../api";
import { documents } from "./service";
import { showMarks, type Marks } from "./editorMarks";
import "./editorMarks.css";
import "./projectEditor.css";
import "../workbench/workbench.css";

type Props = ContractTypes["view:project_editor"];
type Mark = NonNullable<Props["markers"]>[number];
type Editor = Awaited<ReturnType<typeof import("./monaco")["createEditor"]>>;
interface Entry { path: string; uri: string; kind: "file" | "directory" | "missing" | "unavailable" }
const hidden = new Set([".git", "node_modules", "__pycache__", ".venv", ".pytest_cache", ".ruff_cache"]);
const baseName = (path: string) => path.split("/").at(-1) ?? path;

/** The marks of one file, grouped by the file the embedding module named; a tree row and a tab show how many a file has. */
export function marksByFile(markers: Mark[] | undefined): Map<string, Mark[]> {
  const found = new Map<string, Mark[]>();
  for (const mark of markers ?? []) found.set(mark.uri.toLowerCase(), [...(found.get(mark.uri.toLowerCase()) ?? []), mark]);
  return found;
}

function ProjectEditor({ ctx, props, onEmit }: { ctx: ModuleContext; props: Props; onEmit(name: "changed" | "opened", value: Json): void }) {
  const tree = Boolean(props.tree);
  const [children, setChildren] = useState(new Map<string, Entry[]>()), [open, setOpen] = useState(new Set<string>()), [error, setError] = useState<string | null>(null);
  const [tabs, setTabs] = useState<{ documentId: string; uri: string; name: string }[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [, redraw] = useState(0);
  const host = useRef<HTMLDivElement>(null), editor = useRef<Editor | null>(null), marks = useRef<Marks | null>(null);
  const marked = useMemo(() => marksByFile(props.markers), [JSON.stringify(props.markers)]);
  useEffect(() => documents.watch(() => redraw(value => value + 1)), []);
  const load = async (path: string) => {
    if (!props.projectId) return;
    const found = await privateRequest<{ entries: Entry[] }>(ctx, `/projects/${encodeURIComponent(props.projectId)}/files?path=${encodeURIComponent(path)}`);
    setChildren(previous => new Map(previous).set(path, found.entries.filter(entry => !hidden.has(baseName(entry.path)))));
  };
  useEffect(() => { setChildren(new Map()); setOpen(new Set()); void load("").catch(reason => setError(reason instanceof Error ? reason.message : String(reason))); }, [props.projectId, props.rootUri]);
  const show = async (uri: string, position?: { line: number; column?: number }) => {
    const snapshot = await documents.open({ kind: "file", uri, projectId: props.projectId ?? null }, undefined, false);
    setTabs(current => current.some(tab => tab.documentId === snapshot.documentId) ? current : [...current, { documentId: snapshot.documentId, uri: snapshot.uri, name: snapshot.name }]);
    setActive(snapshot.documentId);
    onEmit("opened", { uri: snapshot.uri });
    if (position && editor.current) { editor.current.revealLineInCenter(position.line); editor.current.setPosition({ lineNumber: position.line, column: position.column ?? 1 }); editor.current.focus(); }
  };
  // The file asked for (and the line), once per request.
  const requested = useRef("");
  useEffect(() => {
    const key = JSON.stringify([props.file ?? null, props.position ?? null, props.positionRequestId ?? null]);
    if (!props.file || key === requested.current) return;
    requested.current = key;
    void show(props.file, props.position).catch(reason => setError(reason instanceof Error ? reason.message : String(reason)));
  }, [props.file, props.position?.line, props.positionRequestId]);
  // The editor: one Monaco instance whose model follows the active tab.
  useEffect(() => {
    if (!active || !host.current) return;
    let cancelled = false;
    void Promise.all([documents.model(active), import("./monaco")]).then(async ([model, { createEditor }]) => {
      if (cancelled || !host.current) return;
      if (!editor.current) {
        editor.current = createEditor(host.current, model, { readOnly: Boolean(props.readOnly) || documents.snapshot(active).readOnly, onSave: () => { void documents.save(active).catch(reason => documents.report(reason)); } });
        marks.current = await showMarks(editor.current, `project:${active}`);
      } else if (editor.current.getModel() !== model) editor.current.setModel(model);
      editor.current.updateOptions({ readOnly: Boolean(props.readOnly) || documents.snapshot(active).readOnly });
    }).catch(reason => setError(reason instanceof Error ? reason.message : String(reason)));
    return () => { cancelled = true; };
  }, [active]);
  useEffect(() => {
    const uri = active ? documents.snapshot(active).uri : null;
    marks.current?.show(uri ? (marked.get(uri.toLowerCase()) ?? []).map(({ uri: _uri, ...mark }) => mark) : []);
  }, [active, marked]);
  useEffect(() => () => { marks.current?.dispose(); editor.current?.dispose(); editor.current = null; }, []);
  // What the embedding module hears: the active document's version and whether it has unsaved edits.
  const last = useRef("");
  useEffect(() => {
    if (!active || !documents.entries.has(active)) return;
    const snapshot = documents.snapshot(active), value = { uri: snapshot.uri, documentId: active, version: snapshot.version, dirty: documents.isDirty(active) };
    if (JSON.stringify(value) !== last.current) { last.current = JSON.stringify(value); onEmit("changed", value); }
  });
  const toggle = (path: string) => {
    const next = new Set(open);
    if (next.has(path)) next.delete(path); else { next.add(path); void load(path).catch(reason => setError(reason instanceof Error ? reason.message : String(reason))); }
    setOpen(next);
  };
  const activeUri = active ? documents.snapshot(active).uri.toLowerCase() : "";
  const count = (uri: string) => marked.get(uri.toLowerCase())?.length ?? 0;
  const rows = (path: string, depth: number): JSX.Element[] => (children.get(path) ?? []).flatMap(entry => {
    const directory = entry.kind === "directory", expanded = open.has(entry.path);
    const row = <TxButton key={entry.path} variant="row" className="developer-tree-row" aria-current={!directory && entry.uri.toLowerCase() === activeUri ? "true" : undefined}
      style={{ paddingInlineStart: `calc(var(--tx-space-2) + ${depth} * var(--tx-space-3))` }} disabled={entry.kind === "unavailable"}
      onClick={() => directory ? toggle(entry.path) : void show(entry.uri).catch(reason => setError(reason instanceof Error ? reason.message : String(reason)))}>
      <TxIcon name={directory ? "chevron" : "code"} className={directory && expanded ? "developer-tree-icon developer-tree-open" : "developer-tree-icon"} /><span>{baseName(entry.path)}</span>
      {!directory && count(entry.uri) > 0 && <b className="developer-tree-marks" aria-label={t("{count} marks", { count: count(entry.uri) })}>{count(entry.uri)}</b>}
    </TxButton>;
    return directory && expanded ? [row, ...rows(entry.path, depth + 1)] : [row];
  });
  return <div className="developer-project-editor" data-tree={tree ? "" : undefined}>
    {tree && <nav className="developer-tree developer-project-tree" aria-label={t("Project files")}>
      <header className="developer-tree-head"><span title={props.rootUri}>{props.treeTitle ?? baseName(props.rootUri.replace(/\/+$/, ""))}</span><TxIconButton size="sm" label={t("Refresh files")} icon="refresh" onClick={() => void Promise.all(["", ...open].map(load)).catch(() => undefined)} /></header>
      <div className="developer-tree-rows">{rows("", 0)}</div>
    </nav>}
    <div className="developer-editor-pane">
      <div className="developer-tabs">
        <TxTabs label={t("Open files")} value={active ?? ""} onChange={setActive} options={tabs.map(tab => ({
          value: tab.documentId, label: tab.name, title: tab.uri, dirty: documents.isDirty(tab.documentId), marked: count(tab.uri) > 0 ? t("Has marks") : undefined,
        }))} />
      </div>
      {error && <div className="developer-editor-notice" role="alert">{error}</div>}
      <div ref={host} className="developer-editor-host" hidden={!active} />
    </div>
  </div>;
}

/** Mounts the project view; `update` re-renders it with new props. */
export function mountProjectEditor(node: HTMLElement, ctx: ModuleContext) {
  let props = ctx.props as unknown as Props;
  node.style.height = "100%"; node.style.minHeight = "0";
  const root = createRoot(node);
  const render = () => root.render(<ProjectEditor ctx={ctx} props={props} onEmit={(name, value) => ctx.emit?.(name, value)} />);
  render();
  ctx.onDispose(() => root.unmount());
  return { update(value: Json) { props = value as unknown as Props; render(); }, dispose: () => root.unmount() };
}
