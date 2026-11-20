import type { ModuleContext } from "@talyx/sdk";
import type * as MonacoTypes from "monaco-editor";
import { jsonRequest, privateRequest } from "../api";
import { LanguageConnection, type Lsp } from "./languageConnection";
import { documents, type Position } from "./service";

type Monaco = typeof MonacoTypes;
type Model = MonacoTypes.editor.ITextModel;
type Editor = MonacoTypes.editor.ICodeEditor;

export const OWNER = "python-language";
export const REFRESH_MS = 15_000;
const SKIP_MS = 15_000;
export const BACKOFF_MS = [1_000, 2_000, 4_000, 8_000, 16_000];

/** What the Python language service needs from the editors and documents of this window. */
export interface LanguageHost {
  /** The documents some editor shows. A document no editor shows is not kept open on the server. */
  shown(): string[];
  snapshot(id: string): { uri: string; language: string; source: { kind: string }; context: { projectId: string | null } };
  model(id: string): Promise<Model>;
  /** The session whose server analyses this document, or none when no support gives it a Python. */
  resolve(id: string): Promise<{ session: string | null }>;
  socket(session: string): WebSocket;
  /** A file a definition leads to that is not open yet, as the document of the project of `from`. */
  open(uri: string, from: string): Promise<string | null>;
  show(id: string, position?: Position): void;
  /** Whether the editor is the full editor's: it changes tabs; any other shows one document and switches its model. */
  fullEditor(editor: Editor): boolean;
  report(error: unknown): void;
}

interface Attached { id: string; uri: string; model: Model; session: string; listener: MonacoTypes.IDisposable }
interface Session { key: string; connection: LanguageConnection; capabilities: Lsp; documents: Set<string> }

/** The server's URI and the one the backend gave the document name the same file: case and drive-letter escapes differ. */
export const sameUri = (uri: string) => decodeURIComponent(uri).toLowerCase();
export const pythonFile = (snapshot: { uri: string; language: string; source: { kind: string } }) =>
  snapshot.language === "python" && snapshot.source.kind === "file" && snapshot.uri.startsWith("file:");

/** Monaco's changes of one edit all refer to the text before it; the server applies them one after another, so from the end backwards. */
export function contentChanges(event: MonacoTypes.editor.IModelContentChangedEvent) {
  return [...event.changes].sort((a, b) => b.rangeOffset - a.rangeOffset).map(change => ({
    range: { start: { line: change.range.startLineNumber - 1, character: change.range.startColumn - 1 }, end: { line: change.range.endLineNumber - 1, character: change.range.endColumn - 1 } },
    rangeLength: change.rangeLength, text: change.text }));
}

export function markers(monaco: Monaco, diagnostics: Lsp[]): MonacoTypes.editor.IMarkerData[] {
  const severity = [0, monaco.MarkerSeverity.Error, monaco.MarkerSeverity.Warning, monaco.MarkerSeverity.Info, monaco.MarkerSeverity.Hint];
  return diagnostics.map(item => ({
    severity: severity[item.severity ?? 1], message: item.message, source: item.source,
    code: item.code == null ? undefined : typeof item.code === "object" ? String(item.code.value) : String(item.code),
    // 1: unnecessary (an unused import), 2: deprecated
    tags: (item.tags ?? []).map((tag: number) => tag === 2 ? monaco.MarkerTag.Deprecated : monaco.MarkerTag.Unnecessary),
    startLineNumber: item.range.start.line + 1, startColumn: item.range.start.character + 1,
    endLineNumber: item.range.end.line + 1, endColumn: item.range.end.character + 1 }));
}

/**
 * Python language features for the documents that editors show. Each such document is open on the server of its environment
 * (the backend names it) while an editor shows it; the editor itself never waits for any of this.
 */
export class LanguageManager {
  private attached = new Map<string, Attached>();
  private byModel = new Map<string, Attached>();
  private byUri = new Map<string, Attached>();
  private sessions = new Map<string, Session>();
  private opening = new Map<string, Promise<Session>>();
  private skipped = new Map<string, number>();
  private reported = new Set<string>();
  private registrations: MonacoTypes.IDisposable[] = [];
  private dirty = false;
  private running: Promise<void> | null = null;
  private retries = 0;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private disposed = false;

