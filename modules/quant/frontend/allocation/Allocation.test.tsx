import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { outsideChanges, proxiedAt, reviewOnly, trialWanted } from "./AllocationPage";
import { allocationChecks, changePlain, changeWriter, keyNumbers } from "./Work";
import { ReviewBox, PeriodTable, TradesTable, trialStatus } from "./ThisPeriod";
import { visible } from "./settingsRules";
import { listValues, shownDay, withLink } from "./Settings";
import { SweepGrid } from "./SweepGrid";
import { differsFrom } from "./differs";
import type { AllocationRun, Capability, Metrics, Row } from "./api";

const html = (node: unknown) => renderToStaticMarkup(createElement("div", null, node as never));
const metrics: Metrics = {total: .2, cagr: .0581, vol: .0783, sharpe: .76, maxDrawdown: -.228, turnover: .38, costDrag: .0003, vs6040: .015, vsEqualWeight: -.0459};
const run = (patch: Partial<AllocationRun> = {}): AllocationRun => ({id: "r1", number: 8, runKind: "backtest", state: "completed", stage: "done", createdAt: 0, endedAt: 1, seconds: 3, metrics, changes: [], starred: false, note: "",
  progress: null, failure: null, message: null, of: null, objectId: "o", data: null, stopped: null, notes: [], queue: null, ...patch});
const caps = (patch: Partial<Capability> = {}): Capability => ({short: true, costAware: true, robustReturns: false, robustCovariance: true, regularization: true, riskCaps: ["cvar"], count: true, turnover: true, tracking: true,
  returns: false, measure: true, tree: false, budget: false, ...patch});
const decl = (name: string, showIf: Record<string, unknown> | null = null) => ({name, type: "text", default: "", min: null, max: null, label: name, line: 1, showIf} as never);
const rows: Row[] = [{asset: "SPY", group: "Stocks", weight: .0473, riskShare: .081, published: .0522, bound: null, drifted: .05}, {asset: "IWM", group: "Stocks", weight: .0478, riskShare: .081, published: .0509, bound: null, drifted: .05},
  {asset: "TLT", group: "Bonds", weight: .5, riskShare: .3, published: .55, bound: "cap", drifted: .55}];

