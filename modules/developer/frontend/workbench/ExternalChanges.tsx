import { useState, useSyncExternalStore } from "react";
import { TxAlert, TxButton, TxEmpty, TxPanel, TxRow } from "@talyx/sdk";
import { documents } from "../documents/service";
import type { CompareRequest } from "../documents/Compare";
import { message } from "./errors";

/** Files changed on disk, or by another writer, while this window had edits of its own: both versions stay until the author chooses. */
export function ExternalChanges({ compare }: { compare(request: CompareRequest): void }) {
  useSyncExternalStore(documents.watch, documents.state, documents.state);
  const conflicts = [...documents.entries.values()].filter(entry => documents.review(entry.snapshot.documentId));
  const [working, setWorking] = useState(false), [error, setError] = useState<string | null>(null);
  const run = async (work: () => Promise<unknown>) => { setWorking(true); setError(null); try { await work(); } catch (reason) { setError(message(reason)); } finally { setWorking(false); } };
  return <TxPanel title="External changes">
    {error && <TxAlert title="Both versions are preserved">{error}</TxAlert>}
    {!conflicts.length ? <TxEmpty title="No files need review" /> : conflicts.map(({ snapshot: item, other }) => <TxRow key={item.documentId} title={item.name} detail={documents.review(item.documentId)!}>
      <TxButton disabled={working} onClick={() => void run(async () => {
        if (other) compare({ documentId: item.documentId, name: `${item.name} changed elsewhere`, content: other.content });
        else { const { disk } = await documents.versions(item.documentId); compare({ documentId: item.documentId, name: `${item.name} on disk`, content: disk.content }); }
      })}>Compare</TxButton>
      <TxButton disabled={working} onClick={() => void run(() => documents.resolve(item.documentId, "external"))}>{other ? "Use other version" : "Use disk version"}</TxButton>
      <TxButton disabled={working} onClick={() => void run(() => documents.resolve(item.documentId, "mine"))}>Keep mine</TxButton>
    </TxRow>)}
  </TxPanel>;
}
