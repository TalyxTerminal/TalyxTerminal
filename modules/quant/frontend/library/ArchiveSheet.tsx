/* The archive sheet (library.js archiveSheet): what is kept and dropped; a version something still uses cannot be archived, and the sheet says who. */
import { t } from "../i18n";
import { useState } from "react";
import { TxButton, TxSheet } from "@talyx/sdk";
import { mdy } from "./format";
import type { LibraryApi, UserRow } from "./api";
import { UserLines } from "./StopSheet";

export type ArchiveUser = UserRow;

export function ArchiveBody({name, version, users, figures, stoppedAt, run, models = false, onOpenUser}: {name: string; version: number; users: ArchiveUser[]; figures: string; stoppedAt?: number | null; run?: number | null; models?: boolean;
  onOpenUser?(user: UserRow): void}) {
  return <div className="lb-sheet-body">{users.length > 0 && <><h4>{t("Using v{version} ({usersCount})", { version, usersCount: users.length })}</h4><UserLines users={users} onOpenUser={onOpenUser} /></>}<div className="lb-kv2">
    {stoppedAt != null && <><span>{t("Stopped")}</span><span>{mdy(stoppedAt)}</span></>}
    {users.length === 0 && <><span>{t("Used by")}</span><span>{t("nobody")}</span></>}
    <span>{t("Kept")}</span><span>{(figures ? t("this record, frozen code and settings, published figures ({figures})", { figures }) : t("this record, frozen code and settings, published figures"))}</span>
    <span>{t("Dropped")}</span><span>{(models ? t("the stored values, the window models and its frozen environment") : t("the stored values and its frozen environment"))}</span>
    <span>{t("Undo")}</span><span>{(run ? t("not possible; publish again from #{run} to get values", { run }) : t("not possible; publish again to get values"))}</span></div><span hidden>{`${name} v${version}`}</span></div>;
}
export function ArchiveFooter({version, users, block = "", busy, onCancel, onArchive}: {version: number; users: ArchiveUser[]; block?: string; busy?: boolean; onCancel(): void; onArchive(): void}) {
  const why = block || (users.length > 0 ? t("Still used by {count}: switch them first.", { count: users.length }) : "");
  return <div className="lb-sheet-foot">{why && <span className="lb-block">{why}</span>}<span className="lb-sp" />
    <TxButton onClick={onCancel}>{t("Cancel")}</TxButton>{why === "" && <TxButton variant="primary" disabled={busy} onClick={onArchive}>{t("Archive v{version}", { version })}</TxButton>}</div>;
}
export function ArchiveSheet({api, open, name, versionId, version, users, block, figures, stoppedAt, run, models, onClose, onDone, onOpenUser}: {api: LibraryApi; open: boolean; name: string; versionId: string; version: number; users: ArchiveUser[];
  block?: string; figures: string; models?: boolean; stoppedAt: number | null; run: number | null; onClose(): void; onDone(): void; onOpenUser?(user: UserRow): void}) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const archive = () => {setBusy(true); api.archive(versionId).then(() => {setBusy(false); onDone();}, reason => {setBusy(false); setError(reason instanceof Error ? reason.message : String(reason));});};
  return <TxSheet open={open} onOpenChange={value => {if (!value) onClose();}} title={t("Archive {name} v{version}", { name, version })} label={t("Archive")} footer={<ArchiveFooter version={version} users={users} block={block} busy={busy} onCancel={onClose} onArchive={archive} />}>
    <ArchiveBody name={name} version={version} users={users} figures={figures} stoppedAt={stoppedAt} run={run} models={models} onOpenUser={onOpenUser} />{error && <div className="lb-block lb-sheet-error" role="alert">{error}</div>}</TxSheet>;
}
