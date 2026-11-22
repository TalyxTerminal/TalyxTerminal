/** The notebook's tool row (the draft's): run, stop (only while something runs) and restart (only while the kernel can); add a cell; and, with cells ticked, how many and Extract to new object. */
import { t } from "../i18n";
import type { ReactNode } from "react";
import { TxButton, TxIcon } from "@talyx/sdk";
import "./notebook.css";

export interface ToolbarProps {
  status: ReactNode; busy: boolean; canRestart: boolean; selected: number;
  onRunAll(): void; onInterrupt(): void; onRestart(): void; onAdd(type: "code" | "markdown"): void; onExtract(): void; onClear(): void;
}

export function Toolbar({ status, busy, canRestart, selected, onRunAll, onInterrupt, onRestart, onAdd, onExtract, onClear }: ToolbarProps) {
  return <div className="qv-notebook-bar">
    <TxButton size="sm" onClick={onRunAll}><TxIcon name="play" />{t("Run all")}</TxButton>
    {busy && <TxButton variant="quiet" size="sm" onClick={onInterrupt}>{t("Interrupt")}</TxButton>}
    {canRestart && <TxButton variant="quiet" size="sm" onClick={onRestart}>{t("Restart")}</TxButton>}
    <span className="qv-notebook-sep" />
    <TxButton variant="quiet" size="sm" onClick={() => onAdd("code")}><TxIcon name="plus" />{t("Code")}</TxButton>
    <TxButton variant="quiet" size="sm" onClick={() => onAdd("markdown")}><TxIcon name="plus" />{t("Text")}</TxButton>
    <span className="qv-notebook-sep" />
    {selected > 0 ? <><span className="qv-notebook-selinfo">{(selected === 1 ? t("{selected} cell selected", { selected }) : t("{selected} cells selected", { selected }))}</span><TxButton variant="primary" size="sm" onClick={onExtract}>{t("Extract to new object")}</TxButton><TxButton variant="quiet" size="sm" onClick={onClear}>{t("Clear")}</TxButton></>
      : <TxButton variant="quiet" size="sm" onClick={onExtract}>{t("Extract to new object")}</TxButton>}
    <span className="tx-spacer" style={{ flex: 1 }} />{status}
  </div>;
}
