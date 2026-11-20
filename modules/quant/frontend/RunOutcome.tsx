import { useEffect, useState, type ReactNode } from "react";
import { TxButton, TxDetails, TxIcon, TxSegmented } from "@talyx/sdk";
import "./strategyWorkbench.css";
import { formatNumber } from "./format";
import { active, detailText, runStatus, type Api, type Frame, type RunError, type RunStage, type RunSummary, type RunView } from "./objects";
import { HistoryStart, historyBoundary } from "./DataSettings";
import { dataText } from "./RunHistory";
import type { Run } from "./types";

const STEPS: Record<"strategy" | "sweep" | "research" | "publish", {stage: RunStage; label: string}[]> = {
  strategy: [{stage: "check", label: "Checks"}, {stage: "code", label: "Code"}, {stage: "data", label: "Data"}, {stage: "environment", label: "Environment"}, {stage: "backtest", label: "Backtest"}],
  sweep: [{stage: "code", label: "Code"}, {stage: "data", label: "Data"}, {stage: "environment", label: "Environment"}, {stage: "sweep", label: "Backtests"}],
  research: [{stage: "code", label: "Code"}, {stage: "data", label: "Data"}, {stage: "environment", label: "Environment"}, {stage: "run", label: "Report"}],
  publish: [{stage: "history", label: "History"}, {stage: "checks", label: "Future-data check"}, {stage: "commit", label: "Library"}],
};
const STREAMS = [{value: "stdout", label: "Output"}, {value: "stderr", label: "Errors"}, {value: "environment_stderr", label: "Environment"}] as const;
type Stream = typeof STREAMS[number]["value"];
/** A run as the report components read it, under its own routes. */
export const reportRun = (run: RunView): Run => ({id: run.id, base: `/object-runs/${encodeURIComponent(run.id)}`, name: run.summary.name, state: run.summary.state,
  createdAt: run.summary.createdAt, updatedAt: run.summary.endedAt ?? run.summary.createdAt, message: run.summary.message ?? undefined, result: run.result ?? undefined});

/** The report area for one run: where it is while running, what went wrong when it failed, the report and its raw output when it finished. */
export function RunOutcome({api, run, number, view, kind, report, onCancel, onLine, onLibrary}: {
  api: Api; run: RunView; number: number | undefined; view: "report" | "output"; kind: "strategy" | "research";
  report(run: Run): ReactNode; onCancel(): void; onLine(file: string, line: number): void; onLibrary(versionId: string): void;
}) {
  const summary = run.summary;
  const flow = summary.runKind === "publish" ? STEPS.publish : summary.runKind === "sweep" ? STEPS.sweep : STEPS[kind];
  const sweep = summary.sweep;
  if (active(summary)) {
    const reached = flow.findIndex(step => step.stage === summary.stage);
    return <div className="qx-progress" aria-live="polite">
      <div className="qx-progress-head"><span className="qx-spin" />
        <strong>{summary.runKind === "publish" ? `Publishing ${summary.publish!.name}` : `Run ${number}`} · {runStatus(summary)}
          {sweep !== null && summary.stage === "sweep" && ` · ${sweep.completed + sweep.failed} of ${sweep.total} done`}</strong><span className="tx-spacer" />
        <TxButton size="sm" disabled={summary.state === "cancel_requested"} onClick={onCancel}><TxIcon name="stop" />Cancel</TxButton></div>
      <ol className="qx-steps">{flow.map((step, index) => <li key={step.stage} data-state={index < reached ? "done" : index === reached ? "now" : "next"}>{step.label}</li>)}</ol>
    </div>;
  }
  const output = <RunOutput api={api} runId={run.id} />;
  if (view === "output") return <div className="qx-outcome">{output}</div>;
  if (summary.runKind === "publish") return <div className="qx-outcome"><PublishOutcome summary={summary} error={run.error} onLibrary={onLibrary} /></div>;
  if (summary.state !== "completed") return <div className="qx-outcome">
    {summary.state === "failed" && run.error ? <Failure error={run.error} kind={kind} onLine={onLine} />
      : <div className="qx-ended" data-state={summary.state}>
        <strong>Run {number} {summary.state === "cancelled" ? "was cancelled" : summary.state === "failed" ? "failed" : "was interrupted"}</strong>
        <p>{summary.state === "cancelled" ? "It stopped before finishing; nothing was kept from it." : summary.state === "failed" ? summary.message : "TALYX closed while it was running. It was not started again."}</p>
      </div>}
  </div>;
  return <div className="qx-outcome">{report(reportRun(run))}</div>;
}

const absolute = (path: string) => /^([A-Za-z]:)?[\\/]/.test(path);
/** The frame's file relative to the object's own folder, or null for library code outside it. */
const ownFile = (frame: Frame) => [frame.path, frame.file].find(path => !absolute(path)) ?? null;

/** The traceback as Python wrote it, with each file named relative to the object's folder, or by its name for library code. */
export const shortPaths = (traceback: string) => traceback.replace(/File "([^"]+)"/g, (_, path: string) => {
  const own = /[\\/]qruns[\\/][^\\/]+[\\/]source[\\/](.+)$/.exec(path);
  return `File "${own ? own[1].replaceAll("\\", "/") : path.split(/[\\/]/).at(-1)}"`;
});

