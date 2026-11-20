/* One day of the run's test period, as the draft's Values: the day with arrows, how many names and which window, Highest or Lowest first, a name search; the day's
   predictions against what followed with its figures beside it; and a row for each name with the inputs that moved its prediction most (a click opens them all). */
import { useEffect, useState } from "react";
import { TxButton, TxIcon, TxInput, TxPopover, TxSegmented, TxSkeleton } from "@talyx/sdk";
import type { ModelApi, ModelRun } from "../api";
import { dayYear, pct, sgn } from "../format";
import { Plot, type PlotFigure } from "../../chart/Plot";

interface Row {ticker: string; prediction: number | null; rank: number | null; realised: number | null; sector: string | null; contributions?: {input: string; value: number}[]; base?: number}
interface Day {date: number; rows: Row[]; reason?: string; previous: number | null; next: number | null; figure?: PlotFigure | null; stats?: {rankIc: number | null}; window?: number; note?: string | null; basis?: string}
const tone = (value: number | null | undefined, text: string) => value == null || value === 0 ? text : <span className={value > 0 ? "tx-up" : "tx-down"}>{text}</span>;
const mean = (values: number[]) => values.length ? values.reduce((total, value) => total + value, 0) / values.length : null;
/** Decimals that tell the numbers apart: two when they spread over a tenth or more, up to four when they are small (a ranked label's contributions are thousandths). */
export const decimals = (values: number[]) => {const reach = values.length ? Math.max(...values) - Math.min(...values, 0) : 0; return reach >= 0.1 ? 2 : reach >= 0.01 ? 3 : 4;};
/** What a prediction is measured in: the label as the model learned it, after the label's scale. */
export const predictionUnit = (run: Pick<ModelRun, "mode" | "label" | "params">) => {
  if (run.mode === "code") return "in the units of the model's own label";
  const scale = run.params.label_scale ?? "rank";
  return scale === "rank" ? `the ${run.label} in centred rank-score units: (daily percentile − 0.5) × 3.46; zero is the middle`
    : scale === "zscore" ? `the ${run.label} in standard deviations from that day's mean` : `in the units of the label, ${run.label}`;
};

/** The unit in a column head, beside its name: a centred score for a ranked label, standard deviations for a z-scored one; the label's own units say nothing more. */
export const unitShort = (run: Pick<ModelRun, "mode" | "params">) => run.mode === "code" ? "" : (run.params.label_scale ?? "rank") === "rank" ? ", rank score" : run.params.label_scale === "zscore" ? ", σ" : "";

