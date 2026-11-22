import {createElement} from "react";
import {renderToStaticMarkup} from "react-dom/server";
import {describe, expect, it, vi} from "vitest";
import {ChecksColumn, buttonFor} from "./ChecksColumn";
import {HeldOutConfirm, periodText, warnings} from "./HeldOutConfirm";
import {SweepView, cellAt, edgeNote, heatGrid} from "./SweepView";
import {WalkView, pickedText, tradedSharpe, valuesText, walkNumbers} from "./WalkView";
import {isSlow, confirmText} from "./SweepConfirm";
import {costRequests} from "./api";
import {metricText, span, valueText} from "./format";
import type {CheckRow, SweepCase, SweepDetail, Walk} from "./types";

const html = (node: unknown) => renderToStaticMarkup(createElement("div", null, node as never));
const noop = () => undefined;
const axes = [{name: "lookback", label: "Lookback", values: [24, 48, 96]}, {name: "stop_atr", label: "Stop", values: [1.5, 2, 3]}];
const caseOf = (index: number, lookback: number, stop: number, sharpe: number | null, state: SweepCase["state"] = "completed", edge: SweepCase["edge"] = []): SweepCase => ({
  index, params: {lookback, stop_atr: stop}, state, runId: "r" + index, message: state === "failed" ? "ValueError: boom\nline 2" : null,
  metrics: sharpe == null ? null : {sharpe, totalReturn: sharpe / 4, maxDrawdown: -0.2, trades: 20 + index}, neighbours: 0.5, neighbourCount: 8, edge});
function detail(over: Partial<SweepDetail> = {}): SweepDetail {
  const cases = [caseOf(0, 24, 2, 0.4), caseOf(1, 48, 2, 1.2), caseOf(2, 96, 2, 1.5, "completed", [{name: "lookback", label: "Lookback", value: 96, end: "largest", low: 24, high: 96}])];
  return {runId: "s1", objectId: "o", state: "completed", number: 4, metric: "sharpe", axes, cases, selected: null, viewed: 1, bestIndex: 2, done: 3, total: 3, failed: 0,
    concurrency: {atOnce: 5, limit: "cpu"}, machine: {memory: 1, budget: 1, cores: 8}, estimate: null, metrics: ["sharpe"], figures: {}, ...over};
}
const view = (d: SweepDetail) => html(createElement(SweepView, {detail: d, onView: noop, onCell: noop, onUse: noop, onRunCell: noop, onResume: noop}));

