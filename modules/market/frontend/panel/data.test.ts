import { describe, expect, it, vi } from "vitest";
import type { ModuleContext } from "@talyx/sdk";
import { datasetsFor, kindLabel, marketCall, seriesOf, sourceOf, tasksOf, testResultOf, type JobRow, type Library, type SourceRow } from "./data";

import { offersFor, starters, type Pickable } from "./GetDataDialog";

it("only offers matches for the query currently in the Get data input", () => {
  const apple: Pickable = { id: "apple", label: "AAPL", detail: "Apple", count: 1, market: "us", target: { symbol: "AAPL" } };
  const reply = { query: "AAPL", items: [apple] };
  expect(offersFor(" AAPL ", reply, [])).toEqual([apple]);
  expect(offersFor("QAXYYUNLISTED", reply, [])).toEqual([]);
  expect(offersFor("AAPL", reply, [apple])).toEqual([]);
  expect(offersFor("", reply, [])).toEqual(starters);
});

const row = (symbol: string, extra: Partial<Library["series"][number]> = {}): Library["series"][number] => ({
  instrumentId: `vi2_${symbol}`, symbol, venue: "US", kind: "ohlcv", timeframe: "1d", rows: 252, first: "2024-01-02", last: "2024-12-31", gaps: 0,
  bytes: 9000, source: "yfinance", status: "current", name: `${symbol} Inc.`, delistedAt: null, importId: null, former: null, ...extra,
});
/** The library answer as the backend sends it: series as columns and rows. */
const columnar = (rows: Record<string, unknown>[], imports: Library["imports"]): Library => {
  const columns = Object.keys(rows[0]!) as Library["series"]["columns"];
  return { now: "0", imports, series: { columns, rows: rows.map(item => columns.map(name => item[name])) } };
};

describe("the Market panel's rows from Market's library", () => {
  it("groups stored series into datasets as read_data view=local does", () => {
    const library = columnar([
      row("AAPL"), row("MSFT", { gaps: 2 }), row("BTC/USDT:USDT", { venue: "okx", timeframe: "1m", rows: 1440, source: "okx", first: "2024-11-04T00:00:00Z", last: "2024-11-04T23:59:00Z" }),
      row("XYZQ", { kind: "import:us-minute-1a2b3c4d", importId: "us-minute-1a2b3c4d", source: "import:us-minute-1a2b3c4d" }),
      row("SIVB", { status: "delisted", delistedAt: "1678406400000000000", former: [{ code: "SIVBQ", until: null }] }),
    ], [{ importId: "us-minute-1a2b3c4d", name: "us-minute", createdAt: "0", rows: 252 }]);
    const series = seriesOf(library, { yfinance: "Yahoo Finance", okx: "OKX" }, new Set());
    const sets = datasetsFor(series);
    expect(sets.map(set => [set.market, set.kind, set.series.length, set.rows, set.gaps, set.sources])).toEqual([
      ["US stocks", "Daily", 3, 756, 2, ["Yahoo Finance"]],
      ["OKX", "1 minute", 1, 1440, 0, ["OKX"]],
      ["Imported", "us-minute", 1, 252, 0, ["us-minute"]],
    ]);
    const sivb = series.find(item => item.code === "SIVB")!;
    expect([sivb.status, sivb.statusText, sivb.identity?.[0]?.code]).toEqual(["delisted", "Last traded 2023-03-10", "SIVBQ"]);
    expect(series.find(item => item.code === "XYZQ")?.importId).toBe("us-minute-1a2b3c4d");
  });

  it("names periods, jobs and test answers in the panel's words", () => {
    expect(["1d", "1m", "5m", "1h", "4h"].map(tf => kindLabel("ohlcv", tf))).toEqual(["Daily", "1 minute", "5 minutes", "1 hour", "4 hours"]);
    expect(kindLabel("funding_rate", "8h")).toBe("Funding");
    const job: JobRow = { jobId: "job_1", state: "interrupted", createdAt: "0", request: { targets: [{ index: "^GSPC" }], targetCount: 1, kinds: [{ kind: "bars", timeframe: "1d" }], start: "2016-01-01" },
      done: 8, total: 20, rows: 2016, bytes: 70000, failed: 0, waitingFor: null, waitingUntil: null, interruption: "workspace_closed" };
    expect(tasksOf([job, { ...job, jobId: "job_2", state: "completed" }, { ...job, jobId: "job_3", state: "cancelled" }]))
      .toEqual([expect.objectContaining({ id: "job_1", state: "interrupted", title: "^GSPC members · Daily · from 2016-01-01", text: "Paused when the workspace closed" })]);
    // A job of many targets is listed by its first and a count (the list is polled; it does not carry them all).
    expect(tasksOf([{ ...job, request: { ...job.request, targets: [{ symbol: "AAPL" }], targetCount: 1058 } }])[0]!.title).toBe("AAPL +1057 · Daily · from 2016-01-01");
    expect(testResultOf({ source: "okx", latencyMs: 52, unreachable: null, httpStatus: null, passed: 7, failed: [] })).toEqual({ ms: 52 });
    expect(testResultOf({ source: "bybit", latencyMs: null, unreachable: "http_error", httpStatus: 451, passed: 0, failed: [] }).fail).toBe("HTTP 451");
    expect(testResultOf({ source: "ib", latencyMs: null, unreachable: "local_program_off", httpStatus: null, passed: 0, failed: [] }).tone).toBe("off");
  });

  it("reads a source's last test as write_source answered it", () => {
    const test = { source: "bybit", latencyMs: null, unreachable: "http_error", httpStatus: 403, passed: 0, failed: [] };
    const row: SourceRow = { source: { sourceId: "bybit", name: "Bybit" }, state: "working", problem: null, keyConfigured: null, paid: false,
      enabled: true, credentials: "none", localGateway: false, coverage: [], settings: { schema: [], values: {} }, lastTest: { tests: [test], changed: false } };
    expect(sourceOf(row).lastTest).toEqual(test);
    expect(testResultOf(sourceOf(row).lastTest!).fail).toBe("HTTP 403");
    expect(sourceOf({ ...row, lastTest: null }).lastTest).toBeNull();
  });
});

describe("the panel's calls to Market's public actions", () => {
  const context = (states: unknown[]) => {
    const tasks = [...states];
    const call = vi.fn(async (name: string) => name === "platform.tasks.get" ? tasks.shift() : { taskId: "task-1" });
    return { ctx: { actions: { call } } as unknown as ModuleContext, call };
  };

  it("waits for the task the action started (platform.tasks.get) and answers its result", async () => {
    const { ctx, call } = context([{ state: "running" }, { state: "completed", result: { tests: [], changed: false } }]);
    vi.useFakeTimers();
    const answer = marketCall(ctx, "write_source", { operation: { op: "test_all" } });
    await vi.runAllTimersAsync();
    vi.useRealTimers();
    await expect(answer).resolves.toEqual({ tests: [], changed: false });
    expect(call.mock.calls.map(([name, input]) => [name, input])).toEqual([
      ["market.write_source", { operation: { op: "test_all" } }], ["platform.tasks.get", { taskId: "task-1" }], ["platform.tasks.get", { taskId: "task-1" }]]);
  });

  it("answers a failed task with the task's error", async () => {
    const { ctx } = context([{ state: "failed", sequence: 1, error: { code: "invalid_input", message: "No folder" } }]);
    await expect(marketCall(ctx, "export", {} as never)).rejects.toEqual({ code: "invalid_input", message: "No folder" });
  });
});
