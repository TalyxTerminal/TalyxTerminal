/* A strategy's page: the private routes of backend/strategy_views.py and the shapes they return. */
import type { ModuleContext } from "@talyx/sdk";
import { at, route } from "../types";
import type { DataSettings, ParamValue, RunState } from "../objects";
import type { PlotFigure } from "../chart/Plot";

export type Kind = "perpetual" | "spot" | "stock" | "mixed";
export interface RunSettings {
  capital: number; currency?: string; maxPosition: number; reportCurrency?: string; accounts?: {venue: string; balances: string[]}[];
  exchange: {leverage: number; maxLeverage: number | null; marginModel: "leveraged" | "standard"; liquidation: boolean; maintenancePct: number; stopTrigger: "last" | "mark" | "index"; positionMode: "oneway" | "hedge"};
  limitThroughTicks: number;
  costs: {feePct?: number | null; makerPct?: number | null; commissionBps?: number | null; feeSource?: string; halfSpreadBps: number; impact: number; volumeCapPct: number | null; funding?: boolean; borrowPct?: number};
  fill: "nextOpen" | "nextClose"; shorting: "allowed" | "off"; halted?: "keep" | "close";
  benchmark: {kind: "hold" | "symbol" | "beta" | "equal" | "none"; symbol?: string}; riskFree: {basis: "irx" | "zero" | "fixed"; pct?: number}; heldOutFrom: string | null;
}
export interface RunSettingsView {
  objectId: string; kind: Kind; effective: RunSettings; defaults: RunSettings; stored: Partial<RunSettings>;
  applicable: {exchange: string[]; margin: string | null; benchmark: string[]; shorting: boolean}; feeUnavailable?: string; alsoBuilt?: string;
}
export interface Stats {
  totalReturn: number | null; cagr: number | null; maxDrawdown: number | null; sharpe: number | null; overBenchmark: number | null; costsAYear: number | null; trades: number; rejected?: number;
  sortino: number | null; volatility: number | null; calmar: number | null; winRate: number | null; profitFactor: number | null; openAtEnd?: number; benchmarkCagr?: number | null;
}
import type { CheckGo } from "./validation/types";
export interface Check { id: string; name: string; value: string; need: string; state: "pass" | "fail" | "notrun"; title: string; go: CheckGo | string | null; detail?: string }
export interface Change { what: string; from: unknown; to: unknown }
export interface Failure {
  file: string | null; line: number | null; error: string; stage: string; simulatedAt: number | null; instrument: string | null; cause: string; tradesBefore: number | null;
  equityBefore: number | null; function?: string | null; sourceLine?: string | null; locals: [string, string][]; withoutPrice: string[] | null; inEngine: boolean; partial: boolean;
}
export interface InputUsed { name: string; kind: string; versionId: string; version: number | null; reads?: "values" | "frozen"; trainedTo?: string }
export interface StrategyRun {
  id: string; objectId: string; number: number; kind: "backtest" | "sweep" | "walk" | "heldout"; state: RunState | "queued"; createdAt: number; endedAt: number | null; seconds: number | null;
  settings: {data: DataSettings & {heldOutFrom?: string; fillBars?: string}; run: RunSettings; params: Record<string, ParamValue>}; changes: Change[]; inputs: InputUsed[];
  stats: Stats | null; progress: {simulatedTo: number; value: number; tradesSoFar: number; stage: string; figure?: import("../chart/Plot").PlotFigure; fetch?: Fetching} | null; stage?: string | null; failure: Failure | null; starred: boolean; note: string; of: string | null;
  heldOut: boolean; codeDigest: string | null; environment?: {python: string | null; nautilus: string | null} | null; dataTo: string | null; notes: string[]; tags: string[]; message: string | null;
  /** The run whose report this one shows: a sweep or walk-forward's picked combination, else the run itself. */
  shownRunId?: string;
}
export interface Trade {
  id: number; instrument: string; nativeInstrument: string; side: "Long" | "Short"; entry: number; exit: number | null; entryPrice: number; exitPrice: number | null; exitBy: string;
  qty: number; pnl: number | null; ret: number | null; fees: number | null; spread: number | null; impact: number | null; funding: number | null; mae: number | null; mfe: number | null; bars: number | null; days: number | null;
}
export interface LogLine { t: number; instrument: string | null; kind: string; text: string; trade?: number }
export interface Rebalance {
  at: number; targets: Record<string, number>; ranks?: Record<string, number>; values?: Record<string, number>; actual: Record<string, number>; count: number; unfilled: {name: string; reason: string}[]; skipped: string | null;
  orders: {name: string; side: string; qty: number; filled: number; price: number | null; status: string; reason: string | null}[];
}
export interface HeldPart { cagr: number | null; maxDrawdown: number | null; sharpe: number | null; overBenchmark: number | null; costsAYear: number | null; trades: number }
export interface Costs { fees: number; spread: number; impact: number; funding: number; borrow: number; total: number; gross: number; net: number; incomplete: {instrument: string; from: number; to: number; reason: string | null}[] }
/** What the page opens a run with (`StrategyViews.page_run`): the numbers, checks, notes and the main figure; the trades, orders, log and rebalances are read a page at a time. */
export interface StrategyWork {
  kind: "single" | "multi"; currency: string; capital: number; annualization: number; riskFree: {basis: string; label: string; meanPct?: number; unavailable?: string}; benchmarkLabel: string; stats: Stats;
  /** The equity's first and last day, milliseconds. */
  span: [number, number] | null; tradesTotal: number; logTotal: number; costs: Costs; checks: Check[]; instrument: {id: string; symbol: string; type: string} | null; instrumentCount: number; notes: string[]; dataNotes?: string[]; shortSample?: boolean;
  heldOut?: {from: number; of: number | null; figure: import("../chart/Plot").PlotFigure | null; rows: [string, string, string][]; inSample: HeldPart | null; heldOut: HeldPart | null};
  partial?: boolean; stoppedAt?: number | null; usedToPick?: {name: string; to: string | null}[];
  multi?: {rebalancesTotal: number; holdings: {name: string; weight: number | null; pnl: number | null; days: number | null; sector: string | null}[]; holdingsTotal: number;
    contributions: {top: ContributionRow[]; bottom: ContributionRow[]; all: number}; byInstrument: ContributionRow[]};
  compared?: {runId: string; number: number; label: string; stats: Stats | null; delta: Record<string, number | null>; whatDiffers: string[]};
  figures?: {equity?: PlotFigure};
}
export interface ContributionRow { name: string; nativeInstrument: string; sector: string | null; pnl: number | null; fees: number | null; gross: number | null; trades: number }
export interface RunView { run: StrategyRun; work: StrategyWork | null; codeDiff: {file: string; status: string; diff: string}[] | null }
export type Figure = PlotFigure | {key: string; unavailable: string};
export interface ResultsView { runId: string; compared?: {runId: string; number: number; label: string; stats: Stats | null}[]; sections: Record<string, any>; missing: Record<string, string>; figuresFailed: Record<string, string>; toc: {id: string; title: string}[] }
export interface Page<T> { rows: T[]; total: number; offset: number; nextOffset: number | null }
export interface InputCard {
  name: string; kind: "factor" | "model" | "run"; fromModelRun?: {runId: string; number: number; model: string}; line: number | null; file: string | null; pinned: {versionId: string; version?: number; publishedAt?: number; trainedTo?: string};
  newer?: {versionId: string; version: number; publishedAt?: number}; fromRun: string | null; retired?: boolean; reads?: "values"; pickedOn?: string | null; changedSince?: string; previous?: {version: number}; unavailable?: string;
}

