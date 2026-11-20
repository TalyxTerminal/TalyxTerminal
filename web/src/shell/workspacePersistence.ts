import type { SerializedDockview } from "dockview-react";
import type { Platform } from "@talyx/sdk";
import { platformRuntime } from "../kernel/runtime";
import { boundedModuleState } from "../runtime/moduleState";
import { WORKSPACE_VERSION, type Workspace } from "../workspace/types";
import { layoutSummary } from "./dockLayout";
import type { ShellPanel, WorkspaceState } from "./workspaceChanges";
type Saved = Platform.Saved;
type SavedWorkspace = Platform.SavedWorkspace;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === "object" && !Array.isArray(value);

/** A new panel of the panel type the module offers for opening, with no state yet. */
export function freshPanel(owner: string, title?: string): ShellPanel {
  const kernel = platformRuntime().kernel;
  const type = kernel.launcherPanel(owner);
  if (!type) throw new Error(`Module '${owner}' offers no panel to open`);
  return { id: `${owner}-${crypto.randomUUID().slice(0, 8)}`, kind: `module:${owner}`, title: title?.trim() || kernel.definitions.get(owner)!.name,
    state: { panelType: type, state: null } };
}

function defaultDockLayout(panels: readonly ShellPanel[]): SerializedDockview {
  return {
    grid: {
      root: { type: "branch", data: panels.map((panel, index) => ({ type: "leaf" as const, data: { views: [panel.id], activeView: panel.id, id: `group-${index}` } })) },
      height: 1000, width: 1000, orientation: "HORIZONTAL" as SerializedDockview["grid"]["orientation"],
    },
    panels: Object.fromEntries(panels.map(panel => [panel.id, { id: panel.id, contentComponent: "module", title: panel.title }])) as SerializedDockview["panels"],
    activeGroup: panels.length > 0 ? "group-0" : undefined,
  };
}

const samePanels = (layout: SerializedDockview, panels: readonly ShellPanel[]) => {
  const actual = Object.keys(layout.panels ?? {}).sort(), expected = panels.map(panel => panel.id).sort();
  return actual.length === expected.length && actual.every((id, index) => id === expected[index]);
};

/** The dock layout with each panel's entry: the whole panel while it is drawn, only its id when kept. */
function withPanels(layout: SerializedDockview | null, panels: readonly ShellPanel[], drawn: boolean): SerializedDockview {
  const source = layout && samePanels(layout, panels) ? layout : defaultDockLayout(panels);
  const { popoutGroups: _popouts, ...rest } = source;
  return {
    ...rest,
    panels: Object.fromEntries(panels.map(panel => {
      const { minimumWidth: _width, minimumHeight: _height, ...prior } = (isRecord(source.panels[panel.id]) ? source.panels[panel.id] : {}) as Record<string, unknown>;
      return [panel.id, { ...prior, id: panel.id, contentComponent: "module", title: panel.title, params: drawn ? { panel } : { panelId: panel.id } }];
    })) as SerializedDockview["panels"],
  };
}

export const hydrateDockLayout = (layout: SerializedDockview | null, panels: readonly ShellPanel[]) => withPanels(layout, panels, true);
/** The dock layout as the Host keeps it for an open workspace: panel ids only, the panels themselves are beside it. */
export const keptDockLayout = (layout: SerializedDockview | null, panels: readonly ShellPanel[]) => layout ? withPanels(layout, panels, false) : null;

/** What Save writes: the arrangement and each panel's live state (read from its mounted panel when there is one). */
export function composeWorkspace(workspace: WorkspaceState, layout: SerializedDockview | null, liveState: (panelId: string) => unknown): Workspace {
  const panels = workspace.panels.map(panel => ({ id: panel.id, kind: panel.kind, state: boundedModuleState(liveState(panel.id) ?? panel.state ?? null) }));
  // Dockview's own layout leaves some fields undefined; the saved record is plain JSON.
  return JSON.parse(JSON.stringify({
    version: WORKSPACE_VERSION,
    layout: withPanels(layout, workspace.panels, false),
    panels,
    activePanelId: panels.some(panel => panel.id === workspace.activePanelId) ? workspace.activePanelId! : panels[0]?.id ?? "",
  })) as Workspace;
}

export function restoreWorkspace(loaded: unknown): Pick<WorkspaceState, "panels" | "activePanelId" | "dockLayout"> {
  if (!isRecord(loaded) || loaded.version !== WORKSPACE_VERSION) throw new Error(`Workspace isn't a v${WORKSPACE_VERSION} workspace; save a new one.`);
  if (!Array.isArray(loaded.panels) || !isRecord(loaded.layout) || !isRecord(loaded.layout.grid) || !isRecord(loaded.layout.panels)) throw new Error("Workspace is damaged.");
  const layout = loaded.layout as unknown as SerializedDockview;
  const panels: ShellPanel[] = loaded.panels.map((saved: unknown) => {
    if (!isRecord(saved) || typeof saved.id !== "string" || typeof saved.kind !== "string" || !saved.kind.startsWith("module:")) throw new Error("Workspace has a damaged panel.");
    const title = isRecord(layout.panels[saved.id]) ? (layout.panels[saved.id] as { title?: unknown }).title : undefined;
    return { id: saved.id, kind: saved.kind, title: typeof title === "string" && title.trim() ? title : saved.kind.slice("module:".length), state: boundedModuleState(saved.state ?? null) };
  });
  if (!samePanels(layout, panels)) throw new Error("Workspace layout doesn't match its panels.");
  const active = typeof loaded.activePanelId === "string" && panels.some(panel => panel.id === loaded.activePanelId) ? loaded.activePanelId : null;
  return { panels, activePanelId: active, dockLayout: hydrateDockLayout(layout, panels) };
}

export async function loadSaved(snapshotId: string): Promise<SavedWorkspace> {
  return platformRuntime().platform["workspace.load"]({ snapshotId });
}

/** Saves a workspace's state, with the layout summary the workspace list draws: over its saved state (`replace`, with
    the revision read) or as its first one. Saved from the home window without `replace`, it is a new workspace's. */
export async function writeSaved(input: { name: string; workspace: Workspace; replace: { snapshotId: string; revision: string | null } | null }): Promise<Saved> {
  const kinds = new Map(input.workspace.panels.map(panel => [panel.id, panel.kind]));
  return await platformRuntime().platform["workspace.save"]({
    name: input.name, workspace: input.workspace as unknown as SavedWorkspace["workspace"],
    summary: layoutSummary(input.workspace.layout, id => kinds.get(id)!.slice("module:".length)),
    ...(input.replace ? { snapshotId: input.replace.snapshotId, expectedRevision: input.replace.revision } : {}),
  });
}