  constructor(private monaco: Monaco, private host: LanguageHost, private now: () => number = Date.now) { this.register(); }

  // ---- which documents are open on a server ----
  /** Bring the open documents in line with what editors show. Cheap to call as often as the document store changes. */
  schedule() {
    if (this.disposed) return;
    this.dirty = true;
    this.running ??= this.loop().finally(() => { this.running = null; });
  }
  private async loop() { while (this.dirty && !this.disposed) { this.dirty = false; await this.reconcile(); } }

  private async reconcile() {
    const wanted = this.host.shown().filter(id => pythonFile(this.host.snapshot(id)));
    for (const id of [...this.attached.keys()]) if (!wanted.includes(id)) this.detach(id);
    for (const id of wanted) {
      if (this.attached.has(id) || (this.skipped.get(id) ?? 0) > this.now()) continue;
      try { await this.attach(id); }
      catch (error) { this.skipped.set(id, this.now() + SKIP_MS); this.problem(error); }
    }
  }

  private problem(error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    if (this.reported.has(message)) return;
    this.reported.add(message);
    this.host.report(error);
  }

  private async attach(id: string) {
    const { session: key } = await this.host.resolve(id);
    if (!key) { this.skipped.set(id, this.now() + SKIP_MS); return; }
    const session = await this.session(key);
    const model = await this.host.model(id);
    if (this.disposed || model.isDisposed() || !this.host.shown().includes(id) || this.attached.has(id)) return;
    if (session.connection.closed) { this.later(1_000); return; } // closed while this was under way: connect again
    const uri = this.host.snapshot(id).uri;
    session.connection.notify("textDocument/didOpen", { textDocument: { uri, languageId: "python", version: model.getVersionId(), text: model.getValue() } });
    const listener = model.onDidChangeContent(event => session.connection.notify("textDocument/didChange",
      { textDocument: { uri, version: model.getVersionId() }, contentChanges: contentChanges(event) }));
    const attached = { id, uri, model, session: key, listener };
    this.attached.set(id, attached);
    this.byModel.set(model.uri.toString(), attached);
    this.byUri.set(sameUri(uri), attached);
    session.documents.add(id);
    this.retries = 0;
  }

  private detach(id: string) {
    const attached = this.attached.get(id);
    if (!attached) return;
    attached.listener.dispose();
    this.attached.delete(id);
    this.byModel.delete(attached.model.uri.toString());
    this.byUri.delete(sameUri(attached.uri));
    if (!attached.model.isDisposed()) this.monaco.editor.setModelMarkers(attached.model, OWNER, []);
    const session = this.sessions.get(attached.session);
    if (!session) return;
    session.documents.delete(id);
    if (session.connection.closed) return;
    session.connection.notify("textDocument/didClose", { textDocument: { uri: attached.uri } });
    if (!session.documents.size) { this.sessions.delete(session.key); session.connection.close(); }
  }

  private session(key: string): Promise<Session> {
    const existing = this.sessions.get(key);
    if (existing && !existing.connection.closed) return Promise.resolve(existing);
    let opening = this.opening.get(key);
    if (!opening) {
      opening = (async () => {
        const connection = await LanguageConnection.open(() => this.host.socket(key), {
          notification: (method, params) => this.notification(method, params), closed: code => this.closed(key, code) });
        const capabilities = await connection.initialize();
        const session = { key, connection, capabilities, documents: new Set<string>() };
        this.sessions.set(key, session);
        return session;
      })().finally(() => this.opening.delete(key));
      this.opening.set(key, opening);
    }
    return opening;
  }

  private notification(method: string, params: Lsp) {
    if (method !== "textDocument/publishDiagnostics") return;
    const attached = this.byUri.get(sameUri(params.uri));
    if (attached && !attached.model.isDisposed()) this.monaco.editor.setModelMarkers(attached.model, OWNER, markers(this.monaco, params.diagnostics));
  }

