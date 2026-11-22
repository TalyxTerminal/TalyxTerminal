import { t } from "../i18n";
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
  return <TxPanel title={t("External changes")}>
    {error && <TxAlert title={t("Both versions are preserved")}>{error}</TxAlert>}
    {!conflicts.length ? <TxEmpty title={t("No files need review")} /> : conflicts.map(({ snapshot: item, other }) => <TxRow key={item.documentId} title={item.name} detail={documents.review(item.documentId)!}>
      <TxButton disabled={working} onClick={() => void run(async () => {
        if (other) compare({ documentId: item.documentId, name: t("{name} changed elsewhere", { name: item.name }), content: other.content });
        else { const { disk } = await documents.versions(item.documentId); compare({ documentId: item.documentId, name: t("{name} on disk", { name: item.name }), content: disk.content }); }
      })}>{t("Compare")}</TxButton>
      <TxButton disabled={working} onClick={() => void run(() => documents.resolve(item.documentId, "external"))}>{other ? t("Use other version") : t("Use disk version")}</TxButton>
      <TxButton disabled={working} onClick={() => void run(() => documents.resolve(item.documentId, "mine"))}>{t("Keep mine")}</TxButton>
    </TxRow>)}
  </TxPanel>;
}
