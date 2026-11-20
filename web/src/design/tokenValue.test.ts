import { describe, expect, it } from "vitest";
import { tokenValue } from "../../../sdk/ui/src/ui/theme";

/* tokenValue reads the value the browser resolved, and before there is one (tests, start-up) the declared value in the
   single source stylesheet with every var() resolved, so a canvas never gets "var(--tx-green)" as a colour. */
describe("tokenValue", () => {
  it("resolves a declared var() chain to a real value when nothing is drawn yet", () => {
    expect(tokenValue("--tx-up")).toBe("#20C08A");
    expect(tokenValue("--tx-down")).toBe("#F04760");
    expect(tokenValue("--tx-state-fix")).toBe("#F8A97E");
    expect(tokenValue("--tx-doc-backdrop")).toContain("rgba(243, 107, 33, .075)");
    expect(tokenValue("--tx-doc-backdrop")).not.toContain("var(");
    expect(tokenValue("--tx-no-such-token")).toBe("");
  });

  it("prefers the value the browser resolved", () => {
    const styles = { getPropertyValue: (name: string) => name === "--tx-up" ? " #F04760 " : "" } as unknown as CSSStyleDeclaration;
    expect(tokenValue("--tx-up", styles)).toBe("#F04760");
    expect(tokenValue("--tx-down", styles)).toBe("#F04760");
  });
});
