import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ChecksColumn, KeyNumbersRow, LiveRun, checkItems, costsText, keyNumbers, runRows } from "./Work";
import { FailedBlock, FailureLocals } from "./FailedBlock";
import { mergeRun, noOwnResults, progressText, rowProgress } from "./StrategyPage";
import { CHECKS, run, work } from "./fixtures";

const text = (node: unknown) => renderToStaticMarkup(createElement("div", null, node as never));
const compared = {runId: "run-9", number: 9, label: "#9", stats: null, delta: {cagr: 0.021, maxDrawdown: -0.014, sharpe: 0.1, overBenchmark: 0.02, costsAYear: -0.005, trades: -12, totalReturn: null}, whatDiffers: ["periods differ"]};

describe("Work's key numbers", () => {
  it("costs a year read as a loss, with two places when small and no sign when nothing", () => {
    expect([costsText(0.1007), costsText(0.0004), costsText(0.00004), costsText(0)]).toEqual(["−10.1%", "−0.04%", "0.00%", "0.0%"]);
  });
  it("key_numbers_show_delta_and_total_in_title: six numbers, each with its change against the compared run and the total return in the annual return's hover", () => {
    const items = keyNumbers(work({compared}), () => undefined);
    expect(items.map(item => item.label)).toEqual(["CAGR", "Max drawdown", "Sharpe", "Over BTC hold", "Costs, a year", "Trades"]);
    expect(items[0].title).toContain("Total +14.5%");
    expect(items[2].title).toBe("annualized over 365 days");
    expect(items[3].title).toBe("A year: CAGR minus BTC hold CAGR");
    expect(items[4].title).toContain("Fees 52% of gross");
    const row = renderToStaticMarkup(createElement(KeyNumbersRow, {work: work({compared}), go: vi.fn()}));
    expect(row).toContain("vs");
    expect(row).toContain("#9");
    expect(row).toContain("+2.1 pt");
    expect(row).toContain("−12");
  });
  it("a number the run cannot give reads a dash", () => {
    const short = work({stats: {...work().stats, cagr: null, sharpe: null}});
    const markup = renderToStaticMarkup(createElement(KeyNumbersRow, {work: short, go: vi.fn()}));
    expect(markup).toContain("—");
    expect(markup).not.toContain("NaN");
  });
  it("a run that placed no order says so under the numbers", () => {
    const markup = renderToStaticMarkup(createElement(KeyNumbersRow, {work: work({notes: ["No trades. The code has not placed an order yet."], stats: {...work().stats, trades: 0, sharpe: null}}), go: vi.fn()}));
    expect(markup).toContain("No trades. The code has not placed an order yet.");
  });
  it("key_number_click_jumps_to_section: drawdown goes to Equity, Sharpe to Rolling, costs to Costs", () => {
    const go = vi.fn();
    const items = keyNumbers(work(), go);
    items[1].onClick?.(); items[2].onClick?.(); items[4].onClick?.();
    expect(go.mock.calls.map(call => call[0])).toEqual(["equity", "rolling", "costs"]);
  });
  it("funding the source has no rates for is no tag on the costs: it is one of the run's data notes", () => {
    const markup = renderToStaticMarkup(createElement(KeyNumbersRow, {work: work({costs: {...work().costs, incomplete: [{instrument: "BTC", from: 1, to: 2, reason: "none"}]}}), go: vi.fn()}));
    expect(markup).not.toContain("incomplete");
  });
  it("a run that was stopped says where, and that its checks need a full run", () => {
    const markup = renderToStaticMarkup(createElement(KeyNumbersRow, {work: work({partial: true, stoppedAt: 1_700_000_000_000}), go: vi.fn()}));
    expect(markup).toContain("Stopped at Nov 14, 2023, simulated; the part so far is kept below. Checks need a full run.");
  });
});

