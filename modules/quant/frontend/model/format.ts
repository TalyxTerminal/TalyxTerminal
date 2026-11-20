/* How the model page writes numbers: signed where the sign means something, one place for each unit. */
import type { ModelRun } from "./api";

export const sgn = (value: number | null | undefined, digits = 4) => value === null || value === undefined || !Number.isFinite(value) ? "—" : `${value >= 0 ? "+" : "−"}${Math.abs(value).toFixed(digits)}`;
export const pct = (value: number | null | undefined, digits = 1) => value === null || value === undefined || !Number.isFinite(value) ? "—" : `${value >= 0 ? "+" : "−"}${Math.abs(value * 100).toFixed(digits)}%`;
export const plainPct = (value: number | null | undefined, digits = 0) => value === null || value === undefined ? "—" : `${(value * 100).toFixed(digits)}%`;
export const minutes = (seconds: number | null | undefined) => {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 90) return `${Math.round(seconds)} s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`;
  return `${Math.floor(seconds / 3600)} h ${Math.round((seconds % 3600) / 60)} min`;
};
export const day = (stamp: number | null | undefined) => stamp === null || stamp === undefined ? "—" : new Date(stamp).toLocaleString("en-US", {month: "short", day: "numeric", timeZone: "UTC"});
export const dayYear = (stamp: number | null | undefined) => stamp === null || stamp === undefined ? "—" : new Date(stamp).toLocaleString("en-US", {month: "short", day: "numeric", year: "numeric", timeZone: "UTC"});
export const month = (stamp: number) => new Date(stamp).toLocaleString("en-US", {month: "short", year: "numeric", timeZone: "UTC"});
export const ACTIVE = ["queued", "preparing", "running", "cancel_requested"];
export const isDone = (run: ModelRun) => run.status === "completed";
export const algoLabel = (id: string | null) => id === null ? "—" : ({lightgbm: "LightGBM", xgboost: "XGBoost", catboost: "CatBoost", linear: "Least squares", ridge: "Ridge", lasso: "Lasso", nnls: "Non-negative least squares",
  double_ensemble: "DoubleEnsemble", mlp: "MLP", lstm: "LSTM", gru: "GRU", alstm: "ALSTM", transformer: "Transformer",
  localformer: "Localformer", tcn: "TCN", tabnet: "TabNet", sfm: "SFM", krnn: "KRNN", sandwich: "Sandwich", gats: "GATs", igmtf: "IGMTF", add: "ADD", adarnn: "ADARNN",
  tra: "TRA", tcts: "TCTS", hist: "HIST"} as Record<string, string>)[id] ?? id;
/** The head's data line as the draft writes it ("each day · 2016–2024 · 2025–"): how often, the years the runs may train and test on, and the held-out years.
 * A span to the latest close is open ("2025–"); a held-out start that is not Jan 1 names its month, so the two spans cannot read as overlapping. */
export function headSpan(data: {timeframe?: string; since?: string; until?: string; heldOutFrom?: string | null}): {often: string | null; years: string | null; held: {text: string; title: string} | null} {
  const often = !data.timeframe ? null : data.timeframe === "1d" ? "each day" : data.timeframe === "1w" ? "each week" : `each ${data.timeframe}`;
  const stamp = (iso: string) => Date.parse(iso + "T00:00:00Z");
  const year = (iso: string) => iso.slice(0, 4);
  const until = data.until && data.until !== "latest" ? data.until : null;
  const span = (from: string, fromText: string, to: string | null) => to === null ? `${fromText}–` : year(to) === year(from) ? fromText : `${fromText}–${year(to)}`;
  const held = data.heldOutFrom || null;
  const lastIn = held ? new Date(stamp(held) - 86_400_000).toISOString().slice(0, 10) : until;
  const years = data.since ? span(data.since, year(data.since), lastIn) : null;
  return {often, years, held: held === null ? null : {text: span(held, held.slice(5) === "01-01" ? year(held) : month(stamp(held)), until), title: `Held out from ${dayYear(stamp(held))}`}};
}
/** The selected run: the one the panel names, else the newest finished run (never the best of a batch), else the newest. Runs finishing together do not move it. */
/** The run shown when none is chosen: the newest completed one (by number, whatever the table's order), else the newest. */
export const pickSelected = (runs: ModelRun[], runId: string | null | undefined) => {
  const newest = (items: ModelRun[]) => items.reduce<ModelRun | null>((best, run) => best === null || run.number > best.number ? run : best, null);
  return runs.find(run => run.id === runId) ?? newest(runs.filter(run => run.status === "completed")) ?? newest(runs);
};
/** The dividers' limits: the preparation 280 px to the width less 520; the selected run 220 px to the height less 140. */
export const prepWidth = (x: number, width: number) => Math.round(Math.max(280, Math.min(width - 520, x)));
export const selHeight = (fromBottom: number, height: number) => Math.round(Math.max(220, Math.min(height - 140, fromBottom)));
/** What the run button does, in its words. */
export const runLabel = (kind: "train" | "batch" | "heldout" | "check", groups: number, heldNumber: number | null) =>
  kind === "batch" ? `Train a batch${groups > 1 ? ` · ${groups} groups` : ""}` : kind === "heldout" ? `Validate #${heldNumber ?? ""} on held-out` : kind === "check" ? "Check only" : "Train";
/** Where the held-out period has been used before, in any model: "#4 (v2) in Momentum, #9 in Value", or "none". */
export const heldOutUses = (uses: {number: number; version?: number | null; objectName: string}[]) =>
  uses.length ? uses.map(use => `#${use.number}${use.version ? ` (v${use.version})` : ""} in ${use.objectName}`).join(", ") : "none";
/** Answers to requests sent one after another: only the last one sent is taken (an older answer that comes back later is dropped). */
export function latestOnly() {
  let sent = 0;
  return {next: () => ++sent, current: (ticket: number) => ticket === sent};
}
