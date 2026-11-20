/* Real time ⇄ logical bar position on one chart. Drawings, Pine objects and future indicator points all go through it.
   - Inside a bar, the position moves with time from its open to its close; a time in the gap after a bar's close (night,
     weekend, lunch) lands on the next bar's open, so a daily anchor falls on that day's first intraday bar.
   - After the loaded bars, positions continue over Market's future bar times (weekends and holidays take no room);
     continuous markets step by a fixed interval. Past the calendar's coverage there is no position: not drawn, not alerted.
   - Before the first loaded bar, positions extrapolate by the first bar's length. */
import type { UtcMs } from "./timeZone";

export interface TimeBars { readonly time: readonly UtcMs[]; readonly closeTime: readonly UtcMs[] }

/** What follows the loaded bars: Market's bar times inside its calendar coverage, or a fixed step for 24/7 markets. */
/** `coveredUntil`: where Market's calendar stops (exclusive); from there on nothing has a position. */
export type FutureBars = { kind: "calendar"; open: readonly UtcMs[]; close: readonly UtcMs[]; coveredUntil: UtcMs } | { kind: "continuous"; step: number };

export class TimeMap {
  constructor(private readonly bars: TimeBars, private readonly future: FutureBars) {}

  /** Index into `future.open` of the first future bar after the loaded ones (the loaded bars grow in place). */
  private futureStart(): number {
    if (this.future.kind !== "calendar") return 0;
    const last = this.bars.time[this.bars.time.length - 1];
    return upperBound(this.future.open, last);
  }

  toLogical(time: UtcMs): number | null {
    const { time: open, closeTime: close } = this.bars, count = open.length;
    if (count === 0) return null;
    if (time < open[0]) return (time - open[0]) / (close[0] - open[0]);
    const index = upperBound(open, time) - 1;
    if (index < count - 1 || time < close[count - 1]) {
      return time < close[index] ? index + (time - open[index]) / (close[index] - open[index]) : index + 1;
    }
    if (this.future.kind === "continuous") return count - 1 + (time - open[count - 1]) / this.future.step;
    if (time >= this.future.coveredUntil) return null;
    const start = this.futureStart(), futureOpen = this.future.open, futureClose = this.future.close;
    if (start >= futureOpen.length) return null;
    const at = upperBound(futureOpen, time) - 1;
    if (at < start) return count;
    const position = count + at - start;
    if (time < futureClose[at]) return position + (time - futureOpen[at]) / (futureClose[at] - futureOpen[at]);
    return at + 1 < futureOpen.length ? position + 1 : null;
  }

  toTime(logical: number): UtcMs | null {
    const { time: open, closeTime: close } = this.bars, count = open.length;
    if (count === 0) return null;
    const index = Math.floor(logical), fraction = logical - index;
    if (index < 0) return open[0] + logical * (close[0] - open[0]);
    if (index < count) return open[index] + fraction * (close[index] - open[index]);
    if (this.future.kind === "continuous") return open[count - 1] + (logical - (count - 1)) * this.future.step;
    const at = this.futureStart() + index - count;
    if (at >= this.future.open.length) return null;
    return this.future.open[at] + fraction * (this.future.close[at] - this.future.open[at]);
  }

  /** Bar open at the nearest whole position: where a click or a drag lands. */
  snapTime(logical: number): UtcMs | null {
    return this.toTime(Math.round(logical));
  }
}

/** First index whose value is greater than `value`. */
export function upperBound(values: readonly number[], value: number): number {
  let low = 0, high = values.length;
  while (low < high) { const middle = (low + high) >> 1; if (values[middle] <= value) low = middle + 1; else high = middle; }
  return low;
}

/** A straight drawing's value at a time: positions are bar positions, so closed days take no room. */
export function lineValueAt(map: TimeMap, from: { time: UtcMs; price: number }, to: { time: UtcMs; price: number }, time: UtcMs): number | null {
  const a = map.toLogical(from.time), b = map.toLogical(to.time), at = map.toLogical(time);
  if (a === null || b === null || at === null || a === b) return null;
  return from.price + (to.price - from.price) * (at - a) / (b - a);
}

/** A logical position on screen. Lightweight Charts places whole bars only (a fractional position comes back as 0), so
    a time inside a bar goes between its bar and the next by the bar spacing. */
export function logicalToX(scale: { logicalToCoordinate(logical: never): number | null; options(): { barSpacing: number } }, logical: number): number | null {
  const whole = Math.floor(logical), x = scale.logicalToCoordinate(whole as never);
  return x === null ? null : x + (logical - whole) * scale.options().barSpacing;
}
