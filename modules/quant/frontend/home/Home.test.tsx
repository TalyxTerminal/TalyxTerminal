import { type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { AttentionSection, QueueSection, RecentSection, NewSection, elapsedText } from "./QuantHome";
import { KindTable, sortRows, columnsFor } from "./ObjectsPage";
import { whenText } from "./cells";

const html = (node: ReactNode) => renderToStaticMarkup(<>{node}</>);
const T = Date.UTC(2026, 10, 3);

const attention = (over = {}) => ({tone: "fix", label: "Failed", state: "fail", count: 1, subject: {kind: "library", id: "i1", name: "Earnings revision", version: 1, versionId: "v1", itemKind: "factor"},
  why: "KeyError 'epsTrend'", since: T, action: {id: "retry", label: "Retry now"}, ...over});
const job = (over = {}) => ({subject: {kind: "library", id: "i", name: "Risk parity v1", versionId: "v"}, what: "Retry at Nov 4, 16:30 UTC, try 2", at: T, state: "queued", can_stop: false, ...over});
const object = (over = {}) => ({id: "o1", kind: "factor", name: "Short-term reversal", folder: null, workspaceId: "ws", inThisWorkspace: true, changedAt: T, updatedAt: T, runsCount: 4,
  representative: {run: "r3", number: 3, how: "published", version: "v2"}, keyNumbers: {rank_ic: 0.0312, t: 3.4, long_short_net: 0.081, turnover: 0.42, horizon: "5D"}, checks: {passed: 7, total: 9},
  library: {versions: [], text: "v2"}, lastRun: null, workspace: "ws", ...over});

describe("quant home", () => {
  it("nothing to handle is one line", () => {
    const text = html(<AttentionSection items={[]} watch={0} onAction={vi.fn()} onOpen={vi.fn()} onLibrary={vi.fn()} summary={{onTime: 12}} />);
    const words = text.replace(/<[^>]+>/g, "");
    expect(words).toContain("Nothing to handle");
    expect(words).toContain("12 library items on time");
    expect(text).not.toContain("<table");
  });
  it("each thing to handle has one action: Retry, Switch to v2, Review this period", () => {
    const text = html(<AttentionSection items={[attention(), attention({state: "pinned_stopped", label: "On a stopped version", subject: {kind: "object", id: "s1", name: "Reversal top 50", itemKind: "strategy"}, why: "pins Reversal v1, stopped Nov 3; its values end that day. v2 is producing",
      action: {id: "switch_version", label: "Switch to v2", versionId: "v2", itemId: "i2"}}), attention({state: "drift", label: "Risk drift", action: {id: "review", label: "Review this period"}})]}
      watch={0} onAction={vi.fn()} onOpen={vi.fn()} onLibrary={vi.fn()} summary={{onTime: 0}} />);
    for (const wanted of ["Retry now", "Switch to v2", "Review this period"]) expect(text).toContain(wanted);
  });
  it("an object pinned on a stopped version reads 'On a stopped version' with Switch to v2", () => {
    const text = html(<AttentionSection items={[attention({state: "pinned_stopped", label: "On a stopped version", action: {id: "switch_version", label: "Switch to v2"}})]} watch={0} onAction={vi.fn()} onOpen={vi.fn()} onLibrary={vi.fn()} summary={{onTime: 0}} />);
    expect(text).toContain("On a stopped version");
    expect(text).toContain("Switch to v2");
  });
  it("weaker and late items show only a link to the library with a count", () => {
    const text = html(<AttentionSection items={[attention()]} watch={3} onAction={vi.fn()} onOpen={vi.fn()} onLibrary={vi.fn()} summary={{onTime: 0}} />);
    expect(text).toContain("3 more to watch in the library");
  });
  it("a queued batch row reads 'Batch 6 · #18, #19 queued; 2 train at a time' with Cancel", () => {
    const text = html(<QueueSection jobs={[job({subject: {kind: "object", id: "m1", name: "Cross-sectional 5D"}, what: "Batch 6 · #18, #19 queued; 2 train at a time", state: "queued", can_stop: true})]} onStop={vi.fn()} />);
    expect(text).toContain("Batch 6 · #18, #19 queued; 2 train at a time");
    expect(text).toContain(">Cancel<");
  });
  it("a time the backend writes in UTC stays in UTC, as every time in Quant", () => {
    const text = html(<QueueSection jobs={[job({what: "Retry at Nov 4, 16:30 UTC, try 2", at: Date.UTC(2026, 10, 4, 16, 30)})]} onStop={vi.fn()} />);
    expect(text).toContain("Retry at Nov 4, 16:30 UTC, try 2");
    expect(whenText(Date.UTC(2026, 10, 4, 23, 30), Date.UTC(2026, 10, 4, 23, 50))).toBe("today 23:30 UTC");
  });
  it("a running job's time is how long it has run, not the moment it started", () => {
    const now = Date.UTC(2026, 10, 8, 12);
    expect(elapsedText(now - (12 * 60 + 40) * 1000, now)).toBe("12:40");
    expect(elapsedText(now - 3725 * 1000, now)).toBe("1:02:05");
    expect(html(<QueueSection jobs={[job({subject: {kind: "library", id: "i", name: "Reversal v1"}, what: "Library update", state: "running", elapsed: now - 60_000, can_stop: true})]} onStop={vi.fn()} />)).not.toContain(String(now - 60_000));
  });
  it("running and waiting rows show progress and a way to stop", () => {
    const text = html(<QueueSection jobs={[job({subject: {kind: "object", id: "f1", name: "Short-term reversal"}, what: "#23 full report", state: "running", progress: 0.64, can_stop: true}), job()]} onStop={vi.fn()} />);
    expect(text).toContain("#23 full report");
    expect(text).toContain(">Stop<");
    expect(text).toContain("Risk parity v1");
    expect(text).toContain("1 running, 1 queued");   // the count is the rows'
  });
  it("recently changed objects show the representative run's key numbers", () => {
    const text = html(<RecentSection items={[object()]} total={12} onOpen={vi.fn()} onAll={vi.fn()} />);
    expect(text).toContain("Short-term reversal");
    expect(text).toContain("+0.0312");
    expect(text).toContain("#3");
    expect(text).toContain("published");
  });
  it("a notebook in the recent list shows what it reads and its cells", () => {
    const text = html(<RecentSection items={[object({id: "n1", kind: "notebook", name: "Why it weakened", representative: {run: null, number: null, how: "none"}, keyNumbers: null, checks: null, library: null, cells: 8, reads: "Reversal 1M v2"})]} total={1} onOpen={vi.fn()} onAll={vi.fn()} />);
    expect(text).toContain("Reversal 1M v2");
    expect(text).toContain("8 cells");
    expect(text).not.toContain("No successful run");
  });
  it("new object starts empty or from a preset", () => {
    const text = html(<NewSection kinds={["factor", "model", "strategy"]} presets={[{kind: "factor", id: "rev", label: "Short-term reversal"}] as never} onCreate={vi.fn()} />);
    expect(text).toContain("Blank formula");
    expect(text).toContain("Short-term reversal");
  });
  it("each row's button sends that row's action", () => {
    const item = attention();
    const tree = AttentionSection({items: [item as never], watch: 0, summary: {onTime: 0}, onAction: vi.fn(), onOpen: vi.fn(), onLibrary: vi.fn()});
    expect(html(tree)).toContain("Retry now");
  });
});

describe("all objects", () => {
  const table = (rows: ReturnType<typeof object>[], showWorkspace = false, kind = "factor") => html(<KindTable kind={kind as never} rows={rows as never} showWorkspace={showWorkspace} sort={null} onSort={vi.fn()} onOpen={vi.fn()} />);
  it("lists every object by kind with its representative run, key numbers and checks", () => {
    const text = table([object()]);
    for (const wanted of ["Factors", "1 · 0 not published", "Rank IC", "Long–short, net", "Representative run", "#3", "published", "7/9", ">Changed<", "+0.0312", "5D"]) expect(text).toContain(wanted);
    expect(columnsFor("model").map(column => column.label)).toEqual(["Test Rank IC", "ICIR", "Long–short, net"]);
  });
  it("a candidate is marked and counted as not published", () => {
    const text = table([object({library: null})]);
    expect(text).toContain("candidate");
    expect(text).toContain("1 · 1 not published");
  });
  it("an object without a successful run says so, with why, and no key numbers", () => {
    const text = table([object({representative: {run: null, number: null, how: "none", note: "#4 failed: KeyError"}, keyNumbers: null, checks: null})]);
    expect(text).toContain("No successful run");
    expect(text).toContain("#4 failed: KeyError");
    expect(text).not.toContain("+0.0312");
  });
  it("notebooks show what they read and their cells, without runs or checks", () => {
    const text = table([object({kind: "notebook", cells: 8, reads: "Reversal 1M v2", library: null, checks: null})], false, "notebook");
    for (const wanted of [">Reads<", "Reversal 1M v2", ">Cells<", ">8<"]) expect(text).toContain(wanted);
    for (const absent of ["Representative run", ">Checks", ">Runs", "not published"]) expect(text).not.toContain(absent);
  });
  it("columns sort, an empty value last", () => {
    const rows = [object({id: "a", name: "A", keyNumbers: {rank_ic: 0.01}}), object({id: "b", name: "B", keyNumbers: {rank_ic: 0.03}}), object({id: "c", name: "C", keyNumbers: null})];
    expect(sortRows("factor", rows as never, {label: "Rank IC", dir: -1}).map(row => row.id)).toEqual(["b", "a", "c"]);
    expect(sortRows("factor", rows as never, {label: "Name", dir: 1}).map(row => row.id)).toEqual(["a", "b", "c"]);
  });
  it("This workspace and All switch; All adds a Workspace column after Folder", () => {
    const here = table([object()]), everywhere = table([object(), object({id: "o2", inThisWorkspace: false, workspace: "Crypto desk"})], true);
    expect(here).not.toContain(">Workspace<");
    expect(everywhere.indexOf(">Folder")).toBeLessThan(everywhere.indexOf(">Workspace<"));
    expect(everywhere).toContain("Crypto desk");
  });
});
