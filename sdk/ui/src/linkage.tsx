import { createContext, useContext, useEffect, useRef } from "react";
import { createViewUnavailable } from "./ui/unavailable";
declare const __TALYX_SDK_VERSION__: string;

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type Schema = Record<string, unknown>;
export interface PanelRef { sessionId: string; panelId: string; mountId: string }
export interface ViewRef { sessionId: string; viewInstanceId: string; mountId: string; parentPanel?: PanelRef }
/** What a long-task action answers at once; platform.tasks.get reads its state and result. */
export interface TaskRef { taskId: string }
export interface PanelStatus { state: "idle" | "running" | "attention"; text: string; navigation: Json }
export interface ViewReport { summary: string; refs: Record<string, Json>; facts?: string[]; openTargets?: Array<{kind: string; key: string; label: string}>; generation?: string }
export interface BusinessView { panel: PanelRef; title: string; report: ViewReport | null }
export interface ErrorValue {
  code: string; message: string; requestId?: string; details?: Json;
  execution: "not_started" | "failed" | "unknown"; effects: "none" | "may_have_occurred";
}
export class ContractError extends Error {
  readonly code: string;
  constructor(readonly value: ErrorValue) { super(value.message); this.code = value.code; }
  toJSON(): ErrorValue { return this.value; }
}
/** What a person reads for a failure raised without its own words: the transport ones say what happened, not their code. */
const PLAIN: Record<string, string> = { connection_lost: "The connection to TALYX was lost. Try again once it reconnects." };
export function failure(code: string, message = PLAIN[code] ?? code, details?: Json,
  execution: ErrorValue["execution"] = "not_started", effects: ErrorValue["effects"] = "none"): ContractError {
  return new ContractError({ code, message, details, execution, effects });
}
/** `target`: the panel a panel action runs in. */
export interface CallOptions { target?: PanelRef; signal?: AbortSignal }
/** Who asked, as the Host stamped it where the call entered TALYX. */
export interface Caller { origin: "user" | "ai" | "module" | "code"; module: string | null; workspaceId?: string | null }
/** One of a panel's own actions (declared in the manifest under the panel), run in that panel. */
export type PanelActionHandler = (input: Json, call: { caller: Caller; signal: AbortSignal }) => Json | Promise<Json>;
export interface SignalValue<T> { get(): T; watch(callback: (value: T) => void): () => void }
export interface LeaveDecision { decision: "allow" | "block" | "confirm"; message?: string }
export interface Instance {
  captureState?(): Json; update?(props: Json): void; prepareLeave?(reason: string): LeaveDecision;
  /** Return this save attempt's confirmed JSON after preparing recovery material. */
  prepareSave?(): Promise<void | Json>;
  dispose(): void;
}
export type Mount = (root: HTMLElement, ctx: ModuleContext) => Instance;
export interface Subscription { close(): Promise<void>; readonly id: string }
/** `onReopen`: the provider restarted and the stream was opened again (from the last cursor, if it has one); what
    happened meanwhile without a cursor is to be read again. */
export interface StreamOptions { cursor?: Json; onEnd?: (error?: ErrorValue) => void; onReopen?: () => void }
export interface ModuleContext {
  readonly moduleId: string; readonly panelRef?: PanelRef; readonly viewRef?: ViewRef;
  /** A panel's saved state (null for a new one); a view's props. */
  readonly initialState: Json; readonly props: Json;
  readonly workspace: import("./workspaceContext").ModuleWorkspaceContext;
  readonly visible: SignalValue<boolean>; readonly active: SignalValue<boolean>;
  view: { report(value: ViewReport): void; current(): BusinessView | null; onFlush(callback: () => void | Promise<void>): () => void };
  /**
   * Notifications belong to this mounted panel; state remains in captureState(). A `derived` change moves only where the
   * user is looking (which item shows, the tab, the row picked): it is kept and saved, but never marks the workspace unsaved.
   */
  readonly panel?: { setTitle(title: string): void; stateChanged(change?: { derived?: boolean }): void };
  readonly overlayRoot: HTMLElement;
  reportFault(fault: { phase: "render"; message: string }): void;
  /** A view's declared event, to the module that mounted it. */
  emit(name: string, payload: Json): void;
  /** `call`: a public action of the platform, this module or a module it requires (use the generated createClient).
      `handle`: one of this panel's own actions. */
  actions: { handle(name: string, handler: PanelActionHandler): void; call<T extends Json = Json>(name: string, input: Json, options?: CallOptions): Promise<T> };
  streams: { subscribe(name: string, params: Json, onItem: (item: Json) => void | Promise<void>, options?: StreamOptions): Promise<Subscription> };
  events: { subscribe(name: string, callback: (payload: Json) => void, options?: Pick<StreamOptions, "onReopen">): Promise<Subscription> };
  views: { provide(name: string, mount: Mount, options?: { styles?: string }): void; mount(name: string, root: HTMLElement, props: Json, on?: Record<string, (payload: Json) => void>): Instance & { update(props: Json): void } };
  panels: { provide(name: string, mount: Mount): void };
  /** One of the module's status items (provided at platform.status_item). */
  status: { set(id: string, value: { level: "ok" | "busy" | "warn" | "error"; text?: string; tooltip?: string; visible?: boolean }): void };
  /** The module's own backend routes (not in its contract), for this window and workspace. */
  routes: { fetch(path: string, init?: RequestInit): Promise<Response>; websocket(path: string): WebSocket };
  /** The window this module is shown in: its panels, and the settings window. */
  platform: {
    panels: { open(input: { type: string; state?: Json }): Promise<PanelRef>; openTarget(target: { module: string; panelType: string; objectId: string }): Promise<PanelRef>; list(type?: string): PanelRef[]; active(type?: string): PanelRef | null; activate(ref: PanelRef): void; close(ref: PanelRef): Promise<void>; setStatus(ref: PanelRef, value: PanelStatus): void };
    settings: { open(section?: string): void };
  };
  onDispose(dispose: () => void | Promise<void>): void;
  /** Protect live input before the module is stopped (disabled, reloaded, uninstalled). Reject to keep it running. */
  onBeforeStop(protect: (reason: string) => void | Promise<void>): () => void;
  setInterval(callback: () => void, milliseconds: number): () => void;
}

