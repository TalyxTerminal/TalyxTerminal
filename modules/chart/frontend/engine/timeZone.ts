import { intlLocale } from "@talyx/sdk";
/* Display time zones. Everything in Chart keeps real UTC milliseconds; only labels and tick weights are worked out in a zone.
   Shifting timestamps into the zone instead would make the repeated hour of a DST fall-back collide. */

export type UtcMs = number;

const formatters = new Map<string, Intl.DateTimeFormat>();
const offsets = new Map<string, Map<number, number>>();

function partsFormatter(zone: string): Intl.DateTimeFormat {
  let formatter = formatters.get(zone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat("en-US", { timeZone: zone, hourCycle: "h23", year: "numeric", month: "numeric", day: "numeric", hour: "numeric", minute: "numeric", second: "numeric" });
    formatters.set(zone, formatter);
  }
  return formatter;
}

/** Milliseconds to add to a UTC instant to read the wall clock in `zone`; zones change offset only on half hours. */
export function zoneOffset(zone: string, time: UtcMs): number {
  if (zone === "UTC") return 0;
  let cache = offsets.get(zone);
  if (!cache) { cache = new Map(); offsets.set(zone, cache); }
  const bucket = Math.floor(time / 1_800_000);
  let offset = cache.get(bucket);
  if (offset === undefined) {
    const parts: Record<string, number> = {};
    for (const part of partsFormatter(zone).formatToParts(new Date(bucket * 1_800_000))) if (part.type !== "literal") parts[part.type] = Number(part.value);
    offset = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute, parts.second) - bucket * 1_800_000;
    cache.set(bucket, offset);
  }
  return offset;
}

/** The wall clock in `zone`, read through the UTC getters. */
export function wallClock(zone: string, time: UtcMs): Date {
  return new Date(time + zoneOffset(zone, time));
}

const month = new Intl.DateTimeFormat(intlLocale(), { month: "short", timeZone: "UTC" });
const weekday = new Intl.DateTimeFormat(intlLocale(), { weekday: "short", timeZone: "UTC" });
const chineseDate = new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "short", day: "numeric", weekday: "short", timeZone: "UTC" });
const two = (value: number) => (value < 10 ? "0" : "") + value;

export function clockText(zone: string, time: UtcMs, seconds = false): string {
  const wall = wallClock(zone, time);
  return `${two(wall.getUTCHours())}:${two(wall.getUTCMinutes())}${seconds ? `:${two(wall.getUTCSeconds())}` : ""}`;
}

/** TradingView's crosshair date: `Wed 11 Nov '26`. */
export function dateText(zone: string, time: UtcMs): string {
  const wall = wallClock(zone, time);
  if (intlLocale() === "zh-CN") return chineseDate.format(wall);
  return `${weekday.format(wall)} ${two(wall.getUTCDate())} ${month.format(wall)} '${String(wall.getUTCFullYear()).slice(2)}`;
}

/** What one bar's time reads as in a zone: date only for daily and longer bars, date and clock otherwise. */
export function barTimeText(zone: string, time: UtcMs, dateOnly: boolean): string {
  return dateOnly ? dateText(zone, time) : `${dateText(zone, time)}  ${clockText(zone, time)}`;
}

/** Tick weights, the same scale as the engine's built-in time behaviour. */
export const Weight = { Second: 10, Minute1: 20, Minute5: 21, Minute30: 22, Hour1: 30, Hour3: 31, Hour6: 32, Hour12: 33, Day: 50, Month: 60, Year: 70 } as const;
const INTRADAY = [[1, Weight.Second], [60, Weight.Minute1], [300, Weight.Minute5], [1800, Weight.Minute30], [3600, Weight.Hour1], [10800, Weight.Hour3], [21600, Weight.Hour6], [43200, Weight.Hour12]] as const;

/** How significant the step from `previous` to `current` is on the wall clock of the zone they were read in. */
export function tickWeight(current: Date, previous: Date): number {
  if (current.getUTCFullYear() !== previous.getUTCFullYear()) return Weight.Year;
  if (current.getUTCMonth() !== previous.getUTCMonth()) return Weight.Month;
  if (current.getUTCDate() !== previous.getUTCDate()) return Weight.Day;
  const a = current.getTime() / 1000, b = previous.getTime() / 1000;
  for (let index = INTRADAY.length - 1; index >= 0; index--) if (Math.floor(a / INTRADAY[index][0]) !== Math.floor(b / INTRADAY[index][0])) return INTRADAY[index][1];
  return 0;
}

/** Tick label for a weight: year, month name, day of month, or clock. */
export function tickText(zone: string, time: UtcMs, weight: number, dateOnly: boolean): string {
  const wall = wallClock(zone, time);
  if (weight >= Weight.Year) return String(wall.getUTCFullYear());
  if (weight >= Weight.Month) return month.format(wall);
  if (weight >= Weight.Day || dateOnly) return String(wall.getUTCDate());
  return `${two(wall.getUTCHours())}:${two(wall.getUTCMinutes())}`;
}

/** The zone's short name right now (`UTC−5`), for the bottom bar clock. */
export function zoneLabel(zone: string, time: UtcMs): string {
  const minutes = Math.round(zoneOffset(zone, time) / 60_000);
  if (minutes === 0) return "UTC";
  const sign = minutes > 0 ? "+" : "−", hours = Math.floor(Math.abs(minutes) / 60), rest = Math.abs(minutes) % 60;
  return `UTC${sign}${hours}${rest ? `:${two(rest)}` : ""}`;
}