describe("the allocation page's pieces", () => {
  it("key_numbers_whole_run: seven numbers, signed ones with + or −, shares without", () => {
    const items = keyNumbers(metrics, null, () => undefined);
    expect(items.map(item => item.label)).toEqual(["CAGR", "Volatility", "Sharpe", "Max drawdown", "vs 60/40", "vs equal weight", "Turnover"]);
    const markup = html(createElement("span", null, items.map(item => `${item.label} ${String(item.value)}`).join("|")));
    expect(markup).toContain("CAGR +5.81%"); expect(markup).toContain("Volatility 7.83%"); expect(markup).toContain("vs equal weight −4.59 pt"); expect(markup).toContain("Turnover 38%");
  });
  it("a failed or running run shows the numbers as not there yet, never a stale value", () => {
    expect(keyNumbers(undefined, null, () => undefined, "not run").every(item => item.pending === "not run")).toBe(true);
    // While a run is under way a key number is only a dash, with no note under it.
    expect(keyNumbers(undefined, null, () => undefined).every(item => item.pending === "")).toBe(true);
  });
  it("checks are the allocation's own: a failed one reads to review and opens the setting behind it", () => {
    const items = allocationChecks([{id: "binding", name: "Limits that bind", value: "80%", need: "50% or less", state: "fail"}, {id: "solver", name: "Solver failed or fell back", value: "0", need: "0", state: "pass"}], vi.fn());
    expect(items.map(item => item.state)).toEqual(["watch", "ok"]);
  });
  it("click_period_switches_table: the table shows the rows it is given, groups with a subtotal and the limit that binds", () => {
    const markup = html(createElement(PeriodTable, {lead: "Target", rows, groups: ["Stocks", "Bonds"], reference: true, referenceLabel: "v1"}));
    expect(markup).toContain("4.73%"); expect(markup).toContain("Stocks"); expect(markup).toContain("9.51%"); expect(markup).toContain("cap"); expect(markup).toContain("−0.49");
  });
  it("trial_column: a trial shows its change against the run's period", () => {
    const markup = html(createElement(PeriodTable, {lead: "Trial", rows, groups: ["Stocks", "Bonds"], reference: false, referenceLabel: "v1", base: rows.map(row => ({...row, weight: row.weight - .01})), baseLabel: "#8"}));
    expect(markup).toContain("vs #8"); expect(markup).toContain("+1.00");
  });
  it("trades are the move from the drifted holding to the target, in dollars", () => {
    const markup = html(createElement(TradesTable, {rows, groups: ["Stocks", "Bonds"], lead: "Target", capital: 1_000_000, costBps: {Stocks: 2, Bonds: 2}}));
    expect(markup).toContain("−$50.0k"); expect(markup).toContain("each way");
  });
  it("one_off_needs_reason: the one-off button is never greyed for want of a reason (a click asks for it), only while the trial solves; with a trial open accept is not offered, without one it is", () => {
    const base = {label: "November 2026", edits: ["lookback 126"], reason: "", decided: null, since: "v1 published", onReason: vi.fn(), onAccept: vi.fn(), onOneOff: vi.fn(), onNewVersion: vi.fn(), onDiscard: vi.fn()};
    const empty = html(createElement(ReviewBox, base));
    expect(empty).not.toContain("disabled"); expect(empty).not.toContain("Accept as computed"); expect(empty).not.toContain("Write why this period differs");
    expect(html(createElement(ReviewBox, {...base, waiting: true}))).toContain("disabled");
    expect(html(createElement(ReviewBox, {...base, edits: []}))).toContain("Accept as computed");
  });
  it("three_paths: accept, one-off and a new version are all there; a decided period says what was decided", () => {
    const base = {label: "November 2026", edits: ["lookback 126"], reason: "x", decided: null, since: null, onReason: vi.fn(), onAccept: vi.fn(), onOneOff: vi.fn(), onNewVersion: vi.fn(), onDiscard: vi.fn()};
    const markup = html(createElement(ReviewBox, base));
    expect(markup).toContain("one-off adjustment"); expect(markup).toContain("new version"); expect(html(createElement(ReviewBox, {...base, edits: []}))).toContain("Accept as computed");
    expect(html(createElement(ReviewBox, {...base, decided: "November 2026: v1 accepted as computed"}))).toContain("v1 accepted as computed");
  });
  it("each row shows only where the objective accepts it", () => {
    expect(visible(decl("cardinality"), {}, caps())).toBe(true);
    expect(visible(decl("cardinality"), {}, caps({count: false}))).toBe(false);
    expect(visible(decl("short_cap"), {}, caps({short: false}))).toBe(false);
    expect(visible(decl("robust_covariance"), {risk_measure: "cvar"}, caps())).toBe(false);
    expect(visible(decl("expected_returns", {objective: "max_ratio"}), {objective: "min_risk"}, caps({returns: true}))).toBe(false);
  });
  it("changed settings are those that differ from the run's, where the run has them", () => {
    expect([...differsFrom({lookback: 126, covariance: "oas", cost_multiple: 1}, {lookback: 252, covariance: "oas"})]).toEqual(["lookback"]);
  });
  it("no_title_outside_definitions: the run's change text is plain, no explanatory title", () => {
    expect(changePlain(run({changes: [{name: "lookback", from: 252, to: 126}]}))).toBe("lookback 252d → 126d");
    expect(changePlain(run({changes: [{name: "cost_multiple", from: null, to: 1}, {name: "frequency", from: "weekly", to: "monthly"}]}), changeWriter([{name: "frequency", options: [{value: "weekly", label: "Weekly"}, {value: "monthly", label: "Monthly"}]} as never]))).toBe("rebalance weekly → monthly");
    expect(changePlain(run({number: 1}))).toBe("First run");
  });
});

