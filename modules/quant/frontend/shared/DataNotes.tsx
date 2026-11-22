/* A run's data notes: the facts about its data that change this result (who is missing and why, which days have no price, where values end),
   folded behind one quiet button in the run's head, the same on every Quant page. */
import { t } from "../i18n";
import { useState } from "react";
import { TxIcon, TxPopover } from "@talyx/sdk";
import "./datanotes.css";

export function DataNotes({notes}: {notes: string[]}) {
  const [open, setOpen] = useState(false);
  if (!notes.length) return null;
  return <TxPopover open={open} onOpenChange={setOpen} label={t("Data notes")} size="standard"
    trigger={<button type="button" className="tx-btn qx-datanotes" data-variant="quiet" data-size="sm">{notes.length === 1 ? t("1 data note") : t("{notesCount} data notes", { notesCount: notes.length })}<TxIcon name="chevron" /></button>}>
    <ul className="qx-notelist">{notes.map((note, index) => <li key={index}>{note}</li>)}</ul>
  </TxPopover>;
}
