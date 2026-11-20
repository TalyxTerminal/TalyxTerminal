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
  return <TxDialog open onOpenChange={value => { if (!value) onClose(); }} title="Import data" size="wide" className="market-import" footer={<>
    {done && <span className="tx-secondary tx-num">{done.rows.toLocaleString("en-US")} rows into {done.instruments} instruments{done.rejectedCount ? ` · ${done.rejectedCount} rows refused` : ""}</span>}
    <span className="tx-spacer" />
    <TxButton size="sm" variant="quiet" onClick={onClose}>{done ? "Close" : "Cancel"}</TxButton>
    {!done && <TxButton size="sm" variant="primary" loading={busy && files !== null} disabled={!readable.length || busy} onClick={importAll}>{readable.length ? `Import ${readable.length} files` : "Import"}</TxButton>}
  </>}>
    <div className="market-import-folder">
      <TxField label="Folder"><TxInput value={folder} onChange={event => { setFolder(event.target.value); setFiles(null); }} placeholder="A folder in the workspace, or a full path" /></TxField>
      <TxButton loading={busy && files === null} onClick={read}>Read</TxButton>
    </div>
    {problem && <TxStatus tone="failed" label={problem} />}
    {done?.rejectedRows.length ? <div className="market-import-files">{done.rejectedRows.map(row => <div key={`${row.reason}:${row.line}`} className="tx-secondary">
      Line {row.line}: {row.reason}</div>)}</div> : null}
    {files && !done && <>
      <div className="market-import-summary">
        <span className="tx-num">{files.length} files · {kinds} kinds of data</span>
        <span className="tx-spacer" />
        <TxSegmented size="sm" value={show} onChange={setShow} label="Show files" options={[
          { value: "all", label: "All" },
          { value: "matched", label: `Matched ${readable.length - unmatched}` },
          { value: "unmatched", label: `Not matched ${unmatched}` },
        ]} />
      </div>
      <div className="market-import-files">
        <TxTable stickyHead density="compact">
          <thead><tr><TxTh>File</TxTh><TxTh>Data</TxTh><TxTh numeric>Rows</TxTh><TxTh>Instrument</TxTh></tr></thead>
          <tbody>{shown.map(file => <tr key={`${file.file}:${file.code ?? ""}`}>
            <TxTd className="tx-num">{file.file}</TxTd><TxTd>{file.problem ? "" : kindLabel("ohlcv", file.timeframe ?? null)}</TxTd>
            <TxTd numeric>{file.rows?.toLocaleString("en-US") ?? ""}{file.rejected ? <span className="tx-muted"> · {file.rejected} refused</span> : null}</TxTd>
            <TxTd>{file.problem ? <TxStateWord tone="watch">{file.problem}</TxStateWord> : file.instrumentId ? `${file.market} · ${file.code}`
              : <TxTooltip label={`No Market instrument. Imported as ${file.code}`}><span><TxStateWord tone="watch">Not matched</TxStateWord></span></TxTooltip>}</TxTd>
          </tr>)}</tbody>
        </TxTable>
      </div>
    </>}
  </TxDialog>;
}
