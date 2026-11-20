import { useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";
import { ContractError, TxButton, TxDialog, TxIcon, TxInput, TxPopover, TxSelect, TxToastProvider, type Json, type LeaveDecision, type ModuleContext, type TxMenuItem } from "@talyx/sdk";
import { ObjectNavigation, type FolderAction, type ObjectAction } from "./ObjectNavigation";
import { QuantHome } from "./home/QuantHome";
import { ObjectsPage } from "./home/ObjectsPage";
import { LibraryPage, useLibrary } from "./library/LibraryPage";
import { NotebookPage } from "./NotebookPage";
import { PackagesPage } from "./PackagesPage";
import { FactorPage } from "./factor/FactorPage";
import { ModelPage } from "./model/ModelPage";
import { StrategyPage } from "./strategy/StrategyPage";
import { AllocationPage } from "./allocation/AllocationPage";
import { KINDS, api as objectApi, useEvent, useThrottled, type Kind, type ObjectDetail, type ObjectList, type ObjectRow, type ObjectSummary, type Preset } from "./objects";
import { libraryApi } from "./library/api";
import type { SweepFields } from "./sweep";
import type { PanelState } from "./types";
import "./quantShell.css";

/** What a Quant panel remembers. `objectId` is all another module needs to open one thing in it. */
export interface ShellState {
  objectId?: string; runId?: string; all?: boolean; tab?: "code" | "report" | "runs"; reportView?: "report" | "output" | "sweep";
  bottom?: number; maximized?: boolean; asset?: string; report?: PanelState;
  /** A strategy's parameter sweep being set up: whether the row shows ranges, and each number parameter's range. */
  sweep?: {on: boolean; fields: SweepFields};
  /** Research pages: the code or runs beside the report, and its share of the page. */
  side?: "code" | "runs"; sideSize?: number;
  /** A factor's Work: the tab under the result, the result's height, whether the tab fills the work area, and lists typed for a sweep. */
  factorTab?: "runs" | "values" | "similar"; factorHeight?: number; factorFull?: boolean; factorText?: Record<string, string>;
  /** A factor's page shows Work (the default) or Results; the Results tab, and the runs whose long–short the groups chart draws beside it (up to 4). */
  factorView?: "work" | "results"; factorResults?: string; factorVs?: string[];
  /** A model's page shows Work (the default) or Results; the Results tab, and the run whose publish sheet is open. */
  modelView?: "work" | "results"; modelResults?: string; modelPublish?: boolean; modelTree?: boolean;
  /** A strategy's page: Work or Results, the tab under the result, its height, whether the tab fills the work area, the Results section on screen and the runs drawn beside it. */
  allocationView?: "work" | "results"; allocationTab?: "runs"; allocationResults?: string;
  strategyView?: "work" | "results"; strategyTab?: "runs" | "trades" | "rebalances" | "holdings" | "contributions" | "log"; strategyHeight?: number; strategyFull?: boolean; strategyResults?: string; strategyVs?: string[];
  /** Its code area: the file tree open, and the trade picked (the chart and the log follow it). */
  strategyTree?: boolean; strategyTrade?: number;
  /** The stretch whose trades the Trades table lists and the chart shows: a drawdown (its number, from the high to the recovery) or a month or
   * year picked on Results (its label), in ms. */
  strategyDrawdown?: {n?: number; label?: string; from: number; to: number};
  /** The library instead of an object: its list, or one item at one version. */
  library?: boolean; itemId?: string; versionId?: string; libraryTab?: string;
  /** Every object, by kind, instead of the home. */
  objects?: boolean; objectsSort?: {kind: Kind; label: string; direction: 1 | -1};
  /** The package list instead of an object; `from` is the object to go back to. */
  packages?: boolean; from?: string;
}
export interface ShellHandle { navigate(state: ShellState): void }
/**
 * Where the user is looking in the panel: the run shown (a new run followed after Run included), Work or Results, a tab,
 * the row picked. Moving among these is remembered but is not an unsaved change; anything else in the state is.
 */
const BROWSING = new Set<string>(["runId", "all", "tab", "reportView", "asset", "factorTab", "factorView", "factorResults",
  "modelView", "modelResults", "allocationView", "allocationTab", "allocationResults", "strategyView", "strategyTab", "strategyResults",
  "strategyTrade", "strategyDrawdown", "libraryTab"]);
export function browsingOnly(before: ShellState, after: ShellState): boolean {
  const keys = new Set([...Object.keys(before), ...Object.keys(after)]) as Set<keyof ShellState>;
  return [...keys].every(key => BROWSING.has(key) || JSON.stringify(before[key]) === JSON.stringify(after[key]));
}
/**
 * What the panel itself provides beyond its context: the module's context, whose routes outlive the panel, and how the panel
 * is being left. A notebook sets `guard` to say whether leaving needs the user's word; `reason` is "close" when the panel or
 * the notebook is closed for good (its kernel may stop), "detach" when only this view goes (the workspace is replaced).
 */
export interface PanelHost {
  module: ModuleContext;
  leave: {reason: "close" | "detach"; confirmed: boolean; guard: (() => LeaveDecision) | null};
}

/** Reads a saved panel state; anything from the old research shell is dropped, so such a panel opens on the Quant home. */
export function readShellState(value: Json): ShellState {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return {};
  const keys: (keyof ShellState)[] = ["objectId", "runId", "all", "tab", "reportView", "bottom", "maximized", "asset", "report", "sweep",
    "side", "sideSize", "factorTab", "factorHeight", "factorFull", "factorText", "factorView", "factorResults", "factorVs", "modelView", "modelResults", "modelPublish", "modelTree", "strategyView", "strategyTab", "strategyHeight", "strategyFull", "strategyResults", "strategyVs", "allocationView", "allocationTab", "allocationResults", "strategyTree", "strategyTrade", "library", "itemId", "versionId", "libraryTab", "objects", "objectsSort", "packages", "from"];
  if ("workId" in value || "topicId" in value || "workbench" in value) return {};
  // An open target (the manifest's panel description): "research", "object:<id>" or a bare id, "run:<id>".
  const target = value.objectId;
  if (typeof target === "string" && Object.keys(value).length === 1) {
    if (target === "research") return {};
    if (target.startsWith("run:")) return {runId: target.slice(4), tab: "report"};
    return {objectId: target.startsWith("object:") ? target.slice(7) : target};
  }
  const state = Object.fromEntries(keys.filter(key => key in value).map(key => [key, value[key]])) as ShellState;
  return state;
}
/** Leaving one thing for another forgets what was on screen for the first. */
const forObject = (state: ShellState, objectId: string | undefined, runId?: string): ShellState =>
  ({all: state.all, bottom: state.bottom, sideSize: state.sideSize, factorHeight: state.factorHeight, ...(objectId ? {objectId} : {}), ...(runId ? {runId, tab: "report" as const} : {})});

type Dialog = {kind: "rename" | "move" | "delete"; item: ObjectSummary} | {kind: "folder"; action: FolderAction; folderKind: Kind; name?: string};

export function QuantShell({ctx, host, stateRef, register}: {ctx: ModuleContext; host: PanelHost; stateRef: MutableRefObject<ShellState>; register(handle: ShellHandle | null): void}) {
  const api = useMemo(() => objectApi(ctx), [ctx]);
  const libraryApiOf = useMemo(() => libraryApi(ctx), [ctx]);
  const [state, setState] = useState(stateRef.current);
  const [list, setList] = useState<ObjectList | null>(null);
  const [query, setQuery] = useState("");
  const [detail, setDetail] = useState<ObjectDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [navOpen, setNavOpen] = useState(false);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [wide, setWide] = useState(false);
  const [leaving, setLeaving] = useState<{message: string; go(): void} | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const root = useRef<HTMLDivElement>(null);
  const fail = (reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason));
  useEffect(() => {void api.presets().then(value => setPresets(value.items), fail);}, []);
  // Panel state is saved as JSON, so a field set to undefined is dropped rather than kept.
  const replace = (next: ShellState) => {
    const clean = Object.fromEntries(Object.entries(next).filter(([, value]) => value !== undefined)) as ShellState;
    const derived = browsingOnly(stateRef.current, clean);
    stateRef.current = clean; setState(clean); ctx.panel?.stateChanged({derived});
  };
  const update = (patch: Partial<ShellState>) => replace({...stateRef.current, ...patch});
  /** Going elsewhere in the panel closes the notebook shown, as closing the panel does: with cells running it asks first. */
  const guarded = (go: () => void) => {
    const guard = host.leave.guard;
    const decision = guard === null ? {decision: "allow" as const} : guard();
    const close = (confirmed: boolean) => {host.leave.reason = "close"; host.leave.confirmed = confirmed; go();};
    if (decision.decision === "confirm") setLeaving({message: decision.message ?? "", go: () => close(true)});
    else close(false);
  };
  const openObject = (objectId: string | undefined, runId?: string) => {
    setNavOpen(false); setError(null);
    // Choosing what is already open keeps its view as it is.
    if (objectId !== undefined && objectId === stateRef.current.objectId && runId === undefined && !stateRef.current.packages) return;
    const go = () => replace(forObject(stateRef.current, objectId, runId));
    if (objectId === stateRef.current.objectId && !stateRef.current.packages) go(); else guarded(go);
  };
  const openLibrary = (itemId?: string, versionId?: string) => {
    setNavOpen(false); setError(null);
    guarded(() => replace({...forObject(stateRef.current, undefined), library: true, ...(itemId ? {itemId} : {}), ...(versionId ? {versionId} : {})}));
  };
  const openObjects = () => {
    setNavOpen(false); setError(null);
    guarded(() => replace({...forObject(stateRef.current, undefined), objects: true}));
  };
  const openPackages = () => {
    setNavOpen(false); setError(null);
    // A notebook stays open, its kernel and variables kept, while packages install in a panel of their own; it then asks for the restart.
    if (detail !== null && detail.kind === "notebook" && detail.id === stateRef.current.objectId) {
      void ctx.platform.panels.open({type: "quant.workspace", state: {packages: true}}).catch(fail);
      return;
    }
    const from = stateRef.current.objectId;
    guarded(() => replace({...forObject(stateRef.current, undefined), packages: true, ...(from !== undefined ? {from} : {})}));
  };
  /** A publication's version, shown in the library. */
  const showVersion = (versionId: string) => void api.libraryVersion(versionId).then(version => openLibrary(version.itemId, versionId), fail);
  const openRun = (objectId: string, runId: string) => openObject(objectId, runId);
  /** From a notebook, a run or an object made from it opens in a Quant panel of its own, as Packages does: the notebook, its kernel and its variables stay
   *  (opening the run replaced the notebook and stopped its kernel with 60 MB of variables in it). */
  const openBeside = (objectId: string, runId?: string) => void ctx.platform.panels.open({type: "quant.workspace", state: {objectId, ...(runId ? {runId, tab: "report"} : {})}}).catch(fail);
  useEffect(() => {
    register({navigate: next => openObject(next.objectId, next.runId)});
    return () => register(null);
  }, []);
  useEffect(() => {
    const element = root.current!;
    const observer = new ResizeObserver(() => setWide(element.clientWidth >= 1150));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // Navigation: this workspace or all of them, refreshed when anything changes.
  const all = state.all === true;
  const workspaceId = ctx.workspace.identity.get()?.workspaceId ?? null;
  const loadList = () => api.list({all, ...(query.trim() ? {query: query.trim()} : {})}).then(setList, fail);
  useEffect(() => {void loadList();}, [all, query]);
  const library = useLibrary(ctx, libraryApiOf, query);
  // Runs report several times a second while they go: the list is read once a second at most.
  const listChanged = useThrottled(() => void loadList());
  useEvent(ctx, "quant.object_changed", listChanged, () => {
    setError(null);
    void loadList();
    const objectId = stateRef.current.objectId;
    if (objectId !== undefined) void api.read(objectId).then(setDetail, fail);
  });
  useEvent(ctx, "quant.object_run_changed", listChanged);

  // A result view opened by run only finds its object first.
  useEffect(() => {
    if (state.objectId === undefined && state.runId !== undefined) void api.run(state.runId).then(run => openObject(run.objectId, run.id), () => openObject(undefined));
  }, [state.runId, state.objectId]);
  // The open object, registered with Developer once; a deleted or unknown one (an old panel) leaves for the home.
  useEffect(() => {
    let live = true;
    setDetail(null);
    if (state.objectId === undefined) return;
    void api.open(state.objectId).then(value => {if (live) setDetail(value);}, reason => {
      if (!live) return;
      if (reason instanceof ContractError && reason.code === "not_found") openObject(undefined); else fail(reason);
    });
    return () => {live = false;};
  }, [state.objectId]);
  useEvent(ctx, "quant.object_changed", payload => {
    const change = payload as {objectId: string; version: number};
    if (detail === null || change.objectId !== detail.id || change.version <= detail.version) return;
    void api.read(detail.id).then(setDetail, reason => {if (reason instanceof ContractError && reason.code === "not_found") openObject(undefined); else fail(reason);});
  });
  const libraryItem = state.library && state.itemId !== undefined && library.items !== null ? library.items.find(item => item.itemId === state.itemId) ?? null : null;
  // The panel's tab (the drafts): Quant on home and all objects, Library on the library with or without an item open.
  const name = state.packages ? "Packages" : state.objects ? "Quant" : state.library ? "Library"
    : detail === null ? "Quant" : detail.name;
  useEffect(() => {
    ctx.panel?.setTitle(name);
    if (state.packages) {
      ctx.view.report({summary: "Quant packages", refs: {objectId: null, runId: null}, facts: ["Quant packages"], openTargets: []});
      return;
    }
    if (state.library) {
      ctx.view.report({summary: libraryItem === null ? "Quant library" : `Library item ${libraryItem.name}`, refs: {objectId: null, runId: null},
        facts: libraryItem === null ? ["Quant library"] : [`Library item: ${libraryItem.name}`, `Status: ${libraryItem.stateLabel}`], openTargets: []});
      return;
    }
    ctx.view.report({summary: detail === null ? "Quant home" : `${KINDS[detail.kind].label} ${detail.name}`,
      refs: {objectId: detail === null ? null : detail.id, runId: state.runId ?? null},
      facts: detail === null ? ["Quant home"] : [`${KINDS[detail.kind].label}: ${detail.name}`],
      // What is on screen, as the open targets (the manifest's) that lead here.
      openTargets: detail === null ? [{kind: "home", key: "research", label: "Quant home"}]
        : [{kind: detail.kind, key: `object:${detail.id}`, label: detail.name}, ...(state.runId ? [{kind: "run", key: `run:${state.runId}`, label: `${detail.name} run`}] : [])]});
  }, [detail?.id, name, state.runId, state.library, state.packages]);

  const create = async (kind: Kind, folder?: string, preset?: string) => {
    const created = await api.create(kind, folder, preset);
    openObject(created.id);
    // The code opens for an object written as code; a standard flow (model, allocation) and a one-line formula start with it closed.
    if (kind === "strategy") update({tab: "code"});
    else if (created.hasCode === true) update({side: "code"});
  };
  const onFolder = (action: FolderAction, folderKind: Kind, name?: string) => {
    if (action === "delete") void api.deleteFolder(folderKind, name!).catch(fail);
    else setDialog({kind: "folder", action, folderKind, name});
  };
  const onObject = (action: ObjectAction, item: ObjectSummary) => setDialog({kind: action, item});
  const menu: TxMenuItem[] = detail === null ? [] : [
    {id: "packages", label: "Packages", icon: "box", detail: "Python packages for notebooks, research and strategies", separated: true, onSelect: openPackages},
    {id: "rename", label: "Rename…", icon: "edit", separated: true, onSelect: () => onObject("rename", detail)},
    {id: "move", label: "Move to folder…", icon: "folder", onSelect: () => onObject("move", detail)},
    {id: "delete", label: "Delete…", icon: "trash", tone: "danger", onSelect: () => onObject("delete", detail)},
  ];
  const navigation = <ObjectNavigation list={list} all={all} onAll={value => update({all: value})} query={query} onQuery={setQuery} current={state.library ? undefined : state.objectId}
    workspaceId={workspaceId} onOpen={id => openObject(id)} onCreate={(kind, folder, preset) => void create(kind, folder, preset).catch(fail)} presets={presets} onFolder={onFolder} onObject={onObject}
    library={library.items} libraryCurrent={state.library ? state.itemId ?? null : undefined} onLibrary={itemId => openLibrary(itemId)} onExpand={openObjects}
    onHome={() => {setNavOpen(false); openObject(undefined);}} />;
  const icon = state.packages ? "box" : state.objects ? "list" : state.library ? "bookmark" : detail === null ? "flask" : KINDS[detail.kind].icon;
  const titleText = <><TxIcon name={icon} /><span className="qx-title-name">{name}</span></>;
  // An object's page fills the panel: the navigation opens from the button before its name and lays over the page.
  const onObjectPage = state.objectId !== undefined && !state.library && !state.packages && !state.objects;
  const title = onObjectPage
    ? <><TxPopover open={navOpen} onOpenChange={setNavOpen} label="Quant navigation" className="qx-nav-pop"
        trigger={<button type="button" className="qx-navbtn" title="All objects" aria-label="All objects"><TxIcon name="list" /></button>}>{navigation}</TxPopover>
      <button type="button" className="qx-title" disabled={detail === null} onClick={() => {if (detail !== null) onObject("rename", detail);}} title="Rename">{titleText}</button></>
    : wide
    ? <button type="button" className="qx-title" onClick={() => {if (detail !== null && !state.library && !state.packages) onObject("rename", detail); else if (!state.library) openObject(undefined);}}
      title={detail !== null && !state.library && !state.packages ? "Rename" : name}>{titleText}</button>
    : <TxPopover open={navOpen} onOpenChange={setNavOpen} label="Quant navigation" className="qx-nav-pop"
      trigger={<button type="button" className="qx-title">{titleText}<TxIcon name="chevron" className="qx-title-chevron" /></button>}>{navigation}</TxPopover>;
  return <TxToastProvider><div className="qx" ref={root}>
    {/* Home, the library and all objects have their own heads (output/quant-design): the navigation column stays with the pages that have none. */}
    {wide && state.packages && <aside className="qx-nav">{navigation}</aside>}
    <main className="qx-main">
      {error && <div className="qx-page-error" role="alert"><span>{error}</span><TxButton size="sm" variant="quiet" onClick={() => setError(null)}>Dismiss</TxButton></div>}
      {state.packages ? <PackagesPage ctx={ctx} api={api} title={title} onBack={state.from !== undefined ? () => openObject(state.from) : undefined} />
        : state.objects ? <ObjectsPage initialSort={state.objectsSort} rows={(list?.items ?? []) as ObjectRow[]} all={all} onAll={value => update({all: value})} onBack={() => openObject(undefined)}
          onLibrary={() => openLibrary()} onOpen={row => openObject(row.id, row.representative.run ?? undefined)} />
        : state.library ? <LibraryPage ctx={ctx} state={state} update={update} onHome={() => openObject(undefined)} onOpenObject={id => openObject(id)} onOpenVersion={showVersion} onOpenRun={openRun} />
        : state.objectId !== undefined && detail !== null ? detail.kind === "notebook"
          ? <NotebookPage key={detail.id} ctx={ctx} host={host} api={api} detail={detail} title={title} menu={menu} onOpenObject={openBeside}
            panelKey={ctx.panelRef ? ctx.panelRef.panelId : ctx.viewRef!.viewInstanceId} onPackages={openPackages} />
          : detail.kind === "strategy"
          ? <StrategyPage key={detail.id} ctx={ctx} api={api} detail={detail} onDetail={setDetail} state={state} update={update} title={title} menu={menu}
            onLibrary={showVersion}
            onOpenObject={id => {openObject(id); update({tab: "code"});}} />
          : detail.kind === "allocation"
          ? <AllocationPage key={detail.id} ctx={ctx} api={api} detail={detail} onDetail={setDetail} state={state} update={update} title={title} menu={menu} />
          : detail.kind === "factor"
          ? <FactorPage key={detail.id} ctx={ctx} api={api} detail={detail} onDetail={setDetail} state={state} update={update} title={title} menu={menu} onOpenObject={id => openObject(id)}
            starters={presets.filter(item => item.kind === "factor")} />
          : <ModelPage key={detail.id} ctx={ctx} api={api} detail={detail} onDetail={setDetail} state={state} update={update} title={title} menu={menu} />
        : state.objectId !== undefined ? <div className="qx-bar">{title}</div>
          : <><QuantHome ctx={ctx} list={list} presets={presets} onOpen={id => openObject(id)}
              onCreate={(kind, preset) => void create(kind, undefined, preset).catch(fail)} onOpenObjects={openObjects} onOpenLibrary={openLibrary} onOpenVersion={showVersion} /></>}
    </main>
    {leaving !== null && <TxDialog open title="Leave the notebook?" onOpenChange={open => {if (!open) setLeaving(null);}}
      footer={<><TxButton onClick={() => setLeaving(null)}>Stay</TxButton><TxButton variant="primary" onClick={() => {const {go} = leaving; setLeaving(null); go();}}>Stop and leave</TxButton></>}>
      <p className="qx-pre-line">{leaving.message}</p>
    </TxDialog>}
    {dialog !== null && <ShellDialog dialog={dialog} list={list} onClose={() => setDialog(null)} onDone={action => action.then(() => setDialog(null), fail)} api={api}
      onDeleted={id => {if (id === stateRef.current.objectId) openObject(undefined);}} />}
  </div></TxToastProvider>;
}

function ShellDialog({dialog, list, api, onClose, onDone, onDeleted}: {dialog: Dialog; list: ObjectList | null; api: ReturnType<typeof objectApi>;
  onClose(): void; onDone(action: Promise<unknown>): void; onDeleted(id: string): void}) {
  const folders = list === null ? [] : list.folders.filter(item => item.kind === (dialog.kind === "folder" ? dialog.folderKind : dialog.item.kind));
  const initial = dialog.kind === "rename" ? dialog.item.name : dialog.kind === "move" ? dialog.item.folder ?? "" : dialog.kind === "folder" ? dialog.name ?? "" : "";
  const [text, setText] = useState(initial);
  const [fresh, setFresh] = useState(false);
  const [asked, setAsked] = useState(false);
  const field = useRef<HTMLInputElement>(null);
  const valid = dialog.kind === "delete" || dialog.kind === "move" || text.trim().length > 0;
  const submit = () => {
    if (!valid) {setAsked(true); field.current?.focus(); return;}  // a name is needed: the box says so and takes the cursor
    if (dialog.kind === "folder") onDone(dialog.action === "create" ? api.createFolder(dialog.folderKind, text.trim()) : api.renameFolder(dialog.folderKind, dialog.name!, text.trim()));
    else if (dialog.kind === "rename") onDone(api.update(dialog.item.id, {name: text.trim()}));
    else if (dialog.kind === "move") onDone(api.update(dialog.item.id, {folder: text.trim() || null}));
    else {const id = dialog.item.id; onDone(api.remove(id).then(() => onDeleted(id)));}
  };
  const titles = {rename: "Rename", move: "Move to folder", delete: "Delete", folder: dialog.kind === "folder" && dialog.action === "rename" ? "Rename folder" : "New folder"};
  return <TxDialog open onOpenChange={open => {if (!open) onClose();}} title={titles[dialog.kind]}
    footer={<><TxButton onClick={onClose}>Cancel</TxButton><TxButton variant="primary" onClick={submit}>{dialog.kind === "delete" ? "Delete" : dialog.kind === "move" ? "Move" : dialog.kind === "folder" && dialog.action === "create" ? "Create" : "Rename"}</TxButton></>}>
    {dialog.kind === "delete" ? <p>Delete {dialog.item.name}? Its code is removed with it.</p>
      : dialog.kind === "move" && !fresh ? <TxSelect aria-label="Folder" value={text} onChange={event => {if (event.target.value === "\u0000new") {setFresh(true); setText("");} else setText(event.target.value);}}>
        <option value="">No folder</option>{folders.map(item => <option key={item.name} value={item.name}>{item.name}</option>)}<option value={"\u0000new"}>New folder…</option>
      </TxSelect>
        : <TxInput ref={field} autoFocus aria-label="Name" placeholder={dialog.kind === "move" || dialog.kind === "folder" ? "Folder name" : "Name"} value={text} aria-invalid={asked && !valid || undefined}
          onChange={event => setText(event.target.value)} onKeyDown={event => {if (event.key === "Enter") submit();}} />}
  </TxDialog>;
}
