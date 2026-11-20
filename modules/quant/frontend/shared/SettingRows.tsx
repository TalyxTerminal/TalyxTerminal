/* The rows a settings column is made of, shared by the pages: a labelled row with its changed mark, a checkbox row, a number box that may also take a list. */
import type { ReactNode } from "react";
import { TxCheckbox, TxInput, TxParamRow } from "@talyx/sdk";

export function Row({id, label, hint, changed, onRevert, children}: {id: string; label: string; hint?: string; changed: boolean; onRevert?: () => void; children: ReactNode}) {
  return <div id={`fx-row-${id}`} className="fx-rowwrap"><TxParamRow label={label} hint={hint} changed={changed} onRevert={onRevert}>{children}</TxParamRow></div>;
}

export function Check({label, checked, changed, onRevert, onChange}: {label: string; checked: boolean; changed: boolean; onRevert?: () => void; onChange(value: boolean): void}) {
  if (onRevert) return <TxParamRow wide changed={changed} onRevert={onRevert}><TxCheckbox label={label} checked={checked} onCheckedChange={onChange} /></TxParamRow>;
  return <div className="fx-chk" data-changed={changed ? "" : undefined}><TxCheckbox label={label} checked={checked} onCheckedChange={onChange} /></div>;
}

/** A number box: it keeps what is typed until it is a number, so "1." or "" can be typed on the way; `unit` is its suffix. */
export function NumberBox({label, value, unit, onValue, allowEmpty}: {label: string; value: number | null | undefined; unit?: string; onValue(value: number | null): void; allowEmpty?: boolean}) {
  return <TxInput numeric aria-label={label} value={value === null || value === undefined ? "" : String(value)} suffix={unit}
    onChange={event => {
      const text = event.target.value.trim();
      if (text === "") {if (allowEmpty) onValue(null); return;}
      const number = Number(text);
      if (Number.isFinite(number)) onValue(number);
    }} />;
}
