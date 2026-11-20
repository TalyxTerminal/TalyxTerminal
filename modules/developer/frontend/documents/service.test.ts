import { describe, expect, it, vi } from "vitest";
const api = vi.hoisted(() => vi.fn());
vi.mock("../../public/ts/types", () => ({ createClient: () => new Proxy({}, { get: (_target, name) => (input: unknown, options: unknown) => options === undefined ? api(name, input) : api(name, input, options) }) }));
import { ContractError } from "@talyx/sdk";
import { DocumentStore, minimalEdit } from "./service";

const snapshot = (version: number, content: string, base = "base") => ({ documentId: "doc", uri: "file:///c%3A/p/main.py", name: "main.py", source: { kind: "file", uri: "file:///c%3A/p/main.py", projectId: "p" },
  context: { contextId: "project:p", projectId: "p", rootUri: "file:///c%3A/p", languageMaterials: [] }, content, version, sourceVersion: "v", dirty: content !== base, language: "python", conflict: false, baseContent: base, readOnly: false, externalState: "present", error: null });
const receipt = (version: number, content: string) => ({ documentId: "doc", uri: "file:///c%3A/p/main.py", name: "main.py", version, dirty: content !== "base", conflict: false });
function fakeModel(initial: string) {
  let value = initial;
  return { getValue: () => value, setValue: (next: string) => { value = next; }, isDisposed: () => false, canUndo: () => true, canRedo: () => true, getPositionAt: (offset: number) => ({ lineNumber: 1, column: offset + 1 }),
    pushEditOperations: vi.fn((_selections: unknown, edits: { range: { startColumn: number; endColumn: number }; text: string }[]) => { const edit = edits[0]; value = value.slice(0, edit.range.startColumn - 1) + edit.text + value.slice(edit.range.endColumn - 1); }) };
}
function store(initial = "base") {
  const documents = new DocumentStore();
  (documents as any).ctx = { events: { subscribe: vi.fn(async () => ({ close: vi.fn() })) }, onBeforeStop: vi.fn() };
  documents.entries.set("doc", { snapshot: snapshot(1, initial) as any, queued: false, remote: false, dirty: false, model: fakeModel(initial) as any });
  return documents;
}

describe("minimal remote edits", () => {
  it("replaces only the changed middle", () => {
    expect(minimalEdit("alpha beta gamma", "alpha BETA gamma")).toEqual({ start: 6, end: 10, text: "BETA" });
    expect(minimalEdit("same", "same")).toEqual({ start: 4, end: 4, text: "" });
    expect(minimalEdit("aaa", "aaaa")).toEqual({ start: 3, end: 3, text: "a" });
  });
});

