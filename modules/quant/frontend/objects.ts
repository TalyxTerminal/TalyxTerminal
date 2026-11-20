/* The Quant object shell's private routes (backend/object_routes.py) and the shapes they return. */
import { useEffect, useRef } from "react";
import type { IconName, Json, ModuleContext } from "@talyx/sdk";
import type { ContractTypes as MarketTypes } from "../../market/public/ts/types";
import { createClient as createQuantClient } from "../public/ts/types";
import { route, at } from "./types";
import type { RestoredObject } from "./shared/restored";

export type Kind = "notebook" | "factor" | "model" | "allocation" | "strategy" | "library";
export const KINDS: Record<Kind, {label: string; plural: string; icon: IconName}> = {
  notebook: {label: "Notebook", plural: "Notebooks", icon: "file"},
  factor: {label: "Factor", plural: "Factors", icon: "bars"},
  model: {label: "Model", plural: "Models", icon: "box"},
  allocation: {label: "Allocation", plural: "Allocations", icon: "layers"},
  strategy: {label: "Strategy", plural: "Strategies", icon: "bolt"},
  library: {label: "Library", plural: "Library", icon: "bookmark"},
};

export type Instrument = NonNullable<NonNullable<MarketTypes["instruments.get"]["input"]["records"]>[number]>;
export type CoverageItem = {capability: string; available: {periods: string[]}; source: unknown; local: unknown};
export const TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"] as const;
export type Timeframe = typeof TIMEFRAMES[number];
/** An exact Market index identity; a run freezes its historical members at its cutoff. */
export interface IndexSelection { instrument: Instrument; asOf?: number }
/** An index whose source provides historical members. */
export type IndexMaterial = {index: Instrument; source: {name: string}; start: string | null; end: string | null; pointInTime?: boolean; note?: string | null};
/** One way of choosing the symbols: symbols, a research list (by id), a historical index or the code's own rule. Research may also choose adjusted or raw prices. */
export interface DataSettings {
  symbols?: {instrument: Instrument}[]; list?: string; index?: IndexSelection; universe?: "code"; timeframe?: Timeframe; since?: string; until?: string; adjustment?: "adjusted" | "raw";
  /** Research: where the held-out period starts (UTC date); every run stops the day before. */
  heldOutFrom?: string | null;
  /** A strategy's finer bars the venue matches orders on (stops and targets inside a signal bar). */
  fillBars?: "5m" | "1m" | null;
}
export const RESEARCH_KINDS: Kind[] = ["factor", "model", "allocation"];
export interface AccountSettings { capital?: number; leverage?: number; fee?: {maker: number; taker: number}; stockCommission?: {rate: number; perShare: number; minimum: number; maximumRate: number | null} }
export type ParamValue = number | boolean | string;
/** `library` names a library item (q.library_param); the panel offers the library's items. */
export interface Parameter {
  name: string; type: "int" | "float" | "bool" | "text" | "library" | "choice" | "pick" | "table"; default: ParamValue; min: number | null; max: number | null; label: string; line: number;
  /** Where it is declared in the project, its step, hover text, section and the other settings' values that show it. */
  file?: string; step?: number | null; help?: string | null; group?: string | null; showIf?: Record<string, ParamValue> | null;
  /** choice: its values; pick: the kinds of library items or research objects it offers; table: its columns. */
  options?: {value: ParamValue; label: string}[]; kinds?: string[]; multiple?: boolean; pinned?: "version" | "run"; columns?: string[];
}
export interface ParameterError { line: number | null; message: string; file?: string }
export interface Parameters { parameters: Parameter[]; errors: ParameterError[] }

