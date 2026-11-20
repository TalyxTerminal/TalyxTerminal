import { useEffect, useState, useSyncExternalStore } from "react";
import { TxAlert, TxButton, TxEmpty, TxField, TxPanel, TxRow, TxSelect, type ModuleContext } from "@talyx/sdk";
import { createClient } from "../../public/ts/types";
import { documents, type DocumentSnapshot } from "../documents/service";
import type { CompareRequest } from "../documents/Compare";
import { history, historyRevision, type FileHistory as History, type HistoryRevision } from "./api";
import { message } from "./errors";

export function documentPath(snapshot: DocumentSnapshot) {
  const root = snapshot.context.rootUri;
  if (snapshot.source.kind !== "file" || !snapshot.context.projectId || !root || !snapshot.uri.toLowerCase().startsWith(root.toLowerCase() + "/")) throw new Error("This document does not belong to a project directory.");
  return decodeURIComponent(snapshot.uri.slice(root.length + 1));
}
export function FileHistory({ ctx, compare }: { ctx: ModuleContext; compare(request: CompareRequest): void }) {
  useSyncExternalStore(documents.watch, documents.state, documents.state);
  const files = documents.tabs.map(id => documents.snapshot(id)).filter(item => !item.readOnly && item.source.kind === "file" && !!item.context.projectId);
  const [documentId, setDocumentId] = useState(documents.activeDocumentId ?? files.at(-1)?.documentId ?? ""), [result, setResult] = useState<History | null>(null), [error, setError] = useState<string | null>(null), [working, setWorking] = useState(false);
  const file = files.find(item => item.documentId === documentId);
  const load = async () => { if (file) setResult(await history(ctx, file.context.projectId!, documentPath(file))); };
  useEffect(() => { setResult(null); setError(null); void load().catch(reason => setError(message(reason))); }, [ctx, documentId]);
  const perform = async (revision: HistoryRevision, mode: "compare" | "restore") => {
    if (!file) return; setWorking(true); setError(null);
    try {
      const projectId = file.context.projectId!, path = documentPath(file);
      if (mode === "compare") {
        const previous = await historyRevision(ctx, projectId, path, revision.revisionId);
        compare({ documentId: file.documentId, name: `${file.name} ${new Date(revision.createdAt).toLocaleString()}`, content: previous.content });
      } else {
        if (documents.isDirty(file.documentId)) throw new Error("Save or discard this file's current edits before restoring a revision.");
        const result = await createClient(ctx)["projects.restore_revision"]({ projectId, path, revisionId: revision.revisionId,
          expectedVersion: file.sourceVersion as string | null });
        if (result.status === "conflict") throw new Error(result.message ?? "The file changed before the revision could be restored.");
        await documents.read(file.documentId);
        await load();
      }
    } catch (reason) { setError(message(reason)); }
    finally { setWorking(false); }
  };
  return <TxPanel title="File history" actions={<TxButton disabled={!file || working} onClick={() => void load().catch(reason => setError(message(reason)))}>Refresh</TxButton>}>
    {error && <TxAlert title="File history needs attention">{error}</TxAlert>}
    {files.length ? <TxField label="Open file"><TxSelect value={documentId} disabled={working} onChange={event => setDocumentId(event.target.value)}>{files.map(item => <option key={item.documentId} value={item.documentId}>{documents.projects.get(item.context.projectId!)?.name}: {documentPath(item)}</option>)}</TxSelect></TxField> : <TxEmpty title="Open a project file to view its history" />}
    {result?.warnings.map((warning, index) => <TxAlert key={index} title="Some history is unavailable">{warning}</TxAlert>)}
    {result && !result.revisions.length && <TxEmpty title="No recorded revisions" />}
    {result?.revisions.map(revision => <TxRow key={revision.revisionId} title={new Date(revision.createdAt).toLocaleString()} detail={revision.reason}><TxButton disabled={working} onClick={() => void perform(revision, "compare")}>Compare</TxButton><TxButton disabled={working} onClick={() => void perform(revision, "restore")}>Restore revision</TxButton></TxRow>)}
  </TxPanel>;
}
