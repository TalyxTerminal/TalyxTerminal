/* The window's connection to the Host. A frame is one binary WebSocket message: an envelope (JSON) line, then the
   body (JSON). The Host reads only envelopes and passes bodies on as they are; the module that receives a call
   checks its input, and the module that answers checked its output. When the Host restarts (a new instance id on
   reconnect) the page reloads: there is one identity per window session, never a handover. */
import { ContractError, failure, type Caller, type ErrorValue, type Json, type Subscription } from "@talyx/sdk";
import type { FrontendKernel, Remote } from "./registry";

interface Pending { resolve(value: Json): void; reject(error: ContractError): void; timer: ReturnType<typeof setTimeout>; removeAbort?(): void }
/** One stream the window reads. ``id`` changes when it is opened again after its provider restarted. */
interface Live {
  id: string; capability: string; params: Json; module?: string; cursor?: Json;
  callback: (item: Json) => void | Promise<void>; onEnd?: (error?: ErrorValue) => void; onReopen?: () => void; tail: Promise<void>; closed: boolean;
}
type Envelope = Record<string, unknown> & { t: string };

const encoder = new TextEncoder(), decoder = new TextDecoder();
const MAX_PENDING = 512;
/** A call the Host has not answered in this long is given up (and cancelled there). Long work is a task: its action
    answers at once with a task id. */
const CALL_SECONDS = 120;
/** The window's own actions, which the Host asks of the window that shows a workspace (platform.panels.*). */
export type WindowActions = Record<string, (input: Json, caller: Caller) => Json | Promise<Json>>;

