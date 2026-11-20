/* Bars as the chart holds them: columns, times in UTC milliseconds, as the chart stream sends them. The view reads them in place. */
import type { UtcMs } from "./timeZone";

export interface Bars {
  time: UtcMs[];
  closeTime: UtcMs[];
  open: number[];
  high: number[];
  low: number[];
  close: number[];
  volume: (number | null)[];
}

export interface Bar { time: UtcMs; closeTime: UtcMs; open: number; high: number; low: number; close: number; volume: number | null }

export function emptyBars(): Bars {
  return { time: [], closeTime: [], open: [], high: [], low: [], close: [], volume: [] };
}

export function barAt(bars: Bars, index: number): Bar {
  return { time: bars.time[index], closeTime: bars.closeTime[index], open: bars.open[index], high: bars.high[index], low: bars.low[index], close: bars.close[index], volume: bars.volume[index] };
}

/** Rows after the last one; a first row at the last time replaces it (the forming bar just closed). */
export function appendBars(target: Bars, added: Bars): void {
  for (let index = 0; index < added.time.length; index++) putBar(target, barAt(added, index));
}

/** Drop rows from `start` on (brick chart types redraw their last bricks). */
export function truncateBars(target: Bars, start: number): void {
  for (const column of [target.time, target.closeTime, target.open, target.high, target.low, target.close, target.volume]) column.length = start;
}

/** The forming bar: replaces the last bar when it has the same time, otherwise follows it. */
export function putBar(target: Bars, bar: Bar): void {
  const last = target.time.length - 1;
  if (last >= 0 && bar.time < target.time[last]) throw new Error("Bars arrived out of time order");
  const at = last >= 0 && bar.time === target.time[last] ? last : last + 1;
  target.time[at] = bar.time; target.closeTime[at] = bar.closeTime;
  target.open[at] = bar.open; target.high[at] = bar.high; target.low[at] = bar.low; target.close[at] = bar.close; target.volume[at] = bar.volume;
}
