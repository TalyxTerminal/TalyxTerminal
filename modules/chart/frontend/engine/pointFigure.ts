/* Point & figure as TradingView draws it: a rising column is a stack of X, a falling one a stack of O, one mark a box. The
   columns come projected by the Chart backend: a mark on every box from the column's open to its close, each centred on its
   price. */
import {
  customSeriesDefaultOptions,
  type CustomData, type CustomSeriesOptions, type CustomSeriesWhitespaceData, type ICustomSeriesPaneRenderer, type ICustomSeriesPaneView,
  type PaneRendererCustomData, type PriceToCoordinateConverter,
} from "lightweight-charts";
import type { CanvasRenderingTarget2D } from "fancy-canvas";

export interface PointFigureColumn extends CustomData<number> { open: number; close: number; box: number }
export interface PointFigureOptions extends CustomSeriesOptions { upColor: string; downColor: string }

export class PointFigureColumns implements ICustomSeriesPaneView<number, PointFigureColumn, PointFigureOptions> {
  private data: PaneRendererCustomData<number, PointFigureColumn> | null = null;
  private options: PointFigureOptions | null = null;
  renderer(): ICustomSeriesPaneRenderer { return { draw: (target, toY) => this.draw(target, toY) }; }
  update(data: PaneRendererCustomData<number, PointFigureColumn>, options: PointFigureOptions) { this.data = data; this.options = options; }
  // The outer marks reach half a box past the column's ends.
  priceValueBuilder({ open, close, box }: PointFigureColumn) {
    return close > open ? [open - box / 2, close + box / 2, close] : [open + box / 2, close - box / 2, close];
  }
  isWhitespace(point: PointFigureColumn | CustomSeriesWhitespaceData<number>): point is CustomSeriesWhitespaceData<number> { return (point as PointFigureColumn).close === undefined; }
  defaultOptions(): PointFigureOptions { return { ...customSeriesDefaultOptions, upColor: "", downColor: "" }; }

  private draw(target: CanvasRenderingTarget2D, toY: PriceToCoordinateConverter) {
    const data = this.data, options = this.options;
    if (!data?.visibleRange || !options) return;
    const { from, to } = data.visibleRange;
    target.useBitmapCoordinateSpace(({ context, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
      const half = Math.max(1, data.barSpacing * 0.4) * hr;
      context.lineWidth = Math.max(1, Math.round(hr * (data.barSpacing > 12 ? 2 : 1)));
      for (const up of [true, false]) {
        context.strokeStyle = up ? options.upColor : options.downColor;
        context.beginPath();
        for (let index = from; index < to; index++) {
          const { x, originalData: { open, close, box } } = data.bars[index];
          if ((close > open) !== up) continue;
          const step = up ? box : -box, count = Math.round(Math.abs(close - open) / box) + 1, middle = x * hr;
          for (let mark = 0; mark < count; mark++) {
            const level = open + mark * step, top = toY(level + box / 2), bottom = toY(level - box / 2);
            if (top === null || bottom === null) continue;
            // A small gap between neighbouring marks.
            const y1 = top * vr, y2 = bottom * vr, gap = (y2 - y1) * 0.1;
            if (up) {
              context.moveTo(middle - half, y1 + gap); context.lineTo(middle + half, y2 - gap);
              context.moveTo(middle + half, y1 + gap); context.lineTo(middle - half, y2 - gap);
            } else {
              context.moveTo(middle + half, (y1 + y2) / 2);
              context.ellipse(middle, (y1 + y2) / 2, half, Math.max(0.5, Math.abs(y2 - y1) / 2 * 0.8), 0, 0, 2 * Math.PI);
            }
          }
        }
        context.stroke();
      }
    });
  }
}
