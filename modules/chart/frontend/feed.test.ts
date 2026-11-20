/* A new window of bars leaves no indicator row lined up with the old one (the walkthrough's crash on backfill and on a
   symbol switch read old rows at new bar positions). */
import { describe, expect, it } from "vitest";
import { ChartFeed } from "./feed";

const bars = (times: number[]) => ({ type: "bars" as const, op: "replace" as const, replay: false, priceFormat: { precision: 2, minMove: 0.01 },
  time: times, closeTime: times.map(time => time + 60_000), open: times.map(() => 1), high: times.map(() => 2), low: times.map(() => 0.5), close: times.map(() => 1.5), volume: times.map(() => 10) });

describe("ChartFeed", () => {
  it("places a research window only after its last chunk brings the fourth candle", () => {
    const feed = new ChartFeed(), complete: boolean[] = [];
    feed.listen(event => { if (event.kind === "bars" && event.replace) complete.push(feed.completeWindow); });
    feed.takeBars({ ...bars([0, 60_000]), windowRows: 4 });
    feed.takeBars({ ...bars([120_000]), op: "append" });
    feed.takeBars({ ...bars([180_000]), op: "append" });
    expect(complete).toEqual([false, true]);
    expect(feed.bars.time).toEqual([0, 60_000, 120_000, 180_000]);
  });
  it("moves indicator rows in place to their bars in a new window, and fills the same object on the indicator's replace", () => {
    const feed = new ChartFeed();
    feed.takeBars(bars([60_000, 120_000]));
    feed.takeIndicator({ type: "indicator", instanceId: "rsi", op: "replace", replay: false, meta: [{ id: "p", kind: "plot" }], time: [60_000, 120_000], values: { p: [40, 50] }, colors: { p: ["red", "blue"] } });
    const held = feed.indicators.get("rsi")!;
    feed.takeBars(bars([0, 60_000, 120_000]));
    expect(held.values.p).toEqual([undefined, 40, 50]);
    expect(held.colors.p).toEqual([undefined, "red", "blue"]);
    expect(held.meta).toEqual([{ id: "p", kind: "plot" }]);
    feed.takeBars(bars([0, 30_000, 120_000]));  // a bar the new window lacks takes its row along
    expect(held.values.p).toEqual([undefined, undefined, 50]);
    feed.takeIndicator({ type: "indicator", instanceId: "rsi", op: "replace", replay: false, time: [0, 60_000, 120_000], values: { p: [30, 40, 50] }, colors: {} });
    expect(feed.indicators.get("rsi")).toBe(held);
    expect(held.values.p).toEqual([30, 40, 50]);
  });

  it("folds an indicator's drawing changes into its drawings: changed in place, new on top, gone removed", () => {
    const feed = new ChartFeed();
    feed.takeBars(bars([60_000, 120_000]));
    const box = (id: number, top: number) => ({ id, top }) as never;
    const none = { lines: [], labels: [], polylines: [], linefills: [], tables: [] };
    feed.takeIndicator({ type: "indicator", instanceId: "svp", op: "replace", replay: false, meta: [], time: [60_000], values: {}, colors: {}, drawings: { ...none, boxes: [box(1, 5), box(2, 6), box(3, 7)] } });
    const events: unknown[] = [];
    feed.listen(event => events.push(event));
    feed.takeIndicator({ type: "indicator", instanceId: "svp", op: "update", replay: false, time: [120_000], values: {}, colors: {}, drawingChanges: { boxes: { set: [box(2, 9), box(4, 8)], drop: [1] } } });
    expect(feed.indicators.get("svp")!.drawings).toEqual({ ...none, boxes: [box(2, 9), box(3, 7), box(4, 8)] });
    expect(events).toEqual([{ kind: "indicator", id: "svp", rebuild: false, drawings: true, from: 1 }]);
  });

  it("drops everything of the old series on reset", () => {
    const feed = new ChartFeed();
    feed.takeBars(bars([60_000]));
    feed.takeIndicator({ type: "indicator", instanceId: "bb", op: "replace", replay: false, meta: [], time: [60_000], values: { u: [3] }, colors: {} });
    const events: unknown[] = [];
    feed.listen(event => events.push(event));
    feed.reset(7);
    expect([feed.bars.time.length, feed.loaded, feed.indicators.get("bb")!.values]).toEqual([0, false, {}]);
    expect(events).toEqual([{ kind: "bars", replace: true }]);
  });

  it("holds the stream's items as the old series' until the stream sends the document that brought the new one", () => {
    const feed = new ChartFeed();
    feed.streamReached(4);
    feed.reset(6);
    expect(feed.current).toBe(false);
    feed.streamReached(5);
    expect(feed.current).toBe(false);
    feed.streamReached(6);
    expect(feed.current).toBe(true);
  });
});
