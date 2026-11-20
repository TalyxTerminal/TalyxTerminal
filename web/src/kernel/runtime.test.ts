import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PlatformRuntime } from "./runtime";
import { failure, type ModuleDefinition } from "@talyx/sdk";

/* Every module's frontend loads the same way, whoever made it: from the address its definition gives. */

beforeEach(() => { vi.stubGlobal("document", { createElement: () => ({}) }); });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const definition = (id: string, patch: Partial<ModuleDefinition> = {}, frontend: string | undefined = "frontend/module.tsx"): ModuleDefinition => ({
  id, name: id, version: "1.0.0", description: id, source: "installed", enabled: true, state: "ready", problem: null, pid: 1234,
  digest: `${id}-1`, assetBase: `data:text/javascript,`,
  manifest: { manifestVersion: 5, id, name: id, version: "1.0.0", entries: frontend ? { frontend } : {} },
  contract: { contractVersion: "1", module: id, actions: {}, streams: {}, events: {}, extensionPoints: {}, settings: {}, $defs: {} },
  ...patch,
});
/** A module whose frontend entry is this source: its address is the definition's assetBase plus its entry. */
const withSource = (id: string, source: string, patch: Partial<ModuleDefinition> = {}) =>
  definition(id, patch, encodeURIComponent(`${source}\n//`));

function runtimeWith(list: () => ModuleDefinition[]) {
  const runtime = new PlatformRuntime();
  const call = vi.spyOn(runtime.bridge, "call").mockImplementation(async () => list() as never);
  let changed: () => unknown = () => undefined;
  vi.spyOn(runtime.bridge, "subscribe").mockImplementation(async (_name, _params, callback) => { changed = callback as () => unknown; return { id: "s", close: async () => undefined }; });
  return { runtime, call, changed: () => changed() };
}

it("loads each running module's frontend from its definition's address and digest, with nothing known by name", async () => {
  const source = "export function activate(ctx) { globalThis.__activated = (globalThis.__activated ?? []).concat(ctx.moduleId); }";
  const list = [withSource("one", source), withSource("two", source), definition("headless", {}, undefined),
    withSource("off", source, { enabled: false }), withSource("down", source, { state: "failed", problem: "No module named x" })];
  const { runtime, call } = runtimeWith(() => list);
  await runtime.refresh();
  expect(call.mock.calls.map(([name]) => name)).toEqual(["platform.modules.definitions", "platform.modules.list"]);
  expect((globalThis as { __activated?: string[] }).__activated).toEqual(["one", "two"]);
  expect([...runtime.kernel.modules.keys()]).toEqual(["one", "two"]);
  expect(runtime.kernel.definitions.get("down")?.problem).toBe("No module named x");
});

it("a frontend without activate(ctx) is a fault of that module only", async () => {
  const { runtime } = runtimeWith(() => [withSource("bad", "export const x = 1;"), withSource("good", "export function activate() {}")]);
  await runtime.refresh();
  expect(runtime.faults.get("bad")).toMatch(/activate/);
  expect(runtime.kernel.modules.get("good")?.ready).toBe(true);
});

it("a module whose digest changed starts again; a disabled one stops", async () => {
  let list = [withSource("one", "export function activate() {}")];
  const { runtime, changed } = runtimeWith(() => list);
  await runtime.refresh();
  const first = runtime.kernel.modules.get("one");
  list = [{ ...list[0], digest: "one-2" }];
  await changed();
  expect(runtime.kernel.modules.get("one")).not.toBe(first);
  list = [{ ...list[0], enabled: false }];
  await changed();
  expect(runtime.kernel.modules.has("one")).toBe(false);
});

it("a refresh retired by a lost connection is quiet; other failures are reported", async () => {
  const { runtime, call, changed } = runtimeWith(() => []);
  const fault = vi.spyOn(runtime, "reportFault");
  await runtime.refresh();
  call.mockRejectedValueOnce(failure("connection_lost"));
  await changed(); expect(fault).not.toHaveBeenCalled();
  call.mockRejectedValueOnce(failure("invalid_output", "Invalid definitions"));
  await changed(); expect(fault).toHaveBeenCalledWith("platform", expect.objectContaining({ code: "invalid_output" }));
});

it("the window's kernel and shell name no module", () => {
  // Which modules exist comes from platform.modules.definitions; the window's own code knows none of them.
  const root = resolve(__dirname, "..");
  for (const file of ["kernel/runtime.ts", "kernel/registry.ts", "kernel/bridge.ts", "main.tsx"]) {
    const source = readFileSync(resolve(root, file), "utf8");
    expect(source, file).not.toMatch(/["'`/](chart|market|quant|trade|agent|developer)["'`./]/);
  }
});

it("only a capture the user can fix is reported unavailable; any other capture failure is reported as it is", async () => {
  const runtime = new PlatformRuntime();
  vi.spyOn(runtime.kernel.panels, "get").mockReturnValue({ ref: { panelId: "p1" } } as never);
  const capture = vi.fn();
  vi.stubGlobal("window", { top: { talyxDesktop: { capturePanel: capture } } });
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  const offScreen = Object.assign(new Error("Open the panel in the workspace window to capture it"), { name: "PanelNotCapturable" });
  capture.mockRejectedValueOnce(offScreen);
  await expect(runtime.bridge.windowActions.capture_panel({ panelId: "p1" }, { workspaceId: "W" } as never)).rejects.toMatchObject({ code: "unavailable" });
  capture.mockRejectedValueOnce(new TypeError("pythonnet: object has no attribute"));
  await expect(runtime.bridge.windowActions.capture_panel({ panelId: "p1" }, { workspaceId: "W" } as never)).rejects.toBeInstanceOf(TypeError);
  expect(console.error).toHaveBeenCalledOnce();
});
it("acknowledges a module stop only after the workspace's input guard completes", async () => {
  const runtime = new PlatformRuntime();
  const guard = vi.spyOn(runtime.kernel, "prepareStop").mockRejectedValueOnce(failure("conflict", "Still editing"));
  const request = { id: "editor", reason: "reload" };
  const caller = { origin: "module" as const, module: "platform" };
  await expect(runtime.bridge.windowActions.prepare_module_stop(request, caller)).rejects.toMatchObject({ code: "conflict" });
  guard.mockResolvedValueOnce(undefined);
  await expect(runtime.bridge.windowActions.prepare_module_stop(request, caller)).resolves.toEqual({});
  expect(guard).toHaveBeenCalledWith("editor", "reload");
});
