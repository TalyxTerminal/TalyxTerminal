/* A model's page: the private routes of backend/model_routes.py and the shapes they return. */
import type { RestoredObject } from "../shared/restored";
import type { ModuleContext } from "@talyx/sdk";
import { at, route } from "../types";
import type { FactorFields, FormulaError } from "../factor/api";
import type { DataSettings, Parameter, ParamValue } from "../objects";
import type { PlotFigure } from "../chart/Plot";

export interface Algorithm {
  id: string; label: string; group: string; groupLabel: string; deep?: boolean; shape?: string; route?: "epochs" | "qlib_fit"; needs?: ("base_run" | "concepts" | "aux_labels")[]; optimizers?: {id: string; label: string}[]; earlyStopOn?: {id: string; label: string}[]; note?: string; params: {key: string; label: string; type: string; default: number; hint?: string; setting?: string}[];
  objectives: {id: string; label: string; weights: boolean; param?: {name: string; min: number; max: number | null; default: number}}[];
}
export interface Offer {source: "library" | "value" | "run" | "builtin" | "market" | "code"; name: string; ref: string; version?: number; state?: string; kind?: string; unavailable?: string; features?: number;
  rankIc?: number | null; published?: {version?: number} | null; note?: string; group?: string; formula?: string}
export interface RunSettings {cost: number; topN: number}
export interface Running {concurrency: number; cpus: number; memoryUsed: number; memoryTotal: number; budget: number; running: number; waiting: number}
export interface ModelPrep {
  objectId: string; name: string; mode: "standard" | "code"; ownCode: boolean; groups: {name: string; settings: Parameter[]}[]; values: Record<string, ParamValue>;
  errors: {message: string; line: number | null}[]; algorithms: Algorithm[]; offers: Offer[]; run: RunSettings; data: DataSettings & {heldOutFrom?: string | null};
  batchLimit: number; running: Running; version: number; concepts?: {id: string; label: string; kind: "market" | "library"}[]; baseRuns?: {id: string; number: number; label: string; algorithm: string; inputs?: unknown; lookback: number; width: number; layers: number}[];
}
export interface DeepDevice {id: string; kind: "cpu" | "cuda" | "xpu"; label: string; memoryTotal?: number; memoryFree?: number}
export interface DeepEnvironment {
  installed: boolean; variant: "cpu" | "cuda" | "xpu" | null; offer: {variant: "cpu" | "cuda" | "xpu"; size: string; label: string; choices?: ("cpu" | "xpu")[]; reason?: string};
  installing: {state: string; fraction: number | null; label: string; size: string; startedAt: number} | null; failure: string | null; devices: DeepDevice[]; newCard: boolean;
}
export interface Vary {name: string; values: ParamValue[]}
export interface Check {name: string; value: string; need: string; state: "ok" | "watch" | "fix" | "block" | "notrun"; group: string; detail?: string}
export interface AxisWindow {k: number; train: {from: number; to: number}; valid: {from: number; to: number}; test: {from: number; to: number}; rows: number; flag?: string}
export interface Axis {
  start: number; end: number; warmUp: {from: number; to: number}; period: {from: number; to: number}; heldOut: {from: number; to: number} | null;
  inputs: {name: string; missingTo?: number; peek?: boolean; note: string}[]; windows: AxisWindow[]; count: number; every: string; gap: number;
}
export interface CheckAnswer {
  batchDefault?: number;
  ok: boolean; blocked: boolean; checks: Check[]; axis?: Axis; inputs?: {id: string; name: string; source: string; features: number; coverage: number; start: number | null; flag?: string; note?: string}[];
  seconds: number; windows?: number; names?: number; features?: number; failure?: {title: string; message: string}; engineParams?: {ok: boolean; errors: {key: string; message: string}[]} | null;
  label?: {reach: {ahead: number; back: number; start: {bar: number; at: string}} | null; error: FormulaError | null};
  batch?: {groups: {group: number; values: Record<string, ParamValue>; time: number | null; memory: number | null; blocked: boolean}[]; total: number; peak: number; free: number; concurrency: number; threads?: number; limit: number};
}
export interface WindowRow {k: number; trainIc: number | null; validIc: number | null; testIc: number | null; rows?: number; secs?: number; test?: {from: number; to: number}}
export interface ModelRun {
  id: string; objectId: string; name?: string; number: number; kind: "train" | "heldout" | "combine"; batch: string | null; group: number | null;
  status: "queued" | "running" | "completed" | "failed" | "stopped"; state: string; stage: string; createdAt: number; started: number | null; finished: number | null; time: number | null;
  mode: "standard" | "code"; algo: string | null; label: string; inputs: string[]; params: Record<string, ParamValue>; values: Record<string, ParamValue>; run: RunSettings;
  progress: {stage?: string; window?: number; round?: number; rounds?: number; epoch?: number; epochs?: number; trainLoss?: number; validLoss?: number; bestLoss?: number; bestEpoch?: number; pretrain?: number; pretrains?: number; fraction?: number; windows?: number; left?: number | null; used?: number | null; done?: number; total?: number; waitingFor?: string | null} | null;
  queue: {position?: number} | number | null; failure: {window?: number; title: string; detail?: string; message?: string; resumeFrom?: number | null; epoch?: number; kind?: "memory" | "gpu-memory"; saved?: number; batch?: number; tryBatch?: number; windowsTotal?: number | null; checkpoint?: number | null; file?: string; line?: number; need?: number; free?: number} | null;
  stopped: {window: number; auto: boolean} | null; windowsTotal: number | null; windowsDone: number; windows: WindowRow[]; perWindow: boolean;
  metrics: Record<string, number | null> | null; baselines: {bestInput: {name: string; ic: number | null}; equalWeight: {mean: number | null}; ridge: number | null} | null;
  portfolio?: string | null; checks: Check[] | null; review: number; passed: number; combinedFrom: string[] | null; of: string | null; ofNumber: number | null; dataTo: number | null; dataFrom: number | null;
  starred: boolean; note: string; published: {version: number; name?: string} | null; heldOut: {runId: string; number: number; state: string; numbers?: Record<string, number | null>} | null;
  message: string | null; updatedAt?: number; overrides: Record<string, ParamValue>; overrideFrom?: number | null; resumedAfterClose?: boolean; pipeline: {version: string; digest: string} | null; horizon: number | null;
  reach: {ahead: number; back: number; start: {bar: number; at: string}} | null; heldOutFrom: string | null; estimate: {memory?: number; seconds?: number} | null; memory?: number | null;
}
export interface RunList {items: ModelRun[]; batches: {id: string; title: string; vary: string[]; runs: string[]; started: number}[]; now: number; complete: boolean; running: Running}
export interface ModelResultsReply {parameterLabels: Record<string, string>; run: ModelRun; results: Record<string, any>; settings: any; pins: any; data: any; environment: any; compare?: {run: ModelRun; settings: any}}
export interface Plan {
  runId: string; number: number; name: string; version: number; previous: {version: number; versionId: string; state: string} | null; autoUpdate: boolean;
  alsoPublished: {name: string; fromRun: number; runId: string}[]; pinned: {name: string; version: number; versionId: string; state?: string}[];
  checksToReview: Check[]; users: {name: string; kind: string}[]; compare: {measure: string; previous: number | null; current: number | null; change: number | null; note?: string}[];
  paramsDiff: {name: string; previous: ParamValue; current: ParamValue}[]; blocked: {reason: string; field: "name" | "inputs"} | null; futureDataCheck: unknown; schedule: {every: number | null; text: string};
}
export interface HeldOutPlan {from: number; to: number; windows: number; minutes: number | null; uses: {objectId: string; objectName: string; number: number; version: number | null}[]}

