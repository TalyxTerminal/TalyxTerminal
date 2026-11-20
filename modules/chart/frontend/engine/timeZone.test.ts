import { describe, expect, it } from "vitest";
import { barTimeText, clockText, dateText, Weight } from "./timeZone";
import { ZonedTimeScale, fillWeights } from "./timeScale";

const iso = (value: string) => Date.parse(value);

describe("display time zone", () => {
  it("labels both bars of the repeated fall-back hour 01:00 and keeps every bar distinct", () => {
    // BTC 1h shown in New York across 2026-11-01: 05:00Z and 06:00Z are both 01:00 on the wall clock.
    const times = Array.from({ length: 24 }, (_, index) => iso("2026-11-01T00:00:00Z") + index * 3_600_000);
    expect(clockText("America/New_York", iso("2026-11-01T05:00:00Z"))).toBe("01:00");
    expect(clockText("America/New_York", iso("2026-11-01T06:00:00Z"))).toBe("01:00");
    const scale = new ZonedTimeScale({ zone: "America/New_York", dateOnly: false });
    const keys = times.map(time => scale.key(scale.convertHorzItemToInternal(time)));
    expect(new Set(keys).size).toBe(24);
    expect(keys).toEqual([...keys].sort((a, b) => Number(a) - Number(b)));
    const points = times.map(time => ({ timeWeight: 0, time: { t: time } }));
    fillWeights(points, 0, "America/New_York");
    // One day boundary, at New York midnight (04:00Z, still daylight time), none at the repeated hour.
    expect(points.filter(point => point.timeWeight >= Weight.Day).map(point => point.time.t)).toEqual([iso("2026-11-01T04:00:00Z")]);
    expect(scale.formatHorzItem(scale.convertHorzItemToInternal(iso("2026-11-01T06:00:00Z")))).toBe("Sun 01 Nov '26  01:00");
  });

  it("labels daily bars by the exchange's trading date", () => {
    // 600519.SH 2026-11-13 opens at Shanghai midnight, 2026-11-12T16:00Z.
    expect(barTimeText("Asia/Shanghai", iso("2026-11-12T16:00:00Z"), true)).toBe("Fri 13 Nov '26");
    expect(dateText("America/New_York", iso("2026-11-11T22:00:00Z"))).toBe("Wed 11 Nov '26");
  });
});
