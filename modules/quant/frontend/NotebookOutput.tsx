import { t } from "./i18n";
import { Fragment, memo, useContext, useEffect, useMemo, useRef, useState } from "react";
import MarkdownIt from "markdown-it";
import { TxButton, TxIcon, type Json } from "@talyx/sdk";
import { ansiRuns } from "./ansi";
import { mdy } from "./shared/format";
import type { FileCell, Output } from "./notebook";
import { OpenRunContext, RunStateContext } from "./notebook/openRun";
import { WidgetOutput } from "./notebook/widgets/WidgetOutput";
import { TableExplorer, type TableData } from "./notebook/TableExplorer";
import { JsonTree } from "./notebook/JsonTree";
import { Plot, type PlotFigure } from "./chart/Plot";

/** Raw HTML in Markdown is shown as text, as an untrusted cell's would be; links and tables work. */
const markdown = new MarkdownIt({html: false, linkify: true});
const renderImage = markdown.renderer.rules.image!;
markdown.renderer.rules.image = (tokens, index, options, env, renderer) => {
  const token = tokens[index], src = token.attrGet("src");
  const attachments = env?.attachments as FileCell["attachments"];
  if (typeof src === "string" && src.startsWith("attachment:") && attachments) {
    const bundle = attachments[markdown.utils.lib.mdurl.decode(src.slice("attachment:".length))];
    for (const mime of ["image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml"]) {
      const value = bundle?.[mime];
      const text = Array.isArray(value) ? value.join("") : value;
      if (typeof text !== "string") continue;
      token.attrSet("src", mime === "image/svg+xml" ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(text)}` : `data:${mime};base64,${text.replace(/\s/g, "")}`);
      break;
    }
  }
  return renderImage(tokens, index, options, env, renderer);
};
export const renderMarkdown = (text: string, attachments?: FileCell["attachments"]) => markdown.render(text, {attachments});

/** The richest form an output carries that this panel draws, in Jupyter's order. Widgets show their plain text. */
const ORDER = ["application/vnd.talyx.runsummary+json", "application/vnd.talyx.backtest+json", "application/vnd.talyx.report+json", "application/vnd.talyx.table+json", "application/vnd.plotly.v1+json", "text/html", "image/png", "image/jpeg", "image/svg+xml", "text/markdown", "text/latex", "application/json", "text/plain"];
export function chooseMime(data: Record<string, Json>): string | null {
  if ("application/vnd.jupyter.widget-view+json" in data) return "application/vnd.jupyter.widget-view+json";
  return ORDER.find(mime => mime in data) ?? null;
}

const LONG_LINES = 400, HEAD_LINES = 60, TAIL_LINES = 200;

/** Terminal text with its colours. Very long text shows its start and end, with the rest a click away; the page is the only scroll. */
export const AnsiText = memo(function AnsiText({text, tone}: {text: string; tone?: "stderr" | "error"}) {
  const [full, setFull] = useState(false);
  const shown = text.endsWith("\r") ? text.slice(0, -1) : text;
  const lines = shown.split("\n");
  const long = !full && lines.length > LONG_LINES;
  const parts = long ? [lines.slice(0, HEAD_LINES).join("\n"), lines.slice(-TAIL_LINES).join("\n")] : [shown];
  return <div className="qx-nb-text" data-tone={tone}>
    <pre>{colored(parts[0])}</pre>
    {long && <><div className="qx-nb-more"><TxButton size="sm" variant="quiet" onClick={() => setFull(true)}>{t("Show all {linesCount} lines", { linesCount: lines.length.toLocaleString("en-US") })}</TxButton>
      <span className="qx-muted">{t("{linesCount} lines hidden", { linesCount: (lines.length - HEAD_LINES - TAIL_LINES).toLocaleString("en-US") })}</span></div>
      <pre>{colored(parts[1])}</pre></>}
  </div>;
});

function colored(text: string) {
  return ansiRuns(text).map((run, index) => {
    if (run.fg === null && run.bg === null && !run.bold && !run.dim && !run.italic && !run.underline) return run.text;
    const color = (value: string | null) => value === null ? undefined : /^\d+$/.test(value) ? `var(--qx-ansi-${value})` : value;
    return <span key={index} style={{color: color(run.fg), background: color(run.bg), fontWeight: run.bold ? "var(--tx-weight-strong)" : undefined,
      opacity: run.dim ? .7 : undefined, fontStyle: run.italic ? "italic" : undefined, textDecoration: run.underline ? "underline" : undefined}}>{run.text}</span>;
  });
}

/** One output under a cell. */
export const NotebookOutput = memo(function NotebookOutput({output, onStop, live = true}: {output: Output; onStop?: () => void; live?: boolean}) {
  if (output.output_type === "stream") return <AnsiText text={output.text} tone={output.name === "stderr" ? "stderr" : undefined} />;
  if (output.output_type === "error")
    return <AnsiText text={output.traceback.length ? output.traceback.join("\n") : `${output.ename}: ${output.evalue}`} tone="error" />;
  const mime = chooseMime(output.data);
  if (mime === null) return <p className="qx-muted">{t("Output of type {type} is not shown here.", { type: Object.keys(output.data).join(", ") })}</p>;
  const value = output.data[mime];
  const text = (item: Json) => Array.isArray(item) ? item.join("") : String(item);
  // A progress line the kernel emptied when its work ended (q.data's "Reading bars: 2 of 3 names") leaves nothing behind.
  if (mime === "text/plain" && output.output_type === "display_data" && text(value) === "") return null;
  const size = (output.metadata[mime] ?? {}) as {width?: number; height?: number};
  switch (mime) {
  case "application/vnd.jupyter.widget-view+json": return <WidgetOutput modelId={String((value as {model_id?: string}).model_id)} />;
  case "application/vnd.talyx.runsummary+json": return <RunSummary summary={value as unknown as RunSummaryData} />;
  case "application/vnd.talyx.backtest+json": return <BacktestProgress run={value as unknown as BacktestRun} onStop={onStop} live={live} />;
  case "application/vnd.talyx.report+json": return <ReportOutput report={value as unknown as Report} />;
  case "application/vnd.talyx.table+json": return <TableExplorer table={value as unknown as TableData} />;
  case "application/vnd.plotly.v1+json": return <PlotlyOutput figure={value as unknown as PlotFigure} />;
  case "text/html": return <HtmlFrame html={text(value)} />;
  case "image/png": case "image/jpeg":
    return <img className="qx-nb-image" alt={t("Output")} src={`data:${mime};base64,${text(value).replace(/\s/g, "")}`} width={size.width} height={size.height} />;
  case "image/svg+xml": return <img className="qx-nb-image" alt={t("Output")} src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(text(value))}`} />;
  case "text/markdown": return <div className="qx-nb-markdown" dangerouslySetInnerHTML={{__html: renderMarkdown(text(value))}} />;
  case "application/json": return <JsonTree value={value} />;
  default: return <AnsiText text={text(value)} />;
  }
});

