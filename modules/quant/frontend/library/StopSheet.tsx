/* The stop sheet: lists who uses the version first and what each will see; a published model is warned. */
import { useEffect, useState, type ReactNode } from "react";
import { ContractError, TxButton, TxSheet, TxSkeleton } from "@talyx/sdk";
import { md, mdy } from "./format";
import type { LibraryApi, StopPreview, UserRow } from "./api";

const DAY = 86_400_000;
const label = (kind: string) => kind.charAt(0).toUpperCase() + kind.slice(1);

function Line({user, preview}: {user: UserRow; preview: StopPreview}) {
  const next = preview.producing[0];
  if (user.unattended) return <span className="lb-warn">{`Published ${user.kind.toLowerCase()}, reads it every day: from ${preview.lastDatetime === null ? "the next close" : md(preview.lastDatetime + DAY)} its periods become upstream missing.`}</span>;
  return <span className="lb-t3">{`Research: keeps the values${preview.lastDatetime === null ? "" : ` to ${mdy(preview.lastDatetime)}`} and is told ${next ? `v${next} is available` : "it stopped"}.`}</span>;
}

/** Who uses a version, one line each: its name (numbered when two share one), what it is, how it reads the version, and Open to go and change it. */
export function UserLines({users, onOpenUser, line}: {users: UserRow[]; onOpenUser?(user: UserRow): void; line?(user: UserRow): ReactNode}) {
  const count = (name: string) => users.filter(user => user.name === name).length;
  const nth = (user: UserRow) => users.filter(other => other.name === user.name).indexOf(user) + 1;
  return <div className="lb-ulist">{users.map(user => <div key={user.ownerId}><b>{user.name}{count(user.name) > 1 ? ` (${nth(user)})` : ""}</b>
    <span className="lb-t3">{[user.name.toLowerCase() === user.kind.toLowerCase() ? "" : label(user.kind), user.how].filter(Boolean).join(" · ")}
      {onOpenUser && (user.objectId || user.libraryVersionId) && <> · <button type="button" className="lb-link lb-link-inline" onClick={() => onOpenUser(user)}>Open</button></>}</span>
    {line?.(user)}</div>)}</div>;
}

export function StopBody({name, preview, error, onOpenUser}: {name: string; preview: StopPreview; error?: string | null; onOpenUser?(user: UserRow): void}) {
  const next = preview.producing[0];
  return <div className="lb-sheet-body">
    <h4>{preview.users.length ? `Using v${preview.version} (${preview.users.length})` : "Nobody uses it"}</h4>
    <UserLines users={preview.users} onOpenUser={onOpenUser} line={user => <Line user={user} preview={preview} />} />
    <h4>Stopping</h4>
    <div className="lb-kv2"><span>New values</span><span>{preview.lastDatetime === null ? "none" : `none after ${mdy(preview.lastDatetime)}`}</span><span>Kept</span><span>values to that day, code, settings, published figures</span>
      <span>Undo</span><span>produce it again; the periods missed are made then</span>{next !== undefined && <><span>Still producing</span><span>{`v${next}`}</span></>}</div>
    {error && <div className="lb-block lb-sheet-error" role="alert">{error}</div>}
    <span hidden>{name}</span>
  </div>;
}

export function StopFooter({version, busy, onCancel, onStop}: {version: number; busy?: boolean; onCancel(): void; onStop(): void}) {
  return <div className="lb-sheet-foot"><span className="lb-sp" />
    <TxButton onClick={onCancel}>Cancel</TxButton><TxButton variant="primary" disabled={busy} onClick={onStop}>{`Stop v${version}`}</TxButton></div>;
}

/** The sheet itself: the preview is read first (nothing changes), then the real stop; an error from the stop shows its reason and keeps the sheet. */
export function StopSheet({api, open, name, versionId, version, onClose, onDone, onOpenUser}: {api: LibraryApi; open: boolean; name: string; versionId: string; version: number; onClose(): void; onDone(preview: StopPreview): void; onOpenUser?(user: UserRow): void}) {
  const [preview, setPreview] = useState<StopPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!open) return;
    setPreview(null); setError(null);
    api.stop(versionId, true).then(setPreview, reason => setError(reason instanceof Error ? reason.message : String(reason)));
  }, [open, versionId]);
  const stop = () => {
    setBusy(true);
    api.stop(versionId, false).then(done => {setBusy(false); onDone(done);}, reason => {
      setBusy(false);
      const message = reason instanceof ContractError || reason instanceof Error ? reason.message : String(reason);
      setError(message);
      void api.stop(versionId, true).then(setPreview, again => setError(`${message} Reading who uses it again failed too: ${again instanceof Error ? again.message : String(again)}`));
    });
  };
  return <TxSheet open={open} onOpenChange={value => {if (!value) onClose();}} title={`Stop ${name} v${version}`} label="Stop"
    footer={preview ? <StopFooter version={version} busy={busy} onCancel={onClose} onStop={stop} /> : undefined}>
    {preview ? <StopBody name={name} preview={preview} error={error} onOpenUser={onOpenUser} /> : error ? <div className="lb-calm">{error}</div> : <TxSkeleton width="60%" />}
  </TxSheet>;
}
