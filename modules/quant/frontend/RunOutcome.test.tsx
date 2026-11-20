import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { RunOutcome } from "./RunOutcome";
import type { Api, RunSummary, RunView } from "./objects";

const summary: RunSummary = {
  id: "run-1", objectId: "o", name: "Momentum", runKind: "run", mode: "run", publish: null, state: "running", stage: "run", createdAt: 0, endedAt: null,
  message: null, error: null, params: {}, parameterDiff: {}, data: {symbols: [], timeframe: "1d", since: null, until: null, list: null, universe: null},
  metrics: {}, starred: false, note: "", codeDigest: null,
};
const view = (patch: Partial<RunView>, summaryPatch: Partial<RunSummary>): RunView =>
  ({id: "run-1", objectId: "o", state: "running", error: null, data: null, result: null, summary: {...summary, ...summaryPatch}, ...patch});
const render = (run: RunView) => renderToStaticMarkup(createElement(RunOutcome, {
  api: {} as Api, run, number: 3, view: "report", kind: "research", report: () => null, onCancel: vi.fn(), onLine: vi.fn(), onLibrary: vi.fn(),
}));

describe("Quant run outcome", () => {
  it("names the symbol, the moment and both values when a publication used future data", () => {
    const publish = {sourceRunId: "run-0", name: "Peek ahead", items: ["factor"], checks: 3, versions: []};
    const markup = render(view({state: "failed", error: {stage: "checks", code: "future_data", message: "m",
      details: {name: "factor", instrument: "AAPL", valueTime: Date.UTC(2025, 11, 4), cutoff: Date.UTC(2025, 11, 8), withAllData: 0.0287645, whenCut: null, changed: 30}}},
    {runKind: "publish", mode: "publish", publish, state: "failed", stage: "checks"}));
    expect(markup).toContain("Peek ahead was not published");
    expect(markup).toContain("AAPL");
    expect(markup).toContain("2025-12-04");
    expect(markup).toContain("With data up to 2025-12-08");
    expect(markup).toContain("0.02876");
    expect(markup).toContain("No value");
  });
});
