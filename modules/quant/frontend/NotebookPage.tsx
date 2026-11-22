import { t } from "./i18n";
import { memo, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ContractError, ModuleView, TxButton, TxCheckbox, TxDialog, TxIcon, TxIconButton, TxInput, TxMenu, TxSelect, type Json, type LeaveDecision, type ModuleContext, type TxMenuItem } from "@talyx/sdk";
import type { ContractTypes as DeveloperTypes } from "../../developer/public/ts/types";
import { stripAnsi } from "./ansi";
import { NotebookOutput, renderMarkdown } from "./NotebookOutput";
import {
  KernelSocket, blankCell, type DeepStatus, type ExtractPreview, type KernelEnvironment, busyCells, disconnected, enqueue, fileModel, nextToSend, notebookApi, openDoc, receive, refused,
  toCodePoints, toUnits, type Cell, type CellType, type Doc, type HostMessage, type PackageStatus,
} from "./notebook";
import { openCommonCode, useEvent, type Api, type ObjectDetail } from "./objects";
import type { PanelHost } from "./QuantShell";
import { at } from "./types";
import { ExtractSheet } from "./notebook/ExtractSheet";
import { Rail, SidePanel, libraryEntries, type LibraryEntry, type Side } from "./notebook/SidePanel";
import { RunBand, type OpenedFrom } from "./notebook/RunBand";
import { OpenRunContext, RunStateContext } from "./notebook/openRun";
import { createAutosave } from "./notebook/autosave";
import { WidgetBus, WidgetBusContext } from "./notebook/widgets/bus";
import { Toolbar } from "./notebook/Toolbar";
import "./notebook.css";
import "./notebook/notebook.css";

type EditorProps = DeveloperTypes["view:text_editor"];
type Answer = NonNullable<EditorProps["answer"]>;
type Key = NonNullable<EditorProps["keys"]>[number];
const KEYS: Key[] = ["Shift+Enter", "Ctrl+Enter", "Alt+Enter", "Ctrl+S"];
const COMPLETION_KINDS: Record<string, NonNullable<Extract<Answer, {kind: "complete"}>["items"][number]["kind"]>> = {
  function: "function", builtin_function: "function", method: "function", class: "class", type: "class", module: "module", instance: "variable",
  statement: "variable", param: "variable", keyword: "keyword", property: "property", path: "file", magic: "keyword",
};
/** Autosave: a pause in editing, or this long while output keeps arriving. */
const SAVE_PAUSE = 4000, SAVE_AT_MOST = 20000;

interface Actions {
  edit(cell: string, value: string): void; focus(cell: string): void; key(cell: string, key: Key): void; ask(cell: string, kind: "complete" | "inspect", request: {requestId: string; value: string; offset: number}): void;
  run(cell: string): void; move(cell: string, by: -1 | 1): void; remove(cell: string): void; convert(cell: string, type: CellType): void; clear(cell: string): void;
  editMarkdown(cell: string): void; toggleOutput(cell: string): void; packages(): void; interrupt(): void; tick(cell: string): void; reply(cell: string, value: string): void;
}

