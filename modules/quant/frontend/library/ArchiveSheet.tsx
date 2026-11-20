/* The archive sheet (library.js archiveSheet): what is kept and dropped; a version something still uses cannot be archived, and the sheet says who. */
import { useState } from "react";
import { TxButton, TxSheet } from "@talyx/sdk";
import { mdy } from "./format";
import type { LibraryApi, UserRow } from "./api";
import { UserLines } from "./StopSheet";

export type ArchiveUser = UserRow;

export function ArchiveBody({name, version, users, figures, stoppedAt, run, models = false, onOpenUser}: {name: string; version: number; users: ArchiveUser[]; figures: string; stoppedAt?: number | null; run?: number | null; models?: boolean;
  onOpenUser?(user: UserRow): void}) {
  return <div className="lb-sheet-body">{users.length > 0 && <><h4>{`Using v${version} (${users.length})`}</h4><UserLines users={users} onOpenUser={onOpenUser} /></>}<div className="lb-kv2">
    {stoppedAt != null && <><span>Stopped</span><span>{mdy(stoppedAt)}</span></>}
    {users.length === 0 && <><span>Used by</span><span>nobody</span></>}
    <span>Kept</span><span>{`this record, frozen code and settings, published figures${figures ? ` (${figures})` : ""}`}</span>
    <span>Dropped</span><span>{`the stored values${models ? ", the window models" : ""} and its frozen environment`}</span>
    <span>Undo</span><span>{`not possible; publish again${run ? ` from #${run}` : ""} to get values`}</span></div><span hidden>{`${name} v${version}`}</span></div>;
}
export function ArchiveFooter({version, users, block = "", busy, onCancel, onArchive}: {version: number; users: ArchiveUser[]; block?: string; busy?: boolean; onCancel(): void; onArchive(): void}) {
  const why = block || (users.length > 0 ? `Still used by ${users.length}: switch them first.` : "");
  return <div className="lb-sheet-foot">{why && <span className="lb-block">{why}</span>}<span className="lb-sp" />
    <TxButton onClick={onCancel}>Cancel</TxButton>{why === "" && <TxButton variant="primary" disabled={busy} onClick={onArchive}>{`Archive v${version}`}</TxButton>}</div>;
}
export function ArchiveSheet({api, open, name, versionId, version, users, block, figures, stoppedAt, run, models, onClose, onDone, onOpenUser}: {api: LibraryApi; open: boolean; name: string; versionId: string; version: number; users: ArchiveUser[];
  block?: string; figures: string; models?: boolean; stoppedAt: number | null; run: number | null; onClose(): void; onDone(): void; onOpenUser?(user: UserRow): void}) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const archive = () => {setBusy(true); api.archive(versionId).then(() => {setBusy(false); onDone();}, reason => {setBusy(false); setError(reason instanceof Error ? reason.message : String(reason));});};
  return <TxSheet open={open} onOpenChange={value => {if (!value) onClose();}} title={`Archive ${name} v${version}`} label="Archive" footer={<ArchiveFooter version={version} users={users} block={block} busy={busy} onCancel={onClose} onArchive={archive} />}>
    <ArchiveBody name={name} version={version} users={users} figures={figures} stoppedAt={stoppedAt} run={run} models={models} onOpenUser={onOpenUser} />{error && <div className="lb-block lb-sheet-error" role="alert">{error}</div>}</TxSheet>;
}
