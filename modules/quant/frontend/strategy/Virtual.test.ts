import { describe, expect, it } from "vitest";
import { windowOf } from "./useVirtual";

describe("Rows drawn for a long list", () => {
  it("are the ones in view and a few around them, with space of the same size for the rest", () => {
    const top = windowOf(50_000, 26, 0, 600);
    expect(top.start).toBe(0);
    expect(top.end).toBeLessThan(60);
    expect(top.before + (top.end - top.start) * 26 + top.after).toBe(50_000 * 26);
    const middle = windowOf(50_000, 26, 26 * 20_000, 600);
    expect(middle.start).toBe(20_000 - 12);
    expect(middle.before + (middle.end - middle.start) * 26 + middle.after).toBe(50_000 * 26);
  });
  it("end where the list ends", () => {
    const last = windowOf(100, 22, 22 * 90, 400);
    expect(last.end).toBe(100);
    expect(last.after).toBe(0);
  });
});
