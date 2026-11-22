/* A newer TALYX: a quiet mark in the top bar while one is published, its notes and getting it; the version and
   "Check now" in Settings, General. The Host asks GitHub and downloads (host/updates.py). In the desktop app, when the
   release has an installer with a published checksum, Update downloads it with progress, checks it, then quits, installs
   (the installer's own progress) and opens TALYX again (host/desktop/install.py); otherwise the release page has the
   installer. After a new version is installed, its notes are shown once. */
import { useCallback, useEffect, useRef, useState } from "react";
import MarkdownIt from "markdown-it";
import { TxButton, TxDialog, TxIcon, TxProgress, TxRow, TxSettingsGroup, TxTooltip, type Platform } from "@talyx/sdk";
import { platformRuntime } from "../kernel/runtime";
import { t } from "./i18n";
import { useDesktop } from "./shellChrome";

type UpdateState = Platform.UpdateState;

/** The update state, kept current as checks and downloads go. */
export function useUpdateState(): UpdateState | null {
  const [state, setState] = useState<UpdateState | null>(null);
  useEffect(() => {
    let closed = false, close: (() => Promise<void>) | null = null;
    void platformRuntime().bridge.subscribe("platform.updates.changes", {}, item => setState(item as unknown as UpdateState))
      .then(subscription => { if (closed) void subscription.close(); else close = subscription.close; }, () => undefined);
    return () => { closed = true; void close?.(); };
  }, []);
  return state;
}

const reason = (failure: unknown) => failure instanceof Error ? failure.message : String(failure);
/** Release notes are Markdown; raw HTML in them shows as text. */
const markdown = new MarkdownIt({ html: false });

function Notes({ text }: { text: string }) {
  return <div className="talyx-update-notes" dangerouslySetInnerHTML={{ __html: markdown.render(text) }} />;
}

const percent = (download: NonNullable<UpdateState["download"]>) => download.total ? download.received / download.total * 100 : null;

function ReleaseDialog({ state, open, onOpenChange }: { state: UpdateState; open: boolean; onOpenChange: (open: boolean) => void }) {
  const desktop = useDesktop();
  const [error, setError] = useState<string | null>(null);
  const latest = state.latest!;
  const download = state.download?.version === latest.version ? state.download : null;
  // In the desktop app with a checked installer: one press downloads, checks and installs (going on while this dialog is open).
  const inApp = Boolean(desktop && latest.installer);
  const asked = useRef(false);
  const install = useCallback(async () => {
    try { await desktop!.installUpdate(); onOpenChange(false); }
    catch (failure) { setError(reason(failure)); }
  }, [desktop, onOpenChange]);
  useEffect(() => {
    if (open && asked.current && download?.state === "ready") { asked.current = false; void install(); }
  }, [open, download?.state, install]);
  const page = async () => {
    try { await platformRuntime().platform["updates.open"]({}); onOpenChange(false); }
    catch (failure) { setError(reason(failure)); }
  };
  const update = async () => {
    setError(null);
    if (download?.state === "ready") { await install(); return; }
    asked.current = true;
    try { await platformRuntime().platform["updates.download"]({}); }
    catch (failure) { asked.current = false; setError(reason(failure)); }
  };
  const busy = download?.state === "downloading";
  return <TxDialog open={open} onOpenChange={onOpenChange} title={t("TALYX {version} is out", { version: latest.version })}
    footer={<>
      <TxButton size="sm" variant="quiet" onClick={() => onOpenChange(false)}>{t("Later")}</TxButton>
      {inApp && download?.state === "failed" && <TxButton size="sm" onClick={() => void page()}>{t("Release page")}</TxButton>}
      {inApp
        ? <TxButton size="sm" variant="primary" disabled={busy} aria-busy={busy || undefined} onClick={() => void update()}>
          {download?.state === "ready" ? t("Restart and install") : download?.state === "failed" ? t("Try again") : t("Update")}</TxButton>
        : <TxButton size="sm" variant="primary" onClick={() => void page()}>{t("Download")}</TxButton>}
    </>}>
    <p className="talyx-update-lead">{inApp
      ? t("You have {current}. Updating keeps your workspaces, data and settings; TALYX closes to install and opens again.", { current: state.current })
      : t("You have {current}. Run the new installer: it upgrades TALYX in place and keeps your workspaces, data and settings.", { current: state.current })}</p>
    {latest.notes && <Notes text={latest.notes} />}
    {inApp && busy && <TxProgress className="talyx-update-progress" value={percent(download!)} label={t("Downloading {version}", { version: latest.version })} />}
    {download?.state === "failed" && download.problem && <p className="tx-error" role="alert">{download.problem}</p>}
    {!download && state.problem && <p className="tx-error" role="alert">{state.problem}</p>}
    {error && <p className="tx-error" role="alert">{error}</p>}
  </TxDialog>;
}

