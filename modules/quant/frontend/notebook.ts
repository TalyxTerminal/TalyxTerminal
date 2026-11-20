/* A notebook as its panel holds it: the .ipynb's cells (backend/notebook_file.py), what the kernel socket says about them
   (backend/notebook_routes.py) and the cells this panel has asked to run. */
import type { Json, ModuleContext } from "@talyx/sdk";
import { carriage } from "./ansi";
import { at, route } from "./types";
import type { TableData } from "./notebook/TableExplorer";

export type CellType = "code" | "markdown" | "raw";
export interface StreamOutput { output_type: "stream"; name: "stdout" | "stderr"; text: string }
export interface DataOutput { output_type: "display_data" | "execute_result"; data: Record<string, Json>; metadata: Record<string, Json>; execution_count?: number | null }
export interface ErrorOutput { output_type: "error"; ename: string; evalue: string; traceback: string[] }
/** An output as the file keeps it; `displayId` (never saved) names a display a later update replaces. */
export type Output = (StreamOutput | DataOutput | ErrorOutput) & {displayId?: string};
export interface FileCell { attachments?: Record<string, Record<string, Json>>; id: string; type: CellType; source: string; outputs: Output[]; executionCount: number | null; metadata: Record<string, Json> }
export interface NotebookModel { cells: FileCell[]; metadata: Record<string, Json> }

export type KernelState = "none" | "preparing" | "starting" | "idle" | "busy" | "restarting" | "dead";
export interface KernelView {
  objectId: string; sessionId: string | null; state: KernelState; message: string | null; busy: boolean;
  running: {cell: string; msgId: string; startedAt: number; preview: string}[]; panels: number;
  environment: {preparation: {state: string; message: string | null}; lockDigest?: string; pythonVersion?: string; vscode?: string} | null;
  environmentId?: "research" | "deep";
  /** When the kernel came to its state (epoch milliseconds). */
  since?: number;
}
/** One row of the side panel's variable list; `fromCell` is the execution count of the cell that last set it. */
export interface Loaded { call: string; what: string; at: string; seconds: number }
export interface Variable { name: string; type: string; shape: string | null; size: number | null; fromCell: number | null }
/** What D's deep-learning environment block reads: installed or not, what will be installed, how far an install is. */
export interface DeepStatus {
  installed: boolean; offer: {variant: "cpu" | "cuda" | "xpu"; size: string; label: string; choices?: ("cpu" | "xpu")[]};
  installing: {state: string; fraction: number | null} | null; failure: string | null;
}
export interface KernelEnvironment { id: "research" | "deep"; label: string; python: string; installed: boolean; device?: string }
export interface NotebookRead { objectId: string; name: string; version: number; documentId: string; notebook: NotebookModel; kernel: KernelView }

export type HostMessage = {savedByAction?: boolean} & (
  | ({type: "state"} & KernelView)
  | {type: "accepted"; cell: string; msgId: string}
  | {type: "input"; cell: string; msgId: string; executionCount: number}
  | {type: "output"; cell: string; msgId: string; output: Output; displayId?: string}
  | {type: "output_update"; cell: string; msgId: string; displayId: string; output: Output}
  | {type: "clear_output"; cell: string; msgId: string; wait: boolean}
  | {type: "reply"; cell: string; msgId: string; status: "ok" | "error" | "aborted" | "interrupted"; executionCount: number | null}
  | {type: "complete_reply"; id: number; status: string; matches: string[]; cursor_start: number; cursor_end: number; metadata: {_jupyter_types_experimental?: {type?: string}[]}}
  | {type: "inspect_reply"; id: number; status: string; found: boolean; data: Record<string, Json>}
  | {type: "variables"; variables: Variable[]; loaded?: Loaded[]; memory?: number | null; startedAt?: number | null}
  | {type: "variable_table"; name: string; table: TableData | null}
  | {type: "input_request"; cellId: string; prompt: string; password: boolean}
  | {type: "input_done"; cellId: string; text: string}
  | {type: "comm"; msgType: "comm_open" | "comm_msg" | "comm_close"; commId: string; data: unknown; target?: string}
  | {type: "error"; op: string; cell?: string; id?: number; code: string; message: string});
