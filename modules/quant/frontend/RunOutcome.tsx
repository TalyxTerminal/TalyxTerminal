import { t } from "./i18n";
import { useEffect, useState, type ReactNode } from "react";
import { TxButton, TxDetails, TxIcon, TxSegmented } from "@talyx/sdk";
import "./strategyWorkbench.css";
import { formatNumber } from "./format";
import { active, detailText, runStatus, type Api, type Frame, type RunError, type RunStage, type RunSummary, type RunView } from "./objects";
import { HistoryStart, historyBoundary } from "./DataSettings";
import { dataText } from "./RunHistory";
import type { Run } from "./types";

const STEPS: Record<"strategy" | "sweep" | "research" | "publish", {stage: RunStage; label: string}[]> = {
  strategy: [{stage: "check", label: t("Checks")}, {stage: "code", label: t("Code")}, {stage: "data", label: t("Data")}, {stage: "environment", label: t("Environment")}, {stage: "backtest", label: t("Backtest")}],
  sweep: [{stage: "code", label: t("Code")}, {stage: "data", label: t("Data")}, {stage: "environment", label: t("Environment")}, {stage: "sweep", label: t("Backtests")}],
  research: [{stage: "code", label: t("Code")}, {stage: "data", label: t("Data")}, {stage: "environment", label: t("Environment")}, {stage: "run", label: t("Report")}],
  publish: [{stage: "history", label: t("History")}, {stage: "checks", label: t("Future-data check")}, {stage: "commit", label: t("Library")}],
};
const STREAMS = [{value: "stdout", label: t("Output")}, {value: "stderr", label: t("Errors")}, {value: "environment_stderr", label: t("Environment")}] as const;
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
        <strong>{summary.runKind === "publish" ? t("Publishing {name}", { name: summary.publish!.name }) : t("Run {number}", { number: number ?? "" })} · {runStatus(summary)}
          {sweep !== null && summary.stage === "sweep" && t(" · {completed} of {total} done", { completed: sweep.completed + sweep.failed, total: sweep.total })}</strong><span className="tx-spacer" />
        <TxButton size="sm" disabled={summary.state === "cancel_requested"} onClick={onCancel}><TxIcon name="stop" />{t("Cancel")}</TxButton></div>
      <ol className="qx-steps">{flow.map((step, index) => <li key={step.stage} data-state={index < reached ? "done" : index === reached ? "now" : "next"}>{step.label}</li>)}</ol>
    </div>;
  }
  const output = <RunOutput api={api} runId={run.id} />;
  if (view === "output") return <div className="qx-outcome">{output}</div>;
  if (summary.runKind === "publish") return <div className="qx-outcome"><PublishOutcome summary={summary} error={run.error} onLibrary={onLibrary} /></div>;
  if (summary.state !== "completed") return <div className="qx-outcome">
    {summary.state === "failed" && run.error ? <Failure error={run.error} kind={kind} onLine={onLine} />
      : <div className="qx-ended" data-state={summary.state}>
        <strong>{summary.state === "cancelled" ? t("Run {number} was cancelled", {number: number ?? ""})
          : summary.state === "failed" ? t("Run {number} failed", {number: number ?? ""}) : t("Run {number} was interrupted", {number: number ?? ""})}</strong>
        <p>{summary.state === "cancelled" ? t("It stopped before finishing; nothing was kept from it.") : summary.state === "failed" ? summary.message : t("TALYX closed while it was running. It was not started again.")}</p>
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
  return t("File \"{file}\"", { file: String(own ? own[1].replaceAll("\\", "/") : path.split(/[\\/]/).at(-1)) });
});

/** Where the run broke: the file and line in the author's code with the original error, or the data source and what it said. */
function Failure({error, kind, onLine}: {error: RunError; kind: "strategy" | "research"; onLine(file: string, line: number): void}) {
  const frames = error.frames ?? [];
  // A range that starts before the history reads the same here as in the data settings.
  const boundary = historyBoundary(error.code, error.details);
  return <div className="qx-failure" role="alert">
    {error.stage === "data" ? <>
      <strong>{t("Data could not be prepared")}</strong>
      {boundary !== null ? <HistoryStart boundary={boundary} /> : <p className="qx-failure-message">{error.message}</p>}
      {error.request && <p className="qx-muted">{t("Requested {request}", { request: dataText(error.request) })}</p>}
    </> : <>
      <strong>{error.file && error.line ? t.rich("Error in {location}", { location: <button type="button" className="qx-link" onClick={() => onLine(error.file!, error.line!)}>{t("{file} line {line}", { file: error.file, line: error.line })}</button> })
        : error.stage === "environment" ? t("The {kind} environment could not be prepared", { kind }) : kind === "strategy" ? t("The backtest failed") : t("The code failed")}</strong>
      <pre className="qx-failure-message">{error.message}</pre>
      {frames.length > 0 && <ol className="qx-frames">{frames.map((frame, index) => {const own = ownFile(frame); return <li key={index}>
        {own ? <button type="button" className="qx-link" onClick={() => onLine(own, frame.line)}>{own}:{frame.line}</button> : <span>{frame.path.split(/[\\/]/).at(-1)}:{frame.line}</span>}
        <span className="qx-muted">{t(" in {function}", { function: frame.function })}</span>{frame.text && <code>{frame.text}</code>}
      </li>;})}</ol>}
      {error.traceback && <TxDetails label={t("Full traceback")}><pre className="qx-log">{shortPaths(error.traceback)}</pre></TxDetails>}
    </>}
  </div>;
}

