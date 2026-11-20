import { describe, expect, it } from "vitest";
import { ENGINE_LIMIT, ScaleUnit, compactZeros, formatCompact, formatValue, seriesFormat, unitFormat, valueUnit } from "./precision";

describe("compactZeros", () => {
  it("counts four or more zeros after the point in a subscript, keeping the digits and the sign", () => {
    expect(compactZeros("0.000003864")).toBe("0.0₅3864");
    expect(compactZeros("-0.0000005483")).toBe("-0.0₆5483");
    expect(compactZeros("+0.00000043")).toBe("+0.0₆43");
    expect(compactZeros("0.00001234")).toBe("0.0₄1234");
  });

  it("leaves three zeros or fewer, zero and normal prices as they are", () => {
    expect(compactZeros("0.0001234")).toBe("0.0001234");
    expect(compactZeros("0")).toBe("0");
    expect(compactZeros("65519.5")).toBe("65519.5");
    expect(compactZeros("-12.21")).toBe("-12.21");
    expect(compactZeros("—")).toBe("—");
  });
});

describe("seriesFormat", () => {
  it("gives the chart a whole-number base for every tick, however small", () => {
    expect(seriesFormat({ precision: 9, minMove: 1e-9 }).base).toBe(1e9);
    expect(seriesFormat({ precision: 12, minMove: 1e-12 }).base).toBe(1e12);
    expect(seriesFormat({ precision: 8, minMove: 1e-8 }).base).toBe(1e8);
    expect(seriesFormat({ precision: 2, minMove: 0.25 }).base).toBe(4);
    expect(seriesFormat({ precision: 1, minMove: 0.1 }).base).toBe(10);
  });
});

describe("formatCompact", () => {
  it("names thousands up to quadrillions", () => {
    expect(formatCompact(8370)).toBe("8.37K");
    expect(formatCompact(45_600_000)).toBe("45.60M");
    expect(formatCompact(221_351_160_000_000)).toBe("221.35T");
    expect(formatCompact(19_074_735_181_981_530)).toBe("19.07Q");
    expect(formatCompact(-2.5e12)).toBe("-2.50T");
  });
});

describe("value units", () => {
  it("keeps values that fit the engine and divides larger ones by a power of ten with room to grow", () => {
    expect(valueUnit(0)).toBe(1);
    expect(valueUnit(ENGINE_LIMIT)).toBe(1);
    expect(valueUnit(1.9e16)).toBe(1e6);
    expect(1.9e16 / valueUnit(1.9e16) * 1000).toBeLessThanOrEqual(ENGINE_LIMIT);
  });

  it("is shared by a scale's series: grows when one no longer fits, stays while all fit, goes back to 1 when all fit in it", () => {
    const scale = new ScaleUnit(), volume = {}, average = {};
    const resets: string[] = [];
    scale.join(volume, () => resets.push("volume"));
    const leaveAverage = scale.join(average, () => resets.push("average"));
    expect(scale.unitFor(volume, 1e10, true)).toBe(1);
    expect(scale.unitFor(average, 1.9e16, true)).toBe(1e6);
    expect(resets).toEqual(["volume"]);
    expect(scale.unitFor(volume, 2e16, false)).toBe(1e6);
    expect(scale.unitFor(average, 5e15, true)).toBe(1e6);
    expect(scale.unitFor(volume, 1e17, false)).toBe(1e7);
    expect(resets).toEqual(["volume", "average"]);
    scale.unitFor(volume, 1e10, true);
    expect(scale.value).toBe(1e7);
    leaveAverage();
    expect(scale.value).toBe(1);
    expect(resets).toEqual(["volume", "average", "volume"]);
  });

  it("formats labels of a scaled series multiplied back", () => {
    const base = seriesFormat({ precision: 2, minMove: 0.01 });
    expect(unitFormat(base, 1)).toBe(base);
    const scaled = unitFormat(base, 1e6) as { formatter(price: number): string };
    expect(scaled.formatter(19_074.73518198153)).toBe("19.07B");
    expect(scaled.formatter(19_074_735_181.98153)).toBe("19.07Q");
  });
});

describe("formatValue", () => {
  it("shows a value declared format.volume compact and others at their precision", () => {
    expect(formatValue(90_039_693_702_297_550, "volume", 2)).toBe("90.04Q");
    expect(formatValue(1234.5, "price", 2)).toBe("1,234.50");
    expect(formatValue(1234.5, "inherit", 0)).toBe("1,235");
  });
});

