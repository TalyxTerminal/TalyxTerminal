// Done criterion 3: the backend (tests/unit/chart/test_time_mapping.py) runs the same inputs and expected values.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { TimeMap, lineValueAt, logicalToX } from "./timeMap";

interface Case { bars: { time: number[]; closeTime: number[] }; future: { time: number[]; closeTime: number[] }; coveredUntil: number }
const fixture = JSON.parse(readFileSync(new URL("../../../../tests/fixtures/chart/time_mapping.json", import.meta.url), "utf8")) as {
  daily: Case & { trendline: { time: number; price: number }[]; values: { time: number; value: number | null }[] };
  hourly: Case & { anchors: { time: number; index: number | null; barTime: number | null }[] };
};
const mapOf = (item: Case) => new TimeMap(item.bars, { kind: "calendar", open: item.future.time, close: item.future.closeTime, coveredUntil: item.coveredUntil });

describe("time map (shared fixture)", () => {
  it("gives a trend line's value at each time, over trading days only, nothing past the calendar", () => {
    const { daily } = fixture, map = mapOf(daily);
    for (const { time, value } of daily.values) expect(lineValueAt(map, daily.trendline[0], daily.trendline[1], time)).toBe(value);
  });

  it("maps anchors across timeframes and into the future", () => {
    const { hourly } = fixture, map = mapOf(hourly);
    for (const { time, index, barTime } of hourly.anchors) {
      expect(map.toLogical(time)).toBe(index);
      if (barTime !== null) expect(map.toTime(index!)).toBe(barTime);
    }
  });
});

describe("logicalToX", () => {
  // Lightweight Charts gives 0 for a fractional position; whole bars come from it, the rest by bar spacing.
  const scale = { logicalToCoordinate: (logical: number) => Number.isInteger(logical) ? 100 + logical * 8 : 0, options: () => ({ barSpacing: 8 }) };
  it("places a time inside a bar between its bar and the next", () => {
    expect(logicalToX(scale as never, 3)).toBe(124);
    expect(logicalToX(scale as never, 3.25)).toBe(126);
    expect(logicalToX(scale as never, -0.5)).toBe(96);
  });
});