  /** The server went away (stopped, replaced by a window that took over, or the backend restarted): its documents are dropped and, unless another window took over, opened again. */
  private closed(key: string, code: number) {
    const session = this.sessions.get(key);
    this.sessions.delete(key);
    for (const id of session?.documents ?? []) {
      const attached = this.attached.get(id);
      if (!attached) continue;
      attached.listener.dispose();
      this.attached.delete(id);
      this.byModel.delete(attached.model.uri.toString());
      this.byUri.delete(sameUri(attached.uri));
      if (!attached.model.isDisposed()) this.monaco.editor.setModelMarkers(attached.model, OWNER, []);
    }
    if (code === 4001 || this.disposed) return;
    this.later(BACKOFF_MS[Math.min(this.retries++, BACKOFF_MS.length - 1)]);
  }

  private later(ms: number) {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => { this.skipped.clear(); this.schedule(); }, ms);
  }

  /** Packages may have been installed and documents may have got a Python since they were last looked at: ask again, once per project. */
  async refresh() {
    const projects = new Set<string | null>();
    for (const attached of [...this.attached.values()]) {
      const project = this.host.snapshot(attached.id).context.projectId;
      if (projects.has(project)) continue;
      projects.add(project);
      try {
        const { session } = await this.host.resolve(attached.id);
        if (session !== attached.session) for (const other of [...this.attached.values()]) if (this.host.snapshot(other.id).context.projectId === project) this.detach(other.id);
      } catch (error) { this.problem(error); }
    }
    this.skipped.clear();
    this.schedule();
  }

  /** A file saved or changed on disk may be one the server reads from disk: it is told, so what it knows of the files that import it stays right. */
  fileChanged(event: { documentId: string; kind: string; origin: string }) {
    if (event.kind !== "saved" && event.origin !== "disk") return;
    let uri: string;
    try { const snapshot = this.host.snapshot(event.documentId); if (!pythonFile(snapshot)) return; uri = snapshot.uri; } catch { return; }
    for (const session of this.sessions.values()) session.connection.notify("workspace/didChangeWatchedFiles", { changes: [{ uri, type: 2 }] });
  }

  dispose() {
    this.disposed = true;
    clearTimeout(this.timer);
    for (const id of [...this.attached.keys()]) this.detach(id);
    for (const session of this.sessions.values()) session.connection.close();
    this.sessions.clear();
    for (const registration of this.registrations) registration.dispose();
  }

  // ---- editor features ----
  private connectionOf(model: Model): { connection: LanguageConnection; attached: Attached } | undefined {
    const attached = this.byModel.get(model.uri.toString());
    const connection = attached && this.sessions.get(attached.session)?.connection;
    return attached && connection && !connection.closed ? { connection, attached } : undefined;
  }

  private register() {
    const m = this.monaco, kinds = m.languages.CompletionItemKind;
    const completionKinds = [0, kinds.Text, kinds.Method, kinds.Function, kinds.Constructor, kinds.Field, kinds.Variable, kinds.Class, kinds.Interface, kinds.Module, kinds.Property, kinds.Unit,
      kinds.Value, kinds.Enum, kinds.Keyword, kinds.Snippet, kinds.Color, kinds.File, kinds.Reference, kinds.Folder, kinds.EnumMember, kinds.Constant, kinds.Struct, kinds.Event, kinds.Operator, kinds.TypeParameter];
    const range = (r: Lsp) => new m.Range(r.start.line + 1, r.start.character + 1, r.end.line + 1, r.end.character + 1);
    const text = (content: Lsp): string => content == null ? "" : typeof content === "string" ? content : Array.isArray(content) ? content.map(text).join("\n\n") : content.value ?? "";
    const at = (attached: Attached, position: MonacoTypes.Position) => ({ textDocument: { uri: attached.uri }, position: { line: position.lineNumber - 1, character: position.column - 1 } });
    const location = (item: Lsp): MonacoTypes.languages.Location => {
      const known = this.byUri.get(sameUri(item.uri ?? item.targetUri));
      return { uri: known ? known.model.uri : m.Uri.parse(item.uri ?? item.targetUri), range: range(item.range ?? item.targetSelectionRange) };
    };
    const edit = (item: Lsp) => item.textEdit ? { newText: item.textEdit.newText, range: item.textEdit.range ?? item.textEdit.replace } : undefined;
    this.registrations.push(
      m.languages.registerCompletionItemProvider("python", {
        triggerCharacters: ["."],
        provideCompletionItems: async (model, position, context, token) => {
          const found = this.connectionOf(model);
          if (!found) return undefined;
          const result = await found.connection.request("textDocument/completion", { ...at(found.attached, position), context: { triggerKind: context.triggerKind + 1, triggerCharacter: context.triggerCharacter } }, token);
          if (!result || model.isDisposed()) return { suggestions: [] };
          const items: Lsp[] = result.items ?? result, word = model.getWordUntilPosition(position);
          const fallback = new m.Range(position.lineNumber, word.startColumn, position.lineNumber, word.endColumn);
          return { incomplete: result.isIncomplete, suggestions: items.map(item => ({
            label: item.label, kind: completionKinds[item.kind] ?? kinds.Text, detail: item.detail, sortText: item.sortText, filterText: item.filterText,
            insertText: edit(item)?.newText ?? item.insertText ?? item.label, range: edit(item) ? range(edit(item)!.range) : fallback,
            documentation: item.documentation ? { value: text(item.documentation) } : undefined, _raw: item, _session: found.attached.session })) as MonacoTypes.languages.CompletionItem[] };
        },
        resolveCompletionItem: async (item, token) => {
          const raw = (item as Lsp)._raw, found = this.sessions.get((item as Lsp)._session);
          if (item.documentation || !raw || !found) return item;
          const resolved = await found.connection.request("completionItem/resolve", raw, token);
          if (resolved?.documentation) item.documentation = { value: text(resolved.documentation) };
          if (resolved?.detail) item.detail = resolved.detail;
          return item;
        },
      }),
      m.languages.registerHoverProvider("python", {
        provideHover: async (model, position, token) => {
          const found = this.connectionOf(model);
          const hover = found && await found.connection.request("textDocument/hover", at(found.attached, position), token);
          return hover ? { range: hover.range ? range(hover.range) : undefined, contents: [{ value: text(hover.contents) }] } : undefined;
        },
      }),
      m.languages.registerSignatureHelpProvider("python", {
        signatureHelpTriggerCharacters: ["(", ","],
        provideSignatureHelp: async (model, position, token) => {
          const found = this.connectionOf(model);
          const help = found && await found.connection.request("textDocument/signatureHelp", at(found.attached, position), token);
          if (!help) return undefined;
          return { value: { activeSignature: help.activeSignature ?? 0, activeParameter: help.activeParameter ?? 0,
            signatures: help.signatures.map((signature: Lsp) => ({ label: signature.label, documentation: signature.documentation ? { value: text(signature.documentation) } : undefined,
              parameters: (signature.parameters ?? []).map((parameter: Lsp) => ({ label: parameter.label, documentation: parameter.documentation ? { value: text(parameter.documentation) } : undefined })) })) }, dispose() {} };
        },
      }),
      m.languages.registerDefinitionProvider("python", {
        provideDefinition: async (model, position, token) => {
          const found = this.connectionOf(model);
          const result = found && await found.connection.request("textDocument/definition", at(found.attached, position), token);
          // A stub and its source both answer; the first is the one to go to, and an editor shows nothing for the other file in a peek.
          return result ? ([] as Lsp[]).concat(result).slice(0, 1).map(location) : undefined;
        },
      }),
      m.languages.registerReferenceProvider("python", {
        provideReferences: async (model, position, context, token) => {
          const found = this.connectionOf(model);
          const result = found && await found.connection.request("textDocument/references", { ...at(found.attached, position), context: { includeDeclaration: context.includeDeclaration } }, token);
          return result ? (result as Lsp[]).map(location) : undefined;
        },
      }),
      m.languages.registerRenameProvider("python", {
        resolveRenameLocation: async (model, position, token) => {
          const found = this.connectionOf(model);
          const result = found && await found.connection.request("textDocument/prepareRename", at(found.attached, position), token);
          const target = result?.range ?? result;
          if (!target) return { rejectReason: "This name cannot be renamed.", range: new m.Range(1, 1, 1, 1), text: "" };
          const where = range(target);
          return { range: where, text: result.placeholder ?? model.getValueInRange(where) };
        },
        provideRenameEdits: async (model, position, newName, token) => {
          const found = this.connectionOf(model);
          const result = found && await found.connection.request("textDocument/rename", { ...at(found.attached, position), newName }, token);
          const changes: [string, Lsp[]][] = [...Object.entries<Lsp[]>(result?.changes ?? {}), ...(result?.documentChanges ?? []).filter((change: Lsp) => change.edits).map((change: Lsp) => [change.textDocument.uri, change.edits] as [string, Lsp[]])];
          // Only the documents an editor shows are changed here: a file that is not open would be edited behind the author's back.
          const closed = changes.filter(([uri]) => !this.byUri.has(sameUri(uri))).map(([uri]) => decodeURIComponent(uri.split("/").pop() ?? uri));
          if (closed.length) return { edits: [], rejectReason: `Renaming this also changes ${closed.join(", ")}. Open ${closed.length > 1 ? "them" : "it"} first.` };
          return { edits: changes.flatMap(([uri, list]) => list.map(item => ({ resource: this.byUri.get(sameUri(uri))!.model.uri, versionId: undefined, textEdit: { range: range(item.range), text: item.newText } }))) };
        },
      }),
      m.editor.registerEditorOpener({
        openCodeEditor: (source, resource, selection) => {
          if (source.getModel()?.uri.toString() === resource.toString()) return false;
          void this.jump(source, resource, selection as MonacoTypes.IRange | undefined).catch(error => this.host.report(error));
          return true;
        },
      }),
    );
  }

  /** Go to a definition in another file: the full editor changes tab, an embedded editor shows that file instead. */
  private async jump(source: Editor, resource: MonacoTypes.Uri, selection?: MonacoTypes.IRange) {
    const from = this.byModel.get(source.getModel()?.uri.toString() ?? "");
    const id = this.byModel.get(resource.toString())?.id ?? (resource.scheme === "file" && from ? await this.host.open(resource.toString(), from.id) : null);
    if (!id) return;
    if (this.host.fullEditor(source)) { this.host.show(id, selection ? { line: selection.startLineNumber, column: selection.startColumn } : undefined); return; }
    source.setModel(await this.host.model(id));
    if (selection) { source.setSelection(selection); source.revealRangeInCenter(selection); }
  }
}