export type PanelMessage =
  | {op: "execute"; cell: string; code: string}
  | {op: "interrupt"} | {op: "restart"}
  | {op: "input_reply"; cell: string; value: string}
  | {op: "variable"; name: string}
  | {op: "comm_msg"; commId: string; data: Json}
  | {op: "complete" | "inspect"; id: number; code: string; cursor: number};

/** Why a cell shows a line under its outputs: it was stopped, or never ran. */
export type Note = {kind: "interrupted"} | {kind: "blocked"; code: string; message: string};
/** A cell with what this panel knows about its run. `queued`: waiting here to be sent; `running`: the kernel has it. */
export interface Cell extends FileCell { status: "idle" | "queued" | "running"; msgId: string | null; clearPending: boolean; note: Note | null; editing: boolean }
export interface Doc {
  objectId: string; name: string; version: number; documentId: string; metadata: Record<string, Json>; cells: Cell[];
  kernel: KernelView; connected: boolean; dirty: boolean;
  /** What the kernel holds now, and the cells waiting for an input() answer (the box the cell's output ends with). */
  variables: Variable[]; inputs: Record<string, {prompt: string; password: boolean}>;
  loaded: Loaded[]; memory: number | null; startedAt: number | null; opened: {name: string; table: TableData | null} | null;
  /** This panel's run queue: the cell sent and not yet over (with the text sent), then the cells waiting behind it. */
  sent: {cell: string; code: string; msgId: string | null} | null; queue: {cell: string; code: string}[];
  /** The kernel was replaced without the user asking (Quant restarted, so its kernels ended): said until a cell runs. */
  restarted?: boolean;
}

export const newCellId = () => crypto.randomUUID().replace(/-/g, "").slice(0, 12);
export const blankCell = (type: CellType = "code", source = ""): Cell =>
  ({id: newCellId(), type, source, outputs: [], executionCount: null, metadata: {}, status: "idle", msgId: null, clearPending: false, note: null, editing: true});

export function openDoc(read: NotebookRead): Doc {
  return {objectId: read.objectId, name: read.name, version: read.version, documentId: read.documentId, metadata: read.notebook.metadata,
    cells: read.notebook.cells.map(cell => ({...cell, status: "idle", msgId: null, clearPending: false, note: null, editing: cell.type !== "markdown" || cell.source.trim() === ""})),
    kernel: read.kernel, connected: false, dirty: false, variables: [], inputs: {}, loaded: [], memory: null, startedAt: null, opened: null, sent: null, queue: []};
}

/** The file's model of the cells: outputs without their display ids. */
export function fileModel(doc: Doc): NotebookModel {
  return {metadata: doc.metadata, cells: doc.cells.map(cell => ({id: cell.id, type: cell.type, source: cell.source, ...(cell.type !== "code" && cell.attachments && {attachments: cell.attachments}), executionCount: cell.executionCount, metadata: cell.metadata,
    outputs: cell.outputs.map(output => {
      if (output.displayId === undefined) return output;
      const {displayId: _displayId, ...saved} = output;
      return saved as Output;
    })}))};
}

const mapCell = (doc: Doc, id: string, change: (cell: Cell) => Cell): Doc => ({...doc, cells: doc.cells.map(cell => cell.id === id ? change(cell) : cell)});

/** Adds an output; text to the same stream continues the last output, as Jupyter shows it. */
export function appendOutput(outputs: Output[], output: Output): Output[] {
  const last = outputs[outputs.length - 1];
  if (output.output_type === "stream" && last !== undefined && last.output_type === "stream" && last.name === output.name)
    return [...outputs.slice(0, -1), {...last, text: carriage(last.text + output.text)}];
  return [...outputs, output.output_type === "stream" ? {...output, text: carriage(output.text)} : output];
}