/** A module's manifest (talyx.module.json, version 5), as modules.definitions gives it. */
export interface Manifest {
  manifestVersion: 5; id: string; name: string; version: string; description?: string;
  entries: { backend?: string; frontend?: string };
  requires?: Record<string, { optional?: boolean }>;
  panels?: Record<string, { title: string; icon?: string; launcher?: boolean; description?: string; visibility?: "public" | "private"; actions?: Record<string, Schema> }>;
  views?: Record<string, { description?: string; props?: Schema; emits?: Record<string, Schema> }>;
  /** What the module puts at others' points (owner.point): implementations, and places in the window. */
  provides?: Record<string, Array<{ id: string; descriptor: { [key: string]: Json } }>>;
}
/** A module's contract (public/contract.json, generated from its code). */
export interface Contract {
  contractVersion: string; module: string;
  actions: Record<string, { description: string; effects: string[]; task: boolean; input: Schema; output: Schema }>;
  streams: Record<string, { description: string; params: Schema; item: Schema; cursor?: boolean; trigger?: boolean }>;
  events: Record<string, { description: string; payload: Schema }>;
  /** A point with neither actions nor streams is a place in the window: what others provide there is a descriptor. */
  extensionPoints: Record<string, { description: string; descriptor?: Schema; actions?: Record<string, Schema>; streams?: Record<string, Schema> }>;
  settings: Record<string, { title: string; description?: string; globalOnly: boolean; group?: string; schema: Schema; default: Json }>;
  $defs: Record<string, Schema>;
}
/** One installed module (or the platform), as platform.modules.definitions lists it. */
export interface ModuleDefinition {
  id: string; name: string; version: string; description: string; source: "official" | "installed";
  enabled: boolean; state: "starting" | "ready" | "failed" | "stopped"; problem: string | null; pid: number | null;
  manifest: Manifest; contract: Contract; digest: string; assetBase: string;
}
/** A thing a module provides at a place in the window (a settings section, a status item): its manifest entry as
    written, with the module that provides it. `D` is the point's descriptor (`point:<name>` in the owner's generated
    types). */
export interface Contribution<D = unknown> { moduleId: string; id: string; descriptor: D }
export interface TypedOperation<I, O> { input: I; output: O }
export type TypedClient<T, Tasks extends keyof T = never> = { [K in keyof T]: T[K] extends TypedOperation<infer I, infer O> ? (input: I, options?: CallOptions) => Promise<K extends Tasks ? TaskRef : O> : never };
export function client<T, Tasks extends keyof T = never>(ctx: { actions: Pick<ModuleContext["actions"], "call"> }, prefix: string): TypedClient<T, Tasks> {
  return new Proxy({}, { get: (_target, name: string) => (input: Json, options?: CallOptions) => ctx.actions.call(`${prefix}.${name}`, input, options) }) as TypedClient<T, Tasks>;
}
export const ModuleContextProvider = createContext<ModuleContext | null>(null);
export function ModuleView({ id, props, on, context }: { id: string; props: Json; on?: Record<string, (payload: Json) => void>; context?: ModuleContext }) {
  const inherited = useContext(ModuleContextProvider);
  const ctx = context ?? inherited;
  const root = useRef<HTMLDivElement>(null);
  const instance = useRef<Instance | null>(null);
  const shadowFault = useRef<HTMLElement | null>(null);
  const showFault = () => {
    if (root.current && !shadowFault.current) {
      const target = root.current.shadowRoot ?? root.current;
      const marker = createViewUnavailable(target);
      target.append(marker); shadowFault.current = marker;
    }
  };
  useEffect(() => {
    try {
      if (!ctx || !root.current) throw new Error("ModuleView needs a module context");
      instance.current = ctx.views.mount(id, root.current, props, on);
    } catch { showFault(); }
    return () => { shadowFault.current?.remove(); shadowFault.current = null; instance.current?.dispose(); instance.current = null; };
  }, [ctx, id, on]);
  useEffect(() => {
    try { instance.current?.update?.(props); }
    catch { instance.current?.dispose(); instance.current = null; showFault(); }
  }, [props]);
  return <div ref={root} data-module-view={id} />;
}
export const sdkVersion = __TALYX_SDK_VERSION__;
export { Scope, signalValue } from "./scope";
/** The platform's own actions (`Platform.createClient(ctx)`) and their types, generated from its declarations. */
export * as Platform from "./platform";
/* The design system travels with the SDK so modules share the host's React. */
export * from "./ui/index";

export * from "./workspaceContext";
/** Generic JSON and render fault values shared with the shell. */
export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export interface ModuleRenderFault { phase: "render"; message: string }
