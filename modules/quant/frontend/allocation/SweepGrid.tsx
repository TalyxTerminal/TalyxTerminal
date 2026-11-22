/* A sweep's grid: one cell for each combination of the values tried, each the key number picked from that run; clicking a cell shows that run below it as an ordinary run. Nothing is marked
   as the best. */
import { t } from "../i18n";
import { useState } from "react";
import { TxButton, TxDialog, TxHeatmap, TxRegionHead, TxInput, TxSegmented, TxSelect, type TxHeatCell } from "@talyx/sdk";
import { nf, pc, pct, pp } from "../shared/format";
import type { AllocationRun } from "./api";

const MEASURES: Record<string, [string, (run: AllocationRun) => number | undefined, (value: number) => string]> = {
  sharpe: [t("Sharpe"), run => run.metrics?.sharpe ?? undefined, value => nf(value, 2)],
  ew: [t("vs equal weight"), run => run.metrics?.vsEqualWeight, value => pp(value, 2)],
  turnover: [t("Turnover"), run => run.metrics?.turnover, value => pct(value, 0)],
  move: [t("Weights move"), run => run.sweep?.move ?? undefined, value => nf(value, 1)],
};
const LABELS: Record<string, string> = {cost_multiple: t("Costs ×"), lookback: t("Lookback"), covariance: t("Covariance"), objective: t("Objective"), risk_measure: t("Risk measure"),
  max_weight: t("Each asset at most"), min_weight: t("Each asset at least"), max_turnover: t("Turnover at most")};
/** A group's "at most" is swept as `group_max:<group>` ("Stocks at most"). */
export const axisLabel = (name: string) => name.startsWith("group_max:") ? t("{group} at most", { group: name.slice("group_max:".length) }) : LABELS[name] ?? name;
const same = (a: unknown, b: unknown) => String(a) === String(b);
const UNITS: Record<string, string> = {lookback: "d", max_weight: "%", min_weight: "%", max_turnover: "%"};
const withUnit = (name: string, value: unknown) => `${value}${name.startsWith("group_max:") ? "%" : UNITS[name] ?? ""}`;
/** The settings a sweep varies and the values it tried, for the run strip ("lookback 126d, 252d, 504d · costs 0, 1, 2"). */
export function sweepSummary(runs: AllocationRun[], selected: AllocationRun | null): string | null {
  const first = selected?.sweep; if (!first) return null;
  const cells = runs.filter(run => run.sweep?.id === first.id);
  return first.axes.map(name => `${axisLabel(name).replace(/ ×$/, "").toLowerCase()} ${Array.from(new Set(cells.map(run => run.sweep!.cell[name]))).sort((a, b) => Number(a) - Number(b)).map(value => withUnit(name, value)).join(", ")}`).join(" · ");
}

