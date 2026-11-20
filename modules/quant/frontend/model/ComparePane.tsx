/* Several runs side by side under the table: where their settings differ, their key numbers, and their test IC window by window. */
import { useEffect, useState } from "react";
import { TxButton } from "@talyx/sdk";
import type { ModelApi, ModelRun } from "./api";
import { algoLabel, dayYear, minutes, pct, sgn } from "./format";
import { Plot, type PlotFigure } from "../chart/Plot";

const num = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value : null;

function show(value: unknown): string {
  if (Array.isArray(value)) return value.map(show).join(", ");
  if (value && typeof value === "object") {
    const row = value as Record<string, unknown>;
    if (row.instrument) return show(row.instrument);
    return String(row.displaySymbol ?? row.name ?? row.ref ?? Object.values(row).map(show).join(", "));
  }
  return String(value ?? "—");
}

export function ComparePane({mapi, runs, onClose, error}: {mapi: ModelApi; runs: ModelRun[]; onClose(): void; error(reason: unknown): void}) {
  const [settings, setSettings] = useState<{what: string; values: unknown[]}[]>([]);
  const [figure, setFigure] = useState<PlotFigure | null>(null);
  useEffect(() => {
    let live = true;
    void mapi.compare(runs.map(run => run.id)).then(next => {if (live) {setFigure(next.figure); setSettings(next.settings);}}, error);
    return () => {live = false;};
  }, [runs.map(run => run.id).join(",")]);
  const sameEnd = new Set(runs.map(run => run.dataTo)).size === 1;
  const horizons = new Set(runs.map(run => run.horizon));
  const labels = new Set(runs.map(run => String(run.params.label ?? run.label)));
  const row = (label: string, value: (run: ModelRun) => string) => <tr><td data-align="left">{label}</td>{runs.map(run => <td key={run.id}>{value(run)}</td>)}</tr>;
  return <div className="mx-compare">
    <div className="mx-selhead"><b>Comparing {runs.map(run => `#${run.number}`).join(", ")}</b>{!sameEnd && <span className="mx-t3">data to different days</span>}<span className="tx-spacer" /><TxButton size="sm" variant="quiet" onClick={onClose}>Close</TxButton></div>
    <div className="mx-selbody"><div className="mx-cmptable"><table className="mx-rtable"><thead><tr><th data-align="left" />{runs.map(run => <th key={run.id}>#{run.number}</th>)}</tr></thead><tbody>
      {row("Model", run => algoLabel(run.algo))}{row("Label", run => labels.size > 1 ? String(run.params.label ?? run.label) : run.label)}
      {settings.map(item => <tr key={item.what}><td data-align="left">{item.what}</td>{item.values.map((value, index) => <td key={index}>{show(value)}</td>)}</tr>)}
      {!sameEnd && row("Data to", run => dayYear(run.dataTo))}
      {row("Test Rank IC", run => sgn(num(run.metrics?.ric)))}{row("t", run => sgn(num(run.metrics?.t), 2))}{row("L–S net", run => pct(num(run.metrics?.lsNet)))}
      {row("Turnover", run => num(run.metrics?.turnover) === null ? "—" : `${((run.metrics!.turnover as number) * 100).toFixed(0)}%`)}{row("Time", run => minutes(run.time))}
      {horizons.size > 1 && row("Predicts", run => run.horizon ? `${run.horizon} bars` : "—")}</tbody></table></div>
      <div className="mx-chart">{figure && <Plot figure={figure} ariaLabel="Test rank IC window by window" />}</div></div></div>;
}
