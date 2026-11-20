import { describe, expect, it } from "vitest";
import vectors from "../../../vocabulary/identity-vectors.json";
import { instrumentId, seriesId, validateSemantics } from "@talyx/vocab";
import type { Instrument, SeriesId } from "@talyx/vocab";

describe("shared vocabulary identity vectors", () => {
  it("keeps native monthly aliases distinct from minutes with Python parity", async () => {
    const record = vectors.series[0] as SeriesId;
    const expectedMonth = "vs1_20f54dc11e0ff90dbdd5a4e6a0d3b84fb83a31539f4315b7da79cd0a7bc6e6bf";
    expect(await seriesId({ ...record, timeframe: "1m" })).toBe(record.seriesId);
    expect(await seriesId({ ...record, timeframe: "1mo" })).toBe(expectedMonth);
    expect(await seriesId({ ...record, timeframe: "1M" })).toBe(expectedMonth);
    expect(expectedMonth).not.toBe(record.seriesId);
  });
  it("preserves every identity dimension and exact decimal in both languages", async () => {
    for (const value of vectors.instruments) {
      expect(await instrumentId(value as Instrument)).toBe(value.instrumentId);
      await validateSemantics("Instrument", value);
      await expect(validateSemantics("Instrument", { ...value, instrumentId: "incorrect" })).rejects.toThrow();
    }
    for (const value of vectors.series) {
      expect(await seriesId(value as SeriesId)).toBe(value.seriesId);
      await validateSemantics("SeriesId", value);
    }
    const first = vectors.instruments[0] as Instrument;
    expect(await instrumentId({ ...first, venueId: "STRASSE", contractSize: "100" })).toBe(first.instrumentId);
    expect(await instrumentId({ ...first, strike: "9007199254740993.123456790" })).not.toBe(first.instrumentId);
  });
  it("uses ns half-open ranges and does not round prices through Number", async () => {
    const range = { start: "1790892000000000000", end: "1790892000000000001", unit: "ns", bounds: "[)" };
    await validateSemantics("TimeRange", range);
    await expect(validateSemantics("TimeRange", { ...range, end: range.start })).rejects.toThrow();
    const bar = { open: "9007199254740993.123456789", high: "9007199254740993.123456790", low: "9007199254740993.123456788", close: "9007199254740993.123456789", volume: "0.000000001" };
    await validateSemantics("Bar", bar);
    await expect(validateSemantics("Bar", { ...bar, close: "9007199254740993.123456791" })).rejects.toThrow();
  });
});
