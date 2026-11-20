import { useEffect, useState, useSyncExternalStore } from "react";
import { TxAlert, TxButton, TxDialog, TxEmpty, TxPanel, TxRow, TxSection } from "@talyx/sdk";
import { documents, type CheckpointConflict, type DocumentSnapshot } from "../documents/service";
import type { CompareRequest } from "../documents/Compare";
import { message } from "./errors";

const fileName = (name: string) => name.split(/[\\/]/).at(-1) ?? name;
/** Where an unsaved source belongs, told by its project and project-relative path rather than a machine path. */
function sourceLocation(item: DocumentSnapshot) {
  const { projectId, rootUri } = item.context;
  const project = projectId ? documents.projects.get(projectId) : undefined;
  if (project && rootUri && item.uri.toLowerCase().startsWith(rootUri.toLowerCase() + "/")) return `${project.name} · ${decodeURIComponent(item.uri.slice(rootUri.length + 1))}`;
  return project?.name ?? (item.source.kind === "file" ? "Local file" : "Module content");
}

/** Unsaved edits the backend keeps, even after a restart, and saved workspace versions that differ from current input. */
export function Recovery({ compare, onOpened }: { compare(request: CompareRequest): void; onOpened(): void }) {
  useSyncExternalStore(documents.watch, documents.state, documents.state);
  const [items, setItems] = useState<DocumentSnapshot[]>([]), [error, setError] = useState<string | null>(null), [discard, setDiscard] = useState<DocumentSnapshot | null>(null), [working, setWorking] = useState(false);
  const refresh = async () => { try { setItems((await documents.unsaved()).documents); setError(null); } catch (reason) { setError(message(reason)); } };
  useEffect(() => { void refresh(); }, []);
  const run = async (work: () => Promise<unknown>) => { setWorking(true); setError(null); try { await work(); } catch (reason) { setError(message(reason)); } finally { setWorking(false); } };
  const compareSaved = (conflict: CheckpointConflict) => run(async () => {
    const saved = (await documents.readCheckpoint(conflict.checkpoint)).documents.find(item => item.sourceKey === conflict.sourceKey)!;
    compare({ documentId: conflict.documentId, name: `${conflict.name} saved in workspace`, content: saved.content });
  });
  const conflicts = documents.checkpointConflicts;
  return <>
    <TxPanel title="Recovery" actions={<TxButton size="sm" onClick={() => void refresh()} disabled={working}>Refresh</TxButton>}>
      {error && !discard && <TxAlert title="Recovery needs attention">{error}</TxAlert>}
      {!items.length && !conflicts.length ? <TxEmpty title="Nothing to recover" text="Unsaved edits stay here, even after a restart, until you save or discard them." /> : <div className="developer-recovery-list">
        {conflicts.length > 0 && <TxSection title="Needs review">
          {conflicts.map(item => <TxRow key={item.sourceKey} title={item.name} detail="Saved workspace version differs">
            <TxButton size="sm" disabled={working} onClick={() => void compareSaved(item)}>Compare</TxButton>
            <TxButton size="sm" disabled={working} onClick={() => void run(() => documents.useSaved(item))}>Use saved version</TxButton>
            <TxButton size="sm" disabled={working} onClick={() => documents.keepCurrent(item)}>Keep current</TxButton>
          </TxRow>)}
        </TxSection>}
        {items.length > 0 && <TxSection title="Unsaved changes">
          {items.map(item => <TxRow key={item.documentId} title={fileName(item.name)} detail={sourceLocation(item)}>
            <TxButton size="sm" disabled={working} onClick={() => void run(async () => { await documents.open(item.source); onOpened(); })}>Open</TxButton>
            <TxButton size="sm" disabled={working} onClick={() => { setError(null); setDiscard(item); }}>Discard…</TxButton>
          </TxRow>)}
        </TxSection>}
      </div>}
    </TxPanel>
    <TxDialog open={!!discard} onOpenChange={open => { if (!open && !working) setDiscard(null); }} title="Discard unsaved changes" footer={<><TxButton disabled={working} onClick={() => setDiscard(null)}>Cancel</TxButton><TxButton variant="primary" disabled={working} onClick={() => { const item = discard; if (item) void run(async () => { await documents.open(item.source, undefined, false); await documents.resolve(item.documentId, "external"); setDiscard(null); await refresh(); }); }}>Discard</TxButton></>}>
      <p>Discard the unsaved changes to {discard && fileName(discard.name)}? The saved file is kept.</p>
      {error && <TxAlert title="Changes were not discarded">{error}</TxAlert>}
    </TxDialog>
  </>;
}
