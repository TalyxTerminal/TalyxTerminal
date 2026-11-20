import { useLayoutEffect, useRef, useState } from "react";
import { today, type Series } from "./data";

const day = (date: string) => Date.parse(date.slice(0, 10));

/** A shared time axis ending today. Rows that start before it run off its left edge. */
export interface Axis { from: number; to: number }

export function axisFrom(date: string): Axis {
  const year = Number(date.slice(0, 4));
  return { from: Date.UTC(year, 0, 1), to: day(today()) };
}

/** One dataset's axis: from where most of its instruments start, so one index back to 1927 does not squash the rest. */
export function axisFor(series: Series[]): Axis {
  const starts = series.map(item => item.from).sort();
  return axisFrom(starts[Math.floor(starts.length * .05)] ?? today());
}

const at = (axis: Axis, date: string) => Math.max(0, Math.min(100, (day(date) - axis.from) / (axis.to - axis.from) * 100));

/** Room one year label needs, in pixels. */
const LABEL_WIDTH = 36;

/** Years along the axis, for the column head: as many as the column's width has room for. */
export function AxisTicks({ axis }: { axis: Axis }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const element = ref.current!;
    const observer = new ResizeObserver(() => setWidth(element.clientWidth));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const first = new Date(axis.from).getUTCFullYear(), last = new Date(axis.to).getUTCFullYear();
  const room = Math.floor(width / LABEL_WIDTH);
  const every = [1, 2, 5, 10, 20, 50, 100].find(step => (last - first) / step < room);
  const years: number[] = [];
  if (every) for (let year = Math.ceil(first / every) * every; year <= last; year += every) years.push(year);
  return <span ref={ref} className="market-ticks" aria-hidden="true">
    {years.map(year => { const left = at(axis, `${year}-01-01`); return left / 100 * width + LABEL_WIDTH <= width + 4 ? <span key={year} className="tx-num" style={{ left: `${left}%` }}>{year}</span> : null; })}
  </span>;
}

/** One series on its dataset's axis: the stored stretch, gaps that can be filled, and what is missing up to today. */
export function CoverageBar({ series, axis }: { series: Series; axis: Axis }) {
  const start = at(axis, series.from), end = at(axis, series.to);
  const tail = series.status === "failed" || series.status === "behind" || series.status === "updating" ? series.status : null;
  return <span className="market-bar" aria-hidden="true">
    <i className="market-bar-fill" data-status={series.status} style={{ left: `${start}%`, width: `${Math.max(.6, end - start)}%` }} />
    {tail && <i className="market-bar-tail" data-status={tail} style={{ left: `${end}%`, width: `${Math.max(.8, 100 - end)}%` }} />}
    {series.gaps.filter(gap => gap.fillable).map(gap => <i key={gap.from} className="market-bar-gap" style={{ left: `${at(axis, gap.from)}%` }} />)}
  </span>;
}

const BINS = 120;

/** Many series on one axis: each slice is as bright as the share of the dataset's instruments that have rows there. */
export function CoverageDensity({ series, axis }: { series: Series[]; axis: Axis }) {
  const step = (axis.to - axis.from) / BINS;
  const spans = series.map(item => [day(item.from), day(item.to)]);
  const cells = Array.from({ length: BINS }, (_, index) => {
    const mid = axis.from + step * (index + .5);
    return spans.filter(([from, to]) => from <= mid && to >= mid).length / series.length;
  });
  const gaps = series.flatMap(item => item.gaps.filter(gap => gap.fillable).map(gap => at(axis, gap.from)));
  const failed = series.some(item => item.status === "failed"), behind = series.some(item => item.status === "behind");
  return <span className="market-density" aria-hidden="true">
    {cells.map((share, index) => <i key={index} style={{ opacity: share === 0 ? 0 : .25 + share * .75 }} />)}
    {gaps.map((left, index) => <b key={index} className="market-bar-gap" style={{ left: `${left}%` }} />)}
    {(failed || behind) && <b className="market-density-end" data-status={failed ? "failed" : "behind"} />}
  </span>;
}