describe("the sweep view", () => {
  it("describes_the_best_and_does_not_pick: the best is a key number, nothing is marked selected, the cell looked at is the one the sweep says", () => {
    const text = view(detail());
    expect(text).toContain("Best Sharpe"); expect(text).toContain("96, 2"); expect(text).toContain("5 at a time");
    expect(text).toContain("lookback 48 · stop 2"); expect(text).toContain("Use these values"); expect(text).not.toContain("Picked");
  });
  it("no_cell_looked_at_shows_no_hint", () => expect(view(detail({viewed: null, selected: null}))).not.toContain("Click a cell"));  // the best one opens by itself
  it("edge_note_is_one_line_of_words", () => {
    expect(edgeNote(detail())).toBe("Lookback 96 is the largest value tried (24–96); the best may lie beyond");
    expect(view(detail())).toContain("the best may lie beyond");
    expect(edgeNote(detail({bestIndex: 1}))).toBeNull();
  });
  it("failed_cells_say_why_in_the_table_and_all_failed_is_one_line", () => {
    const some = view(detail({cases: [caseOf(0, 24, 2, 0.4), caseOf(1, 48, 2, null, "failed")], failed: 1, total: 2, bestIndex: 0}));
    expect(some).toContain("Failed: ValueError: boom"); expect(some).not.toContain("line 2");
    const all = view(detail({cases: [caseOf(0, 24, 2, null, "failed"), caseOf(1, 48, 2, null, "failed")], failed: 2, total: 2}));
    expect(all).toContain("All 2 combinations failed; the first one: ValueError: boom");
  });
  it("stopped_sweep_offers_resume_for_the_rest", () => {
    const text = view(detail({state: "cancelled", cases: [caseOf(0, 24, 2, 0.4), caseOf(1, 48, 2, null, "cancelled"), caseOf(2, 96, 2, null, "cancelled")], done: 3, bestIndex: 0}));
    expect(text).toContain("Stopped, 1 of 3"); expect(text).toContain("Resume the other 2");
    expect(view(detail())).not.toContain("Resume the other");
  });
  it("three_axes_offer_the_two_pickers_and_say_what_is_held", () => {
    const three = [...axes, {name: "trail", label: "Trail", values: [1.5, 3]}];
    const cases = detail().cases.map(item => ({...item, params: {...item.params, trail: 1.5}}));
    const text = view(detail({axes: three, cases}));
    expect(text).toContain("Others held at: trail 1.5"); expect(text).toContain("Lookback"); expect(text).toContain("Stop");
  });
  it("heat_grid_holds_the_other_axes_marks_best_and_looked_at_and_leaves_gaps", () => {
    const three = [...axes, {name: "trail", label: "Trail", values: [1.5, 3]}];
    const cases = [{...caseOf(0, 24, 2, 0.4), params: {lookback: 24, stop_atr: 2, trail: 1.5}}, {...caseOf(1, 48, 2, 1.2), params: {lookback: 48, stop_atr: 2, trail: 1.5}},
      {...caseOf(2, 48, 3, null, "failed"), params: {lookback: 48, stop_atr: 3, trail: 1.5}}, {...caseOf(3, 48, 2, 9), params: {lookback: 48, stop_atr: 2, trail: 3}}];
    const grid = heatGrid(detail({axes: three, cases, bestIndex: 1, viewed: 0, selected: null}), "lookback", "stop_atr", "sharpe");
    expect(grid.x).toEqual(["24", "48", "96"]); expect(grid.y).toEqual(["1.5", "2", "3"]);
    expect(grid.cells[1][1]).toMatchObject({value: 1.2}); expect(grid.cells[2][1]).toMatchObject({state: "failed"}); expect(grid.cells[0][0]).toBeNull();
    expect(grid.selected).toEqual([1, 0]); expect(grid.best).toEqual([1, 1]); expect(grid.held).toEqual({trail: 1.5}); expect(grid.index[1][1]).toBe(1);
  });
  it("the_heat_map_is_the_sdk_table_with_the_value_in_each_cell", () => {
    const text = view(detail({cases: [caseOf(0, 24, 2, 0.4), caseOf(1, 48, 2, 1.2), caseOf(2, 96, 2, 1.5)], viewed: 1}));
    expect(text).toContain("tx-heatmap"); expect(text).toContain("1.20"); expect(text).toContain("data-best");
  });
  it("one_axis_has_no_pickers", () => expect(view(detail({axes: [axes[0]]}))).not.toContain("Others held at"));
  it("old_format_says_so", () => expect(view({old: true, number: 4} as SweepDetail)).toContain("Sweep #4 was made by an earlier TALYX; run it again to see it here"));
  it("a_clicked_cell_finds_its_combination_by_the_labels_of_the_axes_drawn", () => {
    const fig = {data: [], layout: {meta: {x: "lookback", y: "stop_atr", heldValues: {}}}} as never;
    const found = cellAt(detail({figures: {heat: fig}, cases: [caseOf(0, 24, 2, 1), caseOf(1, 48, 3, 1)]}), {x: "48", y: "3"});
    expect(found?.index).toBe(1);
  });
  it("each_year_has_no_neighbours_column", () => {
    const years = {name: "@period", label: "Period", values: [{since: "2023-01-01", until: "2023-12-31"}, {since: "2024-01-01", until: "2024-12-31"}]};
    const cases = [{...caseOf(0, 0, 0, 1), params: {"@period": years.values[0]}}, {...caseOf(1, 0, 0, 1), params: {"@period": years.values[1]}}];
    const text = view(detail({axes: [years], cases, total: 2, done: 2}));
    expect(text).toContain("2023"); expect(text).not.toContain("Neighbours"); expect(text).not.toContain("Click a cell");
  });
});