export type RunState = "preparing" | "running" | "cancel_requested" | "completed" | "failed" | "cancelled" | "interrupted";
/** A run's steps: code, data, environment, then the backtest or the research code; a publication's history, checks and commit. */
export type RunStage = "check" | "code" | "data" | "environment" | "backtest" | "sweep" | "run" | "history" | "checks" | "commit";
export interface DataLabel { symbols: string[]; timeframe: string | null; since: string | null; until: string | null; list: string | null; index: string | null; universe: "code" | null }
export interface Frame { file: string; path: string; line: number; function: string; text: string }
export interface RunError {
  stage: RunStage; code: string; message: string; details?: Record<string, unknown> | null; request?: DataLabel | null;
  type?: string; file?: string | null; line?: number | null; frames?: Frame[]; traceback?: string;
}
export interface Publication { sourceRunId: string; name: string; items: string[]; checks: number; versions: string[] }
/** How a sweep's combinations stand and which one its chart and report show. */
export interface SweepSummary { metric: string; parameters: string[]; total: number; completed: number; failed: number; running: number; selected: number | null; selectedRunId: string | null }
export interface RunSummary {
  id: string; objectId: string; name: string; runKind: "run" | "publish" | "sweep"; mode: "run" | "quick" | "publish" | "sweep"; publish: Publication | null;
  sweep: SweepSummary | null;
  state: RunState; stage: RunStage; createdAt: number; endedAt: number | null;
  message: string | null; error: RunError | null; params: Record<string, ParamValue>;
  parameterDiff: Record<string, {before: ParamValue | null; after: ParamValue | null}>; data: DataLabel;
  metrics: Record<string, number>; starred: boolean; note: string; codeDigest: string | null;
}
export interface RunSource { instrument: Instrument; series: {timeframe: string}; nativeInstrument: string }
/** The whole run: its record (full error, prepared data), the published result index and the history summary. */
export interface RunView {
  id: string; objectId: string; state: RunState; error: RunError | null; data: {sources: RunSource[]} | null;
  result: import("./types").ResearchResult | null; summary: RunSummary;
}

/** A ready-made strategy: its code is the module's and only a copy can be edited; `shortParameter` is the parameter that shorts. */
export interface Preset { id: string; kind: "strategy" | "factor" | "model"; label: string; description: string; shortParameter: string | null; /** A factor's: the style as a formula. */ formula?: string }
export interface ObjectSummary { id: string; kind: Kind; name: string; folder: string | null; preset: Preset | null; workspaceId: string; version: number; createdAt: number; updatedAt: number; lastRun: RunSummary | null }
/** What `GET /objects` adds for the all-objects table and the home: the run that stands for the object, its key numbers, and its library versions. */
export interface ObjectRow extends ObjectSummary {
  inThisWorkspace: boolean; workspace: string | null; runsCount: number; changedAt: number; checks: {passed: number; total: number} | null;
  representative: {run: string | null; number: number | null; how: "published" | "starred" | "latest_successful" | "none"; version?: string | null; note?: string | null};
  keyNumbers: Record<string, number | string | null> | null; library: {versions: {versionId: string; version: number; state: string; auto: boolean}[]; text: string} | null;
  /** A notebook: its number of cells and what its cells read (library items and runs). */
  cells?: number | null; reads?: string | null;
}
export interface ObjectList { kinds: {kind: Kind; count: number; creatable: boolean}[]; folders: {kind: Kind; name: string; count: number}[]; items: ObjectSummary[] }
export interface ObjectDetail extends ObjectSummary, Parameters {
  entry: string; entryUri: string;
  settings: {data: DataSettings; params: Record<string, ParamValue>; account: AccountSettings;
    /** A factor: written as a one-line formula or in code, the formula, and its run settings as last run or edited. */
    mode?: "formula" | "code"; formula?: string; run?: Record<string, unknown>};
  /** Whether the object has a code file (a factor written as a formula has none). */
  hasCode?: boolean;
  developer: {projectId: string; contextId: string} | null;
}
export interface DataDefaults {
  /** Spot and stocks trade on cash accounts, which cannot go short; `blockedBy` names those symbols. */
  canShort: {allowed: boolean; blockedBy: string[]};
  instruments: {instrumentId: string; symbol: string; venue: string; venueLabel: string; marketType: string; assetClass: string; quote: string; timeframe: string; history: CoverageItem}[];
  account: {currency: string; capital: number; leverage: number; fee: {maker: number; taker: number} | null; defaults: {capital: number; leverage: number; fee: {maker: number; taker: number} | null}};
}
/** The metrics a sweep can rank and colour its combinations by; every one is "more is better". */
export type SweepMetric = "totalReturn" | "dailySharpe" | "finalEquity" | "equityChange" | "winRate" | "orders";
export interface SweepRange { from: number; to: number; step: number }
export interface SweepCase { index: number; params: Record<string, number>; runId: string | null; state: "queued" | RunState; metrics: Record<string, number> | null; message: string | null }
export interface SweepDetail {
  runId: string; objectId: string; state: RunState; metric: SweepMetric; ranges: {name: string; type: "int" | "float"; values: number[]}[];
  cases: SweepCase[]; selected: number | null; selectedRunId: string | null; bestIndex: number | null; metrics: SweepMetric[];
}
export interface RunPage { items: RunSummary[]; total: number; offset: number; nextOffset: number | null }
export interface Comparison {
  current: RunSummary; previous: RunSummary; metrics: Record<string, {current: number | null; previous: number | null; difference: number | null}>;
  parameterDiff: Record<string, {before: ParamValue | null; after: ParamValue | null}>; codeChanged: boolean; dataChanged: boolean;
  dataDiff: {current: DataLabel; previous: DataLabel};
}

