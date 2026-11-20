import { describe, expect, it } from "vitest";
import { stack, type Stacked } from "./priceLabels";

const label = (y: number, height = 20, first = false): Stacked => ({ y, height, first, top: 0 });
const gapsOk = (kept: Stacked[]) => kept.every((block, index) => index === 0 || kept[index - 1].top + kept[index - 1].height <= block.top + 1e-9);

describe("price label stacking", () => {
  it("leaves labels that don't touch at their prices", () => {
    const kept = stack([label(100), label(50)], 400, 20);
    expect(kept.map(block => block.top)).toEqual([40, 90]);
  });

  it("spreads labels near one price apart, in price order, around their prices", () => {
    const blocks = [label(200), label(205), label(195)];
    const kept = stack(blocks, 400, 20);
    expect(kept.map(block => block.y)).toEqual([195, 200, 205]);
    expect(gapsOk(kept)).toBe(true);
    expect(kept[1].top + 10).toBeCloseTo(200);
  });

  it("keeps a label with the countdown in one piece", () => {
    const kept = stack([label(100, 40, true), label(110)], 400, 20);
    expect(kept[0].top + kept[0].height).toBeLessThanOrEqual(kept[1].top);
  });

  it("stays inside the pane at its edges", () => {
    const kept = stack([label(395), label(398), label(2)], 400, 20);
    expect(kept[0].top).toBe(0);
    expect(kept[2].top + kept[2].height).toBeLessThanOrEqual(400);
    expect(gapsOk(kept)).toBe(true);
  });

  it("keeps the main series' labels and the nearest others when not all fit", () => {
    const blocks = [label(10), label(50), label(90, 40, true), label(100), label(190)];
    const kept = stack(blocks, 90, 20);
    expect(kept.map(block => block.y)).toEqual([50, 90, 100]);
    expect(gapsOk(kept)).toBe(true);
    expect(kept[2].top + kept[2].height).toBeLessThanOrEqual(90);
  });
});
