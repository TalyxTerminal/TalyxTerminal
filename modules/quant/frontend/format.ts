/** One place for how Quant shows numbers and times. */

import { intlLocale } from "@talyx/sdk";
import { t } from "./i18n";

const monthDay = new Intl.DateTimeFormat(intlLocale(), { month: "short", day: "numeric", timeZone: "UTC" });
const monthDayYear = new Intl.DateTimeFormat(intlLocale(), { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
const clock = new Intl.DateTimeFormat(intlLocale(), { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: "UTC" });

/** In UTC, as every time in Quant: "Oct 15, 14:32 UTC" this year, "Oct 15, 2025" otherwise. */
export function formatWhen(value: number | string | undefined | null): string {
  if (value == null || value === "") return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  if (date.getUTCFullYear() !== new Date().getUTCFullYear()) return monthDayYear.format(date);
  return `${monthDay.format(date)}, ${clock.format(date)} UTC`;
}

/** Relative age for lists: "2 min ago", "Yesterday", or a date. */
export function formatAge(value: number | undefined | null): string {
  if (!value) return "—";
  const seconds = (Date.now() - value) / 1000;
  if (seconds < 60) return t("Just now");
  if (seconds < 3600) return t("{minutes} min ago", { minutes: Math.floor(seconds / 60) });
  if (seconds < 86400) return t("{hours} h ago", { hours: Math.floor(seconds / 3600) });
  if (seconds < 172800) return t("Yesterday");
  return formatWhen(value);
}

/** Observation timestamps: date only when the time is midnight UTC. */
export function formatObservation(value: unknown): string {
  if (typeof value !== "string" && typeof value !== "number") return value == null ? "—" : String(value);
  const date = new Date(typeof value === "number" && value > 1e15 ? value / 1e6 : value);
  if (Number.isNaN(date.getTime())) return String(value);
  const iso = date.toISOString();
  return iso.endsWith("T00:00:00.000Z") ? iso.slice(0, 10) : iso.slice(0, 16).replace("T", " ");
}

/** Significant-figure number formatting for result tables and metrics. */
export function formatNumber(value: unknown, digits = 4): string {
  if (value == null || value === "") return "—";
  if (typeof value === "bigint") return groupInteger(value.toString());
  const number = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(number)) return typeof value === "string" ? value : "—";
  if (number === 0 || Math.abs(number) < 1e-12) return "0";
  const magnitude = Math.abs(number);
  if (magnitude >= 1e15) return number.toLocaleString("en-US", { maximumFractionDigits: 0 });
  if (magnitude < 1e-4) return number.toLocaleString("en-US", { maximumSignificantDigits: 3 });
  if (Number.isInteger(number)) return number.toLocaleString("en-US");
  const decimals = Math.max(0, Math.min(8, digits - Math.floor(Math.log10(magnitude)) - 1));
  return number.toLocaleString("en-US", { maximumFractionDigits: decimals, minimumFractionDigits: magnitude >= 1000 ? Math.min(2, decimals) : 0 });
}

export function formatPercent(value: unknown, fractionDigits = 2, ratio = true): string {
  const number = typeof value === "number" ? value : Number(value);
  if (value == null || !Number.isFinite(number)) return "—";
  return `${(ratio ? number * 100 : number).toFixed(fractionDigits)}%`;
}

export function formatSigned(value: unknown, formatter: (value: number) => string): string {
  const number = typeof value === "number" ? value : Number(value);
  if (value == null || !Number.isFinite(number)) return "—";
  return (number > 0 ? "+" : "") + formatter(number);
}

export function formatMoney(value: unknown, currency?: string): string {
  const number = typeof value === "number" ? value : Number(value);
  if (value == null || !Number.isFinite(number)) return "—";
  const text = number.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return currency ? `${text} ${currency}` : text;
}

function groupInteger(text: string): string {
  const negative = text.startsWith("-");
  const digits = negative ? text.slice(1) : text;
  return (negative ? "-" : "") + digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** Raw on-chain integers scaled by token decimals, shown with grouping. */
export function formatUnits(raw: string | number | bigint | null | undefined, decimals = 0, shown = 6): string {
  if (raw == null || raw === "") return "—";
  const text = String(raw).trim();
  if (!/^-?\d+(\.\d+)?$/.test(text)) return text;
  if (text.includes(".")) {
    const [whole, fraction] = text.split(".");
    const trimmed = fraction.slice(0, shown).replace(/0+$/, "");
    return groupInteger(whole) + (trimmed ? "." + trimmed : "");
  }
  if (!decimals) {
    const digits = text.replace("-", "");
    if (digits.length > 15) return compactInteger(text);
    return groupInteger(text);
  }
  const negative = text.startsWith("-");
  const digits = (negative ? text.slice(1) : text).padStart(decimals + 1, "0");
  const whole = digits.slice(0, -decimals);
  const fraction = digits.slice(-decimals).slice(0, shown).replace(/0+$/, "");
  return (negative ? "-" : "") + groupInteger(whole) + (fraction ? "." + fraction : "");
}

/** Very large integers (liquidity, wei) as a mantissa with a suffix. */
export function compactInteger(text: string): string {
  const negative = text.startsWith("-");
  const digits = negative ? text.slice(1) : text;
  const exponent = digits.length - 1;
  const mantissa = `${digits[0]}.${digits.slice(1, 4)}`;
  return `${negative ? "-" : ""}${mantissa}e${exponent}`;
}

/** Shorten a long identifier while keeping both ends recognisable. */
export function shortId(value: string, keep = 6): string {
  return value.length <= keep * 2 + 1 ? value : `${value.slice(0, keep)}…${value.slice(-4)}`;
}
