import { t } from "../i18n";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { ContractError, TxButton, TxEmpty, TxStatus, TxTabs } from "@talyx/sdk";
import type * as Monaco from "monaco-editor";
import type { CompareRequest } from "../documents/Compare";
import { documents } from "../documents/service";
import { message } from "./errors";

type Editor = Monaco.editor.IStandaloneCodeEditor;

/** Tabs of the documents open in the full editor and one Monaco editor showing the active one. A document changed on disk
    or by another writer under this window's edits is marked on its tab; above the editor it offers the choice, both
    versions kept until the author makes it. */
export function EditorPane({ compare, onError }: { compare(request: CompareRequest): void; onError(message: string): void }) {
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
  const other = active ? !!documents.entries.get(active)?.other : false;
  const [working, setWorking] = useState(false);
  const run = (work: () => Promise<unknown>) => { setWorking(true); void work().catch(error => onError(message(error))).finally(() => setWorking(false)); };
  const showComparison = async (documentId: string) => {
    const { name } = documents.snapshot(documentId), { content, elsewhere } = await documents.otherVersion(documentId);
    compare({ documentId, name: elsewhere ? t("{name} changed elsewhere", { name }) : t("{name} on disk", { name }), content });
  };
  // Acts on the version Compare showed; one that changed again since is shown instead.
  const choose = async (documentId: string, mode: "external" | "mine") => {
    try { await documents.resolve(documentId, mode, true); }
    catch (error) {
      if (error instanceof ContractError && error.code === "conflict") await showComparison(documentId);
      throw error;
    }
  };
  return <div className="developer-editor-pane">
    {documents.tabs.length > 0 && <div className="developer-tabs">
      <TxTabs closable label={t("Open files")} value={active ?? ""} onChange={id => documents.show(id)} onClose={id => documents.requestCloseTab(id)}
        options={documents.tabs.map(id => ({ value: id, label: label(id), title: documents.snapshot(id).uri, dirty: documents.isDirty(id), marked: documents.review(id) ?? undefined }))} />
    </div>}
    {review && active && <div className="developer-editor-notice" title={other ? t("Your edits and the other version are both kept.") : t("Your unsaved edits and the disk version are both kept.")}>
      <TxStatus tone="review" label={review} /><span className="tx-spacer" />
      <TxButton size="sm" variant="quiet" disabled={working} onClick={() => run(() => showComparison(active))}>{t("Compare")}</TxButton>
      <TxButton size="sm" disabled={working} onClick={() => run(() => choose(active, "external"))}>{other ? t("Use other version") : t("Use disk version")}</TxButton>
      <TxButton size="sm" disabled={working} onClick={() => run(() => choose(active, "mine"))}>{t("Keep mine")}</TxButton>
    </div>}
    {snapshot?.readOnly && <div className="developer-editor-notice"><TxStatus tone="off" label={t("Read only")} /></div>}
    <div ref={host} className="developer-editor-host" hidden={!active} />
    {!active && <TxEmpty title={t("No file open")} text={t("Choose a file in the project tree, or open the project in VS Code.")} />}
  </div>;
}
