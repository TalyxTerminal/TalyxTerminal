/* The ranges typed into the code settings ("24, 48, 96"): the values, the number of combinations they make and what is wrong with them. */
import type {Parameter} from "../../objects";
import type {AxisValue} from "./types";

export const MAX_COMBINATIONS = 200;
/** What a switch's or a choice's box holds when every value is swept: both sides of a switch, every option of a choice. */
export const EVERY = "*every*";
export type RangeResult = {values: number[]} | {error: string};

/** The numbers of one box, in order and without repeats; each must be whole for an integer parameter and inside the declared bounds. */
export function parseList(text: string, parameter: Pick<Parameter, "type" | "label" | "min" | "max">): RangeResult {
  const parts = text.split(",").map(part => part.trim()).filter(part => part !== "");
  const values: number[] = [];
  for (const part of parts) {
    const number = Number(part);
    if (!Number.isFinite(number)) return {error: `${parameter.label} ${part} is not a number`};
    if (parameter.type === "int" && !Number.isInteger(number)) return {error: `${parameter.label} ${part} is not a whole number`};
    if ((parameter.min !== null && parameter.min !== undefined && number < parameter.min) || (parameter.max !== null && parameter.max !== undefined && number > parameter.max)) {
      return {error: `${parameter.label} ${part} is outside ${parameter.min ?? "…"}–${parameter.max ?? "…"}`};
    }
    if (!values.includes(number)) values.push(number);
  }
  return values.length ? {values} : {error: `${parameter.label}: enter numbers`};
}
export interface RangePlan {axes: {name: string; values: AxisValue[]}[]; total: number; errors: Record<string, string>; tooMany: string | null}
/** Every box with a comma in it is a range; together they make `total` combinations. */
export function planRanges(parameters: Parameter[], texts: Record<string, string>): RangePlan {
  const axes: RangePlan["axes"] = [], errors: Record<string, string> = {};
  for (const [name, text] of Object.entries(texts)) {
    const parameter = parameters.find(item => item.name === name);
    if (parameter && text === EVERY && (parameter.type === "bool" || parameter.type === "choice")) {
      axes.push({name, values: parameter.type === "bool" ? [false, true] : (parameter.options ?? []).map(option => option.value as AxisValue)});
      continue;
    }
    if (!parameter || !text.includes(",")) continue;
    const found = parseList(text, parameter);
    if ("error" in found) errors[name] = found.error; else if (found.values.length > 1) axes.push({name, values: found.values});
  }
  const total = axes.length ? axes.reduce((product, axis) => product * axis.values.length, 1) : 0;
  return {axes, total, errors, tooMany: total > MAX_COMBINATIONS ? `These ranges make ${total} combinations; a sweep runs at most ${MAX_COMBINATIONS}` : null};
}
/** The run button's words: it is a sweep when any box holds a range. */
export const runWord = (plan: RangePlan) => plan.total > 1 ? `Sweep ${plan.total}` : "Run backtest";
