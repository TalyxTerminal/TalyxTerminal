import { describe, expect, it } from "vitest";
import { withPicking } from "./picking";

const figure = {data: [{type: "scatter", name: "Run", x: ["2024-01-01", "2024-06-01", "2025-06-30"], y: [1, 1.1, 1.2]}], layout: {}} as never;

describe("The span a model was picked on", () => {
  it("draws a band from the first day to the picked day with the model's name", () => {
    const out = withPicking(figure, [{name: "Momentum LGBM", to: "2024-12-31"}]) as {layout: {shapes: Record<string, unknown>[]; annotations: Record<string, unknown>[]}};
    expect(out.layout.shapes[0]).toMatchObject({type: "rect", x0: "2024-01-01", x1: "2024-12-31"});
    expect(out.layout.annotations[0].text).toBe("Used to pick Momentum LGBM");
  });
  it("marks the whole run when the model has no held-out start, and nothing when no model's values were read", () => {
    expect((withPicking(figure, [{name: "M", to: null}]) as {layout: {shapes: Record<string, unknown>[]}}).layout.shapes[0]).toMatchObject({x1: "2025-06-30"});
    expect(withPicking(figure, [])).toBe(figure);
  });
});
