/* A sweep: the key numbers, the heat map (or the bars of each year) with the table beside it, one line when the best combination sits on the edge of the grid,
   and under them the combination looked at. Nothing here picks a combination for the strategy: it only describes the best one. */
import {useEffect, useMemo, useState} from "react";
import { TxButton, TxHeatmap, TxIcon, TxKeyNumbers, TxRegionHead, TxSegmented, TxSelect, type TxHeatCell } from "@talyx/sdk";
import {Fig} from "./Fig";
import {METRIC_LABEL, axisLabel, caseTitle, cls, metricText, nf, same, span, stateText, valueText} from "./format";
import type {AxisValue, Metric, SweepAxis, SweepCase, SweepDetail} from "./types";
import type { PlotPoint } from "../../chart/Plot";

export interface SweepViewProps {
  detail: SweepDetail;
  /** The picture's axes or measure changed: ask the backend again with these. */
  onView?(view: {x?: string; y?: string; metric?: Metric}): void;
  onCell(index: number): void;
  onUse(index: number): void;
  onRunCell(index: number): void;
  onResume(): void;
  onWiden?(axis: SweepAxis, end: "smallest" | "largest"): void;
}

const MEASURES: Metric[] = ["sharpe", "totalReturn", "maxDrawdown", "trades"];
const key = (item: SweepCase) => item.index;

/** The combination whose values on the two axes drawn are the labels of the clicked cell (the others held as the picture holds them). */
export function cellAt(detail: SweepDetail, point: PlotPoint): SweepCase | null {
  const meta = (detail.figures.heat && !("unavailable" in detail.figures.heat) ? (detail.figures.heat.layout as {meta?: {x: string; y: string | null; heldValues: Record<string, AxisValue>}})?.meta : undefined);
  if (!meta) return null;
  const find = (name: string | null, label: unknown) => name ? detail.axes.find(axis => axis.name === name)?.values.find(value => valueText(value) === String(label)) : undefined;
  const wanted: Record<string, AxisValue | undefined> = {...meta.heldValues, [meta.x]: find(meta.x, point.x), ...(meta.y ? {[meta.y]: find(meta.y, point.y)} : {})};
  return detail.cases.find(item => detail.axes.every(axis => wanted[axis.name] !== undefined && same(item.params[axis.name], wanted[axis.name] as AxisValue))) ?? null;
}

/** The cells of the two axes drawn, the others held at the combination looked at (or the best one): labels, values and which cell is looked at and which is best. */
export function heatGrid(detail: SweepDetail, xName: string, yName: string | null, metric: Metric) {
  const {axes, cases} = detail;
  const looked = cases.find(item => item.index === (detail.selected ?? detail.viewed)) ?? cases.find(item => item.index === detail.bestIndex) ?? cases[0];
  const xs = axes.find(axis => axis.name === xName)!, ys = yName ? axes.find(axis => axis.name === yName)! : null;
  const held: Record<string, AxisValue> = {};
  for (const axis of axes) if (axis.name !== xName && axis.name !== yName) held[axis.name] = looked.params[axis.name];
  const rows = ys ? ys.values : [null];
  const index: (number | null)[][] = [], cells: (TxHeatCell | null)[][] = [];
  let selected: [number, number] | undefined, best: [number, number] | undefined;
  rows.forEach((row, r) => {
    index.push([]); cells.push([]);
    xs.values.forEach((column, c) => {
      const wanted = {...held, [xName]: column, ...(ys && yName ? {[yName]: row as AxisValue} : {})};
      const item = cases.find(found => axes.every(axis => same(found.params[axis.name], wanted[axis.name] as AxisValue)));
      index[r].push(item ? item.index : null);
      const value = item?.state === "completed" ? item.metrics?.[metric] : undefined;
      cells[r].push(!item ? null : item.state === "failed" ? {state: "failed", title: caseTitle(axes, item.params) + " · " + stateText(item)} : value == null ? {state: "notrun", title: caseTitle(axes, item.params)}
        : {value, title: caseTitle(axes, item.params)});
      if (item && item.index === looked.index) selected = [r, c];
      if (item && item.index === detail.bestIndex) best = [r, c];
    });
  });
  return {x: xs.values.map(valueText), y: ys ? ys.values.map(valueText) : [""], cells, index, selected, best, held};
}

export function edgeNote(detail: SweepDetail): string | null {
  const best = detail.cases.find(item => item.index === detail.bestIndex);
  const edge = best?.edge?.[0];
  if (!edge) return null;
  return `${edge.label ?? edge.name} ${valueText(edge.value)} is the ${edge.end} value tried (${valueText(edge.low)}–${valueText(edge.high)}); the best may lie beyond`;
}

