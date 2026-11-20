import type { SerializedDockview } from "dockview-core";

/** One saved panel: its module (`module:<id>`) and the module's own state, opaque to the platform. */
export interface PersistedPanel {
  id: string;
  kind: string;
  state: unknown;
}

/** A saved workspace: the dock arrangement and its panels. */
export interface Workspace {
  version: number;
  layout: SerializedDockview;
  panels: PersistedPanel[];
  /** A panel's id; empty for a workspace without panels. */
  activePanelId: string;
}

export const WORKSPACE_VERSION = 6;