/** The first start of a new version: its notes, once. */
function WhatsNew({ notes }: { notes: NonNullable<UpdateState["notes"]> }) {
  const [open, setOpen] = useState(true);
  const seen = () => { setOpen(false); void platformRuntime().platform["updates.notes_seen"]({}).catch(() => undefined); };
  return <TxDialog open={open} onOpenChange={next => { if (!next) seen(); }} title={t("What's new in TALYX {version}", { version: notes.version })}
    footer={<TxButton size="sm" variant="primary" onClick={seen}>{t("OK")}</TxButton>}>
    <Notes text={notes.notes} />
  </TxDialog>;
}

/** In the top bar, beside Settings: while a newer version is published, and this version's notes once. */
export function UpdateNotice() {
  const state = useUpdateState();
  const [open, setOpen] = useState(false);
  if (!state) return null;
  const download = state.latest && state.download?.version === state.latest.version ? state.download : null;
  return <>
    {state.notes && <WhatsNew notes={state.notes} />}
    {state.latest && <>
      <TxTooltip label={t("A new version of TALYX is out")}>
        <button type="button" className="talyx-update-mark" onClick={() => setOpen(true)}>
          <TxIcon name="arrow-up-right" />
          {download?.state === "downloading" ? t("Downloading {percent}%", { percent: Math.round(percent(download) ?? 0) })
            : download?.state === "ready" ? t("Restart to update") : t("Update {version}", { version: state.latest.version })}
        </button>
      </TxTooltip>
      <ReleaseDialog state={state} open={open} onOpenChange={setOpen} />
    </>}
  </>;
}

/** Settings, General: this version, what the last check found, and checking now. */
export function VersionGroup() {
  const state = useUpdateState();
  const [checking, setChecking] = useState(false);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const check = useCallback(async () => {
    setChecking(true); setError(null);
    try { await platformRuntime().platform["updates.check"]({}); }
    catch (failure) { setError(reason(failure)); }
    finally { setChecking(false); }
  }, []);
  if (!state) return null;
  const detail = error ?? state.problem
    ?? (state.latest ? t("{version} is out", { version: state.latest.version })
      : state.checkedAt ? t("Up to date") : t("Not checked yet"));
  return <TxSettingsGroup title={t("Version")}>
    <TxRow title={t("TALYX {version}", { version: state.current })} tone={error ?? state.problem ? "failed" : "default"} detail={detail}>
      <div className="tx-row tx-nowrap">
        {state.latest && <TxButton size="sm" variant="primary" onClick={() => setOpen(true)}>{t("See what's new")}</TxButton>}
        <TxButton size="sm" disabled={checking} aria-busy={checking || undefined} onClick={() => void check()}>{checking ? t("Checking…") : t("Check now")}</TxButton>
      </div>
    </TxRow>
    {state.latest && <ReleaseDialog state={state} open={open} onOpenChange={setOpen} />}
  </TxSettingsGroup>;
}
