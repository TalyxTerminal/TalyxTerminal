import { t } from "../i18n";
import { intlLocale } from "@talyx/sdk";
/* How the library writes numbers and times: signed quantities carry + or −, shares none, a change in a share is in points (factor/format.ts), and days are UTC dates. */
export {mdy, nf, pc, pct, pp, sgn, tone} from "../factor/format";
import { clock, when } from "../factor/format";
/** A UTC day: "Nov 3". */
export const md = (ms: number) => {const day = new Date(ms); return day.toLocaleDateString(intlLocale(), { timeZone: "UTC", month: "short", day: "numeric" });};
/** A moment, in UTC as every time in Quant (; the backend writes "Nov 4, 16:30 UTC" the same way): "16:30 UTC", "Nov 3, 16:30 UTC". */
export const hm = clock;
export const at = when;
/** A length of time: "8 min", "2 h". */
export const dur = (ms: number) => {const minutes = Math.round(Math.abs(ms) / 60_000); return minutes < 60 ? t("{minutes} min", { minutes: Math.max(minutes, 1) }) : minutes < 2880 ? `${Math.round(minutes / 60)} h` : `${Math.round(minutes / 1440)} d`;};
export const ago = (ms: number, now = Date.now()) => dur(now - ms);
/** A UTC day for a date input: "2026-11-03". */
export const isoDay = (ms: number) => new Date(ms).toISOString().slice(0, 10);
import { formatObservation } from "../format";
/** A bar's time: the date for daily and longer bars, date and time otherwise (midnight included). */
export const barTime = (value: number | null, timeframe: string | null) => value === null ? "—"
  : timeframe !== null && /[dwM]$/.test(timeframe) ? new Date(value).toISOString().slice(0, 10)
  : timeframe !== null ? new Date(value).toISOString().slice(0, 16).replace("T", " ") : formatObservation(value);
