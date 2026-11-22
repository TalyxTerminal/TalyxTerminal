import { t } from "../i18n";
import { tokenValue } from "@talyx/sdk";
import { AreaSeries, LineSeries, LineStyle, LineType, createSeriesMarkers, createTextWatermark,
  type ISeriesApi, type ITextWatermarkPluginApi, type SeriesMarker, type SeriesType } from "lightweight-charts";
import type { ResearchLayer, ResearchMarker, ResearchPoint } from "../../public/ts/types";
import { failure } from "@talyx/sdk";
import type { Pane, SavedLayer } from "../wire";
import type { ChartView } from "./ChartView";
import { chartColors, withAlpha } from "./theme";
import { upperBound } from "./timeMap";

export function matchesLayer(layer: Pick<ResearchLayer, "instrumentId" | "adjustment">, instrument: string, adjustment: string) {
  return layer.instrumentId === instrument && layer.adjustment === adjustment;
}

const paneId = (layer: string, pane: string) => pane === "price" ? pane : JSON.stringify([layer, pane]);

export function checkLayerSize(layer: ResearchLayer): void {
  function limit(name: string, count: number, maximum: number) {
    if (count > maximum) throw failure("too_large", t("{name} contains {count} entries, {value} over the {maximum} limit", { name, count, value: count - maximum, maximum }));
  }
  limit(t("Trade markers"), layer.markers.length, 20_000);
  for (const series of layer.series) limit(series.name, series.points.length, 200_000);
}

export function layerPanes(panes: Pane[], layers: readonly Pick<SavedLayer, "id" | "panes">[]): Pane[] {
  const extra = (position: "above" | "below") => layers.flatMap(layer => layer.panes.filter(pane => pane.position === position)
    .map(pane => ({ id: paneId(layer.id, pane.key), height: position === "above" ? 2 : 1, collapsed: false })));
  return [...extra("above"), ...panes, ...extra("below")];
}

/** Dates are aligned against the actual Market opens, including exchange holidays and changes in UTC offset. */
export function alignedPoints(times: readonly number[], points: ResearchPoint[], start: number, end: number) {
  const values = new Map<number, { instant: number; value: number | null }>();
  for (const point of points) {
    const instant = Date.parse(point.time);
    if (instant < start || instant > end) continue;
    const index = upperBound(times, instant) - 1;
    if (index < 0) continue;
    const open = times[index], previous = values.get(open);
    if (!previous || previous.instant <= instant) values.set(open, { instant, value: point.value });
  }
  return [...values].sort(([a], [b]) => a - b).map(([time, point]) => point.value === null ? { time } : { time, value: point.value });
}

export function alignedMarkers(times: readonly number[], markers: ResearchMarker[], start: number, end: number) {
  return markers.flatMap(marker => {
    const instant = Date.parse(marker.time);
    if (instant < start || instant > end) return [];
    const index = upperBound(times, instant) - 1;
    return index < 0 ? [] : [{ open: times[index], instant, marker }];
  }).sort((a, b) => a.instant - b.instant);
}

/** Render supplied values without recomputing them. The embed and document use this same overlay. */
export function drawResearch(view: ChartView, layers: ResearchLayer[], panes: Pane[], times: readonly number[]) {
  const indexes = new Map(panes.map((pane, index) => [pane.id, index]));
  const palette = chartColors(), outputs: ISeriesApi<SeriesType, number>[] = [];
  const labelStyle = { color: palette.text, fontSize: palette.fontSize, fontFamily: tokenValue("--tx-font") };
  const fills = new Map<number, string[]>(), markers: SeriesMarker<number>[] = [];
  const legends = new Map<string, { title: string; names: string[]; values: Map<number, number | undefined>[] }>();
  const watermarks: ITextWatermarkPluginApi<number>[] = [];
  for (const layer of layers) {
    const start = Date.parse(layer.window.from), end = Date.parse(layer.window.to);
    let dataColor = 0;
    for (const pane of layer.panes) legends.set(paneId(layer.id, pane.key), { title: pane.title, names: [], values: [] });
    for (const series of layer.series) {
      const id = paneId(layer.id, series.pane), index = indexes.get(id)!;
      const color = series.role === "data" ? palette.series[dataColor++ % palette.series.length]
        : series.role === "main" ? palette.text : palette.textMuted;
      const options = { priceScaleId: view.side, lastValueVisible: false, priceLineVisible: false,
        lineWidth: series.role === "main" ? 2 as const : 1 as const, lineStyle: series.role === "compare" ? LineStyle.Dashed : LineStyle.Solid };
      const output = series.style === "area" ? view.chart.addSeries(AreaSeries, { ...options, lineColor: color,
        topColor: withAlpha(color, 0.28), bottomColor: withAlpha(color, 0.02) }, index)
        : view.chart.addSeries(LineSeries, { ...options, color, lineType: series.style === "step" ? LineType.WithSteps : LineType.Simple }, index);
      const data = alignedPoints(times, series.points, start, end);
      output.setData(data);
      outputs.push(output);
      if (!legends.has(id)) legends.set(id, { title: "", names: [], values: [] });
      const legend = legends.get(id)!;
      legend.names.push(series.name);
      legend.values.push(new Map(data.map(row => [row.time, row.value])));
    }
    for (const { open, marker } of alignedMarkers(times, layer.markers, start, end)) {
      const buy = marker.side === "buy";
      markers.push({ time: open, position: buy ? "belowBar" : "aboveBar", shape: buy ? "arrowUp" : "arrowDown",
        color: buy ? palette.up : palette.down });
      const details = fills.get(open) ?? [];
      details.push(`${buy ? t("Buy") : t("Sell")} ${marker.quantity} @ ${marker.price}`);
      fills.set(open, details);
    }
  }
  const plugin = markers.length ? createSeriesMarkers(view.main, markers.sort((a, b) => a.time - b.time)) : null;
  if (fills.size && !legends.has("price")) legends.set("price", { title: "", names: [], values: [] });
  const updates = [...legends].map(([id, legend]) => {
    const mark = createTextWatermark(view.chart.panes()[indexes.get(id)!], {
      horzAlign: "left", vertAlign: id === "price" ? "bottom" : "top", lines: [],
    });
    watermarks.push(mark);
    return (time: number | undefined) => {
      const fields = legend.names.map((name, i) => {
        const value = time === undefined ? undefined : legend.values[i].get(time);
        return `${name} ${value === undefined ? "—" : value.toLocaleString("en-US", {maximumFractionDigits: Math.abs(value) >= 1 ? 2 : 4})}`;
      });
      const title = legend.names.length === 1 && legend.names[0] === legend.title ? "" : legend.title;
      // One line per series: the watermark scales a line down to the pane width, so a joined line shrinks on narrow charts.
      const texts = [[title, fields[0]].filter(Boolean).join("  ·  "), ...fields.slice(1), ...(id === "price" && time !== undefined ? fills.get(time) ?? [] : [])];
      mark.applyOptions({ lines: texts.filter(Boolean).map(text => ({ text, ...labelStyle })) });
    };
  });
  let lastIndex: number | undefined;
  const show = (index: number | null) => {
    const row = index ?? times.length - 1;
    if (row === lastIndex) return;
    lastIndex = row;
    updates.forEach(update => update(times[row]));
  };
  show(null);
  const stop = view.onReadout(show);
  return () => { stop(); plugin?.detach(); watermarks.forEach(mark => mark.detach()); outputs.forEach(series => view.removeSeries(series)); };
}
