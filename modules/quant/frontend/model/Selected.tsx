/* The selected run under the table: its key numbers, the window chart (the test IC window by window with the running total against two baselines, drawn
   from the first finished window on), and its checks. Before there is a result only the run row and the head say where it is. */
import { useEffect, useState } from "react";
import { TxButton, TxCompare, TxMenu, TxChecks, TxFailure, TxIcon, TxRegionHead } from "@talyx/sdk";
import type { ModelApi, ModelRun } from "./api";
import type { ParamValue } from "../objects";
import { algoLabel, minutes, pct, sgn } from "./format";
import { Plot, type PlotFigure } from "../chart/Plot";

export type Tab = "windows" | "returns" | "information" | "turnover" | "inputs" | "robustness" | "values" | "record";
const num = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value : null;
/** A signed figure (an IC, a return) in the up or down colour, as the factor's key numbers are. */
const signed = (value: number | null, text: string) => value === null || value === 0 ? text : <span className={value > 0 ? "tx-up" : "tx-down"}>{text}</span>;

export function Selected({mapi, run, compare, onStop, onResume, onLog, onGo, onStrategy, onNotebook, onPublish, onHeldOut, onLine, onEdit, runs, onCompare, error}: {
  onEdit?(run: ModelRun): void; runs?: ModelRun[]; onCompare?(id: string | null): void;
  mapi: ModelApi; run: ModelRun; compare: ModelRun | null; onStop(run: ModelRun): void; onResume(run: ModelRun, alone?: boolean, override?: Record<string, ParamValue>): void; onLog(run: ModelRun): void; onGo(tab: Tab): void;
  onStrategy(run: ModelRun): void; onNotebook?(run: ModelRun): void; onPublish(run: ModelRun): void; onHeldOut(run: ModelRun): void; onLine?(line: number): void; error(reason: unknown): void;
}) {
  const [figure, setFigure] = useState<(PlotFigure & {missing?: string}) | null>(null);
  const vs = compare && compare.id !== run.id ? compare.id : undefined;
  const epochs = run.status === "running" && Boolean(run.progress?.epochs);
  const [byWindow, setByWindow] = useState(false);
  const [epochFigure, setEpochFigure] = useState<(PlotFigure & {missing?: string}) | null>(null);
  useEffect(() => {
    if (!epochs || byWindow) {setEpochFigure(null); return;}
    let live = true;
    const load = () => void mapi.figures(run.id, ["model.work.epochs"]).then(reply => {if (live) setEpochFigure(reply.figures["model.work.epochs"] ?? null);}, error);
    load();
    const timer = setInterval(load, 1000);
    return () => {live = false; clearInterval(timer);};
  }, [run.id, epochs, byWindow]);
  useEffect(() => {
    let live = true;
    if (run.status === "queued") {setFigure(null); return;}
    void mapi.figures(run.id, ["model.work.windows"], vs).then(reply => {if (live) setFigure(reply.figures["model.work.windows"] ?? null);}, error);
    return () => {live = false;};
  }, [run.id, run.windowsDone, run.status, run.updatedAt, vs]);
  const m = run.metrics ?? {};
  const done = run.status === "completed";
  const sameHorizon = !compare || compare.horizon === null || run.horizon === null || compare.horizon === run.horizon;
  const o = compare?.status === "completed" && sameHorizon ? compare.metrics ?? {} : null;
  const vsText = (a: unknown, b: unknown, digits: number, unit = "") => o && num(a) !== null && num(b) !== null ? `${sgn((a as number) - (b as number), digits)}${unit} vs #${compare!.number}` : null;
  const delta = {ric: vsText(m.ric, o?.ric, 4), t: vsText(m.t, o?.t, 2), net: o && num(m.lsNet) !== null && num(o.lsNet) !== null ? `${(((m.lsNet as number) - (o.lsNet as number)) * 100) >= 0 ? "+" : "−"}${Math.abs(((m.lsNet as number) - (o.lsNet as number)) * 100).toFixed(1)} pt vs #${compare!.number}` : null};
  const cells: {label: string; value: React.ReactNode; tab?: Tab; delta?: string | null}[] = [
    {label: "Test Rank IC", value: signed(num(m.ric), sgn(num(m.ric))), tab: "information", delta: delta.ric}, {label: "t", value: sgn(num(m.t), 2), tab: "information", delta: delta.t},
    {label: "vs best input", value: sgn(num(m.vsInput)), tab: "robustness", delta: run.baselines?.bestInput?.name ?? null}, {label: "L–S net", value: signed(num(m.lsNet), pct(num(m.lsNet))), tab: "returns", delta: delta.net},
    {label: "Turnover", value: num(m.turnover) !== null ? `${((m.turnover as number) * 100).toFixed(0)}%` : "", tab: "turnover"},
    {label: "Train − test", value: num(m.trainIc) !== null ? sgn((m.trainIc as number) - (m.ric as number)) : "", tab: "windows"},
    {label: "Windows above 0", value: m.windows ? `${m.positive}/${m.windows}` : "", tab: "windows"}, {label: "Checks", value: `${run.passed}/${run.passed + run.review}`},
  ];
  // Nothing is drawn before there is something to draw (design 11-13, empty screens): the key numbers come with a finished run,
  // the window chart with the first finished window (or a deep model's first epochs); until then the run's row and the head say where it is.
  const epochShown = epochs && !byWindow && epochFigure !== null && !epochFigure.missing;
  const chartShown = epochShown || ((done || run.windowsDone > 0) && figure !== null);
  const checks = (run.checks ?? []).map(item => ({name: item.name, value: item.value, need: item.need, state: item.state === "block" ? "fix" as const : item.state, title: item.detail}));
  return <div className="mx-sel">
    <div className="mx-selhead"><b>#{run.number}</b><span>{algoLabel(run.algo)} · {run.label}</span>{run.status === "running" && <span className="mx-t3"><span className="tx-spin" /> Running{run.progress?.window ? ` · window ${run.progress.window}${run.windowsTotal ? ` of ${run.windowsTotal}` : ""}` : ""}{epochText(run)}</span>}
      <span className="tx-spacer" />
      {/* As every Quant page's compare control: "Compare" until a run is picked, then "vs #n" (it read "vs …"). */}
      {onCompare && runs && done && <TxCompare run={compare && compare.id !== run.id ? `#${compare.number}` : null}
        items={[{id: "none", label: "No comparison", onSelect: () => onCompare(null)}, ...runs.filter(item => item.status === "completed" && item.id !== run.id).slice(0, 12).map(item => ({id: item.id, label: `#${item.number} · ${item.label}`, onSelect: () => onCompare(item.id)}))]} />}
      {onEdit && done && <TxButton size="sm" variant="quiet" onClick={() => onEdit(run)}>Edit from this</TxButton>}
      {run.status === "running" && <TxButton size="sm" onClick={() => onStop(run)}>Stop</TxButton>}
      {done && <><TxMenu label="Use this run" align="end" trigger={<TxButton size="sm" variant="quiet">Use this run<TxIcon name="chevron" style={{transform: "rotate(90deg)"}} /></TxButton>}
        items={[{id: "strategy", label: `New strategy · trades #${run.number}'s predictions; data settings follow it`, icon: "bolt", onSelect: () => onStrategy(run)}]} />{onNotebook && <TxButton size="sm" variant="quiet" onClick={() => onNotebook(run)}>Notebook</TxButton>}<TxButton size="sm" variant="quiet" onClick={() => onHeldOut(run)}>Validate on held-out</TxButton>
        <TxButton size="sm" variant="primary" onClick={() => onPublish(run)}>Publish</TxButton></>}</div>
    {done && <div className="mx-keys">{cells.map(cell => <button key={cell.label} type="button" className="mx-key" data-go={cell.tab ? "" : undefined} onClick={() => cell.tab && onGo(cell.tab)}><span className="mx-t3">{cell.label}</span><b className="mx-num">{cell.value}</b><span className="mx-t3 mx-delta">{cell.delta ?? ""}</span></button>)}</div>}
    {compare && !sameHorizon && <div className="mx-t3 mx-predicts">#{compare.number} predicts {compare.horizon} bars ahead; this one {run.horizon}</div>}
    {run.status === "failed" && run.failure && <TxFailure title={run.failure.title} message={run.failure.detail ?? run.failure.message} code={Boolean(run.failure.file)} tone="fix"
      context={run.failure.kind && run.failure.window && run.failure.window > 1 ? `Windows 1–${run.failure.window - 1} are kept.` : undefined}
      actions={<><TxButton size="sm" onClick={() => onResume(run)}>Resume{run.failure.resumeFrom ? ` from window ${run.failure.resumeFrom}` : ""}</TxButton>
        {(run.failure.saved ?? run.failure.epoch) !== undefined && Boolean(run.failure.saved ?? run.failure.epoch) && <TxButton size="sm" variant="quiet" onClick={() => onResume(run, true)}>Resume from epoch {run.failure.saved ?? run.failure.epoch}, alone</TxButton>}
        {run.failure.kind && run.failure.tryBatch && <TxButton size="sm" variant="quiet" onClick={() => onResume(run, false, {nn_batch: run.failure!.tryBatch!})}>Resume with batch size {run.failure.tryBatch}</TxButton>}
        {run.failure.kind === "gpu-memory" && <TxButton size="sm" variant="quiet" onClick={() => onResume(run, false, {nn_device: "cpu"})}>Resume on the CPU</TxButton>}
        {onEdit && run.failure.kind && <TxButton size="sm" variant="quiet" onClick={() => onEdit(run)}>Edit from this</TxButton>}
        {run.failure.resumeFrom && <TxButton size="sm" variant="quiet" onClick={() => onResume(run, true)}>Resume from window {run.failure.resumeFrom}, alone</TxButton>}{onLine && run.failure.line && <TxButton size="sm" variant="quiet" onClick={() => onLine(run.failure!.line!)}>Open {run.failure.file?.split(/[\\/]/).pop() ?? "the code"} line {run.failure.line}</TxButton>}<TxButton size="sm" variant="quiet" onClick={() => onLog(run)}>Log</TxButton></>} />}
    {run.status === "stopped" && <div className="mx-none">Stopped at window {run.stopped?.window ?? run.windowsDone}{run.windowsTotal ? ` of ${run.windowsTotal}` : ""}. Finished windows are kept; resume continues from there.</div>}
    {(chartShown || checks.length > 0) && <div className="mx-selbody" data-solo={checks.length ? undefined : ""}>{chartShown && <div className="mx-chartbox"><TxRegionHead title={epochShown ? `Window ${run.progress?.window ?? ""}, by epoch` : "Each window"}>{epochs && <button type="button" className="mx-link" onClick={() => setByWindow(!byWindow)}>{byWindow ? "Epochs" : "Windows"}</button>}</TxRegionHead><div className="mx-chart">{epochShown ? <Plot figure={epochFigure!} ariaLabel={`Loss by epoch of window ${run.progress?.window ?? ""}`} /> : figure!.missing ? <div className="mx-none">{figure!.missing}</div> : <Plot figure={figure!} ariaLabel={`Test IC by window of #${run.number}`} />}</div></div>}
      {checks.length > 0 && <div className="mx-seldchecks"><TxChecks title="Checks" scope="in-sample only" groups={[{items: checks}]} /></div>}</div>}
  </div>;
}
function epochText(run: ModelRun) {
  const p = run.progress;
  if (!p?.epochs) return "";
  if (p.pretrain && !p.epoch) return ` · pretraining, epoch ${p.pretrain} of ${p.pretrains}`;
  const loss = typeof p.validLoss === "number" ? `, validation loss ${p.validLoss.toFixed(4)}` : "";
  const best = typeof p.bestLoss === "number" && p.bestEpoch ? ` (best ${p.bestLoss.toFixed(4)} at ${p.bestEpoch})` : "";
  return ` · epoch ${p.epoch ?? 0} of ${p.epochs}${loss}${best}`;
}
void TxIcon; void minutes;
