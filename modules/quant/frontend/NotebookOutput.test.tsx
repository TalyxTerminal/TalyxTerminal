import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ReportOutput, chooseMime, renderMarkdown } from "./NotebookOutput";

describe("Notebook outputs", () => {
  it("report_output: key numbers, a table, and the count of sections not shown", () => {
    const html = renderToStaticMarkup(createElement(ReportOutput, {report: {sections: 9, metrics: {rank_ic: 0.0153}, tables: [{title: "T", columns: ["a"], rows: [[1], [2]]}], figures: [], moreFigures: 2}}));
    expect(html).toContain("rank_ic") && expect(html).toContain("0.0153");
    expect(html).toContain("<th>a</th>");
    expect(html).toContain("more sections");
  });
  it("a figure-less report that was cut says so", () => {
    const html = renderToStaticMarkup(createElement(ReportOutput, {report: {sections: 1, metrics: {}, tables: [], figures: [], moreFigures: 0, dropped: "Figures dropped: over the 2 MB output limit"}}));
    expect(html).toContain("Figures dropped: over the 2 MB output limit");
  });
  it("the report type is preferred over plain text", () => {
    expect(chooseMime({"text/plain": "x", "application/vnd.talyx.report+json": {}})).toBe("application/vnd.talyx.report+json");
  });
});

describe("The table explorer", () => {
  it("shows the size, each column's kind and missing count, and the rows", async () => {
    const { TableExplorer } = await import("./notebook/TableExplorer");
    const html = renderToStaticMarkup(createElement(TableExplorer, {table: {shape: [1200, 2], size: 9216, rows: [[1.5, "x"], [null, "y"]],
      columns: [{name: "a", type: "float", missing: 1, hist: [1, 2, 3]}, {name: "b", type: "text", missing: 0}]}}));
    for (const text of ["<b>1,200</b> rows", "float, 1 missing", "text, 0 missing", "first 2", "9 KB", "1.5"]) expect(html).toContain(text);
  });
  it("a DataFrame's date index is its first column, shown as a day (UTC from the kernel's json), and columns may share a name", async () => {
    const { TableExplorer } = await import("./notebook/TableExplorer");
    const html = renderToStaticMarkup(createElement(TableExplorer, {table: {shape: [1, 2], size: 96, rows: [["2026-11-04T00:00:00.000Z", 355.78, 1]],
      columns: [{name: "datetime", type: "date", missing: 0}, {name: "a", type: "float", missing: 0}, {name: "a", type: "int", missing: 0}]}}));
    expect(html).toContain(">2026-11-04</td>");
    expect(html).not.toContain("Z</td>");
    expect(html.match(/<th[ >]/g)?.length).toBe(3);
  });
  it("an emptied progress line shows nothing; an empty result still shows", async () => {
    const { NotebookOutput } = await import("./NotebookOutput");
    expect(renderToStaticMarkup(createElement(NotebookOutput, {output: {output_type: "display_data", data: {"text/plain": ""}, metadata: {}}}))).toBe("");
    expect(renderToStaticMarkup(createElement(NotebookOutput, {output: {output_type: "display_data", data: {"text/plain": "Reading bars: 2 of 3 names · 3 s"}, metadata: {}}}))).toContain("Reading bars");
  });
  it("the explorer is preferred to plain text and html", () => {
    expect(chooseMime({"text/html": "<table>", "application/vnd.talyx.table+json": {}})).toBe("application/vnd.talyx.table+json");
  });
});

describe("A one-line backtest's output", () => {
  it("backtest_progress_reads_out: title, steps, bar with time left and Stop while running; run number and no Stop when over", async () => {
    const { BacktestProgress } = await import("./NotebookOutput");
    const base = {strategy: "Reversal long–short", runId: "r", number: 23, elapsed: 14, left: 30, value: .31, steps: [{name: "Environment", done: true}, {name: "Simulating May 13, 2019", done: false, now: true}]};
    const running = renderToStaticMarkup(createElement(BacktestProgress, {run: {...base, state: "running"}, onStop: () => undefined}));
    for (const text of ["Backtest of", "Reversal long–short", "Environment", "Simulating May 13, 2019", "31%", "about 30 s left", "Stop"]) expect(running).toContain(text);
    const over = renderToStaticMarkup(createElement(BacktestProgress, {run: {...base, state: "completed", signal: "ex", since: "2016-01-04", until: "2026-11-03"}, onStop: () => undefined}));
    // The finished block is the draft's line: the strategy with what it read, the data's span, and no number or Stop.
    expect(over).toContain("with <code>ex</code> as its signal");
    expect(over).toContain("Jan 2016 – Nov 3, 2026 · data to Nov 3");
    expect(over).not.toContain("Stop");
    expect(over).not.toContain("left");
    expect(renderToStaticMarkup(createElement(BacktestProgress, {run: {...base, state: "failed"}}))).toContain("#23");
    // A run whose data span is kept as milliseconds reads the same; one it cannot read shows no span rather than "NaN".
    const stamped = renderToStaticMarkup(createElement(BacktestProgress, {run: {...base, state: "completed", since: Date.UTC(2016, 0, 4), until: Date.UTC(2026, 10, 3)}}));
    expect(stamped).toContain("Jan 2016 – Nov 3, 2026");
    const unreadable = renderToStaticMarkup(createElement(BacktestProgress, {run: {...base, state: "completed", since: "x", until: null}}));
    expect(unreadable).not.toContain("NaN");
    expect(unreadable).not.toContain("undefined");
  });
});

describe("plain numbers in a backtest block", () => {
  it("a negative Sharpe takes the true minus", async () => {
    const { plain } = await import("./NotebookOutput");
    expect(plain(-0.76)).toBe("\u22120.76");
    expect(plain(0.8)).toBe("0.80");
    expect(plain(-0.001)).toBe("0.00");
  });
});


it("renders embedded notebook images using the cell attachment", () => {
  const html = renderMarkdown("![cash](attachment:cash.svg)", {"cash.svg": {"image/svg+xml": ["<svg>", "</svg>"]}});
  expect(html).toContain("data:image/svg+xml;charset=utf-8,%3Csvg%3E");
  expect(html).toContain('alt="cash"');
});
