import { describe, expect, it } from "vitest";
import { nf, pct, sgn } from "./format";

describe("plain numbers", () => {
  it("a negative plain number takes the true minus, as signed ones do; one that rounds to zero has no sign", () => {
    expect(nf(-0.88)).toBe("\u22120.88");
    expect(nf(1.71)).toBe("1.71");
    expect(nf(-1234.5, 1)).toBe("\u22121,234.5");
    expect(nf(-0.001)).toBe("0.00");
    expect(pct(-0.05)).toBe("\u22125.0%");
    expect(sgn(-0.88)).toBe("\u22120.88");
    expect(`${nf(-0.88)}${sgn(-1)}`).not.toContain("-");
  });
});
