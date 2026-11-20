import { ContractError, failure, type ModuleContext } from "@talyx/sdk";

/* The published result index (`result-index.json`) that the report components read, and the route helpers. */
export interface Artifact { name: string; title: string; kind: string; frequency?: string; rows?: number; columns?: string[]; segment?: string; view?: string; distributionView?: string; summaryView?: string }
export interface Section { id: string; title: string; artifacts: Artifact[]; executor?: {class?: string; kwargs?: Record<string, unknown>}; combination?: {class?: string; module_path?: string}; labelSource?: {candidate?: string; recorderId?: string} }
export interface ResearchResult { evaluationConditions?: Record<string, unknown>; status: string; sections: Section[]; artifacts: Artifact[]; results?: {resultId: string; unitId: string; artifactName: string; digest: string; nativeKind: string; availableOperations: string[]}[]; units: { id: string; title: string; state: string; stage?: string; error?: string; recorderId?: string; experimentId?: string; uri?: string; nativeKind?: string; segments?: Record<string, string[]>; actualRanges?: Record<string, string[]>; metrics?: Record<string, number | null>; reportErrors?: { title: string; message: string }[] }[]; progress: { completed: number; total: number | null }; error?: string; rolling?: {stage: string; activeWindow?: string | null}; failedAt?: {unitId?: string; stage: string; error: string}; advanced?: {kind: string; stage: string; failedAt?: string; history?: {time: string; operation: string; state: string; models?: string[]; signalRows?: number; message?: string}[]}; metrics?: Record<string, number | null>; publications?: {name: string; kind: string; field?: string; path?: string; rows?: number}[];
  /** A historical index run: the members and periods left out because the free source has no price for them. */
  dataLimitations?: {statement: string; exclusions: {assetId: string; symbol?: string; instrumentId: string | null; since: number; until: number; kind: string; reason: string}[]} }
/** A finished result as the reports read it: a run's, or a library version's frozen copy; `base` is where its table and file routes are. */
export interface Run { id: string; base: string; name: string; state: string; createdAt: number; updatedAt: number; message?: string; result?: ResearchResult }
export interface PanelState { section?: string; resultOnly?: boolean; resultPages?: Record<string, number>; modelTab?: string; modelView?: string; modelSegment?: string; modelFeature?: string; [key: string]: unknown }
export interface WorkbenchProps { ctx: ModuleContext; run: Run | null; compare: Run | null; state: PanelState; onState(state: Partial<PanelState>): void }
/** What the result reports read: one result, an optional comparison and their own view state. */
export type ReportProps = WorkbenchProps;
type RouteMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
/** Encodes each interpolated value as one path segment: at`/object-runs/${runId}/table`. */
export const at = (parts: TemplateStringsArray, ...values: string[]) => String.raw({raw: parts}, ...values.map(encodeURIComponent));
/** Quant's own backend routes (backend/object_routes.py, backend/library_routes.py). GET sends `input` as the query; other methods send it as JSON. */
export async function route<T>(ctx: ModuleContext, method: RouteMethod, path: string, input?: object): Promise<T> {
  const query = method === "GET" && input
    ? new URLSearchParams(Object.entries(input).filter(([, value]) => value !== undefined).map(([key, value]) => [key, String(value)])).toString() : "";
  const response = await ctx.routes.fetch(query ? `${path}?${query}` : path, method === "GET" || input === undefined
    ? {method} : {method, headers: {"content-type": "application/json"}, body: JSON.stringify(input)});
  if (!response.ok && !response.headers.get("content-type")?.includes("json")) {
    const reason = await response.text();
    throw failure("handler_error", reason.trim() || response.statusText);
  }
  const value = await response.json();
  if (response.ok) return value as T;
  if (value?.error) throw new ContractError(value.error);
  // FastAPI reports rejected input as a list of {loc, msg}.
  const detail = Array.isArray(value?.detail) ? value.detail.map((item: {loc: (string | number)[]; msg: string}) => `${item.loc.slice(1).join("/")}: ${item.msg}`).join("; ") : value?.detail;
  throw failure(response.status === 422 ? "invalid_input" : "handler_error", `${method} ${path}: ${detail ?? response.status}`);
}
