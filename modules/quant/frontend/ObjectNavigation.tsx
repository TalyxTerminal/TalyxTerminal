import { TxIcon, TxIconButton, TxMenu, TxSearch, type TxMenuItem } from "@talyx/sdk";
import { formatNumber } from "./format";
import { KINDS, active, percent, type Kind, type ObjectList, type ObjectSummary, type Preset, type RunSummary } from "./objects";
import type { LibraryRow } from "./library/api";

export type ObjectAction = "rename" | "move" | "delete";
export type FolderAction = "create" | "rename" | "delete";

/** Things by kind, then by folder; this workspace by default, every workspace with "All". */
/** Every kind's presets as menu items after the empty one, as on the home page: a copy of each is the user's own. */
export const presetItems = (presets: Preset[], kind: Kind, onCreate: (preset: string) => void): TxMenuItem[] => presets.filter(preset => preset.kind === kind).map(preset =>
  ({id: "preset-" + preset.id, label: `From a preset: ${preset.label}`, icon: KINDS[kind].icon, onSelect: () => onCreate(preset.id)}));

export function ObjectNavigation({list, all, onAll, query, onQuery, current, workspaceId, onOpen, onCreate, presets, onFolder, onObject, library, libraryCurrent, onLibrary, onExpand, onHome}: {
  list: ObjectList | null; all: boolean; onAll(value: boolean): void; query: string; onQuery(value: string): void;
  current?: string; workspaceId: string | null; onOpen(id: string): void; onCreate(kind: Kind, folder?: string, preset?: string): void; presets: Preset[];
  onFolder(action: FolderAction, kind: Kind, name?: string): void; onObject(action: ObjectAction, item: ObjectSummary): void;
  /** The library is shared by every workspace: its items, the one open (null for the list, undefined when the library is not open). */
  library: LibraryRow[] | null; libraryCurrent: string | null | undefined; onLibrary(itemId?: string): void;
  /** Open every object, by kind, as a page. */
  onExpand(): void;
  /** Back to Quant's home page, from anywhere the list opens. */
  onHome?(): void;
}) {
  const creatable = list ? list.kinds.filter(item => item.creatable) : [];
  const newItems: TxMenuItem[] = [
    ...creatable.flatMap(item => [{id: item.kind, label: KINDS[item.kind].label, icon: KINDS[item.kind].icon, onSelect: () => onCreate(item.kind)},
      ...presetItems(presets, item.kind, preset => onCreate(item.kind, undefined, preset))]),
    ...creatable.map((item, index) => ({id: "folder-" + item.kind, label: creatable.length > 1 ? `${KINDS[item.kind].label} folder` : "Folder", icon: "folder" as const,
      separated: index === 0, onSelect: () => onFolder("create", item.kind)})),
  ];
  return <div className="qx-nav-body">
    <div className="qx-nav-head">
      <TxMenu label="Workspace filter" items={[
        {id: "this", label: "This workspace", checked: !all, onSelect: () => onAll(false)},
        {id: "all", label: "All workspaces", checked: all, onSelect: () => onAll(true)},
      ]} trigger={<button type="button" className="qx-scope">{all ? "All workspaces" : "This workspace"}<TxIcon name="chevron" /></button>} />
      <span className="tx-spacer" />
      <TxIconButton size="sm" label="All objects" icon="list" onClick={onExpand} />
      {newItems.length > 0 && <TxMenu label="New" align="end" items={newItems} trigger={<TxIconButton size="sm" label="New" icon="plus" />} />}
    </div>
    <TxSearch className="qx-nav-search" value={query} onValueChange={onQuery} placeholder="Search" />
    <div className="qx-nav-list" role="tree" aria-label="Quant">
      {onHome && <div className="qx-nav-item qx-nav-heading"><button type="button" className="qx-nav-open qx-nav-section" onClick={onHome}><TxIcon name="flask" /><span>Home</span></button></div>}
      {list && list.kinds.map(section => {
        if (section.kind === "library") return <LibrarySection key="library" items={library} current={libraryCurrent} onLibrary={onLibrary} />;
        const items = list.items.filter(item => item.kind === section.kind);
        const folders = list.folders.filter(folder => folder.kind === section.kind);
        return <div key={section.kind} role="group" aria-label={KINDS[section.kind].plural} data-empty={section.count === 0 && !section.creatable}>
          <div className="qx-nav-section">
            <span>{KINDS[section.kind].plural}</span><span className="qx-count">{section.count}</span><span className="tx-spacer" />
            {section.creatable && <TxMenu label={`New ${KINDS[section.kind].label.toLowerCase()}`} align="end" items={[
              {id: "new", label: KINDS[section.kind].label, icon: KINDS[section.kind].icon, onSelect: () => onCreate(section.kind)},
              ...presetItems(presets, section.kind, preset => onCreate(section.kind, undefined, preset)),
              {id: "folder", label: "Folder", icon: "folder", separated: true, onSelect: () => onFolder("create", section.kind)},
            ]} trigger={<TxIconButton size="sm" className="qx-nav-add" label={`New ${KINDS[section.kind].label.toLowerCase()}`} icon="plus" />} />}
          </div>
          {items.filter(item => !item.folder).map(item => <Row key={item.id} item={item} current={current} workspaceId={workspaceId} onOpen={onOpen} onObject={onObject} />)}
          {folders.map(folder => <div key={folder.name} role="group" aria-label={folder.name}>
            <div className="qx-nav-folder">
              <TxIcon name="folder" /><span className="qx-name">{folder.name}</span>
              <TxMenu label={`Folder ${folder.name}`} align="end" items={[
                ...(section.creatable ? [{id: "new", label: `New ${KINDS[folder.kind].label.toLowerCase()} here`, icon: "plus" as const, onSelect: () => onCreate(folder.kind, folder.name)}] : []),
                {id: "rename", label: "Rename…", icon: "edit", onSelect: () => onFolder("rename", folder.kind, folder.name)},
                // only an empty folder is deleted: one that holds something offers no Delete
                ...(folder.count === 0 ? [{id: "delete", label: "Delete", icon: "trash" as const, tone: "danger" as const, onSelect: () => onFolder("delete", folder.kind, folder.name)}] : []),
              ]} trigger={<TxIconButton size="sm" className="qx-row-more" label={`Folder ${folder.name} actions`} icon="more" />} />
            </div>
            {items.filter(item => item.folder === folder.name).map(item => <Row key={item.id} item={item} nested current={current} workspaceId={workspaceId} onOpen={onOpen} onObject={onObject} />)}
          </div>)}
        </div>;
      })}
    </div>
  </div>;
}

