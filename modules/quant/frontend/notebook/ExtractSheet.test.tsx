import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

// The sheet is a portal; here it is just its children and footer, so the markup can be read.
vi.mock("@talyx/sdk", async importOriginal => ({...(await importOriginal<Record<string, unknown>>()), TxSheet: ({children, footer}: {children: unknown; footer: unknown}) => createElement("div", null, children as never, footer as never)}));
import { ExtractSheet } from "./ExtractSheet";

const preview = (patch = {}) => ({code: "# cell [2]\nx = 1\n", notTaken: ["Cell [1] defines rev that these cells use"], heldOutFrom: "2025-06-30",
  dataSettings: {runs: [], libraryInputs: [{name: "Reversal 1M", version: 2}], universe: "S&P 500", symbols: null, timeframe: "1d", since: "2016-01-04", until: null, heldOutStudied: true, conflicts: [], found: true}, ...patch});
const render = (data: ReturnType<typeof preview> | null) => renderToStaticMarkup(createElement(ExtractSheet, {open: true, count: 1, total: 3, kind: "Factor", onKind: () => undefined, preview: data, failure: null, onClose: () => undefined, onCreate: () => undefined}));

describe("The extract sheet", () => {
  it("shows the code as written, the data settings, the held-out note and what is not taken", () => {
    const html = render(preview());
    for (const text of ["Code, as written", "Jan 4, 2016 – latest", "Jun 30, 2025", "Tidying the code is yours, in Developer.", "# cell [2]", "S&amp;P 500", "Reversal 1M", "these cells studied the part after it", "Not taken", "Cell [1] defines rev that these cells use", "Create factor"]) expect(html).toContain(text);
  });
  it("names a run read by its id the way the run band does, not by the id", () => {
    const html = render(preview({dataSettings: {...preview().dataSettings, runs: [{object: "run-12bcb6be7ca94c60acc4ab53f13e9585", name: "QA Trend", number: 2, at: Date.UTC(2026, 10, 19, 7, 21)}]}}));
    expect(html).toContain("QA Trend <b>#2</b>, Nov 19, 07:21 UTC");
    expect(html).not.toContain("run-12bc");
  });
  it("says None found when the cells read no data", () => {
    expect(render(preview({dataSettings: {runs: [], libraryInputs: [], universe: null, symbols: null, timeframe: "1d", since: null, until: null, heldOutStudied: false, conflicts: [], found: false}}))).toContain("None found in these cells");
  });
});
