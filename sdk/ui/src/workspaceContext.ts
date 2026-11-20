import type { Json, SignalValue } from "./linkage";

/** The open workspace a window shows: its id (what calls are made for, open or saved), and its saved state once saved. */
export interface WorkspaceIdentity {
  workspaceId: string;
  snapshotId: string | null;
}

/** What a module sees of its workspace: which one it is, and a hook run after the workspace is saved. */
export interface ModuleWorkspaceContext {
  readonly identity: SignalValue<WorkspaceIdentity | null>;
  /** Runs after a Save, so the module can persist what that Save captured. A failure fails the Save. */
  onSaved(hook: (saved: WorkspaceSaved) => Promise<void>): () => void;
}

/** What one module sees of a Save: its own panel states. */
export interface WorkspaceSaved {
  workspaceId: string;
  snapshotId: string;
  panels: Json[];
}
