/* A factor's Work: the private routes of backend/factor_runs.py and the shapes they return. */
import type { RestoredObject } from "../shared/restored";
import type { ModuleContext } from "@talyx/sdk";

import { at, route } from "../types";
import type {
  FactorCostsTab, FactorEventTab, FactorIcTab, FactorIndustryTab, FactorResultsStarted, FactorResultsState, FactorResultsView, FactorReturnsTab, FactorStyleTab, FactorTurnoverTab,
} from "./resultTypes";
import type { DataSettings, ObjectDetail, ParamValue, RunState } from "../objects";
import type { PlotFigure } from "../chart/Plot";

/** The platform's run settings of every factor run (backend/../native/factor_work.py DEFAULT_SETTINGS). */
export interface FactorRunSettings {
  leaveOut: {halted: boolean; newListings: boolean; illiquid: boolean};
  holding: number; delay: number;
  grouping: {method: "equal" | "cuts" | "sign"; count: number; cuts: string; withinIndustry: boolean};
  extremes: "mad3" | "pct1" | "none"; scale: "zscore" | "rank" | "none";
  /** Left out, the run takes industry + size for names Market gives an industry (stocks, funds) and nothing for the rest (crypto), and records which. */
  neutralize?: "industry+size" | "industry" | "none";
  costBps: number; borrowPct: number;
  /** Alphalens' options: returns less each day's mean (Demeaned) or as they are (Raw); returns and IC less each industry's mean (Group neutral). */
  demeaned: boolean; groupAdjust: boolean;
}
export const DEFAULT_RUN: FactorRunSettings = {
  leaveOut: {halted: true, newListings: true, illiquid: false}, holding: 5, delay: 1,
  grouping: {method: "equal", count: 5, cuts: "-1, 0, 1", withinIndustry: false},
  extremes: "mad3", scale: "zscore", costBps: 5, borrowPct: 0.3, demeaned: true, groupAdjust: false,
};

export interface FactorNumbers {
  rankIc: number | null; t: number | null; longShortNet: number | null; longShortGross: number | null; turnover: number | null; names: number;
  /** Names the index held on the median day, those without a Market instrument or prices included; null for a list of symbols. */
  members: number | null;
  mostLike: {name: string; version: string | number; rho: number} | null; breakEven: number | null; groupSize: number;
  sharpe: number | null; maxDrawdown: number | null; days: number; groups: number; holding: number;
}
/** Where a check leads: a Results tab, a tab under Work, a run setting, a line of code, the flagged price. */
export type FactorGo = {to: "results"; id: string} | {to: "tab"; id: string; order?: string; date?: number} | {to: "setting"; id: string} | {to: "code"; line: number} | {to: "flag"};
export interface FactorCheck { id: string; name: string; value: string; need: string; state: "ok" | "watch" | "notrun"; title: string; go: FactorGo | null;
  /** "Every year": each whole year's rank IC and long–short net of cost, beside the count. */
  years?: {year: number; rankIc: number; longShortNet: number | null}[] }
export interface FactorStep { name: string; state: "running" | "done" | "failed"; startedAt?: number; seconds?: number; done?: number; total?: number; counts?: Record<string, number>; detail?: string | null;
  /** The data step's count now (each has its own total): names checked, prices fetched, series exported, prices checked; and when it began. */
  phase?: "names" | "prices" | "export" | "checking"; phaseStartedAt?: number }
export interface FactorFailure {
  stage: "data" | "code" | "compute"; title: string; message: string; retry?: number; kept?: number;
  file?: string | null; line?: number | null; traceback?: string; formula?: FormulaError;
}
export interface FactorChange { what: string; from: unknown; to: unknown }
export interface FactorRunData { key?: string; dataFrom?: number; dataTo?: number; names?: number; requested?: number; notes?: string[]; frozenAt?: number; heldOutFrom?: number | null; library?: boolean }
export interface FactorRun {
  id: string; objectId: string; number: number; kind: "run" | "sweep" | "heldout"; state: RunState | "queued"; stage: string;
  createdAt: number; endedAt: number | null; seconds: number | null; formula: string | null; mode: "formula" | "code";
  settings: {data: DataSettings & {heldOutFrom?: string}; run: FactorRunSettings; params: Record<string, ParamValue>};
  changes: FactorChange[]; numbers: FactorNumbers | null; review: number; failure: FactorFailure | null;
  progress: {steps: FactorStep[]; startedAt: number; stage?: string; queue?: number} | null;
  starred: boolean; note: string; of: string | null; ofNumber: number | null;
  sweep: {axes: SweepAxis[]; total: number; done: number; selected?: number; best?: number} | null; version: number | null;
  data: FactorRunData | null; codeDigest: string | null; message: string | null; updatedAt: number; heldOut: {runId: string; number: number} | null;
  /** The code read later prices: its numbers do not count. */
  futureData: boolean;
  /** How many times this formula or code was scored on this held-out period and these data, in any factor (null for what cannot be validated). */
  heldOutUses?: number | null;
  /** Where the run's Results are (they build in the background after Work) and, while building, how far they are (0 to 1). */
  results: FactorResultsState; resultsProgress: number | null;
}

