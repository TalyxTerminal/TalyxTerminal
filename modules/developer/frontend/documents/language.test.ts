import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
vi.mock("./service", () => ({ documents: {} }));
vi.mock("../api", () => ({ privateRequest: vi.fn(), jsonRequest: vi.fn() }));
import { BACKOFF_MS, contentChanges, LanguageManager, markers, OWNER, pythonFile, sameUri, type LanguageHost } from "./language";

const monacoMarkers = { MarkerSeverity: { Error: 8, Warning: 4, Info: 2, Hint: 1 }, MarkerTag: { Unnecessary: 1, Deprecated: 2 } };

describe("what the server and Monaco say about the same text", () => {
  it("names a file one way whatever the case and drive-letter escape", () => {
    expect(sameUri("file:///d%3A/Work/Factor.py")).toBe(sameUri("file:///D:/work/factor.py"));
    expect(sameUri("file:///d%3A/work/a.py")).not.toBe(sameUri("file:///d%3A/work/b.py"));
  });
  it("serves only Python files of the disk", () => {
    const base = { uri: "file:///d%3A/work/a.py", language: "python", source: { kind: "file" } };
    expect(pythonFile(base)).toBe(true);
    expect(pythonFile({ ...base, language: "json" })).toBe(false);
    expect(pythonFile({ ...base, source: { kind: "contribution" } })).toBe(false);
    expect(pythonFile({ ...base, uri: "talyx-source:/sdk/main.py" })).toBe(false);
  });
  it("sends the changes of one edit from the end of the text back, each against the text the server has then", () => {
    const change = (offset: number, text: string, line: number, from: number, to: number) => ({ rangeOffset: offset, rangeLength: to - from, text, range: { startLineNumber: line, startColumn: from, endLineNumber: line, endColumn: to } });
    const sent = contentChanges({ changes: [change(2, "X", 1, 3, 4), change(10, "Y", 2, 2, 2)] } as any);
    expect(sent.map(item => item.text)).toEqual(["Y", "X"]);
    expect(sent[0].range).toEqual({ start: { line: 1, character: 1 }, end: { line: 1, character: 1 } });
    expect(sent[1]).toEqual({ range: { start: { line: 0, character: 2 }, end: { line: 0, character: 3 } }, rangeLength: 1, text: "X" });
  });
  it("shows a diagnostic with its severity, code and the fading of unused code", () => {
    const [unused, error] = markers(monacoMarkers as any, [
      { range: { start: { line: 0, character: 7 }, end: { line: 0, character: 9 } }, message: "Import is not accessed", severity: 4, tags: [1], source: "basedpyright", code: { value: "reportUnusedImport" } },
      { range: { start: { line: 3, character: 0 }, end: { line: 3, character: 4 } }, message: "not defined", code: 7 }]);
    expect(unused).toMatchObject({ severity: 1, tags: [1], startLineNumber: 1, startColumn: 8, endColumn: 10, code: "reportUnusedImport", source: "basedpyright" });
    expect(error).toMatchObject({ severity: 8, tags: [], startLineNumber: 4, code: "7" });
  });
});

