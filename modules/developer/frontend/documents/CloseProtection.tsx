import { useSyncExternalStore } from "react";
import { createRoot } from "react-dom/client";
import { ModuleContextProvider, TxAlert, TxButton, TxDialog, TxRoot, type ModuleContext } from "@talyx/sdk";
import { documents } from "./service";

function CloseProtection() {
  useSyncExternalStore(documents.watch, documents.state, documents.state);
  const request = documents.pendingClose;
  return <TxDialog open={!!request} onOpenChange={open => { if (!open) documents.cancelClose(); }} title="Unsaved documents" footer={<>
    <TxButton onClick={() => documents.cancelClose()}>Cancel</TxButton>
    <TxButton disabled={request?.working} onClick={() => void documents.finishClose("discard")}>Discard and close</TxButton>
    <TxButton variant="primary" disabled={request?.working} onClick={() => void documents.finishClose("save")}>Save and close</TxButton>
  </>}>
    <p>Save your changes before closing, or discard them.</p>
    {request?.documentIds.map(id => <div key={id}><span>{documents.snapshot(id).name}</span></div>)}
    {request?.error && <TxAlert title="Document remains open">{request.error}</TxAlert>}
  </TxDialog>;
}
export function mountCloseProtection(ctx: ModuleContext): () => void {
  // Module activation has no mounted panel overlay. This SDK surface must also
  // survive the last full editor closing while another embedded editor exists.
  const overlay = document.createElement("div"), container = document.createElement("div");
  overlay.className = "tx-root"; overlay.setAttribute("data-talyx-overlays", "developer-close");
  overlay.append(container); document.body.append(overlay);
  const root = createRoot(container);
  root.render(<ModuleContextProvider.Provider value={ctx}><TxRoot overlay={overlay}><CloseProtection /></TxRoot></ModuleContextProvider.Provider>);
  return () => { root.unmount(); overlay.remove(); };
}
