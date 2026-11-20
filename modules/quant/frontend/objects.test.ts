import { describe, expect, it, vi } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { EVENT_READ_EVERY, dataReady, dataSummary, detailText, holdSubscriptions, monthsBack, rangeLabel, throttle, unlessUnloaded, type Instrument } from "./objects";
import { runNumbers } from "./RunHistory";
import { shortPaths } from "./RunOutcome";
import { readShellState } from "./QuantShell";
import { formatNumber } from "./format";

const now = new Date("2026-10-31T08:00:00Z");

describe("Quant shell data settings", () => {
  it("names a range that ends today or yesterday by its length, any other by its dates", () => {
    expect(rangeLabel(monthsBack(24, now), "2026-10-30", now)).toBe("2Y");
    expect(rangeLabel(monthsBack(6, now), "2026-10-31", now)).toBe("6M");
    expect(rangeLabel(monthsBack(24, now), "2026-09-30", now)).toBe(`${monthsBack(24, now)} – 2026-09-30`);
  });
  it("summarises only complete settings", () => {
    const instrument = {displaySymbol: "BTC/USDT"} as Instrument;
    expect(dataSummary({symbols: [{instrument}], timeframe: "1h"})).toBeNull();
    expect(dataSummary({symbols: [{instrument}, {instrument}, {instrument}], timeframe: "1h", since: "2025-01-01", until: "2025-06-30"}))
      .toBe("BTC/USDT +2 1h · Jan 2025 – Jun 2025");
  });
  it("a held-out period closes the summary", () => {
    const lists = [{id: "l", name: "S&P 500", count: 500, createdAt: 0, updatedAt: 0}];
    expect(dataSummary({list: "l", timeframe: "1d", since: "2016-01-01", until: "2026-10-30", heldOutFrom: "2025-01-01"}, lists)).toBe("S&P 500 (500) each day · Jan 2016 – Oct 2026 · held out from Jan 2025");
  });
  it("summarises a research list by name and size, and the code's own rule", () => {
    const lists = [{id: "l", name: "US ten", count: 10, createdAt: 0, updatedAt: 0}];
    expect(dataSummary({list: "l", timeframe: "1d", since: "2025-01-01", until: "2025-06-30"}, lists)).toBe("US ten (10) each day · Jan 2025 – Jun 2025");
    expect(dataSummary({universe: "code", timeframe: "1d", since: "2025-01-01", until: "2025-06-30"})).toBe("Rule in the code each day · Jan 2025 – Jun 2025");
    expect(dataReady({list: "l", timeframe: "1d", since: "2025-01-01", until: "2025-06-30"})).toBe(true);
    expect(dataReady({timeframe: "1d", since: "2025-01-01", until: "2025-06-30"})).toBe(false);
  });
});

describe("Quant shell numbers and errors", () => {
  it("shows error details as dates and ranges, never raw milliseconds", () => {
    expect(detailText({start: 1730332800000, end: 1793401200000})).toBe("2024-10-31 00:00 – 2026-10-30 23:00");
    expect(detailText([{from: 1793401200000, to: 1793491199999}])).toBe("2026-10-30 23:00 – 2026-10-31 23:59");
    expect(detailText(0.0005)).toBe("0.0005");
  });
  it("never writes numbers in scientific notation", () => {
    expect(formatNumber(1.23e-7)).toBe("0.000000123");
    expect(formatNumber(2.5e16)).toBe("25,000,000,000,000,000");
  });
  it("names traceback files relative to the strategy, and library files by name", () => {
    const traceback = 'File "Z:\\data\\modules\\quant\\qruns\\run-1\\source\\pkg\\strategy.py", line 3\nFile "C:\\venv\\Lib\\site-packages\\nautilus\\engine.py", line 9';
    expect(shortPaths(traceback)).toBe('File "pkg/strategy.py", line 3\nFile "engine.py", line 9');
  });
});

describe("Quant shell history and state", () => {
  it("numbers runs from the first, newest first, across pages", () => {
    const page = {items: [{id: "c"}, {id: "b"}], total: 5, offset: 2, nextOffset: 4} as unknown as Parameters<typeof runNumbers>[0];
    expect([...runNumbers(page)]).toEqual([["c", 3], ["b", 2]]);
  });
  it("drops the old research shell's panel state so such a panel opens on the home", () => {
    expect(readShellState({workId: "w", runId: "r", objectId: "o"})).toEqual({});
    expect(readShellState({objectId: "o", tab: "runs"})).toEqual({objectId: "o", tab: "runs"});
    expect(readShellState(null)).toEqual({});
  });
});