export interface ResearchListSummary { id: string; name: string; count: number; createdAt: number; updatedAt: number }
export interface ResearchList extends ResearchListSummary { members: {instrument: Instrument; displaySymbol: string; venue: string}[] }

export type LibraryKind = "factor" | "prediction" | "allocation" | "model" | "data";
export type LibraryState = "updating" | "failed" | "expired" | "delisted" | "fixed";
export interface LibraryVersionSummary {
  versionId: string; itemId: string; name: string; kind: LibraryKind; version: number; state: LibraryState;
  objectId: string | null; runId: string | null; workspaceId: string | null; createdAt: number; updatedAt: number;
  cursor: number | null; misses: number; failure: {code: string; message: string; at?: number} | null; retryAfter: number | null;
  rows: number; firstDatetime: number | null; lastDatetime: number | null; lastAvailableAt: number | null; fields: string[]; timeframe: string | null;
  occupiedBy: {owner_id: string; workspace_id: string | null}[]; superseded: boolean;
  origin: {objectId: string | null; runId: string | null; objectName: string | null; objectKind: Kind | null} | null;
}
export interface LibraryItemSummary { itemId: string; name: string; kind: LibraryKind; createdAt: number; versions: number; latest: LibraryVersionSummary }
export interface LibraryItem { itemId: string; name: string; kind: LibraryKind; versions: LibraryVersionSummary[] }
export type CheckMetrics = Record<string, number | null> & {updatedAt: number};
export interface LibraryVersion extends LibraryVersionSummary {
  checks: Record<string, CheckMetrics>; parameters: Record<string, ParamValue>; data: (DataSettings & {listName?: string}) | null;
  calendar: {calendarId: string; sessionScope: string; timezone: string; annualization: number} | null;
  code: {entry: string; digest: string} | null; model: Record<string, unknown> | null; hasReport: boolean;
  publication: {name: string; kind: LibraryKind; field?: string; path?: string} | null;
}
/** One update of a library version: when it was made, what it added and how, or why it did not. */
export interface LibraryUpdate {
  at: number; versionId: string; version: number; outcome: "updated" | "failed" | "expired" | "interrupted" | "cancelled"; values: number;
  firstDatetime: number | null; lastDatetime: number | null; mode: "scheduled" | "catch_up" | null; message: string | null;
}
export interface ValueRow { datetime: number; instrument: string; field: string; value: number; availableAt: number; recordedAt: number; mode: "history" | "scheduled" | "catch_up" }
export interface ValuePage { versionId: string; state: LibraryState; version: number; rows: ValueRow[]; total: number; nextOffset: number | null }

