/* An allocation's page: the private routes of backend/allocation_routes.py and the shapes they return. */
import type { ModuleContext } from "@talyx/sdk";
import { at, route } from "../types";
import type { Parameter, ParamValue } from "../objects";
import type { PlotFigure } from "../chart/Plot";

export type Figure = PlotFigure | {why: string} | null | undefined;
export interface Metrics {total: number; cagr: number; vol: number; sharpe: number | null; maxDrawdown: number; turnover: number; costDrag: number; vs6040?: number; vsEqualWeight?: number}
export interface Change {name: string; from: unknown; to: unknown}
export interface Check {id: string; name: string; value: string | null; need: string; state: "pass" | "fail" | "unknown"; at?: {date: string; asset?: string; fix?: string | null}}
export interface AllocationRun {
  id: string; number: number; runKind: "backtest" | "heldout"; state: string; stage: string; createdAt: number; endedAt: number | null; seconds: number | null; metrics: Metrics | null;
  changes: Change[]; starred: boolean; note: string; progress: {done: number; of: number | null; stage: string} | null; failure: {stage: string; title: string; message: string; fields: string[]} | null;
  message: string | null; of: string | null; sweep?: {id: string; index: number; of: number; axes: string[]; cell: Record<string, string | number>; move?: number | null} | null; objectId: string; data: {dataFrom?: string; dataTo?: string} | null; stopped: {period: number; at: number} | null; notes: string[]; queue: {position: number} | null;
}
export interface Row {asset: string; group: string | null; weight: number; riskShare: number; published: number | null; bound: "cap" | "zero" | "group" | "over cap" | "over group" | "under group" | null; drifted?: number | null}
export interface Work {
  metrics: Metrics; equalMetrics: Metrics; checks: Check[]; notes: string[]; periods: number; from: string; to: string; thisPeriod: Row[]; periodsFigure: Figure; periodsGroupFigure: Figure; periodDates: string[]; groups: string[]; seconds: number; spliced?: string[]; cutShort: string[];
  holdout?: {from: string; to: string; growth: Figure; rows: {held: Metrics; sixty: Metrics | null; equal: Metrics}} | null;
}
export interface RunView {run: AllocationRun & {settings: {mode?: string; data: Record<string, any>; params: Record<string, ParamValue>; pool: PoolRow[]}}; work: Work | null; periods: PeriodRow[] | null; old?: string}
export interface PeriodRow {date: string; heldFrom: string; weights: Record<string, number>; riskShare: Record<string, number>; turnover: number; cost: number; status: string; note: string; bound: Record<string, string>; seconds: number}
export interface PoolRow {ticker: string; name: string; instrumentId: string | null; group: string; currency: string | null; from: string; proxy: string; kind?: "run" | "library"; runId?: string}
export interface Prep {objectId: string; mode: string; groups: {name: string; settings: Parameter[]}[]; values: Record<string, ParamValue>; errors: {line: number | null; message: string}[]; pool: PoolRow[];
  capabilities: Record<string, Capability>; engineWarm?: boolean}
export interface Capability {short: boolean; costAware: boolean; robustReturns: boolean; robustCovariance: boolean; regularization: boolean; riskCaps: string[]; count: boolean; turnover: boolean; tracking: boolean;
  returns: boolean; measure: boolean; tree: boolean; budget: boolean}
export interface Trial {period: string; rows: Row[]; effectiveAssets: number; status: string; note: string; seconds: number; wall: number; cutShort: string[]; superseded?: boolean; drift?: {publishedVol: number; targetVol: number; riskShareGap: number}}
export interface Published {versionId?: string; version?: number; name?: string; period?: number; weights?: Record<string, number>; review?: {how: string; reason?: string; at: number} | null; publishedAt?: number}
export interface PublishPlan {runId: string; name: string; version: number; runNumber: number; firstPeriod: {label: string; from: string}; checks: {name: string; value: string | null; need: string | null; state: string}[];
  previous: {version: number; publishedAt: number} | null; compare: {measure: string; previous: number | string | null; current: number | string | null; change: number | null; unit: "pct" | "pt" | "ratio" | "text"; signed?: boolean; digits?: number}[];
  users: {name: string; kind: string}[]; blocked: {reason: string; field: "name" | "run"} | null}
export type Tab = "summary" | "weights" | "risk" | "returns" | "style" | "robust" | "settings";

