/* The Market panel's data: the local library, fetch jobs and sources from Market's backend, in the shapes the panel draws.
   Everything here comes from the private route `market.panel` (same numbers as read_data view=local) and the public
   actions fetch, export, write_data, read_source and write_source. */

import { t } from "../i18n";
import { failure, Platform, type Json, type ModuleContext, type TaskRef } from "@talyx/sdk";
import { createClient, type ContractTypes } from "../../public/ts/types";

export type SeriesStatus = "current" | "behind" | "updating" | "failed" | "delisted" | "ended";

export interface Gap { from: string; to: string; missing: string; reason: string; fillable: boolean }
export interface Action { date: string; event: string; factor: string }
export interface Identity { from: string; to: string; code: string; name: string }

export interface Series {
  id: string;
  group: string;
  code: string;
  name: string;
  kind: string;
  from: string;
  to: string;
  rows: number;
  /** Stretches between the first and last stored row that were never fetched (detail lists them). */
  gapCount: number;
  gaps: Gap[];
  source: string;
  bytes: number;
  status: SeriesStatus;
  statusText?: string;
  identity?: Identity[];
  instrumentId: string;
  /** The stored kind as the library names it: a capability, or import:<id>. */
  storedKind: string;
  capability: string;
  timeframe: string | null;
  importId: string | null;
  market: string;
}

export interface Group { id: string; label: string }
export interface Task { id: string; title: string; done: number; total: number; rows?: number; bytes?: number; state: "running" | "waiting" | "failed" | "interrupted"; text: string; detail: string }

const MARKETS: Record<string, string> = { US: t("US stocks"), CN: t("China A shares"), HK: t("Hong Kong") };
const VENUES: Record<string, string> = {
  binance: "Binance", bitfinex: "Bitfinex", bitget: "Bitget", bybit: "Bybit", coinbase: "Coinbase", cryptocom: "Crypto.com",
  deribit: "Deribit", gate: "Gate", htx: "HTX", hyperliquid: "Hyperliquid", kraken: "Kraken", kucoin: "KuCoin", mexc: "MEXC", okx: "OKX",
};
const ORDER = ["us", "cn", "hk", ...Object.keys(VENUES), "imported"];

export function groupOf(venue: string, importId: string | null): Group {
  if (importId) return { id: "imported", label: t("Imported") };
  const id = MARKETS[venue] ? venue.toLowerCase() : venue;
  return { id, label: MARKETS[venue] ?? VENUES[venue] ?? venue };
}

const CAPABILITIES: Record<string, string> = { funding_rate: t("Funding"), open_interest: t("Open interest"), long_short_account_ratio: t("Long/short ratio"),
  taker_volume: t("Taker volume"), option_metrics: t("Option metrics") };

export function kindLabel(capability: string, timeframe: string | null): string {
  if (capability !== "ohlcv") return CAPABILITIES[capability] ?? capability;
  if (timeframe === "1d") return t("Daily");
  const match = /^(\d+)([mhdw])$/.exec(timeframe ?? "");
  if (!match) return timeframe ?? t("Bars");
  const count = match[1]!;
  switch (match[2]) {
    case "m": return count === "1" ? t("1 minute") : t("{count} minutes", { count });
    case "h": return count === "1" ? t("1 hour") : t("{count} hours", { count });
    case "d": return count === "1" ? t("1 day") : t("{count} days", { count });
    default: return count === "1" ? t("1 week") : t("{count} weeks", { count });
  }
}

/* ── backend rows → panel rows ───────────────────────────────────────────── */

export interface LibraryRow {
  instrumentId: string; symbol: string; venue: string; kind: string; timeframe: string | null; rows: number; first: string; last: string;
  gaps: number; bytes: number; source: string | null; status: "current" | "behind" | "delisted"; name: string | null;
  delistedAt: string | null; importId: string | null; former: { code: string; until: string | null }[] | null;
}
export interface ImportRecord { importId: string; name: string; createdAt: string; rows: number }
/** The panel's library answer: series as columns and rows (no key repeated per row; the bridge carries 2 MB a frame). */
export interface Library { series: { columns: (keyof LibraryRow)[]; rows: unknown[][] }; imports: ImportRecord[]; now: string }

export function libraryRows(library: Library): LibraryRow[] {
  const { columns, rows } = library.series;
  return rows.map(values => Object.fromEntries(columns.map((name, index) => [name, values[index]])) as unknown as LibraryRow);
}

const day = (text: string | null | undefined) => (text ?? "").slice(0, 10);
export const dateOfNs = (ns: string | null | undefined) => ns ? new Date(Number(BigInt(ns) / 1_000_000n)).toISOString().slice(0, 10) : "";