export const modelApi = (ctx: ModuleContext) => ({
  prep: (id: string) => route<ModelPrep>(ctx, "GET", at`/objects/${id}/model-prep`),
  check: (id: string, input: {params?: Record<string, ParamValue>; vary?: Vary[]}) => route<CheckAnswer>(ctx, "POST", at`/objects/${id}/model-check`, input),
  inspect: (id: string, input: {input: string; feature?: string}) => route<any>(ctx, "POST", at`/objects/${id}/model-inputs/inspect`, input),
  train: (id: string, input: {params?: Record<string, ParamValue>; vary?: Vary[]; title?: string}) => route<{runs: ModelRun[]; batch: {id: string} | null}>(ctx, "POST", at`/objects/${id}/model-runs`, input),
  runs: (id: string, since?: number) => route<RunList>(ctx, "GET", at`/objects/${id}/model-runs`, since === undefined ? undefined : {since}),
  reset: (id: string) => route<unknown>(ctx, "POST", at`/objects/${id}/model-reset`, {}),
  fields: (id: string) => route<FactorFields>(ctx, "GET", at`/objects/${id}/factor-fields`),
  writeCode: (id: string) => route<unknown>(ctx, "POST", at`/objects/${id}/model-code`, {}),
  run: (runId: string) => route<ModelRun>(ctx, "GET", at`/model-runs/${runId}`),
  results: (runId: string, vs?: string) => route<ModelResultsReply>(ctx, "GET", at`/model-runs/${runId}/results`, vs ? {vs} : undefined),
  figures: (runId: string, keys: string[], vs?: string) => route<{runId: string; figures: Record<string, PlotFigure & {missing?: string}>}>(
    ctx, "GET", `${at`/model-runs/${runId}/figures`}?${new URLSearchParams([...keys.map(key => ["keys", key]), ...(vs ? [["vs", vs]] : [])])}`),
  compareFigure: (ids: string[]) => route<PlotFigure>(ctx, "GET", "/model-runs/compare-figure", {ids: ids.join(",")}),
  portfolio: (runId: string) => route<{state: string}>(ctx, "POST", at`/model-runs/${runId}/portfolio`, {}),
  valuesProgress: (runId: string) => route<{stage: "starting" | "inputs" | "reading"}>(ctx, "GET", at`/model-runs/${runId}/values-progress`),
  values: (runId: string, date?: number) => route<any>(ctx, "GET", at`/model-runs/${runId}/values`, date === undefined ? undefined : {date}),
  stop: (runId: string) => route<unknown>(ctx, "POST", at`/model-runs/${runId}/stop`, {}),
  stopBatch: (batchId: string) => route<unknown>(ctx, "POST", at`/model-batches/${batchId}/stop`, {}),
  resume: (runId: string, input: {alone?: boolean; override?: Record<string, ParamValue>} = {}) => route<unknown>(ctx, "POST", at`/model-runs/${runId}/resume`, input),
  next: (runId: string) => route<unknown>(ctx, "POST", at`/model-runs/${runId}/next`, {}),
  remove: (runId: string) => route<unknown>(ctx, "DELETE", at`/model-runs/${runId}/queue`),
  annotate: (runId: string, input: {starred?: boolean; note?: string}) => route<unknown>(ctx, "PATCH", at`/model-runs/${runId}`, input),
  compare: (ids: string[]) => route<any>(ctx, "POST", "/model-runs/compare", {ids}),
  combine: (ids: string[]) => route<ModelRun>(ctx, "POST", "/model-runs/combine", {ids}),
  heldOut: (runId: string) => route<ModelRun>(ctx, "POST", at`/model-runs/${runId}/held-out`, {}),
  heldOutPlan: (runId: string) => route<HeldOutPlan>(ctx, "GET", at`/model-runs/${runId}/held-out-plan`),
  apply: (runId: string, version: number) => route<RestoredObject>(ctx, "POST", at`/model-runs/${runId}/apply`, {expectedVersion: version}),
  strategy: (runId: string) => route<{id: string}>(ctx, "POST", at`/model-runs/${runId}/strategy`, {}),
  code: (runId: string) => route<{files: {file: string; text: string}[]; pipeline: {version: string; digest: string} | null}>(ctx, "GET", at`/model-runs/${runId}/code`),
  log: (runId: string) => route<{stdout: string; stderr: string}>(ctx, "GET", at`/model-runs/${runId}/log`),
  plan: (runId: string, name?: string) => route<Plan>(ctx, "GET", at`/model-runs/${runId}/publish-plan`, name ? {name} : undefined),
  publish: (runId: string, input: {name: string; autoUpdate: boolean}) => route<any>(ctx, "POST", at`/model-runs/${runId}/publish`, input),
  publication: (id: string) => route<any>(ctx, "GET", at`/model-publications/${id}`),
  stopPublication: (id: string) => route<any>(ctx, "POST", at`/model-publications/${id}/stop`, {}),
  deep: () => route<DeepEnvironment>(ctx, "GET", "/deep-environment"),
  installDeep: (variant?: string) => route<DeepEnvironment>(ctx, "POST", "/deep-environment/install", variant ? {variant} : {}),
  cancelDeep: () => route<DeepEnvironment>(ctx, "POST", "/deep-environment/cancel", {}),
  concurrency: (count: number) => route<Running>(ctx, "PUT", "/model-concurrency", {count}),
});
export type ModelApi = ReturnType<typeof modelApi>;
