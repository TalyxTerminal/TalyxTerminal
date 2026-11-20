/* A parameter sweep as the parameter row sets it up: which number parameters vary, over which from / to / step, and how many backtests that makes. */
import { plain, type Parameter, type ParamValue, type SweepRange } from "./objects";

/** Mirrors backend/object_sweeps.py: a sweep runs at most this many combinations. */
export const MAX_CASES = 200;
export interface SweepField extends SweepRange { on: boolean }
export type SweepFields = Record<string, SweepField>;

export const sweepable = (parameter: Parameter) => parameter.type === "int" || parameter.type === "float";

const decimals = (value: number) => {
  const text = plain(value, 10);
  return text.includes(".") ? text.length - text.indexOf(".") - 1 : 0;
};

/** How many values one range gives, counted as the backend does (from, from + step, … up to `to`, decimal steps exact), or why it gives none. */
export function rangeCount(name: string, type: "int" | "float", range: SweepRange): {count: number} | {error: string} {
  if (![range.from, range.to, range.step].every(Number.isFinite)) return {error: `${name}: enter numbers`};
  if (range.step <= 0) return {error: `${name}: the step must be above zero`};
  if (range.to < range.from) return {error: `${name}: "to" is below "from"`};
  if (type === "int" && !(Number.isInteger(range.from) && Number.isInteger(range.step))) return {error: `${name}: from and step must be whole numbers`};
  const scale = 10 ** Math.max(decimals(range.from), decimals(range.to), decimals(range.step));
  const [first, last, step] = [range.from, range.to, range.step].map(value => Math.round(value * scale));
  return {count: Math.floor((last - first) / step) + 1};
}

/** A first range around the current value: two steps either side, kept inside the parameter's bounds. */
export function defaultRange(parameter: Parameter, value: ParamValue): SweepRange {
  const current = typeof value === "number" ? value : 0;
  const step = parameter.type === "int" ? Math.max(1, Math.round(Math.abs(current) / 4)) : Number((Math.abs(current) / 4 || .1).toPrecision(1));
  const low = parameter.min === null ? -Infinity : parameter.min, high = parameter.max === null ? Infinity : parameter.max;
  const from = Math.max(low, Number((current - 2 * step).toFixed(10)));
  return {from, to: Math.min(high, Number((from + 4 * step).toFixed(10))), step};
}

/** The ranges of the parameters that are ticked, and how many backtests they make; `error` says why the sweep cannot start. */
export function sweepPlan(parameters: Parameter[], fields: SweepFields): {ranges: Record<string, SweepRange>; total: number; error: string | null} {
  const ticked = parameters.filter(parameter => sweepable(parameter) && parameter.name in fields && fields[parameter.name].on);
  const ranges: Record<string, SweepRange> = {};
  let total = ticked.length ? 1 : 0;
  for (const parameter of ticked) {
    const {from, to, step} = fields[parameter.name];
    const counted = rangeCount(parameter.label, parameter.type as "int" | "float", {from, to, step});
    if ("error" in counted) return {ranges, total: 0, error: counted.error};
    if (parameter.min !== null && from < parameter.min) return {ranges, total: 0, error: `${parameter.label}: at least ${plain(parameter.min)}`};
    if (parameter.max !== null && from + (counted.count - 1) * step > parameter.max + 1e-9) return {ranges, total: 0, error: `${parameter.label}: at most ${plain(parameter.max)}`};
    ranges[parameter.name] = {from, to, step};
    total *= counted.count;
  }
  if (!ticked.length) return {ranges, total, error: "Tick at least one parameter to sweep"};
  if (total > MAX_CASES) return {ranges, total, error: `${total} backtests; at most ${MAX_CASES}`};
  return {ranges, total, error: null};
}
