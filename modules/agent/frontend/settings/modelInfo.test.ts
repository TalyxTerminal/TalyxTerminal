import { describe, expect, it } from "vitest";
import type { AgentModel } from "../data/types";
import { abilities, thinkingKnob } from "./modelInfo";

const model = (thinkingSchema: Record<string, unknown>, thinkingDefault: unknown = null) =>
  ({ id: "m", model: "m", profile: {}, contextWindow: 200000, tools: true, images: true, pdf: null, thinkingSchema, thinkingDefault } as unknown as AgentModel);

describe("model info", () => {
  it("offers each model its own thinking levels under one name", () => {
    const deepseek = thinkingKnob(model({ type: "object", properties: { effort: { enum: ["off", "low", "high", "max"] } } }, "high"))!;
    expect([deepseek.field, deepseek.label, deepseek.options.map(option => option.label), deepseek.defaultValue])
      .toEqual(["effort", "Thinking", ["Off", "Low", "High", "Max"], "high"]);
    expect(thinkingKnob(model({ type: "object", properties: { effort: { enum: ["minimal", "xhigh", "on"] } } }))!.options.map(option => option.label))
      .toEqual(["Minimal", "Extra high", "On"]);
    expect(thinkingKnob(model({ type: "object", properties: {} }))).toBeNull();
  });

  it("summarizes a model's window and abilities", () => {
    expect(abilities(model({ type: "object", properties: {} }))).toBe("200K window · Images");
    expect(abilities(model({ type: "object", properties: { effort: { enum: ["low"] } } }))).toBe("200K window · Thinking · Images");
  });
});
