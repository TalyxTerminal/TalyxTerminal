import { afterEach, expect, it, vi } from "vitest";
import { FrontendKernel } from "./registry";
import { BridgeClient } from "./bridge";

/** A WebSocket stand-in: what the window sends is kept as [envelope, body]; ``receive`` delivers a Host frame. */
class Socket {
  static OPEN = 1;
  static last: Socket;
  readyState = 1;
  binaryType = "blob";
  sent: [Record<string, unknown>, unknown][] = [];
  onmessage?: (event: { data: ArrayBuffer }) => void;
  onclose?: () => void;
  constructor() { Socket.last = this; }
  send(data: Uint8Array) {
    const text = new TextDecoder().decode(data), cut = text.indexOf("\n");
    this.sent.push([JSON.parse(text.slice(0, cut)), cut === text.length - 1 ? undefined : JSON.parse(text.slice(cut + 1))]);
  }
  close() { this.readyState = 3; }
  receive(envelope: Record<string, unknown>, body?: unknown) {
    const text = JSON.stringify(envelope) + "\n" + (body === undefined ? "" : JSON.stringify(body));
    this.onmessage?.({ data: new TextEncoder().encode(text).buffer as ArrayBuffer });
  }
}

function connected() {
  vi.stubGlobal("WebSocket", Socket);
  vi.stubGlobal("location", { protocol: "http:", host: "127.0.0.1", reload: vi.fn() });
  const bridge = new BridgeClient(new FrontendKernel());
  bridge.open();
  return { bridge, socket: Socket.last };
}

afterEach(() => { vi.unstubAllGlobals(); });

it("a call goes out as one binary frame (envelope line, then the body) and its result comes back by id", async () => {
  const { bridge, socket } = connected();
  socket.receive({ t: "welcome", session: "s1", host: "h1" });
  const answer = bridge.call("market.instruments.search", { query: "AAPL" });
  await vi.waitFor(() => expect(socket.sent).toHaveLength(1));
  const [envelope, body] = socket.sent[0];
  expect(envelope).toMatchObject({ t: "call", cap: "market.instruments.search" });
  expect(body).toEqual({ query: "AAPL" });
  socket.receive({ t: "result", id: envelope.id }, { results: [] });
  await expect(answer).resolves.toEqual({ results: [] });
});

it("an error answer is the one error type, with its code", async () => {
  const { bridge, socket } = connected();
  socket.receive({ t: "welcome", session: "s1", host: "h1" });
  const answer = bridge.call("market.data.read", {});
  await vi.waitFor(() => expect(socket.sent).toHaveLength(1));
  socket.receive({ t: "error", id: socket.sent[0][0].id, err: { code: "invalid_input", message: "No series", execution: "not_started", effects: "none" } });
  await expect(answer).rejects.toMatchObject({ code: "invalid_input", message: "No series" });
});

it("a connection that comes back (the Host restarted) asks the page to start again, once, and sends nothing", async () => {
  const { bridge, socket } = connected();
  const restarted = vi.fn();
  bridge.onHostRestart = restarted;
  socket.receive({ t: "welcome", session: "s1", host: "h1" });
  expect(restarted).not.toHaveBeenCalled();
  socket.receive({ t: "welcome", session: "s2", host: "h2" });
  expect(restarted).toHaveBeenCalledOnce();
  expect(socket.sent).toHaveLength(0);
});

it("a stream whose provider restarted is opened again from its last cursor, and the reader never sees the gap", async () => {
  const { bridge, socket } = connected();
  socket.receive({ t: "welcome", session: "s1", host: "h1" });
  const items: unknown[] = [], ended = vi.fn();
  const opening = bridge.subscribe("market.session", { series: "s" }, item => { items.push(item); }, { onEnd: ended });
  await vi.waitFor(() => expect(socket.sent).toHaveLength(1));
  const [first] = socket.sent[0];
  socket.receive({ t: "result", id: first.id }, null);
  const subscription = await opening;
  socket.receive({ t: "item", sub: first.sub, cursor: "c1" }, { n: 1 });
  socket.receive({ t: "end", sub: first.sub, err: { code: "provider_restarted", message: "restarted", execution: "unknown", effects: "may_have_occurred" } });
  await vi.waitFor(() => expect(socket.sent).toHaveLength(2));
  const [again, params] = socket.sent[1];
  expect(again).toMatchObject({ t: "sub", cap: "market.session", cursor: "c1" });
  expect(params).toEqual({ series: "s" });
  expect(again.sub).not.toBe(first.sub);
  socket.receive({ t: "result", id: again.id }, null);
  socket.receive({ t: "item", sub: again.sub }, { n: 2 });
  await vi.waitFor(() => expect(items).toEqual([{ n: 1 }, { n: 2 }]));
  expect(ended).not.toHaveBeenCalled();
  await subscription.close();
  expect(socket.sent[2][0]).toMatchObject({ t: "unsub", sub: again.sub });
});

it("any other end of a stream reaches its reader", async () => {
  const { bridge, socket } = connected();
  socket.receive({ t: "welcome", session: "s1", host: "h1" });
  const ended = vi.fn();
  const opening = bridge.subscribe("market.quotes", {}, () => undefined, { onEnd: ended });
  await vi.waitFor(() => expect(socket.sent).toHaveLength(1));
  socket.receive({ t: "result", id: socket.sent[0][0].id }, null);
  await opening;
  socket.receive({ t: "end", sub: socket.sent[0][0].sub, err: { code: "unavailable", message: "stopped", execution: "unknown", effects: "none" } });
  await vi.waitFor(() => expect(ended).toHaveBeenCalledWith(expect.objectContaining({ code: "unavailable" })));
  expect(socket.sent).toHaveLength(1);
});