describe("Work's checks", () => {
  it("the two this segment computes show their state, the rest offer Run", () => {
    const go = vi.fn();
    const items = checkItems(CHECKS, go);
    expect(items.map(item => item.state)).toEqual(["notrun", "ok", "watch"]);
    expect(items[0].onClick).toBeUndefined();
    items[2].onClick?.();
    expect(go).toHaveBeenCalledWith("trades");
    const markup = renderToStaticMarkup(createElement(ChecksColumn, {work: work(), go}));
    expect(markup).toContain("Run");
    expect(markup).toContain("1.4×");
    expect(markup).toContain("62%");
  });
});

describe("Work's runs and failures", () => {
  it("runs_table_changes_and_compare: each run says what changed, its numbers or its failure, and the shown and compared runs are marked", () => {
    const rows = runRows([{id: "a", number: 2, kind: "backtest", state: "completed", createdAt: 1, endedAt: 2, changes: [{what: "lookback", from: 24, to: 48}], stats: work().stats, failure: null, starred: true, note: "", tags: ["notebook"], heldOut: false},
      {id: "b", number: 1, kind: "backtest", state: "failed", createdAt: 1, endedAt: 2, changes: [], stats: null, failure: {error: "boom", line: 7}, starred: false, note: "", tags: [], heldOut: false}], "a", "b", new Set(["b"]));
    expect(rows.map(row => [row.selected, row.compared, row.checked])).toEqual([[true, false, false], [false, true, true]]);
    expect(text(rows[0].cells.changes)).toContain("lookback 24 → 48");
    expect(text(rows[0].cells.n)).toContain("★");
    expect(text(rows[0].cells.changes)).toContain("Notebook");  // the kind of run leads its change, so the numbers line up
    expect(text(rows[1].cells.state)).toContain("Failed at line 7");
    expect(text(rows[1].cells.changes)).toContain("first run");
  });
  it("a failed run says which line, when, and the values the code held; missing prices list the names", () => {
    const failure = {file: "strategy.py", line: 7, error: "RuntimeError: boom", stage: "backtest", simulatedAt: 1_704_153_600_000, function: "on_bar", sourceLine: "level = 1 / n", instrument: "BTC/USDT:USDT", cause: "x", tradesBefore: 4, equityBefore: 10_200,
      locals: [["level", "20000.0"]] as [string, string][], withoutPrice: ["S03", "S09"], inEngine: false, partial: true};
    const markup = renderToStaticMarkup(createElement(FailedBlock, {run: run({state: "failed", failure}), work: work({partial: true}), onLine: vi.fn()}));
    for (const word of ["#11 stopped at line 7", "Go to line 7", "RuntimeError: boom", "Stopped at Jan 2, 2024 00:00 UTC, simulated", "4 trades before", "Traceback", "S03, S09", "Show the error output"]) expect(markup).toContain(word);
    const locals = renderToStaticMarkup(createElement(FailureLocals, {run: run({state: "failed", failure})}));
    for (const word of ["At the failure", "on_bar, local values", "level", "20000.0"]) expect(locals).toContain(word);
    const engine = renderToStaticMarkup(createElement(FailedBlock, {run: run({state: "failed", failure: {...failure, line: null, inEngine: true, locals: []}}), work: null, onLine: vi.fn()}));
    expect(engine).toContain("Stopped inside the engine");
    expect(renderToStaticMarkup(createElement(FailureLocals, {run: run({state: "failed", failure: {...failure, line: null, inEngine: true, locals: []}})}))).toContain("No frame of your code on the stack");
    expect(engine).not.toContain("Go to line");
    const data = renderToStaticMarkup(createElement(FailedBlock, {run: run({state: "failed", failure: {...failure, stage: "data", line: null, inEngine: false, locals: [], simulatedAt: null, cause: "RuntimeError: boom"}}), work: null, onLine: vi.fn()}));
    expect(data).toContain("Stopped while fetching prices");
    expect(data).not.toContain("Show the error output");
  });
  it("saving the platform's settings merges a group and a null removes a value", () => {
    expect(mergeRun({exchange: {leverage: 2}, capital: 5}, {exchange: {stopTrigger: "mark"}})).toEqual({exchange: {leverage: 2, stopTrigger: "mark"}, capital: 5});
    expect(mergeRun({exchange: {leverage: 2}, costs: {impact: 1}}, {exchange: {leverage: null}, capital: null})).toEqual({costs: {impact: 1}});
  });
  it("a run that goes shows how far the simulation is and how many trades it has", () => {
    expect(progressText(run({state: "running", progress: {simulatedTo: Date.UTC(2024, 5, 1), value: .4, tradesSoFar: 17, stage: "backtest"}}))).toEqual({stage: "Backtesting to Jun 1, 2024", count: "17 trades", value: .4});
  });
  it("while another run is shown, the head says the going sweep's step and how many combinations are done, not 'Starting' throughout", () => {
    const row = {id: "run-11", number: 11, kind: "sweep" as const, state: "running" as const, stage: "sweep", createdAt: 0, endedAt: null, changes: [], stats: null, failure: null, starred: false, note: "", tags: [], heldOut: false,
      sweep: {metric: "sharpe", done: 1, total: 3, completed: 1, failed: 0, best: null}};
    expect(rowProgress(row)).toEqual({stage: "Running the combinations", count: "1 of 3", value: 1 / 3});
    expect(rowProgress({...row, stage: "data"})).toEqual({stage: "Fetching prices", value: null});
    expect(rowProgress(null).stage).toBe("Starting");
  });
  it("a walk-forward, or a sweep with nothing picked, has no Results of its own; one with a picked combination shows that one's", () => {
    expect(noOwnResults(run({id: "run-8", kind: "walk", shownRunId: "run-8"}))).toBe(true);
    expect(noOwnResults(run({id: "run-7", kind: "sweep", shownRunId: "run-7c"}))).toBe(false);
    expect(noOwnResults(run({id: "run-6", kind: "backtest", shownRunId: "run-6"}))).toBe(false);
    expect(noOwnResults(null)).toBe(false);
  });
  it("while the data is fetched the head says which step, how many names of all, and the names left out so far; never only 'Starting'", () => {
    const fetch = {step: "prices" as const, done: 150, total: 617, leftOut: ["VRSK", "K", "WBD", "HBI"]};
    expect(progressText(run({state: "preparing", stage: "data", progress: {stage: "data", fetch} as never}))).toEqual({stage: "Fetching prices: 150 of 617", count: "4 left out: VRSK, K, WBD, …", value: 150 / 617});
    expect(progressText(run({state: "preparing", stage: "data", progress: {stage: "data", fetch: {...fetch, step: "terms", leftOut: []}} as never})).stage).toBe("Reading trading terms: 150 of 617");
    expect(progressText(run({state: "preparing", stage: "data", progress: null})).stage).toBe("Fetching prices");
    expect(progressText(run({state: "preparing", stage: "environment", progress: {stage: "environment", fetch} as never})).stage).toBe("Preparing the environment");
    expect(progressText(run({state: "running", stage: "sweep", progress: null})).stage).toBe("Running the combinations");  // not "Starting" while 5 of 6 have run
  });
});

describe("Work while a run is going", () => {
  it("the numbers say when they come, the trades so far are counted and the checks wait for the end", () => {
    const html = text(createElement(LiveRun, {run: {simulatedTo: 1, value: .4, tradesSoFar: 55, stage: "backtest"}, name: "BTC", kind: "perpetual · 1 h", benchmark: "BTC hold"}));
    expect(html).toContain("when done"); expect(html).toContain("55"); expect(html).toContain("so far"); expect(html).toContain("Over BTC hold");
    expect(html).toContain("Deflated Sharpe"); expect(html).toContain("after the run"); expect(html).toContain("The equity appears as the run goes");
  });
  it("draws the equity so far where the chart will be", () => {
    const figure = {data: [{type: "scatter", mode: "lines", x: ["2024-11-08 00:00", "2024-11-09 00:00"], y: [0, .01], meta: {role: "main", key: "equity"}}], layout: {meta: {key: "strategy.live"}}};
    const html = text(createElement(LiveRun, {run: {simulatedTo: 1, value: .4, tradesSoFar: 2, stage: "backtest", figure}, name: "BTC", kind: "", benchmark: "BTC hold"}));
    expect(html).toContain("Equity so far"); expect(html).not.toContain("The equity appears as the run goes");
  });
});
