import { createElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { DEFAULT_RUN, type FactorNumbers, type FactorRun } from "./api";
import * as Result from "./Result";
import { CHECKS_NEED, checksFold, HeldOutBlock, NormalBlock, RunningBlock, VsPicker, keyNumbers, runUniverse, heldOutUsedLine, type ResultHandlers } from "./Result";
import { compareLabel } from "./Bottom";

// The charts are not drawn here: each Plot is noted (its label, its figure, its click) and stands in as an empty element.
const plots: {ariaLabel: string; figure: {layout?: {meta?: {key?: string}}}; height?: number; onPoint?(point: unknown): void}[] = [];
vi.mock("../chart/Plot", async importOriginal => {
  const original = await importOriginal<Record<string, unknown>>();
  return {...original, Plot: (props: {ariaLabel: string}) => {plots.push(props as never); return <i data-plot={props.ariaLabel} />;}};
});

const read = (file: string) => readFileSync(new URL(file, import.meta.url), "utf8");
const html = (node: ReactNode) => renderToStaticMarkup(<>{node}</>);
const DAY = 86_400_000;

const numbers = (over: Partial<FactorNumbers> = {}): FactorNumbers => ({rankIc: .0312, t: 3.4, longShortNet: .081, longShortGross: .1, turnover: .42, names: 480, members: null, mostLike: {name: "Reversal", version: "Style", rho: .31},
  breakEven: 14.8, groupSize: 96, sharpe: 1.1, maxDrawdown: -.2, days: 2400, groups: 5, holding: 5, ...over});
const run = (over: Partial<FactorRun> = {}, holding = 5, last = Date.UTC(2026, 10, 6)): FactorRun => ({
  id: "r3", objectId: "o", number: 3, kind: "run", state: "completed", stage: "done", createdAt: Date.UTC(2026, 10, 6, 9, 30), endedAt: Date.UTC(2026, 10, 6, 9, 31), seconds: 2.5,
  formula: "-($close / Ref($close, 5) - 1)", mode: "formula", settings: {data: {list: "watch", timeframe: "1d", since: "2024-01-01", until: "2026-11-05"}, run: {...DEFAULT_RUN, holding}, params: {}},
  changes: [], numbers: numbers({holding}), review: 0, failure: null, progress: {steps: [], startedAt: 0}, starred: false, note: "", of: null, ofNumber: null, sweep: null, version: null,
  data: {dataTo: last, requested: 12}, codeDigest: null, message: null, updatedAt: 0, heldOut: null, futureData: false, results: {state: "none"} as never, resultsProgress: null, ...over});
const handlers = (): ResultHandlers => ({select: vi.fn(), go: vi.fn(), holding: vi.fn(), date: vi.fn(), retry: vi.fn(), line: vi.fn(), useSettings: vi.fn(), create: vi.fn(), compare: vi.fn(), publish: vi.fn()});
const figures = {groups: {data: [], layout: {meta: {key: "factor.groups"}}}, decay: {data: [], layout: {meta: {key: "factor.decay"}}}};
const normal = (over: Partial<Parameters<typeof NormalBlock>[0]> = {}) => {
  const target = run(), base = {run: target, numbers: target.numbers!, checks: [], figures, notes: [], compared: null, runs: [target], editedHolding: null, valuesDate: null, handlers: handlers()};
  return {...base, ...over} as Parameters<typeof NormalBlock>[0];
};

describe("Factor Work: the key numbers and what stands beside them", () => {
  it("the checks fold to one line in a narrow panel and when the box they would have is under 330 px, both kept", () => {
    expect(CHECKS_NEED).toBe(330);
    expect([0, 250, 329, 330, 420].map(checksFold)).toEqual([false, true, true, false, false]);  // 0 is a box not on screen yet
    const css = readFileSync(new URL("./factor.css", import.meta.url), "utf8");
    expect(css).toMatch(/@container \(max-width: 1000px\)\s*\{[^@]*\.fx-frow > \.fx-csec \{ grid-column: 1 \/ -1/);  // the width rule
    expect(css).toContain(".fx-frow[data-short] > .fx-csec { grid-column: 1 / -1");  // the height rule folds the same way
    expect(css).toContain(".fx-frow[data-short] .fx-csec:not([data-open]) .tx-checklist { display: none; }");
    expect(css).not.toContain(".fx-res[data-short]");
    expect(html(createElement(NormalBlock, normal()))).toContain('class="fx-ckfold"');  // the one row is always there for the styles to show
  });
  it("a finished run's head offers Publish beside Use this run; a run that did not finish does not", () => {
    expect(html(createElement(NormalBlock, normal()))).toContain("Publish");
    const failed = run({state: "failed", numbers: null} as Partial<FactorRun>);
    expect(html(createElement(NormalBlock, normal({run: failed})))).not.toContain("Publish");
  });
  it("only shows differences supplied on common dates", () => {
    const now = run({}, 5), before = run({}, 5);
    const items = keyNumbers(now.numbers, before, now, () => undefined, {rankIc: 0.06372});
    expect(html(items[0].delta)).toContain("0.0637");
    expect(html(items[1].delta).trim()).toBe("");
  });

  it("few names show dashes, not zeros", () => {
    const few = numbers({days: 0, names: 2, rankIc: 0, t: 0});
    const items = keyNumbers(few, null, run({numbers: few}), () => undefined);
    expect(items.slice(0, 2).map(item => item.value)).toEqual(["—", "—"]);
    expect(html(createElement(NormalBlock, normal({numbers: few})))).toContain("Too few names a day for a cross-sectional test: 2");
  });
  it("nothing to compare with is a dash, not a sentence", () => {
    const items = keyNumbers(numbers({mostLike: null}), null, run(), () => undefined);
    const like = items[5];
    expect(like.value).toBe("—");
    expect(like.title).toBeUndefined();
  });
  it("a compared run that ends on another day says so once, on the compare control, not under every number", () => {
    const now = run({data: {dataTo: Date.UTC(2026, 10, 6)}}), before = run({id: "r2", number: 2, data: {dataTo: Date.UTC(2026, 10, 5)}});
    expect(html(keyNumbers(now.numbers, before, now, () => undefined)[2].delta)).not.toContain("to Nov");
    expect(html(<VsPicker run={now} compared={before} runs={[now, before]} onCompare={() => undefined} />)).toContain("data to Nov 5");
    const same = run({id: "r2", number: 2, data: {dataTo: Date.UTC(2026, 10, 6)}});
    expect(html(<VsPicker run={now} compared={same} runs={[now, same]} onCompare={() => undefined} />)).not.toContain("data to");
  });
  it("clicking 10D in the decay chart runs with holding 10", () => {
    plots.length = 0;
    const props = normal();
    html(createElement(NormalBlock, props));
    const decay = plots.find(item => item.ariaLabel === "IC by holding days")!;
    decay.onPoint!({x: "10D", y: .02});
    expect(props.handlers.holding).toHaveBeenCalledWith(10);
  });
});

describe("Factor Work: the page says nothing the data does not", () => {
  it("a held-out validation has no publish button", () => {
    const work = {kind: "heldout" as const, numbers: numbers({days: 400}), years: [], figures: {heldout: figures.groups}, from: Date.UTC(2025, 6, 1), to: Date.UTC(2026, 10, 5), dataTo: 0, dataFrom: 0, notes: [], seconds: 1};
    expect(html(createElement(HeldOutBlock, {run: run({kind: "heldout", of: "r1", ofNumber: 1}), work, source: run({id: "r1", number: 1}), handlers: handlers()}))).not.toContain("Publish");
  });
  it("held-out under 250 days says short sample", () => {
    const heldOut = (days: number) => html(createElement(HeldOutBlock, {run: run({kind: "heldout", of: "r1", ofNumber: 1}), source: run({id: "r1", number: 1}), handlers: handlers(),
      work: {kind: "heldout", numbers: numbers({days}), years: [], figures: {heldout: figures.groups}, from: Date.UTC(2026, 3, 1), to: Date.UTC(2026, 10, 5), dataTo: 0, dataFrom: 0, notes: [], seconds: 1}}));
    expect(heldOut(120)).toContain("short sample");
    expect(heldOut(120)).not.toContain("annual number from so few days");
    expect(heldOut(400)).not.toContain("short sample");
  });
  it("confirm names earlier uses", () => {
    expect(heldOutUsedLine(3)).toBe("This held-out period was already used 3 times for this formula.");
    expect(heldOutUsedLine(0)).toBeNull();
    expect(heldOutUsedLine(null)).toBeNull();
  });
  it("a running run shows the universe it runs, not the factor's current one", () => {
    const lists = [{id: "watch", name: "Watch", count: 5}] as never;  // the list has 5 names now
    const running = run({state: "running", settings: {...run().settings, data: {list: "watch", timeframe: "1d", since: "2024-01-01", until: "2026-11-05"}}, data: {requested: 12}});
    expect(runUniverse(running, lists, [])).toBe("Watch (12)");  // the 12 it was started with
    const noCount = run({state: "running", data: null, settings: {...run().settings, data: {list: "watch", timeframe: "1d", since: "2024-01-01", until: "2026-11-05", symbols: Array(7).fill({instrument: {}}) as never}}});
    expect(runUniverse(noCount, lists, [])).toBe("Watch (7)");
    const markup = html(createElement(RunningBlock, {run: running, rows: [], universe: (target: FactorRun) => runUniverse(target, lists, []), last: null, handlers: handlers()}));
    expect(markup).toContain("Watch (12)");
    expect(markup).not.toContain("Watch (5)");
    // another run of the same factor, started with other data, reads its own
    const other = run({state: "running", id: "r4", number: 4, data: {requested: 30}, settings: {...running.settings, data: {...running.settings.data, until: "2026-01-31"}}});
    expect(runUniverse(other, lists, [])).toBe("Watch (30)");
  });
  it("old run shows its own code snapshot", () => {
    const page = read("./FactorPage.tsx");
    expect(page).toContain("Restore this code");
    expect(page).toContain("Code of #");
    expect(page).toContain(", read-only");  // the header of the snapshot: the code of that run, not editable
  });
  it("compare button appears once runs are ticked", () => {
    expect(compareLabel(0)).toBeNull();
    expect(compareLabel(1)).toBe("Compare 2 in results");
    expect(compareLabel(3)).toBe("Compare 4 in results");
  });
  it("no hover text outside the kept definitions", () => {
    const sources = Object.fromEntries(["Settings", "Bottom", "FactorPage", "FormulaBox", "Result", "Results"].map(name => [name, read(`./${name}.tsx`)]));
    const all = Object.values(sources).join("\n") + read("../DataSettings.tsx");
    const deleted = [
      "Days each position is held", "Names split into this many groups", "Equal counts, fixed cuts of the value", "The fixed risk-free rate, 0% to 30% a year",
      "Every run stops the day before; the rest is scored only by Validate", "Click to select", "Drag to resize", "Open Results with the ticked runs drawn beside this one",
      "Run settings override the default in line", "Run to make it a run of its own", "Turn this formula into a Python factor in Developer",
      "Publishing to the library comes with the library page", "Work's numbers are in; the Results keep building in the background", "Under 250 trading days: an annual number",
      "They are built on request, in the background", "Known once the range starts within the history", "The library has no factor to compare with", "the limit is 0.70",
      "zero would overstate Sharpe", "Cost of each trade, both legs; a list makes a sweep", "Borrow fee on the short leg\""];
    for (const text of deleted) expect(all, text).not.toContain(text);
    const kept = ["Cut points on the preprocessed value", "Values use data up to the close before the return starts", "Residual after industry dummies and log market value, each day",
      "Per trade, each leg", "The value before preprocessing"];
    for (const text of kept) expect(all, text).toContain(text);
    expect(sources.Settings).toMatch(/Borrow fee on the short leg a year|Borrow fee on the short leg, a year/);
  });
});