describe("numbers and labels", () => {
  it("metric_text_follows_the_draft", () => {
    expect(metricText("sharpe", 1.4)).toBe("1.40"); expect(metricText("totalReturn", 0.245)).toBe("+25%"); expect(metricText("totalReturn", -0.1)).toBe("−10%");
    expect(metricText("maxDrawdown", -0.2)).toBe("−20%"); expect(metricText("trades", 31)).toBe("31");
  });
  it("axis_values_read_as_labels", () => {
    expect(valueText(48)).toBe("48"); expect(valueText(2.5)).toBe("2.5"); expect(valueText("nextOpen")).toBe("Next open"); expect(valueText({since: "2024-01-01", until: "2024-12-31"})).toBe("2024");
  });
  it("span_reads_hours_and_minutes", () => { expect(span(40)).toBe("40 s"); expect(span(600)).toBe("10 min"); expect(span(4800)).toBe("1 h 20 min"); });
});

const walk: Walk = {pickOn: 6, trade: 2, pickBy: "sharpe", efficiency: 0.5, changed: 4, stitched: [[1_700_000_000_000, 1], [1_710_000_000_000, 0.98]],
  windows: [{n: 1, pickedOn: [1_700_000_000_000, 1_705_000_000_000], traded: [1_705_000_000_000, 1_710_000_000_000], index: 1, values: {lookback: 48, stop_atr: 2}, sharpePicked: 1.5, sharpeTraded: 0.7, returnTraded: 0.02, trades: 5, runId: "w1"},
    {n: 2, pickedOn: [1_705_000_000_000, 1_710_000_000_000], traded: [1_710_000_000_000, 1_715_000_000_000], index: 1, values: {lookback: 48, stop_atr: 2}, sharpePicked: null, sharpeTraded: -0.2, returnTraded: -0.01, trades: 0, runId: "w2", kept: 1}]};
describe("the walk-forward view", () => {
  it("shows_windows_efficiency_and_how_often_values_changed", () => {
    const text = html(createElement(WalkView, {detail: detail({walk}), sourceNumber: 11}));
    expect(text).toContain("Efficiency"); expect(text).toContain("0.50"); expect(text).toContain("values changed 4×"); expect(text).toContain("each window traded with the values picked on the");
  });
  it("window_without_trades_keeps_the_previous_values", () => {
    expect(pickedText(walk.windows[1])).toBe("none traded"); expect(valuesText(walk.windows[1], axes)).toBe("#1's values");
    expect(pickedText(walk.windows[0])).toBe("1.50"); expect(valuesText(walk.windows[0], axes)).toBe("48 · 2");
  });
  it("numbers_are_from_the_stitched_line_and_the_windows", () => {
    const n = walkNumbers(walk);
    expect(n.total).toBeCloseTo(-0.02, 9); expect(n.sharpeOut).toBeCloseTo(0.25, 9); expect(n.sharpeIn).toBeCloseTo(1.5, 9);
  });
  it("a window that placed no trade counts as Sharpe 0, so the one window that traded does not stand for all", () => {
    const idle = {...walk, windows: [walk.windows[0], {...walk.windows[1], sharpeTraded: null, trades: 0}]};
    expect(tradedSharpe(idle.windows[1])).toBe(0); expect(walkNumbers(idle).sharpeOut).toBeCloseTo(0.35, 9);
    expect(tradedSharpe({...walk.windows[1], sharpeTraded: null, trades: null})).toBeNull();
    expect(html(createElement(WalkView, {detail: detail({walk: idle}), sourceNumber: 11}))).toContain("0.23");  // efficiency 0.35 / 1.5, as the check judges it
  });
  it("a walk-forward with nothing traded yet shows no drawdown rather than a red 0.0%", () => {
    expect(walkNumbers({...walk, stitched: [], windows: []}).maxDrawdown).toBeNull();
  });
});