/** A notebook, Jupyter style: cells with their outputs in one scrolling page, run against the notebook's kernel. */
export function NotebookPage({ctx, host, api, detail, title, menu, panelKey, onPackages, onOpenObject}: {
  ctx: ModuleContext; host: PanelHost; api: Api; detail: ObjectDetail; title: ReactNode; menu: TxMenuItem[]; panelKey: string; onPackages(): void; onOpenObject(objectId: string, runId?: string): void;
}) {
  const nb = useMemo(() => notebookApi(ctx), [ctx]);
  // Closing the panel ends the panel's own routes; what must still happen then goes through the module's.
  const lasting = useMemo(() => notebookApi(host.module), [host.module]);
  const [doc, setDoc] = useState<Doc | null>(null);
  const [runGone, setRunGone] = useState(false);
  const openedRun = (detail.settings as {openedFrom?: {runId?: string}} | undefined)?.openedFrom?.runId;
  useEffect(() => {
    if (openedRun === undefined) return;
    void api.run(openedRun).then(() => setRunGone(false), reason => setRunGone(reason instanceof ContractError && reason.code === "not_found"));
  }, [openedRun]);
  const docRef = useRef<Doc | null>(null);
  const socket = useRef<KernelSocket | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [focus, setFocus] = useState<{cell: string; id: string} | null>(null);
  const [answers, setAnswers] = useState<Record<string, Answer>>({});
  const requests = useRef(new Map<number, {cell: string; requestId: string; value: string; kind: "complete" | "inspect"}>());
  const sequence = useRef(0);
  const [packages, setPackages] = useState<PackageStatus | null>(null);
  const [saving, setSaving] = useState<"idle" | "saving" | "conflict">("idle");
  const savingRef = useRef<"idle" | "saving" | "conflict">("idle");
  const [restartAsk, setRestartAsk] = useState(false);
  // Opened from a run, the draft shows the library beside the first cell (the run is loaded; what to read next is the question); otherwise the variables.
  const [side, setSide] = useState<Side>(() => (detail.settings as {openedFrom?: unknown} | undefined)?.openedFrom === undefined ? "vars" : "lib");
  const [library, setLibrary] = useState<LibraryEntry[]>([]);
  const [environments, setEnvironments] = useState<KernelEnvironment[]>([]);
  const [envAsk, setEnvAsk] = useState<KernelEnvironment | null>(null);
  const [deepAsk, setDeepAsk] = useState<DeepStatus | null>(null);
  const [deepChoice, setDeepChoice] = useState("cpu");
  const [deepWork, setDeepWork] = useState<{text: string; failed: boolean} | null>(null);
  const [ticked, setTicked] = useState<string[]>([]);
  const [extract, setExtract] = useState<{kind: string; preview: ExtractPreview | null; failure: string | null} | null>(null);
  const fail = (reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason));
  // The deep-learning environment is D's: when it is not installed, the confirmation offers its install (D's action) and the kernel restarts in it afterwards.
  useEffect(() => {
    setDeepAsk(null);
    if (envAsk === null || envAsk.id !== "deep" || envAsk.installed) return;
    void nb.deep().then(status => {setDeepAsk(status); setDeepChoice(status.offer.choices?.[0] ?? status.offer.variant);}, fail);
  }, [envAsk]);
  const installDeep = async (variant: string, then: KernelEnvironment) => {
    setDeepWork({text: t("Installing the deep-learning environment…"), failed: false});
    let status = await nb.installDeep(variant);
    for (;;) {
      if (status.failure !== null) {setDeepWork({text: t("Deep-learning environment failed: {failure}", { failure: status.failure }), failed: true}); return;}
      if (status.installing === null || status.installing.state !== "running") break;
      const fraction = status.installing.fraction;
      setDeepWork({text: (typeof fraction === "number" ? t("Installing the deep-learning environment… {percent}%", { percent: Math.round(fraction * 100) }) : t("Installing the deep-learning environment…")), failed: false});
      await new Promise(resolve => setTimeout(resolve, 1500));
      status = await nb.deep();
    }
    if (!status.installed) {setDeepWork({text: t("Deep-learning environment failed: the install did not finish"), failed: true}); return;}
    setDeepWork(null);
    setEnvironments(await nb.environments());
    await nb.setEnvironment(detail.id, then.id);
  };

  /** Every change to the notebook goes through here, so the queue sends its next cell as soon as it may. */
  const change = (update: (doc: Doc) => Doc) => {
    const current = docRef.current;
    if (current === null) return;
    let next = update(current);
    const step = nextToSend(next);
    if (step !== null) {
      next = step.doc;
      socket.current!.send({op: "execute", cell: step.send.cell, code: step.send.code});
    }
    docRef.current = next;
    setDoc(next);
  };
  const send = (message: Parameters<KernelSocket["send"]>[0]) => {
    try {socket.current!.send(message);} catch (reason) {fail(reason);}
  };
  const answer = (id: number, value: (request: {requestId: string; value: string}) => Answer) => {
    const request = requests.current.get(id);
    if (request === undefined) return;
    requests.current.delete(id);
    setAnswers(current => ({...current, [request.cell]: value(request)}));
  };
  const bus = useMemo(() => new WidgetBus((commId, data) => {try {socket.current!.send({op: "comm_msg", commId, data: data as Json});} catch (reason) {fail(reason);}}), []);
  const onMessage = (message: HostMessage) => {
    if (message.type === "comm") bus.receive(message);
    else if (message.type === "state" && docRef.current !== null && docRef.current.kernel.sessionId !== null && message.sessionId !== docRef.current.kernel.sessionId) {bus.reset(); change(current => receive(current, message));}
    else if (message.type === "complete_reply") answer(message.id, request => ({requestId: request.requestId, kind: "complete",
      start: toUnits(request.value, message.cursor_start), end: toUnits(request.value, message.cursor_end),
      items: message.status !== "ok" ? [] : message.matches.slice(0, 2000).map((label, index) => {
        const types = message.metadata._jupyter_types_experimental;
        const type = types === undefined || types[index] === undefined ? undefined : types[index].type;
        return type !== undefined && type in COMPLETION_KINDS ? {label, kind: COMPLETION_KINDS[type]} : {label};
      })}));
    else if (message.type === "inspect_reply") answer(message.id, request => ({requestId: request.requestId, kind: "inspect",
      text: message.status === "ok" && message.found && typeof message.data["text/plain"] === "string" ? stripAnsi(message.data["text/plain"]) : null}));
    else if (message.type === "error") {
      if (message.op === "execute" && message.cell !== undefined) change(current => refused(current, message.cell!, message.code, message.message));
      else if ((message.op === "complete" || message.op === "inspect") && message.id !== undefined)
        answer(message.id, request => message.op === "complete" ? {requestId: request.requestId, kind: "complete", start: 0, end: 0, items: []} : {requestId: request.requestId, kind: "inspect", text: null});
      else {
        if (message.op === "connect") socket.current!.close();
        setError(message.message);
      }
    } else change(current => receive(current, message));
  };
  const handler = useRef(onMessage);
  handler.current = onMessage;

  // Open: read the file, then join the kernel (started on connect if it was not running).
  useEffect(() => {
    let live = true;
    host.detached.delete(detail.id);
    void nb.read(detail.id).then(read => {
      if (!live) return;
      docRef.current = openDoc(read);
      setDoc(docRef.current);
      socket.current = new KernelSocket(() => host.module.routes.websocket(at`/notebooks/${detail.id}/kernel/socket` + `?panel=${encodeURIComponent(panelKey)}`),
        message => handler.current(message), connected => change(current => connected ? {...current, connected: true} : disconnected(current)));
    }, fail);
    return () => {live = false; leave();};
  }, [detail.id]);

  // Saving: the whole file with the version it was read at; a newer file on disk is never overwritten silently.
  const save = async () => {
    const current = docRef.current;
    if (current === null || savingRef.current !== "idle") return;
    savingRef.current = "saving"; setSaving("saving");
    try {
      const saved = await nb.save(current.objectId, fileModel(current), current.version);
      change(latest => ({...latest, version: saved.version, dirty: latest.cells !== current.cells || latest.metadata !== current.metadata}));
      savingRef.current = "idle"; setSaving("idle");
    } catch (reason) {
      const conflict = reason instanceof ContractError && reason.code === "conflict";
      savingRef.current = conflict ? "conflict" : "idle"; setSaving(savingRef.current);
      if (!conflict) fail(reason);
    }
  };
  const reload = async (discardEdits = false) => {
    const read = await nb.read(detail.id);
    const latest = docRef.current;
    if (!latest || latest.objectId !== read.objectId || latest.version > read.version) return;
    if (!discardEdits && latest.dirty) {savingRef.current = "conflict"; setSaving("conflict"); return;}
    const fresh = openDoc(read);
    change(current => ({...current, version: fresh.version, metadata: fresh.metadata, dirty: false,
      cells: fresh.cells.map(cell => {const old = current.cells.find(item => item.id === cell.id); return old !== undefined && old.status !== "idle" ? {...cell, status: old.status, msgId: old.msgId} : cell;})}));
    savingRef.current = "idle"; setSaving("idle");
  };
  const keepMine = async () => {
    const read = await nb.read(detail.id);
    change(current => ({...current, version: read.version}));
    savingRef.current = "idle"; setSaving("idle");
    await save();
  };
  const dirty = doc !== null && doc.dirty;
  const cellsKey = doc === null ? null : doc.cells;
  const saveRef = useRef<() => void>(() => undefined);
  saveRef.current = () => void save();
  const autosave = useMemo(() => createAutosave({pause: SAVE_PAUSE, atMost: SAVE_AT_MOST, save: () => saveRef.current()}), []);
  useEffect(() => {
    if (dirty && saving === "idle") autosave.touch();
  }, [dirty, cellsKey, saving, autosave]);
  useEffect(() => () => autosave.cancel(), [autosave]);
  // The file changed elsewhere (Developer, VS Code, an AI's edit): reload when nothing here is unsaved, otherwise let the user choose.
  useEvent(ctx, "developer.document_changed", payload => {
    const event = payload as {documentId: string; version: number};
    const current = docRef.current;
    if (current === null || event.documentId !== current.documentId || event.version <= current.version || savingRef.current !== "idle") return;
    if (current.dirty) {savingRef.current = "conflict"; setSaving("conflict");}
    else void reload().catch(fail);
  });

  useEffect(() => {
    void nb.environments().then(setEnvironments, fail);
    void nb.library().then(listing => setLibrary(libraryEntries(listing)), fail);
  }, [detail.id]);

  // Packages: whether this kernel runs an environment older than the installed packages. That changes when an installation ends (Quant says so)
  // and when this kernel starts again; nothing else moves it, so it is read then, not every few seconds.
  const live = useRef(true);
  useEffect(() => () => {live.current = false;}, []);
  const loadPackages = useCallback(() => void nb.packages().then(value => {if (live.current) setPackages(value);}, error => {if (live.current) fail(error);}), [nb]);
  useEffect(loadPackages, [doc?.startedAt]);
  useEvent(ctx, "quant.packages_changed", loadPackages, loadPackages);
  const workspaceId = ctx.workspace.identity.get()?.workspaceId ?? null;
  const restartForPackages = packages !== null && packages.restartKernels.some(item => item.objectId === detail.id && item.workspaceId === workspaceId);

  // While cells run, how many panels show this notebook is kept current: closing the last one stops them.
  // While the kernel is being prepared or started, the label counts the seconds.
  const waiting = doc !== null && ["none", "preparing", "starting", "restarting"].includes(doc.kernel.state);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!waiting) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [waiting]);
  const readRun = useCallback((runId: string) => api.run(runId).then(found => ({state: found.state as string, objectId: found.objectId})), [api]);
  const running = doc === null ? [] : busyCells(doc);
  useEffect(() => {
    if (running.length === 0) return;
    const timer = setInterval(() => void nb.kernel(detail.id).then(view => change(current => ({...current, kernel: {...current.kernel, panels: view.panels}})), fail), 3000);
    return () => clearInterval(timer);
  }, [running.length > 0]);
  useEffect(() => {
    host.leave.guard = (): LeaveDecision => {
      const current = docRef.current;
      if (current === null) return {decision: "allow"};
      const cells = busyCells(current);
      const others = current.kernel.panels - (current.connected ? 1 : 0);
      if (cells.length === 0 || others > 0) return {decision: "allow"};
      const lines = cells.map(cell => `• ${cell.source.split("\n").find(line => line.trim()) ?? ""}`.slice(0, 90));
      return {decision: "confirm", message: (cells.length === 1 ? t("{name} is still running a cell:\n{lines}\n\nClose it and stop the kernel? Its variables are lost.", { name: current.name, lines: lines.join("\n") }) : t("{name} is still running {cellsCount} cells:\n{lines}\n\nClose it and stop the kernel? Its variables are lost.", { name: current.name, cellsCount: cells.length, lines: lines.join("\n") }))};
    };
    return () => {host.leave.guard = null;};
  }, []);
  /** Leaving the notebook: save; closing the panel then stops the kernel if no other panel shows it and nothing runs that the user did not
   * agree to stop. Going to another page of the panel leaves the kernel running: the panel stops it when it closes. */
  const leave = () => {
    const connection = socket.current, current = docRef.current;
    const {reason, confirmed: agreed} = host.leave;
    host.leave.confirmed = false;
    if (connection === null || current === null) return;
    if (reason !== "close") host.detached.set(current.objectId, {name: current.name, running: busyCells(current).length});
    void (async () => {
      try {
        if (current.dirty && savingRef.current !== "conflict") await lasting.save(current.objectId, fileModel(current), current.version);
        if (reason !== "close") return;
        const kernel = await lasting.kernel(current.objectId);
        const others = kernel.panels - (current.connected ? 1 : 0);
        if (others <= 0 && (kernel.running.length === 0 || agreed)) await lasting.stop(current.objectId);
      } finally {connection.close();}
    })().catch(reason => console.error(t("Closing the notebook failed"), reason));
  };

  // Running
  const focusCell = (id: string) => {setActive(id); setFocus({cell: id, id: String(++sequence.current)});};
  const insertAfter = (id: string | null, type: CellType = "code") => {
    const cell = blankCell(type);
    change(current => {
      const index = id === null ? current.cells.length : current.cells.findIndex(item => item.id === id) + 1;
      return {...current, dirty: true, cells: [...current.cells.slice(0, index), cell, ...current.cells.slice(index)]};
    });
    focusCell(cell.id);
  };
  const runCells = (ids: string[]) => { if (docRef.current !== null && docRef.current.dirty) autosave.now(); runCellsNow(ids); };
  const runCellsNow = (ids: string[]) => change(current => enqueue({...current, cells: current.cells.map(cell => ids.includes(cell.id) && cell.type === "markdown" && cell.source.trim() ? {...cell, editing: false} : cell)}, ids));
  const runAll = () => runCells(docRef.current!.cells.map(cell => cell.id));
  // A notebook opened from a run reads that run in its first cell: run it once when the kernel is ready.
  const autoRan = useRef(false);
  useEffect(() => {
    const current = docRef.current;
    if (autoRan.current || current === null || !current.connected || current.kernel.state !== "idle") return;
    if ((detail.settings as {openedFrom?: unknown} | undefined)?.openedFrom === undefined) return;
    const first = current.cells[0];
    if (first === undefined || first.type !== "code" || first.executionCount !== null || first.outputs.length > 0 || !first.source.trim()) return;
    autoRan.current = true;
    runCells([first.id]);
  }, [doc?.connected, doc?.kernel.state]);
  const runAndAdvance = (id: string, insert: boolean) => {
    runCells([id]);
    const cells = docRef.current!.cells;
    const index = cells.findIndex(cell => cell.id === id);
    if (insert || index === cells.length - 1) insertAfter(id);
    else focusCell(cells[index + 1].id);
  };
  const restart = () => {
    send({op: "restart"});
    // Like the prototype: a restart starts the notebook over, outputs and counts cleared.
    change(current => ({...current, dirty: true, queue: [], cells: current.cells.map(cell => ({...cell, outputs: [], executionCount: null, note: null, status: cell.status === "queued" ? "idle" : cell.status}))}));
  };
  /** A library item's read line goes into the current cell when that is empty, otherwise into a new cell below it. */
  const insertLine = (line: string) => {
    const current = docRef.current!, at = current.cells.find(cell => cell.id === active);
    if (at !== undefined && at.type === "code" && at.source.trim() === "") {
      change(latest => ({...latest, dirty: true, cells: latest.cells.map(cell => cell.id === at.id ? {...cell, source: line} : cell)}));
      focusCell(at.id);
      return;
    }
    const cell = blankCell("code", line);
    change(latest => {
      const index = at === undefined ? latest.cells.length : latest.cells.findIndex(item => item.id === at.id) + 1;
      return {...latest, dirty: true, cells: [...latest.cells.slice(0, index), cell, ...latest.cells.slice(index)]};
    });
    focusCell(cell.id);
  };
  const openExtract = (kind = t("Factor")) => {
    if (ticked.length === 0) {setError(t("Select the cells to take: tick them in the left margin")); return;}
    setExtract({kind, preview: null, failure: null});
    // The sheet is read from the saved file, so cells typed a moment ago are saved first.
    (docRef.current !== null && docRef.current.dirty ? save() : Promise.resolve()).then(() => nb.previewExtract(detail.id, ticked, kind.toLowerCase())).then(preview => setExtract(current => current === null ? null : {...current, preview}),
      reason => setExtract(current => current === null ? null : {...current, failure: reason instanceof Error ? reason.message : String(reason)}));
  };
  const mapCell = (id: string, update: (cell: Cell) => Cell) => change(current => ({...current, dirty: true, cells: current.cells.map(cell => cell.id === id ? update(cell) : cell)}));

  const actions = useRef<Actions>(null!);
  actions.current = {
    edit: (id, value) => {const cell = docRef.current!.cells.find(item => item.id === id); if (cell !== undefined && cell.source !== value) mapCell(id, item => ({...item, source: value}));},
    focus: id => setActive(id),
    key: (id, key) => {
      if (key === "Ctrl+S") void save();
      else if (key === "Ctrl+Enter") runCells([id]);
      else runAndAdvance(id, key === "Alt+Enter");
    },
    ask: (id, kind, request) => {
      const number = ++sequence.current;
      requests.current.set(number, {cell: id, kind, requestId: request.requestId, value: request.value});
      send({op: kind, id: number, code: request.value, cursor: toCodePoints(request.value, request.offset)});
    },
    run: id => runCells([id]),
    move: (id, by) => change(current => {
      const index = current.cells.findIndex(cell => cell.id === id), target = index + by;
      if (target < 0 || target >= current.cells.length) return current;
      const cells = [...current.cells];
      [cells[index], cells[target]] = [cells[target], cells[index]];
      return {...current, cells, dirty: true};
    }),
    remove: id => change(current => ({...current, dirty: true, cells: current.cells.filter(cell => cell.id !== id)})),
    convert: (id, type) => mapCell(id, cell => ({...cell, type, outputs: [], executionCount: null, note: null, editing: true})),
    clear: id => mapCell(id, cell => ({...cell, outputs: [], executionCount: null, note: null})),
    editMarkdown: id => {mapCell(id, cell => ({...cell, editing: true})); focusCell(id);},
    toggleOutput: id => mapCell(id, cell => ({...cell, metadata: {...cell.metadata, jupyter: {...jupyterMeta(cell), outputs_hidden: !outputsHidden(cell)}}})),
    packages: () => onPackages(),
    interrupt: () => send({op: "interrupt"}),
    tick: id => setTicked(current => current.includes(id) ? current.filter(item => item !== id) : [...current, id]),
    reply: (id, value) => send({op: "input_reply", cell: id, value}),
  };
  const stable = useMemo<Actions>(() => new Proxy({} as Actions, {get: (_target, name: keyof Actions) => (...args: never[]) => (actions.current[name] as (...items: never[]) => void)(...args)}), []);

  if (doc === null) return <div className="qx-page"><div className="qx-bar">{title}</div>
    {error !== null ? <div className="qx-page-error" role="alert"><span>{error}</span></div> : <p className="qx-muted qx-pad">{t("Opening notebook…")}</p>}</div>;
  const busy = running.length > 0;
  const source = (detail.settings as {openedFrom?: OpenedFrom} | undefined)?.openedFrom;
  const opened: OpenedFrom | null = source === undefined ? null : {...source, deleted: runGone, objectName: detail.name.replace(/ #\d+$/, ''), number: Number(/#(\d+)$/.exec(detail.name)?.[1]) || undefined};
  const kernelBase = kernelLabel(doc, environments.find(item => item.id === (doc.kernel.environmentId ?? "research"))?.device, now);
  const kernel = deepWork === null ? kernelBase : {text: deepWork.text, tone: deepWork.failed ? "off" as const : "wait" as const};
  return <WidgetBusContext.Provider value={bus}><OpenRunContext.Provider value={onOpenObject}><RunStateContext.Provider value={readRun}><div className="qx-page">
    <div className="qx-bar qv-notebook-toolbar">
      {title}<span className="fx-kind">{t("Notebook")}</span>
      <span className="tx-spacer" />
      {environments.length > 0 && <span className="qv-notebook-env"><TxSelect value={doc.kernel.environmentId ?? "research"} onChange={event => setEnvAsk(environments.find(item => item.id === event.target.value) ?? null)}>
        {environments.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</TxSelect></span>}
      {deepWork?.failed === true && envAsk === null && <TxButton size="sm" variant="quiet" onClick={() => {const wanted = environments.find(item => item.id === "deep"); if (wanted) setEnvAsk(wanted);}}>{t("Retry")}</TxButton>}
      {saving === "conflict" && <span className="qx-nb-saved qx-muted">{t("Not saved")}</span>}
      <TxMenu label={t("Notebook actions")} align="end" items={[
        // Save while there is something unsaved (autosave keeps up otherwise); after a conflict the strip offers the other version instead.
        ...(doc.dirty && saving !== "conflict" ? [{id: "save", label: t("Save"), icon: "check" as const, shortcut: "Ctrl+S", disabled: saving === "saving", onSelect: () => void save()}] : []),
        {id: "clear", label: t("Clear all outputs"), icon: "close", onSelect: () => change(current => ({...current, dirty: true, cells: current.cells.map(cell => ({...cell, outputs: [], executionCount: null, note: null}))}))},
        {id: "common", label: t("Open common code"), icon: "folder", detail: t("Modules every workspace's code can import"), separated: true, onSelect: () => void openCommonCode(ctx, api).catch(fail)},
        {id: "developer", label: t("Open file in Developer"), icon: "external", onSelect: () => void ctx.platform.panels.openTarget({module: "developer", panelType: "main", objectId: `file:${detail.entryUri}`}).catch(fail)},
        ...menu]} trigger={<TxIconButton label={t("Notebook actions")} icon="more" />} />
    </div>
    <Toolbar status={<span className="qx-nb-kernel" data-state={kernel.tone}>{kernel.tone === "wait" ? <span className="qx-spin" /> : <span className="qx-nb-dot" />}{kernel.text}</span>} busy={busy} canRestart={doc.kernel.state !== "restarting" && doc.connected} selected={ticked.length}
      onRunAll={runAll} onInterrupt={() => send({op: "interrupt"})} onRestart={() => {if (busy) setRestartAsk(true); else restart();}}
      onAdd={type => insertAfter(null, type)} onExtract={() => openExtract()} onClear={() => setTicked([])} />
    {opened !== null && <RunBand from={opened} onOpen={() => onOpenObject(opened.objectId, opened.runId)} />}
    {doc.kernel.state === "dead" && doc.kernel.message !== null && <div className="qx-page-error" role="alert"><span>{doc.kernel.message}</span>
      <TxButton size="sm" onClick={() => send({op: "restart"})}>{t("Restart kernel")}</TxButton></div>}
    {restartForPackages && doc.kernel.state !== "dead" && <div className="qx-nb-strip"><TxIcon name="box" /><span>{t("Packages changed. Restart the kernel to use them.")}</span>
      <TxButton size="sm" disabled={busy} onClick={restart}>{t("Restart")}</TxButton></div>}
    {saving === "conflict" && <div className="qx-nb-strip" data-tone="warn" role="alert"><span>{t("This notebook changed outside this panel since it was opened.")}</span>
      <TxButton size="sm" onClick={() => void reload(true).catch(fail)}>{t("Load the other version")}</TxButton>
      <TxButton size="sm" variant="quiet" onClick={() => void keepMine().catch(fail)}>{t("Keep this one")}</TxButton></div>}
    {error !== null && <div className="qx-page-error" role="alert"><span>{error}</span><TxButton size="sm" variant="quiet" onClick={() => setError(null)}>{t("Dismiss")}</TxButton></div>}
    <div className="qv-body">
      <div className="qx-nb" role="list" aria-label={t("Cells")}>
        {doc.cells.map(cell => <NotebookCell key={cell.id} ctx={ctx} cell={cell} active={cell.id === active} actions={stable} ticked={ticked.includes(cell.id)} ticking={ticked.length > 0}
          input={doc.inputs[cell.id]} answer={answers[cell.id]} focusRequestId={focus !== null && focus.cell === cell.id ? focus.id : undefined} />)}
        <div className="qx-nb-add">
          <TxButton size="sm" variant="quiet" onClick={() => insertAfter(null, "code")}><TxIcon name="plus" />{t("Code")}</TxButton>
          <TxButton size="sm" variant="quiet" onClick={() => insertAfter(null, "markdown")}><TxIcon name="plus" />{t("Text")}</TxButton>
        </div>
      </div>
      {side !== null && <SidePanel side={side} variables={doc.variables} loaded={doc.loaded} opened={doc.opened} library={library} onInsert={insertLine}
        onOpenVariable={name => send({op: "variable", name})} onCloseVariable={() => change(current => ({...current, opened: null}))}
        kernelLine={[doc.startedAt === null ? "" : t("Started {time} UTC", { time: new Date(doc.startedAt).toISOString().slice(11, 16) }), doc.memory === null ? "" : t("{memory} GB in use", { memory: (doc.memory / 1024 ** 3).toFixed(1) })].filter(Boolean).join(" · ")} />}
      <Rail side={side} onSide={setSide} />
    </div>
    {envAsk !== null && <TxDialog open title={t("Change the kernel's environment?")} onOpenChange={open => {if (!open) setEnvAsk(null);}}
      footer={<><TxButton onClick={() => setEnvAsk(null)}>{t("Cancel")}</TxButton><TxButton variant="primary" onClick={() => {
        const wanted = envAsk; setEnvAsk(null);
        if (wanted.id === "deep" && !wanted.installed) void installDeep(deepChoice, wanted).catch(fail);
        else void nb.setEnvironment(detail.id, wanted.id).catch(fail);
      }}>{envAsk.id === "deep" && !envAsk.installed ? t("Install and restart") : t("Restart")}</TxButton></>}>
      {envAsk.id === "deep" && !envAsk.installed && deepAsk?.offer.choices && <div role="radiogroup" aria-label={t("Build")}>{deepAsk.offer.choices.map(variant => <label key={variant} style={{marginRight: 12}}>
        <input type="radio" name="nb-deep-build" checked={deepChoice === variant} onChange={() => setDeepChoice(variant)} /> {variant === "cpu" ? "CPU" : t("Intel GPU")}</label>)}</div>}
      <p>{(envAsk.id === "deep" && !envAsk.installed && deepAsk !== null ? t("Install the deep-learning environment ({label}, {size}) and restart the kernel in it? Variables are cleared; outputs stay.", { label: deepAsk.offer.label, size: deepAsk.offer.size }) : (envAsk.id === "deep" ? t("Restart the kernel in the deep-learning environment? Variables are cleared; outputs stay.") : t("Restart the kernel in the research environment? Variables are cleared; outputs stay.")))}{busy ? t(" Cell [{running}] is running and will be stopped.", { running: running.map(cell => cell.executionCount ?? "*").join(", ") }) : ""}</p>
    </TxDialog>}
    {extract !== null && <ExtractSheet open count={ticked.length} total={doc.cells.filter(cell => cell.type === "code").length} kind={extract.kind} preview={extract.preview} failure={extract.failure}
      onKind={kind => openExtract(kind)} onClose={() => setExtract(null)}
      onCreate={name => void nb.createFromNotebook(detail.id, ticked, extract.kind.toLowerCase(), name).then(made => {
        setExtract(null); setTicked([]);
        onOpenObject(made.id);
      }, reason => setExtract(current => current === null ? null : {...current, failure: reason instanceof Error ? reason.message : String(reason)}))} />}
    {restartAsk && <TxDialog open title={t("Restart the kernel?")} onOpenChange={setRestartAsk}
      footer={<><TxButton onClick={() => setRestartAsk(false)}>{t("Cancel")}</TxButton><TxButton variant="primary" onClick={() => {setRestartAsk(false); restart();}}>{t("Restart")}</TxButton></>}>
      <p>{t("The running cells are stopped, every variable is lost and the outputs are cleared.")}</p>
    </TxDialog>}
  </div></RunStateContext.Provider></OpenRunContext.Provider></WidgetBusContext.Provider>;
}

const jupyterMeta = (cell: Cell) => (cell.metadata.jupyter !== null && typeof cell.metadata.jupyter === "object" && !Array.isArray(cell.metadata.jupyter) ? cell.metadata.jupyter : {}) as Record<string, Json>;
const outputsHidden = (cell: Cell) => jupyterMeta(cell).outputs_hidden === true;

/** The kernel's state in a word, and whether to wait for it. A first start may prepare the environment for a minute or more: the wait counts its seconds
 *  and names the platform's step (65 s under one unchanging line). */
function kernelLabel(doc: Doc, device: string | undefined, now: number): {text: string; tone: "ok" | "busy" | "wait" | "off"} {
  const environment = doc.kernel.environment;
  const python = environment !== null && environment.pythonVersion !== undefined ? `Python ${environment.pythonVersion}` : "Python 3.12";
  const where = device === undefined ? "" : ` · ${device}`;
  if (!doc.connected) return {text: `${python} · ${doc.kernel.sessionId === null ? "connecting" : "reconnecting"}`, tone: "wait"};
  const waited = doc.kernel.since === undefined ? "" : ` · ${Math.max(0, Math.round((now - doc.kernel.since) / 1000))} s`;
  switch (doc.kernel.state) {
  case "none": case "preparing": {
    const preparation = environment === null ? null : environment.preparation;
    const step = preparation === null ? null : preparation.message ?? (preparation.state === "queued" ? t("Waiting to prepare the research environment") : null);
    return {text: (step ?? t("Preparing the research environment")) + waited, tone: "wait"};
  }
  case "starting": return {text: t("{python} · starting{waited}", { python, waited }), tone: "wait"};
  case "restarting": return {text: t("{python} · restarting{waited}", { python, waited }), tone: "wait"};
  case "dead": return {text: (doc.kernel.message !== null ? t("{python} · stopped: {message}", { python, message: doc.kernel.message.split("\n").slice(-3).join(" ") }) : t("{python} · stopped", { python })), tone: "off"};
  default:
    if (doc.restarted && busyCells(doc).length === 0) return {text: t("{python} · kernel restarted{where}", { python, where }), tone: "ok"};
    const busy = busyCells(doc).length > 0 || doc.kernel.state === "busy";
  return {text: busy ? t("{python} · busy{where}", {python, where}) : t("{python} · idle{where}", {python, where}), tone: busy ? "busy" : "ok"};
  }
}

const NotebookCell = memo(function NotebookCell({ctx, cell, active, actions, answer, focusRequestId, ticked, ticking, input}: {
  ctx: ModuleContext; cell: Cell; active: boolean; actions: Actions; answer: Answer | undefined; focusRequestId: string | undefined;
  ticked: boolean; ticking: boolean; input: {prompt: string; password: boolean} | undefined;
}) {
  const id = cell.id;
  const on = useMemo(() => ({
    changed: (payload: Json) => actions.edit(id, (payload as {value: string}).value),
    focus: (payload: Json) => {if ((payload as {focused: boolean}).focused) actions.focus(id);},
    key: (payload: Json) => actions.key(id, (payload as {key: Key}).key),
    complete: (payload: Json) => actions.ask(id, "complete", payload as {requestId: string; value: string; offset: number}),
    inspect: (payload: Json) => actions.ask(id, "inspect", payload as {requestId: string; value: string; offset: number}),
  }), [id, actions]);
  const language = cell.type === "code" ? "python" : cell.type === "markdown" ? "markdown" : "plaintext";
  const props = useMemo(() => ({value: cell.source, language, autoHeight: true, keys: KEYS, assist: cell.type === "code",
    ...(answer !== undefined ? {answer} : {}), ...(focusRequestId !== undefined ? {focusRequestId} : {})} satisfies EditorProps as unknown as Json),
  [cell.source, language, cell.type, answer, focusRequestId]);
  const prompt = cell.type !== "code" ? "" : cell.status !== "idle" ? "[*]:" : cell.executionCount !== null ? `[${cell.executionCount}]:` : "[ ]:";
  const result = cell.outputs.find(output => output.output_type === "execute_result");
  const hidden = outputsHidden(cell);
  const rendered = cell.type === "markdown" && !cell.editing;
  return <div className="qx-nb-cell" role="listitem" data-active={active} data-status={cell.status} data-ticked={ticked ? "" : undefined} data-ticking={ticking ? "" : undefined} onPointerDown={() => actions.focus(id)}>
    <div className="qx-nb-prompt">{prompt}</div>
    {cell.type === "code" && <span className="qv-notebook-tick" onPointerDown={event => event.stopPropagation()}><TxCheckbox checked={ticked} onCheckedChange={() => actions.tick(id)} name="Select for Extract to new object" /></span>}
    <div className="qx-nb-source" data-kind={cell.type}>
      {rendered ? <div className="qx-nb-markdown qx-nb-rendered" onDoubleClick={() => actions.editMarkdown(id)} title={t("Double-click to edit")}
        dangerouslySetInnerHTML={{__html: renderMarkdown(cell.source, cell.attachments)}} />
        : <div className="qx-nb-editor"><ModuleView id="developer.text_editor" context={ctx} props={props} on={on} /></div>}
      <div className="qx-nb-tools">
        <TxIconButton size="sm" icon="play" label={cell.type === "code" ? t("Run cell (Shift+Enter)") : t("Show (Shift+Enter)")} onClick={() => actions.key(id, "Shift+Enter")} />
        <TxIconButton size="sm" icon="arrow-up" label={t("Move up")} onClick={() => actions.move(id, -1)} />
        <TxIconButton size="sm" icon="arrow-down" label={t("Move down")} onClick={() => actions.move(id, 1)} />
        <TxMenu label={t("Cell actions")} align="end" items={[
          ...(rendered ? [{id: "edit", label: t("Edit"), icon: "edit" as const, onSelect: () => actions.editMarkdown(id)}] : []),
          cell.type === "code" ? {id: "markdown", label: t("Change to text"), icon: "file", onSelect: () => actions.convert(id, "markdown")}
            : {id: "code", label: t("Change to code"), icon: "code", onSelect: () => actions.convert(id, "code")},
          ...(cell.type === "code" && cell.outputs.length > 0 ? [{id: "clear", label: t("Clear output"), icon: "close" as const, onSelect: () => actions.clear(id)}] : []),
          {id: "delete", label: t("Delete cell"), icon: "trash", tone: "danger", separated: true, onSelect: () => actions.remove(id)},
        ]} trigger={<TxIconButton size="sm" icon="more" label={t("Cell actions")} />} />
      </div>
    </div>
    {cell.type === "code" && (cell.outputs.length > 0 || cell.note !== null || input !== undefined) && <>
      <button type="button" className="qx-nb-prompt qx-nb-out-prompt" onClick={() => actions.toggleOutput(id)} title={hidden ? t("Show output") : t("Hide output")}>
        {result !== undefined && !hidden ? t("Out[{executionCount}]:", { executionCount: cell.executionCount !== null ? cell.executionCount : " " }) : ""}</button>
      <div className="qx-nb-outputs">
        {hidden ? <button type="button" className="qx-nb-hidden" onClick={() => actions.toggleOutput(id)}>{t("Output hidden · show")}</button>
          : cell.outputs.map((output, index) => <NotebookOutput key={index} output={output} onStop={actions.interrupt} live={cell.status === "running" || cell.status === "queued"} />)}
        {input !== undefined && <InputLine prompt={input.prompt} password={input.password} onReply={value => actions.reply(id, value)} />}
        {cell.note !== null && (cell.note.kind === "interrupted" ? <p className="qx-nb-note">{t("Interrupted")}</p>
          : <div className="qx-nb-note" data-tone="warn" role="alert"><span>{cell.note.message}</span>
            {cell.note.code === "pip_blocked" && <TxButton size="sm" onClick={actions.packages}><TxIcon name="box" />{t("Open packages")}</TxButton>}</div>)}
      </div>
    </>}
  </div>;
});

/** The line a cell's output ends with while its code waits for input(): the prompt, and a box that has the focus; Enter sends it. */
function InputLine({prompt, password, onReply}: {prompt: string; password: boolean; onReply(value: string): void}) {
  const [value, setValue] = useState("");
  return <div className="qv-notebook-input"><span>{prompt}</span>
    <TxInput autoFocus type={password ? "password" : "text"} value={value} onChange={event => setValue(event.target.value)} onKeyDown={event => {if (event.key === "Enter") onReply(value);}} /></div>;
}
