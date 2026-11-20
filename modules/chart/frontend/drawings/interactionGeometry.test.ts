import { describe, expect, it } from "vitest";

import { positionPixels } from "./interactionGeometry";

describe("one-click position", () => {
  it("puts a long's target above and stop below the entry, a tenth of the pane away, 20 bars to the right", () => {
    expect(positionPixels("long_position", { x: 100, y: 300 }, 500, 6)).toEqual([{ x: 220, y: 250 }, { x: 220, y: 350 }]);
  });

  it("puts a short's target below and stop above", () => {
    expect(positionPixels("short_position", { x: 100, y: 300 }, 500, 6)).toEqual([{ x: 220, y: 350 }, { x: 220, y: 250 }]);
  });

  it("follows the chart in view: a taller pane and wider bars move them further", () => {
    expect(positionPixels("long_position", { x: 0, y: 400 }, 800, 12)).toEqual([{ x: 240, y: 320 }, { x: 240, y: 480 }]);
  });
});
