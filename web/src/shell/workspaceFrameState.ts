import { t } from "./i18n";
import { useCallback, useEffect, useRef, useState } from "react";
import type { JsonValue } from "@talyx/sdk-ui";
import { platformRuntime } from "../kernel/runtime";
import { followSaved, takeStash, unsaved, type SavedChange, type WorkspaceState } from "./workspaceChanges";
import { hydrateDockLayout, keptDockLayout, loadSaved, restoreWorkspace } from "./workspacePersistence";
import { workspaceFrameId, workspaceWindowChannel, workspaceWindowCommand } from "./workspaceWindows";

/* One workspace frame's state: opening it, keeping the Host's copy of it current (so a reload or another window
   opens it the same), and the panels' live state for Save. */

/** What this frame kept in the tab before the page reloaded for a restarted Host; taken once per page. */
const frameId = workspaceFrameId();
const restartStash = frameId ? takeStash(frameId) : null;
export const reloading = { forHost: false };

/** A frame does not reload itself when the Host restarts (its connection may come back before the home window's):
    the home window asks every frame to keep its unsaved work in the tab, then reloads the whole page. */
export function leaveHostRestartToHome(bridge: { onHostRestart: () => void }): void {
  bridge.onHostRestart = () => window.parent.postMessage({ channel: workspaceWindowChannel, restart: true }, location.origin);
}

if (workspaceFrameId()) leaveHostRestartToHome(platformRuntime().bridge);

export const detail = (reason: unknown) => reason instanceof Error ? reason.message : String((reason as { message?: string })?.message ?? reason);

/** The workspace as the Host last had it (or as this tab kept it before a Host restart), else its saved state. */
async function openingState(workspaceId: string): Promise<WorkspaceState> {
  const keptState = await restartStash;
  if (keptState) return { ...keptState, dockLayout: hydrateDockLayout(keptState.dockLayout, keptState.panels) };
  const opened = await platformRuntime().platform["workspace.list_open"]({});
  const record = opened.find(item => item.workspaceId === workspaceId);
  if (!record) throw new Error(t("This workspace is closed."));
  const kept = record.presentation as WorkspaceState | null | undefined;
  if (kept) return { ...kept, workspaceId, dockLayout: hydrateDockLayout(kept.dockLayout, kept.panels) };
  const blank: WorkspaceState = { workspaceId, snapshotId: null, snapshotRevision: null, name: record.name ?? t("Untitled"), panels: [], activePanelId: null,
    dockLayout: null, changes: 0, savedChanges: 0 };
  if (!record.snapshotId) return blank;
  const saved = await loadSaved(record.snapshotId);
  return { ...blank, ...restoreWorkspace(saved.workspace), snapshotId: saved.snapshotId, snapshotRevision: saved.revision, name: saved.name };
}

const moduleInstances = (workspace: WorkspaceState) => {
  const counts: Record<string, number> = {};
  for (const panel of workspace.panels) { const owner = panel.kind.slice("module:".length); counts[owner] = (counts[owner] ?? 0) + 1; }
  return counts;
};

export function useWorkspaceFrameState(workspaceId: string, say: (title: string, failed?: boolean) => void) {
  const kernel = platformRuntime().kernel;
  const [workspace, setWorkspace] = useState<WorkspaceState | null>(null);
  const ref = useRef<WorkspaceState | null>(null);
  const commit = useCallback((next: (current: WorkspaceState) => WorkspaceState) => {
    const current = ref.current;
    if (!current) return;
    const value = next(current);
    if (value === current) return;
    ref.current = value; setWorkspace(value);
  }, []);
  /** Each mounted panel's current state, read when saving. */
  const readers = useRef(new Map<string, () => JsonValue>());
  const live = useCallback((current: WorkspaceState): WorkspaceState => ({
    ...current, panels: current.panels.map(panel => ({ ...panel, state: readers.current.get(panel.id)?.() ?? panel.state })),
  }), []);

  // Open: what the Host or this tab kept, else the saved state.
  useEffect(() => {
    let disposed = false;
    kernel.displayed.set(false);
    void openingState(workspaceId).then(async next => {
      if (disposed) return;
      ref.current = next; setWorkspace(next);
      // Calls from this window name its workspace themselves (the bridge adds it), so modules may call at once.
      kernel.workspace.bind({ workspaceId, snapshotId: next.snapshotId });
      await platformRuntime().ready();
      if (!disposed) await workspaceWindowCommand({ kind: "ready", id: workspaceId });
    }).catch(reason => say(t("Workspace not opened: {reason}", { reason: detail(reason) }), true));
    return () => { disposed = true; };
  }, [workspaceId, kernel, say]);

  // Follow writes from every window so the next save uses the current revision.
  useEffect(() => {
    const subscription = platformRuntime().bridge.subscribe("platform.workspace.changes", {}, value => {
      const item = value as unknown as SavedChange & { workspaceId: string };
      if (item.workspaceId === workspaceId && item.kind === "saved") commit(current => followSaved(current, item));
    });
    return () => { void subscription.then(value => value.close()); };
  }, [workspaceId, commit]);

  // Its saved state changes on its first save, and when that is deleted.
  const snapshotId = workspace?.snapshotId ?? null;
  useEffect(() => {
    if (kernel.workspace.identity.get() && kernel.workspace.identity.get()!.snapshotId !== snapshotId) kernel.workspace.bind({ workspaceId, snapshotId });
  }, [kernel, workspaceId, snapshotId]);

  // The Host keeps each open workspace as it is now, and which window shows it with how many panels of each module.
  // One update in flight, and only the newest waits: a panel that changes many times a second must not queue a call
  // per change.
  const hostSync = useRef<{ busy: boolean; next: (() => Promise<unknown>) | null; shown: string }>({ busy: false, next: null, shown: "" });
  useEffect(() => {
    if (!workspace) return;
    const sync = hostSync.current, platform = platformRuntime().platform;
    const presentation = JSON.parse(JSON.stringify({ ...workspace, dockLayout: keptDockLayout(workspace.dockLayout, workspace.panels) }));
    const counts = moduleInstances(workspace), shown = JSON.stringify(counts);
    sync.next = async () => {
      // The saved snapshot belongs to the Host. A frame update queued before Save must not clear it afterwards.
      await platform["workspace.open"]({ workspaceId, name: workspace.name, presentation });
      if (shown !== sync.shown) { await platform["workspace.shown"]({ workspaceId, moduleInstances: counts }); sync.shown = shown; }
      await workspaceWindowCommand({ kind: "changed" });
    };
    if (sync.busy) return;
    sync.busy = true;
    void (async () => {
      let failed: unknown = null;
      while (sync.next) {
        const send = sync.next;
        sync.next = null;
        try { await send(); failed = null; } catch (reason) { failed = reason; }
      }
      sync.busy = false;
      if (failed !== null && !reloading.forHost) say(t("Workspace update failed: {reason}", { reason: detail(failed) }), true);
    })();
  }, [workspaceId, say, workspace]);

  // Leaving with unsaved changes, or with a module that wants to keep its input, asks first.
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (reloading.forHost || !ref.current) return;
      if (!unsaved(ref.current) && kernel.prepareLeave("window_close").every(decision => decision.decision === "allow")) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [kernel]);

  return { workspace, ref, commit, readers, live };
}
