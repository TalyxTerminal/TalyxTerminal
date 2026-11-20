import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { TxButton, TxEmpty, TxStatus, TxTabs } from "@talyx/sdk";
import type * as Monaco from "monaco-editor";
import { documents } from "../documents/service";
import { message } from "./errors";

type Editor = Monaco.editor.IStandaloneCodeEditor;

/** Tabs of the documents open in the full editor and one Monaco editor showing the active one. */
export function EditorPane({ onReviewChanges, onError }: { onReviewChanges(): void; onError(message: string): void }) {
  useSyncExternalStore(documents.watch, documents.state, documents.state);
  const host = useRef<HTMLDivElement>(null), editor = useRef<Editor | null>(null), views = useRef(new Map<string, Monaco.editor.ICodeEditorViewState | null>());
  const [shown, setShown] = useState<string | null>(null);
  const active = documents.activeDocumentId, reveal = documents.reveal;
  useEffect(() => {
    if (!active || !host.current) return;
    let cancelled = false;
    void Promise.all([documents.model(active), import("../documents/monaco")]).then(([model, { createEditor }]) => {
      if (cancelled || !host.current) return;
      const readOnly = documents.snapshot(active).readOnly;
      if (!editor.current) editor.current = createEditor(host.current, model, { readOnly, onSave: () => { const id = documents.activeDocumentId; if (id) void documents.save(id).catch(error => onError(message(error))); } });
      else if (editor.current.getModel() !== model) {
        if (shown) views.current.set(shown, editor.current.saveViewState());
        editor.current.setModel(model);
        editor.current.restoreViewState(views.current.get(active) ?? null);
      }
      editor.current.updateOptions({ readOnly });
      setShown(active);
      editor.current.focus();
    }).catch(error => { if (!cancelled) onError(message(error)); });
    return () => { cancelled = true; };
  }, [active]);
  useEffect(() => {
    if (!reveal || reveal.documentId !== shown || !editor.current) return;
    editor.current.revealLineInCenter(reveal.position.line);
    editor.current.setPosition({ lineNumber: reveal.position.line, column: reveal.position.column ?? 1 });
    editor.current.focus();
  }, [reveal?.id, shown]);
  useEffect(() => () => { editor.current?.dispose(); editor.current = null; }, []);
  const names = documents.tabs.map(id => documents.snapshot(id).name);
  const label = (id: string) => {
    const snapshot = documents.snapshot(id), project = snapshot.context.projectId ? documents.projects.get(snapshot.context.projectId) : undefined;
    return names.filter(name => name === snapshot.name).length > 1 && project ? `${snapshot.name} · ${project.name}` : snapshot.name;
  };
  const snapshot = active ? documents.snapshot(active) : null, review = active ? documents.review(active) : null;
  return <div className="developer-editor-pane">
    {documents.tabs.length > 0 && <div className="developer-tabs">
      <TxTabs closable label="Open files" value={active ?? ""} onChange={id => documents.show(id)} onClose={id => documents.requestCloseTab(id)}
        options={documents.tabs.map(id => ({ value: id, label: label(id), title: documents.snapshot(id).uri, dirty: documents.isDirty(id) }))} />
    </div>}
    {review && <div className="developer-editor-notice"><TxStatus tone="review" label={review} /><span>{active && documents.entries.get(active)?.other ? "Your edits and the other version are both kept." : "Your unsaved edits and the disk version are both kept."}</span><TxButton size="sm" onClick={onReviewChanges}>Review</TxButton></div>}
    {snapshot?.readOnly && <div className="developer-editor-notice"><TxStatus tone="off" label="Read only" /></div>}
    <div ref={host} className="developer-editor-host" hidden={!active} />
    {!active && <TxEmpty title="No file open" text="Choose a file in the project tree, or open the project in VS Code." />}
  </div>;
}
