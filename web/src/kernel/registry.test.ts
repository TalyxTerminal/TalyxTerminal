import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FrontendKernel, type PanelShell, type Remote } from "./registry";
import type { Json, Manifest, ModuleContext, ModuleDefinition, PanelRef } from "@talyx/sdk";

/* The window's registry works from the modules' declarations as the Host lists them (manifest and contract, version
   5): nothing about a module is known from its name. */

const element = (): HTMLElement => ({ append: vi.fn(), remove: vi.fn(), setAttribute: vi.fn(), className: "" }) as unknown as HTMLElement;
beforeEach(() => { vi.stubGlobal("document", { createElement: element, body: element() }); });
afterEach(() => { vi.unstubAllGlobals(); });

const definition = (id: string, manifest: Partial<Manifest> = {}, contract: Partial<ModuleDefinition["contract"]> = {}): ModuleDefinition => ({
  id, name: id.toUpperCase(), version: "1.0.0", description: id, source: "official", enabled: true, state: "ready", problem: null, pid: 1234,
  digest: `${id}-1`, assetBase: `/api/modules/${id}/files/`,
  manifest: { manifestVersion: 5, id, name: id.toUpperCase(), version: "1.0.0", entries: { frontend: "frontend/module.tsx" }, ...manifest },
  contract: { contractVersion: "1", module: id, actions: {}, streams: {}, events: {}, extensionPoints: {}, settings: {}, $defs: {}, ...contract },
});
const nothing = () => ({ dispose() {} });
const remote = (): Remote & { call: ReturnType<typeof vi.fn> } => ({ call: vi.fn(async () => ({ ok: true })), subscribe: vi.fn() });

/** A kernel with these modules declared and their frontends running (each provides what it declares). */
async function running(...definitions: ModuleDefinition[]) {
  const kernel = new FrontendKernel();
  kernel.setDefinitions(definitions);
  const contexts = new Map<string, ModuleContext>();
  for (const item of definitions) await kernel.activate(item, ctx => {
    contexts.set(item.id, ctx);
    for (const name of Object.keys(item.manifest.panels ?? {})) ctx.panels.provide(name, nothing);
    for (const name of Object.keys(item.manifest.views ?? {})) ctx.views.provide(name, nothing);
  });
  return { kernel, contexts };
}
const mount = (kernel: FrontendKernel, type: string, panelId: string, state: Json = null, beforeDispose = () => undefined) =>
  kernel.mountPanel(type, panelId, element(), state, () => undefined, beforeDispose, { onTitleChange() {}, onStateChange() {} });

describe("a module's frontend", () => {
  it("must provide every panel and view its manifest declares, or it doesn't run (and can start again)", async () => {
    const kernel = new FrontendKernel();
    const item = definition("notes", { panels: { main: { title: "Notes" } }, views: { card: {} } });
    kernel.setDefinitions([item]);
    await expect(kernel.activate(item, ctx => { ctx.panels.provide("main", nothing); })).rejects.toMatchObject({ code: "invalid_input" });
    expect(kernel.modules.has("notes")).toBe(false);
    await kernel.activate(item, ctx => { ctx.panels.provide("main", nothing); ctx.views.provide("card", nothing); });
    expect(kernel.modules.get("notes")?.ready).toBe(true);
  });

  it("cannot provide what its manifest doesn't declare", async () => {
    const kernel = new FrontendKernel();
    const item = definition("notes");
    kernel.setDefinitions([item]);
    await expect(kernel.activate(item, ctx => { ctx.panels.provide("main", nothing); })).rejects.toMatchObject({ code: "invalid_input" });
  });

  it("calls the platform, itself and the modules it requires, as they are; nothing else", async () => {
    const { kernel, contexts } = await running(definition("data"), definition("chart", { requires: { data: {} } }), definition("other"));
    const host = remote(); kernel.remote = host;
    const chart = contexts.get("chart")!;
    await chart.actions.call("data.read", { id: 1 });
    await chart.actions.call("platform.tasks.get", { taskId: "t" });
    await chart.actions.call("chart.own", {});
    expect(host.call.mock.calls.map(([name, input, options]) => [name, input, options.module])).toEqual([
      ["data.read", { id: 1 }, "chart"], ["platform.tasks.get", { taskId: "t" }, "chart"], ["chart.own", {}, "chart"]]);
    await expect(chart.actions.call("other.read", {})).rejects.toMatchObject({ code: "permission_denied" });
  });

  it("before it stops, its frontend may refuse (onBeforeStop)", async () => {
    const { kernel, contexts } = await running(definition("notes"));
    const reasons: string[] = [];
    contexts.get("notes")!.onBeforeStop(reason => { reasons.push(reason); if (reason === "uninstall") throw new Error("Unsaved note"); });
    await kernel.prepareStop("notes", "disable");
    await expect(kernel.prepareStop("notes", "uninstall")).rejects.toThrow("Unsaved note");
    expect(reasons).toEqual(["disable", "uninstall"]);
  });

  it("stopping closes its panels, each giving its state first", async () => {
    const { kernel } = await running(definition("notes", { panels: { main: { title: "Notes" } } }));
    const captured = vi.fn();
    mount(kernel, "notes.main", "p1", null, captured);
    await kernel.disable("notes");
    expect(captured).toHaveBeenCalledOnce();
    expect(kernel.panels.size).toBe(0);
    expect(kernel.modules.has("notes")).toBe(false);
  });
});