export function SweepGrid({runs, selected, onSelect, onUse}: {runs: AllocationRun[]; selected: string; onSelect(id: string): void; onUse(id: string): void}) {
  const [measure, setMeasure] = useState("sharpe");
  const first = runs.find(run => run.id === selected)?.sweep;
  if (!first) return null;
  const cells = runs.filter(run => run.sweep?.id === first.id).sort((a, b) => a.sweep!.index - b.sweep!.index);
  const [ax, ay] = first.axes;
  const rows = Array.from(new Set(cells.map(run => String(run.sweep!.cell[ax])))).sort((a, b) => Number(a) - Number(b) || a.localeCompare(b)).reverse();   // smallest at the bottom, as drawn
  const cols = ay ? Array.from(new Set(cells.map(run => String(run.sweep!.cell[ay])))).sort((a, b) => Number(a) - Number(b) || a.localeCompare(b)) : [""];
  const [label, pick, format] = MEASURES[measure];
  const at = (r: number, c: number) => cells.find(run => same(run.sweep!.cell[ax], rows[r]) && (!ay || same(run.sweep!.cell[ay], cols[c])));
  const grid: TxHeatCell[][] = rows.map((_, r) => cols.map((__, c) => { const run = at(r, c); const value = run && run.state === "completed" ? pick(run) : undefined;
    return value === undefined ? {state: run && ["failed"].includes(run.state) ? "failed" : "notrun"} : {value, title: `${label} ${format(value)}`}; }));
  const chosen = cells.find(run => run.id === selected);
  const mark: [number, number] = [Math.max(0, rows.findIndex(row => same(row, chosen?.sweep?.cell[ax]))), ay ? Math.max(0, cols.findIndex(col => same(col, chosen?.sweep?.cell[ay]))) : 0];
  const done = cells.filter(run => run.state === "completed").length;
  return <div className="al-sweep">
    <TxRegionHead title={t("Sweep")} sub={t("{value}, {done} of {cellsCount} done, in-sample", { value: [ax, ay].filter(Boolean).map(axisLabel).join(" × "), done, cellsCount: cells.length })}>
      <TxSegmented label={t("Measure")} value={measure} onChange={setMeasure} options={Object.entries(MEASURES).map(([value, [text]]) => ({value, label: text}))} /></TxRegionHead>
    <div className="al-swg">
    <div className="al-hm"><span className="al-axisy">{axisLabel(ax)}</span>
    <TxHeatmap title="" x={cols} y={rows.map(row => withUnit(ax, row))} format={format}
      cells={grid} selected={mark} onCell={(r, c) => { const run = at(r, c); if (run) onSelect(run.id); }} />
    {ay && <span className="al-axisx">{axisLabel(ay)}</span>}</div>
    <div className="al-swgtable"><table className="qv-table al-sweeptable"><thead><tr>{[ax, ay].filter(Boolean).map(name => <th key={name} className="l">{axisLabel(name)}</th>)}<th>{t("CAGR")}</th><th>{t("Sharpe")}</th><th title={t("Return a year over equal weight")}>{t("vs EW")}</th><th>{t("Turnover")}</th>
      <th title={t("Average one-way change in target weights against the neighbouring cells, pt a period")}>{t("Weights move")}</th></tr></thead>
      <tbody>{cells.map(run => { const done = run.state === "completed" && run.metrics; return <tr key={run.id} className={run.id === selected ? "al-selrow" : undefined} onClick={() => onSelect(run.id)} style={{cursor: "pointer"}}>
        {[ax, ay].filter(Boolean).map(name => <td key={name} className="l">{withUnit(name, run.sweep!.cell[name])}</td>)}
        {done ? <><td>{pc(run.metrics!.cagr, 2)}</td><td>{run.metrics!.sharpe == null ? "" : nf(run.metrics!.sharpe, 2)}</td><td>{run.metrics!.vsEqualWeight == null ? "" : pp(run.metrics!.vsEqualWeight, 2)}</td><td>{pct(run.metrics!.turnover, 0)}</td>
          <td>{run.sweep?.move == null ? "" : nf(run.sweep.move, 1)}</td></> : <td colSpan={5} className="al-dim">{run.state === "failed" ? t("failed") : t("not run")}</td>}</tr>; })}</tbody></table></div>
    </div>
    {chosen && <div className="al-cellbar"><b>{[ax, ay].filter(Boolean).map(name => `${axisLabel(name)} ${chosen.sweep!.cell[name]}`).join(", ")}</b><span className="al-dim">{t("the cell picked above; below is this cell as a run")}</span>
      <span className="tx-spacer" /><TxButton size="sm" onClick={() => onUse(chosen.id)}>{t("Use these settings")}</TxButton></div>}
  </div>;
}

/** The sweep's two settings and the values to try, written as a list ("126, 252, 504"). */
export function SweepDialog({open, names, onCancel, onStart}: {open: boolean; names: string[]; onCancel(): void; onStart(axes: {name: string; values: (string | number)[]}[]): void}) {
  const [first, setFirst] = useState({name: "lookback", text: "126, 252, 504"});
  const [second, setSecond] = useState({name: "@costMultiple", text: ""});   // a second setting only when one is written: no threefold batch by default
  const parse = (text: string) => text.split(",").map(item => item.trim()).filter(Boolean).map(item => Number.isFinite(Number(item)) ? Number(item) : item);
  const axes = [first, second].filter(axis => axis.text.trim()).map(axis => ({name: axis.name, values: parse(axis.text)}));
  // Run is there once a setting has two values or more and none has one alone; a list of one is marked in its box.
  const ready = axes.length > 0 && axes.every(axis => axis.values.length >= 2);
  const alone = (text: string) => text.trim() !== "" && parse(text).length < 2;
  const options = (value: string, on: (name: string) => void) => <TxSelect value={value} onChange={event => on(event.target.value)}>{["@costMultiple", ...names].map(name => <option key={name} value={name}>{name === "@costMultiple" ? t("Costs ×") : axisLabel(name)}</option>)}</TxSelect>;
  return <TxDialog open={open} title={t("Run a sweep")} onOpenChange={next => { if (!next) onCancel(); }} footer={<><TxButton size="sm" onClick={onCancel}>{t("Cancel")}</TxButton>
    {ready && <TxButton variant="primary" size="sm" onClick={() => onStart(axes)}>{t("Run {count} backtests", { count: axes.reduce((total, axis) => total * axis.values.length, 1) })}</TxButton>}</>}>
    <div className="al-sweepform">{[[first, setFirst], [second, setSecond]].map(([axis, set], index) => <div key={index} className="al-sweeprow">
      {options((axis as typeof first).name, name => (set as typeof setFirst)({...(axis as typeof first), name}))}
      <TxInput aria-label={t("Values {index}", { index: index + 1 })} value={(axis as typeof first).text} placeholder={t("values, separated by commas")} aria-invalid={alone((axis as typeof first).text) || undefined}
        title={alone((axis as typeof first).text) ? t("A sweep tries two values or more") : undefined} onChange={event => (set as typeof setFirst)({...(axis as typeof first), text: event.target.value})} /></div>)}</div>
  </TxDialog>;
}
