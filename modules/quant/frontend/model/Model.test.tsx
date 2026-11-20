// M section, F1: the model page's rules, on minimal objects (section 7's contract, written by hand). Each name is one from sections 3 and 4.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { CheckBand } from "./CheckBand";
import { ComparePane } from "./ComparePane";
import { COLUMNS, Experiments, tiers } from "./Experiments";
import { algoLabel, headSpan, heldOutUses, latestOnly, pickSelected, prepWidth, runLabel, selHeight } from "./format";
import { AlgorithmSelect, Prep, visible } from "./Prep";
import { Selected } from "./Selected";
import { decimals } from "./results/ValuesTab";
import type { ModelApi, ModelRun } from "./api";

const source = (file: string) => readFileSync(new URL(file, import.meta.url), "utf8");
const run = (over: Partial<ModelRun> = {}): ModelRun => ({
  id: "r1", objectId: "o", number: 12, kind: "train", batch: null, group: null, status: "completed", state: "completed", stage: "done", createdAt: 0, started: 0, finished: 1, time: 600,
  mode: "standard", algo: "lightgbm", label: "5D rank", inputs: [], params: {label: "CSRank(Ref($open, -6) / Ref($open, -1) - 1)"}, values: {}, run: {cost: 5, topN: 50}, progress: null, queue: null,
  failure: null, stopped: null, windowsTotal: 23, windowsDone: 23, windows: [], perWindow: true, metrics: {ric: 0.0123, t: 2.1, lsNet: 0.04, turnover: 0.3, trainIc: 0.05, windows: 23, positive: 15},
  baselines: null, checks: [], review: 0, passed: 7, combinedFrom: null, of: null, ofNumber: null, dataTo: Date.UTC(2026, 9, 30), dataFrom: null, memory: null, estimate: null, starred: false,
  note: "", published: null, heldOut: null, message: null, overrides: {}, pipeline: null, horizon: 5, heldOutFrom: null, ...over,
} as unknown as ModelRun);
const mapi = {figures: vi.fn(), compareFigure: vi.fn()} as unknown as ModelApi;
const noop = () => undefined;
const selected = (self: ModelRun, compare: ModelRun | null) => renderToStaticMarkup(createElement(Selected, {mapi, run: self, compare, onStop: noop, onResume: noop, onLog: noop, onGo: noop, onStrategy: noop,
  onPublish: noop, onHeldOut: noop, error: noop}));
const table = (runs: ModelRun[], extra: Record<string, unknown> = {}) => renderToStaticMarkup(createElement(Experiments, {view: "table", onView: noop, list: {items: runs, batches: [], running: [], now: 0, complete: true} as never,
  runs, selectedId: null, compareId: null, ticked: new Set<string>(), filter: "all", query: "", sort: "ric", byBatch: true, onFilter: noop, onQuery: noop, onSort: noop, onByBatch: noop, onSelect: noop,
  onTick: noop, onStar: noop, onNext: noop, onRemove: noop, onResume: noop, onOpen: noop, onCompare: noop, onCombine: noop, ...extra}));

