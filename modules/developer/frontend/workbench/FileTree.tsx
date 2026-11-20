import { useEffect, useState, useSyncExternalStore } from "react";
import { TxAlert, TxButton, TxIcon, TxIconButton, type ModuleContext } from "@talyx/sdk";
import { documents } from "../documents/service";
import { projectFiles, type FileEntry, type WorkspaceProject } from "./api";
import { message } from "./errors";

const hidden = new Set([".git", "node_modules", "__pycache__", ".venv", ".pytest_cache", ".ruff_cache"]);
const baseName = (path: string) => path.split("/").at(-1) ?? path;

/** The project's folders and files, read on demand; files changed elsewhere appear after Refresh or reopening a folder. */
export function FileTree({ ctx, project }: { ctx: ModuleContext; project: WorkspaceProject }) {
  useSyncExternalStore(documents.watch, documents.state, documents.state);
  const [children, setChildren] = useState(new Map<string, FileEntry[]>()), [open, setOpen] = useState(new Set<string>()), [error, setError] = useState<string | null>(null);
  const load = async (path: string) => {
    const entries = await projectFiles(ctx, project.projectId, path);
    setChildren(previous => new Map(previous).set(path, entries.filter(entry => !hidden.has(baseName(entry.path)))));
  };
  const refresh = () => { setError(null); void Promise.all(["", ...open].map(load)).catch(reason => setError(message(reason))); };
  useEffect(() => { setChildren(new Map()); setOpen(new Set()); setError(null); void load("").catch(reason => setError(message(reason))); }, [ctx, project.projectId]);
  const toggle = (path: string) => {
    const next = new Set(open);
    if (next.has(path)) next.delete(path);
    else { next.add(path); void load(path).catch(reason => setError(message(reason))); }
    setOpen(next);
  };
  const active = documents.activeDocumentId && documents.entries.get(documents.activeDocumentId)?.snapshot.uri.toLowerCase();
  const rows = (path: string, depth: number): JSX.Element[] => (children.get(path) ?? []).flatMap(entry => {
    const directory = entry.kind === "directory", expanded = open.has(entry.path);
    const row = <TxButton key={entry.path} variant="row" className="developer-tree-row" aria-current={!directory && entry.uri.toLowerCase() === active ? "true" : undefined}
      style={{ paddingInlineStart: `calc(var(--tx-space-2) + ${depth} * var(--tx-space-3))` }} disabled={entry.kind === "unavailable"}
      onClick={() => directory ? toggle(entry.path) : void documents.open({ kind: "file", uri: entry.uri, projectId: project.projectId }).catch(reason => setError(message(reason)))}>
      <TxIcon name={directory ? "chevron" : "code"} className={directory && expanded ? "developer-tree-icon developer-tree-open" : "developer-tree-icon"} /><span>{baseName(entry.path)}</span>
    </TxButton>;
    return directory && expanded ? [row, ...rows(entry.path, depth + 1)] : [row];
  });
  return <nav className="developer-tree" aria-label={`${project.name} files`}>
    <header className="developer-tree-head"><span title={project.root}>{project.name}</span><TxIconButton size="sm" label="Refresh files" icon="refresh" onClick={refresh} /></header>
    {error && <TxAlert title="Files unavailable">{error}</TxAlert>}
    <div className="developer-tree-rows">{rows("", 0)}</div>
  </nav>;
}
