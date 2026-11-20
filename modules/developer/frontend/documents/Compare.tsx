import { useEffect, useRef, useState } from "react";
import { TxAlert, TxButton, TxDialog } from "@talyx/sdk";
import { documents } from "./service";

export interface CompareRequest { documentId: string; name: string; content: string }

/** The other version on the left, the live document on the right. */
export function CompareDialog({ request, onClose }: { request: CompareRequest | null; onClose(): void }) {
  const host = useRef<HTMLDivElement>(null), [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!request) return;
    let disposed = false, view: { dispose(): void } | undefined;
    setError(null);
    void Promise.all([documents.model(request.documentId), import("./monaco")]).then(([model, { createCompare }]) => {
      if (disposed || !host.current) return;
      view = createCompare(host.current, { name: request.name, content: request.content, language: documents.snapshot(request.documentId).language }, model);
    }).catch(reason => setError(reason instanceof Error ? reason.message : String(reason)));
    return () => { disposed = true; view?.dispose(); };
  }, [request]);
  return <TxDialog open={!!request} onOpenChange={open => { if (!open) onClose(); }} size="wide" title={request ? `${request.name} ↔ current` : "Compare"} footer={<TxButton onClick={onClose}>Close</TxButton>}>
    {error && <TxAlert title="Comparison unavailable">{error}</TxAlert>}
    <div ref={host} className="developer-compare" />
  </TxDialog>;
}