/** A factor shows its rank IC, everything else its total return. */
function lastNumber(run: RunSummary) {
  const rankIc = (run as {numbers?: {rankIc?: number | null}}).numbers?.rankIc;
  if (typeof rankIc === "number") return <span className="qx-meta tx-num" data-sign={Math.sign(rankIc)} title="Rank IC">{rankIc > 0 ? "+" : rankIc < 0 ? "−" : ""}{Math.abs(rankIc).toFixed(4)}</span>;
  const total = run.metrics?.totalReturn;
  return typeof total === "number" ? <span className="qx-meta tx-num" data-sign={Math.sign(total)}>{percent(total, 1)}</span> : null;
}

function Row({item, nested, current, workspaceId, onOpen, onObject}: {item: ObjectSummary; nested?: boolean; current?: string; workspaceId: string | null;
  onOpen(id: string): void; onObject(action: ObjectAction, item: ObjectSummary): void}) {
  const run = item.lastRun;
  const meta = run === null ? null : active(run) ? <span className="qx-dot" data-tone="busy" aria-label="Running" />
    : run.state === "failed" ? <span className="qx-meta" data-tone="warn">Failed</span>
      : run.state === "completed" ? lastNumber(run) : null;
  return <div className="qx-nav-item" data-nested={nested} aria-current={item.id === current} role="treeitem">
    <button type="button" className="qx-nav-open" onClick={() => onOpen(item.id)} title={item.name}>
      <span className="qx-name">{item.name}</span>
      {item.workspaceId !== workspaceId && <span className="qx-tag">Other workspace</span>}
      {meta}
    </button>
    <TxMenu label={`${item.name} actions`} align="end" items={[
      {id: "rename", label: "Rename…", icon: "edit", onSelect: () => onObject("rename", item)},
      {id: "move", label: "Move to folder…", icon: "folder", onSelect: () => onObject("move", item)},
      {id: "delete", label: "Delete…", icon: "trash", tone: "danger", onSelect: () => onObject("delete", item)},
    ]} trigger={<TxIconButton size="sm" className="qx-row-more" label={`${item.name} actions`} icon="more" />} />
  </div>;
}

/** The library's items under its heading; the heading opens the whole list. */
function LibrarySection({items, current, onLibrary}: {items: LibraryRow[] | null; current: string | null | undefined; onLibrary(itemId?: string): void}) {
  return <div role="group" aria-label="Library">
    <div className="qx-nav-item qx-nav-heading" aria-current={current === null}>
      <button type="button" className="qx-nav-open qx-nav-section" onClick={() => onLibrary()}>
        <span>Library</span><span className="qx-count">{items === null ? "" : formatNumber(items.length)}</span></button>
    </div>
    {(items ?? []).map(item => <div key={item.itemId} className="qx-nav-item" aria-current={item.itemId === current} role="treeitem">
      <button type="button" className="qx-nav-open" onClick={() => onLibrary(item.itemId)} title={item.name}>
        <span className="qx-name">{item.name}</span>
        {(item.kind === "model" || item.kind === "data") && <span className="qx-tag">{item.kind === "model" ? "Model" : "Data"}</span>}
        {item.attention ? <span className="qx-meta" data-tone="warn">{item.stateLabel}</span> : item.state === "ok" || item.state === "late" ? <span className="qx-meta tx-num">{item.freq === "Daily" ? "1d" : item.freq}</span>
          : <span className="qx-meta">{item.stateLabel}</span>}
      </button>
    </div>)}
  </div>;
}
