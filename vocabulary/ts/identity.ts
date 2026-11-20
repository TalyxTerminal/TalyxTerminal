import type { Instrument, SeriesId, TimeRange, Bar, Drawing } from "./types";
import casefold from "./casefold.json";
const text = (value: string): string => {
  if (typeof value !== "string" || !value.trim() || [...value].length > 256) throw new Error("Invalid identity text");
  return value.trim();
};
const optional = (value: string | null | undefined, upper = false): string | null => value?.trim() ? upper ? value.trim().toUpperCase() : value.trim() : null;
const fold = (value: string) => [...value].map(char => (casefold as Record<string, string>)[char] ?? char.toLowerCase()).join("");
export function exactDecimal(value: string | null | undefined): string | null {
  if (value == null) return null;
  if (!/^-?(0|[1-9][0-9]*)(\.[0-9]+)?$/.test(value)) throw new Error("Decimal must be an exact string");
  const cleaned = value.includes(".") ? value.replace(/0+$/, "").replace(/\.$/, "") : value;
  return /^-?0$/.test(cleaned) ? "0" : cleaned;
}
async function identity(prefix: string, value: Record<string, unknown>): Promise<string> {
  const canonical = JSON.stringify(Object.fromEntries(Object.entries(value).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)));
  const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical));
  return prefix + [...new Uint8Array(hash)].map(byte => byte.toString(16).padStart(2, "0")).join("");
}
const UNIT_NS: Record<string, bigint> = { s: 1_000_000_000n, m: 60_000_000_000n, h: 3_600_000_000_000n, d: 86_400_000_000_000n, w: 604_800_000_000_000n };
export const ASSIGNED_ID = /^vi2_[a-f0-9]{32}$/;
export function normalizeTimeframe(value: string): string {
  const trimmed = value.trim();
  const result = /^[1-9][0-9]*M$/.test(trimmed) ? trimmed.slice(0, -1) + "mo" : trimmed.toLowerCase();
  if (!/^[1-9][0-9]*(s|m|h|d|w|mo|y)$/.test(result)) throw new Error("Invalid timeframe");
  return result;
}
/** Fixed length of a timeframe in nanoseconds; calendar months and years have none (null). */
export function timeframeNanoseconds(value: string): bigint | null {
  const timeframe = normalizeTimeframe(value), count = BigInt(/^[0-9]+/.exec(timeframe)![0]), unit = timeframe.replace(/^[0-9]+/, "");
  return unit === "mo" || unit === "y" ? null : count * UNIT_NS[unit];
}
export async function instrumentId(record: Instrument): Promise<string> {
  if (record.identityVersion === 2) throw new Error("identityVersion 2 ids are assigned by Market, not computed");
  const venue = fold(text(record.venueId));
  // The hash material keeps the key sourceId, filled with the venue id.
  const payload: Record<string, unknown> = { version: 1, sourceId: venue, venueId: venue, symbol: text(record.symbol).toUpperCase(), calendarId: text(record.calendarId).toUpperCase() };
  for (const key of ["marketType", "assetClass", "contractOrientation", "optionSide", "adjustmentEligible"] as const) payload[key] = record[key] ?? null;
  for (const key of ["base", "quote", "settle", "underlyingSymbol"] as const) payload[key] = optional(record[key], true);
  for (const key of ["contractSize", "strike"] as const) payload[key] = exactDecimal(record[key]);
  for (const key of ["expiry", "retiredAt"] as const) {
    const value = record[key] ?? null;
    if (value !== null && !/^(0|[1-9][0-9]*)$/.test(value)) throw new Error(`${key} requires nanosecond decimal string`);
  }
  payload.expiry = record.expiry ?? null;
  if (record.retiredAt != null) payload.retiredAt = record.retiredAt;
  return identity("vi1_", payload);
}
export async function seriesId(record: SeriesId): Promise<string> {
  const anchor = record.adjustmentAnchor ?? null;
  if (anchor !== null && anchor !== "latest" && anchor !== "earliest") throw new Error("Invalid adjustment anchor");
  if (!["split", "split_dividend"].includes(record.adjustment) && anchor !== null) throw new Error("Only split and split_dividend series take an adjustment anchor");
  const nativeTimeframe = optional(record.timeframe);
  const timeframe = nativeTimeframe === null ? null : normalizeTimeframe(nativeTimeframe);
  if (["ohlcv", "reference"].includes(record.capability) && timeframe === null) throw new Error("Series requires timeframe");
  const provider = record.provider ?? null, derivedKey = record.key ?? null;
  if ((record.capability === "derived_value") !== (provider !== null && derivedKey !== null) || (record.capability !== "derived_value" && (provider !== null || derivedKey !== null))) throw new Error("Derived value series require provider and key; other series cannot carry them");
  const reference = record.referenceKind ?? null, unit = record.valueUnit ?? null;
  if (record.capability === "reference") {
    if (record.priceBasis !== "reference" || record.sessionScope !== "all" || record.adjustment !== "not_applicable" || !["exchange_rate", "yield", "commodity_price"].includes(reference ?? "") || !unit) throw new Error("Invalid reference series");
  } else if (reference !== null || record.priceBasis === "reference" || unit !== null && !(record.capability === "open_interest" && unit === "contracts")) throw new Error("Reference fields require reference capability");
  const payload: Record<string, unknown> = { version: 1, instrumentId: text(record.instrumentId), capability: record.capability, timeframe, priceBasis: record.priceBasis, sessionScope: record.sessionScope, adjustment: record.adjustment };
  if (record.capability === "reference") { payload.referenceKind = reference; payload.valueUnit = text(unit!); }
  else if (record.capability === "open_interest" && unit !== null) payload.valueUnit = unit;
  if (record.capability === "derived_value") { payload.provider = text(provider!); payload.key = text(derivedKey!); }
  // latest (forward adjusted) is the default and never enters the hash.
  if (anchor === "earliest") payload.adjustmentAnchor = anchor;
  if (record.importId != null) payload.importId = text(record.importId);
  return identity("vs1_", payload);
}
function compareDecimal(a: string, b: string): number {
  const [ai, af = ""] = a.split("."), [bi, bf = ""] = b.split("."), scale = Math.max(af.length, bf.length);
  const left = BigInt(ai + af.padEnd(scale, "0")), right = BigInt(bi + bf.padEnd(scale, "0"));
  return left < right ? -1 : left > right ? 1 : 0;
}
export async function validateSemantics(kind: string, value: unknown): Promise<void> {
  if (kind === "Instrument") {
    const record = value as Instrument;
    if (record.identityVersion === 2) { if (!ASSIGNED_ID.test(record.instrumentId) || record.retiredAt != null) throw new Error("Invalid assigned instrument id"); }
    else if (record.instrumentId !== await instrumentId(record)) throw new Error("Instrument identity mismatch");
  }
  if (kind === "SeriesId") { const record = value as SeriesId; if (record.seriesId !== await seriesId(record)) throw new Error("Series identity mismatch"); }
  if (kind === "TimeRange") { const record = value as TimeRange; if (BigInt(record.end) <= BigInt(record.start)) throw new Error("Time range must be nonempty and half-open"); }
  if (kind === "Bar") {
    const record = value as Bar;
    if (compareDecimal(record.low, record.high) > 0 || compareDecimal(record.volume, "0") < 0 || [record.open, record.close].some(price => compareDecimal(price, record.low) < 0 || compareDecimal(price, record.high) > 0)) throw new Error("Invalid bar prices or volume");
  }
  if (kind === "Drawing") {
    const record = value as Drawing, lengths: Record<string, number> = { trendline: 2, horizontal: 1, vertical: 1, rectangle: 2 };
    if (lengths[record.kind] && record.points.length !== lengths[record.kind]) throw new Error("Invalid drawing point count");
  }
}
