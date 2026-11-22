import { t } from "./i18n";
import { forwardRef, useCallback, useEffect, useImperativeHandle, useState } from "react";
import { platformRuntime } from "../kernel/runtime";
import { WorkspaceCloseDialog } from "./WorkspaceDialogs";
import type { WorkspaceActivities } from "./workspaceActivityData";
import type {} from "../../../host/desktop/preload";

/* Closing a workspace (0083) or exiting the desktop app: when something would stop or the workspace has unsaved
   changes, a dialog lists what stops (as each module reports it) before it does; confirmed, it really stops. */

type Confirmation = { workspaces: WorkspaceActivities[] } & ({ mode: "close"; unsaved: boolean; deletions: string[] } | { mode: "exit"; desktopRequestId: string });
export interface WorkspaceCloseHandle { close(id: string): Promise<void> }

const moduleName = (id: string) => platformRuntime().kernel.definitions.get(id)?.name ?? id;

export const WorkspaceCloseFlow = forwardRef<WorkspaceCloseHandle, {
  /** Takes the workspace's frame down: it must not go on calling for a workspace that is closing. */
  onClosing: (id: string) => Promise<void>;
  onClosed: () => Promise<void>;
  onError: (error: unknown) => void;
  onWorkspaceAction: (id: string, action: "status" | "save") => Promise<boolean>;
}>(function WorkspaceCloseFlow({ onClosing, onClosed, onError, onWorkspaceAction }, ref) {
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const confirm = useCallback(async (request: Confirmation, save = false) => {
    setBusy(true);
    setError(null);
    try {
      if (request.mode === "close") {
        const workspaceId = request.workspaces[0].workspace.workspaceId;
        if (save) await onWorkspaceAction(workspaceId, "save");
        await onClosing(workspaceId);
        await platformRuntime().platform["workspace.close"]({ workspaceId });
        await onClosed();
      } else await window.talyxDesktop!.confirmExit(request.desktopRequestId);
      setConfirmation(current => current === request ? null : current);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      throw reason;
    } finally { setBusy(false); }
  }, [onClosing, onClosed, onWorkspaceAction]);
  const present = useCallback(async (request: Confirmation) => {
    if (!(request.mode === "close" && (request.unsaved || request.deletions.length)) && !request.workspaces.some(item => item.activities.length > 0)) { await confirm(request); return; }
    setConfirmation(request); setError(null);
  }, [confirm]);
  useImperativeHandle(ref, () => ({ close: async id => {
    const platform = platformRuntime().platform;
    const workspace = (await platform["workspace.list_open"]({})).find(record => record.workspaceId === id);
    if (!workspace) throw new Error(t("This workspace is closed already."));
    const { activities } = await platform["activity.list"]({ workspaceId: id });
    const unsaved = await onWorkspaceAction(id, "status");
    const deletions = workspace.snapshotId === null ? await platform["workspace.delete_preview"]({ workspaceId: id }) : [];
    await present({ mode: "close", workspaces: [{ workspace, activities }], unsaved: unsaved || deletions.length > 0, deletions });
  } }), [present, onWorkspaceAction]);
  useEffect(() => {
    let release: (() => void) | undefined;
    const ready = () => {
      release?.();
      release = window.talyxDesktop?.onExitRequested(request =>
        void present({ mode: "exit", workspaces: request.preview.workspaces, desktopRequestId: request.requestId }).catch(onError));
    };
    window.addEventListener("talyx-desktop-ready", ready);
    ready();
    return () => { window.removeEventListener("talyx-desktop-ready", ready); release?.(); };
  }, [present, onError]);
  const cancel = async (request: Confirmation) => {
    if (request.mode === "exit") await window.talyxDesktop!.cancelExit(request.desktopRequestId);
    setConfirmation(current => current === request ? null : current);
  };
  return confirmation && <WorkspaceCloseDialog mode={confirmation.mode} workspaces={confirmation.workspaces} busy={busy} error={error}
    unsaved={confirmation.mode === "close" && confirmation.unsaved} moduleName={moduleName}
    deletions={confirmation.mode === "close" ? confirmation.deletions : []}
    onSave={() => { void confirm(confirmation, true).catch(() => undefined); }}
    onConfirm={() => { void confirm(confirmation).catch(() => undefined); }}
    onCancel={() => { void cancel(confirmation).catch(reason => setError(String(reason))); }} />;
});
