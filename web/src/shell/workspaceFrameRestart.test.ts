import { afterEach, beforeAll, expect, it, vi } from "vitest";
import { WorkspaceWindowController } from "./workspaceWindowController";

const { stash, bridge } = vi.hoisted(() => ({ stash: vi.fn(), bridge: { onHostRestart: () => location.reload() } }));
vi.mock("../kernel/runtime", () => ({ platformRuntime: () => ({ bridge }) }));
vi.mock("./workspaceChanges", async original => ({
  ...await original<typeof import("./workspaceChanges")>(), stashUnsaved: stash, takeStash: () => Promise.resolve(null),
}));

const frameLocation = Object.assign(new URL("http://127.0.0.1/?workspaceFrame=1&workspaceId=w1"), { reload: vi.fn() });
beforeAll(async () => {
  vi.stubGlobal("location", frameLocation);
  try { await import("./workspaceFrameState"); } // opening a frame installs its bridge handler
  finally { vi.unstubAllGlobals(); }
});
afterEach(() => { vi.unstubAllGlobals(); });

it("a workspace frame does not reload itself when the Host restarts: the home window keeps its work, then reloads", () => {
  vi.stubGlobal("location", frameLocation);
  const postMessage = vi.fn();
  vi.stubGlobal("window", { parent: { postMessage } });
  bridge.onHostRestart();
  expect(frameLocation.reload).not.toHaveBeenCalled();
  expect(postMessage).toHaveBeenCalledWith({ channel: "talyx-workspace-window", restart: true }, "http://127.0.0.1");
});

it.each(["failure", "timeout", "quota", "success"])("reload follows only confirmed workspace recovery: %s", async outcome => {
  vi.useFakeTimers();
  stash.mockReset();
  if (outcome === "quota") stash.mockRejectedValueOnce(new Error("QuotaExceededError"));
  const win = new EventTarget();
  const reload = vi.fn(), fail = vi.fn();
  vi.stubGlobal("window", win);
  vi.stubGlobal("location", { origin: "http://localhost", href: "http://localhost/", reload });
  const controller = new WorkspaceWindowController({ show: vi.fn(), close: vi.fn(), fail });
  const child = Object.assign(new EventTarget(), {
    postMessage: (data: { requestId: string; channel: string }) => {
      if (outcome === "timeout") return;
      const message = Object.assign(new Event("message"), { origin: location.origin, source: child,
        data: { ...data, ...(outcome === "failure" ? { error: "Cannot capture panel input" } : { recovery: { workspaceId: "w" } }) } });
      win.dispatchEvent(message);
    },
  });
  controller.frame("w", { contentWindow: child } as unknown as HTMLIFrameElement);
  try {
    let commit: (() => void) | undefined;
    if (outcome === "success") stash.mockReturnValueOnce(new Promise<void>(resolve => { commit = resolve; }));
    const restarted = controller.hostRestarted();
    await vi.runAllTimersAsync();
    if (commit) {
      expect(stash).toHaveBeenCalledWith([{ workspaceId: "w" }]);
      expect(reload).not.toHaveBeenCalled();
      commit();
    }
    await restarted;
    expect(stash).toHaveBeenCalledTimes(outcome === "success" || outcome === "quota" ? 1 : 0);
    expect(reload).toHaveBeenCalledTimes(outcome === "success" ? 1 : 0);
    expect(fail).toHaveBeenCalledTimes(outcome === "success" ? 0 : 1);
  } finally { vi.useRealTimers(); }
});
