/* The settings column's values: the factor's current ones (as last run or edited), and how they differ from the selected run. */
import type { Parameter, ParamValue } from "../objects";
import { DEFAULT_RUN, type FactorRun, type FactorRunSettings, type SweepAxis } from "./api";

/** What the page edits: the formula, the platform's run settings and the code's settings. Number fields are kept as typed, so
    "1, 3, 5, 10" stays a list (a sweep) until it runs. */
export interface Draft { formula: string; run: FactorRunSettings; params: Record<string, ParamValue>; text: Record<string, string> }

/** Run settings that a list of values turns into a sweep, with the sweep axis each one is. */
export const SWEEPABLE: Record<string, string> = {holding: "holding", groups: "groups", costBps: "costBps", delay: "delay"};

export function runSettings(value: Partial<FactorRunSettings> | undefined): FactorRunSettings {
  const run = value ?? {};
  return {...DEFAULT_RUN, ...run, leaveOut: {...DEFAULT_RUN.leaveOut, ...(run.leaveOut ?? {})}, grouping: {...DEFAULT_RUN.grouping, ...(run.grouping ?? {})}};
}

/** The number a run setting holds, by its field key. */
export function runNumber(run: FactorRunSettings, key: string): number {
  return key === "groups" ? run.grouping.count : (run as unknown as Record<string, number>)[key];
}
export function withRunNumber(run: FactorRunSettings, key: string, value: number): FactorRunSettings {
  return key === "groups" ? {...run, grouping: {...run.grouping, count: value}} : {...run, [key]: value};
}

/** The values a typed number field lists ("1, 3, 5, 10"); one value is not a list. */
export function listOf(text: string | undefined): number[] | null {
  if (text === undefined || !text.includes(",")) return null;
  const values = text.split(",").map(part => part.trim()).filter(Boolean).map(Number);
  return values.length > 1 && values.every(Number.isFinite) ? values : null;
}

/** The sweep the draft asks for: each field holding a list, or nothing when every field holds one value. */
export function sweepAxes(draft: Draft): SweepAxis[] {
  const axes: SweepAxis[] = [];
  for (const [key, text] of Object.entries(draft.text)) {
    const values = listOf(text);
    if (values === null) continue;
    axes.push({name: key.startsWith("param:") ? key.slice(6) : SWEEPABLE[key] ?? key, values});
  }
  return axes;
}
export const sweepCount = (draft: Draft) => sweepAxes(draft).reduce((total, axis) => total * axis.values.length, 1);

function flat(value: unknown, prefix = ""): Record<string, unknown> {
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    return Object.fromEntries(Object.entries(value as Record<string, unknown>).flatMap(([key, item]) => Object.entries(flat(item, prefix ? `${prefix}.${key}` : key))));
  }
  return {[prefix]: value};
}
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** Which fields differ from the selected run (by the column's field keys), and how many changes that makes with the formula. */
export function changedFields(draft: Draft, run: FactorRun | null, mode: "formula" | "code"): {fields: Set<string>; count: number} {
  const fields = new Set<string>();
  if (run === null) return {fields, count: 0};
  const before = flat(runSettings(run.settings.run)), now = flat(draft.run);
  for (const key of new Set([...Object.keys(before), ...Object.keys(now)])) {
    if (key === "neutralize" && now[key] === undefined) continue; // left out takes the default the run recorded
    if (!same(before[key], now[key])) fields.add(key === "grouping.count" ? "groups" : key);
  }
  for (const [key, text] of Object.entries(draft.text)) if (listOf(text) !== null) fields.add(key.startsWith("param:") ? key : key === "groups" ? "groups" : key);
  if (mode === "code") for (const key of new Set([...Object.keys(run.settings.params ?? {}), ...Object.keys(draft.params)])) {
    if (!same(run.settings.params?.[key], draft.params[key])) fields.add("param:" + key);
  }
  const formula = mode === "formula" && run.formula !== null && draft.formula.trim() !== run.formula.trim() ? 1 : 0;
  return {fields, count: fields.size + formula};
}

/** Whether a declared setting shows: `showIf` names other settings and the values that show it. */
export function shown(parameter: Parameter, values: Record<string, ParamValue>, parameters: Parameter[]): boolean {
  if (!parameter.showIf) return true;
  return Object.entries(parameter.showIf).every(([name, value]) => {
    const other = parameters.find(item => item.name === name);
    return same(values[name] ?? other?.default, value);
  });
}

/** A code setting's value: the factor's, else the code's default. */
export const paramValue = (parameter: Parameter, values: Record<string, ParamValue>) => (parameter.name in values ? values[parameter.name] : parameter.default) as ParamValue;

/** The changes a run made against the one before, in words: "holding 5 → 10". */
const NAMES: Record<string, string> = {
  holding: "holding", delay: "delay", "grouping.count": "groups", "grouping.method": "grouping", "grouping.cuts": "cuts", "grouping.withinIndustry": "within industry",
  extremes: "extremes", scale: "scale", neutralize: "neutralize", costBps: "cost", borrowPct: "borrow", "leaveOut.halted": "halted names", "leaveOut.newListings": "new listings",
  "leaveOut.illiquid": "illiquid names", "data since": "data from", "data until": "data to", "data heldOutFrom": "held out from", "written as": "written as",
};
const LABELS: Record<string, string> = {"industry+size": "industry + size", industry: "industry", none: "none", mad3: "3 MAD", pct1: "1% / 99%", zscore: "z-score", rank: "rank", equal: "equal", cuts: "fixed cuts", sign: "by sign"};
const show = (value: unknown) => typeof value === "boolean" ? (value ? "on" : "off") : value == null ? "—" : LABELS[String(value)] ?? String(value);
export function changeText(change: {what: string; from: unknown; to: unknown}): {what: string; from: string; to: string} {
  const what = NAMES[change.what] ?? change.what.replace(/^data /, "data ").replace(/^code /, "");
  const unit = change.what === "costBps" ? " bps" : change.what === "borrowPct" ? "%" : "";
  return {what, from: show(change.from) + (change.from == null ? "" : unit), to: show(change.to) + (change.to == null ? "" : unit)};
}