export const strategyApi = (ctx: ModuleContext) => ({
  runs: (objectId: string, since?: number) => route<{items: StrategyRunRow[]; now: number; complete: boolean}>(ctx, "GET", at`/objects/${objectId}/strategy-runs`, since === undefined ? {} : {since}),
  run: (runId: string, vs?: string) => route<RunView>(ctx, "GET", at`/strategy-runs/${runId}`, vs ? {vs} : {}),
  results: (runId: string, section?: string, vs?: string[]) => route<ResultsView>(ctx, "GET", at`/strategy-runs/${runId}/results`, {...(section ? {section} : {}), ...(vs?.length ? {vs: vs.slice(0, 3).join(",")} : {})}),
  trades: (runId: string, input: {offset?: number; limit?: number; side?: string; exit?: string; losing?: boolean; since?: number; until?: number; id?: number} = {}) => route<Page<Trade>>(ctx, "GET", at`/strategy-runs/${runId}/trades`, input),
  orders: (runId: string, input: {offset?: number; limit?: number; status?: string} = {}) => route<Page<Record<string, any>>>(ctx, "GET", at`/strategy-runs/${runId}/orders`, input),
  log: (runId: string, input: {offset?: number; limit?: number; kind?: string; trade?: number; since?: number} = {}) => route<Page<LogLine>>(ctx, "GET", at`/strategy-runs/${runId}/log`, input),
  rebalances: (runId: string, input: {filter?: "changed" | "unfilled" | "all"; at?: number; offset?: number; limit?: number; since?: number; until?: number} = {}) => route<Page<Rebalance>>(ctx, "GET", at`/strategy-runs/${runId}/rebalances`, input),
  inputs: (objectId: string) => route<{items: InputCard[]}>(ctx, "GET", at`/objects/${objectId}/inputs`),
  switchInput: (objectId: string, name: string, versionId: string) => route<{items: InputCard[]; notComparable: boolean}>(ctx, "POST", at`/objects/${objectId}/inputs/switch`, {name, versionId}),
  runSettings: (objectId: string) => route<RunSettingsView>(ctx, "GET", at`/objects/${objectId}/run-settings`),
  start: (objectId: string, of?: string) => route<StrategyRunRow>(ctx, "POST", at`/objects/${objectId}/runs`, of ? {of} : {}),
  cancel: (runId: string) => route<StrategyRunRow>(ctx, "POST", at`/object-runs/${runId}/cancel`),
  annotate: (runId: string, input: {starred?: boolean; note?: string}) => route<StrategyRunRow>(ctx, "PATCH", at`/object-runs/${runId}`, input),
});
export type StrategyApi = ReturnType<typeof strategyApi>;