/** A finished, failed or stopped publication. Nothing reaches the library unless every step passed. */
function PublishOutcome({summary, error, onLibrary}: {summary: RunSummary; error: RunError | null; onLibrary(versionId: string): void}) {
  const publish = summary.publish!;
  if (summary.state === "completed") return <div className="qx-ended" data-state="completed">
    <strong>{t("Published {name} to the library", { name: publish.name })}</strong>
    <p>{publish.items.length > 1 ? t("{count} items, history filled in and checked for future data at {checks} points.", { count: publish.items.length, checks: publish.checks }) : t("history filled in and checked for future data at {checks} points.", { checks: publish.checks })}</p>
    {publish.versions.length > 0 && <TxButton size="sm" onClick={() => onLibrary(publish.versions[0])}><TxIcon name="bookmark" />{t("Open in library")}</TxButton>}
  </div>;
  if (summary.state === "failed" && error !== null && error.code === "future_data") return <FutureData error={error} name={publish.name} />;
  return <div className={summary.state === "failed" ? "qx-failure" : "qx-ended"} data-state={summary.state} role={summary.state === "failed" ? "alert" : undefined}>
    <strong>{summary.state === "failed" ? t("{name} was not published", { name: publish.name }) : summary.state === "cancelled" ? t("Publishing was cancelled") : t("Publishing was interrupted")}</strong>
    <p className="qx-failure-message">{summary.state === "failed" ? summary.message : t("Nothing was added to the library.")}</p>
    {error !== null && error.details && "failure" in error.details && <TxDetails label={t("Full traceback")}>
      <pre className="qx-log">{shortPaths(String((error.details.failure as {traceback?: string}).traceback ?? ""))}</pre></TxDetails>}
  </div>;
}

/** The publication used data from after a value's time: which symbol, which moment, and the two values that disagree. */
function FutureData({error, name}: {error: RunError; name: string}) {
  const details = error.details as {instrument: string; valueTime: number; cutoff: number; withAllData: number; whenCut: number | null; changed: number};
  const when = (value: number) => detailText(value).replace(" 00:00", "");
  return <div className="qx-failure" role="alert">
    <strong>{t("{name} was not published: it uses data from after the time each value is dated", { name })}</strong>
    <p>{t("The data was cut at {cutoff} and the code run again. A value dated before the cut came out differently, so it must have looked at later data.", { cutoff: when(details.cutoff) })}</p>
    <dl className="qx-failure-details">
      <div><dt>{t("Symbol")}</dt><dd>{details.instrument}</dd></div>
      <div><dt>{t("Value dated")}</dt><dd className="tx-num">{when(details.valueTime)}</dd></div>
      <div><dt>{t("With all the data")}</dt><dd className="tx-num">{formatNumber(details.withAllData, 6)}</dd></div>
      <div><dt>{t("With data up to {cutoff}", { cutoff: when(details.cutoff) })}</dt><dd className="tx-num">{details.whenCut === null ? t("No value") : formatNumber(details.whenCut, 6)}</dd></div>
      <div><dt>{t("Values that changed")}</dt><dd className="tx-num">{formatNumber(details.changed)}</dd></div>
    </dl>
    <p className="qx-muted">{t("Shift the factor so each value uses only bars that had closed by then, run again and publish that run.")}</p>
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
    <TxSegmented label={t("Stream")} value={stream} options={STREAMS.map(item => ({value: item.value, label: item.label}))} onChange={setStream} />
    {error ? <p role="alert" className="qx-error">{error}</p> : <pre className="qx-log">{text === null ? "" : text || t("Nothing was written.")}</pre>}
    {!next.eof && <TxButton size="sm" variant="quiet" onClick={() => void read(next.offset)}>{t("Read more")}</TxButton>}
  </div>;
}
