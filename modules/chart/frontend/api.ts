/* Chart's own backend: private routes (POST /m/chart/<path>) and its two streams, each a WebSocket on a private route
   (/streams/chart/<chartId>, /streams/workspace) whose last message is {end: null | error}. Errors arrive in the
   platform's one format. */
import { ContractError, failure, type ErrorValue, type ModuleContext, type Subscription } from "@talyx/sdk";
import type { ChartItem, Routes, WorkspaceItem } from "./wire";

export async function post<P extends keyof Routes>(ctx: ModuleContext, path: P, body: Routes[P][0]): Promise<Routes[P][1]> {
  const response = await ctx.routes.fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const value = await response.json() as Routes[P][1] & { error?: ErrorValue };
  if (response.ok) return value;
  if (value.error) throw new ContractError(value.error);
  throw failure("handler_error", `${path} answered ${response.status}`);
}

/** A stream of Chart's backend over its WebSocket; `onEnd` hears how it ended (an error, or none when closed here). */
function stream<T>(ctx: ModuleContext, path: string, onItem: (item: T) => void, onEnd: (error?: ErrorValue) => void): Promise<Subscription> {
  const socket = ctx.routes.websocket(path);
  socket.binaryType = "arraybuffer";
  const decoder = new TextDecoder();
  let ended = false;
  const end = (error?: ErrorValue) => { if (!ended) { ended = true; onEnd(error); } };
  socket.onmessage = event => {
    const value = JSON.parse(typeof event.data === "string" ? event.data : decoder.decode(event.data as ArrayBuffer)) as T & { end?: ErrorValue | null };
    if (value !== null && typeof value === "object" && "end" in value && Object.keys(value).length === 1) end(value.end ?? undefined);
    else onItem(value);
  };
  socket.onclose = event => {
    if (!ended && event.code !== 1000) end({ code: "connection_lost", message: "Chart's stream closed", execution: "unknown", effects: "none" } as ErrorValue);
  };
  return new Promise((resolve, reject) => {
    socket.onopen = () => resolve({ id: path, close: async () => { ended = true; socket.close(); } });
    socket.onerror = () => reject(failure("connection_lost", `Chart's stream ${path} could not open`, undefined, "not_started"));
  });
}

export function subscribeChart(ctx: ModuleContext, chartId: string, onItem: (item: ChartItem) => void, onEnd: (error?: ErrorValue) => void): Promise<Subscription> {
  return stream(ctx, `/streams/chart/${encodeURIComponent(chartId)}`, onItem, onEnd);
}

export function subscribeWorkspace(ctx: ModuleContext, onItem: (item: WorkspaceItem) => void, onEnd: (error?: ErrorValue) => void): Promise<Subscription> {
  return stream(ctx, "/streams/workspace", onItem, onEnd);
}
