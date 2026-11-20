import { useCallback, useEffect, useRef, useState } from "react";
import type { Platform } from "@talyx/sdk";
import { platformRuntime } from "../kernel/runtime";
import { ShellConfirm, ShellNameDialog } from "./ShellMenus";
import { loadSaved, restoreWorkspace, writeSaved } from "./workspacePersistence";
type SavedWorkspace = Platform.SavedInfo;

export type { SavedWorkspace };
export type CatalogPhase = "loading" | "ready" | "error";

/** The saved workspaces, newest first. */
export function useSavedWorkspaces() {
  const [saved, setSaved] = useState<SavedWorkspace[]>([]);
  const [phase, setPhase] = useState<CatalogPhase>("loading");
  const [error, setError] = useState<string | null>(null);
  const request = useRef(0);
  const refresh = useCallback(async () => {
    const current = ++request.current;
    setPhase("loading"); setError(null);
    try {
      const { saved, unreadable } = await platformRuntime().platform["workspace.list"]({});
      if (current !== request.current) return;
      setSaved(saved);
      setError(unreadable.length ? 'Unreadable workspace files: ' + unreadable.join(', ') : null);
      setPhase("ready");
    } catch (reason) {
      if (current !== request.current) return;
      setPhase("error"); setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  return { saved, phase, error, refresh };
}

const detail = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);

/** Rename and delete of a saved workspace, from the workspace list or the "+" box. */
export function SavedWorkspaceDialogs({ rename, remove, onClose, onRenamed, onDeleted, say }: {
  rename: SavedWorkspace | null;
  remove: SavedWorkspace | null;
  onClose: () => void;
  onRenamed: (item: SavedWorkspace, receipt: { snapshotId: string; revision: string }, name: string) => void;
  onDeleted: (item: SavedWorkspace) => void;
  say: (title: string, failed?: boolean) => void;
}) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<{ item: SavedWorkspace; items: string[] } | null>(null);
  const [previewError, setPreviewError] = useState("");
  useEffect(() => {
    if (!remove) return;
    let active = true;
    setPreview(null); setPreviewError("");
    platformRuntime().platform["workspace.delete_preview"]({ workspaceId: remove.workspaceId })
      .then(items => { if (active) setPreview({ item: remove, items }); })
      .catch(reason => { if (active) setPreviewError(detail(reason)); });
    return () => { active = false; };
  }, [remove]);
  useEffect(() => { setName(rename?.name ?? ""); }, [rename]);
  const commitRename = async () => {
    const item = rename!, next = name.trim();
    if (!next || next === item.name) { onClose(); return; }
    setBusy(true);
    try {
      const loaded = await loadSaved(item.snapshotId);
      if (loaded.revision !== item.revision) throw new Error("It changed since the list was read. Try again.");
      restoreWorkspace(loaded.workspace);
      const receipt = await writeSaved({ name: next, workspace: loaded.workspace as never, replace: { snapshotId: item.snapshotId, revision: loaded.revision } });
      onRenamed(item, receipt, next);
      onClose();
    } catch (reason) { say(`Couldn't rename ${item.name}: ${detail(reason)}`, true); }
    finally { setBusy(false); }
  };
  const commitDelete = async () => {
    const item = remove!;
    setBusy(true);
    try {
      await platformRuntime().platform["workspace.delete"]({ snapshotId: item.snapshotId, expectedRevision: item.revision });
      onDeleted(item);
      onClose();
    } catch (reason) { say(`Couldn't delete ${item.name}: ${detail(reason)}`, true); }
    finally { setBusy(false); }
  };
  return <>
    <ShellNameDialog open={rename !== null} mode="rename" value={name} busy={busy}
      onValue={setName} onCancel={() => { if (!busy) onClose(); }} onCommit={() => void commitRename()} />
    <ShellConfirm open={remove !== null} title={`Delete ${remove?.name ?? "workspace"}?`} confirm="Delete" busy={busy}
      disabled={preview?.item !== remove}
      onCancel={() => { if (!busy) onClose(); }} onConfirm={() => void commitDelete()}>
      <p>{previewError || (preview?.item === remove ? "Deleting this workspace removes its saved layout and contents." : "Reading workspace contents…")}</p>
      {preview?.item === remove && preview.items.length > 0 &&
        <ul>{preview.items.map((label, index) => <li key={index}>{label}</li>)}</ul>}
    </ShellConfirm>
  </>;
}