/** The Results tabs the backend builds, in the order the page lists them; Settings is read from the run record. */
export type ResultsTabName = "returns" | "ic" | "industry" | "style" | "turnover" | "costs" | "event";
export type ResultsPageTab = ResultsTabName | "settings" | "price";
export type { FactorCostsTab, FactorEventTab, FactorIcTab, FactorIndustryTab, FactorResultsStarted, FactorResultsState, FactorResultsView, FactorReturnsTab, FactorStyleTab, FactorTurnoverTab };
/** One figure of a Results tab: the figure, or why it could not be drawn. */
export type ResultsFigure = NonNullable<FactorReturnsTab["figures"]["groups"]>;
export interface SweepAxis { name: string; values: (number | string | boolean)[] }

/** A library factor like the run's, or (style true) one of the built-in styles, which the Style page of Results shows. */
export interface SimilarFactor { name: string; version: string | number; rho: number; topOverlap: number | null; rankIc: number | null; usedBy: string[]; style?: boolean }
export interface FactorYear { year: number; whole: boolean; rankIc: number | null; t: number | null; longShortNet: number | null; partial?: boolean; from?: string | null; to?: string | null }
export interface FactorFlag { t: number; i: number; label: number; text: string }
export interface RunWork {
  numbers: FactorNumbers; checks: FactorCheck[]; decay: (number | null)[] | null; years: FactorYear[]; groupMeans: (number | null)[];
  comparison?: Record<string, number>; comparisonMissing?: number[]; similar: SimilarFactor[]; flags: FactorFlag[]; figures: {groups: PlotFigure; decay?: PlotFigure}; inputs: string[]; notes: string[];
  dataTo: number; dataFrom: number; seconds: number;
}
export interface SweepCell { combo: Record<string, number | string | boolean>; settings: FactorRunSettings & {params?: Record<string, ParamValue>}; numbers: FactorNumbers; checks: FactorCheck[]; review: number; figures: {groups: PlotFigure; decay?: PlotFigure} }
export interface SweepWork { kind: "sweep"; axes: SweepAxis[]; cells: SweepCell[]; dataTo: number; dataFrom: number; notes: string[]; seconds: number }
export interface HeldOutWork { kind: "heldout"; numbers: FactorNumbers; years: FactorYear[]; figures: {heldout: PlotFigure}; from: number; to: number; dataTo: number; dataFrom: number; notes: string[]; seconds: number }
export interface WorkView { run: FactorRun; work: RunWork | SweepWork | HeldOutWork | null; codeDiff: {file: string; status: string; diff: string}[] | null; codeFiles?: {file: string; text: string}[] | null }

export interface FetchRow { instrumentId: string; ticker: string; sector: string | null; result: "fetched" | "partial" | "none" | "failed" | "waiting"; reason: string | null; bars: number | null; from: string | null }
export interface CrossRow { ticker: string; sector: string | null; inputs: Record<string, number | null>; raw: number | null; after: number | null; rank: number | null; group: number | null; next: number | null; missing?: string; flag?: string; unmapped?: boolean }
export interface CrossSection {
  date: number; run: number; runId: string; inputNames: string[]; rows: CrossRow[]; count: number; members: number; holding: number; groups: number;
  dates: {first: number; last: number}; previous: number | null; next: number | null;
}

export interface FormulaError { kind: "function" | "field" | "brackets" | "future" | "syntax"; at: number; length: number; message: string; fix?: string | null }
export interface OperatorDecl { name: string; group: string; signature: string; help: string; kind?: string }
export interface FieldDecl { name: string; label: string; group: string; from?: string | number; note?: string; unavailable?: string }
export interface FactorFields {
  operators: OperatorDecl[]; fields: FieldDecl[]; limits: {sourceId: string; text: string}[]; sources: {sourceId: string; label: string}[];
  /** Whether Market gives these names an industry (stocks and funds, not crypto): the settings that need one exist only then. */
  industries: boolean;
  /** What the run takes out when the factor leaves Neutralize out, decided by the backend for these data. */
  neutralizeDefault: "industry+size" | "none";
}

