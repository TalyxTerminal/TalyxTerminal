import type { SerializedDockview } from "dockview-react";

/** One panel in an open workspace. Its state is the module's own JSON, opaque here. */
export interface ShellPanel {
  id: string;
  /** `module:<id>` of the module that draws it. */
  kind: string;
  title: string;
  state?: unknown;
  /** Placement asked for by an AI opening the panel; read once, when it is added. */
}

/** An open workspace as its frame holds it. */
export interface WorkspaceState {
  workspaceId: string;
  /** The saved record it was opened from or last saved to. */
  snapshotId: string | null;
  snapshotRevision: string | null;
  name: string;
  panels: ShellPanel[];
  activePanelId: string | null;
  dockLayout: SerializedDockview | null;
  /** Counts the user's changes. Derived ones (where the user is looking) are kept for Save but not counted. */
  changes: number;
  /** `changes` as of the last save. */
  savedChanges: number;
}

export const unsaved = (workspace: WorkspaceState): boolean => workspace.changes !== workspace.savedChanges;

export function change(workspace: WorkspaceState, patch: Partial<WorkspaceState>, how?: { derived: boolean }): WorkspaceState {
  return { ...workspace, ...patch, changes: how?.derived ? workspace.changes : workspace.changes + 1 };
}

/** After a save that captured the workspace at `captured` changes: later changes stay unsaved. */
export function savedAt(workspace: WorkspaceState, captured: number, receipt: { snapshotId: string; revision: string }): WorkspaceState {
  return { ...workspace, snapshotId: receipt.snapshotId, snapshotRevision: receipt.revision, savedChanges: captured };
}

type Receipt = { snapshotId: string; revision: string };
type Commit = (update: (latest: WorkspaceState) => WorkspaceState) => void;

/** Save: the platform writes the saved state, then each module persists what the save captured. What the platform
    wrote is this workspace's saved state from then on, even when a module then fails: Save again goes over it. The
    workspace counts as saved only when both are done. */
export async function saveWorkspace(commit: Commit, captured: number, write: () => Promise<Receipt>,
  modulesSaved: (receipt: Receipt) => Promise<void>): Promise<Receipt> {
  const receipt = await write();
  commit(latest => ({ ...latest, snapshotId: receipt.snapshotId, snapshotRevision: receipt.revision }));
  await modulesSaved(receipt);
  commit(latest => savedAt(latest, captured, receipt));
  return receipt;
}

/** A saved state written or renamed from any window. */
export type SavedChange = { kind: "saved"; snapshotId: string; name: string; revision: string };

export function followSaved(workspace: WorkspaceState, saved: SavedChange): WorkspaceState {
  if (workspace.snapshotRevision === saved.revision && workspace.name === saved.name) return workspace;
  return change(workspace, { snapshotId: saved.snapshotId, snapshotRevision: saved.revision, name: saved.name }, { derived: true });
}

/** A panel that never mounted leaves; when nothing else changed meanwhile, so does the change that added it. */
export function undoFailedOpen(current: WorkspaceState, before: WorkspaceState, panelId: string): WorkspaceState {
  const panels = current.panels.filter(panel => panel.id !== panelId);
  const onlyThis = current.changes === before.changes + 1;
  return {
    ...current, panels,
    activePanelId: current.activePanelId === panelId ? before.activePanelId ?? panels[0]?.id ?? null : current.activePanelId,
    ...(onlyThis ? { changes: before.changes, dockLayout: before.dockLayout } : {}),
  };
}

/** The home window commits its frames' recovery states together. A failed or unanswered frame leaves the previous
 * recovery data intact. Each workspace is held by one window and takes its record once after reloading. */
async function recoveryTransaction<T>(operation: (store: IDBObjectStore) => () => T): Promise<T> {
  const db = await new Promise<IDBDatabase>((resolve, reject) => {
    const open = indexedDB.open("talyx-workspace-recovery", 1);
    open.onupgradeneeded = () => open.result.createObjectStore("workspaces");
    open.onsuccess = () => resolve(open.result);
    open.onerror = () => reject(open.error);
  });
  try {
    return await new Promise<T>((resolve, reject) => {
      const tx = db.transaction("workspaces", "readwrite");
      tx.onabort = () => reject(tx.error ?? new Error("Workspace recovery was interrupted."));
      try {
        const result = operation(tx.objectStore("workspaces"));
        tx.oncomplete = () => resolve(result());
      } catch (error) {
        tx.abort();
        reject(error);
      }
    });
  } finally { db.close(); }
}

export function stashUnsaved(states: WorkspaceState[]): Promise<void> {
  return recoveryTransaction(store => {
    for (const state of states) {
      if (unsaved(state)) store.put(state, state.workspaceId);
      else store.delete(state.workspaceId);
    }
    return () => undefined;
  });
}

export function takeStash(workspaceId: string): Promise<WorkspaceState | null> {
  return recoveryTransaction(store => {
    const read = store.get(workspaceId);
    store.delete(workspaceId);
    return () => read.result ?? null;
  });
}
