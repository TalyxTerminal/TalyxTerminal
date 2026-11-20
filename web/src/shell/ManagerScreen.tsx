import { useEffect, useMemo, useState } from "react";
import {
  TxAlert, TxButton, TxIcon, TxIconButton, TxMenu, TxRoot, TxSearch, TxSegmented, TxSkeleton, TxToastProvider, TxTooltipProvider,
  useTxToast, type TxMenuItem,
} from "@talyx/sdk";
import type { TalyxDesktop } from "../../../host/desktop/preload";
import { platformRuntime } from "../kernel/runtime";
import { openWorkspaceFolder } from "./shellChrome";
import type { CatalogPhase, SavedWorkspace } from "./savedWorkspaces";
import { ModuleIcon } from "./ModuleIcon";
import { moduleLine, savedWhen, type LayoutTile } from "./workspaceSummary";
import { workspaceWindowChannel, workspaceWindowCommand, type OpenWorkspace } from "./workspaceWindows";
import "./ManagerScreen.css";

/* All workspaces: a list in the manner of saved layouts. Each row starts with a small sketch of the
   workspace's arrangement as it was saved (where each panel sits, with its module's icon), then the name, its
   modules and when it was last saved; whether it is open. Hovering a row brings Open and a ⋯ menu (open, rename,
   duplicate, show its folder, delete). */

type Sort = "modified" | "name";

interface Row {
  item: SavedWorkspace;
  /** Null for a record saved before workspaces carried their layout summary. */
  tiles: LayoutTile[] | null;
  /** The open workspace it is (its folder can be shown), or null. */
  openId: string | null;
}

const percent = (value: number) => `${value * 100}%`;

function Sketch({ tiles }: { tiles: LayoutTile[] | null }) {
  return <span className="ws-sketch" aria-hidden="true">
    <span className="ws-sketch-area">
      {tiles?.map((tile, index) => <span key={index} className="ws-tile"
        style={{ left: percent(tile.x), top: percent(tile.y), width: percent(tile.w), height: percent(tile.h) }}>
        <span><ModuleIcon module={tile.module} /></span>
      </span>)}
    </span>
  </span>;
}

export interface ManagerScreenProps {
  saved: SavedWorkspace[];
  phase: CatalogPhase;
  error: string | null;
  onOpen: (item: SavedWorkspace) => void;
  onCreate: () => void;
  onRetry: () => void;
  onRename: (item: SavedWorkspace) => void;
  onDelete: (item: SavedWorkspace) => void;
}

export default function ManagerScreen(props: ManagerScreenProps) {
  return <TxRoot className="ws-root">
    <TxToastProvider><TxTooltipProvider><WorkspacePage {...props} /></TxTooltipProvider></TxToastProvider>
  </TxRoot>;
}

