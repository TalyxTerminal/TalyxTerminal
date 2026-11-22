import { t } from "./i18n";
import { lazy, Suspense, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { SerializedDockview } from "dockview-react";
import { failure, useTxToast } from "@talyx/sdk";
import type { JsonValue } from "@talyx/sdk-ui";
import { platformRuntime } from "../kernel/runtime";
import { RegisteredPanelHost } from "../kernel/RegisteredPanelHost";
import { ExperienceShellStyles } from "./ExperienceShellStyles";
import { ExperienceDockview } from "./ExperienceDockview";
import { ModulePresentationContext } from "./dockParts";
import { ModuleMenu, ShellConfirm, WorkspaceLauncher, type ShellSavedWorkspace } from "./ShellMenus";
import { shellModuleChoices, useShellAlerts, useWorkspaceTabs, type ShellAttention, type ShellWorkspaceTab } from "./shellChrome";
import { SavedWorkspaceDialogs, useSavedWorkspaces, type SavedWorkspace } from "./savedWorkspaces";
import { change, saveWorkspace, unsaved, type ShellPanel } from "./workspaceChanges";
import { composeWorkspace, writeSaved } from "./workspacePersistence";
import { detail, useWorkspaceFrameState } from "./workspaceFrameState";
import { useFramePanels, useFrameRequests } from "./workspaceFramePanels";
import { workspaceFrameId, workspaceWindowChannel, workspaceWindowCommand, type OpenWorkspace } from "./workspaceWindows";

const WorkspaceCommandHeader = lazy(() => import("./WorkspaceCommandHeader"));

function PanelView({ panel, onActivate, onTitle, onState, registerState }: {
  panel: ShellPanel; onActivate: () => void; onTitle: (title: string) => void;
  onState: (state: JsonValue, how?: { derived: boolean }) => void; registerState: (id: string, read: (() => JsonValue) | null) => void;
}) {
  const presentation = useContext(ModulePresentationContext);
  return <RegisteredPanelHost id={panel.id} owner={panel.kind.slice("module:".length)} state={panel.state} title={panel.title}
    active={presentation.active} visible={presentation.visible} onActivate={onActivate} onTitleChange={onTitle}
    onStateChange={onState} registerState={registerState} />;
}

/** One open workspace in its own frame: its top bar, its panels, and saving it. */
export function WorkspaceFrame() {
  const workspaceId = workspaceFrameId()!;
  const kernel = platformRuntime().kernel;
  const toast = useTxToast();
  const say = useCallback((title: string, failed = false) => toast({ title, tone: failed ? "failed" : "info" }), [toast]);
  const { workspace, ref, commit, readers, live } = useWorkspaceFrameState(workspaceId, say);
  const layoutReader = useRef<(() => SerializedDockview) | null>(null);
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);
  const [openWorkspaces, setOpenWorkspaces] = useState<OpenWorkspace[]>([]);
  const tabs = useWorkspaceTabs(openWorkspaces.map(record => ({ id: record.workspaceId, name: record.name })));
  const alerts = useShellAlerts();
  const moduleChoices = shellModuleChoices(alerts.faults);
  const catalog = useSavedWorkspaces();
  const [renaming, setRenaming] = useState<SavedWorkspace | null>(null);
  const [deleting, setDeleting] = useState<SavedWorkspace | null>(null);
  const [nameMode, setNameMode] = useState<"save" | "rename" | null>(null);
  const [nameInput, setNameInput] = useState("");
  const [launcherOpen, setLauncherOpen] = useState(false);
  const [moduleMenu, setModuleMenu] = useState<HTMLElement | null>(null);
  const panels = useFramePanels(ref, commit, workspace !== null, say);

  // The open workspaces (for the capsules) and whether this frame is the one shown come from the home window.
  useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (event.origin !== location.origin || event.source !== window.parent || event.data?.channel !== workspaceWindowChannel) return;
      if (event.data.opened && !event.data.requestId) setOpenWorkspaces(event.data.opened);
      if (typeof event.data.displayed === "boolean") kernel.displayed.set(event.data.displayed);
    };
    window.addEventListener("message", receive);
    void workspaceWindowCommand({ kind: "list" }).then(setOpenWorkspaces).catch(reason => say(detail(reason), true));
    return () => window.removeEventListener("message", receive);
  }, [kernel, say]);

  const save = useCallback(async (): Promise<void> => {
    if (savingRef.current) throw failure("conflict", t("The workspace is already saving"));
    savingRef.current = true; setSaving(true);
    try {
      for (const panel of [...kernel.panels.values()]) if (!panel.scope.disposed) await panel.instance.prepareSave?.();
      const current = ref.current!, captured = current.changes;
      const composed = composeWorkspace(current, layoutReader.current?.() ?? current.dockLayout, id => readers.current.get(id)?.());
      await saveWorkspace(commit, captured, () => writeSaved({ name: current.name, workspace: composed,
        replace: current.snapshotId ? { snapshotId: current.snapshotId, revision: current.snapshotRevision } : null }),
        // Each module persists what this Save captured.
        receipt => kernel.workspace.saved({ workspaceId, snapshotId: receipt.snapshotId, panels: composed.panels }));
      void catalog.refresh();
      say(unsaved(ref.current!) ? t("Saved; newer changes remain unsaved.") : t("Workspace saved."));
    } finally { savingRef.current = false; setSaving(false); }
  }, [catalog.refresh, commit, workspaceId, kernel, readers, ref, say]);
  useFrameRequests(workspaceId, ref, workspace !== null, live, save);

  const saveFromUser = useCallback(() => {
    if (savingRef.current || !ref.current) return;
    if (!ref.current.snapshotId) { setNameInput(ref.current.name === t("Untitled") ? "" : ref.current.name); setNameMode("save"); return; }
    void save().catch(reason => say(t("Workspace wasn't saved: {reason}", { reason: detail(reason) }), true));
  }, [ref, save, say]);
  const commitName = useCallback(() => {
    const name = nameInput.trim();
    if (!name || !ref.current) return;
    const mode = nameMode;
    setNameMode(null);
    if (name !== ref.current.name) commit(current => change(current, { name }));
    if (mode === "save") void save().catch(reason => say(t("Workspace wasn't saved: {reason}", { reason: detail(reason) }), true));
  }, [commit, nameInput, nameMode, ref, save, say]);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); saveFromUser(); }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [saveFromUser]);

  const command = useCallback((value: Parameters<typeof workspaceWindowCommand>[0]) => { void workspaceWindowCommand(value).catch(reason => say(detail(reason), true)); }, [say]);
  const goHomeTo = useCallback((event: "talyx:shell-home" | "talyx:shell-community", moduleId: string | null = null) => {
    void workspaceWindowCommand({ kind: "home" }).then(() => window.parent.dispatchEvent(new CustomEvent(event, { detail: moduleId }))).catch(reason => say(detail(reason), true));
  }, [say]);
  const openFault = useCallback((item: ShellAttention) => goHomeTo("talyx:shell-community", item.id), [goHomeTo]);
  const selectTab = useCallback((tab: ShellWorkspaceTab) => command({ kind: "select", id: tab.id }), [command]);
  const openModulePicker = useCallback((anchor?: HTMLElement | null) => {
    setModuleMenu(anchor ?? document.querySelector<HTMLElement>(".talyx-ws-tab[aria-pressed='true']"));
  }, []);

  if (!workspace) return <ExperienceShellStyles />;
  const openSnapshots = new Set(openWorkspaces.map(record => record.snapshotId).filter(Boolean));
  const launcherItems: ShellSavedWorkspace[] = catalog.saved.map(item => ({ id: item.snapshotId, name: item.name, savedAt: item.savedAt, open: openSnapshots.has(item.snapshotId) }));
  const savedItem = (item: ShellSavedWorkspace) => catalog.saved.find(entry => entry.snapshotId === item.id)!;
  const launcher = <WorkspaceLauncher saved={launcherItems} phase={catalog.phase} error={catalog.error} onRetry={() => void catalog.refresh()}
    onNew={() => command({ kind: "open", snapshotId: null, name: t("Untitled"), presentation: {
      workspaceId: crypto.randomUUID(), snapshotId: null, snapshotRevision: null, name: t("Untitled"), panels: [], activePanelId: null, dockLayout: null, changes: 0, savedChanges: 0,
    } })}
    onOpen={item => command({ kind: "open", snapshotId: item.id, name: item.name, presentation: null })}
    onRename={item => setRenaming(savedItem(item))} onDelete={item => setDeleting(savedItem(item))}
    external={{ open: launcherOpen, onOpenChange: setLauncherOpen }} />;
  const { panelConfirm, setPanelConfirm } = panels;

  return <div data-screen="workspace" id="talyx-layout-manager-candidate">
    <ExperienceShellStyles />
    <div className="product">
      <Suspense fallback={null}>
        <WorkspaceCommandHeader tabs={tabs} attention={alerts.faults} settingsAlert={alerts.settings} modules={moduleChoices}
          onCommunity={() => goHomeTo("talyx:shell-community")} onFault={openFault} addWorkspace={launcher} onOpenLauncher={() => setLauncherOpen(true)}
          name={workspace.name} saving={saving} nameMode={nameMode} nameInput={nameInput} onBack={() => goHomeTo("talyx:shell-home")}
          onError={message => say(message, true)} onNotice={message => say(message)} workspaceId={workspaceId} onSelectWorkspace={selectTab}
          onOpenNewWindow={() => command({ kind: "new-window", id: workspaceId })}
          onCloseWorkspace={() => {
            if (savingRef.current) { say(t("The workspace is still saving. Close it once that finishes.")); return; }
            command({ kind: "close", id: workspaceId });
          }}
          onAddModule={openModulePicker} onAddModuleKind={panels.addModule} onSave={saveFromUser}
          onStartRename={() => { setNameInput(workspace.name); setNameMode("rename"); }}
          onNameInput={setNameInput} onCommitName={commitName} onCancelName={() => setNameMode(null)} />
      </Suspense>
      <ModuleMenu anchor={moduleMenu} modules={moduleChoices} onOpenChange={open => { if (!open) setModuleMenu(null); }} onPick={panels.addModule} />
      <ShellConfirm open={panelConfirm !== null} title={panelConfirm?.title ?? ""} confirm={t("Close")}
        onCancel={() => { panelConfirm?.resolve(false); setPanelConfirm(null); }}
        onConfirm={() => { panelConfirm?.resolve(true); setPanelConfirm(null); }}>
        <p>{panelConfirm?.message}</p>
      </ShellConfirm>
      <SavedWorkspaceDialogs rename={renaming} remove={deleting} say={say} onClose={() => { setRenaming(null); setDeleting(null); }}
        onRenamed={() => { say(t("Workspace renamed.")); void catalog.refresh(); }}
        onDeleted={() => { say(t("Workspace deleted.")); void catalog.refresh(); }} />
      <main>
        <section className="screen is-active">
          <div aria-label={t("TALYX workspace")} data-workspace-screen="operate"
            style={{ background: "var(--tx-panel)", fontFamily: "var(--tx-font)", height: "100%", minHeight: 0, minWidth: 0, overflow: "hidden" }}>
            {/* Dockview's sashes and drag overlays carry their own z-index; isolating the dock keeps them under dialogs such as Settings. */}
            <div className="workspace-shell workspace-shell-dock-only" aria-label={t("Workspace module dock")} data-workspace-surface="dockview"
              style={{ height: "100%", minHeight: 0, minWidth: 0, overflow: "hidden", isolation: "isolate" }}>
              <ExperienceDockview panels={workspace.panels} activePanelId={workspace.activePanelId} initialLayout={workspace.dockLayout}
                  renderModule={panel => <PanelView panel={panel} onActivate={() => panels.activatePanel(panel.id)}
                    onTitle={title => commit(current => change(current, { panels: current.panels.map(item => item.id === panel.id && item.title !== title ? { ...item, title } : item) }, { derived: true }))}
                    onState={(state, how) => commit(current => {
                      const target = current.panels.find(item => item.id === panel.id);
                      if (!target || JSON.stringify(target.state) === JSON.stringify(state)) return current;
                      return change(current, { panels: current.panels.map(item => item.id === panel.id ? { ...item, state } : item) }, how);
                    })}
                    registerState={(id, read) => { if (read) readers.current.set(id, read); else readers.current.delete(id); }} />}
                  onActivatePanel={panels.activatePanel} onRemovePanel={panels.removePanel} onRequestRemovePanel={panels.requestPanelClose}
                  onLayoutChange={(dockLayout, source) => commit(current => change(current, { dockLayout }, { derived: source === "panel-sync" }))}
                  registerLayoutReader={reader => { layoutReader.current = reader; }}
                  onAddModule={openModulePicker} modules={moduleChoices} onAddModuleKind={panels.addModule} />
            </div>
          </div>
        </section>
      </main>
    </div>
  </div>;
}
