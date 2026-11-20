import type { Activity, OpenRecord } from "../public/ts/types";

export interface DesktopExitRequest {
  requestId: string;
  /** What quitting stops: each open workspace (platform.workspace.list_open) with what runs for it (platform.activity.list). */
  preview: { workspaces: { workspace: OpenRecord; activities: Activity[] }[] };
}

export interface TalyxDesktop {
  /** Loads the same UI with ?workspaceId=<encoded id>. */
  openWorkspaceWindow(workspaceId: string): Promise<void>;
  onWorkspaceRequested(callback: (workspaceId: string) => void): () => void;
  /** Subscribing also delivers a pending tray exit request; returns unsubscribe. */
  onExitRequested(callback: (request: DesktopExitRequest) => void): () => void;
  minimizeWindow(): Promise<void>;
  capturePanel(workspace: string, panel: string): Promise<{ path: string; bytes: number; sha256: string }>;
  toggleMaximizeWindow(): Promise<void>;
  /** The same as the system close: the window goes to the tray. */
  closeWindow(): Promise<void>;
  isWindowMaximized(): Promise<boolean>;
  onWindowStateChanged(callback: (state: { maximized: boolean }) => void): () => void;
  /** Moves the window with the mouse that is pressed now, as the system title bar does (snapping included). */
  dragWindow(): Promise<void>;
  /** Resizes the window from its top edge or a top corner with the mouse that is pressed now. */
  resizeWindow(edge: "top" | "top-left" | "top-right"): Promise<void>;
  /** Opens an existing folder in File Explorer. */
  openFolder(path: string): Promise<void>;
  /** The interface's Exit: the same path as the tray, previewing before any exit. */
  requestExit(): Promise<void>;
  confirmExit(requestId: string): Promise<void>;
  cancelExit(requestId: string): Promise<void>;
}

declare global {
  interface Window { talyxDesktop?: TalyxDesktop }
}