/** Where the run broke: the file and line in the author's code with the original error, or the data source and what it said. */
function Failure({error, kind, onLine}: {error: RunError; kind: "strategy" | "research"; onLine(file: string, line: number): void}) {
  const frames = error.frames ?? [];
  // A range that starts before the history reads the same here as in the data settings.
  const boundary = historyBoundary(error.code, error.details);
  return <div className="qx-failure" role="alert">
    {error.stage === "data" ? <>
      <strong>Data could not be prepared</strong>
      {boundary !== null ? <HistoryStart boundary={boundary} /> : <p className="qx-failure-message">{error.message}</p>}
      {error.request && <p className="qx-muted">Requested {dataText(error.request)}</p>}
    </> : <>
      <strong>{error.file && error.line ? <>Error in <button type="button" className="qx-link" onClick={() => onLine(error.file!, error.line!)}>{error.file} line {error.line}</button></>
        : error.stage === "environment" ? `The ${kind} environment could not be prepared` : kind === "strategy" ? "The backtest failed" : "The code failed"}</strong>
      <pre className="qx-failure-message">{error.message}</pre>
      {frames.length > 0 && <ol className="qx-frames">{frames.map((frame, index) => {const own = ownFile(frame); return <li key={index}>
        {own ? <button type="button" className="qx-link" onClick={() => onLine(own, frame.line)}>{own}:{frame.line}</button> : <span>{frame.path.split(/[\\/]/).at(-1)}:{frame.line}</span>}
        <span className="qx-muted"> in {frame.function}</span>{frame.text && <code>{frame.text}</code>}
      </li>;})}</ol>}
      {error.traceback && <TxDetails label="Full traceback"><pre className="qx-log">{shortPaths(error.traceback)}</pre></TxDetails>}
    </>}
  </div>;
}

/** A finished, failed or stopped publication. Nothing reaches the library unless every step passed. */
function PublishOutcome({summary, error, onLibrary}: {summary: RunSummary; error: RunError | null; onLibrary(versionId: string): void}) {
  const publish = summary.publish!;
  if (summary.state === "completed") return <div className="qx-ended" data-state="completed">
    <strong>Published {publish.name} to the library</strong>
    <p>{publish.items.length > 1 ? `${publish.items.length} items, ` : ""}history filled in and checked for future data at {publish.checks} points.</p>
    {publish.versions.length > 0 && <TxButton size="sm" onClick={() => onLibrary(publish.versions[0])}><TxIcon name="bookmark" />Open in library</TxButton>}
  </div>;
  if (summary.state === "failed" && error !== null && error.code === "future_data") return <FutureData error={error} name={publish.name} />;
  return <div className={summary.state === "failed" ? "qx-failure" : "qx-ended"} data-state={summary.state} role={summary.state === "failed" ? "alert" : undefined}>
    <strong>{summary.state === "failed" ? `${publish.name} was not published` : summary.state === "cancelled" ? "Publishing was cancelled" : "Publishing was interrupted"}</strong>
    <p className="qx-failure-message">{summary.state === "failed" ? summary.message : "Nothing was added to the library."}</p>
    {error !== null && error.details && "failure" in error.details && <TxDetails label="Full traceback">
      <pre className="qx-log">{shortPaths(String((error.details.failure as {traceback?: string}).traceback ?? ""))}</pre></TxDetails>}
  </div>;
}

/** The publication used data from after a value's time: which symbol, which moment, and the two values that disagree. */
function FutureData({error, name}: {error: RunError; name: string}) {
  const details = error.details as {instrument: string; valueTime: number; cutoff: number; withAllData: number; whenCut: number | null; changed: number};
  const when = (value: number) => detailText(value).replace(" 00:00", "");
  return <div className="qx-failure" role="alert">
    <strong>{name} was not published: it uses data from after the time each value is dated</strong>
    <p>The data was cut at {when(details.cutoff)} and the code run again. A value dated before the cut came out differently, so it must have looked at later data.</p>
    <dl className="qx-failure-details">
      <div><dt>Symbol</dt><dd>{details.instrument}</dd></div>
      <div><dt>Value dated</dt><dd className="tx-num">{when(details.valueTime)}</dd></div>
      <div><dt>With all the data</dt><dd className="tx-num">{formatNumber(details.withAllData, 6)}</dd></div>
      <div><dt>With data up to {when(details.cutoff)}</dt><dd className="tx-num">{details.whenCut === null ? "No value" : formatNumber(details.whenCut, 6)}</dd></div>
      <div><dt>Values that changed</dt><dd className="tx-num">{formatNumber(details.changed)}</dd></div>
    </dl>
    <p className="qx-muted">Shift the factor so each value uses only bars that had closed by then, run again and publish that run.</p>
  </div>;
}

/** The run's own printed output, read in pages as it grows. */
function RunOutput({api, runId}: {api: Api; runId: string}) {
  const [stream, setStream] = useState<Stream>("stdout");
  const [text, setText] = useState<string | null>(null);
  const [next, setNext] = useState<{offset: number; eof: boolean}>({offset: 0, eof: true});
  const [error, setError] = useState<string | null>(null);
  const read = (offset: number) => api.log(runId, stream, offset).then(page => {
    setText(current => offset && current !== null ? current + page.text : page.text); setNext({offset: page.nextOffset, eof: page.eof});
  }, reason => setError(String(reason)));
  useEffect(() => {setText(null); setError(null); void read(0);}, [runId, stream]);
  return <div className="qx-output">
    <TxSegmented label="Stream" value={stream} options={STREAMS.map(item => ({value: item.value, label: item.label}))} onChange={setStream} />
    {error ? <p role="alert" className="qx-error">{error}</p> : <pre className="qx-log">{text === null ? "" : text || "Nothing was written."}</pre>}
    {!next.eof && <TxButton size="sm" variant="quiet" onClick={() => void read(next.offset)}>Read more</TxButton>}
  </div>;
}