export class BridgeClient implements Remote {
  private ws: WebSocket | null = null;
  private ready!: Promise<void>;
  private readyResolve?: () => void;
  private pending = new Map<string, Pending>();
  private subscriptions = new Map<string, Live>();
  private ids = 0;
  private session: string | null = null;
  private stopped = false;
  private reconnect?: ReturnType<typeof setTimeout>;
  /** One session per page: when the connection comes back (the Host restarted, or the socket dropped and the
   *  Host forgot this session's calls and streams) the page starts again. The shell sets this to stash unsaved
   *  work first; by default the page just reloads. */
  onHostRestart: () => void = () => location.reload();
  private welcomed = false;
  windowActions: WindowActions = {};
  constructor(readonly kernel: FrontendKernel, readonly url?: string) {
    kernel.remote = this;
    this.resetReady();
  }
  private resetReady(): void {
    this.ready = new Promise(resolve => { this.readyResolve = resolve; });
    void this.ready.catch(() => undefined);
  }
  open(): void {
    if (this.ws && this.ws.readyState <= WebSocket.OPEN) return;
    this.stopped = false;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(this.url ?? `${proto}://${location.host}/ws/bridge`);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onmessage = event => {
      const text = typeof event.data === "string" ? event.data : decoder.decode(event.data as ArrayBuffer);
      const split = text.indexOf("\n");
      const envelope = JSON.parse(split < 0 ? text : text.slice(0, split)) as Envelope;
      const body = split < 0 || split === text.length - 1 ? undefined : text.slice(split + 1);
      void this.handle(envelope, body);
    };
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null; this.session = null;
      for (const [id, pending] of this.pending) { pending.reject(failure("connection_lost", undefined, undefined, "unknown", "may_have_occurred")); this.retire(id); }
      for (const [id, live] of this.subscriptions) { live.closed = true; live.onEnd?.(failure("connection_lost").value); this.subscriptions.delete(id); }
      this.resetReady();
      if (!this.stopped) this.reconnect = setTimeout(() => this.open(), 500);
    };
  }
  private send(envelope: Envelope, body?: Json): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) throw failure("connection_lost", undefined, undefined, "not_started");
    const head = JSON.stringify(envelope);
    this.ws.send(encoder.encode(body === undefined ? head + "\n" : head + "\n" + JSON.stringify(body)));
  }
  private retire(id: string): void {
    const pending = this.pending.get(id);
    if (pending) { clearTimeout(pending.timer); pending.removeAbort?.(); this.pending.delete(id); }
  }
  private async request(envelope: Envelope, body: Json, options: { signal?: AbortSignal } = {}): Promise<Json> {
    if (this.stopped) throw failure("connection_lost", undefined, undefined, "not_started");
    const deadline = Date.now() + CALL_SECONDS * 1000;
    if (!this.session) await Promise.race([this.ready, new Promise((_, reject) => setTimeout(() => reject(failure("timeout")), Math.max(0, deadline - Date.now())))]);
    if (options.signal?.aborted) throw failure("cancelled");
    if (this.pending.size >= MAX_PENDING) throw failure("backpressure", "Too many calls are waiting for the Host");
    const id = `w${++this.ids}`;
    return new Promise((resolve, reject) => {
      const pending: Pending = { resolve, reject, timer: setTimeout(() => {
        try { this.send({ t: "cancel", id }); } catch { /* the connection is gone already */ }
        reject(failure("timeout", undefined, undefined, "unknown", "may_have_occurred")); this.retire(id);
      }, Math.max(0, deadline - Date.now())) };
      const abort = () => { try { this.send({ t: "cancel", id }); } catch { /* the connection is gone already */ } };
      options.signal?.addEventListener("abort", abort, { once: true });
      pending.removeAbort = () => options.signal?.removeEventListener("abort", abort);
      this.pending.set(id, pending);
      // Every call says which workspace this window shows; the Host never has to have heard of the binding first.
      const ws = this.kernel.workspace.identity.get()?.workspaceId;
      try { this.send({ ...(ws ? { ws } : {}), ...envelope, id }, body); }
      catch (error) { reject(error); this.retire(id); }
    });
  }
  call(name: string, input: Json, options: { module?: string; signal?: AbortSignal } = {}): Promise<Json> {
    return this.request({ t: "call", cap: name, ...(options.module ? { module: options.module } : {}) }, input, options);
  }
  /** A stream whose provider restarts is opened again from the last cursor it delivered (the Host holds the request
   *  until the provider is ready): a panel keeps receiving without doing anything. Any other end reaches ``onEnd``. */
  async subscribe(capability: string, params: Json, callback: Live["callback"], options: { module?: string; cursor?: Json; onEnd?: Live["onEnd"]; onReopen?: () => void } = {}): Promise<Subscription> {
    const live: Live = { id: "", capability, params, module: options.module, cursor: options.cursor,
      callback, onEnd: options.onEnd, onReopen: options.onReopen, tail: Promise.resolve(), closed: false };
    await this.openStream(live);
    return { get id() { return live.id; }, close: async () => {
      if (live.closed) return; live.closed = true;
      this.subscriptions.delete(live.id);
      try { this.send({ t: "unsub", sub: live.id }); } catch { /* the connection is gone already */ }
    } };
  }
  private async openStream(live: Live): Promise<void> {
    const id = live.id = `ws${++this.ids}`;
    this.subscriptions.set(id, live);
    try {
      await this.request({ t: "sub", sub: id, cap: live.capability, ...(live.module ? { module: live.module } : {}),
        ...(live.cursor !== undefined ? { cursor: live.cursor } : {}) }, live.params);
    } catch (error) { this.subscriptions.delete(id); throw error; }
  }
  private async reopen(live: Live): Promise<void> {
    try { await this.openStream(live); live.onReopen?.(); }
    catch (error) {
      if (live.closed) return;
      live.closed = true;
      live.onEnd?.(error instanceof ContractError ? error.value : failure("unavailable", String(error)).value);
    }
  }
  getSessionId(): string | null { return this.session; }
  private async handle(envelope: Envelope, body: string | undefined): Promise<void> {
    const value = body === undefined ? null : JSON.parse(body) as Json;
    switch (envelope.t) {
      case "welcome": {
        if (this.welcomed) { this.onHostRestart(); return; }
        this.welcomed = true;
        this.session = String(envelope.session);
        this.kernel.bindSession(this.session);
        this.readyResolve?.();
        return;
      }
      case "result": case "error": {
        const id = String(envelope.id), pending = this.pending.get(id);
        if (!pending) return;
        if (envelope.t === "error") pending.reject(new ContractError(envelope.err as ErrorValue)); else pending.resolve(value as Json);
        this.retire(id); return;
      }
      case "item": case "end": {
        const id = String(envelope.sub), live = this.subscriptions.get(id);
        if (!live) return;
        if (envelope.t === "end") {
          const error = envelope.err as ErrorValue | undefined;
          this.subscriptions.delete(id);
          live.tail = live.tail.then(() => {
            if (live.closed) return;
            if (error?.code === "provider_restarted") return this.reopen(live);
            live.closed = true; live.onEnd?.(error);
          });
          return;
        }
        const cursor = envelope.cursor as Json | undefined;
        live.tail = live.tail.then(async () => {
          if (live.closed) return;
          if (cursor !== undefined) live.cursor = cursor;
          try { await live.callback(value as Json); }
          catch (error) {
            console.error(`A subscriber of ${id} failed`, error);
            live.closed = true; this.subscriptions.delete(id);
            try { this.send({ t: "unsub", sub: id }); } catch { /* the connection is gone already */ }
            live.onEnd?.(failure("handler_error", "Subscriber failed", undefined, "failed").value);
          }
        });
        return;
      }
      case "call": {
        // One of the window's own actions (its panels), asked for by the Host for someone (AI, a module): the caller
        // is the Host's stamp, as it is.
        const id = String(envelope.id);
        try {
          const action = this.windowActions[String(envelope.cap).replace(/^platform\./, "")];
          if (!action) throw failure("unknown_capability", `This window has no action ${String(envelope.cap)}`);
          const result = await action(value as Json, envelope.from as Caller);
          this.send({ t: "result", id }, result);
        } catch (error) {
          this.send({ t: "error", id, err: error instanceof ContractError ? error.value : failure("handler_error", String(error), undefined, "failed").value });
        }
        return;
      }
    }
  }
  close(): void {
    this.stopped = true;
    if (this.reconnect) clearTimeout(this.reconnect);
    const ws = this.ws; this.ws = null; ws?.close();
    for (const [id, pending] of this.pending) { pending.reject(failure("connection_lost", undefined, undefined, "unknown")); this.retire(id); }
  }
}