describe("the publish sheet's comparison", () => {
  const row = (extra: Record<string, unknown>) => ({measure: "x", previous: null, current: null, change: null, unit: "pct", ...extra}) as any;
  it("writes a quantity without a direction with no sign, and a difference always with one", async () => {
    const {compareFigure, compareChange} = await import("./PublishSheet");
    expect(compareFigure(row({unit: "pct", signed: false, digits: 2}), 0.0654)).toBe("6.54%");   // volatility
    expect(compareFigure(row({unit: "pct", signed: false, digits: 0}), 0.3943)).toBe("39%");     // turnover
    expect(compareFigure(row({unit: "ratio", digits: 2}), 0.76)).toBe("0.76");                    // a ratio
    expect(compareFigure(row({unit: "pct", signed: true, digits: 2}), 0.0299)).toMatch(/^\+/);    // a return keeps its sign
    expect(compareFigure(row({unit: "pt", digits: 2}), 0.0143)).toMatch(/^\+1\.43 pt$/);
    expect(compareChange(row({unit: "pct", signed: false, digits: 2, change: 0.001}))).toMatch(/^\+0\.10 pt$/);
    expect(compareChange(row({unit: "ratio", digits: 2, change: -0.05}))).toBe("−0.05");
  });
  it("a typed list makes a sweep: only the numbers count", () => {
    expect(listValues("126, 252, 504")).toEqual([126, 252, 504]);
    expect(listValues("126, x, ")).toEqual([126]);
  });
  it("sweep_table_beside_the_grid: a row for each cell, with Weights move", () => {
    const cell = (index: number, lookback: number, move: number | null) => run({id: `c${index}`, number: index, sweep: {id: "s", index, of: 2, axes: ["lookback"], cell: {lookback}, move}});
    const markup = html(createElement(SweepGrid, {runs: [cell(1, 126, 3.4), cell(2, 252, null)], selected: "c1", onSelect: () => undefined, onUse: () => undefined}));
    expect(markup).toContain("Weights move"); expect(markup).toContain("<td>3.4</td>"); expect(markup).toContain("Turnover");
  });
  it("trial_cold_start_message: only the first trial waits for the research process, a later one is solving, a finished one shows its time", () => {
    expect(trialStatus({busy: true, engineWarm: false}, false)).toBe("Starting the research process…");
    expect(trialStatus({busy: true, engineWarm: true}, false)).toBe("Solving…");
    expect(trialStatus({busy: true, engineWarm: false}, true)).toBe("Solving…");
    expect(trialStatus({busy: false, seconds: 0.184}, true)).toBe("0.18 s");
    expect(trialStatus({busy: false}, false)).toBe("");
  });
  it("strategy_run_and_library_item_enter_the_pool: a row of their own group that remembers the run, and once", () => {
    const rowsNow = [{ticker: "SPY", group: "Stocks", currency: "USD", from: "", proxy: "", kind: "", runId: ""}];
    const added = withLink(rowsNow, {name: "Breakout BTC run #14", kind: "run", runId: "run-14", from: "2018-01-02", to: "2024-10-31"} as never);
    expect(added).toHaveLength(2);
    expect(added[1]).toMatchObject({ticker: "Breakout BTC run #14", group: "Strategies", kind: "run", runId: "run-14", currency: ""});
    const again = withLink(added, {name: "Breakout BTC run #14", kind: "run", runId: "run-14", from: "2018-01-02"} as never);
    expect(again).toHaveLength(2);
    const library = withLink(rowsNow, {name: "Minimum variance v2", kind: "library", runId: "v-2"} as never);
    expect(library[1]).toMatchObject({kind: "library", runId: "v-2"});
  });

  it("marks an asset only in the periods before its own prices begin", () => {
    const notes = ["IEF before Jul 2002 from VFITX", "6 missing closes (DBC 3): returns run across the gap"];
    expect([...proxiedAt(notes, "2001-12-31")]).toEqual(["IEF"]);
    expect([...proxiedAt(notes, "2002-07-01")]).toEqual([]);
    expect([...proxiedAt(notes, "2020-01-31")]).toEqual([]);
  });

  it("does not call a pool changed because its rows list the same keys in another order", () => {
    expect([...differsFrom({pool: [{ticker: "SPY", group: "Stocks"}], lookback: 126}, {pool: [{group: "Stocks", ticker: "SPY"}], lookback: 126})]).toEqual([]);
    expect([...differsFrom({pool: [{ticker: "SPY", group: "Bonds"}], lookback: 252}, {pool: [{group: "Stocks", ticker: "SPY"}], lookback: 126})].sort()).toEqual(["lookback", "pool"]);
  });

  it("shows the days of a run or library item offered for the pool as words, not ISO dates", () => {
    expect(shownDay("2024-11-06")).toBe("Nov 6, 2024");
    expect(shownDay("2026-11-07")).toBe("Nov 7, 2026");
    expect(shownDay("")).toBe("");
    expect(shownDay(undefined)).toBe("");
    const source = readFileSync(new URL("./Settings.tsx", import.meta.url), "utf8");
    expect(source).not.toMatch(/\{item\.(from|to)\}/);
  });

  it("sets where the held-out part starts in the run settings, as the factor, model and strategy pages do", () => {
    const settings = readFileSync(new URL("./Settings.tsx", import.meta.url), "utf8");
    const page = readFileSync(new URL("./AllocationPage.tsx", import.meta.url), "utf8");
    expect(settings).toMatch(/<DayInput label=\{t\("Held out from"\)\} value=\{value\} empty onChange=\{onChange\} \/>/);  // typed as the app writes days, not the browser's date box
    expect(page).toMatch(/onHeldOut=\{day => void api\.settings\(detail\.id, \{data: \{\.\.\.data, heldOutFrom: day\}\}\)/);
  });

  it("a new standard-flow allocation starts with its code closed: only an object written as code opens it", () => {
    const shell = readFileSync(new URL("../QuantShell.tsx", import.meta.url), "utf8");
    expect(shell).not.toContain('update(kind === "strategy" ? {tab: "code"} : {side: "code"})');
    expect(shell).toContain('else if (created.hasCode === true) update({side: "code"});');
  });

  it("the review says when the published period was computed, not where the shown run ends", () => {
    const page = readFileSync(new URL("./AllocationPage.tsx", import.meta.url), "utf8");
    expect(page).toContain("computed at the {date} close\", { version: String(published.version), date: dayText(trial.trial.period)");
    expect(page).toContain("trialCase.reviewPending ? published.period : undefined");
    expect(page).not.toMatch(/computed=\{work\?\.to/);
  });

  it("a published period waiting for review is compared with this method's weights for that period, never with the shown run's last period", () => {
    const quiet = {pool: 3, hasRun: true, edits: 0, pinned: false, failed: false};
    // Nothing changed and nothing to review: the shown run's last period is the table.
    expect(trialWanted({...quiet, reviewPending: false})).toBe(false);
    // The library moved on past the run (Nov against Feb): the newest period is worked out, and it is the target, not a trial against the run.
    expect(trialWanted({...quiet, reviewPending: true})).toBe(true);
    expect(reviewOnly({...quiet, reviewPending: true})).toBe(true);
    // A change made during the review is a trial again.
    expect(reviewOnly({...quiet, edits: 1, reviewPending: true})).toBe(false);
    expect(reviewOnly({...quiet, pinned: true, reviewPending: true})).toBe(false);
    // An empty pool asks for nothing; no run yet, a change, a held weight or a failed run each ask for the newest period.
    expect(trialWanted({...quiet, pool: 0, reviewPending: true})).toBe(false);
    for (const one of [{hasRun: false}, {edits: 2}, {pinned: true}, {failed: true}]) expect(trialWanted({...quiet, ...one, reviewPending: false})).toBe(true);
  });

  it("a list setting reads in what changed as how many it holds, never as rows", () => {
    const write = changeWriter([{name: "pool", label: "Assets"}, {name: "shocks", label: "Shocks"}] as never);
    const text = changePlain(run({changes: [{name: "pool", from: [{ticker: "One name on a signal #16"}], to: []}, {name: "shocks", from: [{}, {}, {}], to: [{}, {}, {}, {}]}]}), write);
    expect(text).toBe("assets 1 → none, shocks 3 → 4");
    expect(text).not.toMatch(/rows/);
  });

  it("the two engine-parameter rows carry different labels and regularization names its two numbers", () => {
    const source = readFileSync(new URL("./Settings.tsx", import.meta.url), "utf8");
    expect(source).toContain('"Objective options"'); expect(source).toContain('"Estimator options"'); expect(source).not.toMatch(/ args"/); expect(source).not.toMatch(/"Engine"/);
    expect(source).toContain('suffix="L1"'); expect(source).toContain('suffix="L2"');
  });
});

describe("what differs from the run shown outside the code settings", () => {
  const settings = {data: {since: "2016-11-01", until: "2024-10-31", heldOutFrom: "2024-01-01"}, pool: [{ticker: "SPY"}, {ticker: "IEF"}]};
  it("names added assets and moved dates, and nothing when they are the same", () => {
    expect(outsideChanges(settings, [{ticker: "SPY"}, {ticker: "IEF"}], {since: "2016-11-01", heldOutFrom: "2024-01-01", untilLatest: true})).toEqual([]);
    expect(outsideChanges(settings, [{ticker: "SPY"}, {ticker: "IEF"}, {ticker: "BTC"}], {since: "2018-01-01", heldOutFrom: "2024-01-01", untilLatest: true})).toEqual(["assets", "dates"]);
  });
});
