import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SidePanel, libraryEntries, readLine, sizeText, visibleRows } from "./SidePanel";

describe("The side panel", () => {
  it("the line a library item inserts always names the version, and a one-off has none", () => {
    expect(readLine({group: "Factors", name: "Reversal 1M", variable: "reversal_1m", version: 2, tone: "ok", note: "v2"})).toBe('reversal_1m = q.library.read("Reversal 1M", version=2)');
    expect(readLine({group: "One-off data", name: "Earnings windows", variable: "earnings_windows", version: null, tone: "muted", note: "one-off"})).toBe('earnings_windows = q.library.read("Earnings windows")');
  });
  it("library items group by kind and read their newest live version", () => {
    const items = libraryEntries({items: [{name: "A", kind: "factor", state: "ok", versions: [{version: 1, state: "stopped"}, {version: 2}, {version: 3, state: "stopped"}]},
      {name: "B", kind: "model", state: "fail", versions: [{version: 1}]}, {name: "C", kind: "data", state: "one_off", versions: [{version: 1}]}]});
    expect(items.map(item => [item.group, item.version, item.tone])).toEqual([["Factors", 2, "ok"], ["Models", 1, "fix"], ["One-off data", null, "muted"]]);
  });
  it("sizes read as the variable list shows them, a dash when unknown", () => {
    expect([sizeText(null), sizeText(900), sizeText(9 * 1024), sizeText(41 * 1024 ** 2), sizeText(1.4 * 1024 ** 2)]).toEqual(["—", "900 B", "9 KB", "41 MB", "1.4 MB"]);
  });
  it("lists the variables with type, shape and size", () => {
    const html = renderToStaticMarkup(createElement(SidePanel, {side: "vars", variables: [{name: "ic", type: "DataFrame", shape: "130 × 6", size: 9216, fromCell: 2}], loaded: [{call: "q.run('run-1')", what: "completed, 3 tables", at: "09:02", seconds: 1.4}], opened: null, library: [], kernelLine: "Research, Python 3.12", onInsert: () => undefined, onOpenVariable: () => undefined, onCloseVariable: () => undefined}));
    expect(html).toContain("ic") && expect(html).toContain("DataFrame") && expect(html).toContain("130 × 6") && expect(html).toContain("9 KB");
    expect(html).toContain("completed, 3 tables") && expect(html).toContain("09:02, 1.4 s");
  });
  it("a thousand variables draw only the visible rows and a margin, and a short list draws all", () => {
    expect(visibleRows(50, 0)).toEqual({from: 0, to: 50});
    const top = visibleRows(1000, 0), middle = visibleRows(1000, 28 * 500);
    expect(top.from).toBe(0);
    expect(top.to - top.from).toBeLessThan(60);
    expect(middle.from).toBe(488);
    expect(middle.to).toBeGreaterThan(500);
    expect(middle.to - middle.from).toBeLessThan(60);
    expect(visibleRows(1000, 28 * 990).to).toBe(1000);
    const many = Array.from({length: 1000}, (_, index) => ({name: `v${index}`, type: "int", shape: null, size: 28, fromCell: null}));
    const html = renderToStaticMarkup(createElement(SidePanel, {side: "vars", variables: many, loaded: [], opened: null, library: [], kernelLine: "k", onInsert: () => undefined, onOpenVariable: () => undefined, onCloseVariable: () => undefined}));
    expect((html.match(/<tr title|<tr>/g) ?? []).length).toBeLessThan(60);
    expect(html).toContain("v0");
    expect(html).not.toContain("v999<");
  });
});
