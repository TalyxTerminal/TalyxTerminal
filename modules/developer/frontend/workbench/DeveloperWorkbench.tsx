import { t } from "../i18n";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { TxAlert, TxButton, TxDialog, TxIcon, TxIconButton, TxMenu, TxSheet, TxSplit, TxToolbarDivider, type ModuleContext, type TxMenuItem } from "@talyx/sdk";
import { documents, type CheckpointReference, type DocumentSource } from "../documents/service";
import { CompareDialog, type CompareRequest } from "../documents/Compare";
import { openInVsCode, projectModule, removeProject, touchProject, type ProjectModule, type WorkspaceProject } from "./api";
import { message } from "./errors";
import { ProjectDialog } from "./ProjectDialog";
import { Recovery } from "./Recovery";
import { DeveloperHelp } from "./Help";
import { FileHistory } from "./FileHistory";
import { FileTree } from "./FileTree";
import { EditorPane } from "./EditorPane";
import { ModuleBar } from "./ModuleBar";
import "./workbench.css";

type Utility = "recovery" | "help" | "history";
export interface WorkbenchState { activeProjectId?: string | null; sources?: DocumentSource[]; activeSource?: DocumentSource | null; treeWidth?: number; checkpoint?: CheckpointReference }
const utilityNames: Record<Utility, string> = { recovery: t("Recovery"), help: t("Developer help"), history: t("File history") };