/** The language service of this window: starts with the first Python file an editor shows and ends with the module. */
export function bindLanguage(ctx: ModuleContext): () => void {
  const host: LanguageHost = {
    shown: () => documents.shownDocuments(),
    snapshot: id => documents.snapshot(id),
    model: id => documents.model(id),
    resolve: id => privateRequest(ctx, "/language/resolve", jsonRequest({ documentId: id })),
    socket: session => ctx.routes.websocket(`/language/sessions/${encodeURIComponent(session)}`),
    open: async (uri, from) => {
      // A file of the project is opened as its document; any other (a library, the support's own files) read-only, as a reference.
      const { projectId: project, rootUri: root } = documents.snapshot(from).context;
      const inside = !!root && !!project && sameUri(uri).startsWith(sameUri(root).replace(/\/$/, "") + "/");
      try { return (await documents.open({ kind: "file", uri, ...(inside ? { projectId: project! } : {}) }, undefined, false, !inside)).documentId; }
      catch { return null; }
    },
    show: (id, position) => documents.show(id, position),
    fullEditor: editor => !!editor.getContainerDomNode().closest(".developer-editor-host"),
    report: error => documents.report(error),
  };
  let manager: LanguageManager | undefined, loading = false, stopped = false;
  const wake = () => {
    if (manager) { manager.schedule(); return; }
    if (loading || stopped || !documents.shownDocuments().some(id => pythonFile(documents.snapshot(id)))) return;
    loading = true;
    void import("./monaco").then(({ monaco }) => { if (!stopped) { manager = new LanguageManager(monaco, host); manager.schedule(); } }).finally(() => { loading = false; });
  };
  const release = documents.watch(wake);
  const changes = ctx.events.subscribe("developer.document_changed", payload => manager?.fileChanged(payload as Parameters<LanguageManager["fileChanged"]>[0]));
  ctx.setInterval(() => { void manager?.refresh(); }, REFRESH_MS);
  wake();
  return () => { stopped = true; release(); void changes.then(subscription => subscription.close()); manager?.dispose(); manager = undefined; };
}