/** A line of the run list: what the table shows of a run. */
export interface StrategyRunRow {
  id: string; number: number; kind: StrategyRun["kind"]; state: StrategyRun["state"]; createdAt: number; endedAt: number | null; changes: Change[]; stats: Stats | null;
  failure: {error: string | null; line: number | null} | null; starred: boolean; note: string; tags: string[]; heldOut: boolean; stage?: string | null;
  sweep?: {windows?: number; metric: string; done: number; total: number; completed: number; failed: number; best: {metric: string; value: number; index: number; params: Record<string, unknown>} | null};
}
export const ACTIVE = ["queued", "preparing", "running", "cancel_requested"];
export const isActive = (run: {state: string}) => ACTIVE.includes(run.state);
/** The data stage's steps in the order they run; a step with a total counts names (prices: names fetched; export: series written, two a stock). */
export const FETCH_STEPS = {names: "Checking names", prices: "Fetching prices", export: "Exporting series", terms: "Reading trading terms", coverage: "Checking coverage",
  inputs: "Writing backtest inputs", library: "Reading library inputs", benchmark: "Reading the benchmark and risk-free rate", extras: "Reading extra series"} as const;
/** How far fetching a run's data is: the step, how many of its total are done, and the names left out so far. */
export interface Fetching {waitingFor?: string | null; waitingUntil?: string | null; message?: string; step: keyof typeof FETCH_STEPS; done: number; total: number; leftOut: string[]}
export const usable = (run: StrategyRunRow | StrategyRun | null | undefined): boolean => Boolean(run && run.state === "completed" && run.kind === "backtest" && run.stats);
