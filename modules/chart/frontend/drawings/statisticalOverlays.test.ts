import { describe, expect, it } from "vitest";

import type { GeometryBar as Bar } from "./types";
import {
  anchoredVwapAvailable,
  bucketAnchoredVwapForViewport,
  buildRegressionResult,
  createAnchoredVwapAccumulator,
  regressionSourceValue,
  regressionValueAt,
  updateAnchoredVwapAccumulator,
} from "./statisticalOverlays";

const bars: Bar[] = [
  { time: 1, open: 9, high: 12, low: 8, close: 10, volume: 2 },
  { time: 2, open: 10, high: 15, low: 9, close: 12, volume: 1 },
  { time: 3, open: 13, high: 18, low: 12, close: 15, volume: 3 },
];

describe("Anchored VWAP", () => {
  it("builds the hand-calculated causal HLC3 × volume curve", () => {
    const accumulator = createAnchoredVwapAccumulator([{ timestamp: 1, value: 999 }], bars);
    expect(accumulator.points.map((point) => point.value)).toEqual([
      10,
      32 / 3,
      77 / 6,
    ]);
    expect(anchoredVwapAvailable(accumulator)).toBe(true);
  });

  it("replaces a forming bar and appends the next bar without double counting", () => {
    const current = bars.map((bar) => ({ ...bar }));
    const accumulator = createAnchoredVwapAccumulator([{ timestamp: 1, value: 0 }], current);
    const stablePrefix = accumulator.points[1];
    current[2] = { ...current[2], high: 21, low: 15, close: 18 };
    updateAnchoredVwapAccumulator(accumulator, current, 2);
    expect(accumulator.points).toHaveLength(3);
    expect(accumulator.points[1]).toBe(stablePrefix);
    expect(accumulator.points[2].value).toBe(86 / 6);

    current.push({ time: 4, open: 18, high: 22, low: 16, close: 19, volume: 2 });
    updateAnchoredVwapAccumulator(accumulator, current, 3);
    expect(accumulator.points).toHaveLength(4);
    expect(accumulator.points[1]).toBe(stablePrefix);
    expect(accumulator.points[3].value).toBe((86 + 19 * 2) / 8);
  });

  it("recalculates only the affected suffix for an older replacement", () => {
    const current = bars.map((bar) => ({ ...bar }));
    const accumulator = createAnchoredVwapAccumulator([{ timestamp: 1, value: 0 }], current);
    const first = accumulator.points[0];
    current[1] = { ...current[1], high: 22, low: 18, close: 20 };
    updateAnchoredVwapAccumulator(accumulator, current, 1);
    expect(accumulator.points[0]).toBe(first);
    expect(accumulator.points[1].value).toBe(40 / 3);
    expect(accumulator.points[2].value).toBe(85 / 6);
  });

  it("is unavailable when any contributing bar lacks volume", () => {
    const accumulator = createAnchoredVwapAccumulator(
      [{ timestamp: 1, value: 0 }],
      [{ ...bars[0], volume: null }, bars[1]],
    );
    expect(anchoredVwapAvailable(accumulator)).toBe(false);
    expect(accumulator.points.map((point) => point.value)).toEqual([null, null]);
  });

  it("projects only visible first/min/max/last pixel buckets", () => {
    const dense = Array.from({ length: 50_000 }, (_, index): Bar => ({
      time: index,
      open: 100,
      high: 101 + Math.sin(index / 9),
      low: 99,
      close: 100 + Math.sin(index / 9),
      volume: 1,
    }));
    const accumulator = createAnchoredVwapAccumulator([{ timestamp: 0, value: 0 }], dense);
    const projected = bucketAnchoredVwapForViewport(accumulator.points, 10_000, 20_000, 200);
    expect(projected.length).toBeLessThanOrEqual(800);
    expect(projected.every((point) => point.dataIndex >= 10_000 && point.dataIndex <= 20_000))
      .toBe(true);
    expect(projected[0].dataIndex).toBe(10_000);
    expect(projected[projected.length - 1].dataIndex).toBe(20_000);
  });
});

describe("Regression Trend", () => {
  it("uses bar index and close by default with population sigma and Pearson R", () => {
    const selected = [
      { ...bars[0], close: 1 },
      { ...bars[1], close: 2 },
      { ...bars[2], close: 4 },
    ];
    const result = buildRegressionResult(
      [{ timestamp: 1, value: 999 }, { timestamp: 3, value: -999 }],
      selected,
    )!;
    expect(result.slope).toBeCloseTo(1.5);
    expect(result.intercept).toBeCloseTo(5 / 6);
    expect(result.sigma).toBeCloseTo(Math.sqrt(1 / 18));
    expect(result.pearson).toBeCloseTo(0.9819805);
    expect(regressionValueAt(result, 2)).toBeCloseTo(23 / 6);
  });

  it("supports every declared source and bounded deviation/toggle settings", () => {
    expect(regressionSourceValue(bars[0], "open")).toBe(9);
    expect(regressionSourceValue(bars[0], "high")).toBe(12);
    expect(regressionSourceValue(bars[0], "low")).toBe(8);
    expect(regressionSourceValue(bars[0], "close")).toBe(10);
    expect(regressionSourceValue(bars[0], "hl2")).toBe(10);
    expect(regressionSourceValue(bars[0], "hlc3")).toBe(10);
    expect(regressionSourceValue(bars[0], "ohlc4")).toBe(9.75);
    expect(buildRegressionResult(
      [{ timestamp: 1, value: 0 }, { timestamp: 3, value: 0 }],
      bars,
      {
        source: "high",
        upperDeviation: 1.5,
        lowerDeviation: 3,
        upperEnabled: false,
        lowerEnabled: true,
        extendRight: true,
        showPearson: true,
      },
    )?.settings).toMatchObject({
      source: "high",
      upperDeviation: 1.5,
      lowerDeviation: 3,
      upperEnabled: false,
      lowerEnabled: true,
      extendRight: true,
      showPearson: true,
    });
  });
});
