/* Line marks an embedding module gives the editor view (props.markers): Monaco markers under the view's own owner, so the language service's
   and development supports' diagnostics stay as they are; the marked lines tinted; the first mark with detail shown under its line. */
import type { ContractTypes } from "../../public/ts/types";

type Mark = NonNullable<ContractTypes["view:editor"]["markers"]>[number];
type Editor = Awaited<ReturnType<typeof import("./monaco")["createEditor"]>>;
export type Marks = { show(marks: Mark[]): void; dispose(): void };

export async function showMarks(editor: Editor, owner: string): Promise<Marks> {
  const { monaco } = await import("./monaco");
  const severity = { error: monaco.MarkerSeverity.Error, warning: monaco.MarkerSeverity.Warning, information: monaco.MarkerSeverity.Info };
  const lines = editor.createDecorationsCollection();
  let zone: string | null = null, shown = "[]", model = editor.getModel();
  const clear = () => {
    if (model && !model.isDisposed()) monaco.editor.setModelMarkers(model, owner, []);
    lines.clear();
    if (zone !== null) { const id = zone; editor.changeViewZones(access => access.removeZone(id)); zone = null; }
  };
  const peek = (mark: Mark) => {
    const node = document.createElement("div");
    node.className = "developer-mark-peek";
    node.dataset.severity = mark.severity;
    if (mark.title) { const lead = document.createElement("b"); lead.textContent = mark.title; node.append(lead, " "); }
    node.append(mark.detail ?? mark.message);
    if (mark.source) { const source = document.createElement("span"); source.className = "developer-mark-source"; source.textContent = mark.source; node.append(source); }
    return node;
  };
  const show = (marks: Mark[]) => {
    const next = JSON.stringify(marks);
    if (next === shown && model === editor.getModel()) return;
    clear();
    shown = next; model = editor.getModel();
    const current = model;
    if (!current || current.isDisposed() || !marks.length) return;
    const last = current.getLineCount(), inside = marks.filter(mark => mark.line <= last);
    const endOf = (mark: Mark) => Math.min(Math.max(mark.endLine ?? mark.line, mark.line), last);
    monaco.editor.setModelMarkers(current, owner, inside.map(mark => ({
      message: mark.source ? `${mark.message} (${mark.source})` : mark.message, severity: severity[mark.severity], startLineNumber: mark.line,
      startColumn: mark.column ?? (current.getLineFirstNonWhitespaceColumn(mark.line) || 1), endLineNumber: endOf(mark), endColumn: mark.endColumn ?? current.getLineMaxColumn(endOf(mark)),
    })));
    lines.set(inside.map(mark => ({ range: new monaco.Range(mark.line, 1, endOf(mark), 1),
      options: { isWholeLine: true, className: `developer-mark-line developer-mark-${mark.severity}`, linesDecorationsClassName: `developer-mark-gutter developer-mark-${mark.severity}` } })));
    const first = inside.find(mark => mark.detail);
    if (!first) return;
    const node = peek(first);
    // Measure the peek at the editor's width before Monaco gives it room under the line.
    node.style.cssText = `position:absolute;visibility:hidden;width:${Math.max(200, editor.getLayoutInfo().contentWidth - 24)}px`;
    editor.getDomNode()?.append(node);
    const height = Math.ceil(node.getBoundingClientRect().height) + 8;
    node.remove(); node.style.cssText = "";
    editor.changeViewZones(access => { zone = access.addZone({ afterLineNumber: endOf(first), heightInPx: height, domNode: node }); });
  };
  return { show, dispose: clear };
}