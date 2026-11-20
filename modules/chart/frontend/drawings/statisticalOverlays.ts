import type { GeometryBar as Bar } from "./types";
import type { OverlayPoint, OverlaySettings } from "./types";

type RegressionSource = "open" | "high" | "low" | "close" | "hl2" | "hlc3" | "ohlc4";

export interface RegressionSettings {
  source: RegressionSource;
  upperDeviation: number;
  lowerDeviation: number;
  upperEnabled: boolean;
  lowerEnabled: boolean;
  extendRight: boolean;
  showPearson: boolean;
}

export const DEFAULT_REGRESSION_SETTINGS: RegressionSettings = {
  source: "close",
  upperDeviation: 2,
  lowerDeviation: 2,
  upperEnabled: true,
  lowerEnabled: true,
  extendRight: false,
  showPearson: false,
};

export function regressionSettings(settings?: OverlaySettings): RegressionSettings {
  return {
    source: settings?.source ?? DEFAULT_REGRESSION_SETTINGS.source,
    upperDeviation: settings?.upperDeviation ?? DEFAULT_REGRESSION_SETTINGS.upperDeviation,
    lowerDeviation: settings?.lowerDeviation ?? DEFAULT_REGRESSION_SETTINGS.lowerDeviation,
    upperEnabled: settings?.upperEnabled ?? DEFAULT_REGRESSION_SETTINGS.upperEnabled,
    lowerEnabled: settings?.lowerEnabled ?? DEFAULT_REGRESSION_SETTINGS.lowerEnabled,
    extendRight: settings?.extendRight ?? DEFAULT_REGRESSION_SETTINGS.extendRight,
    showPearson: settings?.showPearson ?? DEFAULT_REGRESSION_SETTINGS.showPearson,
  };
}

export function regressionSourceValue(bar: Bar, source: RegressionSource): number {
  switch (source) {
    case "open": return bar.open;
    case "high": return bar.high;
    case "low": return bar.low;
    case "hl2": return (bar.high + bar.low) / 2;
    case "hlc3": return (bar.high + bar.low + bar.close) / 3;
    case "ohlc4": return (bar.open + bar.high + bar.low + bar.close) / 4;
    default: return bar.close;
  }
}

export interface RegressionResult {
  fromIndex: number;
  toIndex: number;
  slope: number;
  intercept: number;
  sigma: number;
  pearson: number | null;
  settings: RegressionSettings;
}

export function regressionValueAt(result: RegressionResult, dataIndex: number): number {
  return result.intercept + result.slope * dataIndex;
}

export function buildRegressionResult(
  points: readonly OverlayPoint[],
  bars: readonly Bar[],
  configured?: OverlaySettings,
): RegressionResult | null {
  if (points.length !== 2 || bars.length < 2) return null;
  const from = Math.min(points[0].timestamp, points[1].timestamp);
  const to = Math.max(points[0].timestamp, points[1].timestamp);
  const fromIndex = bars.findIndex((bar) => bar.time >= from);
  if (fromIndex < 0) return null;
  let toIndex = fromIndex;
  while (toIndex + 1 < bars.length && bars[toIndex + 1].time <= to) toIndex += 1;
  if (toIndex <= fromIndex) return null;

  const settings = regressionSettings(configured);
  const count = toIndex - fromIndex + 1;
  const xMean = (fromIndex + toIndex) / 2;
  let ySum = 0;
  for (let index = fromIndex; index <= toIndex; index += 1) {
    ySum += regressionSourceValue(bars[index], settings.source);
  }
  const yMean = ySum / count;
  let covariance = 0;
  let xVariance = 0;
  let yVariance = 0;
  for (let index = fromIndex; index <= toIndex; index += 1) {
    const dx = index - xMean;
    const dy = regressionSourceValue(bars[index], settings.source) - yMean;
    covariance += dx * dy;
    xVariance += dx * dx;
    yVariance += dy * dy;
  }
  const slope = xVariance === 0 ? 0 : covariance / xVariance;
  const intercept = yMean - slope * xMean;
  let residualSquares = 0;
  for (let index = fromIndex; index <= toIndex; index += 1) {
    const residual = regressionSourceValue(bars[index], settings.source) -
      (intercept + slope * index);
    residualSquares += residual * residual;
  }
  return {
    fromIndex,
    toIndex,
    slope,
    intercept,
    sigma: Math.sqrt(residualSquares / count),
    pearson: xVariance === 0 || yVariance === 0
      ? null
      : covariance / Math.sqrt(xVariance * yVariance),
    settings,
  };
}

export interface AnchoredVwapPoint {
  dataIndex: number;
  timestamp: number;
  value: number | null;
  cumulativeVolume: number;
  cumulativeWeightedVolume: number;
  missingVolumeCount: number;
}

