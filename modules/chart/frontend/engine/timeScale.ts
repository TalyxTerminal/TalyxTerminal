/* The chart's horizontal scale (`createChartEx`): items are real UTC milliseconds; weights and labels come from the display zone.
   Daily and longer bars are labelled by the exchange's trading date, so a Shanghai daily bar (local midnight, the day before
   in UTC) reads as its own day whatever the display zone. */
import type {
  ChartOptionsImpl, DataItem, HorzScaleItemConverterToInternalObj, IHorzScaleBehavior, InternalHorzScaleItem,
  InternalHorzScaleItemKey, LocalizationOptions, SeriesDataItemTypeMap, SeriesType, TickMark, TickMarkWeightValue, TimeMark,
} from "lightweight-charts";
import { Weight, barTimeText, tickText, tickWeight, wallClock, type UtcMs } from "./timeZone";

export interface TimeScaleZone {
  /** The zone labels and day boundaries are read in: the display zone, or the exchange's for daily and longer bars. */
  zone: string;
  dateOnly: boolean;
}

type Item = { t: UtcMs };
const item = (value: InternalHorzScaleItem) => (value as unknown as Item).t;

export class ZonedTimeScale implements IHorzScaleBehavior<UtcMs> {
  private chartOptions!: ChartOptionsImpl<UtcMs>;
  /** Whether a label at this time fits on the axis whole (set by the view). Lightweight Charts draws a label at the
      edge cut in half; TradingView leaves it out. The label cache is keyed by fit, so a label scrolled back in shows. */
  fits: (time: UtcMs) => boolean = () => true;
  constructor(readonly config: TimeScaleZone) {}
  options() { return this.chartOptions; }
  setOptions(options: ChartOptionsImpl<UtcMs>) { this.chartOptions = options; }
  preprocessData(_data: DataItem<UtcMs> | DataItem<UtcMs>[]) {}
  convertHorzItemToInternal(time: UtcMs) { return { t: time } as unknown as InternalHorzScaleItem; }
  createConverterToInternalObj(_data: SeriesDataItemTypeMap<UtcMs>[SeriesType][]): HorzScaleItemConverterToInternalObj<UtcMs> {
    return time => ({ t: time }) as unknown as InternalHorzScaleItem;
  }
  key(value: InternalHorzScaleItem | UtcMs) { return (typeof value === "number" ? value : item(value)) as InternalHorzScaleItemKey; }
  cacheKey(value: InternalHorzScaleItem) { const time = item(value); return this.fits(time) ? time : -time - 1; }
  updateFormatter(_options: LocalizationOptions<UtcMs>) {}
  formatHorzItem(value: InternalHorzScaleItem) { return barTimeText(this.config.zone, item(value), this.config.dateOnly); }
  formatTickmark(mark: TickMark, _options: LocalizationOptions<UtcMs>) {
    if (!this.fits(item(mark.time))) return "";
    return tickText(this.config.zone, item(mark.time), mark.weight, this.config.dateOnly);
  }
  maxTickMarkWeight(marks: TimeMark[]) {
    let weight = marks.reduce((top, mark) => (mark.weight > top ? mark.weight : top), marks[0].weight) as number;
    if (weight > Weight.Hour1 && weight < Weight.Day) weight = Weight.Hour1;
    return weight as TickMarkWeightValue;
  }
  fillWeightsForPoints(points: readonly { timeWeight: TickMarkWeightValue; time: InternalHorzScaleItem }[], start: number) {
    fillWeights(points as unknown as { timeWeight: number; time: Item }[], start, this.config.zone);
  }
}

/** Weights from the wall clock in `zone`; the first point is weighed against an average step before it. */
export function fillWeights(points: { timeWeight: number; time: Item }[], start: number, zone: string): void {
  if (!points.length) return;
  let previous = start > 0 ? wallClock(zone, points[start - 1].time.t) : null;
  for (let index = start; index < points.length; index++) {
    const current = wallClock(zone, points[index].time.t);
    if (previous) points[index].timeWeight = tickWeight(current, previous);
    previous = current;
  }
  if (start === 0 && points.length > 1) {
    const step = (points[points.length - 1].time.t - points[0].time.t) / (points.length - 1);
    points[0].timeWeight = tickWeight(wallClock(zone, points[0].time.t), wallClock(zone, points[0].time.t - step));
  }
}