export function seriesOf(library: Library, sourceNames: Record<string, string>, updating: Set<string>): Series[] {
  const imports = new Map(library.imports.map(item => [item.importId, item]));
  return libraryRows(library).map(row => {
    const capability = row.importId ? "ohlcv" : row.kind;
    const group = groupOf(row.venue, row.importId);
    const id = `${row.instrumentId}:${row.kind}:${row.timeframe ?? ""}`;
    const status: SeriesStatus = updating.has(row.instrumentId) && row.status !== "delisted" ? "updating" : row.status;
    return {
      id, group: group.id, code: row.symbol, name: row.name ?? "", kind: kindLabel(capability, row.timeframe), from: day(row.first), to: day(row.last),
      rows: row.rows, gapCount: row.gaps, gaps: [], bytes: row.bytes, status,
      source: row.importId ? imports.get(row.importId)?.name ?? row.importId : sourceNames[row.source ?? ""] ?? row.source ?? "",
      statusText: row.status === "delisted" && row.delistedAt ? t("Last traded {date}", { date: dateOfNs(row.delistedAt) }) : row.status === "behind" ? t("Newer rows are at the source") : undefined,
      identity: row.former ? row.former.map(item => ({ from: "", to: dateOfNs(item.until), code: item.code, name: "" })) : undefined,
      instrumentId: row.instrumentId, storedKind: row.kind, capability, timeframe: row.timeframe, importId: row.importId, market: row.venue,
    };
  });
}

export interface JobRow {
  jobId: string; state: string; createdAt: string; request: { targets: Json[]; targetCount: number; kinds: { kind: string; timeframe?: string }[]; start?: string | null };
  done: number; total: number; rows: number; bytes: number; failed: number; waitingFor: string | null; waitingUntil: string | null; interruption: string | null;
}

const targetText = (target: Json) => {
  const value = target as Record<string, unknown>;
  if (typeof value.symbol === "string") return value.symbol;
  if (value.index) return t("{index} members", { index: typeof value.index === "string" ? value.index : String((value.index as { symbol?: string }).symbol) });
  return (value.instrument as { symbol?: string } | undefined)?.symbol ?? "?";
};

export function tasksOf(jobs: JobRow[]): Task[] {
  return jobs.filter(job => ["running", "waiting", "queued", "interrupted", "failed"].includes(job.state)).map(job => {
    // The job list names the first target and counts the rest.
    const first = job.request.targets[0] === undefined ? "" : targetText(job.request.targets[0]), count = job.request.targetCount;
    const names = count > 1 ? `${first} +${count - 1}` : first;
    const kinds = job.request.kinds.map(kind => kind.kind === "bars" ? kindLabel("ohlcv", kind.timeframe ?? "1d") : kind.kind).join(", ");
    const state: Task["state"] = job.state === "waiting" ? "waiting" : job.state === "interrupted" ? "interrupted" : job.state === "failed" ? "failed" : "running";
    const text = state === "waiting" ? job.waitingFor ? t("Waiting for {source}", { source: job.waitingFor }) : t("Waiting for the source") : state === "interrupted"
      ? (job.interruption === "workspace_closed" ? t("Paused when the workspace closed") : t("Paused")) : state === "failed" ? t("{failed} failed", { failed: job.failed }) : t("Fetching");
    return { id: job.jobId, title: `${names} · ${kinds}${job.request.start ? t(" · from {date}", { date: day(job.request.start) }) : ""}`, done: job.done, total: Math.max(1, job.total),
      rows: job.rows, bytes: job.bytes, state, text, detail: (job.failed ? t("{done} of {total} done, {failed} failed", { done: job.done, total: job.total, failed: job.failed }) : t("{done} of {total} done", { done: job.done, total: job.total })) };
  });
}

/* ── datasets: one market (or venue, or import) × one kind of data ───────── */

const KIND_ORDER = [t("Daily"), t("1 hour"), t("1 minute"), t("Funding"), t("Open interest")];

export interface Dataset {
  id: string; group: string; market: string; kind: string; series: Series[]; from: string; to: string; bytes: number; rows: number; gaps: number;
  sources: string[]; count: Record<SeriesStatus, number>;
}

export function datasetKey(series: Series): string {
  return series.group === "imported" ? `imported:${series.importId}` : `${series.group}:${series.kind}`;
}

