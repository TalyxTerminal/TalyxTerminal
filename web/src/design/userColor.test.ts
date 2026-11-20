import { describe, expect, it } from "vitest";
import { hexOpacity, withHexOpacity } from "../../../sdk/ui/src/ui/colorValue";
describe("user color opacity", () => {
  it("reads every CSS hex form and changes alpha without changing RGB", () => {
    expect(hexOpacity("#abc")).toBe(100);
    expect(hexOpacity("#abc8")).toBe(53);
    expect(hexOpacity("#123abc80")).toBe(50);
    expect(withHexOpacity("#abc8", 25)).toBe("#aabbcc40");
    expect(withHexOpacity("#123abc80", 80)).toBe("#123abccc");
    expect(withHexOpacity("#123abc", 0)).toBe("#123abc00");
    expect(withHexOpacity("#123abc", 101)).toBe("#123abcff");
  });
});
