import { expect, it, vi } from "vitest";
import { MEASURED_SCALE, type ChartView } from "./ChartView";
import { IndicatorView } from "./indicatorView";

it.each([true, false])("uses the series' assigned price scale in comparison mode (noScale=%s)", noScale => {
  const addSeries = vi.fn((_kind, options) => ({ options: () => options, setData: vi.fn() }));
  const measure = vi.fn();
  const view = {
    chart: { addSeries, priceScale: () => ({ applyOptions: vi.fn() }) },
    measure, pricePane: 0, firstRow: 0, barCount: () => 0, onWindow: vi.fn(),
    scaleUnit: () => ({ unitFor: () => 1, join: vi.fn() }),
  } as unknown as ChartView;
  new IndicatorView(view, {
    meta: [{ kind: "plot", id: "vol", style: "columns", color: "#777777" }],
    noScale, behindChart: false, values: { vol: [] }, colors: {}, drawings: null,
  }, {
    pane: 0, scale: MEASURED_SCALE, side: "right", visible: true, labels: false, styles: {}, palette: [],
    format: { precision: 2, minMove: 0.01 },
  });
  const scaleId = addSeries.mock.calls[0][1].priceScaleId;
  if (noScale) {
    expect(scaleId).toMatch(/^own-/);
    expect(measure).not.toHaveBeenCalled();
  } else {
    expect(scaleId).toBe(MEASURED_SCALE);
    expect(measure).toHaveBeenCalledOnce();
  }
});
