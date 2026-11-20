import { describe, expect, it } from "vitest";
import { MAX_CASES, defaultRange, rangeCount, sweepPlan } from "./sweep";
import type { Parameter } from "./objects";

const parameter = (name: string, type: Parameter["type"], extra: Partial<Parameter> = {}): Parameter =>
  ({name, type, default: 10, min: null, max: null, label: name, line: 1, ...extra});

describe("Sweep ranges", () => {
  it("counts values as the backend does, decimal steps included", () => {
    expect(rangeCount("fast", "int", {from: 5, to: 15, step: 5})).toEqual({count: 3});
    expect(rangeCount("fast", "int", {from: 5, to: 16, step: 5})).toEqual({count: 3});
    expect(rangeCount("level", "float", {from: 0.1, to: 0.3, step: 0.1})).toEqual({count: 3});
    expect(rangeCount("level", "float", {from: 0, to: 1, step: 0.05})).toEqual({count: 21});
  });
  it("says why a range gives nothing", () => {
    expect(rangeCount("fast", "int", {from: 5, to: 15, step: 0})).toHaveProperty("error");
    expect(rangeCount("fast", "int", {from: 15, to: 5, step: 1})).toHaveProperty("error");
    expect(rangeCount("fast", "int", {from: 5.5, to: 15, step: 1})).toHaveProperty("error");
  });
  it("multiplies the ticked ranges and stops above the limit", () => {
    const parameters = [parameter("fast", "int"), parameter("slow", "int"), parameter("stake", "float")];
    const plan = sweepPlan(parameters, {fast: {on: true, from: 5, to: 15, step: 5}, slow: {on: true, from: 20, to: 40, step: 10}, stake: {on: false, from: 1, to: 9, step: 1}});
    expect(plan).toEqual({ranges: {fast: {from: 5, to: 15, step: 5}, slow: {from: 20, to: 40, step: 10}}, total: 9, error: null});
    const big = sweepPlan(parameters, {fast: {on: true, from: 1, to: 100, step: 1}, slow: {on: true, from: 1, to: 3, step: 1}});
    expect(big.total).toBe(300);
    expect(big.error).toContain(String(MAX_CASES));
    expect(sweepPlan(parameters, {}).error).not.toBeNull();
  });
  it("keeps values inside the parameter's bounds", () => {
    const fast = parameter("fast", "int", {min: 2, max: 20});
    expect(sweepPlan([fast], {fast: {on: true, from: 1, to: 10, step: 1}}).error).toContain("at least");
    expect(sweepPlan([fast], {fast: {on: true, from: 10, to: 21, step: 5}}).error).toBeNull();
    expect(sweepPlan([fast], {fast: {on: true, from: 10, to: 25, step: 5}}).error).toContain("at most");
    expect(defaultRange(fast, 4)).toEqual({from: 2, to: 6, step: 1});
  });
});