function WorkspacePage({ saved, phase, error, onOpen, onCreate, onRetry, onRename, onDelete }: ManagerScreenProps) {
  const toast = useTxToast();
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort>("modified");
  const [open, setOpen] = useState<OpenWorkspace[]>([]);
  const [desktop, setDesktop] = useState<TalyxDesktop | undefined>();

  // Which saved workspaces are open: the home window lists them, and says again whenever the list changes.
  useEffect(() => {
    let live = true;
    void workspaceWindowCommand({ kind: "list" }).then(records => { if (live) setOpen(records); }, () => undefined);
    const receive = (event: MessageEvent) => {
      if (event.origin === location.origin && event.data?.channel === workspaceWindowChannel && Array.isArray(event.data.opened) && !event.data.requestId)
        setOpen(event.data.opened);
    };
    window.addEventListener("message", receive);
    return () => { live = false; window.removeEventListener("message", receive); };
  }, []);
  useEffect(() => {
    const host = window.top!;
    const ready = () => setDesktop(host.talyxDesktop);
    host.addEventListener("talyx-desktop-ready", ready);
    ready();
    return () => host.removeEventListener("talyx-desktop-ready", ready);
  }, []);

  const all = useMemo<Row[]>(() => {
    if (phase !== "ready") return [];
    return saved.map((item): Row => ({
      item, tiles: item.summary?.tiles ?? null, openId: open.some(record => record.workspaceId === item.workspaceId) ? item.workspaceId : null,
    }));
  }, [saved, open, phase]);

  const shown = useMemo(() => {
    const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return all
      .filter(row => words.every(word => `${row.item.name} ${row.tiles ? moduleLine(row.tiles) : ""}`.toLowerCase().includes(word)))
      .sort(sort === "name"
        ? (a, b) => a.item.name.localeCompare(b.item.name, undefined, { sensitivity: "base", numeric: true })
        : (a, b) => b.item.savedAt.localeCompare(a.item.savedAt));
  }, [all, query, sort]);

  const duplicate = async ({ item }: Row) => {
    try {
      const copy = await platformRuntime().platform["workspace.copy"]({ workspaceId: item.workspaceId });
      onRetry();
      toast({ title: `Saved ${copy.name}` });
    } catch (reason) {
      toast({ title: `Couldn't duplicate ${item.name}`, detail: reason instanceof Error ? reason.message : String(reason), tone: "failed" });
    }
  };

  const showFolder = async ({ item, openId }: Row) => {
    try { await openWorkspaceFolder(openId!); }
    catch (reason) {
      toast({ title: `Couldn't show the folder of ${item.name}`, detail: reason instanceof Error ? reason.message : String(reason), tone: "failed" });
    }
  };

  const menu = (row: Row): TxMenuItem[] => [
    { id: "open", label: "Open", icon: "external", onSelect: () => onOpen(row.item) },
    { id: "rename", label: "Rename", icon: "edit", onSelect: () => onRename(row.item) },
    { id: "duplicate", label: "Duplicate", icon: "copy", onSelect: () => void duplicate(row) },
    ...(desktop && row.openId ? [{ id: "folder", label: "Show folder", icon: "folder" as const, onSelect: () => void showFolder(row) }] : []),
    { id: "delete", label: "Delete", icon: "trash", tone: "danger", separated: true, onSelect: () => onDelete(row.item) },
  ];

  const nothingSaved = phase === "ready" && !error && all.length === 0;

  return <div className="ws-shell">
    {nothingSaved
      ? <div className="ws-empty">
        <h1>No saved workspaces yet</h1>
        <TxButton variant="primary" onClick={onCreate}><TxIcon name="plus" />New workspace</TxButton>
      </div>
      : <div className="ws-page">
        <div className="ws-main">
          <header className="ws-head">
            <h1>Workspace</h1>
            {phase === "ready" && <span className="ws-count">{shown.length}</span>}
            <div className="ws-tools">
              <TxSearch className="ws-search" value={query} onValueChange={setQuery} aria-label="Search workspaces" placeholder="Search workspaces" />
              <TxSegmented size="sm" label="Sort" value={sort} onChange={setSort} options={[
                { value: "modified", label: "Last modified" }, { value: "name", label: "Name" },
              ]} />
              <TxButton variant="primary" onClick={onCreate}><TxIcon name="plus" />New workspace</TxButton>
            </div>
          </header>

          <div className="ws-table" role="table" aria-label="Saved workspaces">
            <div className="ws-table-head" role="row">
              <span role="columnheader" className="ws-col-name">Name</span>
              <span role="columnheader">Modified</span>
              <span />
            </div>
            <div className="ws-rows" role="rowgroup">
              {phase === "loading" && <>
                <span className="ws-sr" role="status">Loading saved workspaces</span>
                {Array.from({ length: 6 }, (_, index) => <div key={index} className="ws-row" aria-hidden="true">
                  <span className="ws-sketch" /><span className="ws-name"><TxSkeleton width="40%" /><TxSkeleton width="22%" /></span>
                </div>)}
              </>}
              {phase === "error" && <div className="ws-state">
                <TxAlert title="Saved workspaces are unavailable" actions={<TxButton size="sm" onClick={onRetry}><TxIcon name="refresh" />Retry</TxButton>}>
                  {error ?? undefined}
                </TxAlert>
              </div>}
              {phase === "ready" && error && <TxAlert title="Some saved workspaces could not be read">{error}</TxAlert>}
              {shown.map(row => {
                const when = savedWhen(row.item.savedAt);
                return <div key={row.item.snapshotId} className="ws-row" role="row">
                  <button type="button" className="ws-hit" aria-label={`Open ${row.item.name}`} onClick={() => onOpen(row.item)} />
                  <Sketch tiles={row.tiles} />
                  <span className="ws-name" role="cell">
                    <span className="ws-name-line">
                      <strong title={row.item.name}>{row.item.name}</strong>
                      {row.openId && <span className="ws-open"><i />Open</span>}
                    </span>
                    <span className="ws-sub">{row.tiles ? moduleLine(row.tiles) : ""}</span>
                  </span>
                  <span role="cell" className="ws-when" title={when.full}>{when.label}</span>
                  <span role="cell" className="ws-actions">
                    <TxButton size="sm" onClick={() => onOpen(row.item)}>Open</TxButton>
                    <TxMenu label={`Actions for ${row.item.name}`} align="end" items={menu(row)}
                      trigger={<TxIconButton size="sm" icon="more" label={`More actions for ${row.item.name}`} />} />
                  </span>
                </div>;
              })}
              {phase === "ready" && !shown.length && <div className="ws-nothing">No workspace matches “{query.trim()}”</div>}
            </div>
          </div>
        </div>
      </div>}
  </div>;
}