describe("the held-out confirmation", () => {
  const base = {runId: "r", number: 11, from: "2026-05-01", to: "2026-10-31", days: 120, short: false, used: null};
  it("says_what_it_runs_and_that_it_is_once", () => {
    expect(warnings(base)).toEqual([]);
    expect(periodText(base)).toBe("May 1, 2026 – Oct 31, 2026");
    expect(html(createElement(HeldOutConfirm, {open: false, status: null, onCancel: noop, onConfirm: noop}))).toBe("<div></div>");
  });
  it("second_look_and_short_part_are_said", () => {
    expect(warnings({...base, used: {runId: "x", number: 13, same: true}})).toEqual(["#13 already ran on the held-out period with these values"]);
    expect(warnings({...base, used: {runId: "x", number: 13, same: false}})[0]).toBe("#13 already ran on the held-out period");
    expect(warnings({...base, days: 41, short: true})).toEqual([]);
  });
});

const row = (over: Partial<CheckRow>): CheckRow => ({id: "walk", name: "Walk-forward", value: "Not run", need: "≥ 0.50", state: "notrun", title: "definition", go: {kind: "walk"}, ...over});
describe("the checks column", () => {
  it("notrun_checks_have_buttons: each check that can be run says what pressing does", () => {
    expect(buttonFor(row({go: {kind: "validate"}}))?.label).toBe("Validate");
    expect(buttonFor(row({go: {kind: "walk"}}))?.label).toBe("Walk-forward");
    expect(buttonFor(row({go: {kind: "sweep"}}))?.label).toBe("Sweep");
    expect(buttonFor(row({go: {kind: "costs"}}))?.label).toBe("Rerun at 2× costs");
    expect(buttonFor(row({state: "pass", go: {kind: "open", runId: "x"}}))).toBeNull();
  });
  it("pressing_a_button_calls_the_matching_action", () => {
    const actions = {validate: vi.fn(), walk: vi.fn(), sweep: vi.fn(), costs: vi.fn(), open: vi.fn(), resweep: vi.fn(), section: vi.fn()};
    const tree = ChecksColumn({rows: [row({go: {kind: "costs"}}), row({id: "edge", state: "fail", value: "inside", go: {kind: "open", runId: "s", resweep: true}})], actions});
    const buttons: {props: {onClick(): void; children: unknown}}[] = [];
    (function walkTree(node: unknown) { if (Array.isArray(node)) node.forEach(walkTree); else if (node && typeof node === "object" && "props" in node) { const element = node as {type: unknown; props: {onClick?(): void; children?: unknown}}; if (typeof element.props.onClick === "function") buttons.push(element as never); walkTree(element.props.children); } })(tree);
    buttons[0].props.onClick(); buttons[1].props.onClick();
    expect(actions.costs).toHaveBeenCalledTimes(1); expect(actions.resweep).toHaveBeenCalledWith("s");
  });
  it("no_title_outside_definitions: a row's only hover is its definition", () => {
    const text = html(createElement(ChecksColumn, {rows: [row({})], actions: {validate: noop, walk: noop, sweep: noop, costs: noop, open: noop, resweep: noop, section: noop}}));
    expect(text.match(/title="/g)?.length).toBe(1); expect(text).toContain('title="definition"'); expect(text).toContain("Not run");
  });
  it("detail_rides_after_the_value", () => expect(html(createElement(ChecksColumn, {rows: [row({id: "tries", state: "pass", value: "97%", detail: "1 try", go: null})], actions: {validate: noop, walk: noop, sweep: noop, costs: noop, open: noop, resweep: noop, section: noop}}))).toContain("1 try"));
});

describe("estimates and the cost reruns", () => {
  it("only_a_long_sweep_is_confirmed", () => {
    expect(isSlow({seconds: 601, perCase: 1, atOnce: 3, limit: "cpu", combinations: 30})).toBe(true);
    expect(isSlow({seconds: 600, perCase: 1, atOnce: 3, limit: "cpu", combinations: 30})).toBe(false);
    expect(confirmText({seconds: 4800, perCase: 1, atOnce: 3, limit: "cpu", combinations: 30})).toBe("3 at a time on this machine, about 1 h 20 min.");
  });
  it("costs_and_capacity_send_two_sweeps_that_start_from_the_run", () => {
    expect(costRequests("run-1", 10_000)).toEqual([{axes: [{name: "@costMultiple", values: [0, 1, 2]}], of: "run-1"}, {axes: [{name: "@capital", values: [10_000, 100_000, 1_000_000]}], of: "run-1"}]);
  });
});

import {EVERY, parseList, planRanges, runWord} from "./ranges";
import {calendarYears} from "./useValidation";
const lookback = {name: "lookback", label: "Lookback", type: "int", min: 12, max: 240} as never;
const stop = {name: "stop", label: "Stop", type: "float", min: 0.5, max: 6} as never;
describe("ranges typed into the settings", () => {
  it("values_are_checked_against_the_declaration_at_the_box", () => {
    expect(parseList("24, 48, 96", lookback)).toEqual({values: [24, 48, 96]});
    expect(parseList("24, 24, 48", lookback)).toEqual({values: [24, 48]});
    expect(parseList("24, 300", lookback)).toEqual({error: "Lookback 300 is outside 12–240"});
    expect(parseList("24, 2.5", lookback)).toEqual({error: "Lookback 2.5 is not a whole number"});
    expect(parseList("24, x", lookback)).toEqual({error: "Lookback x is not a number"});
    expect(parseList("1.5, 2.5", stop)).toEqual({values: [1.5, 2.5]});
  });
  it("combinations_multiply_with_no_cap_on_how_many", () => {
    const parameters = [lookback, stop];
    const plan = planRanges(parameters, {lookback: "24, 48, 96", stop: "1.5, 2, 3"});
    expect(plan.total).toBe(9); expect(runWord(plan)).toBe("Sweep 9");
    expect(runWord(planRanges(parameters, {}))).toBe("Run backtest");
    const many = planRanges(parameters, {lookback: Array.from({length: 24}, (_, i) => 12 + i).join(", "), stop: Array.from({length: 10}, (_, i) => 1 + i / 2).join(", ")});
    expect(many.total).toBe(240); expect(many.errors).toEqual({}); expect(runWord(many)).toBe("Sweep 240");
    expect(planRanges(parameters, {lookback: "24, 300"}).errors.lookback).toBe("Lookback 300 is outside 12–240");
  });
  it("a switch sweeps both sides and a choice each option", () => {
    const shorts = {name: "shorts", type: "bool" as const, default: true, label: "Shorts", line: 3, min: null, max: null};
    const weighting = {name: "weighting", type: "choice" as const, default: "equal", label: "Weighting", line: 4, min: null, max: null, options: [{value: "equal", label: "Equal"}, {value: "rank", label: "By rank"}]};
    const plan = planRanges([lookback, shorts, weighting] as never, {lookback: "24, 48", shorts: EVERY, weighting: EVERY});
    expect(plan.axes).toEqual([{name: "lookback", values: [24, 48]}, {name: "shorts", values: [false, true]}, {name: "weighting", values: ["equal", "rank"]}]);
    expect(runWord(plan)).toBe("Sweep 8");
  });
  it("each_year_separately_covers_the_span_by_calendar_years", () => {
    expect(calendarYears("2024-11-07", "2026-11-03")).toEqual([{since: "2024-11-07", until: "2024-12-31"}, {since: "2025-01-01", until: "2025-12-31"}, {since: "2026-01-01", until: "2026-11-03"}]);
  });
});
