import { t } from "../i18n";
import { useEffect, useState } from "react";
import { TxButton, TxDialog, TxField, TxInput, TxSegmented, TxStatus } from "@talyx/sdk";
import { errorText, formatBytes, formatCount, listOf, marketCall, panelCall, selectionsOf, today, type ExportInput, type Series } from "./data";
import { usePanelContext } from "./MarketPanel";
import type { ContractTypes } from "../../public/ts/types";

type Format = "parquet" | "csv";
type Adjust = "raw" | "split" | "split_dividend";


/** Write a dataset, or the chosen series, to files in a folder: Parquet or CSV, with the adjustment applied on the way out.
    The folder starts inside the workspace's folder; any full path on disk works too. */
export function ExportDialog({ title, series, adjustable, onClose }: { title: string; series: Series[]; adjustable: boolean; onClose: () => void }) {
  const ctx = usePanelContext();
  const [folder, setFolder] = useState("");
  const [format, setFormat] = useState<Format>("parquet");
  const [adjust, setAdjust] = useState<Adjust>("split");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [done, setDone] = useState<ContractTypes["export"]["output"] | null>(null);
  useEffect(() => {
    const name = `exports/${title.replace(/[^A-Za-z0-9]+/g, "-").replace(/^-|-$/g, "").toLowerCase()}-${today()}`;
    void panelCall<{ folder: string }>(ctx, "folder", { folder: name }).then(value => setFolder(current => current || value.folder)).catch(() => undefined);
  }, [ctx, title]);
  const rows = series.reduce((sum, item) => sum + item.rows, 0);
  // Stored bytes are compressed columns; Parquet comes out about the same, CSV about three times larger.
  const bytes = series.reduce((sum, item) => sum + item.bytes, 0) * (format === "csv" ? 3.1 : 1.05);
  const exportAll = () => {
    setBusy(true); setProblem(null);
    void selectionsOf(ctx, series)
      .then(selections => marketCall(ctx, "export", { series: listOf<NonNullable<ExportInput["series"]>>(selections.map(({ instrument, series }) => ({ instrumentId: instrument.instrumentId, series })), 1, 10000), format, fill: "none",
        allowEmpty: true, destination: { folder }, ...(adjustable ? { adjustment: adjust } : {}) }))
      .then(setDone).catch(error => setProblem(errorText(error))).finally(() => setBusy(false));
  };
  return <TxDialog open onOpenChange={value => { if (!value) onClose(); }} title={t("Export {title}", { title })} size="standard" footer={<>
    <span className="tx-secondary tx-num">{done ? t("{filesCount} files · {files} rows written", { filesCount: done.files.length, files: formatCount(done.files.reduce((sum, file) => sum + file.rows, 0)) })
      : t("{seriesCount} series · {rows} rows · ≈ {bytes}", { seriesCount: series.length.toLocaleString("en-US"), rows: formatCount(rows), bytes: formatBytes(bytes) })}</span>
    <span className="tx-spacer" />
    <TxButton size="sm" variant="quiet" onClick={onClose}>{done ? t.c("action", "Close") : t("Cancel")}</TxButton>
    {!done && <TxButton size="sm" variant="primary" loading={busy} disabled={!folder || busy} onClick={exportAll}>{t("Export {seriesCount} series", { seriesCount: series.length.toLocaleString("en-US") })}</TxButton>}
  </>}>
    <div className="market-export">
      <div className="market-import-folder">
        <TxField label={t("Folder")}><TxInput value={folder} onChange={event => setFolder(event.target.value)} placeholder={t("A folder in the workspace, or a full path")} /></TxField>
      </div>
      <TxField label={t("Format")}><TxSegmented value={format} onChange={setFormat} label={t("Format")} options={[{ value: "parquet", label: t("Parquet") }, { value: "csv", label: "CSV" }]} /></TxField>
      {adjustable && <TxField label={t("Adjustment")}><TxSegmented value={adjust} onChange={setAdjust} label={t("Adjustment")} options={[
        { value: "raw", label: t("Raw") }, { value: "split", label: t("Splits") }, { value: "split_dividend", label: t("Splits + dividends") },
      ]} /></TxField>}
      {problem && <TxStatus tone="failed" label={problem} />}
      {done?.files.map(file => <div key={file.path ?? ""} className="tx-num tx-secondary market-clip" title={file.path ?? ""}>{file.path}</div>)}
    </div>
  </TxDialog>;
}
