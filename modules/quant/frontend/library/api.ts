/* The library's private routes (backend/library_routes.py) and the shapes they return, as the library page reads them. */
import type { ModuleContext } from "@talyx/sdk";
import { at, route } from "../types";
import type { PlotFigure } from "../chart/Plot";

export type Result = "ok" | "late" | "fail" | "miss" | "up";
export interface Last20 { period: number; result: Result; readyAt: number | null; dueAt: number | null; why: string | null }
export interface VersionRow {
  versionId: string; version: number; state: "producing" | "stopped" | "archived" | "fixed"; health: string; count: number; auto: boolean; users: number;
  publishedAt: number; stoppedAt: number | null; archivedAt: number | null;
}
/** One row of the library list: an item as its summary reads (library_summary.item_summary). */
export interface LibraryRow {
  itemId: string; name: string; kind: "factor" | "model" | "allocation" | "data"; createdAt: number; updatedAt: number;
  state: string; stateLabel: string; stateCount: number; attention: boolean; versions: VersionRow[]; producing: number[]; users: number; usersList: {name: string; kind: string; version: number}[];
  shownVersion: number | null; shownVersionId: string | null; freq: string; measure: string | null; universe: string | null; auto: boolean; latestPeriod: number | null; latestNames: number | null; last20: Last20[];
  published: number | null; after: number | null; collecting: boolean; collectingNeed: number; settled: number; settledDays: number; pending: number;
  origin: {objectId: string | null; runId: string | null; objectName: string | null; objectKind: string; runNumber?: number | null} | null; oneOff: boolean;
  inputs: {name: string; version: number; versionId: string; state: string}[]; nextDue: number | null;
}
export interface Production {
  lastRound: {at: number; took: number; versions: number; order: string[]} | null; concurrency: number; running: unknown[]; queued: unknown[];
  retries: {name: string; version: number; at: number; tries: number | null}[]; waiting: unknown[]; next: {at: number; versions: number} | null; order: string[]; producing: number;
}
export interface LibraryList { items: LibraryRow[]; needsAttention: number; archivable: {itemId: string; versionId: string; name: string; version: number}[]; production: Production | null; nextRound: {at: number; versions: number} | null }

export interface Published { metric?: string; value: number | null; sharpe?: number | null; maxDrawdown?: number | null; t?: number | null; se?: number | null; ls?: number | null; turnover?: number | null; names?: number | null; heldOut?: number | null; holding?: number | null; heldOutLs?: number | null }
export interface After { kind?: string; value: number | null; se?: number | null; t?: number | null; ls?: number | null; names?: number | null; settled: number; settledDays?: number; pending: number; published?: number | null; multiple?: number | null; drift?: boolean;
  worstShare?: {name: string; published: number | null; now: number | null; delta: number}; sharpe?: number | null; turnover?: number | null; rebalances?: number;
  benchmark?: {name: string; published: number | null; since: number} }