describe("model page", () => {
  it("new model", () => {
    const markup = renderToStaticMarkup(createElement(CheckBand, {check: null, checking: false, width: "wide", onGo: noop}));
    expect(markup).toContain("waiting for a universe, inputs and an algorithm");
  });
  it("queued row", () => {
    const markup = table([run({status: "queued", state: "queued", queue: {position: 2}, metrics: null, windowsDone: 0})]);
    expect(markup).toContain("2nd in the queue");
    expect(markup).toContain("Run next");
    expect(markup).toContain("Remove");
  });
  it("a queued or running run's row marks a resume after Quant closed", () => {
    expect(table([run({status: "running", state: "running", resumedAfterClose: true, progress: {window: 6}, metrics: null} as never)])).toContain("Resumed after Quant closed · Window");
  });
  it("a resume with an override says from which window", () => {
    expect(table([run({overrides: {lgb_depth: 8}, overrideFrom: 10})])).toContain("changed from window 10: depth 8");
  });
  it("publish appears only on completed rows", () => {
    expect(selected(run(), null)).toContain(">Publish<");
    for (const status of ["running", "failed", "queued", "stopped"] as const) expect(selected(run({status, state: status}), null), status).not.toContain(">Publish<");
  });
  it("different label horizons give no delta", () => {
    const text = selected(run({horizon: 5}), run({id: "r2", number: 5, horizon: 10}));
    expect(text).toContain("#5 predicts 10 bars ahead");
    expect(text).not.toContain("vs #5");
    expect(selected(run({horizon: 5}), run({id: "r2", number: 5, horizon: 5}))).toContain("vs #5");
  });
  it("compare names different data ends", () => {
    const markup = renderToStaticMarkup(createElement(ComparePane, {mapi, runs: [run(), run({id: "r2", number: 5, dataTo: Date.UTC(2026, 8, 30)})], onClose: noop, error: noop}));
    expect(markup).toContain("data to different days");
    expect(markup).toContain("Data to");
  });
  it("compare shows each label formula", () => {
    const markup = renderToStaticMarkup(createElement(ComparePane, {mapi, runs: [run(), run({id: "r2", number: 5, params: {label: "Ref($close, -21) / Ref($close, -1) - 1"}})], onClose: noop, error: noop}));
    expect(markup).toContain("CSRank(Ref($open, -6) / Ref($open, -1) - 1)");
    expect(markup).toContain("Ref($close, -21) / Ref($close, -1) - 1");
  });
  it("concurrency menu lists only what fits", () => {
    const GB = 1024 ** 3, machine = {concurrency: 2, cpus: 8, memoryUsed: 3, memoryTotal: 25.6, budget: 12.8, running: 1, waiting: 0};
    expect(tiers(machine, 5 * GB).map(item => item.count)).toEqual([1, 2]);           // 3 at 5 GB would need 15 GB of a 12.8 GB budget
    expect(tiers(machine, 5 * GB)[1].label).toBe("2 at a time · about 10 GB of 12.8 GB");
    expect(tiers({...machine, budget: 1}, 5 * GB).map(item => item.count)).toEqual([1]); // one always runs
    expect(tiers({...machine, cpus: 2}, 1 * GB).map(item => item.count)).toEqual([1, 2]); // never more than the cores
  });
  it("algorithm list has no greyed rows", () => {
    const algorithms = [{id: "lightgbm", label: "LightGBM", groupLabel: "Trees", objectives: []}, {id: "ridge", label: "Ridge", groupLabel: "Linear", objectives: []}];
    const markup = renderToStaticMarkup(createElement(AlgorithmSelect, {algorithms: algorithms as never, value: "", onChange: noop}));
    expect(markup).toContain("Trees");
    expect(markup).toContain("Linear");
    expect(markup).not.toContain("disabled");
    expect(markup).not.toContain("Neural");  // the neural-network group is D's, and is not here unless it can run
  });
  const prep = {objectId: "o", name: "m", mode: "standard", ownCode: false, values: {algorithm: "lightgbm", engine_params: "min_leaf=50"}, offers: [], batchLimit: 20, running: {}, version: 1,
    run: {cost: 5, topN: 50}, data: {}, algorithms: [{id: "lightgbm", label: "LightGBM", groupLabel: "Trees", objectives: [{id: "mse", label: "Regression · L2", weights: true}]}],
    groups: [{name: "Algorithm", settings: [{name: "algorithm", label: "Algorithm", type: "choice", default: "lightgbm", options: []},
      {name: "engine_params", label: "Engine parameters", type: "text", default: ""}]}, {name: "Inputs", settings: [{name: "inputs", label: "Inputs", type: "list", default: []}]}]};
  const renderPrep = (width: "narrow" | "wide" | "full", check: unknown) => renderToStaticMarkup(createElement(Prep, {prep: prep as never, values: prep.values as never, base: null, baseNumber: null, width, check: check as never,
    checking: false, vary: [], onVary: noop, onChange: noop, onRevert: noop, baseRun: null, onRun: noop, onHeldOut: noop, onReset: noop, onWidth: noop, onWriteCode: noop, onGo: noop, fields: null, inspect: (() => Promise.resolve({})) as never}));
  it("engine parameters name the unknown key", () => {
    const markup = renderPrep("wide", {ok: true, blocked: true, checks: [], engineParams: {ok: false, errors: [{key: "min_leaf", message: "min_leaf: LightGBM has no such parameter"}]}});
    expect(markup).toContain("Engine parameters");
    expect(markup).toContain("min_leaf: LightGBM has no such parameter");
    expect(renderPrep("wide", {ok: true, blocked: false, checks: [], engineParams: {ok: true, errors: []}})).not.toContain("has no such parameter");  // a right one is not echoed back
  });
  it("new model opens wide with every group open", () => {
    const text = source("./Prep.tsx");
    expect(text).toContain('defaultOpen={width !== "narrow"}');
    expect(source("./ModelPage.tsx")).toContain('list !== null && runs.length === 0 ? "wide" : "narrow"');
  });
  it("key numbers open their results tab", () => {
    const text = source("./Selected.tsx");
    for (const [label, tab] of [["Test Rank IC", "information"], ["t", "information"], ["vs best input", "robustness"], ["L–S net", "returns"], ["Turnover", "turnover"], ["Train − test", "windows"], ["Windows above 0", "windows"]])
      expect(text, label).toMatch(new RegExp(`label: "${label}"[\\s\\S]{0,160}?tab: "${tab}"`));
    expect(text).not.toMatch(/label: "Checks"[^}]*tab:/);
  });
  it("a finished batch does not select its best group", () => {
    // newest first, as the list comes: #14 finished last with the lower IC, #13 is the batch's best
    const runs = [run({id: "a", number: 14, metrics: {ric: 0.01} as never}), run({id: "b", number: 13, metrics: {ric: 0.05} as never}), run({id: "c", number: 12, status: "running", state: "running"})];
    expect(pickSelected(runs, null)?.number).toBe(14);
    expect(source("./ModelPage.tsx")).toContain("pickSelected(runs, state.runId)");
    expect(source("./Experiments.tsx")).not.toMatch(/onSelect\([^)]*best/);
  });
  it("no title outside the kept definitions", () => {
    const text = source("./Prep.tsx");
    expect(text).not.toContain("Today's members over past years");
    expect(text).not.toContain("Train, a batch, or validate on held-out");
  });
  it("the code area exists only for a model written as code", () => {
    const text = source("./ModelPage.tsx");
    expect(text).toContain('const ownCode = detail.hasCode === true && prep?.mode === "code"');
    expect(text).toContain("const codeShown = (ownCode || snapRun !== null) && state.side === \"code\"");
    expect(text).toContain("<CodeArea");  // the editor is for own code only; a standard pipeline run shows its file read-only
  });
  it("a label and a weight take the formula box without To code", () => {
    const text = source("./Prep.tsx");
    expect(text).toContain("<FormulaBox");
    expect(text).not.toMatch(/<FormulaBox[^>]*onToCode/);
  });
  it("prep fills the panel and back", () => {
    expect(renderPrep("wide", null)).toContain('aria-label="Fill the panel"');
    expect(renderPrep("full", null)).not.toContain('aria-label="Fill the panel"');
    expect(renderPrep("full", null)).toMatch(/aria-label="(Back|Show the experiments|Leave full)/);
  });
  it("standard pipeline opens with the code area shut", () => {
    const text = source("./ModelPage.tsx");
    expect(text).toContain('state.side === "code"');  // shown only after Show code: the panel opens with side unset
    expect(text).not.toMatch(/update\(\{side: "code"\}\)[^;]*\[\]\)/);  // nothing opens it on load
  });
  it("table columns at 1440", () => {
    // 1440 less the 330 px preparation and the gutter leaves about 1100 px: every column of the draft is there
    const shown = COLUMNS.filter(column => !column.hideBelow || column.hideBelow <= 1100).map(column => column.label);
    for (const label of ["Test Rank IC", "t", "vs input", "L–S net", "Turnover", "Train − test", "> 0", "Checks", "Time", "Data to"]) expect(shown, label).toContain(label);
  });
  it("xgboost shows its own parameter names", () => {
    const all = [{name: "algorithm", label: "Algorithm", type: "choice", default: "lightgbm"}, {name: "lgb_leaves", label: "Leaves", type: "integer", default: 210, showIf: {algorithm: "lightgbm"}},
      {name: "xgb_depth", label: "Max depth", type: "integer", default: 6, showIf: {algorithm: "xgboost"}}] as never[];
    expect(visible(all[1], {algorithm: "xgboost"}, all)).toBe(false);
    expect(visible(all[2], {algorithm: "xgboost"}, all)).toBe(true);
    expect(visible(all[1], {algorithm: "lightgbm"}, all)).toBe(true);
  });
  it("held-out asks once and lists earlier uses", () => {
    expect(heldOutUses([])).toBe("none");
    expect(heldOutUses([{number: 4, version: 2, objectName: "Momentum"}, {number: 9, version: null, objectName: "Value"}])).toBe("#4 (v2) in Momentum, #9 in Value");
    expect(source("./ModelPage.tsx")).toMatch(/title=\{`Validate #\$\{confirm\.run\.number\} on held-out\?`\}/);
    expect(source("./ModelPage.tsx")).toContain('data-autofocus=""');  // the main button has the focus
  });
  it("results of a running run show finished windows", () => {
    expect(selected(run({status: "running", state: "running", windowsDone: 6, windowsTotal: 23, progress: {window: 7} as never}), null)).not.toContain(">Publish<");
    // the figure of a running run is fetched again each time a window finishes (the backend draws finished windows only)
    expect(source("./Selected.tsx")).toContain("}, [run.id, run.windowsDone, run.status");
  });
  it("run button says what it will do", () => {
    expect(runLabel("train", 1, null)).toBe("Train");
    expect(runLabel("batch", 4, null)).toBe("Train a batch · 4 groups");
    expect(runLabel("batch", 1, null)).toBe("Train a batch");
    expect(runLabel("heldout", 1, 12)).toBe("Validate #12 on held-out");
    expect(runLabel("check", 1, null)).toBe("Check only");
  });
  it("publish sheet lists factors published with it", () => {
    const text = source("./PublishSheet.tsx");
    expect(text).toContain("Published with it");
    expect(text).toContain("plan.alsoPublished.map(item =>");
    expect(text).toContain("not in the library yet");
  });
  it("old run shows its own code", () => {
    const text = source("./ModelPage.tsx");
    expect(text).toContain("Standard pipeline ${snap.pipeline.version} as #${snapRun.number} ran it, read-only");
    expect(text).toContain("Code of #${snapRun.number}, read-only");
    expect(text).toContain("mapi.code(snapRun.id)");  // the code that run kept, not the file now
  });
  it("new listings are left out by default", () => {
    const withData = {...prep, groups: [{name: "Data", settings: [{name: "leave_new", label: "Leave out new listings", type: "bool", default: true}]}, ...prep.groups]};
    const markup = renderToStaticMarkup(createElement(Prep, {prep: withData as never, values: prep.values as never, base: null, baseNumber: null, width: "wide", check: null, checking: false, vary: [], onVary: noop,
      onChange: noop, onRevert: noop, baseRun: null, onRun: noop, onHeldOut: noop, onReset: noop, onWidth: noop, onWriteCode: noop, onGo: noop, fields: null, inspect: (() => Promise.resolve({})) as never}));
    expect(markup).toContain("Leave out new listings");
    expect(markup).toMatch(/checked/);
  });
  it("blocked by memory names the group", () => {
    const markup = renderToStaticMarkup(createElement(CheckBand, {check: {ok: true, blocked: true, checks: [{name: "Memory", value: "group 6 needs 9.1 GB, 6.2 GB free", need: "", state: "block", group: "batch"}]} as never, checking: false, width: "wide", onGo: noop}));
    expect(markup).toContain("group 6 needs 9.1 GB, 6.2 GB free");
    expect(markup).toContain("Blocks sending");
    expect(source("./ModelPage.tsx")).toContain("Blocked by {blocked}");
  });
  it("results head shows the run's own data", () => {
    expect(headSpan({timeframe: "1d", since: "2016-01-04", until: "latest", heldOutFrom: "2025-01-01"})).toEqual({often: "each day", years: "2016–2024", held: {text: "2025–", title: "Held out from Jan 1, 2025"}});
    expect(headSpan({timeframe: "1d", since: "2024-11-07", until: "2026-11-06", heldOutFrom: "2026-06-01"})).toEqual({often: "each day", years: "2024–2026", held: {text: "Jun 2026", title: "Held out from Jun 1, 2026"}});
    expect(headSpan({timeframe: "1d", since: "2020-01-01", until: "latest"}).years).toBe("2020–");
    expect(source("./results/Results.tsx")).toMatch(/run\.dataTo|dataTo/);
  });
  it("values day follows the selected run", () => {
    expect(source("./results/ValuesTab.tsx")).toMatch(/\[run\.id[^\]]*\]/);  // the day is reloaded when the run changes
  });
  it("checks are debounced and stale answers dropped", () => {
    const answers = latestOnly();
    const first = answers.next(), second = answers.next();
    expect(answers.current(first)).toBe(false);   // the first answer comes back after the second was sent: dropped
    expect(answers.current(second)).toBe(true);
    expect(source("./ModelPage.tsx")).toContain("}, 400);");
  });
  it("dragging keeps the minimums", () => {
    expect(prepWidth(100, 1440)).toBe(280);
    expect(prepWidth(1300, 1440)).toBe(920);
    expect(prepWidth(500, 1440)).toBe(500);
    expect(selHeight(50, 700)).toBe(220);
    expect(selHeight(690, 700)).toBe(560);
  });
  it("two runs finishing together keep the selection", () => {
    const before = [run({id: "a", number: 7, status: "running", state: "running"}), run({id: "b", number: 6, status: "running", state: "running"}), run({id: "c", number: 5})];
    const after = [run({id: "a", number: 7}), run({id: "b", number: 6}), run({id: "c", number: 5})];
    expect(pickSelected(before, "c")?.id).toBe("c");
    expect(pickSelected(after, "c")?.id).toBe("c");
  });
});