export interface AnchoredVwapAccumulator {
  anchor: number;
  anchorIndex: number;
  points: AnchoredVwapPoint[];
}

function firstBarAtOrAfter(bars: readonly Bar[], timestamp: number): number {
  let low = 0;
  let high = bars.length;
  while (low < high) {
    const middle = low + Math.floor((high - low) / 2);
    if (bars[middle].time < timestamp) low = middle + 1;
    else high = middle;
  }
  return low;
}

function appendVwapPoint(
  accumulator: AnchoredVwapAccumulator,
  bar: Bar,
  dataIndex: number,
): void {
  const previous = accumulator.points[accumulator.points.length - 1];
  const missing = bar.volume == null || !Number.isFinite(bar.volume) || bar.volume < 0;
  const volume = missing ? 0 : bar.volume ?? 0;
  const cumulativeVolume = (previous?.cumulativeVolume ?? 0) + volume;
  const cumulativeWeightedVolume = (previous?.cumulativeWeightedVolume ?? 0) +
    ((bar.high + bar.low + bar.close) / 3) * volume;
  const missingVolumeCount = (previous?.missingVolumeCount ?? 0) + (missing ? 1 : 0);
  accumulator.points.push({
    dataIndex,
    timestamp: bar.time,
    value: missingVolumeCount === 0 && cumulativeVolume > 0
      ? cumulativeWeightedVolume / cumulativeVolume
      : null,
    cumulativeVolume,
    cumulativeWeightedVolume,
    missingVolumeCount,
  });
}

export function createAnchoredVwapAccumulator(
  points: readonly OverlayPoint[],
  bars: readonly Bar[],
): AnchoredVwapAccumulator {
  const anchor = points[0]?.timestamp ?? Number.POSITIVE_INFINITY;
  const anchorIndex = firstBarAtOrAfter(bars, anchor);
  const accumulator: AnchoredVwapAccumulator = { anchor, anchorIndex, points: [] };
  for (let index = anchorIndex; index < bars.length; index += 1) {
    appendVwapPoint(accumulator, bars[index], index);
  }
  return accumulator;
}

/**
 * Mutates the engine-owned accumulator. Forming-bar replacement and append touch one
 * point; an older mutation discards and recalculates only the affected suffix.
 */
export function updateAnchoredVwapAccumulator(
  accumulator: AnchoredVwapAccumulator,
  bars: readonly Bar[],
  mutationIndex: number,
): AnchoredVwapAccumulator {
  if (mutationIndex < accumulator.anchorIndex || mutationIndex >= bars.length) return accumulator;
  const pointOffset = mutationIndex - accumulator.anchorIndex;
  accumulator.points.length = Math.max(0, Math.min(pointOffset, accumulator.points.length));
  for (let index = mutationIndex; index < bars.length; index += 1) {
    appendVwapPoint(accumulator, bars[index], index);
  }
  return accumulator;
}

export function anchoredVwapAvailable(accumulator: AnchoredVwapAccumulator): boolean {
  const latest = accumulator.points[accumulator.points.length - 1];
  return latest?.value != null && latest.missingVolumeCount === 0;
}

export function bucketAnchoredVwapForViewport(
  points: readonly AnchoredVwapPoint[],
  fromIndex: number,
  toIndex: number,
  pixelWidth: number,
): AnchoredVwapPoint[] {
  if (pixelWidth <= 0 || toIndex < fromIndex) return [];
  const visible = points.filter((point) =>
    point.value !== null && point.dataIndex >= fromIndex && point.dataIndex <= toIndex
  );
  if (visible.length <= 2) return [...visible];
  const bucketCount = Math.max(1, Math.floor(pixelWidth));
  const span = Math.max(1, toIndex - fromIndex + 1);
  const result: AnchoredVwapPoint[] = [];
  let offset = 0;
  while (offset < visible.length) {
    const bucket = Math.min(
      bucketCount - 1,
      Math.floor((visible[offset].dataIndex - fromIndex) / span * bucketCount),
    );
    let end = offset + 1;
    while (end < visible.length && Math.min(
      bucketCount - 1,
      Math.floor((visible[end].dataIndex - fromIndex) / span * bucketCount),
    ) === bucket) end += 1;
    const group = visible.slice(offset, end);
    const first = group[0];
    const last = group[group.length - 1];
    const minimum = group.reduce((best, point) => point.value! < best.value! ? point : best);
    const maximum = group.reduce((best, point) => point.value! > best.value! ? point : best);
    for (const point of [first, minimum, maximum, last].sort((a, b) => a.dataIndex - b.dataIndex)) {
      if (result[result.length - 1] !== point) result.push(point);
    }
    offset = end;
  }
  return result;
}
