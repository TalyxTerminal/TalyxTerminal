import { describe, expect, it } from "vitest";
import { composeWorkspace, restoreWorkspace } from "./workspacePersistence";
import type { WorkspaceState } from "./workspaceChanges";

const envelope = (state: unknown) => ({ linkageVersion: 1, panelType: "gone.main", stateVersion: 1, state });
const workspace = (): WorkspaceState => ({
  workspaceId: "ctx", snapshotId: null, snapshotRevision: null, name: "Desk", activePanelId: "b", dockLayout: null, changes: 0, savedChanges: 0,
  panels: [
    { id: "a", kind: "module:gone", title: "Uninstalled", state: envelope({ kept: true }) },
    { id: "b", kind: "module:chart", title: "Chart", state: envelope({ symbol: "US:AAPL" }) },
  ],
});

describe("a saved workspace keeps every panel", () => {
  it("writes a mounted panel's current state, and an unmounted one's (its module not installed) as it was", () => {
    const saved = composeWorkspace(workspace(), null, id => id === "b" ? envelope({ symbol: "US:MSFT" }) : undefined);
    const restored = restoreWorkspace(JSON.parse(JSON.stringify(saved)));
    expect(restored.panels.map(panel => [panel.id, panel.kind, panel.title, panel.state])).toEqual([
      ["a", "module:gone", "Uninstalled", envelope({ kept: true })],
      ["b", "module:chart", "Chart", envelope({ symbol: "US:MSFT" })],
    ]);
    expect(restored.activePanelId).toBe("b");
  });
});