describe("the look back with Alpha360 alone", () => {
  it("is fixed when every input is Alpha360, free otherwise", async () => {
    const {alpha360Only} = await import("./Prep");
    expect(alpha360Only([{source: "builtin", ref: "alpha360"}])).toBe(true);
    expect(alpha360Only([{source: "builtin", ref: "alpha360"}, {source: "library", ref: "momentum"}])).toBe(false);
    expect(alpha360Only([])).toBe(false);
  });
});

describe("Early stop on", () => {
  it("starts at each network's own first choice until the person picks one", async () => {
    const {nextEarlyStop} = await import("./Prep");
    const add = {earlyStopOn: [{id: "ic"}, {id: "loss"}, {id: "ric"}]}, adarnn = {earlyStopOn: [{id: "loss"}, {id: "ic"}]}, lstm = {};
    expect(nextEarlyStop(undefined, lstm, adarnn)).toBe("loss");
    expect(nextEarlyStop("ic", add, adarnn)).toBe("loss");
    expect(nextEarlyStop("loss", adarnn, add)).toBe("ic");
    expect(nextEarlyStop("ric", add, adarnn)).toBeUndefined();
    expect(nextEarlyStop(undefined, adarnn, lstm)).toBeUndefined();
  });
});

describe("Start from", () => {
  it("lists only the finished runs with the same inputs, look back, width and layers", async () => {
    const {matchingBases} = await import("./Prep");
    const inputs = [{source: "builtin", ref: "alpha158"}];
    const runs = [{id: "a", number: 14, label: "run #14 · LSTM", algorithm: "lstm", inputs, lookback: 20, width: 64, layers: 2},
      {id: "b", number: 15, label: "run #15 · GRU", algorithm: "gru", inputs, lookback: 30, width: 64, layers: 2},
      {id: "c", number: 16, label: "run #16 · GRU", algorithm: "gru", inputs: [], lookback: 20, width: 64, layers: 2}];
    expect(matchingBases(runs, {inputs, nn_lookback: 20}).map(run => run.id)).toEqual(["a"]);
    expect(matchingBases(runs, {inputs, nn_lookback: 30, nn_width: 64}).map(run => run.id)).toEqual(["b"]);
    expect(matchingBases(undefined, {})).toEqual([]);
  });
});

