/** Extract to a new object (the draft's sheet): the ticked cells' code as written and the data settings they read; tidying the code is the user's, in Developer. */
import { t } from "../i18n";
import { useEffect, useRef, useState } from "react";
import { TxButton, TxInput, TxSegmented, TxSheet } from "@talyx/sdk";
import { mdy } from "../shared/format";
import type { ExtractPreview } from "../notebook";
import { madeWhen } from "./RunBand";
import "./notebook.css";

const day = (value: string | null, fallback: string) => value === null ? fallback : (/^\d{4}-\d{2}-\d{2}/.test(value) ? mdy(new Date(`${value.slice(0, 10)}T00:00:00Z`)) : value);
const KINDS = [t("Factor"), t("Model"), t("Allocation"), t("Strategy")];

export function ExtractSheet({ open, count, total, kind, onKind, preview, failure, onClose, onCreate }: {
  open: boolean; count: number; total: number; kind: string; onKind(kind: string): void; preview: ExtractPreview | null; failure: string | null; onClose(): void; onCreate(name: string): void;
}) {
  const [name, setName] = useState("");
  const [asked, setAsked] = useState(false);
  const field = useRef<HTMLInputElement>(null);
  useEffect(() => { if (open) { setName(current => current || t("From notebook")); setAsked(false); } }, [open]);
  // Create is there once the cells are read; with no name it marks the box and puts the cursor in it.
  const create = () => { if (preview === null) return; if (name.trim()) onCreate(name.trim()); else { setAsked(true); field.current?.focus(); } };
  const data = preview?.dataSettings;
  return <TxSheet open={open} onOpenChange={value => { if (!value) onClose(); }} title={(count === 1 ? t("Extract to new object · {count} cell", { count }) : t("Extract to new object · {count} cells", { count }))}
    footer={<div className="qv-notebook-footer"><span className="qv-notebook-dim">{t("Enter creates")}</span><span className="qv-notebook-footgap" /><TxButton size="sm" onClick={onClose}>{t("Cancel")}</TxButton>
      {preview !== null && <TxButton variant="primary" size="sm" onClick={create}>{t("Create {kind}", { kind: kind.toLowerCase() })}</TxButton>}</div>}>
    <dl className="qv-notebook-kv qv-notebook-sheetkv"><dt>{t("Kind")}</dt><dd><TxSegmented value={kind} onChange={onKind} options={KINDS.map(item => ({ value: item, label: item }))} label={t("Kind")} /></dd>
      <dt>{t("Name")}</dt><dd><TxInput ref={field} autoFocus onFocus={event => event.currentTarget.select()} value={name} aria-invalid={asked && !name.trim() || undefined} onChange={event => setName(event.target.value)} onKeyDown={event => { if (event.key === "Enter") create(); }} /></dd></dl>
    {failure !== null && <p role="alert">{failure}</p>}
    {preview !== null && <>
      <h4 className="qv-notebook-sheeth">{t("Code, as written")}</h4>
      <pre className="qv-notebook-pre">{preview.code}</pre>
      <h4 className="qv-notebook-sheeth">{t("Data settings, from these cells")}</h4>
      {data !== undefined && data.found ? <dl className="qv-notebook-kv">
        {(data.universe !== null || data.symbols !== null) && <><dt>{t("Universe")}</dt><dd>{data.universe ?? (Array.isArray(data.symbols) ? data.symbols.join(", ") : String(data.symbols))}</dd></>}
        {data.timeframe && <><dt>{t("Bar period")}</dt><dd>{data.timeframe}</dd></>}
        {(data.since !== null || data.until !== null) && <><dt>{t("Period")}</dt><dd>{day(data.since, "start")} – {day(data.until, "latest")}</dd></>}
        {data.libraryInputs.length > 0 && <><dt>{t("Library inputs")}</dt><dd>{data.libraryInputs.map((item, index) => <span key={item.name}>{index > 0 && ", "}{item.name}{item.version !== null && <> <b>v{item.version}</b></>}</span>)}{t(", pinned")}</dd></>}
        {data.runs.length > 0 && <><dt>{t("Runs")}</dt><dd>{data.runs.map((item, index) => <span key={index}>{index > 0 && "; "}{item.name ?? item.object}{item.number !== undefined && <> <b>#{item.number}</b></>}{item.at !== undefined && `, ${madeWhen(new Date(item.at))}`}</span>)}</dd></>}
        {preview.heldOutFrom !== null && <><dt>{t("Held out")}</dt><dd>{data.heldOutStudied ? t.rich("from {date} these cells studied the part after it", { date: <b>{day(preview.heldOutFrom, "")}</b> }) : t.rich("from {date}", { date: <b>{day(preview.heldOutFrom, "")}</b> })}</dd></>}
        {data.conflicts.map(item => <><dt>{t("Also read")}</dt><dd>{item}</dd></>)}
      </dl> : <p className="qv-notebook-dim">{t("None found in these cells")}</p>}
      <h4 className="qv-notebook-sheeth">{t("Not taken")}</h4>
      <p className="qv-notebook-dim">{t("Outputs, the other {total} cells, the kernel's variables. Tidying the code is yours, in Developer.", { total: total - count })}</p>
      {preview.notTaken.map(line => <p key={line} className="qv-notebook-dim">{line}</p>)}
    </>}
  </TxSheet>;
}
