import { describe, expect, it } from "vitest";
import { ansiRuns, carriage, stripAnsi } from "./ansi";
import { chooseMime } from "./NotebookOutput";
import {
  appendOutput, disconnected, enqueue, fileModel, nextToSend, openDoc, receive, refused, toCodePoints, toUnits,
  type Doc, type KernelView, type NotebookRead,
} from "./notebook";
import { addRequirement, removeRequirement, requirementProblem } from "./requirements";

const kernel = (patch: Partial<KernelView> = {}): KernelView =>
  ({objectId: "n", sessionId: "s1", state: "idle", message: null, busy: false, running: [], panels: 1, environment: null, ...patch});
function notebook(...sources: string[]): Doc {
  const read: NotebookRead = {objectId: "n", name: "Scratch", version: 3, documentId: "d", kernel: kernel(),
    notebook: {metadata: {}, cells: sources.map((source, index) => ({id: `c${index}`, type: "code", source, outputs: [], executionCount: null, metadata: {}}))}};
  return {...openDoc(read), connected: true};
}
/** Sends what the queue would send, as the page does after every change. */
const pump = (doc: Doc) => {const step = nextToSend(doc); return step === null ? doc : step.doc;};

describe("Notebook kernel messages", () => {
  it("shows action results while the action owns the file write", () => {
    for (const dirty of [false, true]) {
      let doc = {...notebook("6 * 7"), dirty};
      doc = receive(doc, {type: "accepted", cell: "c0", msgId: "m", savedByAction: true});
      doc = receive(doc, {type: "output", cell: "c0", msgId: "m", savedByAction: true,
        output: {output_type: "execute_result", data: {"text/plain": "42"}, metadata: {}}});
      doc = receive(doc, {type: "reply", cell: "c0", msgId: "m", savedByAction: true, status: "ok", executionCount: 1});
      expect(doc.cells[0].executionCount).toBe(1);
      expect(doc.cells[0].outputs).toHaveLength(1);
      expect(doc.dirty).toBe(dirty);
    }
  });
  it("runs a panel's queue one cell at a time and stops it at a failed cell", () => {
    let doc = pump(enqueue(notebook("a = 1", "b = a", "  ", "c = b"), ["c0", "c1", "c2", "c3"]));
    expect(doc.sent).toEqual({cell: "c0", code: "a = 1", msgId: null});
    expect(doc.cells.map(cell => cell.status)).toEqual(["queued", "queued", "idle", "queued"]);
    doc = receive(doc, {type: "accepted", cell: "c0", msgId: "m0"});
    doc = receive(doc, {type: "reply", cell: "c0", msgId: "m0", status: "ok", executionCount: 1});
    doc = pump(doc);
    expect(doc.sent).toEqual({cell: "c1", code: "b = a", msgId: null});
    doc = receive(doc, {type: "accepted", cell: "c1", msgId: "m1"});
    doc = receive(doc, {type: "output", cell: "c1", msgId: "m1", output: {output_type: "error", ename: "NameError", evalue: "a", traceback: []}});
    doc = receive(doc, {type: "reply", cell: "c1", msgId: "m1", status: "error", executionCount: 2});
    expect(doc.queue).toEqual([]);
    expect(doc.cells.map(cell => [cell.status, cell.executionCount])).toEqual([["idle", 1], ["idle", 2], ["idle", null], ["idle", null]]);
    expect(doc.cells[1].outputs).toHaveLength(1);
  });

  it("joins stream text, redraws carriage returns and replaces a display by its id", () => {
    let doc = receive(pump(enqueue(notebook("x"), ["c0"])), {type: "accepted", cell: "c0", msgId: "m"});
    for (const text of ["10%\r", "50%\r", "100%\ndone\n"]) doc = receive(doc, {type: "output", cell: "c0", msgId: "m", output: {output_type: "stream", name: "stdout", text}});
    expect(doc.cells[0].outputs).toEqual([{output_type: "stream", name: "stdout", text: "100%\ndone\n"}]);
    doc = receive(doc, {type: "output", cell: "c0", msgId: "m", displayId: "p", output: {output_type: "display_data", data: {"text/plain": "1"}, metadata: {}}});
    doc = receive(doc, {type: "output_update", cell: "c0", msgId: "m", displayId: "p", output: {output_type: "display_data", data: {"text/plain": "2"}, metadata: {}}});
    expect(doc.cells[0].outputs[1]).toEqual({output_type: "display_data", data: {"text/plain": "2"}, metadata: {}, displayId: "p"});
    expect(fileModel(doc).cells[0].outputs[1]).toEqual({output_type: "display_data", data: {"text/plain": "2"}, metadata: {}});
    doc = receive(doc, {type: "clear_output", cell: "c0", msgId: "m", wait: true});
    expect(doc.cells[0].outputs).toHaveLength(2);
    doc = receive(doc, {type: "output", cell: "c0", msgId: "m", output: {output_type: "stream", name: "stdout", text: "fresh"}});
    expect(doc.cells[0].outputs).toEqual([{output_type: "stream", name: "stdout", text: "fresh"}]);
  });

  it("says the kernel restarted when Quant replaced it, not when the user restarted it, until a cell runs", () => {
    const unasked = receive(notebook("x = 1"), {type: "state", ...kernel({sessionId: "s2"})});
    expect(unasked.restarted).toBe(true);
    expect(receive(unasked, {type: "accepted", cell: "c0", msgId: "m"}).restarted).toBe(false);
    const asked = receive(notebook("x = 1"), {type: "state", ...kernel({state: "restarting"})});
    expect(receive(asked, {type: "state", ...kernel({sessionId: "s2"})}).restarted).toBe(false);
  });

  it("marks cells interrupted when the kernel's session changes, and shows cells another panel runs", () => {
    let doc = receive(pump(enqueue(notebook("while True: pass", "y = 1"), ["c0", "c1"])), {type: "accepted", cell: "c0", msgId: "m"});
    doc = receive(doc, {type: "state", ...kernel({sessionId: "s2", state: "starting"})});
    expect(doc.cells.map(cell => [cell.status, cell.note])).toEqual([["idle", {kind: "interrupted"}], ["idle", {kind: "interrupted"}]]);
    expect([doc.sent, doc.queue]).toEqual([null, []]);
    doc = receive(doc, {type: "state", ...kernel({sessionId: "s2", busy: true, running: [{cell: "c1", msgId: "other", startedAt: 0, preview: "y = 1"}]})});
    expect(doc.cells[1]).toMatchObject({status: "running", msgId: "other", note: null});
    const same = receive(doc, {type: "state", ...kernel({sessionId: "s2", busy: true, running: [{cell: "c1", msgId: "other", startedAt: 0, preview: "y = 1"}]})});
    expect(same.cells).toBe(doc.cells);
  });

  it("keeps a refused cell and the cells behind it unrun, and stops the queue when the socket drops before a cell is accepted", () => {
    const sent = pump(enqueue(notebook("%pip install ta", "x"), ["c0", "c1"]));
    const doc = refused(sent, "c0", "pip_blocked", "Use Packages");
    expect(doc.cells.map(cell => [cell.status, cell.note])).toEqual([["idle", {kind: "blocked", code: "pip_blocked", message: "Use Packages"}], ["idle", null]]);
    expect(disconnected(sent)).toMatchObject({connected: false, sent: null, queue: []});
    expect(disconnected(sent).cells.map(cell => cell.status)).toEqual(["idle", "idle"]);
  });
});