export function ValuesTab({mapi, run, error, onPrice}: {onPrice(symbol: string): void; mapi: ModelApi; run: ModelRun; error(reason: unknown): void}) {
  // The day picked belongs to its run: another run opens on its own first day, without first asking for the old run's day.
  const [picked, setPicked] = useState<{run: string; date: number} | null>(null);
  const date = picked?.run === run.id ? picked.date : undefined;
  const setDate = (next: number) => setPicked({run: run.id, date: next});
  const [day, setDay] = useState<Day | null>(null);
  const [order, setOrder] = useState<"top" | "bottom">("top"), [query, setQuery] = useState(""), [open, setOpen] = useState<string | null>(null);
  const [waiting, setWaiting] = useState<{stage: string; seconds: number}>({stage: "starting", seconds: 0});
  useEffect(() => {
    // While the first look builds the run's inputs (minutes on a large run), the stage and the time so far; a failed read of the progress is reported, not dropped.
    if (day !== null) return undefined;
    let live = true;
    const started = Date.now();
    const ask = () => void mapi.valuesProgress(run.id).then(found => {if (live) setWaiting({stage: found.stage, seconds: Math.round((Date.now() - started) / 1000)});},
      reason => {if (live) {live = false; window.clearInterval(timer); error(reason);}});
    const timer = window.setInterval(ask, 1500);
    return () => {live = false; window.clearInterval(timer);};
  }, [day, run.id]);
  useEffect(() => {
    let live = true;
    setDay(null);
    void mapi.values(run.id, date).then(next => {if (live) setDay(next);}, error);
    return () => {live = false;};
  }, [run.id, date]);
  if (day === null) return waiting.stage === "inputs" ? <div className="mx-none">Building the inputs of this run, once{waiting.seconds ? ` · ${waiting.seconds} s` : "…"}</div> : <div className="mx-skel"><TxSkeleton width="40%" /></div>;
  const days = run.horizon ?? 5;
  const byPrediction = [...day.rows].filter(row => row.prediction !== null).sort((a, b) => (b.prediction ?? 0) - (a.prediction ?? 0)).map((row, index) => ({...row, place: index + 1}));
  const realised = byPrediction.filter(row => row.realised !== null).sort((a, b) => (b.realised ?? 0) - (a.realised ?? 0));
  const realisedRank = new Map(realised.map((row, index) => [row.ticker, index + 1]));
  const list = (order === "top" ? byPrediction : [...byPrediction].reverse()).filter(row => !query || row.ticker.toLowerCase().startsWith(query.toLowerCase()));
  const k = Math.max(1, Math.round(realised.length * 0.2));

  const topGroup = mean(byPrediction.slice(0, k).filter(row => row.realised !== null).map(row => row.realised as number));
  const bottomGroup = mean(byPrediction.slice(-k).filter(row => row.realised !== null).map(row => row.realised as number));
  // Rank scores use neutral colours to keep them distinct from realised returns.
  const ranked = run.mode !== "code" && (run.params.label_scale ?? "rank") === "rank";
  const shownPrediction = (value: number | null, digits: number) => value === null ? "" : ranked ? value.toFixed(digits) : tone(value, sgn(value, digits));
  return <div className="mx-values">
    <div className="mx-vtools"><span className="mx-datenav"><button type="button" aria-label="Previous day" disabled={day.previous === null} onClick={() => day.previous !== null && setDate(day.previous)}><TxIcon name="chevron" style={{transform: "rotate(180deg)"}} /></button>
      <span>{dayYear(day.date)}</span><button type="button" aria-label="Next day" disabled={day.next === null} onClick={() => day.next !== null && setDate(day.next)}><TxIcon name="chevron" /></button></span>
      <span className="mx-t3"><b className="mx-num mx-strong">{day.rows.length}</b> names{day.window ? `, window ${day.window}` : ""}</span><span className="tx-spacer" />
      <TxSegmented label="Order" value={order} options={[{value: "top", label: "Highest"}, {value: "bottom", label: "Lowest"}]} onChange={setOrder} />
      <TxInput className="mx-search" placeholder="Name" aria-label="Name" value={query} onChange={event => setQuery(event.target.value)} /></div>
    {day.reason && <div className="mx-none">{day.reason}</div>}
    <div className="mx-vhead"><div className="mx-fig mx-vsc">{day.figure ? <Plot figure={day.figure} ariaLabel="Prediction against what followed" /> : null}</div>
      <div className="mx-vstat"><span>Rank IC that day</span><span>{tone(day.stats?.rankIc, sgn(day.stats?.rankIc ?? null, 3))}</span><span>Top group, realised</span><span>{pct(topGroup, 2)}</span><span>Bottom group</span><span>{pct(bottomGroup, 2)}</span><span>Names</span><span>{day.rows.length}</span></div></div>
    <div className="mx-vtable"><table className="mx-rtable"><thead><tr><th style={{width: 44}}>Rank</th><th data-align="left">Name</th><th data-align="left">Sector</th><th title={`Prediction: ${predictionUnit(run)}`}>Prediction{unitShort(run)}</th><th title={`Return over the ${days} days from the next open`}>Realised {days}D</th><th>Its rank</th><th data-align="left" title={`Contributions to the prediction: ${predictionUnit(run)}`}>{day.basis === "Integrated gradients" ? "Integrated gradients" : "Largest contributions"}{unitShort(run)}</th></tr></thead>
      <tbody>{list.slice(0, 150).map((row, _index, shown) => {
        const pd = decimals(shown.map(item => item.prediction ?? 0).map(value => value - Math.min(...shown.map(item => item.prediction ?? 0))));
        const cd = decimals(shown.flatMap(item => (item.contributions ?? []).map(part => Math.abs(part.value))));
        const parts = [...(row.contributions ?? [])].sort((a, b) => Math.abs(b.value) - Math.abs(a.value));
        const rest = (row.prediction ?? 0) - (row.base ?? 0) - parts.reduce((total, item) => total + item.value, 0);   // the contributions add up from the base, not from zero
        const reach = Math.max(...parts.slice(0, 10).map(item => Math.abs(item.value)), Math.abs(rest), 1e-9);
        const bar = (name: string, value: number) => <div className="mx-cbar" key={name}><span title={name}>{name}</span><span className="mx-track"><i style={value >= 0 ? {left: "50%", width: `${value / reach * 50}%`, background: "var(--tx-up)", opacity: .7} : {right: "50%", width: `${-value / reach * 50}%`, background: "var(--tx-down)", opacity: .7}} /></span><span className={`mx-num ${value > 0 ? "tx-up" : value < 0 ? "tx-down" : ""}`}>{sgn(value, cd)}</span></div>;
        return <TxPopover key={row.ticker} open={open === row.ticker} onOpenChange={next => setOpen(next ? row.ticker : null)} label={`Contributions to ${row.ticker}`} trigger={<tr onClick={() => setOpen(row.ticker)}>
          <td className="mx-t3">{row.place}</td><td data-align="left" className="mx-tk"><TxButton size="sm" variant="quiet" onClick={event => {event.stopPropagation(); onPrice(row.ticker);}}>{row.ticker}</TxButton></td><td data-align="left" className="mx-t2">{row.sector ?? ""}</td><td>{shownPrediction(row.prediction, pd)}</td>
          <td>{row.realised === null ? "" : tone(row.realised, pct(row.realised, 1))}</td><td className="mx-t2">{realisedRank.get(row.ticker) ?? ""}</td>
          <td data-align="left" className="mx-ct">{parts.slice(0, 3).map(item => <span key={item.input}>{item.input} <b className={item.value > 0 ? "tx-up" : "tx-down"}>{sgn(item.value, cd)}</b></span>)}</td></tr>}>
          <div className="mx-contrib"><div className="mx-chead"><b className="mx-num">{row.ticker}</b><span className="mx-t3">{row.sector ?? ""} · rank {row.place} · prediction {ranked ? row.prediction?.toFixed(pd) : sgn(row.prediction, pd)}</span></div>
            {parts.slice(0, 10).map(item => bar(item.input, item.value))}{bar("Everything else", rest)}
            <div className="mx-chead"><span className="mx-t3">contribution to the prediction, {run.params.label_scale === "zscore" ? "in standard deviations" : run.mode === "code" || run.params.label_scale === "none" ? "in the label's units" : "on the same 0–1 scale"}</span></div></div></TxPopover>;
      })}</tbody></table></div>
  </div>;
}
