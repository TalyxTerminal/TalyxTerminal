import { TxAlert, TxButton, TxDialog, TxSection } from "@talyx/sdk";
import type { WorkspaceActivities } from "./workspaceActivityData";
import "./WorkspaceShell.css";

/** What closing (or exiting) stops, per workspace: each item as the module running it reported it. */
export function WorkspaceActivitySummary({ mode, workspaces, moduleName }: {
  mode: "close" | "exit"; workspaces: WorkspaceActivities[]; moduleName: (id: string) => string;
}) {
  return <div className="talyx-activity-summary">
    <p className="talyx-activity-intro">{mode === "close" ? "Closing stops the work below." : "Exiting stops the work below."}</p>
    {workspaces.filter(item => item.activities.length > 0).map(({ workspace, activities }) =>
      <TxSection key={workspace.workspaceId} className="talyx-activity-group" title={workspace.name ?? "Untitled workspace"}>
        {activities.map(activity => <div key={activity.id} className="talyx-activity-row">
          <span className="talyx-activity-title">{activity.title}</span>
          <span className="talyx-activity-detail">{moduleName(activity.module)}</span>
        </div>)}
      </TxSection>)}
  </div>;
}

export function WorkspaceCloseDialog({ mode, workspaces, busy, error, moduleName, onCancel, onConfirm, unsaved = false, onSave, deletions }: {
  mode: "close" | "exit";
  workspaces: WorkspaceActivities[];
  busy: boolean;
  error: string | null;
  moduleName: (id: string) => string;
  onCancel: () => void;
  onConfirm: () => void;
  unsaved?: boolean;
  deletions: string[];
  onSave?: () => void;
}) {
  return <TxDialog open size="standard" title={mode === "close" ? "Close workspace?" : "Exit TALYX?"}
    onOpenChange={open => { if (!open && !busy) onCancel(); }}
    footer={<><TxButton data-autofocus="" disabled={busy} onClick={onCancel}>Cancel</TxButton>
      {mode === "close" && unsaved && <TxButton disabled={busy} onClick={onConfirm}>Don't save</TxButton>}
      <TxButton variant="primary" disabled={busy} onClick={mode === "close" && unsaved ? onSave : onConfirm}>{mode === "close" ? unsaved ? "Save and close" : "Close workspace" : "Exit"}</TxButton></>}>
    {error && <TxAlert title={mode === "close" ? "Workspace could not close" : "TALYX could not exit"}>{error}</TxAlert>}
    {mode === "close" && unsaved && <p className="talyx-activity-intro">{workspaces[0].workspace.name ?? "Untitled workspace"} has unsaved changes. Save before closing?</p>}
    {workspaces.some(item => item.activities.length > 0) && <WorkspaceActivitySummary mode={mode} workspaces={workspaces} moduleName={moduleName} />}
    {deletions.length > 0 && <><p>Closing without saving deletes these items.</p>
      <ul>{deletions.map((label, index) => <li key={index}>{label}</li>)}</ul></>}
  </TxDialog>;
}
