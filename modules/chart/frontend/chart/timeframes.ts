/* Timeframes in Market's vocabulary: s, m, h, d, w, M (or mo), y; aligned UTC unless the calendar says otherwise. */

const UNIT_MS: Record<string, number> = { s: 1000, m: 60_000, h: 3_600_000, d: 86_400_000, w: 604_800_000, M: 2_592_000_000, mo: 2_592_000_000, y: 31_536_000_000 };

export function parseTimeframe(timeframe: string): { count: number; unit: string } {
  const match = /^(\d+)(mo|[smhdwMy])$/.exec(timeframe);
  if (!match) throw new Error(`Not a timeframe: ${timeframe}`);
  return { count: Number(match[1]), unit: match[2] };
}

/** Nominal length (months as 30 days); for spacing future bars of 24/7 markets and sizing calendar requests. */
export function timeframeMs(timeframe: string): number {
  const { count, unit } = parseTimeframe(timeframe);
  return count * UNIT_MS[unit];
}

export function isDailyOrLonger(timeframe: string): boolean {
  return ["d", "w", "M", "mo", "y"].includes(parseTimeframe(timeframe).unit);
}

/** TradingView's way of writing it: 1m, 15m, 1h, 4h, 1D, 1W, 1M. */
export function timeframeLabel(timeframe: string): string {
  const { count, unit } = parseTimeframe(timeframe);
  return `${count}${{ s: "s", m: "m", h: "h", d: "D", w: "W", M: "M", mo: "M", y: "Y" }[unit]}`;
}
