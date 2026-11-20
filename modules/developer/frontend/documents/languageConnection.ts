/** JSON-RPC to the Python language server through Developer's backend: requests with answers and cancellation, notifications both ways. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Lsp = any;

export interface Cancellation { onCancellationRequested(listener: () => void): unknown }
export interface Handlers {
  notification(method: string, params: Lsp): void;
  /** The socket ended. `code` 4001: another window took over; 4002: the service stopped; 4004: the session is unknown. */
  closed(code: number): void;
}

export class LanguageConnection {
  private sequence = 0;
  private pending = new Map<number, { resolve(value: Lsp): void; reject(error: Error): void }>();
  closed = false;

  private constructor(private socket: WebSocket, private handlers: Handlers) {
    socket.onmessage = event => this.receive(JSON.parse(String(event.data)));
    socket.onclose = event => this.end(event.code);
  }

  /** Resolves once the socket is open. The backend starts the server behind it, which can take a few seconds. */
  static open(create: () => WebSocket, handlers: Handlers): Promise<LanguageConnection> {
    return new Promise((resolve, reject) => {
      const socket = create();
      const connection = new LanguageConnection(socket, handlers);
      socket.onopen = () => resolve(connection);
      const early = socket.onclose;
      socket.onclose = event => { if (!connection.closed) reject(new Error("The Python language service did not start.")); early?.call(socket, event); };
    });
  }

  private end(code: number) {
    if (this.closed) return;
    this.closed = true;
    for (const waiting of this.pending.values()) waiting.reject(new Error("The Python language service closed."));
    this.pending.clear();
    this.handlers.closed(code);
  }

  private send(message: Lsp) { if (!this.closed && this.socket.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify({ jsonrpc: "2.0", ...message })); }
  notify(method: string, params: Lsp) { this.send({ method, params }); }

  /** The answer's result; a cancelled request answers null. */
  request(method: string, params: Lsp, token?: Cancellation): Promise<Lsp> {
    const id = ++this.sequence;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      token?.onCancellationRequested(() => { if (this.pending.delete(id)) { this.notify("$/cancelRequest", { id }); resolve(null); } });
      this.send({ id, method, params });
    });
  }

  private receive(message: Lsp) {
    if (message.method) { this.handlers.notification(message.method, message.params); return; }
    const waiting = this.pending.get(message.id);
    if (!waiting) return;
    this.pending.delete(message.id);
    if (message.error) waiting.reject(new Error(message.error.message ?? "The language server refused the request."));
    else waiting.resolve(message.result);
  }

  /** The server is already initialized by the backend; this returns what it can do. */
  async initialize(): Promise<Lsp> {
    const result = await this.request("initialize", { processId: null, rootUri: null, capabilities: {} });
    this.notify("initialized", {});
    return result.capabilities;
  }

  close() { this.closed = true; this.pending.clear(); this.socket.close(); }
}