export const TERMINAL: RunState[] = ["completed", "failed", "cancelled", "interrupted"];
export const active = (run: {state: RunState}) => !TERMINAL.includes(run.state);


export const api = (ctx: ModuleContext) => ({
  list: (input: {kind?: Kind; all?: boolean; query?: string}) => route<ObjectList>(ctx, "GET", "/objects", input),
  // Folders and deleting an object go through Quant's public actions, the same ones an AI calls (a deletion by an AI asks the user first).
  createFolder: (kind: Kind, name: string) => createQuantClient(ctx).create_object_folder({kind, name}) as Promise<{kind: Kind; name: string}>,
  renameFolder: (kind: Kind, from: string, name: string) => createQuantClient(ctx).rename_object_folder({kind, from, name}),
  deleteFolder: (kind: Kind, name: string) => createQuantClient(ctx).delete_object_folder({kind, name}),
  create: (kind: Kind, folder?: string | null, preset?: string) => route<ObjectDetail>(ctx, "POST", "/objects", {kind, ...(folder ? {folder} : {}), ...(preset ? {preset} : {})}),
  presets: () => route<{items: Preset[]}>(ctx, "GET", "/strategy-presets"),
  copy: (id: string) => route<ObjectDetail>(ctx, "POST", at`/objects/${id}/copy`, {}),
  open: (id: string) => route<ObjectDetail>(ctx, "POST", at`/objects/${id}/open`),
  read: (id: string) => route<ObjectDetail>(ctx, "GET", at`/objects/${id}`),
  update: (id: string, input: {name?: string; folder?: string | null}) => route<ObjectSummary>(ctx, "PATCH", at`/objects/${id}`, input),
  remove: (id: string) => createQuantClient(ctx).delete_object({objectId: id}),
  settings: (id: string, input: {data?: DataSettings; params?: Record<string, ParamValue>; account?: AccountSettings; formula?: string; run?: object}) =>
    route<ObjectDetail>(ctx, "PUT", at`/objects/${id}/settings`, input),
  parameters: (id: string, content: string) => route<Parameters>(ctx, "POST", at`/objects/${id}/parameters`, {content}),
  defaults: (id: string) => route<DataDefaults>(ctx, "GET", at`/objects/${id}/data-defaults`),
  start: (id: string, quick = false) => route<RunSummary>(ctx, "POST", at`/objects/${id}/runs`, quick ? {quick} : {}),
  startSweep: (id: string, ranges: Record<string, SweepRange>) => route<RunSummary>(ctx, "POST", at`/objects/${id}/sweeps`, {ranges}),
  sweep: (runId: string) => route<SweepDetail>(ctx, "GET", at`/object-runs/${runId}/sweep`),
  runs: (id: string, offset = 0) => route<RunPage>(ctx, "GET", at`/objects/${id}/runs`, {offset, limit: 100}),
  run: (runId: string) => route<RunView>(ctx, "GET", at`/object-runs/${runId}`),
  cancel: (runId: string) => route<RunSummary>(ctx, "POST", at`/object-runs/${runId}/cancel`),
  annotate: (runId: string, input: {note?: string; starred?: boolean}) => route<RunSummary>(ctx, "PATCH", at`/object-runs/${runId}`, input),
  compare: (runId: string, otherId: string) => route<Comparison>(ctx, "GET", at`/object-runs/${runId}/compare/${otherId}`),
  apply: (runId: string, expectedVersion: number) => route<RestoredObject>(ctx, "POST", at`/object-runs/${runId}/apply`, {expectedVersion}),
  log: (runId: string, stream: string, offset: number) => route<{text: string; nextOffset: number; eof: boolean}>(ctx, "GET", at`/object-runs/${runId}/logs/${stream}`, {offset}),
  publish: (runId: string, input: {name: string; items?: string[]}) => route<RunSummary>(ctx, "POST", at`/object-runs/${runId}/publish`, input),
  library: (query?: string) => route<{items: LibraryItemSummary[]}>(ctx, "GET", "/library", query ? {query} : {}),
  libraryItem: (itemId: string) => route<LibraryItem>(ctx, "GET", at`/library/items/${itemId}`),
  libraryVersion: (versionId: string) => route<LibraryVersion>(ctx, "GET", at`/library/versions/${versionId}`),
  libraryReport: (versionId: string) => route<import("./types").ResearchResult>(ctx, "GET", at`/library/versions/${versionId}/report`),
  libraryValues: (versionId: string, input: {offset?: number; limit?: number; field?: string}) => route<ValuePage>(ctx, "GET", at`/library/versions/${versionId}/values`, input),
  libraryUpdates: (versionId: string) => route<{items: LibraryUpdate[]}>(ctx, "GET", at`/library/versions/${versionId}/updates`, {limit: 200}),
  delist: (itemId: string) => route<LibraryItem>(ctx, "POST", at`/library/items/${itemId}/delist`),
  lists: () => route<{items: ResearchListSummary[]}>(ctx, "GET", "/research-lists"),
  readList: (listId: string) => route<ResearchList>(ctx, "GET", at`/research-lists/${listId}`),
  createList: (name: string) => route<ResearchList>(ctx, "POST", "/research-lists", {name, members: []}),
  updateList: (listId: string, input: {name?: string; members?: {instrument: Instrument}[]}) => route<ResearchList>(ctx, "PATCH", at`/research-lists/${listId}`, input),
  deleteList: (listId: string) => route(ctx, "DELETE", at`/research-lists/${listId}`),
  openCommonCode: () => route<{directory: string; projectId: string; contextId: string}>(ctx, "POST", "/common-code/open"),
  commonCode: () => route<{directory: string; files: string[]}>(ctx, "GET", "/common-code"),
});

