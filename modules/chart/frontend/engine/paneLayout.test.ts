import { expect, it, vi } from "vitest";
import { PaneLayout } from "./paneLayout";

it("moves the indicator legend as its divider moves without a feed or pointer update", () => {
  let changed = () => {};
  const observed = new Set<HTMLElement>();
  vi.stubGlobal("ResizeObserver", class {
    constructor(callback: () => void) { changed = callback; }
    observe(element: HTMLElement) { observed.add(element); }
    disconnect() { observed.clear(); }
  });
  let y = 360;
  const element = (read: () => number) => ({ getBoundingClientRect: () => ({ top: read() }) } as HTMLElement);
  const parent = element(() => 100), indicator = element(() => y), update = vi.fn();
  const layout = new PaneLayout(parent, update);
  try {
    layout.set(new Map([["price", parent], ["rsi", indicator]]));
    expect(observed.has(indicator)).toBe(true);
    expect(update.mock.lastCall![0].get("rsi")).toBe(260);
    y = 288;
    changed();
    expect(update.mock.lastCall![0].get("rsi")).toBe(188);
  } finally {
    layout.dispose();
    vi.unstubAllGlobals();
  }
  expect(observed.size).toBe(0);
});