interface RunSummaryData {label?: string; title: string; meta: string; rows: {name: string; code: string; text: string}[]; failure?: string}

/** What `q.run` shows: which run, then each attribute with what it holds. */
function RunSummary({summary}: {summary: RunSummaryData}) {
  return <div className="qv-notebook-objsum">
    <span>{summary.label ?? t.c("run record", "Run")}</span><span><b>{summary.title}</b> <span className="qv-notebook-dim">{summary.meta}</span></span>
    {summary.rows.map(row => <Fragment key={row.name}><span>{row.name}</span><span>{row.code !== "" && <code>{row.code}</code>} {row.text}</span></Fragment>)}
    {summary.failure !== undefined && <><span>{t("Failure")}</span><span>{summary.failure}</span></>}
  </div>;
}

export interface BacktestRun {
  strategy: string; steps: {name: string; done: boolean; now?: boolean}[]; state: string; value: number | null; elapsed: number; left: number | null; runId: string; number: number | null;
  equity?: Json; failure?: string; objectId?: string | null; stats?: Record<string, number>; ranFor?: number; signal?: string; since?: string | number | null; until?: string | number | null;
}

/** A plain number with the true minus when it is below zero (a Sharpe of −0.76). */
export const plain = (value: number, digits = 2) => { const text = value.toFixed(digits); return /^-0(\.0+)?$/.test(text) ? text.slice(1) : text.replace(/^-/, "\u2212"); };
const percent = (value: number, digits = 1) => `${value < 0 ? "−" : ""}${Math.abs(value * 100).toFixed(digits)}%`;
/** The numbers a finished backtest block shows, in the draft's band: only those the run has. */
function backtestStats(stats: Record<string, number>): {label: string; value: string; tone?: "up" | "down"}[] {
  const found: {label: string; value: string; tone?: "up" | "down"}[] = [];
  if (stats.cagr !== undefined) found.push({label: "CAGR", value: percent(stats.cagr), tone: stats.cagr < 0 ? "down" : "up"});
  if (stats.sharpe !== undefined) found.push({label: t("Sharpe"), value: plain(stats.sharpe)});
  if (stats.maxDrawdown !== undefined) found.push({label: t("Max drawdown"), value: percent(-Math.abs(stats.maxDrawdown)), tone: "down"});
  if (stats.costsAYear !== undefined) found.push({label: t("Costs a year"), value: percent(-Math.abs(stats.costsAYear)), tone: "down"});
  return found;
}

