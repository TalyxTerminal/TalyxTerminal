/* Kagi as TradingView draws it: one line, verticals joined by the horizontal shoulders and waists. It turns thick (yang,
   the up colour) where it rises above the previous shoulder and thin (yin, the down colour) where it falls below the
   previous waist. The bars come projected by the Chart backend: each is one vertical from its open (the previous
   line's end) to its close. */
import {
  customSeriesDefaultOptions,
  type CustomData, type CustomSeriesOptions, type CustomSeriesWhitespaceData, type ICustomSeriesPaneRenderer, type ICustomSeriesPaneView,
  type PaneRendererCustomData, type PriceToCoordinateConverter,
} from "lightweight-charts";
import type { CanvasRenderingTarget2D } from "fancy-canvas";

export interface KagiPoint extends CustomData<number> { open: number; close: number }
export interface KagiOptions extends CustomSeriesOptions { yangColor: string; yinColor: string }

const YANG_WIDTH = 3, YIN_WIDTH = 1;

export class KagiLine implements ICustomSeriesPaneView<number, KagiPoint, KagiOptions> {
  private data: PaneRendererCustomData<number, KagiPoint> | null = null;
  private options: KagiOptions | null = null;
  renderer(): ICustomSeriesPaneRenderer { return { draw: (target, toY) => this.draw(target, toY) }; }
  update(data: PaneRendererCustomData<number, KagiPoint>, options: KagiOptions) { this.data = data; this.options = options; }
  priceValueBuilder(point: KagiPoint) { return [point.open, point.close, point.close]; }
  isWhitespace(point: KagiPoint | CustomSeriesWhitespaceData<number>): point is CustomSeriesWhitespaceData<number> { return (point as KagiPoint).close === undefined; }
  defaultOptions(): KagiOptions { return { ...customSeriesDefaultOptions, yangColor: "", yinColor: "" }; }

  private draw(target: CanvasRenderingTarget2D, toY: PriceToCoordinateConverter) {
    const data = this.data, options = this.options;
    if (!data?.visibleRange || !options || !data.bars.length) return;
    const { from, to } = data.visibleRange, bars = data.bars;
    target.useBitmapCoordinateSpace(({ context, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
      const segment = (yang: boolean, x1: number, p1: number, x2: number, p2: number) => {
        const y1 = toY(p1), y2 = toY(p2);
        if (y1 === null || y2 === null) return;
        context.strokeStyle = yang ? options.yangColor : options.yinColor;
        context.lineWidth = Math.max(1, Math.round((yang ? YANG_WIDTH : YIN_WIDTH) * hr));
        context.beginPath(); context.moveTo(Math.round(x1 * hr), Math.round(y1 * vr)); context.lineTo(Math.round(x2 * hr), Math.round(y2 * vr)); context.stroke();
      };
      context.lineCap = "square";
      // The thickness at a line depends on every line before it: walked from the series' first bar.
      const first = bars[0].originalData;
      let yang = first.close >= first.open;
      for (let index = 0; index < to; index++) {
        const { open, close } = bars[index].originalData;
        const shown = index >= from - 1;
        // Only bars inside the visible range carry a current x; the one before it is placed a bar to the left.
        const x = index === from - 1 ? bars[from].x - data.barSpacing : bars[index].x;
        if (index >= from && index > 0) segment(yang, index === from ? x - data.barSpacing : bars[index - 1].x, open, x, open);
        const turn = index ? bars[index - 1].originalData.open : open;
        const turns = close > open ? !yang && close > turn : yang && close < turn;
        if (turns) {
          if (shown) { segment(yang, x, open, x, turn); segment(!yang, x, turn, x, close); }
          yang = !yang;
        } else if (shown) segment(yang, x, open, x, close);
      }
    });
  }
}
