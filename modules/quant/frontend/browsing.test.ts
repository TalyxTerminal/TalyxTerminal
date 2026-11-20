import { describe, expect, it } from "vitest";
import { browsingOnly } from "./QuantShell";

describe("Quant panel state that only moves where the user is looking", () => {
  it("follows a new run, switches Work and Results, picks a run or a row without an unsaved change", () => {
    const open = {objectId: "strategy", runId: "run-1", strategyView: "work" as const};
    expect(browsingOnly(open, {...open, runId: "run-2"})).toBe(true);
    expect(browsingOnly(open, {...open, strategyView: "results", strategyResults: "trades"})).toBe(true);
    expect(browsingOnly(open, {...open, strategyTab: "runs", strategyTrade: 4})).toBe(true);
    expect(browsingOnly({...open, strategyResults: "trades"}, {objectId: "strategy", runId: "run-2"})).toBe(true);
  });
  it("counts what the user arranges or changes: another object, sizes, sweep ranges", () => {
    const open = {objectId: "strategy", runId: "run-1"};
    expect(browsingOnly(open, {objectId: "other", runId: "run-1"})).toBe(false);
    expect(browsingOnly(open, {...open, strategyHeight: 320})).toBe(false);
    expect(browsingOnly(open, {...open, runId: "run-2", sweep: {on: true, fields: {}}})).toBe(false);
  });
});