export interface UserRow { ownerId: string; name: string; kind: string; how: string; objectId: string | null; libraryVersionId: string | null; version: number | null; readsDaily: boolean; unattended: boolean }
export interface ItemVersion {
  versionId: string; version: number; state: VersionRow["state"]; health: string; count: number; auto: boolean; publishedAt: number; stoppedAt: number | null; archivedAt: number | null;
  runId: string | null; objectId: string | null; origin: LibraryRow["origin"]; settingsText: string; measure: string | null; pub: Published | null; after: After | null; collecting: boolean;
  settled: number; pending: number; users: UserRow[]; cursor: number | null; lastDatetime: number | null; failure: {message: string; code: string | null} | null; inputs: LibraryRow["inputs"]; rows: number; oneOff: boolean;
}
/** A failure's code when the version was published before a change it cannot be produced under: a retry fails the same way, publishing again mends it. */
export const REPUBLISH = "republish";
export interface Period { version_id: string; label: number; due_at: number | null; result: Result; ready_at: number | null; names: number | null; distinct_values: number | null; tries: number; took: number | null; why: string | null }
export interface Issue { kind: string; title: string; since: number; note?: string | null; rows: {label: string; text: string}[]; links: {kind: string; file?: string; line?: number; versionId?: string; itemId?: string; name?: string}[] }
export type Figure = PlotFigure | {missing: string};
export interface ItemDetail {
  summary: LibraryRow; versions: ItemVersion[]; versionId: string; issue: Issue | null; overview: unknown; figures?: {cumulative?: Figure; volatility?: Figure; riskShares?: Figure}; periods: Period[];
  feeds: {itemId: string; name: string; kind: string; versionId: string; version: number; reads: string; state: string; users: UserRow[]}[];
  inputs: LibraryRow["inputs"]; nextDue: {at: number; why: string} | null; failures: Failure[]; files: string[]; users: UserRow[]; oneOff: boolean; origin: LibraryRow["origin"];
}
export interface Failure { at: number; label: number | null; message: string; location: {file: string; line: number} | null; traceback: string | null; output: string | null }
export interface VersionFull {
  versionId: string; itemId: string; name: string; kind: string; version: number; state: string; fields: string[]; timeframe: string | null; published: Published | null; after: After | null;
  settings: Record<string, unknown> | null; settingsText: string | null; carriesValues: boolean; trainingCutoff: number | null; heldOutFrom: string | null; runId: string | null; origin: LibraryRow["origin"];
  formula?: string | null; publication: {kind: string; name: string; field?: string} | null; calendar: {annualization: number} | null; measure: string | null;
}
export interface ValuesDay {
  versionId: string; date: number; previousDay: number | null; nextDay: number | null; firstDay: number; lastDay: number; days: number; readyAt: number | null; dueAt: number | null; result: Result | null; onTime: boolean | null;
  have: number; total: number; missing: number; rows: {rank: number; instrumentId: string; name: string; sector: string | null; value: number; previous: number | null}[]; matching: number; page: number; pages: number; field: string;
  missingRows?: {name: string; sector: string | null; why: string}[];
  /** An allocation: its weights on the day the publication holds and on this day. */
  weights?: {published: {instrumentId: string; name: string; weight: number}[]; thisPeriod: {instrumentId: string; name: string; weight: number}[]; publishedDate: number};
}
export interface ValuesArgs { date?: number; order?: "top" | "bottom" | "missing"; q?: string; page?: number; field?: string }
export interface LogView { periods: Period[]; failures: Failure[]; updates: {at: number; outcome: string; values: number; firstDatetime: number | null; lastDatetime: number | null; mode: string | null; message: string | null}[] }
export interface CompareSide { versionId: string; version: number; runId: string | null; origin: LibraryRow["origin"]; published: Published | null; after: After | null; measure: string | null; state: string; archivedAt: number | null; settingsText: string }
export interface Compare {
  a: CompareSide; b: CompareSide; overlap: {from: number; to: number; days: number; rankCorrelation: number | null; namesA: number; namesB: number; onlyA: number; onlyB: number} | null;
  code: {file: string; status: string; diff: string}[]; settings: {what: string; from: unknown; to: unknown}[];
}
export interface StopPreview {
  versionId: string; version: number; users: UserRow[]; producing: number[]; lastDatetime: number | null; consequence: string; stopped?: boolean;
}

export const libraryApi = (ctx: ModuleContext) => ({
  list: (args: {query?: string; kind?: string; state?: string} = {}) => route<LibraryList>(ctx, "GET", "/library", args),
  item: (itemId: string, args: {versionId?: string; metric?: "ic" | "ls"; span?: "since" | "all"} = {}) => route<ItemDetail>(ctx, "GET", at`/library/items/${itemId}`,
    {...args, metric: args.metric === "ls" ? "ls" : undefined, span: args.span === "all" ? "all" : undefined}),
  version: (versionId: string) => route<VersionFull>(ctx, "GET", at`/library/versions/${versionId}`),
  values: (versionId: string, args: ValuesArgs) => route<ValuesDay>(ctx, "GET", at`/library/versions/${versionId}/values`, args),
  log: (versionId: string) => route<LogView>(ctx, "GET", at`/library/versions/${versionId}/log`),
  compare: (a: string, b: string) => route<Compare>(ctx, "GET", at`/library/versions/${a}/compare/${b}`),
  source: (versionId: string, name?: string) => route<{files?: string[]; name?: string; content?: string; readOnly?: boolean}>(ctx, "GET", at`/library/versions/${versionId}/source`, name ? {name} : {}),
  stop: (versionId: string, dryRun: boolean) => route<StopPreview>(ctx, "POST", at`/library/versions/${versionId}/stop`, {dryRun}),
  resume: (versionId: string) => route<unknown>(ctx, "POST", at`/library/versions/${versionId}/resume`),
  archive: (versionId: string) => route<unknown>(ctx, "POST", at`/library/versions/${versionId}/archive`),
  retry: (versionId: string) => route<unknown>(ctx, "POST", at`/library/versions/${versionId}/retry`),
  cancel: (versionId: string) => route<unknown>(ctx, "POST", at`/library/versions/${versionId}/cancel`),
  setAuto: (versionId: string, on: boolean) => route<unknown>(ctx, "PUT", at`/library/versions/${versionId}/auto`, {on}),
  openInNotebook: (name: string, version: number, kind: string) => route<{notebookId: string}>(ctx, "POST", "/library/notebook", {name, version, kind}),
  createFrom: (versionId: string, kind: "model" | "strategy" | "allocation") => route<{id: string; name: string}>(ctx, "POST", "/objects", {kind, fromLibrary: versionId}),
});
export type LibraryApi = ReturnType<typeof libraryApi>;