export function DeveloperWorkbench({ ctx, onStateChange }: { ctx: ModuleContext; onStateChange?(state: WorkbenchState): void }) {
  useSyncExternalStore(documents.watch, documents.state, documents.state);
  const initial = useRef((ctx.initialState ?? {}) as WorkbenchState);
  const projects = [...documents.projects.values()];
  const [restored, setRestored] = useState(false);
  const [utility, setUtility] = useState<Utility | null>(null), [dialog, setDialog] = useState<"new" | "open" | null>(null);
  const [error, setError] = useState<string | null>(null), [problem, setProblem] = useState<string | null>(null), [compare, setCompare] = useState<CompareRequest | null>(null);
  const [removing, setRemoving] = useState<WorkspaceProject | null>(null), [removingBusy, setRemovingBusy] = useState(false), [removeError, setRemoveError] = useState<string | null>(null);
  const [treeWidth, setTreeWidth] = useState(initial.current.treeWidth ?? 240);
  const [module, setModule] = useState<ProjectModule | null>(null);
  const project = projects.find(item => item.projectId === documents.activeProjectId);
  const active = documents.activeDocumentId ? documents.snapshot(documents.activeDocumentId) : undefined;
  const unsaved = active ? documents.isDirty(active.documentId) : false;
  const run = (work: () => Promise<unknown>) => { setError(null); void work().catch(reason => setError(message(reason))); };
  const openProject = async (item: WorkspaceProject) => { setUtility(null); documents.activateProject((await touchProject(ctx, item.projectId)).projectId); };
  useEffect(() => {
    const report = () => {
      const current = documents.activeDocumentId ? documents.snapshot(documents.activeDocumentId) : undefined;
      const selected = documents.activeProjectId ? documents.projects.get(documents.activeProjectId) : undefined;
      const dirty = current ? documents.isDirty(current.documentId) : false;
      const place = { project: selected?.name ?? t("No project"), file: current?.name ?? t("No file") };
      ctx.view.report({ summary: dirty ? t("{project} · {file} · unsaved changes", place) : t("{project} · {file}", place),
        refs: { projectId: selected?.projectId ?? null, documentId: current?.documentId ?? null, version: current?.version ?? null, unsaved: dirty },
        facts: [current?.conflict ? "Changed on disk under unsaved edits" : dirty ? "Unsaved" : "Saved", ...(current ? [`File: ${current.uri}`, `Version: ${current.version}`] : [])],
        // The file on screen, as the open target (the manifest's) that leads here.
        openTargets: current?.source.kind === "file" ? [{ kind: "file", key: `file:${current.source.uri}`, label: current.name }] : [] });
    };
    report();
    return ctx.view.onFlush(async () => { await documents.flush(); report(); });
  }, [ctx, project?.projectId, project?.name, active?.documentId, active?.name, active?.version, unsaved]);
  useEffect(() => {
    let alive = true;
    void (async () => {
      await documents.refreshProjects();
      if (initial.current.checkpoint) {
        const result = await documents.restoreCheckpoint({ checkpoint: initial.current.checkpoint });
        if (result.documents.some(item => item.status === "conflict")) setUtility("recovery");
        if (result.documents.some(item => item.status === "unavailable")) setError(t("Some files saved with the workspace could not be restored. Your current edits are kept."));
      }
      for (const source of initial.current.sources ?? []) await documents.open(source).catch(reason => setError(message(reason)));
      if (initial.current.activeSource) await documents.open(initial.current.activeSource);
      if (!documents.activeProjectId && initial.current.activeProjectId) documents.activateProject(initial.current.activeProjectId);
    })().catch(reason => { if (alive) setError(message(reason)); }).finally(() => { if (alive) setRestored(true); });
    return () => { alive = false; };
  }, [ctx]);
  useEffect(() => {
    setModule(null); setProblem(null);
    if (project) void projectModule(ctx, project.projectId).then(setModule, reason => setError(message(reason)));
  }, [ctx, project?.projectId]);
  const sources = documents.tabs.map(id => documents.snapshot(id).source);
  useEffect(() => {
    if (restored) onStateChange?.({ activeProjectId: project?.projectId ?? null, sources, activeSource: active?.source ?? null, treeWidth, ...(initial.current.checkpoint ? { checkpoint: initial.current.checkpoint } : {}) });
  }, [restored, project?.projectId, JSON.stringify(sources), active?.documentId, treeWidth, onStateChange]);
  const closeProject = (item: WorkspaceProject) => {
    for (const id of documents.tabs.filter(id => documents.snapshot(id).context.projectId === item.projectId)) documents.closeTab(id);
    if (documents.activeProjectId === item.projectId) documents.activateProject(null);
    setUtility(null);
  };
  const confirmRemove = async (mode: "forget" | "trash") => {
    if (!removing) return;
    const item = removing;
    setRemovingBusy(true); setRemoveError(null);
    try {
      if (documents.dirty().some(entry => entry.context.projectId === item.projectId)) throw new Error(t("Save or discard this project's unsaved files before removing it."));
      await removeProject(ctx, item, mode);
      closeProject(item);
      await documents.refreshProjects();
      setRemoving(null);
    } catch (reason) { setRemoveError(message(reason)); }
    finally { setRemovingBusy(false); }
  };
  /** A module written in TALYX's modules folder is gone once uninstalled: its project leaves the list with it. */
  const forget = async (item: WorkspaceProject) => {
    await removeProject(ctx, item, "forget");
    closeProject(item);
    await documents.refreshProjects();
  };
  const created = async (projectId: string, file?: string) => {
    await documents.refreshProjects();
    documents.activateProject(projectId);
    if (file) await documents.open({ kind: "file", uri: file, projectId });
  };
  const save = async () => { if (active && !active.readOnly) await documents.save(active.documentId); };
  // A document to review shows on its tab and above the editor; one that has no tab (closed with unsaved edits) is named here.
  const conflicts = [...documents.entries.keys()].filter(id => documents.review(id) && !documents.tabs.includes(id));
  const conflictTitles = new Set(conflicts.map(id => documents.review(id)));
  const openConflicts = () => { for (const id of [...conflicts].reverse()) documents.show(id); };
  const recentProjects = [...projects].sort((a, b) => (b.lastOpened ?? "").localeCompare(a.lastOpened ?? ""));
  const switchItems: TxMenuItem[] = [
    ...recentProjects.map(item => ({ id: item.projectId, label: item.name, checked: project?.projectId === item.projectId, onSelect: () => run(() => openProject(item)) })),
    { id: "new", label: t("New TALYX module"), separated: true, onSelect: () => setDialog("new") },
    { id: "open", label: t("Open folder"), onSelect: () => setDialog("open") },
    { id: "remove", label: t("Remove project…"), separated: true, disabled: !project || project.readOnly, onSelect: () => { if (project) { setRemoveError(null); setRemoving(project); } } },
  ];
  const utilities: TxMenuItem[] = [
    ...(["history", "recovery", "help"] as Utility[]).map(name => ({ id: name, label: utilityNames[name], onSelect: () => setUtility(name) })),
    { id: "close", label: t("Close project"), disabled: !project, onSelect: () => { if (project) closeProject(project); } },
  ];
  return <div className="developer-workspace">
    <header className="developer-workspace-toolbar tx-glass">
      <TxMenu label={t("Switch project")} trigger={<TxButton variant="quiet" className="developer-switch"><span>{project?.name ?? t("Developer")}</span><TxIcon name="chevron-down" className="developer-switch-chevron" /></TxButton>} items={switchItems} />
      <TxMenu label={t("Workspace tools")} trigger={<TxIconButton label={t("Workspace tools")} icon="settings" />} items={utilities} />
      <span className="tx-spacer" />
      {project && module && <ModuleBar key={project.projectId} ctx={ctx} projectId={project.projectId} module={module} onError={setError} onProblem={setProblem}
        onUninstalled={module.installed ? () => forget(project) : undefined} />}
      {project && <><TxToolbarDivider /><TxButton onClick={() => run(() => openInVsCode(ctx, project.projectId))}>{t("Open in VS Code")}</TxButton></>}
      <TxButton disabled={!active || active.readOnly || !unsaved} onClick={() => run(save)}>{t("Save")}</TxButton>
    </header>
    {(error || documents.error) && <TxAlert title={t("Developer needs attention")} actions={<TxButton size="sm" onClick={() => { setError(null); documents.error = null; documents.changed(); }}>{t("Dismiss")}</TxButton>}>{error ?? documents.error}</TxAlert>}
    {problem && module && <TxAlert title={t("{name} couldn't start", { name: module.name })}><pre className="developer-project-output">{problem}</pre></TxAlert>}
    {!!conflicts.length && <TxAlert title={conflictTitles.size === 1 ? [...conflictTitles][0]! : t("Changed elsewhere")} actions={<TxButton onClick={openConflicts}>{t("Review")}</TxButton>}>{conflicts.map(id => documents.snapshot(id).name).join(", ")}</TxAlert>}
    {!!documents.checkpointConflicts.length && <TxAlert title={t("Files saved with the workspace differ")} actions={<TxButton onClick={() => setUtility("recovery")}>{t("Compare versions")}</TxButton>}>{t("Your current edits and the saved versions are both kept.")}</TxAlert>}
    <TxSplit sideLabel={t("Project files")} width={treeWidth} onWidth={setTreeWidth} min={160} max={480} mainMin={320} sideHidden={!project} side={project && <FileTree ctx={ctx} project={project} />}>
      <EditorPane compare={setCompare} onError={setError} />
    </TxSplit>
    <TxDialog open={!!removing} onOpenChange={open => { if (!open && !removingBusy) setRemoving(null); }} title={t("Remove project")} footer={<><TxButton disabled={removingBusy} onClick={() => setRemoving(null)}>{t("Cancel")}</TxButton><TxButton disabled={removingBusy} onClick={() => void confirmRemove("forget")}>{t("Remove from list")}</TxButton><TxButton variant="primary" disabled={removingBusy || removing?.status !== "available"} onClick={() => void confirmRemove("trash")}>{t("Move to Recycle Bin")}</TxButton></>}>
      <p>{removing?.name}</p><p style={{ overflowWrap: "anywhere" }}>{removing?.root}</p>
      <p>{t("Remove from list keeps the files. Move to Recycle Bin removes the whole project folder.")}</p>
      {removeError && <TxAlert title={t("Project was not removed")}>{removeError}</TxAlert>}
    </TxDialog>
    {dialog && <ProjectDialog key={dialog} ctx={ctx} mode={dialog} onClose={() => setDialog(null)} onCreated={created} />}
    <TxSheet open={!!utility} onOpenChange={open => { if (!open) setUtility(null); }} title={utility ? utilityNames[utility] : ""}>
      {utility === "recovery" && <Recovery compare={setCompare} onOpened={() => setUtility(null)} />}
      {utility === "help" && <DeveloperHelp ctx={ctx} onOpened={() => setUtility(null)} />}
      {utility === "history" && <FileHistory ctx={ctx} compare={setCompare} />}
    </TxSheet>
    <CompareDialog request={compare} onClose={() => setCompare(null)} />
  </div>;
}
