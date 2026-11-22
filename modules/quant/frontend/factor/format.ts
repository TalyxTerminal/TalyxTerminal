import { intlLocale } from "@talyx/sdk";
import { t } from "../i18n";
/* How a factor's Work writes numbers: signed quantities carry their sign, unsigned shares none, a change in a share is in points. */
const two = (value: number) => String(value).padStart(2, "0");

/** A plain number; a negative one takes the tables' minus sign (−), not a hyphen. */
const formats = new Map<number, Intl.NumberFormat>();  // one formatter per number of digits: a table of thousands of cells builds none per cell
export const nf = (value: number | null | undefined, digits = 2) => {
  if (value == null || Number.isNaN(value)) return "";
  let format = formats.get(digits);
  if (!format) {format = new Intl.NumberFormat("en-US", {minimumFractionDigits: digits, maximumFractionDigits: digits}); formats.set(digits, format);}
  return format.format(value).replace("-", "−");
};
/** Signed quantities only (returns, IC, deltas): + or − before the number. */
export const sgn = (value: number | null | undefined, digits = 2) => value == null || Number.isNaN(value) ? "" : (value > 0 ? "+" : value < 0 ? "−" : "") + nf(Math.abs(value), digits);
export const pc = (value: number | null | undefined, digits = 1) => value == null ? "" : sgn(value * 100, digits) + "%";
/** Unsigned shares: turnover, coverage. */
export const pct = (value: number | null | undefined, digits = 1) => value == null ? "" : nf(value * 100, digits) + "%";
/** A change in a share, in percentage points. */
export const pp = (value: number, digits = 2) => t("{value} pt", { value: sgn(value * 100, digits) });
export const tone = (value: number | null | undefined) => value == null ? undefined : value > 0 ? "tx-up" : value < 0 ? "tx-down" : undefined;

/** A UTC day: "Oct 30, 2026". */
export const mdy = (ms: number) => { const day = new Date(ms); return day.toLocaleDateString(intlLocale(), { timeZone: "UTC", month: "short", day: "numeric", year: "numeric" }); };
/** "Jan 2016". */
export const my = (ms: number) => { const day = new Date(ms); return day.toLocaleDateString(intlLocale(), { timeZone: "UTC", month: "short", year: "numeric" }); };
/** A moment (when a run started, stopped, was published), in UTC as every time in Quant (design.md, ): "Oct 30, 13:24 UTC". */
export const clock = (ms: number) => { const day = new Date(ms); return `${two(day.getUTCHours())}:${two(day.getUTCMinutes())} UTC`; };
export const when = (ms: number) => { const day = new Date(ms); return `${day.toLocaleDateString(intlLocale(), { timeZone: "UTC", month: "short", day: "numeric" })}, ${clock(ms)}`; };
/** "1:48" for an elapsed time. */
/** m:ss; a time read against a clock that has not ticked since the run began is never shown below zero. */
export const elapsed = (seconds: number) => {const at = Math.max(0, seconds); return `${Math.floor(at / 60)}:${two(Math.floor(at % 60))}`;};
/** A data setting's date ("2016-01-04") as "Jan 4, 2016". */
export const dayText = (iso: string | undefined | null) => iso ? mdy(Date.parse(iso + "T00:00:00Z")) : "";
export const monthText = (iso: string | undefined | null) => iso ? my(Date.parse(iso + "T00:00:00Z")) : "";
export const secondsText = (value: number | null | undefined) => value == null ? "" : value < 10 ? `${value.toFixed(1)} s` : value < 100 ? `${Math.round(value)} s` : elapsed(value);