describe("places in the window", () => {
  it("are what running modules provide at the platform's points, as declared: by module, in manifest order", async () => {
    const { kernel } = await running(
      definition("b", { provides: { "platform.settings_section": [{ id: "s", descriptor: { title: "B", view: "v" } }] }, views: { v: {} } }),
      definition("a", { provides: { "platform.settings_section": [{ id: "s", descriptor: { title: "A", view: "v" } }, { id: "first", descriptor: { title: "A0", view: "v" } }] }, views: { v: {} } }),
    );
    expect(kernel.contributions("platform.settings_section")).toEqual([
      { moduleId: "a", id: "s", descriptor: { title: "A", view: "v" } },
      { moduleId: "a", id: "first", descriptor: { title: "A0", view: "v" } },
      { moduleId: "b", id: "s", descriptor: { title: "B", view: "v" } },
    ]);
    await kernel.disable("a");
    expect(kernel.contributions("platform.settings_section").map(item => item.moduleId)).toEqual(["b"]);
  });

  it("a status item is set only when the manifest declares it", async () => {
    const { kernel, contexts } = await running(definition("feed", { provides: { "platform.status_item": [{ id: "link", descriptor: { title: "Link", icon: "bell" } }] } }));
    contexts.get("feed")!.status.set("link", { level: "warn", text: "Slow" });
    expect(kernel.statusValues.get("feed.link")).toEqual({ level: "warn", text: "Slow" });
    expect(() => contexts.get("feed")!.status.set("other", { level: "ok" })).toThrow(expect.objectContaining({ code: "invalid_input" }));
  });
});

describe("panels", () => {
  const panels = { main: { title: "Main", actions: { focus: { type: "object", properties: { row: { type: "integer" } }, required: ["row"] } } }, secret: { title: "Secret", visibility: "private" as const } };

  it("a panel's own action reaches the panel it targets, checked against its declaration", async () => {
    const { kernel } = await running(definition("table", { panels }), definition("agent"));
    const mounted = mount(kernel, "table.main", "p1");
    const panelCtx = kernel.panels.get("p1")!.ctx;
    const handled = vi.fn(async (input: Json) => ({ seen: input }));
    panelCtx.actions.handle("focus", handled);
    await expect(kernel.call("agent", "table.focus", { row: 3 }, { target: mounted.ref })).resolves.toEqual({ seen: { row: 3 } });
    expect(handled.mock.calls[0][1].caller).toEqual({ origin: "user", module: "agent" });
    await expect(kernel.call("agent", "table.focus", { row: "x" }, { target: mounted.ref })).rejects.toMatchObject({ code: "invalid_input" });
    await expect(kernel.call("agent", "table.other", {}, { target: mounted.ref })).rejects.toMatchObject({ code: "unknown_capability" });
    expect(() => panelCtx.actions.handle("other", handled)).toThrow(expect.objectContaining({ code: "invalid_input" }));
    mounted.instance.dispose();
    await new Promise(resolve => setTimeout(resolve, 0));
    await expect(kernel.call("agent", "table.focus", { row: 3 }, { target: mounted.ref })).rejects.toMatchObject({ code: "target_gone" });
  });

  it("open through the window's layout; another module's private panel can't be opened; a target opens once", async () => {
    const { kernel } = await running(definition("table", { panels }), definition("agent"));
    const opened: [string, Json][] = [];
    const shell: PanelShell = {
      open: async (type, state) => { opened.push([type, state]); return mount(kernel, type, `p${opened.length}`, state).ref; },
      activate: vi.fn(), close: vi.fn(async () => undefined), settings: vi.fn(),
    };
    kernel.shell = shell;
    await kernel.openPanel("agent", { type: "table.main", state: { row: 1 } });
    await expect(kernel.openPanel("agent", { type: "table.secret" })).rejects.toMatchObject({ code: "permission_denied" });
    await kernel.openPanel("table", { type: "table.secret" });
    const first: PanelRef = await kernel.openTarget({ module: "table", panelType: "main", objectId: "row:7" });
    const again = await kernel.openTarget({ module: "table", panelType: "main", objectId: "row:7" });
    expect(again).toEqual(first);
    expect(shell.activate).toHaveBeenCalledWith(first);
    expect(opened.map(([type]) => type)).toEqual(["table.main", "table.secret", "table.main"]);
    expect(kernel.listPanels().map(panel => ({ ...panel, panelId: undefined }))).toEqual([
      { type: "table.main", active: false, visible: true, view: null },
      { type: "table.secret", active: false, visible: true, view: null },
      { type: "table.main", active: false, visible: true, view: null },
    ].map(panel => ({ ...panel, panelId: undefined })));
  });

  it("a target goes to the panel that shows it now, as the panel last reported", async () => {
    const { kernel } = await running(definition("table", { panels }), definition("agent"));
    let count = 0;
    const shell: PanelShell = {
      open: async (type, state) => mount(kernel, type, `p${++count}`, state).ref,
      activate: vi.fn(), close: vi.fn(async () => undefined), settings: vi.fn(),
    };
    kernel.shell = shell;
    const first = await kernel.openTarget({ module: "table", panelType: "main", objectId: "row:7" });
    // The panel moved on to another row: row 7 opens anew, row 9 brings this panel forward.
    kernel.panels.get(first.panelId)!.ctx.view.report({ summary: "Row 9", refs: {}, openTargets: [{ kind: "row", key: "row:9", label: "Row 9" }] });
    const seven = await kernel.openTarget({ module: "table", panelType: "main", objectId: "row:7" });
    expect(seven.panelId).not.toBe(first.panelId);
    expect(await kernel.openTarget({ module: "table", panelType: "main", objectId: "row:9" })).toEqual(first);
    // Showing nothing that can be opened again, it is not brought forward for anything.
    kernel.panels.get(first.panelId)!.ctx.view.report({ summary: "Nothing", refs: {}, openTargets: [] });
    expect((await kernel.openTarget({ module: "table", panelType: "main", objectId: "row:9" })).panelId).toBe("p3");
  });

  it("without a workspace shown in the window there is nowhere to open one", async () => {
    const { kernel } = await running(definition("table", { panels }));
    await expect(kernel.openPanel("table", { type: "table.main" })).rejects.toMatchObject({ code: "ui_required" });
  });
});