/** The kernel's view of the notebook applied to the cells. A new session means the cells this panel thought running were stopped. */
export function receive(doc: Doc, message: HostMessage): Doc {
  const dirty = doc.dirty || !message.savedByAction;
  switch (message.type) {
  case "state": {
    const {type: _type, ...kernel} = message;
    const restarted = doc.kernel.sessionId !== null && kernel.sessionId !== doc.kernel.sessionId;
    const live = new Map(kernel.running.map(item => [item.msgId, item.cell]));
    const cells = doc.cells.map(cell => {
      if (cell.status === "running" && !live.has(cell.msgId!)) return {...cell, status: "idle" as const, msgId: null, note: restarted ? {kind: "interrupted" as const} : cell.note};
      if (restarted && cell.status === "queued") return {...cell, status: "idle" as const, note: {kind: "interrupted" as const}};
      const running = kernel.running.find(item => item.cell === cell.id);
      if (cell.status !== "running" && running !== undefined) return {...cell, status: "running" as const, msgId: running.msgId, note: null};
      return cell;
    });
    const same = cells.every((cell, index) => cell === doc.cells[index]);
    if (restarted) return {...doc, kernel, cells, sent: null, queue: [], variables: [], inputs: {}, loaded: [], opened: null,
      restarted: doc.kernel.state !== "restarting"};
    // A reply missed while the socket was down: the cell is over, and the queue behind it is not run blindly.
    const lost = doc.sent !== null && doc.sent.msgId !== null && !live.has(doc.sent.msgId);
    if (lost) return {...doc, kernel, cells: dropQueue(doc, cells), sent: null, queue: []};
    return {...doc, kernel, cells: same ? doc.cells : cells};
  }
  case "accepted": {
    const sent = !message.savedByAction && doc.sent !== null && doc.sent.cell === message.cell && doc.sent.msgId === null ? {...doc.sent, msgId: message.msgId} : doc.sent;
    return {...mapCell(doc, message.cell, cell => ({...cell, status: "running", msgId: message.msgId, outputs: [], clearPending: false, note: null})), sent, dirty, restarted: false};
  }
  case "input":
    return mapCell(doc, message.cell, cell => cell.msgId === message.msgId ? {...cell, executionCount: message.executionCount} : cell);
  case "output":
    return {...mapCell(doc, message.cell, cell => cell.msgId !== message.msgId ? cell : {...cell, clearPending: false,
      outputs: appendOutput(cell.clearPending ? [] : cell.outputs, message.displayId === undefined ? message.output : {...message.output, displayId: message.displayId})}), dirty};
  case "output_update":
    return {...doc, dirty, cells: doc.cells.map(cell => cell.outputs.some(output => output.displayId === message.displayId)
      ? {...cell, outputs: cell.outputs.map(output => output.displayId === message.displayId ? {...message.output, displayId: message.displayId} : output)} : cell)};
  case "clear_output":
    return {...mapCell(doc, message.cell, cell => cell.msgId !== message.msgId ? cell : message.wait ? {...cell, clearPending: true} : {...cell, outputs: []}), dirty};
  case "reply": {
    const ours = doc.sent !== null && doc.sent.msgId === message.msgId;
    const next = mapCell(doc, message.cell, cell => cell.msgId !== message.msgId ? cell : {...cell, status: "idle", msgId: null, clearPending: false,
      executionCount: message.status === "aborted" ? cell.executionCount : message.executionCount, note: message.status === "interrupted" ? {kind: "interrupted"} : null});
    if (!ours) return {...next, dirty};
    // Like Jupyter, a failed or stopped cell stops the cells queued behind it.
    const stopped = message.status !== "ok";
    return {...next, dirty, sent: null, queue: stopped ? [] : next.queue,
      cells: stopped ? next.cells.map(cell => doc.queue.some(item => item.cell === cell.id) ? {...cell, status: "idle"} : cell) : next.cells};
  }
  case "variables":
    return {...doc, variables: message.variables, loaded: message.loaded ?? doc.loaded, memory: message.memory ?? null, startedAt: message.startedAt ?? doc.startedAt};
  case "variable_table":
    return {...doc, opened: {name: message.name, table: message.table}};
  case "input_request":
    return {...doc, inputs: {...doc.inputs, [message.cellId]: {prompt: message.prompt, password: message.password}}};
  case "input_done": {
    const {[message.cellId]: _gone, ...rest} = doc.inputs;
    return {...doc, inputs: rest};
  }
  default:
    return doc;
  }
}