export const allocationApi = (ctx: ModuleContext) => ({
  links: (objectId: string) => route<{runs: {kind: "run" | "library"; runId?: string; name: string; from?: string; to?: string}[]; library: {kind: "run" | "library"; runId?: string; name: string; from?: string; to?: string}[]}>(ctx, "GET", at`/objects/${objectId}/allocation-links`),
  prep: (objectId: string) => route<Prep>(ctx, "GET", at`/objects/${objectId}/allocation-prep`),
  /** `heldFrom`: the published period under review (ms), so the trial is fitted where the version fitted those weights; else `runId`: the run it is compared with, whose newest period is fitted again from what it held. */
  trial: (objectId: string, changes: Record<string, ParamValue>, pin?: {asset: string; weight: number}, published?: Record<string, number>, heldFrom?: number, runId?: string) => route<Trial>(ctx, "POST", at`/objects/${objectId}/allocation-trial`, {changes, ...(pin ? {pin} : {}), ...(published ? {published} : {}), ...(heldFrom !== undefined ? {heldFrom} : runId ? {runId} : {})}),
  start: (objectId: string, input: {kind?: "backtest" | "heldout"; of?: string} = {}) => route<AllocationRun>(ctx, "POST", at`/objects/${objectId}/allocation-runs`, input),
  runs: (objectId: string) => route<{items: AllocationRun[]; now: number}>(ctx, "GET", at`/objects/${objectId}/allocation-runs`),
  run: (runId: string) => route<RunView>(ctx, "GET", at`/allocation-runs/${runId}`),
  results: (runId: string, tab: Tab) => route<Record<string, any>>(ctx, "GET", at`/allocation-runs/${runId}/results`, {tab}),
  code: (runId: string) => route<{files: {file: string; text: string}[]}>(ctx, "GET", at`/allocation-runs/${runId}/code`),
  period: (runId: string, index: number) => route<PeriodRow>(ctx, "GET", at`/allocation-runs/${runId}/periods/${String(index)}`),
  heldStatus: (objectId: string, runId?: string) => route<{runId: string; number: number | null; from: string; to: string; days: number; short: boolean; used: {runId: string; number: number | null; same: boolean} | null}>(ctx, "GET", at`/objects/${objectId}/allocation-held-out`, runId ? {runId} : {}),
  published: (objectId: string) => route<Published>(ctx, "GET", at`/objects/${objectId}/allocation-published`),
  publishPlan: (runId: string, name: string) => route<PublishPlan>(ctx, "GET", at`/allocation-runs/${runId}/publish-plan`, {name}),
  publish: (runId: string, name: string, auto: boolean) => route<{versionId: string; version: number; name: string}>(ctx, "POST", at`/allocation-runs/${runId}/publish`, {name, auto}),
  review: (versionId: string, input: {period: number; how: "accept" | "one_off"; weights?: Record<string, number>; reason?: string}) => route<{review: {how: string}}>(ctx, "POST", at`/library-versions/${versionId}/allocation-review`, input),
  sweep: (objectId: string, axes: {name: string; values: (string | number)[]}[]) => route<{sweepId: string; runs: AllocationRun[]}>(ctx, "POST", at`/objects/${objectId}/allocation-sweeps`, {axes}),
  shock: (runId: string, target: string, pct: number) => route<{scenario: string; kind: string; portfolio: number; worst: string}>(ctx, "POST", at`/allocation-runs/${runId}/shock`, {target, pct}),
  notebook: (runId: string) => route<{notebookId: string}>(ctx, "POST", at`/runs/${runId}/notebook`, {}),
  apply: (runId: string, expectedVersion: number) => route<import("../shared/restored").RestoredObject>(ctx, "POST", at`/allocation-runs/${runId}/apply`, {expectedVersion}),
  stop: (runId: string) => route<AllocationRun>(ctx, "POST", at`/allocation-runs/${runId}/stop`),
  annotate: (runId: string, input: {note?: string; starred?: boolean}) => route<AllocationRun>(ctx, "PATCH", at`/allocation-runs/${runId}`, input),
});
export type AllocationApi = ReturnType<typeof allocationApi>;
export const ACTIVE = ["queued", "preparing", "running", "cancel_requested"];
export const isActive = (run: {state: string} | null | undefined) => Boolean(run && ACTIVE.includes(run.state));
export const usable = (run: AllocationRun | null | undefined) => Boolean(run && run.state === "completed" && run.metrics);