describe("Notebook output text", () => {
  it("reads colour codes into runs and strips them", () => {
    const text = "\x1b[0;31mValueError\x1b[0m: bad \x1b[1;38;5;196mx\x1b[0m";
    expect(ansiRuns(text)).toEqual([
      {text: "ValueError", fg: "1", bg: null, bold: false, dim: false, italic: false, underline: false},
      {text: ": bad ", fg: null, bg: null, bold: false, dim: false, italic: false, underline: false},
      {text: "x", fg: "rgb(255,0,0)", bg: null, bold: true, dim: false, italic: false, underline: false},
    ]);
    expect(stripAnsi(text)).toBe("ValueError: bad x");
  });
  it("redraws a line after a carriage return but keeps a trailing one for the next piece", () => {
    expect(carriage("a\rb\nc\r")).toBe("b\nc\r");
    expect(carriage("x\r\ny")).toBe("x\ny");
    expect(appendOutput([{output_type: "stream", name: "stderr", text: "1/3\r"}], {output_type: "stream", name: "stderr", text: "3/3\n"}))
      .toEqual([{output_type: "stream", name: "stderr", text: "3/3\n"}]);
  });
  it("chooses the richest output, with a widget drawn as one", () => {
    expect(chooseMime({"text/plain": "fig", "image/png": "..", "text/html": "<b>"})).toBe("text/html");
    expect(chooseMime({"text/plain": "Widget", "application/vnd.jupyter.widget-view+json": {}})).toBe("application/vnd.jupyter.widget-view+json");
    expect(chooseMime({"application/vnd.plotly.v1+json": {}, "text/html": ""})).toBe("application/vnd.plotly.v1+json");
  });
  it("counts the kernel's cursor in code points", () => {
    expect(toCodePoints("𝑥 = 1", 3)).toBe(2);
    expect(toUnits("𝑥 = 1", 2)).toBe(3);
  });
});

describe("Package list", () => {
  it("adds and removes requirements by package name, keeping comments", () => {
    const file = "# header\nPandas_TA==0.3\n";
    expect(addRequirement(file, " ta>=0.11 ")).toBe("# header\nPandas_TA==0.3\nta>=0.11\n");
    expect(removeRequirement("# header\nPandas_TA==0.3  # pinned\nta\n", "pandas-ta")).toBe("# header\nta\n");
    expect(requirementProblem("pandas-ta", ["Pandas_TA==0.3"])).toBe("pandas-ta is already in the list.");
    expect(requirementProblem("ta >= 0.11", [])).toBeNull();
    expect(requirementProblem("--index-url x", [])).not.toBeNull();
    expect(requirementProblem("numpy pandas", [])).not.toBeNull();
  });
});

describe("Notebook side panel and input messages", () => {
  it("the kernel's variables replace the list, and a restart empties it", () => {
    let doc = notebook("a = 1");
    doc = receive(doc, {type: "variables", variables: [{name: "a", type: "int", shape: null, size: null, fromCell: 1}]});
    expect(doc.variables.map(item => item.name)).toEqual(["a"]);
    doc = receive(doc, {type: "state", ...kernel({sessionId: "s2"})});
    expect(doc.variables).toEqual([]);
  });
  it("input_request shows a box on its cell and input_done takes it away", () => {
    let doc = receive(notebook("x = input()"), {type: "input_request", cellId: "c0", prompt: "n? ", password: false});
    expect(doc.inputs).toEqual({c0: {prompt: "n? ", password: false}});
    doc = receive(doc, {type: "input_done", cellId: "c0", text: "n? 3"});
    expect(doc.inputs).toEqual({});
  });
});


it("keeps a Markdown attachment when another cell moves before it", () => {
  const doc = notebook("![cash](attachment:cash.svg)", "42");
  const attached = {"cash.svg": {"image/svg+xml": "<svg/>"}};
  const markdown = {...doc.cells[0], type: "markdown" as const, attachments: attached};
  expect(fileModel({...doc, cells: [doc.cells[1], markdown]}).cells[1].attachments).toEqual(attached);
});