export function SweepView({detail, onCell, onUse, onRunCell, onResume, onWiden}: SweepViewProps) {
  if (detail.old) return <p className="sv-why" role="status">Sweep #{detail.number} was made by an earlier TALYX; run it again to see it here</p>;
  const {axes, cases} = detail;
  const years = axes.some(axis => axis.name === "@period");
  const [pick, setPick] = useState<{x?: string; y?: string; metric?: Metric}>({});
  const metric: Metric = pick.metric ?? (MEASURES.includes(detail.metric) ? detail.metric : "sharpe");
  const xName = pick.x && axes.some(axis => axis.name === pick.x) ? pick.x : axes[0].name;
  const yName = axes.length < 2 ? null : pick.y && pick.y !== xName && axes.some(axis => axis.name === pick.y) ? pick.y : axes.find(axis => axis.name !== xName)!.name;
  const grid = useMemo(() => heatGrid(detail, xName, yName, metric), [detail, xName, yName, metric]);
  const heat = {x: xName, y: yName, held: Object.fromEntries(Object.entries(grid.held).map(([name, value]) => [name, valueText(value)]))};
  const completed = cases.filter(item => item.state === "completed");
  const best = cases.find(item => item.index === detail.bestIndex) ?? null;
  const worstKey = years ? "totalReturn" : "sharpe";
  const worst = completed.reduce<SweepCase | null>((low, item) => (low === null || (item.metrics?.[worstKey] ?? 0) < (low.metrics?.[worstKey] ?? 0) ? item : low), null);
  const viewed = cases.find(item => item.index === (detail.selected ?? detail.viewed)) ?? null;
  const stopped = ["cancelled", "interrupted"].includes(detail.state);
  const left = cases.filter(item => item.state !== "completed" && item.state !== "failed").length;
  const ranText = stopped ? `Stopped, ${detail.done - left < 0 ? 0 : cases.length - left} of ${detail.total}` : detail.state === "completed" ? `${detail.total}` : `${detail.done} of ${detail.total}`;
  const atOnce = detail.concurrency.atOnce;
  const atText = atOnce ? `${atOnce} at a time${detail.concurrency.limit === "memory" ? " (memory)" : ""}` : undefined;
  const numbers = useMemo(() => years ? [
    {label: "Years", value: String(detail.total)},
    {label: "Best year", value: best ? metricText("totalReturn", best.metrics?.totalReturn) : "", delta: best ? valueText(best.params[axes[0].name]) : undefined},
    {label: "Worst year", value: worst ? metricText("totalReturn", worst.metrics?.totalReturn) : "", delta: worst ? valueText(worst.params[axes[0].name]) : undefined},
    {label: "Ran", value: ranText, delta: atText},
  ] : [
    {label: "Combinations", value: String(detail.total), delta: axes.map(axis => axis.values.length).join(" × ") + ", in-sample"},
    {label: `Best ${METRIC_LABEL[detail.metric]}`, value: best ? metricText(detail.metric, best.metrics?.[detail.metric]) : "", delta: best ? axes.map(axis => valueText(best.params[axis.name])).join(", ") : undefined},
    {label: "Best neighbourhood", value: best?.neighbours != null ? nf(best.neighbours, 2) : "", delta: best ? `${best.neighbourCount} neighbour${best.neighbourCount === 1 ? "" : "s"}` : undefined},
    {label: `Worst ${METRIC_LABEL.sharpe}`, value: worst ? nf(worst.metrics?.sharpe, 2) : ""},
    {label: "Ran", value: ranText, delta: atText},
  ], [detail, best, worst, ranText, atText, axes, years]);
  const ordered = useMemo(() => years ? cases : [...cases].sort((a, b) => (b.metrics?.[metric] ?? -Infinity) - (a.metrics?.[metric] ?? -Infinity)), [cases, metric, years]);
  const note = edgeNote(detail);
  // Nothing looked at yet: the best combination's equity shows under the grid, so the page opens on the answer rather than on a blank.
  useEffect(() => { if (!years && !viewed && best && best.state === "completed") onCell(best.index); }, [detail.number, years, viewed === null, best?.index]);
  const showState = cases.some(item => item.state !== "completed");
  if (detail.failed === detail.total) {
    return <p className="sv-why" role="status">All {detail.total} combinations failed; the first one: {stateText(cases[0]).replace(/^Failed: /, "")}</p>;
  }
  return <div className="sv-sweep">
    <TxKeyNumbers items={numbers} />
    {stopped && left > 0 && <div className="sv-resume"><TxButton size="sm" onClick={onResume}><TxIcon name="play" />Resume the other {left}</TxButton></div>}
    <div className="sv-split">
      <div className="sv-heatbox">
        {!years && axes.length > 2 && heat && <div className="sv-axes">
          <label>x <TxSelect value={heat.x} onChange={event => setPick({x: event.target.value, y: heat.y === event.target.value ? undefined : heat.y ?? undefined, metric})}>{axes.map(axis => <option key={axis.name} value={axis.name}>{axisLabel(axis)}</option>)}</TxSelect></label>
          <label>y <TxSelect value={heat.y ?? ""} onChange={event => setPick({x: heat.x, y: event.target.value, metric})}>{axes.filter(axis => axis.name !== heat.x).map(axis => <option key={axis.name} value={axis.name}>{axisLabel(axis)}</option>)}</TxSelect></label>
          <span className="sv-held">Others held at: {Object.entries(heat.held).map(([name, value]) => `${axisLabel(axes.find(axis => axis.name === name)!).replace(/^./, first => first.toLowerCase())} ${value}`).join(", ")}</span></div>}
        {years ? <><TxRegionHead title="Each year" /><Fig fig={detail.figures.years} label="Each year" /></>
          : <TxHeatmap title={yName ? `${axisLabel(axes.find(axis => axis.name === xName)!)} × ${axisLabel(axes.find(axis => axis.name === yName)!).replace(/^./, first => first.toLowerCase())}` : axisLabel(axes[0])}
            metric={<TxSegmented label="Measure" value={metric} onChange={next => setPick({...pick, metric: next})} options={MEASURES.map(value => ({value, label: METRIC_LABEL[value]}))} />}
            x={grid.x} y={grid.y} cells={grid.cells} selected={grid.selected} best={grid.best} scale="neutral" format={value => metricText(metric, value)}
            onCell={(row, column) => { const found = grid.index[row]?.[column]; if (found !== null && found !== undefined && cases[found]?.state === "completed") onCell(found); }} />}
        {note && <p className="sv-edge">{note}{onWiden && best?.edge?.[0] && <> <TxButton size="sm" variant="quiet" onClick={() => onWiden(axes.find(axis => axis.name === best.edge[0].name)!, best.edge[0].end)}>Widen the range</TxButton></>}</p>}
      </div>
      <div className="sv-tablebox"><div className="sv-tablehead"><b>Results</b> <span className="dim">{cases.length}</span></div><table className="sv-table"><thead><tr>
        {axes.map(axis => <th key={axis.name} className="l">{axisLabel(axis)}</th>)}<th>Return</th><th>{METRIC_LABEL.sharpe}{years ? "" : " ↓"}</th>{!years && <th>Neighbours</th>}<th>Max DD</th>{showState && <th className="l">State</th>}</tr></thead>
        <tbody>{ordered.map(item => <tr key={key(item)} data-click="" data-on={item.index === viewed?.index ? "" : undefined} onClick={() => item.state === "completed" && onCell(item.index)}>
          {axes.map(axis => <td key={axis.name} className="l">{valueText(item.params[axis.name])}</td>)}
          <td className={cls(item.metrics?.totalReturn)}>{metricText("totalReturn", item.metrics?.totalReturn)}</td><td>{metricText("sharpe", item.metrics?.sharpe)}</td>
          {!years && <td className="dim">{item.neighbours != null ? nf(item.neighbours, 2) : ""}</td>}<td>{metricText("maxDrawdown", item.metrics?.maxDrawdown)}</td>
          {showState && <td className="l dim">{stateText(item)}</td>}</tr>)}</tbody></table></div>
    </div>
    {!years && viewed && <><div className="sv-sub"><b>{caseTitle(axes, viewed.params).toLowerCase()}</b>
      <span className="dim">Sharpe {nf(viewed.metrics?.sharpe, 2)}{viewed.neighbours != null ? `, neighbours ${nf(viewed.neighbours, 2)}` : ""}, max DD {metricText("maxDrawdown", viewed.metrics?.maxDrawdown)}</span><span className="sv-grow" />
      <TxButton size="sm" onClick={() => onUse(viewed.index)}>Use these values</TxButton><TxButton variant="quiet" size="sm" onClick={() => onRunCell(viewed.index)}><TxIcon name="play" />Run</TxButton></div>
      <Fig fig={detail.figures.preview} label="Equity of the combination" /></>}
  </div>;
}
export const sweepSeconds = (detail: SweepDetail) => detail.estimate ? span(detail.estimate.seconds) : "";
