import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import * as Data from "./DataSettings";
import { lastFullDay, monthsBack, periodText, rangeLabel, untilDay } from "./objects";

const INDEX_INSTRUMENT = {instrumentId: "vi2:index", symbol: "^GSPC", displaySymbol: "S&P 500", venueId: "US"};
const INDEX = [{index: INDEX_INSTRUMENT, source: {sourceId: "slickcharts", name: "Slickcharts"}, start: null, end: null, pointInTime: true, survivorshipBias: false, note: null}] as never;

describe("Data settings: the last day is the latest close or a date", () => {
  it("a new factor's data go to the latest close", () => {
    expect(Data.defaultResearchData(INDEX)).toEqual({index: {instrument: INDEX_INSTRUMENT}, timeframe: "1d", since: "2016-01-01", until: "latest"});
  });
  it("the day behind 'latest' is the last full day for showing; a date is itself", () => {
    expect(untilDay({until: "latest"})).toBe(lastFullDay());
    expect(untilDay({until: "2026-01-31"})).toBe("2026-01-31");
    expect(untilDay({})).toBeUndefined();
  });
  it("the head's period says latest, and a range ending at the latest close still reads as 2Y", () => {
    expect(periodText("2016-01-01", "latest")).toBe("Jan 2016 – latest");
    expect(periodText("2016-01-01", "2026-10-30")).toBe("Jan 2016 – Oct 2026");
    expect(rangeLabel(monthsBack(24), "latest")).toBe("2Y");
    expect(rangeLabel("2016-01-01", "latest")).toBe("2016-01-01 – latest");
  });
  it("the To row offers Latest close and A date, and shows a date box only for a date", () => {
    const UntilField = (Data as Record<string, unknown>).UntilField as (props: {until: string | undefined; since: string | undefined; onChange(until: string): void}) => JSX.Element;
    expect(UntilField).toBeTypeOf("function");
    const latest = renderToStaticMarkup(createElement(UntilField, {until: "latest", since: "2016-01-01", onChange: vi.fn()}));
    expect(latest).toContain("Latest close");
    expect(latest).not.toContain('aria-label="To" value');
    const fixed = renderToStaticMarkup(createElement(UntilField, {until: "2026-01-31", since: "2016-01-01", onChange: vi.fn()}));
    expect(fixed).toContain("A date");
    expect(fixed).toContain('value="Jan 31, 2026"'); expect(fixed).not.toContain('type="date"');  // the app's way of writing a day, not the browser's date box
    expect(fixed).toContain("Jan 31, 2026");
  });
  it("the range presets end at the latest close and the settings carry no explanation", () => {
    const source = readFileSync(new URL("./DataSettings.tsx", import.meta.url), "utf8");
    expect(source).toMatch(/until: "latest"/);
    expect(source).not.toContain("Known once the range starts within the history");
  });
});

describe("Data settings: only the routes Market has verified are offered", () => {
  const row = (extra: object) => ({capability: "ohlcv", source: {sourceId: "okx", name: "OKX"}, local: null,
    available: {periods: ["1m", "1d"], earliest: null, live: true}, status: "not_stored", ...extra});
  it("offers the source base periods and supported integral aggregations", () => {
    expect(Data.verifiedTimeframes([row({})] as never)).toEqual(["1m", "5m", "15m", "30m", "1h", "4h", "1d"]);
    expect(Data.verifiedTimeframes([row({available: {periods: ["1h"]}})] as never)).toEqual(["1h", "4h"]);
    expect(Data.verifiedTimeframes([row({available: {periods: ["1d"]}})] as never)).toEqual(["1d"]);
    expect(Data.verifiedTimeframes([row({available: {periods: []}})] as never)).toEqual([]);
    expect(Data.verifiedTimeframes([row({capability: "funding_rate"})] as never)).toEqual([]);
  });
  it("Fill with offers only verified bars finer than the signal bars", () => {
    expect(Data.fillChoices(["1m", "5m", "1h", "1d"], "1h")).toEqual(["5m", "1m"]);
    expect(Data.fillChoices(["5m", "1h", "1d"], "1h")).toEqual(["5m"]);
    expect(Data.fillChoices(["1m", "5m", "1h"], "5m")).toEqual(["1m"]);
    expect(Data.fillChoices(["1h", "1d"], "1d")).toEqual([]);
  });
  it("Extra series lists what Market has for the instrument and nothing it lacks", () => {
    const rows = [row({}), row({capability: "funding_rate"}), row({capability: "mark_price", source: null})] as never;
    expect(Data.extraSeriesOf(rows)).toEqual(["Funding rate"]);
    expect(Data.extraSeriesOf([row({capability: "open_interest", source: null, local: {}})] as never)).toEqual(["Open interest"]);
  });
  it("the held-out start is only shown in the data settings (it is set in the run settings)", () => {
    const source = readFileSync(new URL("./DataSettings.tsx", import.meta.url), "utf8");
    expect(source).toContain("Held out from");
    expect(source).not.toMatch(/aria-label="Held out from"/);
  });
});

describe("Data settings show what the data has, not what it lacks", () => {
  it("no extra series is listed as unavailable, no empty watchlist is offered as a disabled button, no note under the list name", () => {
    const read = (file: string) => readFileSync(new URL(file, import.meta.url), "utf8");
    expect(read("./factor/FactorPage.tsx")).not.toMatch(/unavailable: \{/);
    expect(read("./DataSettings.tsx")).not.toMatch(/shared by every workspace/);
    expect(read("./DataSettings.tsx")).not.toMatch(/Choose a symbol, interval and range/);  // the venue row waits for the venue instead of asking for what is chosen
  });
});


it("offers Market's first exchange date instead of rounding a US midnight into the following day", () => {
  const boundary = Data.historyBoundary("history_unavailable", {earliestAt: Date.parse("2024-01-02T05:00:00Z"), earliestDate: "2024-01-02", reason: "History begins here."})!;
  const markup = renderToStaticMarkup(createElement(Data.HistoryStart, {boundary, onStart: vi.fn()}));
  expect(markup).toContain("Start on 2024-01-02");
  expect(markup).not.toContain("Start on 2024-01-03");
});


it("symbols added in quick succession are all kept: each save waits for the one before", async () => {
  // The answers come back out of order; the store keeps whatever lands last.
  let stored: string[] = [];
  const answers: Array<() => void> = [];
  const save = Data.savesInTurn<string[]>(next => new Promise<void>(done => answers.push(() => {stored = next; done();})), vi.fn());
  let latest: string[] = [];
  for (const symbol of ["SPY", "QQQ", "TLT", "GLD"]) {latest = [...latest, symbol]; save(latest);}
  for (let step = 0; step < 4; step += 1) {
    await new Promise(done => setTimeout(done, 0));
    expect(answers).toHaveLength(step + 1);  // the next save has not started before this one answered
    answers[step]();
  }
  await new Promise(done => setTimeout(done, 0));
  expect(stored).toEqual(["SPY", "QQQ", "TLT", "GLD"]);
});

it("a failed save is reported and the next one still goes", async () => {
  const failed = vi.fn();
  const seen: number[] = [];
  const save = Data.savesInTurn<number>(next => next === 1 ? Promise.reject(new Error("no")) : (seen.push(next), Promise.resolve()), failed);
  save(1); save(2);
  await new Promise(done => setTimeout(done, 0));
  expect(failed).toHaveBeenCalledTimes(1);
  expect(seen).toEqual([2]);
});