export function datasetsFor(all: Series[]): Dataset[] {
  const map = new Map<string, Dataset>();
  for (const series of all) {
    const id = datasetKey(series);
    let set = map.get(id);
    if (!set) {
      const market = series.group === "imported" ? t("Imported") : groupOf(series.market, null).label;
      const kind = series.group === "imported" ? series.source : series.kind;
      set = { id, group: series.group, market, kind, series: [], from: series.from, to: series.to, bytes: 0, rows: 0, gaps: 0, sources: [],
        count: { current: 0, behind: 0, updating: 0, failed: 0, delisted: 0, ended: 0 } };
      map.set(id, set);
    }
    set.series.push(series);
    if (series.from < set.from) set.from = series.from;
    if (series.to > set.to) set.to = series.to;
    set.bytes += series.bytes;
    set.rows += series.rows;
    set.gaps += series.gapCount;
    if (!set.sources.includes(series.source)) set.sources.push(series.source);
    set.count[series.status] += 1;
  }
  const rank = (set: Dataset) => {
    const group = ORDER.indexOf(set.group);
    return (group < 0 ? ORDER.length : group) * 100 + Math.max(0, KIND_ORDER.indexOf(set.kind));
  };
  return [...map.values()].sort((a, b) => rank(a) - rank(b));
}

/** What Market says about a dataset: stored raw, adjusted when read. */
export function datasetFacts(set: Dataset): { adjustment?: string; bias?: string } {
  if (["us", "cn", "hk"].includes(set.group) && set.kind === t("Daily")) return { adjustment: t("Unadjusted, applied on read") };
  if (set.group === "imported") return { adjustment: t("As imported") };
  return {};
}

/* ── sources ─────────────────────────────────────────────────────────────── */

export type SourceStatus = "ok" | "cooling" | "unreachable" | "needs-key" | "key-rejected" | "app-off" | "ended";

export interface Source {
  id: string; name: string; status: SourceStatus; statusText: string; key: "none" | "optional" | "required" | "local";
  keySet?: boolean; enabled: boolean; paid: boolean; coverage: CoverageRow[]; settings: { schema: SettingSpec[]; values: Record<string, Json> };
  lastTest: SourceTest | null;
}
export interface CoverageRow { scope: { markets?: string[]; venues?: string[]; assetClasses?: string[]; marketTypes?: string[] }; kind: string;
  periods?: { timeframe: string; depth: string }[]; live: string; historyRole?: string; delisted?: boolean }
export interface SettingSpec { name: string; label: string; type: string; secret: boolean; required: boolean; choices?: string[] }

export interface SourceRow {
  source: { sourceId: string; name: string }; state: string; problem: { code: string; message: string; status?: number | null } | null;
  keyConfigured: boolean | null; paid: boolean; enabled: boolean; credentials: "none" | "optional" | "required"; localGateway: boolean;
  coverage: CoverageRow[]; settings: Source["settings"];
  /** The last test, as write_source answered it. */
  lastTest: { tests: SourceTest[]; changed: boolean } | null;
}
export interface SelectionRow { market: string; kind: string; timeframes?: string; source: { sourceId: string; name: string } | null; reason: string }
export interface SourcesAnswer { sources: SourceRow[]; selection: SelectionRow[] }

export function sourceOf(row: SourceRow): Source {
  const local = row.localGateway;
  const key: Source["key"] = local ? "local" : row.credentials;
  const status: SourceStatus = row.state === "working" || row.state === "off" ? "ok" : row.state === "rate_limited" ? "cooling"
    : row.state === "not_configured" ? "needs-key" : row.state === "unsubscribed" ? "ended" : row.problem?.code === "unauthorized" ? "key-rejected"
    : local ? "app-off" : "unreachable";
  const statusText = status === "ok" ? t("Connected") : status === "needs-key" ? t("Needs key") : status === "ended" ? t("Plan does not cover this")
    : row.problem?.message ?? (status === "app-off" ? t("Program not running") : t("Not reachable"));
  return { id: row.source.sourceId, name: t(row.source.name), status, statusText, key, keySet: row.keyConfigured ?? undefined, enabled: row.enabled,
    paid: row.paid, coverage: row.coverage, settings: row.settings, lastTest: row.lastTest?.tests[0] ?? null };
}

/** What a test answers: the round trip in milliseconds, or why it did not get through. */
export interface TestResult { ms?: number; fail?: string; detail?: string; tone?: "watch" | "off" }

export interface SourceTest { source: string; latencyMs: number | null; unreachable: string | null; httpStatus: number | null; passed: number; failed: { check: string; detail: string }[] }

export function testResultOf(test: SourceTest): TestResult {
  if (test.latencyMs !== null && !test.unreachable) {
    return test.failed.length ? { ms: test.latencyMs, fail: t("{count} checks failed", { count: test.failed.length }), detail: test.failed.map(item => `${item.check}: ${item.detail}`).join("\n") } : { ms: test.latencyMs };
  }
  switch (test.unreachable) {
    case "timeout": return { fail: t("Timeout"), detail: t("No answer within 10 s") };
    case "http_error": return { fail: `HTTP ${test.httpStatus}`, detail: test.httpStatus === 451 || test.httpStatus === 403 ? t("Blocked from your region; change your proxy node") : undefined };
    case "key_missing": return { fail: t("Needs key"), tone: "off" };
    case "key_rejected": return { fail: t("Key rejected") };
    case "local_program_off": return { fail: t("Program not running"), tone: "off" };
    case "rate_limited": return { fail: t("Rate limited"), tone: "watch" };
    case "network_unreachable": return { fail: t("No network") };
    default: return { fail: t("Failed"), detail: test.failed.map(item => item.detail).join("\n") || undefined };
  }
}