/** A one-line backtest while it runs and when it is over (the draft's block): title line, the steps, the progress bar with the time left, and Stop. */
export function BacktestProgress({run, onStop, live = true}: {run: BacktestRun; onStop?: () => void; live?: boolean}) {
  const running = !["completed", "failed", "cancelled", "stopped"].includes(run.state);
  const open = useContext(OpenRunContext);
  const readRun = useContext(RunStateContext);
  // Closed while it ran: the cell no longer updates this block, so it says how the backtest ended (it kept running in the Host).
  const [ended, setEnded] = useState<{state: string; objectId: string} | null>(null);
  const [unread, setUnread] = useState<string | null>(null);   // why how it ended could not be read
  const left = running && !live;
  useEffect(() => {
    if (!left || readRun === null) return;
    let gone = false;
    void readRun(run.runId).then(found => { if (!gone && ["completed", "failed", "cancelled", "stopped", "interrupted"].includes(found.state)) setEnded(found); }, reason => { if (!gone) setUnread(reason instanceof Error ? reason.message : String(reason)); });
    return () => { gone = true; };
  }, [left, readRun, run.runId]);
  if (left && unread !== null) return <div className="qv-notebook-meta"><span>{t.rich("How run {number} of {strategy} ended could not be read: {unread}", { number: <b>#{run.number}</b>, strategy: <b>{run.strategy}</b>, unread: unread })}</span>
    {open !== null && run.objectId && <TxButton variant="quiet" size="sm" onClick={() => open(run.objectId!, run.runId)}>{t.c("action", "Open")}</TxButton>}</div>;
  if (left && ended !== null) return <div className="qv-notebook-meta"><span>{ended.state === "completed"
    ? t.rich("The backtest finished as run {number} of {strategy}", { number: <b>#{run.number}</b>, strategy: <b>{run.strategy}</b> })
    : t.rich("The backtest was {state} (run {number} of {strategy})", { state: t(ended.state), number: <b>#{run.number}</b>, strategy: <b>{run.strategy}</b> })}</span>
    {open !== null && <TxButton variant="quiet" size="sm" onClick={() => open(ended.objectId, run.runId)}>{t.c("action", "Open")}</TxButton>}</div>;
  const stats = run.state === "completed" && run.stats !== undefined ? backtestStats(run.stats) : [];
  const finished = run.state === "completed";
  // The draft's second span: the data's first month and its last day, "Jan 2016 – Nov 3, 2026 · data to Nov 3".
  const day = (value: string | number | null | undefined) => { if (value === null || value === undefined || value === "") return null; const date = typeof value === "number" ? new Date(value) : new Date(`${value.slice(0, 10)}T00:00:00Z`); return Number.isNaN(date.getTime()) ? null : date; };
  const first = day(run.since), last = day(run.until);
  const span = first === null || last === null ? null : t("{start} – {end} · data to {last}", { start: mdy(first).replace(/ \d+,/, ""), end: mdy(last), last: mdy(last).replace(/, \d+$/, "") });
  return <div className="qv-notebook-bt">
    <div className="qv-notebook-meta">{running && <span className="tx-spin" />}{finished
      ? <><span>{run.signal !== undefined ? t.rich("{strategy} with {signal} as its signal", { strategy: run.strategy, signal: <code>{run.signal}</code> }) : run.strategy}</span>{span !== null && <span>{span}</span>}</>
      : <span>{run.signal !== undefined
          ? t.rich("Backtest of {strategy} with {signal} as its signal, in the strategy environment", { strategy: <b>{run.strategy}</b>, signal: <code>{run.signal}</code> })
          : t.rich("Backtest of {strategy}, in the strategy environment", { strategy: <b>{run.strategy}</b> })}
          {!running && run.number !== null && t.rich(", run {number}", { number: <b>#{run.number}</b> })}</span>}<span className="tx-spacer" />
      {run.state === "completed" && run.ranFor !== undefined && <span>{t("ran {ranFor} s", { ranFor: run.ranFor })}</span>}
      {run.state === "completed" && open !== null && run.objectId && <TxButton variant="quiet" size="sm" onClick={() => open(run.objectId!, run.runId)}>{t("Open as a run of the strategy")}</TxButton>}
      {running && onStop && <TxButton variant="quiet" size="sm" onClick={onStop}><TxIcon name="stop" />{t.c("action", "Stop")}</TxButton>}</div>
    {stats.length > 0 && <div className="qv-notebook-stats">{stats.map(item => <div key={item.label}><div className="qv-notebook-statl">{item.label}</div><div className="qv-notebook-statv" data-tone={item.tone}>{item.value}</div></div>)}</div>}
    {run.failure !== undefined && <div className="qv-notebook-failure" role="alert">{run.failure}</div>}
    {!finished && <div className="qv-notebook-steps">{run.steps.map(step => <span key={step.name} data-done={step.done ? "" : undefined} data-now={step.now && !step.done ? "" : undefined}>{step.done && <TxIcon name="check" />}{step.name}</span>)}</div>}
    {running && <div className="qv-notebook-progress"><span className="qv-notebook-bar2"><i style={{width: `${Math.round((run.value ?? 0) * 100)}%`}} /></span><b>{Math.round((run.value ?? 0) * 100)}%</b>
      <span className="qv-notebook-dim">{run.elapsed} s{run.left === null ? "" : t(", about {left} s left", { left: run.left })}</span></div>}
    {run.equity !== undefined && <><div className="qv-notebook-meta"><span>{t("Equity so far")}</span></div><PlotlyOutput figure={run.equity as unknown as PlotFigure} /></>}
  </div>;
}

export interface Report {
  sections: number; metrics: Record<string, Json>; tables: {title?: string; columns: string[]; rows: Json[][]}[]; figures: {title?: string; figure: Json}[]; moreFigures: number; dropped?: string;
}

/** A standard report in a cell: the key numbers, a few tables and the first figures (the report's own Plotly figures, drawn as everywhere). */
export function ReportOutput({report}: {report: Report}) {
  const numbers = Object.entries(report.metrics).filter(([, value]) => typeof value === "number" || typeof value === "string");
  const more = report.moreFigures + Math.max(0, report.sections - report.figures.length - report.tables.length);
  return <div className="qx-nb-report">
    {numbers.length > 0 && <table className="qx-nb-reporttable"><tbody>{numbers.slice(0, 12).map(([key, value]) => <tr key={key}><td>{key}</td><td>{typeof value === "number" ? Number(value.toPrecision(5)) : String(value)}</td></tr>)}</tbody></table>}
    {report.tables.map((table, index) => <table key={index} className="qx-nb-reporttable">
      <thead><tr>{table.columns.map(column => <th key={column}>{column}</th>)}</tr></thead>
      <tbody>{table.rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}>{typeof cell === "number" ? Number(cell.toPrecision(5)) : String(cell ?? "")}</td>)}</tr>)}</tbody></table>)}
    {report.figures.map((item, index) => <PlotlyOutput key={index} figure={item.figure as unknown as PlotFigure} />)}
    {report.dropped !== undefined && <p className="qx-muted">{report.dropped}</p>}
    {more > 0 && <p className="qx-muted">{t("{more} more sections", { more })}</p>}
  </div>;
}

function PlotlyOutput({figure}: {figure: PlotFigure}) {
  const title = figure.layout === undefined ? undefined : figure.layout.title;
  const label = typeof title === "string" ? title : title !== null && typeof title === "object" && "text" in title ? String((title as {text: unknown}).text) : t("Plot");
  return <div className="qx-nb-plot"><Plot figure={figure} ariaLabel={label} keepTitle /></div>;
}

/**
 * HTML a cell displayed (a table, a widget's page) in an isolated frame: scripts run, but with no access to TALYX.
 * The frame takes the height of its content, so the notebook stays the one scrolling area.
 */
function HtmlFrame({html}: {html: string}) {
  const frame = useRef<HTMLIFrameElement>(null);
  const host = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState(40);
  const [theme, setTheme] = useState<string | null>(null);
  useEffect(() => {
    const css = getComputedStyle(host.current!);
    const token = (name: string) => css.getPropertyValue(name).trim();
    setTheme(`html{color-scheme:dark}body{margin:0;padding:2px 0;background:transparent;color:${token("--tx-text-2")};font:12px/1.5 ${token("--tx-font")};overflow-x:auto;overflow-y:hidden}
table{border:0;border-collapse:collapse;font-family:${token("--tx-font-mono")};font-size:11.5px;color:${token("--tx-text")}}
th,td{padding:3px 10px;text-align:right;border:0;border-bottom:1px solid ${token("--tx-line-2")};white-space:nowrap;vertical-align:top}
th{color:${token("--tx-text-3")};font-weight:500}thead th{border-bottom-color:${token("--tx-line-3")}}tbody tr:hover{background:${token("--tx-white-04")}}
a{color:${token("--tx-ember-text")}}pre,code{font-family:${token("--tx-font-mono")}}`);
  }, []);
  const content = useMemo(() => theme === null ? null : `<!doctype html><html><head><meta charset="utf-8"><style>${theme}</style></head><body>${html}<script>(function(){
    let last=0,queued=false;
    function measure(){queued=false;const h=Math.ceil(document.documentElement.scrollHeight);if(h!==last){last=h;parent.postMessage({type:'talyx-output-size',height:h},'*')}}
    function queue(){if(!queued){queued=true;requestAnimationFrame(measure)}}
    new ResizeObserver(queue).observe(document.documentElement);window.addEventListener('load',queue);queue();
  })();</script></body></html>`, [html, theme]);
  useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (frame.current === null || event.source !== frame.current.contentWindow || event.data === null || typeof event.data !== "object" || event.data.type !== "talyx-output-size") return;
      const next = Number(event.data.height);
      if (Number.isFinite(next) && next > 0) setHeight(Math.min(30000, next));
    };
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, []);
  return <div ref={host} className="qx-nb-html">{content !== null &&
    <iframe ref={frame} title={t("Output")} srcDoc={content} sandbox="allow-scripts allow-downloads" style={{height}} />}</div>;
}