// ---- the manager, with a fake host, models and sockets ----
class FakeSocket {
  static all: FakeSocket[] = [];
  readyState = 1;
  sent: any[] = [];
  onopen: (() => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  constructor(public session: string) { FakeSocket.all.push(this); queueMicrotask(() => this.onopen?.()); }
  send(text: string) {
    const message = JSON.parse(text);
    this.sent.push(message);
    if (message.method === "initialize") this.say({ jsonrpc: "2.0", id: message.id, result: { capabilities: { completionProvider: {} } } });
  }
  say(message: unknown) { this.onmessage?.({ data: JSON.stringify(message) }); }
  close() { this.readyState = 3; }
  end(code: number) { this.readyState = 3; this.onclose?.({ code }); }
  methods() { return this.sent.map(item => item.method).filter(Boolean); }
}

function fakeModel(id: string, text: string) {
  let listener: ((event: any) => void) | undefined;
  return { uri: { toString: () => `talyx-document:/${id}/a.py`, scheme: "talyx-document" }, getValue: () => text, getVersionId: () => 3, isDisposed: () => false,
    onDidChangeContent: (callback: (event: any) => void) => { listener = callback; return { dispose: () => { listener = undefined; } }; },
    type: (event: any) => listener?.(event), listening: () => !!listener };
}

function setup() {
  const registered: Record<string, any> = {};
  const marked = new Map<string, any[]>();
  const record = (name: string) => (_language: string, provider: any) => { registered[name] = provider; return { dispose: vi.fn() }; };
  const monaco: any = { ...monacoMarkers, Range: class { constructor(public a: number, public b: number, public c: number, public d: number) {} }, Uri: { parse: (value: string) => ({ toString: () => value, scheme: "file" }) },
    languages: { CompletionItemKind: new Proxy({}, { get: (_target, name) => String(name) }), registerCompletionItemProvider: record("completion"), registerHoverProvider: record("hover"),
      registerSignatureHelpProvider: record("signature"), registerDefinitionProvider: record("definition"), registerReferenceProvider: record("references"), registerRenameProvider: record("rename") },
    editor: { setModelMarkers: (model: any, owner: string, list: any[]) => marked.set(model.uri.toString() + "|" + owner, list), registerEditorOpener: (opener: any) => { registered.opener = opener; return { dispose: vi.fn() }; } } };
  const documents: Record<string, { uri: string; language: string; source: { kind: string }; context: { projectId: string | null }; model: ReturnType<typeof fakeModel> }> = {};
  const add = (id: string, project: string | null = "p", extra: object = {}) => {
    documents[id] = { uri: `file:///d%3A/work/${id}.py`, language: "python", source: { kind: "file" }, context: { projectId: project }, model: fakeModel(id, `# ${id}\n`), ...extra };
  };
  let shown: string[] = [];
  const sessions: Record<string, string | null> = {};
  const host = {
    shown: () => shown, snapshot: (id: string) => documents[id], model: async (id: string) => documents[id].model,
    resolve: vi.fn(async (id: string) => ({ session: id in sessions ? sessions[id] : "research" })), socket: (session: string) => new FakeSocket(session) as unknown as WebSocket,
    open: vi.fn(async () => null), show: vi.fn(), fullEditor: vi.fn(() => true), report: vi.fn(),
  };
  const manager = new LanguageManager(monaco, host as unknown as LanguageHost);
  return { manager, host, monaco, registered, marked, documents, add, sessions, show: (...ids: string[]) => { shown = ids; manager.schedule(); } };
}
const settle = async (times = 6) => { for (let i = 0; i < times; i++) await new Promise(resolve => setTimeout(resolve, 0)); };

describe("language service for the documents editors show", () => {
  beforeEach(() => { FakeSocket.all = []; vi.stubGlobal("WebSocket", { OPEN: 1 }); });
  afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

  it("opens a shown Python file on its environment's server and keeps the server's copy current", async () => {
    const t = setup();
    t.add("a");
    t.show("a");
    await settle();
    const [socket] = FakeSocket.all;
    expect(FakeSocket.all).toHaveLength(1);
    expect(socket.methods()).toEqual(["initialize", "initialized", "textDocument/didOpen"]);
    expect(socket.sent[2].params.textDocument).toEqual({ uri: "file:///d%3A/work/a.py", languageId: "python", version: 3, text: "# a\n" });
    t.documents.a.model.type({ changes: [{ rangeOffset: 0, rangeLength: 0, text: "x", range: { startLineNumber: 1, startColumn: 1, endLineNumber: 1, endColumn: 1 } }] });
    expect(socket.sent.at(-1)).toMatchObject({ method: "textDocument/didChange", params: { textDocument: { uri: "file:///d%3A/work/a.py", version: 3 }, contentChanges: [{ text: "x" }] } });
    // The server's diagnostics for that file (named in its own spelling) become the model's markers.
    socket.say({ jsonrpc: "2.0", method: "textDocument/publishDiagnostics", params: { uri: "file:///D:/work/A.py", diagnostics: [{ range: { start: { line: 0, character: 0 }, end: { line: 0, character: 1 } }, message: "bad", severity: 1 }] } });
    expect(t.marked.get(`talyx-document:/a/a.py|${OWNER}`)).toMatchObject([{ message: "bad", severity: 8, startLineNumber: 1 }]);
  });

  it("shares one connection between documents of one environment and closes it after the last one", async () => {
    const t = setup();
    t.add("a"); t.add("b"); t.add("c");
    t.sessions.c = "strategy";
    t.show("a", "b", "c");
    await settle();
    expect(FakeSocket.all.map(socket => socket.session)).toEqual(["research", "strategy"]);
    t.show("b", "c");
    await settle();
    const [research, strategy] = FakeSocket.all;
    expect(research.methods().at(-1)).toBe("textDocument/didClose");
    expect(research.readyState).toBe(1);
    expect(t.documents.a.model.listening()).toBe(false);
    expect(t.marked.get(`talyx-document:/a/a.py|${OWNER}`)).toEqual([]);
    t.show("c");
    await settle();
    expect(research.readyState).toBe(3);
    expect(strategy.readyState).toBe(1);
    t.show();
    await settle();
    expect(strategy.readyState).toBe(3);
  });

  it("leaves out what is not a Python file of a project, and asks again later about a file no support gave a Python", async () => {
    const t = setup();
    t.add("notes", "p", { language: "plaintext" });
    t.add("reference", "p", { source: { kind: "contribution" } });
    t.add("early");
    t.sessions.early = null;
    t.show("notes", "reference", "early");
    await settle();
    expect(FakeSocket.all).toHaveLength(0);
    expect(t.host.resolve).toHaveBeenCalledTimes(1);
    t.show("early");  // looked at again while skipped: not asked again
    await settle();
    expect(t.host.resolve).toHaveBeenCalledTimes(1);
    t.sessions.early = "research";
    await t.manager.refresh();  // the environment has become ready
    await settle();
    expect(FakeSocket.all).toHaveLength(1);
  });

  it("moves a document to the new server when its environment changed", async () => {
    const t = setup();
    t.add("a");
    t.show("a");
    await settle();
    t.sessions.a = "research-new";
    await t.manager.refresh();
    await settle();
    expect(FakeSocket.all.map(socket => socket.session)).toEqual(["research", "research-new"]);
    expect(FakeSocket.all[0].readyState).toBe(3);
    expect(FakeSocket.all[1].methods()).toContain("textDocument/didOpen");
  });

  it("opens the documents again on a new connection when the server stopped, and not at all when another window took over", async () => {
    vi.useFakeTimers();
    const t = setup();
    t.add("a");
    t.show("a");
    await vi.advanceTimersByTimeAsync(10);
    FakeSocket.all[0].end(4002);
    expect(t.documents.a.model.listening()).toBe(false);
    await vi.advanceTimersByTimeAsync(BACKOFF_MS[0] + 10);
    expect(FakeSocket.all).toHaveLength(2);
    expect(FakeSocket.all[1].methods()).toContain("textDocument/didOpen");
    FakeSocket.all[1].end(4001);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(FakeSocket.all).toHaveLength(2);
  });

  it("reports a failure to resolve once, however often it is asked", async () => {
    const t = setup();
    t.add("a");
    t.host.resolve.mockRejectedValue(new Error("The Python language service (basedpyright) is not installed."));
    t.show("a");
    await settle();
    await t.manager.refresh();
    await t.manager.refresh();
    await settle();
    expect(t.host.report).toHaveBeenCalledTimes(1);
  });

  it("tells the server which Python file was saved or changed on disk, and nothing about typing", async () => {
    const t = setup();
    t.add("a"); t.add("notes", "p", { language: "plaintext" });
    t.show("a");
    await settle();
    const [socket] = FakeSocket.all, before = socket.sent.length;
    t.manager.fileChanged({ documentId: "a", kind: "changed", origin: "user" });
    t.manager.fileChanged({ documentId: "notes", kind: "saved", origin: "user" });
    t.manager.fileChanged({ documentId: "unknown", kind: "saved", origin: "user" });
    expect(socket.sent).toHaveLength(before);
    t.manager.fileChanged({ documentId: "a", kind: "saved", origin: "user" });
    t.manager.fileChanged({ documentId: "a", kind: "changed", origin: "disk" });
    expect(socket.sent.slice(before)).toEqual([1, 2].map(() => ({ jsonrpc: "2.0", method: "workspace/didChangeWatchedFiles", params: { changes: [{ uri: "file:///d%3A/work/a.py", type: 2 }] } })));
  });

  it("answers completion requests from the server of the model's document and nothing for any other model", async () => {
    const t = setup();
    t.add("a");
    t.show("a");
    await settle();
    const [socket] = FakeSocket.all;
    const model = { ...t.documents.a.model, getWordUntilPosition: () => ({ startColumn: 3, endColumn: 4 }) };
    const asked = t.registered.completion.provideCompletionItems(model, { lineNumber: 2, column: 4 }, { triggerKind: 0, triggerCharacter: "." }, { onCancellationRequested: () => undefined });
    await settle();
    const request = socket.sent.at(-1);
    expect(request).toMatchObject({ method: "textDocument/completion", params: { textDocument: { uri: "file:///d%3A/work/a.py" }, position: { line: 1, character: 3 }, context: { triggerKind: 1, triggerCharacter: "." } } });
    socket.say({ jsonrpc: "2.0", id: request.id, result: { isIncomplete: false, items: [{ label: "close", kind: 10, detail: "DataFrame", sortText: "a", textEdit: { newText: "close", range: { start: { line: 1, character: 2 }, end: { line: 1, character: 3 } } } }] } });
    const result = await asked;
    expect(result.suggestions).toHaveLength(1);
    expect(result.suggestions[0]).toMatchObject({ label: "close", detail: "DataFrame", insertText: "close", sortText: "a" });
    const stranger = { uri: { toString: () => "talyx-text:/cell" }, getWordUntilPosition: () => ({}) };
    expect(await t.registered.completion.provideCompletionItems(stranger, { lineNumber: 1, column: 1 }, { triggerKind: 0 }, { onCancellationRequested: () => undefined })).toBeUndefined();
  });

  it("goes to a definition in another file: the full editor changes tab, an embedded editor shows the file in place", async () => {
    const t = setup();
    t.add("a"); t.add("helpers");
    t.show("a", "helpers");
    await settle();
    const selection = { startLineNumber: 3, startColumn: 5, endLineNumber: 3, endColumn: 9 };
    const editor = (model: any) => ({ getModel: () => model, setModel: vi.fn(), setSelection: vi.fn(), revealRangeInCenter: vi.fn() });
    t.host.fullEditor.mockReturnValue(true);
    const source = editor(t.documents.a.model);
    // The same file: Monaco's own jump.
    expect(t.registered.opener.openCodeEditor(source, t.documents.a.model.uri, selection)).toBe(false);
    // Another open file, named by its model: the full editor shows its tab at the line.
    expect(t.registered.opener.openCodeEditor(source, t.documents.helpers.model.uri, selection)).toBe(true);
    await settle();
    expect(t.host.show).toHaveBeenCalledWith("helpers", { line: 3, column: 5 });
    // A file nobody has open yet is opened as a document of the same project first; a file outside every project goes nowhere.
    t.host.open.mockResolvedValueOnce("fresh").mockResolvedValueOnce(null);
    const file = (uri: string) => ({ toString: () => uri, scheme: "file" });
    t.registered.opener.openCodeEditor(source, file("file:///d%3A/work/new.py"), selection);
    await settle();
    expect(t.host.open).toHaveBeenCalledWith("file:///d%3A/work/new.py", "a");
    expect(t.host.show).toHaveBeenLastCalledWith("fresh", { line: 3, column: 5 });
    t.registered.opener.openCodeEditor(source, file("file:///c%3A/lib/pandas.py"), selection);
    await settle();
    expect(t.host.show).toHaveBeenCalledTimes(2);
    // An embedded editor has no tabs: it switches its own model.
    t.host.fullEditor.mockReturnValue(false);
    t.registered.opener.openCodeEditor(source, t.documents.helpers.model.uri, selection);
    await settle();
    expect(source.setModel).toHaveBeenCalledWith(t.documents.helpers.model);
    expect(source.revealRangeInCenter).toHaveBeenCalledWith(selection);
  });

  it("goes to the first of the places a definition is found: a stub and its source both answer, and a peek could show neither", async () => {
    const t = setup();
    t.add("a"); t.add("helpers");
    t.show("a", "helpers");
    await settle();
    const [socket] = FakeSocket.all;
    const asked = t.registered.definition.provideDefinition(t.documents.a.model, { lineNumber: 1, column: 1 }, { onCancellationRequested: () => undefined });
    await settle();
    const request = socket.sent.at(-1);
    const where = (uri: string) => ({ uri, range: { start: { line: 4, character: 0 }, end: { line: 4, character: 3 } } });
    socket.say({ jsonrpc: "2.0", id: request.id, result: [where("file:///D:/work/helpers.py"), where("file:///d%3A/lib/talyx_quant.py")] });
    const found = await asked;
    expect(found).toHaveLength(1);
    expect(found[0].uri).toBe(t.documents.helpers.model.uri);
    expect(found[0].range).toMatchObject({ a: 5, b: 1, c: 5, d: 4 });
  });

  it("renames only where an editor shows the file, and says which files it would otherwise touch", async () => {
    const t = setup();
    t.add("a"); t.add("helpers");
    t.show("a");
    await settle();
    const [socket] = FakeSocket.all;
    const edit = (uri: string) => ({ range: { start: { line: 0, character: 0 }, end: { line: 0, character: 1 } }, newText: "y" });
    const run = async (changes: Record<string, unknown[]>) => {
      const asked = t.registered.rename.provideRenameEdits(t.documents.a.model, { lineNumber: 1, column: 1 }, "y", { onCancellationRequested: () => undefined });
      await settle();
      const request = socket.sent.at(-1);
      socket.say({ jsonrpc: "2.0", id: request.id, result: { changes } });
      return asked;
    };
    const open = await run({ "file:///d%3A/work/a.py": [edit("a")] });
    expect(open.edits).toHaveLength(1);
    expect(open.edits[0]).toMatchObject({ resource: t.documents.a.model.uri, textEdit: { text: "y" } });
    const closed = await run({ "file:///d%3A/work/a.py": [edit("a")], "file:///d%3A/work/helpers.py": [edit("helpers")] });
    expect(closed.edits).toEqual([]);
    expect(closed.rejectReason).toContain("helpers.py");
  });
});
