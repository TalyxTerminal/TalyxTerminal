import { failure, type Json, type ModuleContext } from "@talyx/sdk";
import type { ContractTypes } from "../../public/ts/types";
import { documents } from "./service";
import { showMarks, type Marks } from "./editorMarks";
import "./editorMarks.css";

type Editor = Awaited<ReturnType<typeof import("./monaco")["createEditor"]>>;

/** The embedded editor other modules show: the document's shared model in a Monaco editor. */
export function mountDocumentEditor(node: HTMLElement, ctx: ModuleContext) {
  let props = ctx.props as unknown as ContractTypes["view:editor"];
  let disposed = false, editor: Editor | undefined, documentId: string | undefined, turn = 0, requested = "", positioned: string | undefined;
  let releaseView: () => void = () => undefined, marks: Marks | undefined;
  const viewId = ctx.viewRef?.viewInstanceId ?? crypto.randomUUID();
  node.style.height = "100%"; node.style.minHeight = "0";
  const applyProps = () => {
    if (!editor || !documentId) return;
    editor.updateOptions({ readOnly: Boolean(props.readOnly) || documents.snapshot(documentId).readOnly });
    marks?.show(props.markers ?? []);
    const request = props.position ? props.positionRequestId ?? "initial" : undefined;
    if (props.position && request !== positioned) {
      positioned = request;
      editor.revealLineInCenter(props.position.line); editor.setPosition({ lineNumber: props.position.line, column: props.position.column ?? 1 }); editor.focus();
    }
  };
  const show = async () => {
    const current = ++turn;
    const id = props.documentId ?? (props.source ? (await documents.open(props.source, undefined, false)).documentId : undefined);
    if (!id) throw failure("invalid_input", "Choose a source or an open document.");
    const [model, { createEditor }] = await Promise.all([documents.model(id), import("./monaco")]);
    if (disposed || current !== turn) return;
    marks?.dispose(); marks = undefined; editor?.dispose(); releaseView(); documentId = id; positioned = undefined;
    releaseView = documents.registerView(viewId, id, () => { if (ctx.viewRef?.parentPanel) return ctx.platform.panels.close(ctx.viewRef.parentPanel); dispose(); }, ctx.viewRef?.parentPanel?.mountId);
    node.textContent = "";
    editor = createEditor(node, model, { readOnly: true, onSave: () => { void documents.save(id).catch(error => documents.report(error)); } });
    marks = await showMarks(editor, `view:${viewId}`);
    if (disposed || current !== turn) return;
    applyProps();
  };
  const update = () => {
    const identity = JSON.stringify([props.documentId ?? null, props.source ?? null]);
    if (identity === requested) { applyProps(); return; }
    requested = identity;
    void show().catch(error => { if (!disposed) node.textContent = error.message; });
  };
  let lastEmitted = "";
  const release = documents.watch(() => {
    if (!documentId || !documents.entries.has(documentId)) return;
    const snapshot = documents.snapshot(documentId), value = { documentId, version: snapshot.version, dirty: documents.isDirty(documentId) };
    if (JSON.stringify(value) !== lastEmitted) { lastEmitted = JSON.stringify(value); ctx.emit?.("changed", value); }
  });
  update();
  const dispose = () => { if (disposed) return; disposed = true; releaseView(); release(); marks?.dispose(); editor?.dispose(); };
  ctx.onDispose(dispose);
  return { prepareLeave: () => documents.prepareViewLeave(viewId), update(value: Json) { props = value as unknown as ContractTypes["view:editor"]; update(); }, dispose };
}
