import { describe, expect, it } from "vitest";
import { madeWhen } from "./RunBand";

describe("the opened-from band", () => {
  it("says when the run was made in UTC, as the run lists do", () => {
    const made = new Date(Date.UTC(2026, 10, 8, 5, 12));
    expect(madeWhen(made)).toBe("Nov 8, 05:12 UTC");
  });
});