/**
 * The common code folder in Developer: its first module when it has one, otherwise the folder as Developer's project,
 * so the editor shows where shared modules go.
 */
export async function openCommonCode(ctx: ModuleContext, api: Api) {
  const opened = await api.openCommonCode();
  const files = (await api.commonCode()).files;
  const first = files.find(file => file.endsWith(".py")) ?? files[0];
  if (first !== undefined) {
    const uri = new URL(first.split("/").map(encodeURIComponent).join("/"), pathUri(opened.directory) + "/").href;
    await ctx.platform.panels.openTarget({module: "developer", panelType: "main", objectId: `file:${uri}`});
    return;
  }
  await ctx.platform.panels.openTarget({module: "developer", panelType: "main", objectId: `folder:${opened.directory}`});
}
/** A Windows or POSIX folder path as a file URI. */
export const pathUri = (path: string) => "file:///" + path.replaceAll("\\", "/").replace(/^\/+/, "").split("/").map((part, index) => index === 0 && /^[A-Za-z]:$/.test(part) ? part : encodeURIComponent(part)).join("/");
export type Api = ReturnType<typeof api>;

/** Numbers as people write them: grouped, no exponent, at most `digits` decimals. */
export function plain(value: number, digits = 8): string {
  return value.toLocaleString("en-US", {maximumFractionDigits: digits, useGrouping: false});
}
export const percent = (value: number | null | undefined, digits = 2) =>
  typeof value === "number" ? `${value > 0 ? "+" : ""}${(value * 100).toFixed(digits)}%` : "—";

const STAGES: Record<RunStage, string> = {check: "Checking the settings", code: "Saving code", data: "Fetching data", environment: "Preparing environment", backtest: "Backtesting", sweep: "Backtesting", run: "Running the code",
  history: "Reading the values", checks: "Checking for future data", commit: "Adding to the library"};
/** What a run is doing or how it ended, in words. */
export function runStatus(run: {state: RunState; stage: RunStage}): string {
  if (run.state === "preparing" || run.state === "running") return STAGES[run.stage];
  return {cancel_requested: "Cancelling", completed: "Completed", failed: "Failed", cancelled: "Cancelled", interrupted: "Interrupted"}[run.state];
}

