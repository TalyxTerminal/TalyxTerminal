/* A day typed and shown as Quant writes days ("Jan 4, 2016", UTC), not the browser's date box (which writes the system's "yyyy/mm/dd"
 * and cuts it in a narrow field; ). It also takes "2016-01-04". The value in and out is the ISO day. */
import { useEffect, useState } from "react";
import { TxInput } from "@talyx/sdk";
import { MON } from "./format";

export const dayShown = (iso: string) => {const [year, month, day] = iso.split("-").map(Number); return `${MON[month - 1]} ${day}, ${year}`;};

/** The ISO day a text names, or null when it names none. */
export function dayRead(text: string): string | null {
  const iso = /^\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*$/.exec(text);
  const written = /^\s*([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})\s*$/.exec(text);
  const [year, month, day] = iso ? [Number(iso[1]), Number(iso[2]), Number(iso[3])]
    : written ? [Number(written[3]), MON.findIndex(name => name.toLowerCase() === written[1].toLowerCase()) + 1, Number(written[2])] : [0, 0, 0];
  const at = new Date(Date.UTC(year, month - 1, day));
  if (!month || at.getUTCFullYear() !== year || at.getUTCMonth() !== month - 1 || at.getUTCDate() !== day) return null;
  return at.toISOString().slice(0, 10);
}

/** `empty` lets the field be cleared (onChange(null)); a day outside min..max, or text that names no day, is marked and not taken. */
export function DayInput({label, value, min, max, empty = false, onChange}: {label: string; value: string | null; min?: string; max?: string; empty?: boolean; onChange(day: string | null): void}) {
  const [text, setText] = useState(value ? dayShown(value) : "");
  useEffect(() => setText(value ? dayShown(value) : ""), [value]);
  const day = dayRead(text);
  const bad = text.trim() === "" ? !empty : day === null || (min !== undefined && day < min) || (max !== undefined && day > max);
  return <TxInput aria-label={label} value={text} aria-invalid={bad || undefined} title={min || max ? [min && `from ${dayShown(min)}`, max && `to ${dayShown(max)}`].filter(Boolean).join(" ") : undefined}
    onChange={event => setText(event.target.value)}
    onKeyDown={event => {if (event.key === "Enter") (event.target as HTMLInputElement).blur();}}
    onBlur={() => {
      if (bad) return;
      const next = text.trim() === "" ? null : day;
      if (next !== value) onChange(next);
      else setText(value ? dayShown(value) : "");
    }} />;
}