describe("typing reaches the backend buffer", () => {
  it("sends one request at a time and the latest content last", async () => {
    const documents = store(), model = documents.entries.get("doc")!.model as any;
    let release: () => void = () => undefined;
    api.mockReset().mockImplementation((name: string, input: any) => name === "documents.edit"
      ? new Promise(resolve => { release = () => resolve(receipt(input.expectedVersion + 1, input.content)); }) : undefined);
    model.setValue("base 1"); const first = (documents as any).send("doc");
    model.setValue("base 12"); void (documents as any).send("doc");
    expect(api).toHaveBeenCalledTimes(1);
    release(); await vi.waitFor(() => expect(api).toHaveBeenCalledTimes(2));
    expect(api.mock.calls[1][1]).toEqual({ documentId: "doc", expectedVersion: 2, content: "base 12" });
    release(); await first; await documents.flush();
    expect(documents.snapshot("doc")).toMatchObject({ version: 3, content: "base 12" });
    expect(documents.isDirty("doc")).toBe(true);
  });
  it("keeps the tab after a failed send and saves the local text before closing", async () => {
    const documents = store();
    documents.show("doc");
    const model = documents.entries.get("doc")!.model as any;
    model.setValue("new work");
    api.mockReset().mockRejectedValue(new Error("Disconnected"));
    await expect((documents as any).send("doc")).rejects.toThrow("Disconnected");
    documents.requestCloseTab("doc");
    await documents.finishClose("save");
    expect(documents.tabs).toEqual(["doc"]);
    expect(documents.pendingClose?.error).toBe("Disconnected");
    expect(api).not.toHaveBeenCalledWith("documents.save", expect.anything());
    api.mockImplementation(async (name, input) => {
      if (name === "documents.edit") return receipt(2, input.content);
      if (name === "documents.save") return { status: "saved" };
      return snapshot(2, "new work", "new work");
    });
    await documents.finishClose("save");
    expect(api).toHaveBeenCalledWith("documents.edit", { documentId: "doc", expectedVersion: 1, content: "new work" });
    expect(api).toHaveBeenCalledWith("documents.save", { documentId: "doc", expectedVersion: 2 });
    expect(documents.tabs).toEqual([]);
  });
  it("keeps both versions when another writer moved the buffer on under unsent typing", async () => {
    const documents = store(), model = documents.entries.get("doc")!.model as any;
    api.mockReset().mockImplementation(async (name: string, input: any) => {
      if (name === "documents.read") return snapshot(5, "agent text");
      if (input.expectedVersion !== 5) throw new ContractError({ code: "conflict", message: "Read the current revision" } as any);
      return receipt(6, input.content);
    });
    model.setValue("typed"); await (documents as any).send("doc");
    expect(api.mock.calls.filter(([name]) => name === "documents.edit")).toHaveLength(1);
    expect(model.getValue()).toBe("typed");
    expect(documents.review("doc")).toBe("Changed elsewhere");
    await expect(documents.save("doc")).rejects.toMatchObject({ code: "conflict" });
    model.setValue("typed more"); await (documents as any).send("doc");
    expect(api.mock.calls.filter(([name]) => name === "documents.edit")).toHaveLength(1);
    await documents.resolve("doc", "external");
    expect(model.getValue()).toBe("agent text");
    expect(documents.snapshot("doc")).toMatchObject({ version: 5, content: "agent text" });
    expect(documents.review("doc")).toBeNull();
  });
  it("writes this window's text over the other version only when the author keeps it", async () => {
    const documents = store(), model = documents.entries.get("doc")!.model as any;
    api.mockReset().mockImplementation(async (name: string, input: any) => {
      if (name === "documents.read") return snapshot(5, "agent text");
      if (input.expectedVersion !== 5) throw new ContractError({ code: "conflict", message: "Read the current revision" } as any);
      return receipt(6, input.content);
    });
    model.setValue("typed"); await (documents as any).send("doc");
    await documents.resolve("doc", "mine");
    expect(api).toHaveBeenLastCalledWith("documents.edit", { documentId: "doc", expectedVersion: 5, content: "typed" });
    expect(documents.snapshot("doc")).toMatchObject({ version: 6, content: "typed" });
    expect(documents.review("doc")).toBeNull();
  });
  it("takes the other writer's content on a conflict when this window's typing is gone", async () => {
    const documents = store(), model = documents.entries.get("doc")!.model as any;
    api.mockReset().mockImplementation(async (name: string) => {
      if (name === "documents.read") { model.setValue("base"); return snapshot(5, "agent text"); }
      throw new ContractError({ code: "conflict", message: "Read the current revision" } as any);
    });
    model.setValue("typed"); await (documents as any).send("doc");
    expect(model.getValue()).toBe("agent text");
    expect(documents.snapshot("doc")).toMatchObject({ version: 5, content: "agent text" });
    expect(documents.review("doc")).toBeNull();
  });
  it("rereads after a send when another writer's change arrived meanwhile", async () => {
    const documents = store(), model = documents.entries.get("doc")!.model as any;
    let changed: (payload: unknown) => void = () => undefined, release: () => void = () => undefined;
    const ctx = { events: { subscribe: vi.fn(async (_name: string, handler: (payload: unknown) => void) => { changed = handler; return { close: vi.fn() }; }) }, onBeforeStop: vi.fn() };
    vi.stubGlobal("window", { addEventListener: vi.fn(), removeEventListener: vi.fn() });
    documents.bind(ctx as any);
    api.mockReset().mockImplementation((name: string, input: any) => name === "documents.edit"
      ? new Promise(resolve => { release = () => resolve(receipt(2, input.content)); })
      : Promise.resolve(snapshot(3, "typed, then agent")));
    model.setValue("typed"); const sending = (documents as any).send("doc");
    changed({ documentId: "doc", version: 3, kind: "changed", origin: "agent" });
    expect(api).not.toHaveBeenCalledWith("documents.read", expect.anything());
    release(); await sending;
    await vi.waitFor(() => expect(model.getValue()).toBe("typed, then agent"));
    expect(documents.snapshot("doc")).toMatchObject({ version: 3 });
    vi.unstubAllGlobals();
  });
  it("applies a remote change to an idle model without resending it", async () => {
    const documents = store(), model = documents.entries.get("doc")!.model as any;
    api.mockReset().mockImplementation(async (name: string) => name === "documents.read" ? snapshot(2, "base from agent") : undefined);
    await documents.read("doc");
    expect(model.getValue()).toBe("base from agent");
    expect(model.pushEditOperations).toHaveBeenCalledOnce();
    expect(api).not.toHaveBeenCalledWith("documents.edit", expect.anything());
  });
});

it("reloads the workbench project list when a different caller registers a project", async () => {
  const documents = store();
  const refresh = vi.spyOn(documents, "refreshProjects").mockResolvedValue([]);
  const handlers = new Map<string, () => void>();
  const ctx = { events: { subscribe: async (name: string, handle: () => void) => { handlers.set(name, handle); return { close: vi.fn() }; } }, onBeforeStop: vi.fn() };
  vi.stubGlobal("window", { addEventListener: vi.fn(), removeEventListener: vi.fn() });
  const stop = documents.bind(ctx as any);
  handlers.get("developer.projects_changed")!();
  expect(refresh).toHaveBeenCalledOnce();
  stop();
  vi.unstubAllGlobals();
});

describe("which documents are shown", () => {
  it("is what some view shows: the open tabs of the full editor, the one document of an embedded editor", () => {
    const documents = store();
    documents.entries.set("other", { snapshot: { ...snapshot(1, "x"), documentId: "other" } as any, queued: false, remote: false, dirty: false });
    documents.entries.set("third", { snapshot: { ...snapshot(1, "x"), documentId: "third" } as any, queued: false, remote: false, dirty: false });
    documents.tabs = ["doc"];
    expect(documents.shownDocuments()).toEqual([]);
    const changes = vi.fn(); documents.watch(changes);
    const releaseFull = documents.registerView("panel", null, () => undefined);
    expect(documents.shownDocuments()).toEqual(["doc"]);
    const releaseEmbedded = documents.registerView("embedded", "other", () => undefined);
    expect(documents.shownDocuments()).toEqual(["doc", "other"]);
    expect(changes).toHaveBeenCalledTimes(2);
    releaseFull();
    expect(documents.shownDocuments()).toEqual(["other"]);
    releaseEmbedded();
    expect(documents.shownDocuments()).toEqual([]);
    expect(changes).toHaveBeenCalledTimes(4);
  });
});