/** Queues cells to run here, in order, with their text as it is now. */
export function enqueue(doc: Doc, ids: string[]): Doc {
  const runnable = ids.filter(id => {const cell = doc.cells.find(item => item.id === id); return cell !== undefined && cell.type === "code" && cell.source.trim() !== "";});
  const queue = [...doc.queue, ...runnable.map(id => ({cell: id, code: doc.cells.find(cell => cell.id === id)!.source}))];
  return {...doc, queue, cells: doc.cells.map(cell => runnable.includes(cell.id) && cell.status === "idle" ? {...cell, status: "queued", note: null} : cell)};
}

/** The next cell to send when nothing of this panel's is in the kernel, or null. */
export function nextToSend(doc: Doc): {doc: Doc; send: {cell: string; code: string}} | null {
  if (doc.sent !== null || doc.queue.length === 0 || !doc.connected) return null;
  const [first, ...rest] = doc.queue;
  return {doc: {...doc, sent: {...first, msgId: null}, queue: rest}, send: first};
}

/** Stops this panel's queue: the cells waiting in it go back to not running. */
const dropQueue = (doc: Doc, cells: Cell[]): Cell[] => cells.map(cell => cell.status === "queued" && doc.queue.some(item => item.cell === cell.id) ? {...cell, status: "idle"} : cell);

/** The socket refused to run the sent cell (a pip line, the kernel not running): it and the cells queued behind it stay unrun. */
export function refused(doc: Doc, cellId: string, code: string, message: string): Doc {
  const cells = doc.cells.map(cell => cell.id === cellId ? {...cell, status: "idle" as const, note: {kind: "blocked" as const, code, message}} : cell);
  if (doc.sent === null || doc.sent.cell !== cellId) return {...doc, cells};
  return {...doc, sent: null, queue: [], cells: dropQueue(doc, cells)};
}

/** The socket dropped: a cell sent but not yet accepted may never have arrived, so this panel's queue stops there. */
export function disconnected(doc: Doc): Doc {
  if (doc.sent === null || doc.sent.msgId !== null) return {...doc, connected: false};
  const cellId = doc.sent.cell;
  return {...doc, connected: false, sent: null, queue: [], cells: dropQueue(doc, doc.cells.map(cell => cell.id === cellId && cell.status === "queued" ? {...cell, status: "idle" as const} : cell))};
}

/** Cells this notebook is running now, in any panel. */
export const busyCells = (doc: Doc) => doc.cells.filter(cell => cell.status !== "idle");

/** Jupyter counts cursor positions in code points; the editor counts UTF-16 units. */
export function toCodePoints(text: string, offset: number): number { return [...text.slice(0, offset)].length; }
export function toUnits(text: string, points: number): number { return [...text].slice(0, points).join("").length; }