/* ── calls ───────────────────────────────────────────────────────────────── */

/** The panel's own requests go to Market's private route, not to its public actions. */
export async function panelCall<T>(ctx: ModuleContext, op: string, value: Record<string, unknown> = {}): Promise<T> {
  const response = await ctx.routes.fetch("panel", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ op, ...value }) });
  const answer = await response.json();
  if (!response.ok) throw failure(answer.code ?? "handler_error", answer.message ?? answer.detail ?? t("Market answered {status}", { status: response.status }), undefined, answer.execution ?? "failed", answer.effects ?? "none");
  return answer as T;
}

type TaskAction = "fetch" | "export" | "write_data" | "write_source";
export type FetchInput = ContractTypes["fetch"]["input"];
export type ExportInput = ContractTypes["export"]["input"];
export type FetchTarget = { index: string } | { symbol: string };
export type FetchKind = NonNullable<FetchInput["data"]>[number];
export type DeleteKinds = NonNullable<Extract<ContractTypes["write_data"]["input"]["operation"], { op: "delete" }>["kinds"]>;
export type MarketInstrument = ContractTypes["instruments.search"]["output"]["results"][number]["instrument"];
export type SeriesRef = NonNullable<ExportInput["series"]>[number]["series"];

/** A list as the contract bounds it (minItems, maxItems): TypeScript sees an array, the generated type a tuple, so the
 *  bounds are checked here. */
export function listOf<L extends readonly unknown[]>(items: L[number][], min: number, max: number): L {
  if (items.length < min || items.length > max) throw new Error(items.length < min ? t("Nothing is chosen") : t("At most {max} at a time", { max }));
  return items as unknown as L;
}
/** The instrument records and series ids of chosen rows, as fetch and export take them. */
export async function selectionsOf(ctx: ModuleContext, rows: Series[]): Promise<{ instrument: MarketInstrument; series: SeriesRef }[]> {
  const answer = await panelCall<{ selections: { instrument: MarketInstrument; series: SeriesRef }[] }>(ctx, "selections",
    { series: rows.map(row => ({ instrumentId: row.instrumentId, kind: row.storedKind, timeframe: row.timeframe, importId: row.importId })) });
  return answer.selections;
}

/** Market's public actions the panel calls run as platform tasks: the generated client answers the task's reference,
 *  and the panel waits for the task's own answer (platform.tasks.get), which is that action's output. */
export async function marketCall<K extends TaskAction>(ctx: ModuleContext, name: K, input: ContractTypes[K]["input"]): Promise<ContractTypes[K]["output"]> {
  const start = createClient(ctx)[name] as (value: ContractTypes[K]["input"]) => Promise<TaskRef>;
  const reference = await start(input);
  for (;;) {
    const task = await Platform.createClient(ctx)["tasks.get"]({ taskId: reference.taskId });
    if (task.state === "completed") return task.result as ContractTypes[K]["output"];
    if (task.state !== "running") throw task.error ?? { code: task.state, message: t("The {name} task was {state}", { name, state: task.state }) };
    await new Promise<void>(resolve => setTimeout(resolve, 250));
  }
}

export function errorText(error: unknown): string {
  if (error && typeof error === "object" && "message" in error) return String((error as { message: unknown }).message);
  return String(error);
}

/* ── formatting ──────────────────────────────────────────────────────────── */

export const today = () => new Date().toISOString().slice(0, 10);
export const spans = [{ value: "1y", label: t("1 year") }, { value: "5y", label: t("5 years") }, { value: "10y", label: t("10 years") }, { value: "max", label: t("All") }] as const;

export function formatCount(value: number): string {
  if (value >= 1e9) return `${(value / 1e9).toFixed(1)}B`;
  if (value >= 1e6) return `${(value / 1e6).toFixed(1)}M`;
  if (value >= 1e4) return `${Math.round(value / 1e3)}K`;
  return value.toLocaleString("en-US");
}

export function formatBytes(bytes: number): string {
  if (bytes >= 1e9) return t("{value} GB", { value: (bytes / 1e9).toFixed(2) });
  if (bytes >= 1e6) return t("{value} MB", { value: (bytes / 1e6).toFixed(1) });
  return t("{value} KB", { value: Math.max(1, Math.round(bytes / 1e3)) });
}
