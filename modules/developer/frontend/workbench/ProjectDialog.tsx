import { useState } from "react";
import { TxAlert, TxButton, TxDialog, TxDisclosure, TxField, TxInput, type ModuleContext } from "@talyx/sdk";
import { createClient } from "../../public/ts/types";
import { documents } from "../documents/service";
import { message } from "./errors";

/** The id a module gets from its name (as the backend makes it): lowercase letters, digits and underscores. */
export const moduleIdentifier = (name: string) => name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/^[^a-z]+/, "").slice(0, 64);

/** New TALYX module (a name, and an id under Advanced) or Open folder (a location). */
export function ProjectDialog({ ctx, mode, onClose, onCreated }: {
  ctx: ModuleContext; mode: "open" | "new"; onClose(): void; onCreated(projectId: string, file?: string): Promise<void>;
}) {
  const [directory, setDirectory] = useState(""), [name, setName] = useState(""), [identifier, setIdentifier] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null), [working, setWorking] = useState(false);
  const moduleId = identifier ?? moduleIdentifier(name);
  const invalidId = mode === "new" && !!name.trim() && !/^[a-z][a-z0-9_]{0,63}$/.test(moduleId);
  const submit = async () => {
    setWorking(true); setError(null);
    try {
      if (mode === "new") {
        const made = await createClient(ctx)["projects.create_module"]({ name: name.trim(), moduleId });
        await onCreated(made.projectId, `${made.rootUri}/backend/entry.py`);
      } else await onCreated((await documents.openProject(directory.trim())).projectId);
      onClose();
    } catch (reason) { setError(message(reason)); }
    finally { setWorking(false); }
  };
  const ready = mode === "new" ? !!name.trim() && !invalidId : !!directory.trim();
  return <TxDialog open onOpenChange={open => { if (!open && !working) onClose(); }} title={mode === "new" ? "New TALYX module" : "Open folder"} footer={<>
    <TxButton disabled={working} onClick={onClose}>Cancel</TxButton>
    <TxButton variant="primary" loading={working} disabled={!ready} onClick={() => void submit()}>{mode === "new" ? "Create module" : "Open"}</TxButton>
  </>}>
    {error && <TxAlert title="Unable to continue">{error}</TxAlert>}
    {mode === "new" && <TxField label="Name"><TxInput autoFocus value={name} disabled={working} onChange={event => setName(event.target.value)} /></TxField>}
    {mode === "new" && <TxDisclosure label="Advanced" open={invalidId || undefined}>
      <TxField label="Module id" error={invalidId ? "Use lowercase letters, numbers and underscores; start with a letter." : undefined}>
        <TxInput value={moduleId} disabled={working} onChange={event => setIdentifier(event.target.value)} /></TxField>
    </TxDisclosure>}
    {mode === "open" && <TxField label="Location"><TxInput aria-label="Location" autoFocus placeholder="D:\Projects\folder" value={directory} disabled={working} onChange={event => setDirectory(event.target.value)} /></TxField>}
  </TxDialog>;
}