const DAY = 86_400_000;
/** The last whole UTC day: today's bars are not complete yet, so a range ending today would miss data. */
export const lastFullDay = () => new Date(Date.now() - DAY).toISOString().slice(0, 10);
/** The start date `months` back from today (UTC). */
export function monthsBack(months: number, from = new Date()): string {
  const date = new Date(Date.UTC(from.getUTCFullYear(), from.getUTCMonth() - months, from.getUTCDate()));
  return date.toISOString().slice(0, 10);
}
/** The day an `until` stands for when it is shown: the last full day for "latest" (the run records the day the backend resolved), else the date. */
export const untilDay = (data: {until?: string}): string | undefined => data.until === "latest" ? lastFullDay() : data.until;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const monthYear = (iso: string) => `${MONTHS[Number(iso.slice(5, 7)) - 1]} ${iso.slice(0, 4)}`;
/** "Jan 2016 – latest": the period of a data setting for the head of a page. */
export const periodText = (since: string, until: string) => `${monthYear(since)} – ${until === "latest" ? "latest" : monthYear(until)}`;
/** "2Y", "6M" or the dates, for a since–until range ending today or yesterday. */
export function rangeLabel(since: string, until: string, now = new Date()): string {
  if (until === "latest") {
    for (const [months, label] of [[6, "6M"], [12, "1Y"], [24, "2Y"], [60, "5Y"], [120, "10Y"]] as const) if (since === monthsBack(months, now)) return label;
    return `${since} – latest`;
  }
  const end = Date.parse(until + "T00:00:00Z"), last = Date.parse(now.toISOString().slice(0, 10) + "T00:00:00Z");
  if (last - end <= DAY) for (const [months, label] of [[6, "6M"], [12, "1Y"], [24, "2Y"], [60, "5Y"], [120, "10Y"]] as const) if (since === monthsBack(months, now)) return label;
  return `${since} – ${until}`;
}
/** A historical index the way runs name it: the index's name and that its members follow their effective dates. */
export const indexText = (name: string) => `${name} members`;
/** What the symbols are: the symbols themselves, the research list's name, the historical index or the code's rule; null when nothing is chosen. */
export function universeText(data: DataSettings, lists: ResearchListSummary[] | null, indexes: IndexMaterial[] | null = null): string | null {
  const symbols = data.symbols ?? [];
  if (symbols.length) return symbols.length > 2 ? `${symbols[0].instrument.displaySymbol} +${symbols.length - 1}` : symbols.map(item => item.instrument.displaySymbol).join(", ");
  if (data.list) {
    const found = lists === null ? undefined : lists.find(item => item.id === data.list);
    return found === undefined ? "Research list" : `${found.name} (${found.count})`;
  }
  if (data.index) {
    const selected = data.index;
    const found = indexes === null ? undefined : indexes.find(item => item.index.instrumentId === selected.instrument.instrumentId);
    const instrument = found === undefined ? selected.instrument : found.index;
    return indexText(instrument.name ?? instrument.displaySymbol);
  }
  return data.universe === "code" ? "Rule in the code" : null;
}
/** Whether a run has what it needs: the symbols chosen one way, a bar period and a range. */
export const dataReady = (data: DataSettings) => universeText(data, null) !== null && Boolean(data.timeframe && data.since && data.until);
export function dataSummary(data: DataSettings, lists: ResearchListSummary[] | null = null, indexes: IndexMaterial[] | null = null): string | null {
  const universe = universeText(data, lists, indexes);
  if (universe === null || !data.timeframe || !data.since || !data.until) return null;
  // The draft's line: name, how often, the months it covers and, when there is one, where the held-out period starts.
  const month = (day: string) => new Date(day + "T00:00:00Z").toLocaleString("en-US", {month: "short", year: "numeric", timeZone: "UTC"});
  const often = data.timeframe === "1d" ? "each day" : data.timeframe;
  const range = data.until === "latest" ? rangeLabel(data.since, data.until) : `${month(data.since)} – ${month(data.until)}`;
  const held = data.heldOutFrom ? ` · held out from ${month(data.heldOutFrom)}` : "";
  return `${universe} ${often} · ${range}${held}`;
}

