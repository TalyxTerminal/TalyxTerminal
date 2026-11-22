import { describe, expect, it } from "vitest";

import {
  buildOverlayGeometry,
  buildOverlayRuntimeData,
} from "./geometry";
import { hitTestOverlayGeometry } from "./interactionGeometry";
import { OVERLAY_TOOL_REGISTRY } from "./registry";
import type { OverlayPoint } from "./types";

const points: OverlayPoint[] = [
  { timestamp: 1_000, value: 100 },
  { timestamp: 86_401_000, value: 115 },
  { timestamp: 172_801_000, value: 92 },
  { timestamp: 259_201_000, value: 121 },
  { timestamp: 345_601_000, value: 98 },
  { timestamp: 432_001_000, value: 126 },
  { timestamp: 518_401_000, value: 104 },
];
const pixels = [
  { x: 10, y: 80 },
  { x: 90, y: 30 },
  { x: 140, y: 110 },
  { x: 170, y: 40 },
  { x: 190, y: 100 },
  { x: 205, y: 35 },
  { x: 215, y: 90 },
];

describe("TALYX professional overlay geometry", () => {
  it.each(OVERLAY_TOOL_REGISTRY)(
    "$label has selectable non-placeholder geometry in the shared executable contract",
    (tool) => {
      const pointCount = tool.points.kind === "minimum" ? 3 : tool.points.count;
      const runtime = buildOverlayRuntimeData(
        tool.id,
        points.slice(0, pointCount),
        [
          { time: 1_000, open: 99, high: 102, low: 98, close: 101, volume: 10 },
          { time: 86_401_000, open: 110, high: 117, low: 108, close: 115, volume: 20 },
        ],
        tool.textMode === "required" || tool.textMode === "optional" ? "Note" : undefined,
      );
      const projected = tool.id === "anchored_vwap"
        ? { ...runtime, projectedCurve: pixels.slice(0, 2) }
        : tool.id === "regression_trend"
          ? {
              ...runtime,
              projectedRegression: {
                base: [pixels[0], pixels[1]] as const,
                upper: [pixels[0], pixels[2]] as const,
                lower: [pixels[2], pixels[1]] as const,
              },
            }
          : runtime;
      const figures = buildOverlayGeometry(
        tool.id,
        pixels.slice(0, pointCount),
        { width: 200, height: 150 },
        projected,
      );
      expect(figures.length).toBeGreaterThan(0);
      expect(figures.every((figure) => figure.type.length > 0 && figure.attrs)).toBe(true);
      expect(figures.every((figure) => !(figure as { ignoreEvent?: boolean }).ignoreEvent)).toBe(true);
      const first = figures[0];
      const coordinates = Array.isArray(first.attrs.coordinates)
        ? first.attrs.coordinates as Array<{ x: number; y: number }>
        : [];
      const hitPoint = coordinates[0] ?? {
        x: Number(first.attrs.x),
        y: Number(first.attrs.y),
      };
      expect(hitTestOverlayGeometry(figures, hitPoint, 6)).not.toBeNull();
    },
  );

  it("computes truthful regression, VWAP, range, and position readouts", () => {
    const data = [
      { time: 1_000, open: 99, high: 102, low: 98, close: 100, volume: 10 },
      { time: 86_401_000, open: 108, high: 112, low: 106, close: 110, volume: 30 },
      { time: 172_801_000, open: 116, high: 120, low: 114, close: 118, volume: 20 },
    ];
    const regression = buildOverlayRuntimeData(
      "regression_trend",
      points.slice(0, 2),
      data,
      undefined,
    ).readout;
    expect(regression).toMatchObject({ kind: "regression" });
    expect(regression?.values.pearson).toEqual(expect.any(Number));

    const vwap = buildOverlayRuntimeData("anchored_vwap", points.slice(0, 1), [
      { time: 1_000, open: 99, high: 102, low: 98, close: 100, volume: 10 },
      { time: 86_401_000, open: 108, high: 112, low: 106, close: 110, volume: 30 },
    ], undefined).readout;
    expect(vwap).toMatchObject({ kind: "anchored_vwap", values: { observations: 2 } });
    expect(vwap?.values.vwap).toBeCloseTo(107, 5);

    expect(buildOverlayRuntimeData("date_price_range", points.slice(0, 2), [], undefined).readout)
      .toMatchObject({ kind: "range", values: { delta: 15, durationMs: 86_400_000 } });
    expect(buildOverlayRuntimeData("long_position", points, [], undefined).readout)
      .toMatchObject({ kind: "position", values: { entry: 100, target: 115, stop: 92, rewardRisk: 1.875 } });
  });

  it("does not collapse calculated geometries into one line alias", () => {
    const types = ["regression_trend", "fib_extension", "fib_channel", "fib_time", "gann_box", "gann_fan", "date_price_range", "anchored_vwap", "long_position"] as const;
    const signatures = types.map((type) => {
      const runtime = buildOverlayRuntimeData(type, points, [], undefined);
      const projected = type === "anchored_vwap"
        ? { ...runtime, projectedCurve: pixels }
        : type === "regression_trend"
          ? { ...runtime, projectedRegression: { base: [pixels[0], pixels[1]] as const } }
          : runtime;
      const figures = buildOverlayGeometry(type, pixels, { width: 200, height: 150 }, projected);
      return JSON.stringify(figures.map((figure) => [figure.type, figure.attrs]));
    });
    expect(new Set(signatures).size).toBe(types.length);
  });

  it("renders and explains configured Fib/Gann levels instead of fixed aliases", () => {
    const fib = buildOverlayRuntimeData(
      "fib",
      points.slice(0, 2),
      [],
      undefined,
      { levels: [0.25, 0.75] },
    );
    const figures = buildOverlayGeometry(
      "fib",
      pixels.slice(0, 2),
      { width: 200, height: 150 },
      fib,
    );
    expect(fib.readout?.values.ratios).toBe("0.25,0.75");
    expect(figures.filter((figure) => figure.key?.startsWith("level:"))).toHaveLength(2);

    const gann = buildOverlayRuntimeData(
      "gann_fan",
      points.slice(0, 2),
      [],
      undefined,
      { levels: [0.5, 1, 2] },
    );
    expect(gann.readout?.values.ratios).toBe("0.5,1,2");
    expect(buildOverlayGeometry(
      "gann_fan",
      pixels.slice(0, 2),
      { width: 200, height: 150 },
      gann,
    ).filter((figure) => figure.type === "line")).toHaveLength(3);
  });

  it("puts a two-point line's text at its middle, along it, above it and upright whichever way it was drawn", () => {
    for (const type of ["trendline", "ray", "extended_line", "info_line", "trend_angle", "arrow"] as const) {
      for (const [a, b] of [[{ x: 10, y: 80 }, { x: 90, y: 30 }], [{ x: 90, y: 30 }, { x: 10, y: 80 }]]) {
        const runtime = buildOverlayRuntimeData(type, points.slice(0, 2), [], "Note");
        const text = buildOverlayGeometry(type, [a, b], { width: 200, height: 150 }, runtime).find((figure) => figure.attrs.text === "Note")!;
        const { x, y, angle, align } = text.attrs as { x: number; y: number; angle: number; align: string };
        expect(angle).toBeCloseTo(Math.atan2(-50, 80));
        expect(align).toBe("center");
        // 4px off the middle (50, 55), on the line's upper side.
        expect(Math.hypot(x - 50, y - 55)).toBeCloseTo(4);
        expect(y).toBeLessThan(55);
      }
    }
  });
});
