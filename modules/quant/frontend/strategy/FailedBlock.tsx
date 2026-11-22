/* A run that stopped: where in the code it broke (a click goes there), when in the backtest, and the values the code held. Missing prices list the names. */
import { t } from "../i18n";
import { useState } from "react";
import { TxFailure, TxIcon, TxRegionHead } from "@talyx/sdk";
import { mdy } from "../factor/format";
import type { Failure, StrategyRun } from "./api";

/** The values the code held where it broke, beside the part of the chart that ran. */
export function FailureLocals({run, onNotebook}: {run: StrategyRun; onNotebook?(): void}) {
  const failure = run.failure as Failure;
  return <div className="qx-locals">{failure.locals.length === 0
    ? <p className="fx-empty">{t("No frame of your code on the stack")}</p>
    : <><TxRegionHead title={t("At the failure")} sub={failure.function ? t("{function}, local values", { function: failure.function }) : t("local values")} /><table className="fx-g"><tbody>{failure.locals.map(([name, value]) => <tr key={name}><td className="l">{name}</td><td className="fx-vcell" title={value}>{value}</td></tr>)}</tbody></table></>}
    {onNotebook && <button type="button" className="tx-btn qx-locals-nb" data-variant="quiet" data-size="sm" onClick={onNotebook}><TxIcon name="book" />{t("Open in a notebook")}</button>}</div>;
}

/** A run that stopped before the backtest began says the step it stopped in, not the engine. */
const BEFORE: Record<string, string> = {check: t("Stopped at the checks before the run"), code: t("Stopped while reading the code"), data: t("Stopped while fetching prices"), environment: t("Stopped while preparing Python")};

/** The whole error output (the traceback, or the error's own words) opens under the failure. */
export function FailedBlock({run, onLine}: {run: StrategyRun; onLine(file: string | null, line: number): void}) {
  const [output, setOutput] = useState(false);
  const failure = run.failure as Failure;
  const inEngine = failure.inEngine || failure.line === null;
  const message = failure.error.split("\n")[0];
  const title = inEngine ? (BEFORE[failure.stage] ?? t("Stopped inside the engine")) : t("#{run} stopped at line {line}", { run: run.number, line: String(failure.line) });
  const more = failure.cause && failure.cause.trim() !== failure.error.trim();
  const when = failure.simulatedAt !== null ? t("Stopped at {date} {time} UTC, simulated", { date: mdy(failure.simulatedAt), time: new Date(failure.simulatedAt).toISOString().slice(11, 16) }) : "";
  const context = [when, failure.instrument ?? "", failure.tradesBefore !== null ? t("{count} trades before", { count: failure.tradesBefore }) : "",
    failure.equityBefore !== null ? t("equity {value}", { value: Math.round(failure.equityBefore).toLocaleString("en-US") }) : ""].filter(Boolean).join(" · ");
  return <div className="qx-failed">
    <TxFailure title={title} message={message} code
      location={inEngine ? undefined : {label: t("Go to line {line}", { line: failure.line ?? "" }), onOpen: () => onLine(failure.file, failure.line as number)}} context={context}
      actions={more ? <button type="button" className="tx-btn" data-variant="quiet" onClick={() => setOutput(value => !value)}>{output ? t("Hide the error output") : t("Show the error output")}</button> : undefined}
      trace={inEngine ? undefined : [{text: (failure.function ? t("File \"{file}\", line {line}, in {function}", { file: failure.file ?? "strategy.py", line: failure.line ?? "", function: failure.function }) : t("File \"{file}\", line {line}", { file: failure.file ?? "strategy.py", line: failure.line ?? "" })), mine: true}, ...(failure.sourceLine ? [{text: `    ${failure.sourceLine}`}] : []), {text: failure.error.split("\n")[0]}]} />
    {output && more && <pre className="qx-errout">{failure.cause}</pre>}
    {failure.withoutPrice && failure.withoutPrice.length > 0 && <div className="qx-noprice"><h4>{t("Names without a price that day")}</h4><p>{failure.withoutPrice.join(", ")}</p></div>}
  </div>;
}
