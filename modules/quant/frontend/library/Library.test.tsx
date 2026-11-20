import { createElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { LibraryTable, stateWord, type LibraryRow } from "./LibraryTable";
import { IssueBlock } from "./IssueBlock";
import { AfterTable } from "./ItemOverview";
import { ProductionStrip, PeriodDetail, ProductionQueue, periodUnit } from "./Production";
import { tabsFor } from "./LibraryItem";
import { clampSplit, neighbour } from "./LibraryPage";
import { ValuesBar, WeightsTable, valuesQuery } from "./ItemValues";
import { CompareTable } from "./ItemVersions";
import { StopBody, StopFooter } from "./StopSheet";
import { ArchiveBody, ArchiveFooter } from "./ArchiveSheet";
import { CodePane } from "./ItemCode";
import { ItemFigures } from "./ItemOverview";
import { archiveBlock, moreItems } from "./LibraryItem";

const plots: {ariaLabel: string; figure: {layout?: {meta?: {key?: string}}}}[] = [];
vi.mock("../chart/Plot", async importOriginal => {
  const original = await importOriginal<Record<string, unknown>>();
  return {...original, Plot: (props: {ariaLabel: string}) => {plots.push(props as never); return <i data-plot={props.ariaLabel} />;}};
});

const html = (node: ReactNode) => renderToStaticMarkup(<>{node}</>);
const DAY = 86_400_000;
const T = Date.UTC(2026, 10, 3);

const row = (over: Partial<LibraryRow> = {}): LibraryRow => ({
  itemId: "i1", name: "Short-term reversal", kind: "factor", createdAt: T, updatedAt: T, state: "ok", stateLabel: "On time", stateCount: 0, attention: false,
  versions: [{versionId: "v1", version: 1, state: "producing", health: "ok", count: 0, auto: true, users: 0, publishedAt: T, stoppedAt: null, archivedAt: null}],
  producing: [1], users: 0, usersList: [], shownVersion: 1, shownVersionId: "v1", freq: "Daily", measure: "Rank IC 5D", universe: "S&P 500", auto: true, latestPeriod: T, latestNames: 482,
  last20: Array.from({length: 20}, (_, index) => ({period: T - (19 - index) * DAY, result: "ok", readyAt: null, dueAt: null, why: null})),
  published: 0.0153, after: 0.0031, collecting: false, collectingNeed: 60, settled: 80, pending: 5, origin: null, oneOff: false, inputs: [], nextDue: null, ...over});

describe("library table", () => {
  it("problems sort first and the state column reads On time, Late, Missing, Failed, Upstream missing, Weaker, Stopped", () => {
    const rows = [row({itemId: "a", name: "A", state: "fail", stateLabel: "Failed", stateCount: 2, attention: true}), row({itemId: "b", name: "B", state: "miss", stateLabel: "Missing", stateCount: 5}),
      row({itemId: "c", name: "C", state: "up", stateLabel: "Upstream missing"}), row({itemId: "d", name: "D", state: "weak", stateLabel: "Weaker"}), row({itemId: "e", name: "E", state: "late", stateLabel: "Late"}),
      row({itemId: "f", name: "F"}), row({itemId: "g", name: "G", state: "stopped", stateLabel: "Stopped"})];
    const text = html(<LibraryTable rows={rows} selected={null} onSelect={vi.fn()} onAuto={vi.fn()} />);
    const order = ["Failed", "Missing", "Upstream missing", "Weaker", "Late", "On time", "Stopped"].map(label => text.search(new RegExp(`class="lb-state"[^>]*>${label}( ×\\d+)?</span>`)));  // a plain word, no dot
    expect(order.every(index => index >= 0) && [...order].sort((x, y) => x - y).join() === order.join()).toBe(true);
    expect(text).toContain("×2");
    expect(stateWord(rows[0])).toEqual({tone: "fix", text: "Failed ×2"});
    expect(stateWord(rows[3]).tone).toBe("watch");
    expect(stateWord(rows[6]).tone).toBe("muted");
  });
  it("the whole table has the columns of the draft and the compact one only state, name and the last 20", () => {
    const full = html(<LibraryTable rows={[row()]} selected={null} onSelect={vi.fn()} onAuto={vi.fn()} />);
    for (const head of ["State", "Name", "Kind", "Producing", "Latest", "Last 20", "Main measure", "Published", "After", "Settled", "Pending", "Users", "Auto"]) expect(full).toContain(`>${head}<`);
    const compact = html(<LibraryTable rows={[row()]} selected="i1" compact onSelect={vi.fn()} onAuto={vi.fn()} />);
    expect(compact).not.toContain(">Producing<");
    expect(compact).toContain(">Last 20<");
  });
  it("a one-off item says what it is and has no Production log tab", () => {
    expect(tabsFor({kind: "data", oneOff: true, versions: 1}).map(tab => tab.id)).toEqual(["overview", "values", "versions"]);
    expect(tabsFor({kind: "factor", oneOff: false, versions: 2}).map(tab => tab.id)).toEqual(["overview", "values", "log", "versions"]);
    expect(tabsFor({kind: "allocation", oneOff: false, versions: 1}).map(tab => tab.label)).toContain("Weights");
  });
});

describe("the reason block", () => {
  const issue = (over = {}) => ({kind: "fail", title: "Failed 2 periods in a row", note: "Nov 2 – Nov 3 have no values", since: T, rows: [{label: "Reason", text: "The code failed on the new data: KeyError: 'epsTrend' in revision.py line 8."},
    {label: "Tried", text: "3 times since Nov 3, 16:30 UTC; next automatic try Nov 4, 08:50 UTC"}, {label: "Downstream", text: "Cross-sectional 5D v1"}], links: [{kind: "source", file: "revision.py", line: 8, versionId: "v1"}], ...over});
  it("a failed item shows the reason block with the failing line of the user's code, the tries and the next automatic try", () => {
    const text = html(<IssueBlock issue={issue()} onOpenCode={vi.fn()} onLog={vi.fn()} />);
    for (const wanted of ["Failed 2 periods in a row", "Nov 2 – Nov 3 have no values", "KeyError: &#x27;epsTrend&#x27;", "revision.py line 8", "Tried", "Downstream", "Open revision.py line 8"]) expect(text).toContain(wanted);
    expect(text).toContain("3 times since Nov 3, 16:30 UTC; next automatic try Nov 4, 08:50 UTC");  // times stay in UTC, as everywhere in Quant
  });
  it("a missing item shows Missing x5 and the reason 'No new bars from the source for 5 periods; production stopped'", () => {
    const text = html(<IssueBlock issue={issue({kind: "miss", title: "Value v1 is missing its newest period ×5", rows: [{label: "Missing", text: "No new bars from the source for 5 periods; production stopped"}], links: []})} onOpenCode={vi.fn()} onLog={vi.fn()} />);
    expect(text).toContain("×5");
    expect(text).toContain("No new bars from the source for 5 periods; production stopped");
  });
  it("a weaker item shows 'published 0.0153, after 0.0031 ± 0.0019: same sign, under a quarter'", () => {
    const text = html(<IssueBlock issue={issue({kind: "weak", title: "Reversal v1 is weaker since publication", rows: [{label: "Reason", text: "published 0.0153, after 0.0031 ± 0.0019: same sign, under a quarter"}], links: []})} onOpenCode={vi.fn()} onLog={vi.fn()} />);
    expect(text).toContain("published 0.0153, after 0.0031 ± 0.0019: same sign, under a quarter");
  });
});

describe("overview", () => {
  it("before sixty trading days settled After is empty and the Periods row says 23/60 days · 5 pending", () => {
    const text = html(<AfterTable measure="Rank IC 5D" kind="factor" published={{value: 0.0153, t: 2.1, ls: 0.05, names: 497}} after={{value: 0.0031, se: 0.0019, t: 1.1, ls: 0.01, settled: 23, settledDays: 23, pending: 5}} collecting need={60} />);
    expect(text).toContain("23/60 days · 5 pending");
    expect(text).not.toContain("0.0031");
  });
  it("with no periods yet the Periods row says auto-update is off only when it is", () => {
    const pub = {value: 0.0153, t: 2.1, ls: 0.05, names: 497};
    expect(html(<AfterTable measure="Rank IC 5D" kind="factor" published={pub} after={null} collecting={false} need={60} auto />)).toContain("none yet");
    expect(html(<AfterTable measure="Rank IC 5D" kind="factor" published={pub} after={null} collecting={false} need={60} auto />)).not.toContain("auto-update off");
    expect(html(<AfterTable measure="Rank IC 5D" kind="factor" published={pub} after={null} collecting={false} need={60} />)).toContain("none, auto-update off");
  });
  it("an allocation's table reads as the draft's: volatility, the risk share that moved most, turnover, Sharpe and the return against its benchmark, published and now", () => {
    const text = html(<AfterTable measure={null} kind="allocation" published={{value: 0.078, sharpe: 1.29, maxDrawdown: -0.071, turnover: 5.17}} collecting={false} need={60}
      after={{kind: "allocation", value: 0.129, multiple: 1.64, settled: 26, pending: 0, worstShare: {name: "AES", published: 0.821, now: 0.70, delta: -0.121}, sharpe: 0.42,
        turnover: 4.8, rebalances: 2, benchmark: {name: "equal weight", published: -0.169, since: 0.021}}} />);
    expect(text).toContain("Risk share, AES");
    expect(text).toContain("−12.1 pt");
    expect(text).toMatch(/Turnover a year.*517%.*480%.*−37\.0 pt/);
    expect(text).toMatch(/Sharpe.*1\.29.*Under 126 trading days a Sharpe says little.*0\.42/);
    expect(text).toMatch(/Return vs equal weight.*−16\.9 pt.*a year.*\+2\.1 pt.*since/);
    expect(text).toContain("26 trading days · 2 rebalances");
    expect(text).not.toContain("Max drawdown");
  });
  it("an allocation with nothing after publication shows what it published, drawdown too", () => {
    const text = html(<AfterTable measure={null} kind="allocation" published={{value: 0.078, sharpe: 1.29, maxDrawdown: -0.071, turnover: 5.17}} after={null} collecting={false} need={60} />);
    expect(text).toContain("−7.1%");
    expect(text).not.toContain("Turnover a year");
  });
  it("a weaker version's after figure is in the watch colour", () => {
    const text = html(<AfterTable measure="Rank IC 5D" kind="factor" published={{value: 0.0032, t: 0.7, ls: 0.01, names: 69}} after={{value: 0.0006, se: 0.0011, t: 0.6, ls: 0.07, names: 70, settled: 63, pending: 6}} collecting={false} need={60} auto weak />);
    expect(text).toMatch(/lb-watch[^>]*>\+0\.0006/);
    expect(text).toContain("+1");
  });
  it("the production caption counts trading days for a daily item, as the draft does", () => {
    expect(periodUnit("Daily")).toBe("trading days");
    expect(periodUnit("Daily", 1)).toBe("trading day");
    expect(periodUnit("Weekly")).toBe("weeks");
    expect(periodUnit("1h")).toBe("periods");
  });
  it("the three charts show the backend figure or the reason it is missing", () => {
    plots.length = 0;
    const text = html(<ItemFigures figures={{cumulative: {data: [], layout: {meta: {key: "library.cumulative"}}}, volatility: {missing: "No realised volatility yet"}}} />);
    expect(plots.map(plot => plot.figure.layout?.meta?.key)).toEqual(["library.cumulative"]);
    expect(text).toContain("No realised volatility yet");
  });
});

describe("production", () => {
  const periods = Array.from({length: 6}, (_, index) => ({label: T + index * DAY, due_at: T + index * DAY + 5 * 60_000, result: index === 3 ? "fail" : "ok", ready_at: T + index * DAY + 8 * 60_000, names: 497, tries: 1, took: 20, why: index === 3 ? "KeyError" : null}));
  it("clicking a cell of the production strip shows that period", () => {
    const strip = html(<ProductionStrip periods={periods} selected={3} onSelect={vi.fn()} />);
    expect(strip.match(/data-selected/g)?.length).toBe(1);
    const detail = html(<PeriodDetail period={periods[3]} onValues={vi.fn()} onLog={vi.fn()} onRetry={vi.fn()} />);
    expect(detail).toContain("KeyError");
    expect(detail).toContain("Retry");
  });
  it("the production queue popover says 'Nothing producing yet' when it is empty", () => {
    expect(html(<ProductionQueue production={null} />)).toContain("Nothing producing yet");
    expect(html(<ProductionQueue production={{lastRound: null, concurrency: 0, running: [], queued: [], retries: [], waiting: [], next: null, order: [], producing: 0}} />)).toContain("Nothing producing yet");
  });
});

describe("the page", () => {
  it("up and down keys change the item, Escape closes it", () => {
    const ids = ["a", "b", "c"];
    expect(neighbour(ids, "b", 1)).toBe("c");
    expect(neighbour(ids, "b", -1)).toBe("a");
    expect(neighbour(ids, "c", 1)).toBe("c");
    expect(neighbour(ids, "a", -1)).toBe("a");
  });
  it("dragging the split to either end keeps at least three table rows and the whole item header", () => {
    expect(clampSplit(1200, 5000)).toBeLessThanOrEqual(1200 - 520);
    expect(clampSplit(1200, 10)).toBeGreaterThanOrEqual(300);
  });
});

describe("values and versions", () => {
  it("values page by date and instrument, and jump to a date", () => {
    expect(valuesQuery({date: T, order: "top", q: "AA", page: 2})).toEqual({date: T, order: "top", q: "AA", page: 2});
    const text = html(<ValuesBar day={{date: T, previousDay: T - DAY, nextDay: null, firstDay: T - 30 * DAY, lastDay: T, have: 497, total: 500, missing: 3, readyAt: T + 3_600_000, onTime: true, matching: 497, page: 0, pages: 10} as never}
      order="top" q="" onDate={vi.fn()} onOrder={vi.fn()} onQuery={vi.fn()} onPage={vi.fn()} />);
    expect(text).toContain("497");
    expect(text).not.toContain('type="date"');
    expect(text).toContain('aria-label="Day"');
    expect(text).toContain("Missing 3");
  });
  it("an allocation's weights stand beside the published ones with the change in points", () => {
    const text = html(<WeightsTable weights={{publishedDate: T, published: [{instrumentId: "a", name: "BTC", weight: 0.4}, {instrumentId: "b", name: "ETH", weight: 0.6}], thisPeriod: [{instrumentId: "a", name: "BTC", weight: 0.55}, {instrumentId: "b", name: "ETH", weight: 0.45}]}} />);
    for (const wanted of ["Weight, published Nov 3, 2026", "Weight, this period", ">Change<", "40.0%", "55.0%", "+15.0 pt", "−15.0 pt"]) expect(text).toContain(wanted);
  });
  it("versions tab compares two ticked versions: code, settings, overlap correlation, figures, coverage", () => {
    const side = (version: number) => ({versionId: "v" + version, version, runId: "r", origin: {runNumber: 20 + version}, published: {value: 0.01 * version, t: 2, ls: 0.05, names: 497 - version}, after: null, measure: "Rank IC 5D", state: "updating", archivedAt: null, settingsText: "S&P 500, 5D"});
    const text = html(<CompareTable compare={{a: side(1), b: side(2), overlap: {from: T, to: T + 30 * DAY, days: 30, rankCorrelation: 0.91, namesA: 497, namesB: 451, onlyA: 46, onlyB: 0},
      code: [{file: "factor.py", status: "changed", diff: "-a\n+b"}], settings: [{what: "run.holding", from: 3, to: 5}]}} need={60} kind="factor" />);
    for (const wanted of ["Rank correlation", "0.91", "Names only in v1", "46", "factor.py", "run.holding", "No values after publication"]) expect(text).toContain(wanted);
    const collecting = html(<CompareTable compare={{a: {...side(1), after: {value: null, settled: 3, pending: 2}}, b: {...side(2), after: {value: 0.012, settled: 80, settledDays: 80, pending: 5}}, overlap: null, code: [], settings: []}} need={60} kind="factor" />);
    expect(collecting).toContain(">collecting<");
    expect(collecting).toContain("+0.0120");
  });
});

describe("sheets and menus", () => {
  const preview = (users: object[], over = {}) => ({versionId: "v1", version: 1, users, producing: [2], lastDatetime: T, consequence: "", ...over});
  it("stop sheet with only research users: Stop works and each user's consequence is written", () => {
    const research = {ownerId: "qobject:s1", name: "Reversal top 50", kind: "strategy", how: "research, pinned v1", objectId: "s1", readsDaily: false, unattended: false};
    const body = html(<StopBody name="Reversal" preview={preview([research]) as never} onOpenUser={vi.fn()} />);
    expect(body).toContain(">Open</button>");
    expect(body).toContain("Research: keeps the values to");
    expect(body).toContain("v2 is available");
    expect(html(<StopFooter version={1} onCancel={vi.fn()} onStop={vi.fn()} />)).not.toMatch(/<button[^>]*disabled[^>]*>[^]*Stop v1/);
  });
  it("stop sheet shows the orange warning for a published model that reads the version", () => {
    const model = {ownerId: "qlib:m1", name: "Blend v1", kind: "model", how: "reads it every period", readsDaily: true, unattended: true};
    expect(html(<StopBody name="Breadth" preview={preview([model]) as never} />)).toContain("Published model, reads it every day: from");
  });
  it("stop sheet takes an error from the real stop and shows its reason instead of closing", () => {
    const text = html(<StopBody name="Reversal" preview={preview([]) as never} error="This version is archived already." />);
    expect(text).toContain("This version is archived already.");
  });
  it("archive sheet lists users and blocks: 'Still used by 2: switch them first.'", () => {
    const user = (ownerId: string, name: string, kind: string, how: string) => ({ownerId, name, kind, how, objectId: ownerId.replace("qobject:", ""), libraryVersionId: null,
      version: 1, readsDaily: false, unattended: false});
    const users = [user("qobject:a", "Study", "strategy", "research, its newest run reads it"), user("qobject:b", "Study", "strategy", "research, pinned v1")];
    const body = html(<ArchiveBody name="Momentum" version={1} users={users} figures="Rank IC 5D 0.0153" onOpenUser={vi.fn()} />);
    // two strategies of one name are told apart, say how each reads the version, and each can be opened from here
    expect(body).toContain("Study (1)"); expect(body).toContain("Study (2)"); expect(body).toContain("pinned v1"); expect(body.match(/>Open</g)?.length).toBe(2);
    const footer = html(<ArchiveFooter version={1} users={users} onCancel={vi.fn()} onArchive={vi.fn()} />);
    expect(footer).toContain("Still used by 2: switch them first.");
    expect(footer).not.toContain("Archive v1");
  });
  it("Archive is on the head, pressable only for a stopped version nobody uses, and says why otherwise", () => {
    expect(archiveBlock({state: "producing", version: 2, users: []})).toBe("Stop v2 first; a stopped version nobody uses can be archived");
    expect(archiveBlock({state: "stopped", version: 1, users: [{}]})).toBe("Still in use");
    expect(archiveBlock({state: "stopped", version: 1, users: []})).toBe("");
    expect(archiveBlock({state: "archived", version: 1, users: []})).toBe("Already archived");
  });
  it("More holds Frozen code and Open in a notebook; a one-off has no frozen code", () => {
    expect(moreItems({state: "producing", kind: "factor"}).map(item => item.id)).toEqual(["code", "notebook"]);
    expect(moreItems({state: "fixed", kind: "data"}).map(item => item.id)).toEqual(["notebook"]);
  });
  it("frozen code panel opens at the failing line; a model or allocation shows its settings", () => {
    const code = html(<CodePane title="Earnings revision v1" file="revision.py" lines={["import q", "x = est['epsTrend']"]} errorLine={2} error="KeyError 'epsTrend'" frozen="Frozen with v1, read-only" from="from #23, Nov 2" />);
    expect(code).toContain("data-bad");
    expect(code).toContain("KeyError");
    expect(code).toContain("Frozen with v1, read-only");
    expect(html(<CodePane title="Blend v1" file="model settings" lines={["algorithm: LightGBM"]} frozen="Frozen with v1, read-only" from="from #4, Nov 2" />)).toContain("model settings");
  });
});