describe("reading again on events", () => {
  it("follows a run that reports every 0.2 s once a second, and the last report always shows", () => {
    vi.useFakeTimers({now: 0});
    try {
      const reads: number[] = [];
      const changed = throttle(() => reads.push(Date.now()));
      expect(EVENT_READ_EVERY).toBe(1000);
      for (let at = 0; at <= 3000; at += 200) {changed(); vi.advanceTimersByTime(200);}
      vi.advanceTimersByTime(EVENT_READ_EVERY);
      // 16 reports in 3 s, 5 reads: the first at once, none closer than a second, the last report (at 3 s) read a second later.
      expect(reads).toEqual([0, 1000, 2000, 3000, 4000]);
      for (let index = 1; index < reads.length; index++) expect(reads[index] - reads[index - 1]).toBeGreaterThanOrEqual(EVENT_READ_EVERY);
    } finally {vi.useRealTimers();}
  });

  it("every page that follows run events reads through the throttle, not once per event", () => {
    const root = fileURLToPath(new URL(".", import.meta.url));
    const walk = (dir: string): string[] => readdirSync(dir).flatMap(name => {const path = join(dir, name); return statSync(path).isDirectory() ? walk(path) : [path];});
    const subscribe = 'useEvent(ctx, "quant.object_run_changed"';
    const followers = walk(root).filter(path => /\.tsx?$/.test(path) && !/\.test\.tsx?$/.test(path))
      .map(path => ({path, text: readFileSync(path, "utf8")})).filter(file => file.text.includes(subscribe));
    // The shell's list, home, and the factor, model, strategy and allocation pages (the research page went in c23e1275; a sweep's results
    // follow through the strategy page).
    expect(followers.length).toBeGreaterThanOrEqual(6);
    for (const {path, text} of followers) {
      const throttled = [...text.matchAll(/const (\w+) = useThrottled\(/g)].map(match => match[1]);
      const rest = text.slice(text.indexOf(subscribe));
      // The event's own callback: up to the reconnect callback (which reads everything at once) or the next statement of the component.
      const handler = rest.slice(0, rest.search(/\}, \(\) =>|\r?\n {2}(?! )/));
      expect(throttled.some(name => handler.includes(name)), path).toBe(true);
      expect(handler, path).not.toMatch(/void load\w*\(|setTick\(/);
    }
  });

  it("reads at once after a quiet spell and drops a pending read when cancelled", () => {
    vi.useFakeTimers({now: 0});
    try {
      const reads: number[] = [];
      const changed = throttle(() => reads.push(Date.now()));
      changed();
      vi.advanceTimersByTime(5000);
      changed();
      expect(reads).toEqual([0, 5000]);
      vi.advanceTimersByTime(300);
      changed();
      changed.cancel();
      vi.advanceTimersByTime(5000);
      expect(reads).toEqual([0, 5000]);
    } finally {vi.useRealTimers();}
  });
});

describe("A page's subscriptions when its panel unloads", () => {
  it("closing or subscribing while the panel's module scope is gone (Extract switches the panel) leaves no unhandled rejection", async () => {
    const unloaded = Object.assign(new Error("The panel provider is not active."), {code: "provider_unloaded"});
    const rejections: unknown[] = [];
    const listen = (reason: unknown) => {rejections.push(reason);};
    process.on("unhandledRejection", listen);
    try {
      const closed: string[] = [];
      const held = holdSubscriptions();
      held.keep(Promise.resolve({close: () => {closed.push("first"); return Promise.reject(unloaded);}}));
      held.keep(Promise.reject(unloaded));
      await Promise.resolve(); await Promise.resolve();
      held.close();
      expect(held.live()).toBe(false);
      held.keep(Promise.resolve({close: () => {closed.push("late"); return Promise.reject(unloaded);}}));
      await new Promise(resolve => setTimeout(resolve, 20));
      expect(closed).toEqual(["first", "late"]);
      expect(rejections).toEqual([]);
    } finally {process.off("unhandledRejection", listen);}
    expect(() => unlessUnloaded(Object.assign(new Error("x"), {code: "handler_error"}))).toThrow("x");
  });
});
