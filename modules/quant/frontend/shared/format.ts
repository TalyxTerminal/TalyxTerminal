/** Number and date formatting for the Quant pages. Signs only on signed quantities; a delta keeps its value's decimals. */
export const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/** A plain number; a negative one (a Sharpe below zero) takes the true minus, and one that rounds to zero has no sign. */
export const nf = (value: number, digits = 2) => {
  const text = value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return /^-0(\.0+)?$/.test(text) ? text.slice(1) : text.replace(/^-/, "\u2212");
};
/** Signed quantities (returns, P&L, IC, deltas): + or the true minus sign. */
export const sgn = (value: number, digits = 2) => (value > 0 ? "+" : value < 0 ? "−" : "") + nf(Math.abs(value), digits);
/** A signed percentage from a fraction. */
export const pc = (value: number, digits = 1) => sgn(value * 100, digits) + "%";
/** An unsigned share from a fraction (turnover, weights, win rate). */
export const pct = (value: number, digits = 1) => nf(value * 100, digits) + "%";
/** A change in percentage points from fractions. */
export const pp = (value: number, digits = 1) => sgn(value * 100, digits) + " pt";
export const tone = (value: number): "up" | "down" | undefined => (value > 0 ? "up" : value < 0 ? "down" : undefined);
export const mdy = (date: Date) => `${MON[date.getUTCMonth()]} ${date.getUTCDate()}, ${date.getUTCFullYear()}`;
export const md = (date: Date) => `${MON[date.getUTCMonth()]} ${date.getUTCDate()}`;
export const hm = (date: Date) => `${String(date.getUTCHours()).padStart(2, "0")}:${String(date.getUTCMinutes()).padStart(2, "0")}`;
export const my = (date: Date) => `${MON[date.getUTCMonth()]} ${date.getUTCFullYear()}`;