describe("a module's own routes", () => {
  it("carry this window's session and the workspace it shows, and stay inside the module", async () => {
    const { kernel, contexts } = await running(definition("notes"));
    kernel.workspace.bind({ workspaceId: "w1", snapshotId: null });
    const fetched = vi.fn(async (_url: string, _init: RequestInit) => new Response("{}"));
    vi.stubGlobal("fetch", fetched);
    await contexts.get("notes")!.routes.fetch("items?x=1");
    const [url, init] = fetched.mock.calls[0];
    expect(url).toBe("/m/notes/items?x=1");
    expect(new Headers(init.headers).get("x-talyx-session")).toBe(kernel.sessionId);
    expect(new Headers(init.headers).get("x-talyx-workspace")).toBe("w1");
    await expect(contexts.get("notes")!.routes.fetch("../platform/x")).rejects.toMatchObject({ code: "permission_denied" });
  });

  it("a socket keeps the module's own parameters, even ones named session or workspace, beside the window's talyx- ones", async () => {
    const { kernel, contexts } = await running(definition("notes"));
    kernel.workspace.bind({ workspaceId: "w1", snapshotId: null });
    vi.stubGlobal("location", new URL("http://127.0.0.1:5173/"));
    const opened: string[] = [];
    vi.stubGlobal("WebSocket", class { constructor(address: URL) { opened.push(String(address)); } close() { /* nothing open */ } });
    contexts.get("notes")!.routes.websocket("live?session=mine&workspace=other&panel=p1");
    const address = new URL(opened[0]);
    expect(address.pathname).toBe("/m/notes/live");
    expect(Object.fromEntries(address.searchParams)).toEqual({ session: "mine", workspace: "other", panel: "p1", "talyx-session": kernel.sessionId, "talyx-workspace": "w1" });
    expect(() => contexts.get("notes")!.routes.websocket("live?talyx-session=forged")).toThrow(/cannot start with talyx-/);
  });

  it("an answer is read after fetch returns; only stopping the module aborts it", async () => {
    const { kernel, contexts } = await running(definition("notes"));
    const signals: AbortSignal[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => { signals.push(init.signal!); return new Response('{"rows": [1, 2]}'); }));
    const read = await contexts.get("notes")!.routes.fetch("items");
    expect(signals[0].aborted).toBe(false);
    await expect(read.json()).resolves.toEqual({ rows: [1, 2] });
    await contexts.get("notes")!.routes.fetch("items");
    await kernel.disable("notes");
    expect(signals.map(signal => signal.aborted)).toEqual([false, true]);
  });
});
