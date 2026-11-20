import { afterEach, expect, it, vi } from "vitest";
import { chartColorsWith, watchUpColor } from "./theme";

afterEach(() => vi.unstubAllGlobals());

it("restyles charts on the workspace up-color change and preserves chart-specific candle colors", () => {
  const root = {};
  let swapped = false;
  vi.stubGlobal("document", { documentElement: root });
  vi.stubGlobal("getComputedStyle", () => ({ getPropertyValue: (name: string) =>
    name === "--tx-up" ? (swapped ? "red" : "green") : name === "--tx-down" ? (swapped ? "green" : "red") : "" }));
  let notify = () => {};
  vi.stubGlobal("MutationObserver", class {
    constructor(readonly update: () => void) {}
    observe(node: object, options: MutationObserverInit) {
      if (node === root && options.attributeFilter?.includes("data-up-color")) notify = this.update;
    }
    disconnect() { notify = () => {}; }
  });
  const look = { up: "pink", borderUp: "purple", wickUp: "magenta" };
  let colors = chartColorsWith(look);
  const stop = watchUpColor(() => { colors = chartColorsWith(look); });
  expect(colors.down).toBe("red");
  swapped = true;
  notify();
  expect(colors).toMatchObject({ ...look, down: "green", borderDown: "green", wickDown: "green" });
  stop();
});
