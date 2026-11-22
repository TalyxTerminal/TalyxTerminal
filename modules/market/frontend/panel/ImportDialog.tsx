import { t } from "../i18n";
import { useState } from "react";
import { TxButton, TxDialog, TxField, TxInput, TxSegmented, TxStateWord, TxStatus, TxTable, TxTd, TxTh, TxTooltip } from "@talyx/sdk";
import { errorText, kindLabel, panelCall } from "./data";
import { usePanelContext } from "./MarketPanel";

type Show = "all" | "matched" | "unmatched";

interface ScannedFile { file: string; kind?: string; timeframe?: string | null; market?: string; code?: string; rows?: number; rejected?: number; instrumentId?: string | null; problem?: string }
interface ImportResult { rows: number; instruments: number; rejectedCount: number; rejectedRows: { line: number; reason: string }[] }

/** Import a folder: a relative folder is inside the workspace's folder, an absolute one anywhere on disk. Files are read
    first and shown with what each becomes; nothing is written until Import. */
export function ImportDialog({ onClose, onImported }: { onClose: () => void; onImported: () => void }) {
  const ctx = usePanelContext();
  const [folder, setFolder] = useState("");
  const [files, setFiles] = useState<ScannedFile[] | null>(null);
  const [show, setShow] = useState<Show>("all");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [done, setDone] = useState<ImportResult | null>(null);
  const read = () => {
    setBusy(true); setProblem(null); setDone(null);
    void panelCall<{ files: ScannedFile[]; folder: string }>(ctx, "scan", { folder: folder.trim() || null })
      .then(value => { setFiles(value.files); setFolder(value.folder); })
      .catch(error => { setFiles(null); setProblem(errorText(error)); }).finally(() => setBusy(false));
  };
  const importAll = () => {
    setBusy(true); setProblem(null);
    void panelCall<ImportResult>(ctx, "import", { folder })
      .then(value => { setDone(value); onImported(); })
      .catch(error => setProblem(errorText(error))).finally(() => setBusy(false));
  };
  const readable = (files ?? []).filter(file => !file.problem);
  const unmatched = readable.filter(file => !file.instrumentId).length;
  const kinds = new Set(readable.map(file => file.timeframe)).size;
  const shown = (files ?? []).filter(file => show === "all" || (show === "matched") === Boolean(file.instrumentId))
    .sort((a, b) => Number(Boolean(a.instrumentId)) - Number(Boolean(b.instrumentId)));
  return <TxDialog open onOpenChange={value => { if (!value) onClose(); }} title={t("Import data")} size="wide" className="market-import" footer={<>
    {done && <span className="tx-secondary tx-num">{(done.rejectedCount ? t("{rows} rows into {instruments} instruments · {rejectedCount} rows refused", { rows: done.rows.toLocaleString("en-US"), instruments: done.instruments, rejectedCount: done.rejectedCount }) : t("{rows} rows into {instruments} instruments", { rows: done.rows.toLocaleString("en-US"), instruments: done.instruments }))}</span>}
    <span className="tx-spacer" />
    <TxButton size="sm" variant="quiet" onClick={onClose}>{done ? t.c("action", "Close") : t("Cancel")}</TxButton>
    {!done && <TxButton size="sm" variant="primary" loading={busy && files !== null} disabled={!readable.length || busy} onClick={importAll}>{readable.length ? t("Import {readableCount} files", { readableCount: readable.length }) : t("Import")}</TxButton>}
  </>}>
    <div className="market-import-folder">
      <TxField label={t("Folder")}><TxInput value={folder} onChange={event => { setFolder(event.target.value); setFiles(null); }} placeholder={t("A folder in the workspace, or a full path")} /></TxField>
      <TxButton loading={busy && files === null} onClick={read}>{t("Read")}</TxButton>
    </div>
    {problem && <TxStatus tone="failed" label={problem} />}
    {done?.rejectedRows.length ? <div className="market-import-files">{done.rejectedRows.map(row => <div key={`${row.reason}:${row.line}`} className="tx-secondary">{t("Line {line}: {reason}", { line: row.line, reason: row.reason })}</div>)}</div> : null}
    {files && !done && <>
      <div className="market-import-summary">
        <span className="tx-num">{t("{filesCount} files · {kinds} kinds of data", { filesCount: files.length, kinds })}</span>
        <span className="tx-spacer" />
        <TxSegmented size="sm" value={show} onChange={setShow} label={t("Show files")} options={[
          { value: "all", label: t("All") },
          { value: "matched", label: t("Matched {readableCount}", { readableCount: readable.length - unmatched }) },
          { value: "unmatched", label: t("Not matched {unmatched}", { unmatched }) },
        ]} />
      </div>
      <div className="market-import-files">
        <TxTable stickyHead density="compact">
          <thead><tr><TxTh>{t("File")}</TxTh><TxTh>{t("Data")}</TxTh><TxTh numeric>{t("Rows")}</TxTh><TxTh>{t("Instrument")}</TxTh></tr></thead>
          <tbody>{shown.map(file => <tr key={`${file.file}:${file.code ?? ""}`}>
            <TxTd className="tx-num">{file.file}</TxTd><TxTd>{file.problem ? "" : kindLabel("ohlcv", file.timeframe ?? null)}</TxTd>
            <TxTd numeric>{file.rows?.toLocaleString("en-US") ?? ""}{file.rejected ? <span className="tx-muted">{t(" · {rejected} refused", { rejected: file.rejected })}</span> : null}</TxTd>
            <TxTd>{file.problem ? <TxStateWord tone="watch">{file.problem}</TxStateWord> : file.instrumentId ? `${file.market} · ${file.code}`
              : <TxTooltip label={t("No Market instrument. Imported as {code}", { code: String(file.code) })}><span><TxStateWord tone="watch">{t("Not matched")}</TxStateWord></span></TxTooltip>}</TxTd>
          </tr>)}</tbody>
        </TxTable>
      </div>
    </>}
  </TxDialog>;
}
