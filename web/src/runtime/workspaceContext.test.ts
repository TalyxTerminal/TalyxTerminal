import { expect, it } from "vitest";
import type { Json, WorkspaceSaved } from "@talyx/sdk";
import { WorkspaceContextRuntime } from "./workspaceContext";

it("after a save, each module's hook gets its own panels' states as its captureState gave them", async () => {
  const runtime = new WorkspaceContextRuntime();
  const seen: Json[][] = [];
  runtime.onSaved("chart", async (saved: WorkspaceSaved) => { seen.push(saved.panels); });
  const envelope = (panelType: string, state: Json) => ({ linkageVersion: 1, panelType, stateVersion: 1, state });
  await runtime.saved({ workspaceId: "ws", contextId: "ctx", panels: [
    { kind: "module:chart", state: envelope("chart.main", { version: 1, revisions: { "chart-1": 3 } }) },
    { kind: "module:market", state: envelope("market.main", { tab: "sources" }) },
  ] });
  expect(seen).toEqual([[{ version: 1, revisions: { "chart-1": 3 } }]]);
});
