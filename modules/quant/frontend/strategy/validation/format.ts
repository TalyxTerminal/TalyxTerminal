import { intlLocale } from "@talyx/sdk";
/* How a sweep writes its numbers and axis values: the draft's `sweepMetric` and the backend's labels. */
import { t } from "../../i18n";
import type {AxisValue, Metric, SweepAxis, SweepCase} from "./types";

const MINUS = "−";
export const nf = (value: number | null | undefined, digits = 2) => value == null || Number.isNaN(value) ? "" : value.toLocaleString("en-US", {minimumFractionDigits: digits, maximumFractionDigits: digits});
const signed = (value: number, digits: number) => (value > 0 ? "+" : value < 0 ? MINUS : "") + nf(Math.abs(value), digits);
/** One number of a combination as the heat map's cell writes it: Sharpe two digits, return signed, a fall with its minus, counts whole. */
export function metricText(metric: Metric, value: number | null | undefined): string {
  if (value == null) return "";
  switch (metric) {
    case "sharpe": case "profitFactor": return nf(value, 2);
    case "cagr": return signed(value * 100, 1) + "%";
    case "totalReturn": return signed(value * 100, 0) + "%";
    case "maxDrawdown": return signed(value * 100, 0) + "%";
    case "winRate": return nf(value * 100, 0) + "%";
    default: return nf(value, 0);
  }
}
export const METRIC_LABEL: Record<Metric, string> = {sharpe: t("Sharpe"), cagr: "CAGR", totalReturn: t.c("performance", "Return"), maxDrawdown: t("Max DD"), trades: t("Trades"), profitFactor: t("Profit factor"), winRate: t("Win rate")};
/** An axis value as a label: a whole number without a point, a period as its year, `Next open` for a fill. */
export function valueText(value: AxisValue): string {
  if (typeof value === "object") return value.since.slice(0, 4) === value.until.slice(0, 4) ? value.since.slice(0, 4) : `${value.since} – ${value.until}`;
  if (typeof value === "number") return `${Number(value.toPrecision(12))}`;
  if (value === "nextOpen") return t("Next open");
  if (value === "nextClose") return t("Next close");
  return String(value);
}
export const axisLabel = (axis: SweepAxis) => axis.label ?? axis.name;
export const same = (a: AxisValue, b: AxisValue) => JSON.stringify(a) === JSON.stringify(b);
export function caseTitle(axes: SweepAxis[], params: Record<string, AxisValue>) { return axes.map(axis => `${axisLabel(axis)} ${valueText(params[axis.name])}`).join(" · "); }
/** "3 of 8 ran", "Failed: boom", "Stopped" ... what the State column of the table says about a combination. */
export function stateText(item: SweepCase): string {
  if (item.state === "completed") return "";
  if (item.state === "failed") return t("Failed: {reason}", { reason: (item.message ?? "").split("\n")[0] });
  return item.state.charAt(0).toUpperCase() + item.state.slice(1);
}
/** "1 h 20 min", "6 min", "40 s": the time a sweep is expected to take. */
export function span(seconds: number): string {
  if (seconds < 90) return `${Math.max(1, Math.round(seconds))} s`;
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? t("{minutes} min", { minutes }) : `${Math.floor(minutes / 60)} h ${minutes % 60 ? t("{minutes} min", { minutes: minutes % 60 }) : ""}`.trim();
}
export const day = (ms: number) => new Date(ms).toISOString().slice(0, 10);
export const monthYear = (ms: number) => { const date = new Date(ms); return date.toLocaleDateString(intlLocale(), { timeZone: "UTC", month: "short", year: "numeric" }); };
export const sgnPc = (value: number | null | undefined, digits = 1) => value == null ? "" : signed(value * 100, digits) + "%";
export const cls = (value: number | null | undefined) => value == null || value === 0 ? undefined : value > 0 ? "up" : "down";
