/* A run that stopped: where in the code it broke (a click goes there), when in the backtest, and the values the code held. Missing prices list the names. */
import { useState } from "react";
import { TxFailure, TxIcon, TxRegionHead } from "@talyx/sdk";
import { mdy } from "../factor/format";
import type { Failure, StrategyRun } from "./api";

/** The values the code held where it broke, beside the part of the chart that ran. */
export function FailureLocals({run, onNotebook}: {run: StrategyRun; onNotebook?(): void}) {
  const failure = run.failure as Failure;
  return <div className="qx-locals">{failure.locals.length === 0
    ? <p className="fx-empty">No frame of your code on the stack</p>
    : <><TxRegionHead title="At the failure" sub={failure.function ? `${failure.function}, local values` : "local values"} /><table className="fx-g"><tbody>{failure.locals.map(([name, value]) => <tr key={name}><td className="l">{name}</td><td className="fx-vcell" title={value}>{value}</td></tr>)}</tbody></table></>}
    {onNotebook && <button type="button" className="tx-btn qx-locals-nb" data-variant="quiet" data-size="sm" onClick={onNotebook}><TxIcon name="book" />Open in a notebook</button>}</div>;
}

/** A run that stopped before the backtest began says the step it stopped in, not the engine. */
const BEFORE: Record<string, string> = {check: "Stopped at the checks before the run", code: "Stopped while reading the code", data: "Stopped while fetching prices", environment: "Stopped while preparing Python"};

/** The whole error output (the traceback, or the error's own words) opens under the failure. */
export function FailedBlock({run, onLine}: {run: StrategyRun; onLine(file: string | null, line: number): void}) {
  const [output, setOutput] = useState(false);
  const failure = run.failure as Failure;
  const inEngine = failure.inEngine || failure.line === null;
  const message = failure.error.split("\n")[0];
  const title = inEngine ? (BEFORE[failure.stage] ?? "Stopped inside the engine") : `#${run.number} stopped at line ${failure.line}`;
  const more = failure.cause && failure.cause.trim() !== failure.error.trim();
  const when = failure.simulatedAt !== null ? `Stopped at ${mdy(failure.simulatedAt)} ${new Date(failure.simulatedAt).toISOString().slice(11, 16)} UTC, simulated` : "";
  const context = [when, failure.instrument ?? "", failure.tradesBefore !== null ? `${failure.tradesBefore} trades before` : "",
    failure.equityBefore !== null ? `equity ${Math.round(failure.equityBefore).toLocaleString("en-US")}` : ""].filter(Boolean).join(" · ");
  return <div className="qx-failed">
    <TxFailure title={title} message={message} code
      location={inEngine ? undefined : {label: `Go to line ${failure.line}`, onOpen: () => onLine(failure.file, failure.line as number)}} context={context}
      actions={more ? <button type="button" className="tx-btn" data-variant="quiet" onClick={() => setOutput(value => !value)}>{output ? "Hide the error output" : "Show the error output"}</button> : undefined}
      trace={inEngine ? undefined : [{text: `File "${failure.file ?? "strategy.py"}", line ${failure.line}${failure.function ? `, in ${failure.function}` : ""}`, mine: true}, ...(failure.sourceLine ? [{text: `    ${failure.sourceLine}`}] : []), {text: failure.error.split("\n")[0]}]} />
    {output && more && <pre className="qx-errout">{failure.cause}</pre>}
    {failure.withoutPrice && failure.withoutPrice.length > 0 && <div className="qx-noprice"><h4>Names without a price that day</h4><p>{failure.withoutPrice.join(", ")}</p></div>}
  </div>;
}