describe("Auxiliary labels", () => {
  it("shows the automatic note when empty and one input for each written line", async () => {
    const {AuxLabels} = await import("./Prep");
    const empty = renderToStaticMarkup(createElement(AuxLabels, {value: "", onChange: noop}));
    expect(empty).toContain("next 1 and 2 bars further out");
    expect(empty).toContain("Add label");
    const two = renderToStaticMarkup(createElement(AuxLabels, {value: "Ref($open, -8) / Ref($open, -1) - 1\nCSRank($close)", onChange: noop}));
    expect(two).toContain("Auxiliary label 1");
    expect(two).toContain("Auxiliary label 2");
    expect(two).toContain("Remove auxiliary label 2");
  });
});

describe("Resume from window", () => {
  it("a network Qlib trains whole has no epoch to resume from, only the window", () => {
    const failed = {...run(), status: "failed", failure: {window: 5, title: "Window 5: stopped", message: "stopped", resumeFrom: 5, checkpoint: 4}} as unknown as ModelRun;
    const markup = selected(failed, null);
    expect(markup).toContain("Resume from window 5");
    expect(markup).toContain("Resume from window 5, alone");
    expect(markup).not.toContain("from epoch");
  });
  it("a network that saves epochs offers the epoch", () => {
    const failed = {...run(), status: "failed", failure: {window: 5, title: "Ran out of memory", message: "x", kind: "memory", saved: 7, epoch: 8, resumeFrom: 5}} as unknown as ModelRun;
    expect(selected(failed, null)).toContain("Resume from epoch 7, alone");
  });
});

