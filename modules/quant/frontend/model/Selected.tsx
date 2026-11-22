/* The selected run under the table: its key numbers, the window chart (the test IC window by window with the running total against two baselines, drawn
   from the first finished window on), and its checks. Before there is a result only the run row and the head say where it is. */
import { t } from "../i18n";
import { useEffect, useState } from "react";
import { TxButton, TxCompare, TxMenu, TxChecks, TxFailure, TxIcon, TxRegionHead } from "@talyx/sdk";
import type { ModelApi, ModelRun } from "./api";
import type { ParamValue } from "../objects";
import { algoLabel, minutes, pct, sgn } from "./format";
import { Plot, type PlotFigure } from "../chart/Plot";
import { DataNotes } from "../shared/DataNotes";

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
  const vsText = (a: unknown, b: unknown, digits: number, unit = "") => o && num(a) !== null && num(b) !== null ? t("{value}{unit} vs #{run}", { value: sgn((a as number) - (b as number), digits), unit, run: compare!.number }) : null;
  const delta = {ric: vsText(m.ric, o?.ric, 4), t: vsText(m.t, o?.t, 2), net: o && num(m.lsNet) !== null && num(o.lsNet) !== null ? t("{sign}{delta} pt vs #{run}", { sign: (((m.lsNet as number) - (o.lsNet as number)) * 100) >= 0 ? "+" : "−", delta: Math.abs(((m.lsNet as number) - (o.lsNet as number)) * 100).toFixed(1), run: compare!.number }) : null};
  const cells: {label: string; value: React.ReactNode; tab?: Tab; delta?: string | null}[] = [
    {label: t("Test Rank IC"), value: signed(num(m.ric), sgn(num(m.ric))), tab: "information", delta: delta.ric}, {label: "t", value: sgn(num(m.t), 2), tab: "information", delta: delta.t},
    {label: t("vs best input"), value: sgn(num(m.vsInput)), tab: "robustness", delta: run.baselines?.bestInput?.name ?? null}, {label: t("L–S net"), value: signed(num(m.lsNet), pct(num(m.lsNet))), tab: "returns", delta: delta.net},
    {label: t("Turnover"), value: num(m.turnover) !== null ? `${((m.turnover as number) * 100).toFixed(0)}%` : "", tab: "turnover"},
    {label: t("Train − test"), value: num(m.trainIc) !== null ? sgn((m.trainIc as number) - (m.ric as number)) : "", tab: "windows"},
    {label: t("Windows above 0"), value: m.windows ? `${m.positive}/${m.windows}` : "", tab: "windows"}, {label: t("Checks"), value: `${run.passed}/${run.passed + run.review}`},
  ];
  // Nothing is drawn before there is something to draw (design 11-13, empty screens): the key numbers come with a finished run,
  // the window chart with the first finished window (or a deep model's first epochs); until then the run's row and the head say where it is.
  const epochShown = epochs && !byWindow && epochFigure !== null && !epochFigure.missing;
  const chartShown = epochShown || ((done || run.windowsDone > 0) && figure !== null);
  const checks = (run.checks ?? []).map(item => ({name: item.name, value: item.value, need: item.need, state: item.state === "block" ? "fix" as const : item.state, title: item.detail}));
  return <div className="mx-sel">
    <div className="mx-selhead"><b>#{run.number}</b><span>{algoLabel(run.algo)} · {run.label}</span>{run.status === "running" && <span className="mx-t3"><span className="tx-spin" />{(run.progress?.window ? (run.windowsTotal ? t(" Running · window {window} of {windowsTotal}{run}", { window: run.progress.window, windowsTotal: run.windowsTotal, run: epochText(run) }) : t(" Running · window {window}{run}", { window: run.progress.window, run: epochText(run) })) : t(" Running{run}", { run: epochText(run) }))}</span>}
      <DataNotes notes={run.dataNotes} />
      <span className="tx-spacer" />
      {/* As every Quant page's compare control: "Compare" until a run is picked, then "vs #n" (it read "vs …"). */}
      {onCompare && runs && done && <TxCompare run={compare && compare.id !== run.id ? `#${compare.number}` : null}
        items={[{id: "none", label: t("No comparison"), onSelect: () => onCompare(null)}, ...runs.filter(item => item.status === "completed" && item.id !== run.id).slice(0, 12).map(item => ({id: item.id, label: `#${item.number} · ${item.label}`, onSelect: () => onCompare(item.id)}))]} />}
      {onEdit && done && <TxButton size="sm" variant="quiet" onClick={() => onEdit(run)}>{t("Edit from this")}</TxButton>}
      {run.status === "running" && <TxButton size="sm" onClick={() => onStop(run)}>{t.c("action", "Stop")}</TxButton>}
      {done && <><TxMenu label={t("Use this run")} align="end" trigger={<TxButton size="sm" variant="quiet">{t("Use this run")}<TxIcon name="chevron" style={{transform: "rotate(90deg)"}} /></TxButton>}
        items={[{id: "strategy", label: t("New strategy · trades #{number}'s predictions; data settings follow it", { number: run.number }), icon: "bolt", onSelect: () => onStrategy(run)}]} />{onNotebook && <TxButton size="sm" variant="quiet" onClick={() => onNotebook(run)}>{t("Notebook")}</TxButton>}<TxButton size="sm" variant="quiet" onClick={() => onHeldOut(run)}>{t("Validate on held-out")}</TxButton>
        <TxButton size="sm" variant="primary" onClick={() => onPublish(run)}>{t("Publish")}</TxButton></>}</div>
    {done && <div className="mx-keys">{cells.map(cell => <button key={cell.label} type="button" className="mx-key" data-go={cell.tab ? "" : undefined} onClick={() => cell.tab && onGo(cell.tab)}><span className="mx-t3">{cell.label}</span><b className="mx-num">{cell.value}</b><span className="mx-t3 mx-delta">{cell.delta ?? ""}</span></button>)}</div>}
    {compare && !sameHorizon && <div className="mx-t3 mx-predicts">{t("#{number} predicts {horizon} bars ahead; this one {horizon2}", { number: compare.number, horizon: compare.horizon ?? "", horizon2: run.horizon ?? "" })}</div>}
    {run.status === "failed" && run.failure && <TxFailure title={run.failure.title} message={run.failure.detail ?? run.failure.message} code={Boolean(run.failure.file)} tone="fix"
      context={run.failure.kind && run.failure.window && run.failure.window > 1 ? t("Windows 1–{last} are kept.", { last: run.failure.window - 1 }) : undefined}
      actions={<><TxButton size="sm" onClick={() => onResume(run)}>{(run.failure.resumeFrom ? t("Resume from window {resumeFrom}", { resumeFrom: run.failure.resumeFrom }) : t("Resume"))}</TxButton>
        {(run.failure.saved ?? run.failure.epoch) !== undefined && Boolean(run.failure.saved ?? run.failure.epoch) && <TxButton size="sm" variant="quiet" onClick={() => onResume(run, true)}>{t("Resume from epoch {saved}, alone", { saved: run.failure.saved ?? run.failure.epoch ?? "" })}</TxButton>}
        {run.failure.kind && run.failure.tryBatch && <TxButton size="sm" variant="quiet" onClick={() => onResume(run, false, {nn_batch: run.failure!.tryBatch!})}>{t("Resume with batch size {tryBatch}", { tryBatch: run.failure.tryBatch })}</TxButton>}
        {run.failure.kind === "gpu-memory" && <TxButton size="sm" variant="quiet" onClick={() => onResume(run, false, {nn_device: "cpu"})}>{t("Resume on the CPU")}</TxButton>}
        {onEdit && run.failure.kind && <TxButton size="sm" variant="quiet" onClick={() => onEdit(run)}>{t("Edit from this")}</TxButton>}
        {run.failure.resumeFrom && <TxButton size="sm" variant="quiet" onClick={() => onResume(run, true)}>{t("Resume from window {resumeFrom}, alone", { resumeFrom: run.failure.resumeFrom })}</TxButton>}{onLine && run.failure.line && <TxButton size="sm" variant="quiet" onClick={() => onLine(run.failure!.line!)}>{t("Open {file} line {line}", { file: run.failure.file?.split(/[\\/]/).pop() ?? t("the code"), line: run.failure.line })}</TxButton>}<TxButton size="sm" variant="quiet" onClick={() => onLog(run)}>{t("Log")}</TxButton></>} />}
    {run.status === "stopped" && <div className="mx-none">{(run.windowsTotal ? t("Stopped at window {window} of {windowsTotal}. Finished windows are kept; resume continues from there.", { window: run.stopped?.window ?? run.windowsDone, windowsTotal: run.windowsTotal }) : t("Stopped at window {window}. Finished windows are kept; resume continues from there.", { window: run.stopped?.window ?? run.windowsDone }))}</div>}
    {(chartShown || checks.length > 0) && <div className="mx-selbody" data-solo={checks.length ? undefined : ""}>{chartShown && <div className="mx-chartbox"><TxRegionHead title={epochShown ? t("Window {window}, by epoch", { window: run.progress?.window ?? "" }) : t("Each window")}>{epochs && <button type="button" className="mx-link" onClick={() => setByWindow(!byWindow)}>{byWindow ? t("Epochs") : t("Windows")}</button>}</TxRegionHead><div className="mx-chart">{epochShown ? <Plot figure={epochFigure!} ariaLabel={t("Loss by epoch of window {window}", { window: run.progress?.window ?? "" })} /> : figure!.missing ? <div className="mx-none">{figure!.missing}</div> : <Plot figure={figure!} ariaLabel={t("Test IC by window of #{number}", { number: run.number })} />}</div></div>}
      {checks.length > 0 && <div className="mx-seldchecks"><TxChecks title={t("Checks")} scope={t("in-sample only")} groups={[{items: checks}]} /></div>}</div>}
  </div>;
}
function epochText(run: ModelRun) {
  const p = run.progress;
  if (!p?.epochs) return "";
  if (p.pretrain && !p.epoch) return t(" · pretraining, epoch {epoch} of {epochs}", { epoch: p.pretrain, epochs: String(p.pretrains) });
  const loss = typeof p.validLoss === "number" ? t(", validation loss {loss}", { loss: p.validLoss.toFixed(4) }) : "";
  const best = typeof p.bestLoss === "number" && p.bestEpoch ? t(" (best {loss} at {epoch})", { loss: p.bestLoss.toFixed(4), epoch: p.bestEpoch }) : "";
  return t(" · epoch {epoch} of {epochs}{loss}{best}", { epoch: p.epoch ?? 0, epochs: p.epochs, loss, best });
}
void TxIcon; void minutes;