/** Calls `callback` with each event of `name` while mounted; the latest callback is used without resubscribing.
    `onReconnect` reads everything again once Quant restarted (events sent meanwhile are not delivered). */
export function useEvent(ctx: ModuleContext, name: string, callback: (payload: Json) => void, onReconnect?: () => void) {
  const latest = useRef(callback);
  const reconnected = useRef(onReconnect);
  latest.current = callback;
  reconnected.current = onReconnect;
  useEffect(() => {
    const held = holdSubscriptions();
    held.keep(ctx.events.subscribe(name, payload => {if (held.live()) latest.current(payload);},
      {onReopen: () => {if (held.live()) reconnected.current?.();}}));
    return held.close;
  }, [ctx, name]);
}

/** A failure that only says the panel's module scope is gone (the panel closed or switched, as Extract does): nothing is left to tell. Others are thrown on. */
export function unlessUnloaded(error: unknown): void {
  if ((error as {code?: string} | null)?.code !== "provider_unloaded") throw error;
}

/** The subscriptions a mounted page holds. `close` ends them; one that arrives after it is closed at once; subscribing or closing while the panel
 *  unloads fails with provider_unloaded, which is not an error (it was an unhandled rejection when Extract switched the panel). */
export function holdSubscriptions() {
  let live = true;
  const subscriptions: {close(): Promise<void>}[] = [];
  const end = (subscription: {close(): Promise<void>}) => void subscription.close().catch(unlessUnloaded);
  return {
    live: () => live,
    keep(pending: Promise<{close(): Promise<void>}>) {
      void pending.then(subscription => {if (live) subscriptions.push(subscription); else end(subscription);}).catch(unlessUnloaded);
    },
    close() {live = false; subscriptions.splice(0).forEach(end);},
  };
}

/** How often a page reads again at most when events come in: a run reports several times a second, the page follows once a second. */
export const EVENT_READ_EVERY = 1000;

/** `fn` at most once per `every` ms: a call after a quiet spell runs at once, calls inside the window fold into one at its end, so the last change always shows. */
export function throttle(fn: () => void, every = EVENT_READ_EVERY): {(): void; cancel(): void} {
  let last = -Infinity;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const run = () => {timer = null; last = Date.now(); fn();};
  const call = () => {
    if (timer !== null) return;
    const wait = last + every - Date.now();
    if (wait <= 0) run();
    else timer = setTimeout(run, wait);
  };
  return Object.assign(call, {cancel: () => {if (timer !== null) clearTimeout(timer); timer = null;}});
}

/** `throttle` for a component: the latest `fn` is used, a pending call is dropped on unmount. */
export function useThrottled(fn: () => void, every = EVENT_READ_EVERY) {
  const latest = useRef(fn);
  latest.current = fn;
  const throttled = useRef<ReturnType<typeof throttle> | null>(null);
  throttled.current ??= throttle(() => latest.current(), every);
  useEffect(() => () => throttled.current?.cancel(), []);
  return throttled.current;
}

const moment =(value: number) => new Date(value).toISOString().slice(0, 16).replace("T", " ");
/** An error detail value in words: millisecond times as UTC dates, ranges as "from – to", lists one after another. */
export function detailText(value: unknown): string {
  if (typeof value === "number") return value > 1e11 ? moment(value) : plain(value);
  if (Array.isArray(value)) return value.map(detailText).join(", ");
  if (value !== null && typeof value === "object") {
    const record = value as Record<string, unknown>;
    for (const [low, high] of [["start", "end"], ["from", "to"]]) if (low in record && high in record) return `${detailText(record[low])} – ${detailText(record[high])}`;
    return Object.entries(record).map(([key, item]) => `${key} ${detailText(item)}`).join(", ");
  }
  return String(value);
}
