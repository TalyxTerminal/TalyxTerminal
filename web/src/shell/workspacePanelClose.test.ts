import { afterEach, expect, it, vi } from "vitest";
import type { FrontendKernel } from "../kernel/registry";
import type { WorkspaceState } from "./workspaceChanges";

const { kernel } = vi.hoisted(() => ({ kernel: { panels: new Map(), shell: undefined } }));
vi.mock("../kernel/runtime", () => ({ platformRuntime: () => ({ kernel }) }));
vi.mock("react", async original => ({
  ...await original<typeof import("react")>(),
  useState: () => [null, vi.fn()], useRef: () => ({ current: null }),
  useEffect: (effect: () => void) => effect(), useCallback: (fn: unknown) => fn,
}));

afterEach(() => vi.unstubAllGlobals());

it.each(["block", "confirm"])("public panel close reports a %s decision", async decision => {
  vi.stubGlobal("location", { href: "http://localhost/" });
  vi.stubGlobal("window", { confirm: () => false });
  const { useFramePanels } = await import("./workspaceFramePanels");
  const ref = { sessionId: "s", panelId: "p", mountId: "m" };
  kernel.panels.set("p", { ref, instance: { prepareLeave: () => ({ decision, message: "Busy" }) } });
  const commit = vi.fn();
  useFramePanels({ current: null as WorkspaceState | null }, commit, true, vi.fn());
  const shell = (kernel as unknown as FrontendKernel).shell!;
  await expect(shell.close(ref)).rejects.toMatchObject({ code: decision === "block" ? "conflict" : "cancelled" });
  expect(commit).not.toHaveBeenCalled();
});
