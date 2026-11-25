import { t } from "../i18n";
import { ContractError, failure, type Json, type ModuleContext } from "@talyx/sdk";
import type * as MonacoTypes from "monaco-editor";
import { createClient, type CheckpointReference as Reference, type Document, type RestoreCheckpointInput, type ContributionSource, type FileSource } from "../../public/ts/types";
import { jsonRequest, privateRequest } from "../api";

export type DocumentSource = FileSource | ContributionSource;
export type DocumentSnapshot = Document;
export type CheckpointReference = Reference;
export type Position = { line: number; column?: number };
export interface ProjectSummary { projectId: string; name: string; root: string; rootUri: string; rootIdentity?: string; lastOpened?: string; kind: string; contextId: string; status: string; readOnly?: boolean }
type Model = MonacoTypes.editor.ITextModel;
// `snapshot` is the backend content the model's typing builds on. `other` is a newer backend content from another writer,
// held back because the model has typing not yet sent; `stale` asks for a reread once the current send settles.
interface Entry { snapshot: DocumentSnapshot; model?: Model; sending?: Promise<void>; queued: boolean; remote: boolean; dirty: boolean; other?: DocumentSnapshot; stale?: boolean }
interface View { documentId: string | null; group: string; close(): void | Promise<void> }
export interface CheckpointConflict { checkpoint: CheckpointReference; sourceKey: string; name: string; documentId: string }
export interface PendingClose { documentIds: string[]; error: string | null; working: boolean; close(): void | Promise<void> }
const loadMonaco = () => import("./monaco");

/** A whole document as the editor keeps it: without unsaved edits the backend leaves out the saved text, which is the content. */
const whole = (snapshot: DocumentSnapshot): DocumentSnapshot => snapshot.baseContent === null ? { ...snapshot, baseContent: snapshot.content } : snapshot;

/** Replace only the changed middle of a model, so a remote edit keeps the reader's cursor. */
export function minimalEdit(before: string, after: string) {
  let start = 0;
  while (start < before.length && start < after.length && before[start] === after[start]) start++;
  let end = 0;
  while (end < before.length - start && end < after.length - start && before[before.length - 1 - end] === after[after.length - 1 - end]) end++;
  return { start, end: before.length - end, text: after.slice(start, after.length - end) };
}

/** The window's view of backend documents. Each document has one Monaco model; every editor of it shares that model. */
export class DocumentStore {
  readonly entries = new Map<string, Entry>();
  readonly projects = new Map<string, ProjectSummary>();
  tabs: string[] = [];
  activeDocumentId: string | null = null;
  activeProjectId: string | null = null;
  reveal: { documentId: string; position: Position; id: number } | null = null;
  error: string | null = null;
  pendingClose: PendingClose | null = null;
  checkpointConflicts: CheckpointConflict[] = [];
  /** The version each conflict's Compare showed: another writer's buffer, or the file on disk. Choosing above the editor acts
      on that version; one that moved on since is refused, so a version the author never saw is not overwritten. */
  private compared = new Map<string, { other: number } | { disk: Json }>();
  private views = new Map<string, View>();
  private allowedClose = new Set<string>();
  private listeners = new Set<() => void>();
  private revision = 0;
  private ctx?: ModuleContext;
  watch = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  state = () => this.revision;
  changed() { this.revision++; for (const listener of this.listeners) listener(); }
  context(): ModuleContext { if (!this.ctx) throw failure("unavailable", t("Developer is not active.")); return this.ctx; }
  private client() { return createClient(this.context()); }
  report(error: unknown) { this.error = error instanceof Error ? error.message : String(error); this.changed(); }