describe("algorithm names", () => {
  it("every algorithm the standard pipeline offers is named as its menu names it, networks too", () => {
    const choices = source("../../native/model_standard.py").split("q.choice(\"algorithm\"")[1].split("label=")[0];
    const pairs = [...choices.matchAll(/\("([a-z_]+)", "([^"]+)"\)/g)].map(match => [match[1], match[2]]).filter(([id]) => id !== "");
    expect(pairs.length).toBeGreaterThan(20);
    for (const [id, label] of pairs) expect(algoLabel(id)).toBe(label);
  });
});

describe("values numbers", () => {
  it("contributions of a ranked label (thousandths) are not all +0.00, and predictions spread over a tenth keep two decimals", () => {
    expect(decimals([0.0012, 0.0031, 0.0007])).toBe(4);
    expect(decimals([0.012, 0.031])).toBe(3);
    expect(decimals([0.45, 0.65])).toBe(2);
  });
  it("another run opens on its own first day, without first asking for the old run's day", () => {
    const text = source("./results/ValuesTab.tsx");
    expect(text).not.toContain("setDate(undefined)");
    expect(text).toMatch(/picked\?\.run === run\.id/);
    expect(text).toContain("(row.base ?? 0)");
  });
});

describe("publishing again", () => {
  it("the sheet names the library item the model last published to, so v2 goes to the same item instead of a new one named after the object", () => {
    expect(source("./PublishSheet.tsx")).toContain("useState(published ?? run.name ?? \"\")");
    expect(source("./ModelPage.tsx")).toMatch(/published=\{\(selected\.published \?\? runs\.find/);
    expect(source("./PublishSheet.tsx")).not.toMatch(/sgn\(row\.current, 3\)/);  // compared with the last version: one format per measure in every column
  });
});
