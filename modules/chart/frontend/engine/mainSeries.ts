/* The main series for each chart type. Non-time types (Heikin Ashi, Renko, Line break, Kagi, Point & figure, Range) arrive
   already projected by the Chart backend and are drawn with the plain series that matches their look; Kagi and Point & figure
   have their own (kagi.ts, pointFigure.ts). */
import {
  AreaSeries, BarSeries, BaselineSeries, CandlestickSeries, HistogramSeries, LineSeries, LineType,
  type IChartApiBase, type ISeriesApi, type SeriesType,
} from "lightweight-charts";
import { tokenValue } from "@talyx/sdk";
import type { Bars } from "./bars";
import { KagiLine } from "./kagi";
import { PointFigureColumns } from "./pointFigure";
import { withAlpha, type ChartColors } from "./theme";
import { seriesFormat, type PriceFormat } from "./precision";

export const CHART_TYPES = [
  "bars", "candles", "hollow_candles", "columns", "line", "line_markers", "step_line", "area", "baseline", "high_low",
  "heikin_ashi", "renko", "line_break", "kagi", "point_figure", "range",
] as const;
export type ChartType = (typeof CHART_TYPES)[number];

type Shape = "candle" | "bar" | "column" | "line" | "area" | "baseline" | "kagi" | "point_figure";
const SHAPES: Record<ChartType, Shape> = {
  bars: "bar", candles: "candle", hollow_candles: "candle", columns: "column", line: "line", line_markers: "line", step_line: "line",
  area: "area", baseline: "baseline", high_low: "candle", heikin_ashi: "candle", renko: "candle", line_break: "candle", kagi: "kagi",
  point_figure: "point_figure", range: "candle",
};

export type MainSeries = ISeriesApi<SeriesType, number>;

export function createMainSeries(chart: IChartApiBase<number>, type: ChartType, colors: ChartColors, format: PriceFormat, side: "left" | "right"): MainSeries {
  const priceFormat = seriesFormat(format);
  // Its labels are laid out with the others (priceLabels).
  const common = { priceFormat, priceScaleId: side, priceLineVisible: true, lastValueVisible: false };
  const { up, down } = colors;
  switch (SHAPES[type]) {
    case "candle": return chart.addSeries(CandlestickSeries, {
      ...common, upColor: type === "hollow_candles" ? tokenValue("--tx-clear") : up, downColor: down,
      borderUpColor: type === "hollow_candles" ? up : colors.borderUp, borderDownColor: type === "hollow_candles" ? down : colors.borderDown,
      wickUpColor: colors.wickUp, wickDownColor: colors.wickDown, borderVisible: true,
    });
    case "bar": return chart.addSeries(BarSeries, { ...common, upColor: up, downColor: down, thinBars: false });
    case "column": return chart.addSeries(HistogramSeries, { ...common, color: up });
    case "line": return chart.addSeries(LineSeries, {
      ...common, color: colors.series[0], lineWidth: 2, pointMarkersVisible: type === "line_markers",
      lineType: type === "step_line" ? LineType.WithSteps : LineType.Simple,
    });
    case "area": return chart.addSeries(AreaSeries, { ...common, lineColor: colors.series[0], topColor: withAlpha(colors.series[0], 0.28), bottomColor: withAlpha(colors.series[0], 0.02), lineWidth: 2 });
    case "baseline": return chart.addSeries(BaselineSeries, {
      ...common, topLineColor: up, bottomLineColor: down, topFillColor1: withAlpha(up, 0.28), topFillColor2: withAlpha(up, 0.05),
      bottomFillColor1: withAlpha(down, 0.05), bottomFillColor2: withAlpha(down, 0.28),
    });
    case "kagi": return chart.addCustomSeries(new KagiLine(), { ...common, yangColor: up, yinColor: down });
    case "point_figure": return chart.addCustomSeries(new PointFigureColumns(), { ...common, upColor: up, downColor: down });
  }
}

/** One bar as the main series of a type takes it; `box` is the bars' box (Point & figure marks its columns in boxes), null on
    types without one. */
export function mainPoint(type: ChartType, bars: Bars, index: number, colors: ChartColors, box: number | null) {
  const time = bars.time[index], open = bars.open[index], high = bars.high[index], low = bars.low[index], close = bars.close[index];
  switch (SHAPES[type]) {
    case "candle": return type === "high_low" ? { time, open: low, high, low, close: high } : { time, open, high, low, close };
    case "bar": return { time, open, high, low, close };
    case "kagi": return { time, open, close, color: close >= open ? colors.up : colors.down };
    // No box: another type's bars, until its own come.
    case "point_figure": return box === null ? { time } : { time, open, close, box, color: close > open ? colors.up : colors.down };
    case "column": return { time, value: close, color: close >= open ? colors.up : colors.down };
    default: return { time, value: close };
  }
}

/** Baseline charts split at the middle of the loaded prices, as TradingView starts them. */
export function baselineValue(bars: Bars): number {
  let low = Infinity, high = -Infinity;
  for (let index = 0; index < bars.close.length; index++) { if (bars.low[index] < low) low = bars.low[index]; if (bars.high[index] > high) high = bars.high[index]; }
  return (low + high) / 2;
}

export function isBaseline(type: ChartType): boolean { return SHAPES[type] === "baseline"; }
