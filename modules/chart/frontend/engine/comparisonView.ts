/* One comparison symbol on a chart (TradingView's Compare): a line on the price pane in its own colour. Its values are
   the backend's, one per main bar (that symbol's last close at or before the bar's time), so it uses the bars' times and
   another calendar never adds time to the axis. On the main price scale it reads in percent with the main series. */
import { LineSeries, type ISeriesApi } from "lightweight-charts";
import type { ChartView } from "./ChartView";
import type { ComparisonData } from "../feed";
import { seriesFormat } from "./precision";

export interface ComparisonPlacement { color: string; scaleId: string; visible: boolean }

export class ComparisonView {
  private readonly series: ISeriesApi<"Line", number>;
  private readonly stopWindow: () => void;

  constructor(private readonly view: ChartView, private readonly data: ComparisonData, placement: ComparisonPlacement) {
    this.series = view.chart.addSeries(LineSeries, {
      color: placement.color, lineWidth: 2, visible: placement.visible, priceScaleId: placement.scaleId, priceLineVisible: false,
      lastValueVisible: false, crosshairMarkerVisible: false, priceFormat: seriesFormat(data.priceFormat),
    }, view.pricePane);
    view.labels.attach(this.series);
    this.all();
    // The chart's series window moved: the same rows as the bars.
    this.stopWindow = view.onWindow(() => this.all());
  }

  private point(index: number) {
    const time = this.view.timeAtLogical(index)!, value = this.data.value[index];
    return value === null || value === undefined ? { time } : { time, value };
  }

  /** Every row again (a replace, a new window). */
  all(): void {
    const data = [];
    for (let index = this.view.firstRow; index < this.view.barCount(); index++) data.push(this.point(index));
    this.series.setData(data);
  }

  /** Rows from `from` on changed (the forming bar, bars that just closed); drawn on the next frame, after the bars. */
  changed(from: number): void { this.dirtyFrom = Math.min(this.dirtyFrom, from); this.view.schedule(this.flush); }
  private dirtyFrom = Infinity;

  private readonly flush = () => {
    const from = Math.max(this.view.firstRow, this.dirtyFrom), rows = this.view.barCount();
    this.dirtyFrom = Infinity;
    // Each update walks the whole series (Lightweight Charts 5.3): many rows at once are set together.
    if (rows - from > 8) { this.all(); return; }
    for (let index = from; index < rows; index++) this.series.update(this.point(index), true);
  };

  dispose(): void { this.stopWindow(); this.view.removeSeries(this.series); }
}