export const factorApi = (ctx: ModuleContext) => ({
  fields: (objectId: string) => route<FactorFields>(ctx, "GET", at`/objects/${objectId}/factor-fields`),
  check: (formula: string, objectId?: string) => route<{error: FormulaError | null}>(ctx, "POST", "/factor-formula/check", {formula, ...(objectId ? {objectId} : {})}),
  toCode: (objectId: string, formula: string) => route<ObjectDetail>(ctx, "POST", at`/objects/${objectId}/factor-formula/code`, {formula}),
  runs: (objectId: string, since?: number) => route<{items: FactorRun[]; now: number; complete: boolean}>(ctx, "GET", at`/objects/${objectId}/factor-runs`, since === undefined ? {} : {since}),
  start: (objectId: string, input: {kind?: "run" | "sweep" | "heldout"; formula?: string; settings?: FactorRunSettings; params?: Record<string, ParamValue>; sweep?: SweepAxis[]; of?: string}) =>
    route<FactorRun>(ctx, "POST", at`/objects/${objectId}/runs`, input),
  work: (runId: string, vs?: string) => route<WorkView>(ctx, "GET", at`/factor-runs/${runId}`, vs ? {vs} : {}),
  fetch: (runId: string) => route<{rows: FetchRow[]; runId: string}>(ctx, "GET", at`/factor-runs/${runId}/fetch`),
  cross: (runId: string, date?: number) => route<CrossSection>(ctx, "GET", at`/factor-runs/${runId}/cross-section`, date === undefined ? {} : {date}),
  retry: (runId: string) => route<FactorRun>(ctx, "POST", at`/factor-runs/${runId}/retry`),
  heldOut: (runId: string) => route<FactorRun>(ctx, "POST", at`/factor-runs/${runId}/held-out`),
  apply: (runId: string, expectedVersion: number, sweepIndex?: number) => route<RestoredObject>(ctx, "POST", at`/factor-runs/${runId}/apply`, {expectedVersion, ...(sweepIndex === undefined ? {} : {sweepIndex})}),
  createFrom: (runId: string, kind: "model" | "strategy", sweepIndex?: number) => route<ObjectDetail>(ctx, "POST", at`/factor-runs/${runId}/create`, {kind, ...(sweepIndex === undefined ? {} : {sweepIndex})}),
  cancel: (runId: string) => route<FactorRun>(ctx, "POST", at`/object-runs/${runId}/cancel`),
  annotate: (runId: string, input: {starred?: boolean; note?: string}) => route<FactorRun>(ctx, "PATCH", at`/object-runs/${runId}`, input),
  /** One tab of a run's Results with its figures; `vs` (up to 4 runs, repeated) adds their long–short to the groups chart; `cell` is a sweep's combination. */
  results: (runId: string, input: {tab?: ResultsTabName; vs?: string[]; cell?: number} = {}) => {
    const query = new URLSearchParams();
    if (input.tab) query.append("tab", input.tab);
    for (const other of input.vs ?? []) query.append("vs", other);
    if (input.cell !== undefined) query.append("cell", String(input.cell));
    return route<FactorResultsView>(ctx, "GET", at`/factor-runs/${runId}/results` + (query.size ? `?${query}` : ""));
  },
  /** Builds the Results of a run that has none (a sweep's combination, a run put aside, one that failed or was interrupted) in the background. */
  buildResults: (runId: string, cell?: number) => route<FactorResultsStarted>(ctx, "POST", at`/factor-runs/${runId}/results`, cell === undefined ? {} : {cell}),
  files: (objectId: string) => route<{files: string[]}>(ctx, "GET", at`/objects/${objectId}/files`),
});
export type FactorApi = ReturnType<typeof factorApi>;

export const finished = (run: FactorRun) => ["completed", "failed", "cancelled", "interrupted"].includes(run.state);
/** A run whose numbers Work shows: completed, not a sweep or a held-out validation. */
export const usable = (run: FactorRun | undefined | null): boolean => Boolean(run && run.state === "completed" && run.kind === "run" && run.numbers);