export const notebookApi = (ctx: ModuleContext) => ({
  read: (id: string) => route<NotebookRead>(ctx, "GET", at`/notebooks/${id}`),
  save: (id: string, notebook: NotebookModel, expectedVersion: number) => route<{objectId: string; version: number; documentId: string}>(ctx, "PUT", at`/notebooks/${id}`, {notebook, expectedVersion}),
  kernel: (id: string) => route<KernelView>(ctx, "GET", at`/notebooks/${id}/kernel`),
  stop: (id: string) => route<KernelView>(ctx, "POST", at`/notebooks/${id}/kernel/stop`),
  packages: () => route<PackageStatus>(ctx, "GET", "/packages"),
  install: () => route<PackageStatus>(ctx, "POST", "/packages/install"),
  cancelInstall: () => route<PackageStatus>(ctx, "POST", "/packages/cancel"),
  environments: () => route<KernelEnvironment[]>(ctx, "GET", "/kernel-environments"),
  setEnvironment: (notebookId: string, environment: "research" | "deep") => route(ctx, "POST", at`/notebooks/${notebookId}/kernel/environment`, {environment}),
  deep: () => route<DeepStatus>(ctx, "GET", "/deep-environment"),
  installDeep: (variant?: string) => route<DeepStatus>(ctx, "POST", "/deep-environment/install", variant ? {variant} : {}),
  library: () => route<unknown>(ctx, "GET", "/library", {notebook: "true"}),
  previewExtract: (notebookId: string, cells: string[], kind: string) => route<ExtractPreview>(ctx, "POST", at`/notebooks/${notebookId}/extract/preview`, {cells, kind}),
  createFromNotebook: (notebookId: string, cells: string[], kind: string, name: string) => route<{id: string; name: string}>(ctx, "POST", at`/notebooks/${notebookId}/extract`, {cells, kind, name}),
});
export interface ExtractPreview {
  code: string; notTaken: string[]; heldOutFrom: string | null;
  dataSettings: {runs: {object: string; name?: string; number?: number; at?: number}[]; libraryInputs: {name: string; version: number | null}[]; universe: string | null; symbols: unknown; timeframe: string | null; since: string | null; until: string | null;
    heldOutStudied: boolean; conflicts: string[]; found: boolean};
}
export type NotebookApi = ReturnType<typeof notebookApi>;

export type EnvironmentName = "research" | "strategy";
export interface PackageFailure { code: string; message: string; details?: {environment?: string; requested?: string[]; cause?: string; package?: string} | null }
export interface PackageStatus {
  requirements: string[]; file: string;
  job: {state: "running" | "completed" | "failed" | "cancelled"; stage: string; startedAt: number; endedAt: number | null;
    environments: Partial<Record<EnvironmentName, {state: string; message: string | null}>>; failures: Partial<Record<EnvironmentName, PackageFailure>>} | null;
  environments: Record<EnvironmentName, {lockDigest: string; installedAt: number; packages: string[]} | null>;
  restartKernels: {objectId: string; name: string; workspaceId: string}[];
}

/**
 * The panel's socket to the notebook's kernel. It opens on the module's own routes, not the panel's, so closing the panel
 * can still ask whether other panels show the notebook before the kernel is stopped; `close` ends it. A dropped socket reconnects.
 */
export class KernelSocket {
  private socket: WebSocket | null = null;
  private closed = false;
  private retry: ReturnType<typeof setTimeout> | null = null;
  private delay = 1000;
  constructor(private open: () => WebSocket, private onMessage: (message: HostMessage) => void, private onConnected: (connected: boolean) => void) { this.connect(); }
  private connect() {
    const socket = this.socket = this.open();
    socket.onopen = () => {this.delay = 1000; this.onConnected(true);};
    socket.onmessage = event => this.onMessage(JSON.parse(String(event.data)) as HostMessage);
    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.socket = null;
      this.onConnected(false);
      if (this.closed) return;
      this.retry = setTimeout(() => {this.retry = null; if (!this.closed) this.connect();}, this.delay);
      this.delay = Math.min(this.delay * 2, 10000);
    };
  }
  send(message: PanelMessage) {
    if (this.socket === null || this.socket.readyState !== WebSocket.OPEN) throw new Error("The kernel connection is not open.");
    this.socket.send(JSON.stringify(message));
  }
  close() {
    this.closed = true;
    if (this.retry !== null) clearTimeout(this.retry);
    const socket = this.socket;
    this.socket = null;
    if (socket !== null) socket.close();
  }
}