  bind(ctx: ModuleContext): () => void {
    this.ctx = ctx;
    const projectsChanged = ctx.events.subscribe("developer.projects_changed", () => { void this.refreshProjects().catch(error => this.report(error)); });
    const subscription = ctx.events.subscribe("developer.document_changed", payload => {
      const event = payload as { documentId: string; version: number; kind: string; origin: string };
      const entry = this.entries.get(event.documentId);
      // Disk, source and save events can change the baseline or conflict state without a new buffer version.
      const changed = !!entry && (event.version !== entry.snapshot.version || event.kind === "saved" || event.kind === "resolved" || event.origin === "disk" || event.origin === "source");
      if (!entry || !changed) return;
      if (entry.sending) entry.stale = true;
      else void this.read(event.documentId).catch(error => this.report(error));
    });
    ctx.onBeforeStop(() => this.flush());
    const beforeUnload = (event: BeforeUnloadEvent) => { if ([...this.entries.values()].some(entry => entry.sending || entry.queued || entry.other)) event.preventDefault(); };
    window.addEventListener("beforeunload", beforeUnload);
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      void projectsChanged.then(value => value.close());
      void subscription.then(value => value.close());
      if (this.ctx === ctx) this.ctx = undefined;
    };
  }

  async refreshProjects(): Promise<ProjectSummary[]> {
    const { projects } = await privateRequest<{ projects: ProjectSummary[] }>(this.context(), "/projects");
    this.projects.clear();
    for (const project of projects) this.projects.set(project.projectId, project);
    this.changed(); return projects;
  }
  async openProject(directory: string, create = false): Promise<ProjectSummary> {
    const { project } = await privateRequest<{ project: ProjectSummary }>(this.context(), "/projects/open", jsonRequest({ directory, create }));
    this.projects.set(project.projectId, project); this.changed(); return project;
  }
  activateProject(projectId: string | null) { this.activeProjectId = projectId; this.changed(); }

  snapshot(documentId: string): DocumentSnapshot {
    const entry = this.entries.get(documentId); if (!entry) throw failure("source_unavailable", t("This document is no longer open."));
    return entry.snapshot;
  }
  /** Open in the backend; `reveal` also shows it as a tab of the full editor; `reference` opens a file outside every project read-only. */
  async open(source: DocumentSource, position?: Position, reveal = true, reference = false): Promise<DocumentSnapshot> {
    const opened = await this.client()["documents.open"]({ source, ...(reference ? { reference } : {}) });
    const snapshot = await this.readWhole(opened.documentId);
    this.accept(snapshot);
    if (reveal) this.show(snapshot.documentId, position);
    return snapshot;
  }
  show(documentId: string, position?: Position) {
    const snapshot = this.snapshot(documentId);
    if (!this.tabs.includes(documentId)) this.tabs = [...this.tabs, documentId];
    this.activeDocumentId = documentId;
    if (snapshot.context.projectId && !snapshot.readOnly && snapshot.source.kind === "file") this.activeProjectId = snapshot.context.projectId;
    if (position) this.reveal = { documentId, position, id: (this.reveal?.id ?? 0) + 1 };
    this.changed();
  }
  closeTab(documentId: string) {
    this.tabs = this.tabs.filter(id => id !== documentId);
    if (this.activeDocumentId === documentId) this.activeDocumentId = this.tabs.at(-1) ?? null;
    this.changed();
  }
  /** Closing an unsaved tab asks first; the buffer otherwise stays in the backend and in Recovery. */
  requestCloseTab(documentId: string) {
    if (!this.isDirty(documentId)) { this.closeTab(documentId); return; }
    this.pendingClose = { documentIds: [documentId], error: null, working: false, close: () => this.closeTab(documentId) };
    this.changed();
  }
  private accept(snapshot: DocumentSnapshot) {
    const entry = this.entries.get(snapshot.documentId);
    if (!entry) { this.entries.set(snapshot.documentId, { snapshot, queued: false, remote: false, dirty: snapshot.dirty }); this.changed(); return; }
    if (snapshot.version < (entry.other ?? entry.snapshot).version) return;
    if (entry.sending) { entry.stale = true; return; }
    this.take(entry, snapshot);
    if (!entry.other && !entry.snapshot.conflict) this.compared.delete(snapshot.documentId);
    entry.dirty = this.isDirty(snapshot.documentId);
    this.changed();
  }
  /** Take a newer backend content into the model, unless that would drop typing not yet sent: then both are kept for the author. */
  private take(entry: Entry, snapshot: DocumentSnapshot) {
    const acknowledged = entry.snapshot.content, local = entry.model?.getValue() ?? acknowledged;
    if (local !== acknowledged && snapshot.content !== acknowledged && snapshot.content !== local) { entry.other = snapshot; return; }
    entry.snapshot = snapshot; entry.other = undefined;
    if (local === acknowledged) this.replaceModel(entry, snapshot.content);
  }
  private replaceModel(entry: Entry, content: string) {
    const model = entry.model;
    if (!model || model.getValue() === content) return;
    const edit = minimalEdit(model.getValue(), content), start = model.getPositionAt(edit.start), end = model.getPositionAt(edit.end);
    entry.remote = true;
    try { model.pushEditOperations([], [{ range: { startLineNumber: start.lineNumber, startColumn: start.column, endLineNumber: end.lineNumber, endColumn: end.column }, text: edit.text }], () => null); }
    finally { entry.remote = false; }
  }
  /** Why a document needs the author's review, or null. */
  review(documentId: string): string | null {
    const entry = this.entries.get(documentId);
    if (entry?.other) return t("Changed elsewhere");
    if (entry?.snapshot.conflict) return entry.snapshot.externalState === "deleted" ? t("Deleted on disk") : t("Changed on disk");
    return null;
  }
  /** The whole document for the editor, over Developer's own route: a file the editor takes (up to 16 MiB) may not fit
      in one action answer. */
  private async readWhole(documentId: string) {
    return whole(await privateRequest<DocumentSnapshot>(this.context(), `/documents/${encodeURIComponent(documentId)}/text`));
  }
  async read(documentId: string): Promise<DocumentSnapshot> {
    await this.flush(documentId);
    this.accept(await this.readWhole(documentId)); return this.snapshot(documentId);
  }
  /** The shared model, created on first use. Typing is sent to the backend as it happens, one request at a time. */
  async model(documentId: string): Promise<Model> {
    const { monaco, editorLanguage, modelUri } = await loadMonaco();
    const entry = this.entries.get(documentId) ?? (await this.read(documentId), this.entries.get(documentId)!);
    if (entry.model && !entry.model.isDisposed()) return entry.model;
    const model = monaco.editor.createModel(entry.snapshot.content, editorLanguage(entry.snapshot.language), modelUri(documentId, entry.snapshot.name));
    entry.model = model;
    model.onDidChangeContent(() => {
      if (entry.remote) return;
      void this.send(documentId).catch(error => this.report(error));
      const dirty = this.isDirty(documentId);
      if (dirty !== entry.dirty) { entry.dirty = dirty; this.changed(); }
    });
    return model;
  }
  private send(documentId: string): Promise<void> {
    const entry = this.entries.get(documentId)!;
    if (entry.sending) { entry.queued = true; return entry.sending; }
    entry.sending = (async () => {
      do {
        entry.queued = false;
        const content = entry.model!.getValue();
        if (entry.other || content === entry.snapshot.content) continue;
        try {
          const edited = await this.client()["documents.edit"]({ documentId, expectedVersion: entry.snapshot.version, content });
          entry.snapshot = { ...entry.snapshot, ...edited, content };
        }
        catch (error) {
          if (!(error instanceof ContractError) || error.code !== "conflict") throw error;
          // Another writer moved the buffer on. Its change is never overwritten here.
          this.take(entry, await this.readWhole(documentId));
        }
      } while (entry.queued && !entry.other);
    })().finally(() => {
      entry.sending = undefined; entry.dirty = this.isDirty(documentId); this.changed();
      if (entry.stale) { entry.stale = false; void this.read(documentId).catch(error => this.report(error)); }
    });
    return entry.sending;
  }
  async flush(documentId?: string) {
    await Promise.all([...this.entries].filter(([id]) => !documentId || id === documentId).map(([, entry]) => entry.sending));
  }
  isDirty(documentId: string) {
    const entry = this.entries.get(documentId);
    return !!entry && !entry.snapshot.readOnly && (entry.model?.getValue() ?? entry.snapshot.content) !== entry.snapshot.baseContent;
  }
  dirty(): DocumentSnapshot[] { return [...this.entries.keys()].filter(id => this.isDirty(id)).map(id => this.snapshot(id)); }
  async save(documentId: string) {
    await this.flush(documentId);
    const entry = this.entries.get(documentId)!;
    if (entry.model && entry.model.getValue() !== entry.snapshot.content && !entry.other) await this.send(documentId);
    if (entry.other || (entry.model && entry.model.getValue() !== entry.snapshot.content)) throw failure("conflict", t("Another writer changed this document. Review both versions before saving."));
    const result = await this.client()["documents.save"]({ documentId, expectedVersion: this.snapshot(documentId).version });
    await this.read(documentId);
    if (!["saved", "clean"].includes(result.status)) throw failure("save_failed", result.message ?? t("The file remains unsaved."));
    return result;
  }
  unsaved() { return privateRequest<{ documents: DocumentSnapshot[] }>(this.context(), "/documents/unsaved"); }
  versions(documentId: string) {
    return privateRequest<{ base: { content: string; version: Json }; disk: { content: string; version: Json }; unsaved: DocumentSnapshot }>(this.context(), `/documents/${encodeURIComponent(documentId)}/versions`);
  }
  /** The other version of a document under review, for Compare: another writer's buffer, otherwise the file on disk. */
  async otherVersion(documentId: string): Promise<{ content: string; elsewhere: boolean }> {
    const other = this.entries.get(documentId)!.other;
    if (other) { this.compared.set(documentId, { other: other.version }); return { content: other.content, elsewhere: true }; }
    const { disk } = await this.versions(documentId);
    this.compared.set(documentId, { disk: disk.version });
    return { content: disk.content, elsewhere: false };
  }
  /** Keep this window's edits, or take the other version: another writer's buffer first, otherwise the disk version.
      `asCompared`: the version Compare showed, refused when it changed since; without a comparison, the current one. */
  async resolve(documentId: string, mode: "mine" | "external", asCompared = false) {
    await this.flush(documentId);
    const entry = this.entries.get(documentId)!, other = entry.other, shown = asCompared ? this.compared.get(documentId) : undefined;
    if (shown && ("other" in shown ? shown.other !== other?.version : !!other))
      throw failure("conflict", t("The other version changed again. Compare it before choosing."));
    if (other) {
      entry.snapshot = other; entry.other = undefined; this.compared.delete(documentId);
      if (mode === "external") this.replaceModel(entry, other.content);
      else await this.send(documentId);
      entry.dirty = this.isDirty(documentId); this.changed(); return;
    }
    const expectedVersion = entry.snapshot.version;
    const sourceVersion = shown && "disk" in shown ? shown.disk : (await this.versions(documentId)).disk.version;
    this.accept(await privateRequest<DocumentSnapshot>(this.context(), `/documents/${encodeURIComponent(documentId)}/resolve`, jsonRequest({ mode, expectedVersion, expectedSourceVersion: sourceVersion })));
    this.compared.delete(documentId);
    if (mode === "mine") await this.save(documentId);
  }

  async checkpointWorkbench(): Promise<CheckpointReference | undefined> {
    await this.flush();
    const selected = this.tabs.map(id => this.snapshot(id)).filter(item => !item.readOnly);
    if (!selected.length) return undefined;
    return (await this.client()["documents.create_checkpoint"]({ submissionId: `developer-workspace:${crypto.randomUUID()}`, documents: selected.map(item => ({ documentId: item.documentId, expectedVersion: item.version })) })).checkpoint;
  }
  async restoreCheckpoint(input: RestoreCheckpointInput) {
    const result = await this.client()["documents.restore_checkpoint"](input);
    for (const item of result.documents) if (item.documentId) await this.read(item.documentId);
    const settled = new Set(result.documents.filter(item => item.status !== "conflict").map(item => item.sourceKey));
    this.checkpointConflicts = [...this.checkpointConflicts.filter(item => !settled.has(item.sourceKey)),
      ...result.documents.filter(item => item.status === "conflict" && item.documentId && !this.checkpointConflicts.some(known => known.sourceKey === item.sourceKey))
        .map(item => ({ checkpoint: input.checkpoint, sourceKey: item.sourceKey, name: item.name, documentId: item.documentId! }))];
    this.changed(); return result;
  }
  /** Replace the current input with the version saved in the workspace; the current input is kept in a new checkpoint. */
  async useSaved(conflict: CheckpointConflict) {
    const current = await this.read(conflict.documentId);
    const result = await this.restoreCheckpoint({ checkpoint: conflict.checkpoint, sourceKeys: [conflict.sourceKey], resolutions: [{ sourceKey: conflict.sourceKey, expectedVersion: current.version, expectedSourceVersion: current.sourceVersion }] });
    const receipt = result.documents[0];
    if (!["restored", "unchanged"].includes(receipt.status)) throw failure("conflict", receipt.message ?? t("The source changed again. Compare the latest version and retry."));
  }
  keepCurrent(conflict: CheckpointConflict) { this.checkpointConflicts = this.checkpointConflicts.filter(item => item.sourceKey !== conflict.sourceKey); this.changed(); }
  readCheckpoint(checkpoint: CheckpointReference) {
    return privateRequest<{ documents: { sourceKey: string; name: string; content: string; source: DocumentSource }[] }>(this.context(), `/documents/checkpoints/${checkpoint.checkpointId}?digest=${checkpoint.digest}`);
  }

  /** A closing view asks only for documents no other open view still shows. */
  registerView(viewId: string, documentId: string | null, close: () => void | Promise<void>, group = viewId): () => void {
    const view = { documentId, group, close }; this.views.set(viewId, view); this.changed();
    return () => { if (this.views.get(viewId) === view) { this.views.delete(viewId); this.allowedClose.delete(viewId); this.changed(); } };
  }
  /** The documents some view shows right now: the open tabs for the full editor, the one document of an embedded editor. */
  shownDocuments(): string[] {
    const views = [...this.views.values()];
    return [...this.entries.keys()].filter(id => views.some(view => this.shown(view, id)));
  }
  private shown(view: View, documentId: string) { return view.documentId ? view.documentId === documentId : this.tabs.includes(documentId); }
  private lastDocuments(viewId: string) {
    const closing = this.views.get(viewId); if (!closing) return [];
    const views = [...this.views.values()];
    return this.dirty().filter(item => views.some(view => view.group === closing.group && this.shown(view, item.documentId))
      && !views.some(view => view.group !== closing.group && this.shown(view, item.documentId)));
  }
  prepareViewLeave(viewId: string) {
    if (this.allowedClose.has(viewId)) return { decision: "allow" as const };
    const affected = this.lastDocuments(viewId); if (!affected.length) return { decision: "allow" as const };
    this.pendingClose ??= { documentIds: affected.map(item => item.documentId), error: null, working: false, close: () => { this.allowedClose.add(viewId); return this.views.get(viewId)?.close(); } };
    this.changed();
    return { decision: "block" as const, message: t("Choose how to handle unsaved documents in Developer.") };
  }
  cancelClose() { this.pendingClose = null; this.changed(); }
  async finishClose(mode: "save" | "discard") {
    const request = this.pendingClose; if (!request || request.working) return;
    request.working = true; request.error = null; this.changed();
    try {
      for (const id of request.documentIds) {
        if (mode === "save") await this.save(id);
        else await this.resolve(id, "external");
      }
      if (mode === "save" && request.documentIds.some(id => this.isDirty(id))) throw failure("conflict", t("The document changed while saving. Save the latest edits before closing."));
      this.pendingClose = null; this.changed();
      await request.close();
    } catch (error) { request.error = error instanceof Error ? error.message : String(error); request.working = false; this.changed(); }
  }
}
export const documents = new DocumentStore();
