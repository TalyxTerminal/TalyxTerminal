/* One run's Results, a tab at a time: Windows, Returns, Information, Turnover, Inputs, Robustness, Values and Settings. Every figure is the backend's Plotly figure shown
   by Plot, taken by its key; one that could not be drawn says why in its place. Tables are read from the run's results. */
import { useEffect, useState, type ReactNode } from "react";
import { TxButton, TxCompare, TxFailure, TxIcon, TxRegionHead, TxRunPicker, TxTabs, TxTag, TxSkeleton, type TxMenuItem } from "@talyx/sdk";
import type { ModelApi, ModelResultsReply, ModelRun } from "../api";
import { algoLabel, day, dayYear, minutes, month, pct, plainPct, sgn } from "../format";
import { differences } from "../Experiments";
import { ValuesTab } from "./ValuesTab";
import { Plot, type PlotFigure } from "../../chart/Plot";

import { RunPrice } from "../../shared/RunPrice";

export type ResultsTab = "windows" | "returns" | "information" | "turnover" | "inputs" | "robustness" | "values" | "price" | "settings";
export const TABS: {value: ResultsTab; label: string}[] = [
  {value: "windows", label: "Windows"}, {value: "returns", label: "Returns"}, {value: "information", label: "Information"}, {value: "turnover", label: "Turnover"},
  {value: "inputs", label: "Inputs"}, {value: "robustness", label: "Robustness"}, {value: "values", label: "Values"}, {value: "price", label: "On price"}, {value: "settings", label: "Settings"},
];
type Figures = Record<string, (PlotFigure & {missing?: string}) | undefined>;
const FIGURES: Record<string, string[]> = {
  windows: ["model.windows.each", "model.windows.valid-test", "model.windows.importance", "model.windows.learning"],
  returns: ["model.returns.groups", "model.returns.ls-cumulative", "model.returns.group-mean", "model.returns.top-excess", "model.returns.ls-drawdown", "model.returns.top-rank", "model.returns.net-by-cost", "model.returns.portfolio"],
  information: ["model.information.ic", "model.information.monthly", "model.information.distribution", "model.information.qq", "model.information.decay", "model.information.industry"],
  turnover: ["model.turnover.top-bottom", "model.turnover.autocorr"],
  inputs: ["model.inputs.baselines", "model.inputs.library", "model.inputs.styles", "model.inputs.industry", "model.inputs.destyled"],
  robustness: ["model.robustness.years", "model.robustness.size", "model.robustness.coverage", "model.robustness.held-out"],
};
/** A figure with the sign's colour: gains and losses follow the user's up and down colours. */
const up = (value: number | null | undefined, text: string) => value == null || value === 0 ? text : <span className={value > 0 ? "tx-up" : "tx-down"}>{text}</span>;
const down = (text: string) => <span className="tx-down">{text}</span>;
const num = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) ? value : null;

function Sec({title, sub, right, children}: {title: string; sub?: string; right?: ReactNode; children: ReactNode}) {
  return <section className="mx-rsec"><TxRegionHead title={title} sub={sub}>{right}</TxRegionHead>{children}</section>;
}
/** The key of a chart, in the head of its section: a swatch and a name for each line the chart draws (the lines of one group of names are one entry). */
export function Legend({figure}: {figure: (PlotFigure & {missing?: string}) | undefined}) {
  const traces = ((figure?.data ?? []) as {name?: string; meta?: {role?: string}; line?: {dash?: string}}[]).filter(item => item.name && item.meta?.role !== "detail");
  if (!traces.length) return null;
  return <span className="mx-legend">{traces.map((item, index) => <span key={index}><i data-role={item.meta?.role} data-dash={item.line?.dash ?? undefined} />{item.name}</span>)}</span>;
}
function Fig({id, figures, missing, label}: {id: string; figures: Figures | null; missing: Record<string, string>; label: string}) {
  const found = figures?.[id];
  if (figures === null) return <div className="mx-fig" />;
  if (!found) return <div className="mx-why"><TxIcon name="alert" /><span>This figure was not drawn: {missing[id] ?? "no data for it"}</span></div>;
  if (found.missing) return <div className="mx-why"><TxIcon name="alert" /><span>{found.missing}</span></div>;
  return <div className="mx-fig"><Plot figure={found} ariaLabel={label} /></div>;
}
function Table({head, rows}: {head: string[]; rows: ReactNode[][]}) {
  return <table className="mx-rtable"><thead><tr>{head.map((cell, index) => <th key={index} data-align={index ? undefined : "left"}>{cell}</th>)}</tr></thead>
    <tbody>{rows.map((row, r) => <tr key={r}>{row.map((cell, c) => <td key={c} data-align={c ? undefined : "left"}>{cell}</td>)}</tr>)}</tbody></table>;
}

export function Results({mapi, run, runs, tab, vs, onTab, onVs, onPublish, onSelect, onStar, error, onHeldOut, onUseSettings}: {
  mapi: ModelApi; run: ModelRun; runs: ModelRun[]; tab: ResultsTab; vs: string | null; onTab(next: ResultsTab): void; onVs(id: string | null): void; onPublish(run: ModelRun): void;
  onSelect(id: string): void; onStar(run: ModelRun): void; error(reason: unknown): void; onHeldOut(): void; onUseSettings(): void;
}) {
  const [picked, setPicked] = useState<{run: string; symbol: string} | null>(null);
  const [reply, setReply] = useState<ModelResultsReply | null>(null);
  const [figures, setFigures] = useState<Figures | null>(null);
  const done = run.status === "completed";
  useEffect(() => {
    let live = true;
    setReply(null);
    if (!done) return;
    void mapi.results(run.id, vs ?? undefined).then(next => {if (live) setReply(next);}, error);
    return () => {live = false;};
  }, [run.id, run.status, vs, run.heldOut?.state, run.portfolio]);
  useEffect(() => {
    let live = true;
    setFigures(null);
    const keys = FIGURES[tab];
    if (!keys || !done) return;
    void mapi.figures(run.id, keys, vs ?? undefined).then(next => {if (live) setFigures(next.figures);}, error);
    return () => {live = false;};
  }, [run.id, tab, vs, run.status, run.heldOut?.state, run.portfolio]);
  const results = reply?.results ?? {};
  const missing: Record<string, string> = Object.fromEntries(((results.missing ?? []) as {key: string; reason: string}[]).map(item => [item.key, item.reason]));
  const other = vs ? runs.find(item => item.id === vs) ?? null : null;
  const compareItems: TxMenuItem[] = [{id: "none", label: "No comparison", onSelect: () => onVs(null)}, ...runs.filter(item => item.id !== run.id && item.status === "completed")
    .map(item => ({id: item.id, label: `#${item.number} · ${algoLabel(item.algo)} · ${item.label}`, onSelect: () => onVs(item.id)}))];
  const fig = (id: string, label: string) => <Fig id={id} figures={figures} missing={missing} label={label} />;
  // the keys of a chart sit in its section head, drawn by the shared chart layer; a second row of them here would say the same twice
  const legend = (_id: string): ReactNode => null;
  const runItems: TxMenuItem[] = runs.filter(item => item.status === "completed" || item.id === run.id).map(item => ({id: item.id, label: `#${item.number} · ${algoLabel(item.algo)} · ${item.label}`, onSelect: () => onSelect(item.id)}));
  const what = `${algoLabel(run.algo)}${differences(run, null) ? `, ${differences(run, null)}` : ""}, ${run.horizon ?? ""}D`;
  const head = <div className="mx-rhead"><TxRunPicker run={`#${run.number}`} items={runItems} /><span className="mx-nw">{what}</span><span className="mx-t3 mx-nw">{day(run.createdAt)}{run.time ? ` · ${minutes(run.time)}` : ""}</span>
    <span className="mx-t3 mx-nw">{run.dataFrom && run.dataTo ? `${month(run.dataFrom)} – ${month(run.dataTo)}` : ""}</span><span className="mx-t3 mx-nw">{run.dataTo ? `data to ${dayYear(run.dataTo)}` : ""}</span>
    <TxCompare run={other ? `#${other.number}` : null} items={compareItems} />{run.status === "running" && <TxTag tone="muted">Running</TxTag>}{run.heldOut && <TxTag tone="muted">Held-out</TxTag>}
    <span className="tx-spacer" /><button type="button" className="mx-btn" data-on={run.starred ? "" : undefined} onClick={() => onStar(run)}><TxIcon name="star" />Star</button>{done && <TxButton size="sm" variant="primary" onClick={() => onPublish(run)}>Publish</TxButton>}</div>;
  const body = () => {
    if (!done) return <div className="mx-none">{run.status === "failed" && run.failure ? <TxFailure title={run.failure.title} message={run.failure.detail ?? run.failure.message} /> : run.status === "running" ? "Results appear when the run has finished its windows." : "No results yet."}</div>;
    if (tab === "price") return <RunPrice key={run.id} runId={run.id} initialSymbol={picked?.run === run.id ? picked.symbol : undefined} />;
    if (reply === null) return <div className="mx-skel" aria-busy="true"><TxSkeleton width="45%" /><TxSkeleton width="70%" /><TxSkeleton className="mx-skel-plot" /></div>;
    switch (tab) {
      case "windows": return <WindowsTab run={run} results={results} fig={fig} legend={legend} has={id => figures === null || Boolean(figures[id])} />;
      case "returns": return <ReturnsTab run={run} results={results} fig={fig} legend={legend} onRunBacktest={() => void mapi.portfolio(run.id).catch(error)} />;
      case "information": return <InformationTab run={run} results={results} fig={fig} legend={legend} />;
      case "turnover": return <TurnoverTab run={run} results={results} fig={fig} legend={legend} />;
      case "inputs": return <InputsTab run={run} results={results} fig={fig} legend={legend} />;
      case "robustness": return <RobustnessTab run={run} results={results} fig={fig} legend={legend} reply={reply} onHeldOut={onHeldOut} />;
      case "values": return <ValuesTab mapi={mapi} run={run} error={error} onPrice={symbol => {setPicked({run: run.id, symbol}); onTab("price");}} />;
      default: return <SettingsTab run={run} reply={reply} other={other} mapi={mapi} onUse={onUseSettings} />;
    }
  };
  return <div className="mx-results"><div className="mx-rtop">{head}<TxTabs value={tab} options={TABS} onChange={onTab} /></div><div className="mx-rbody">{body()}</div></div>;
}

type FigFn = (id: string, label: string) => ReactNode;
function WindowsTab({run, results, fig, legend, has}: {run: ModelRun; results: Record<string, any>; fig: FigFn; legend: (id: string) => ReactNode; has(id: string): boolean}) {
  const rows = (results.windows?.table ?? []) as {k: number; test: {from: number; to: number}; trainIc: number; validIc: number; testIc: number; best: number | null; rows: number; secs: number}[];
  const deepIds = new Set(["mlp", "lstm", "gru", "alstm", "transformer", "localformer", "tcn", "tabnet", "sfm", "krnn", "sandwich", "gats"]);
  const importance = results.windows?.importance;
  const algorithm = run.algo;
  const what = algorithm === "lightgbm" ? "share of split gain" : algorithm === "ridge" || algorithm === "lasso" || algorithm === "linear" ? "share of |coefficient| on ranked inputs" : algorithm && deepIds.has(algorithm) ? "share of the fall in validation Rank IC when an input is shuffled within each day (permutation)" : "share of importance";
  // An algorithm that trains on its validation rows keeps no validation IC, and one fitted in one step no rounds: those parts are left out, not shown empty.
  const validated = rows.length === 0 || rows.some(w => num(w.validIc) !== null);
  const rounds = rows.some(w => w.best != null);
  const curves = has("model.windows.learning") || rounds;
  const features = (importance?.features ?? []) as string[];
  const extra = [
    validated && <Sec key="vt" title="Validation against test" sub="each dot a window; under the line the test was worse">{fig("model.windows.valid-test", "Validation against test")}</Sec>,
    curves && <Sec key="lc" title="Learning curves" sub={`last 4 windows, validation loss relative to ${deepIds.has(String(algorithm)) ? "epoch" : "round"} 0; dot is where it stopped`}>{fig("model.windows.learning", "Learning curves")}</Sec>,
  ].filter(Boolean);
  return <>
    <div className="mx-two"><Sec title="Each window" sub={validated ? "validation and test Rank IC; dotted, the test mean" : "test Rank IC; dotted, the mean"} right={legend("model.windows.each")}>{fig("model.windows.each", "Rank IC by window")}</Sec>
      <Sec title="Windows" sub="Rank IC in each part">{rows.length === 0 ? <div className="mx-none">This run's code keeps no windows (no q.record); numbers are for the whole period.</div>
        : <div className="mx-scroll"><Table head={["#", "Test period", "Train", ...(validated ? ["Valid"] : []), "Test", ...(rounds ? ["Round"] : []), "Rows", "Time"]} rows={rows.map(w => [<span className="mx-t2">{w.k}</span>, w.test ? `${month(w.test.from)} – ${month(w.test.to)}` : "",
          sgn(num(w.trainIc), 4), ...(validated ? [up(num(w.validIc), sgn(num(w.validIc), 4))] : []), up(num(w.testIc), sgn(num(w.testIc), 4)), ...(rounds ? [w.best ?? ""] : []), `${Math.round((w.rows ?? 0) / 1000)}k`, minutes(w.secs)])} /></div>}</Sec></div>
    {extra.length > 0 && <div className={extra.length > 1 ? "mx-two" : undefined}>{extra}</div>}
    <Sec title="Importance by window" sub={features.length >= 20 ? `${what}; the 20 inputs with the most on average` : what}>{importance ? <div className="mx-fig" style={{height: Math.max(features.length, 3) * 22 + 40}}>{fig("model.windows.importance", "Importance by window")}</div> : fig("model.windows.importance", "Importance by window")}</Sec>
  </>;
}
function ReturnsTab({run, results, fig, legend, onRunBacktest}: {run: ModelRun; results: Record<string, any>; fig: FigFn; legend: (id: string) => ReactNode; onRunBacktest(): void}) {
  const returns = results.returns ?? {};
  const rows = (returns.longShort?.rows ?? []) as {name: string; annual: number; sharpe: number; maxDrawdown: number; alpha?: number; beta?: number}[];
  const top = returns.topN ?? {};
  const days = run.horizon ?? 5;
  const rebalance = (returns.rebalance ?? []) as {days: number; lsNet: number; gross: number; costs: number; turnover: number; sharpe: number; breakeven: number | null}[];
  const perBar = num(returns.groupSpreadPerBar);
  const spread = perBar === null ? null : perBar * 1e4;
  const portfolio = returns.portfolio as {state: string; message?: string; risk?: Record<string, Record<string, number>>} | undefined;
  return <>
    <Sec title="Cumulative return by group" sub={`${days}-day rebalance, relative to all names, net long–short in white`} right={legend("model.returns.groups")}>{fig("model.returns.groups", "Cumulative return by group")}</Sec>
    <div className="mx-two">
      <Sec title="Long–short and long–average" sub={`groups by prediction, ${days}-day rebalance; average is all names`}><Table head={["", "Annual", "Sharpe", "Max DD", "Alpha", "Beta"]} rows={rows.map(row => [row.name, up(num(row.annual), pct(num(row.annual), 2)), sgn(num(row.sharpe), 2), down(pct(num(row.maxDrawdown))),
        num(row.alpha) === null ? "" : up(num(row.alpha), pct(num(row.alpha))), num(row.beta) === null ? "" : sgn(num(row.beta), 2)])} /></Sec>
      <Sec title={`Top ${top.n ?? 50} against the average`} sub={`excess return a day; cost ${returns.costBps ?? ""} bps a trade`}>{top.before && <Table head={["", "Mean, bps", "Std, bps", "Annualized", "Info. ratio", "Max DD"]} rows={(["before", "after"] as const).map(key => [key === "before" ? "Before cost" : "After cost",
        sgn(num(top[key].meanBps), 2), num(top[key].stdBps)?.toFixed(1) ?? "—", up(num(top[key].annual), pct(num(top[key].annual), 2)), sgn(num(top[key].ir), 2), down(pct(num(top[key].maxDrawdown)))])} />}</Sec></div>
    <Sec title="Cumulative long–short and long–average" sub="gross, net long–short in white" right={legend("model.returns.ls-cumulative")}>{fig("model.returns.ls-cumulative", "Cumulative long–short and long–average")}</Sec>
    <div className="mx-two"><Sec title="Mean return by group" sub={`relative to all names, bps a bar over ${days} bars${spread !== null ? `; spread ${sgn(spread, 2)} bps` : ""}`}>{fig("model.returns.group-mean", "Mean return by group")}</Sec>
      <Sec title={`Top ${top.n ?? 50}, excess over the average`} sub="cumulative, before and after cost" right={legend("model.returns.top-excess")}>{fig("model.returns.top-excess", "Top N excess over the average")}</Sec></div>
    <div className="mx-two"><Sec title="Drawdown" sub="net of cost" right={legend("model.returns.ls-drawdown")}>{fig("model.returns.ls-drawdown", "Drawdown")}</Sec>
      <Sec title={`Rank of the top and bottom ${top.n ?? 50}`} sub={`realised ${days}-day percentile, 3-month mean`} right={legend("model.returns.top-rank")}>{fig("model.returns.top-rank", "Rank of the top and bottom")}</Sec></div>
    <div className="mx-two"><Sec title="Rebalance every" sub="same predictions, cost per trade on both legs"><Table head={["Every", "Gross", "Turnover", "Costs", "Net", "Sharpe", "Break-even"]}
      rows={rebalance.map(row => [`${row.days}D`, up(num(row.gross), pct(num(row.gross), 2)), plainPct(num(row.turnover), 1), down(pct(num(row.costs) === null ? null : -(row.costs), 2)), up(num(row.lsNet), pct(num(row.lsNet), 2)), sgn(num(row.sharpe), 2), row.breakeven === null ? "none" : row.breakeven.toFixed(1)])} /></Sec>
      <Sec title="Net by cost per trade" sub="long–short, a year">{fig("model.returns.net-by-cost", "Net by cost per trade")}</Sec></div>
    <Sec title="Portfolio backtest" sub="Qlib: top 50, drop 5 a day, after cost; on request" right={legend("model.returns.portfolio")}>
      {portfolio?.state === "done" ? <>{fig("model.returns.portfolio", "Portfolio backtest")}{portfolio.risk && <Table head={["", "Mean", "Std", "Annualized return", "Information ratio", "Max drawdown"]} rows={Object.entries(portfolio.risk).map(([name, found]) =>
        [name, sgn(num(found.mean), 5), num(found.std)?.toFixed(5) ?? "—", pct(num(found.annualized_return), 2), sgn(num(found.information_ratio), 2), pct(num(found.max_drawdown))])} />}</>
        : portfolio?.state === "running" ? <div className="mx-none"><span className="tx-spin" /> Running the backtest</div>
        : <div className="mx-usebar"><span className="mx-t3">{portfolio?.state === "failed" ? `Failed: ${portfolio.message}` : `Not run for #${run.number}`}</span><span className="tx-spacer" /><TxButton size="sm" onClick={onRunBacktest}>Run the backtest</TxButton></div>}</Sec>
  </>;
}
function InformationTab({run, results, fig, legend}: {run: ModelRun; results: Record<string, any>; fig: FigFn; legend: (id: string) => ReactNode}) {
  const table = (results.information?.table?.rows ?? []) as {name: string; mean: number; std: number; ir: number; t: number | null; p: number | null; skew: number; kurtosis: number; positive: number}[];
  const days = run.horizon ?? 5;
  const pfmt = (p: number | null) => p === null ? "" : p < 0.0001 ? "< 0.0001" : p.toFixed(4);
  return <>
    <Sec title="IC and Rank IC" sub={`daily, against the ${days}-day label; t and p with Newey–West errors over ${days - 1} lags`}><Table head={["", "Mean", "Std", "ICIR", "t", "p", "Skew", "Excess kurtosis", "Above 0"]}
      rows={table.map(row => [row.name, up(num(row.mean), sgn(num(row.mean), 4)), num(row.std)?.toFixed(4) ?? "—", num(row.ir)?.toFixed(2) ?? "—", row.t === null ? "—" : row.t.toFixed(2), pfmt(row.p), sgn(num(row.skew), 2), sgn(num(row.kurtosis), 2), plainPct(num(row.positive))])} /></Sec>
    <Sec title="IC and Rank IC each day" sub="bars are daily Rank IC; lines the 1-month means; click a day to see its values" right={legend("model.information.ic")}>{fig("model.information.ic", "IC and Rank IC each day")}</Sec>
    <div className="mx-two"><Sec title="Distribution" sub="daily Rank IC">{fig("model.information.distribution", "Distribution of the daily Rank IC")}</Sec><Sec title="Against a normal distribution" sub="quantiles of daily Rank IC">{fig("model.information.qq", "Quantiles against a normal distribution")}</Sec></div>
    <div className="mx-two"><Sec title="Monthly mean Rank IC">{fig("model.information.monthly", "Rank IC by month")}</Sec><Sec title="Decay" sub="Rank IC against the return over 1 to 20 days from the next open">{fig("model.information.decay", "Decay")}</Sec></div>
    <Sec title="Inside each sector" sub="Rank IC among the names of one sector">{fig("model.information.industry", "Rank IC inside each sector")}</Sec>
  </>;
}
function TurnoverTab({run, results, fig, legend}: {run: ModelRun; results: Record<string, any>; fig: FigFn; legend: (id: string) => ReactNode}) {
  const groups = (results.turnover?.groups ?? []) as {group: number; turnover: number}[];
  const days = run.horizon ?? 5;
  return <><Sec title="Turnover" sub={`share of each group replaced at each ${days}-day rebalance; rank autocorrelation of the prediction a day apart`}><Table head={["", ...groups.map(item => `Q${item.group}`), "Autocorrelation"]}
      rows={[[`${days}D`, ...groups.map(item => plainPct(num(item.turnover), 1)), num(results.turnover?.autocorr)?.toFixed(3) ?? "—"]]} /></Sec>
    <Sec title="Top and bottom group turnover" sub={`every ${days} days`} right={legend("model.turnover.top-bottom")}>{fig("model.turnover.top-bottom", "Turnover of the top and bottom group")}</Sec>
    <Sec title="Prediction autocorrelation" sub="rank correlation with the day before">{fig("model.turnover.autocorr", "Prediction autocorrelation")}</Sec></>;
}
function InputsTab({run, results, fig, legend}: {run: ModelRun; results: Record<string, any>; fig: FigFn; legend: (id: string) => ReactNode}) {
  const inputs = results.inputs ?? {};
  const best = inputs.baselines, mine = num(run.metrics?.ric);
  const rows: [string, string, number | null][] = best ? [["Best single input", best.bestInput?.name ?? "", num(best.bestInput?.ic)], ["Equal-weight inputs", "rank average of the inputs, signed", num(best.equalWeight?.mean ?? best.equalWeight)],
    ["Linear model", "ridge on the same inputs", num(best.ridge)], [`#${run.number}`, algoLabel(run.algo), mine]] : [];
  const biggest = Math.max(...rows.map(item => Math.abs(item[2] ?? 0)), 1e-9);
  const list = (inputs.table ?? []) as {feature: string; source: string; share: number; split: number | null; ownIc: number | null; t: number | null; missing: number | null}[];
  const [all, setAll] = useState(false);
  const shown = all ? list : list.slice(0, 25);
  const tops = Math.max(...list.map(item => item.share), 1e-9);
  const after = inputs.destyled;
  return <>
    <div className="mx-two"><Sec title="Against simpler ways" sub={`test Rank IC, ${run.horizon ?? 5}D`}><Table head={["", "", "Rank IC", "", `#${run.number} adds`]} rows={rows.map(([label, what, value], index) => [label, <span className="mx-t2 mx-clip">{what}</span>, up(value, sgn(value, 4)),
        <span className="mx-bar1" style={{width: `${Math.abs(value ?? 0) / biggest * 100}%`, background: index === 3 ? "var(--tx-text)" : "#5F8DD4"}} />, index === 3 || value === null || mine === null ? "" : up(mine - value, sgn(mine - value, 4))])} /></Sec>
      <Sec title="Like the library" sub="mean daily rank correlation; library limit 0.70">{fig("model.inputs.library", "Rank correlation with the library")}</Sec></div>
    <div className="mx-two"><Sec title="Style exposure" sub="mean daily rank correlation of the prediction; limit 0.30">{fig("model.inputs.styles", "Style exposure")}</Sec><Sec title="Sector tilt" sub="mean prediction percentile in each sector, less 50">{fig("model.inputs.industry", "Sector tilt")}</Sec></div>
    <Sec title="After removing styles" sub="Rank IC once size, momentum, volatility, liquidity, beta and sector are taken out each day" right={legend("model.inputs.destyled")}>
      <div className="mx-after"><div className="mx-stats"><div><span className="mx-t3">Before</span><b className="mx-num">{up(num(after?.of), sgn(num(after?.of), 4))}</b></div>
        <div><span className="mx-t3">After</span><b className="mx-num">{up(num(after?.rankIc), sgn(num(after?.rankIc), 4))}</b><span className="mx-t3">{num(after?.of) && num(after?.rankIc) !== null ? `${Math.round((after.rankIc / after.of) * 100)}% kept` : ""}</span></div></div>
        <div className="mx-fig mx-xs">{fig("model.inputs.destyled", "Rank IC before and after styles")}</div></div></Sec>
    <Sec title="Inputs" sub={`${list.length} used, by importance in #${run.number}; own Rank IC on the test period, signed as before the first window`}><Table head={["Rank", "Input", "Source", "Gain", "", "Splits", "Own Rank IC", "t", "Missing"]}
      rows={shown.map((item, index) => [<span className="mx-t3">{index + 1}</span>, item.feature, <span className="mx-t2">{item.source}</span>, plainPct(num(item.share), 1), <span className="mx-bar1" style={{width: `${item.share / tops * 100}%`, background: "#83B1F7"}} />,
        item.split === null || item.split === undefined ? "" : plainPct(item.split, 1), item.ownIc === null ? "" : up(item.ownIc, sgn(item.ownIc, 4)), item.t === null ? "" : Math.abs(item.t).toFixed(2), plainPct(num(item.missing), 1)])} />
      {!all && list.length > 25 && <TxButton size="sm" variant="quiet" onClick={() => setAll(true)}>All {list.length}</TxButton>}</Sec>
  </>;
}
function RobustnessTab({run, results, fig, legend, reply, onHeldOut}: {run: ModelRun; results: Record<string, any>; fig: FigFn; legend: (id: string) => ReactNode; reply: ModelResultsReply; onHeldOut(): void}) {
  const robust = results.robustness ?? {};
  const years = (robust.years ?? []) as {year: number; rankIc: number; t: number | null; lsNet: number | null; sharpe: number | null; partial: boolean}[];
  const held = robust.heldOut as {numbers?: Record<string, number | null>; number?: number; state?: string; window?: number; windows?: number} | null;
  const here = reply.run.metrics ?? {};
  const subsets = [...(robust.bySize ?? []).map((row: any) => ({name: `${row.part} names`, ic: row.rankIc})), ...(robust.byLiquidity ?? []).map((row: any) => ({name: `${row.part}`, ic: row.rankIc}))] as {name: string; ic: number | null}[];
  const cover = robust.coverage as {median: number; min: number} | undefined;
  return <>
    <div className="mx-two"><Sec title="By year" sub={`net of ${results.returns?.costBps ?? 5} bps`}><Table head={["Year", "Rank IC", "t", "L–S net", "Sharpe"]} rows={years.map(row => [<>{row.year}{row.partial ? <span className="mx-t3"> part</span> : null}</>,
        up(num(row.rankIc), sgn(num(row.rankIc), 4)), <span className="mx-t2">{row.t === null ? "—" : sgn(row.t, 2)}</span>, up(num(row.lsNet), pct(num(row.lsNet), 2)), sgn(num(row.sharpe), 2)])} /></Sec>
      <Sec title="Rank IC by year">{fig("model.robustness.years", "Rank IC by year")}</Sec></div>
    <div className="mx-two"><Sec title="Held out" sub={held?.numbers ? `${held.windows ?? ""} windows retrained on schedule, scored once` : ""}>{held?.numbers ? <><Table head={["", "Rank IC", "t", "L–S net"]} rows={[["Test", up(num(here.ric), sgn(num(here.ric), 4)), num(here.t)?.toFixed(2) ?? "—", up(num(here.lsNet), pct(num(here.lsNet), 2))],
        [<b>Held out{robust.shortSample ? " · short sample" : ""}</b>, up(num(held.numbers.ric), sgn(num(held.numbers.ric), 4)), num(held.numbers.t)?.toFixed(2) ?? "—", up(num(held.numbers.lsNet), pct(num(held.numbers.lsNet), 2))]]} />{fig("model.robustness.held-out", "Held-out net long–short")}</>
      : held ? <div className="mx-none"><span className="tx-spin" /> Retraining held-out window {held.window ?? 1}{held.windows ? ` of ${held.windows}` : ""} on schedule</div>
      : run.heldOutFrom ? <div className="mx-usebar"><span className="mx-t2"><TxIcon name="lock" /> {dayYear(Date.parse(run.heldOutFrom))} onward is held out; nothing on this page uses it.</span><span className="tx-spacer" /><TxButton size="sm" onClick={onHeldOut}>Validate on held-out</TxButton></div>
      : <div className="mx-none">No held-out period is set.</div>}</Sec>
      <Sec title="Subsets" sub={`Rank IC, ${run.horizon ?? 5} days`}><Table head={["", ""]} rows={subsets.map(row => [row.name, up(num(row.ic), sgn(num(row.ic), 4))])} /></Sec></div>
    <Sec title="Coverage" sub={cover ? `names with a prediction, weekly; mean ${cover.median}, lowest ${cover.min}` : "names with a prediction, weekly"} right={legend("model.robustness.coverage")}>{fig("model.robustness.coverage", "Coverage")}</Sec>
  </>;
}
function SettingsTab({run, reply, other, mapi, onUse}: {run: ModelRun; reply: ModelResultsReply; other: ModelRun | null; mapi: ModelApi; onUse(): void}) {
  const [code, setCode] = useState<{files: {file: string; text: string}[]; pipeline: {version: string; digest: string} | null} | null>(null);
  const [codeProblem, setCodeProblem] = useState<string | null>(null);
  useEffect(() => {setCodeProblem(null); void mapi.code(run.id).then(setCode, reason => {setCode(null); setCodeProblem(reason instanceof Error ? reason.message : String(reason));});}, [run.id]);
  const kv = (items: [string, ReactNode][]) => <div className="mx-kv">{items.map(([key, value], index) => <><span key={`k${index}`}>{key}</span><span key={`v${index}`}>{value}</span></>)}</div>;
  const data = (reply.data ?? {}) as Record<string, any>;
  const env = (reply.environment ?? {}) as Record<string, any>;
  const params = Object.entries(run.params).filter(([key]) => key !== "inputs" && key !== "engine_params" && key in reply.parameterLabels);
  const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
  const m = run.metrics ?? {};
  const o = other?.metrics ?? {};
  const logged: [string, string, string][] = [["rank_ic", sgn(num(m.ric), 4), other ? sgn(num(o.ric), 4) : ""], ["t", sgn(num(m.t), 2), other ? sgn(num(o.t), 2) : ""], ["long_short_net.annual", pct(num(m.lsNet), 2), other ? pct(num(o.lsNet), 2) : ""],
    ["long_short.annual", pct(num(m.lsGross), 2), other ? pct(num(o.lsGross), 2) : ""], ["turnover", plainPct(num(m.turnover), 1), other ? plainPct(num(o.turnover), 1) : ""], ["breakeven_bps", num(m.breakeven)?.toFixed(1) ?? "none", other ? num(o.breakeven)?.toFixed(1) ?? "none" : ""]];
  return <>
    <div className="mx-usebar"><span className="mx-t2">Everything #{run.number} ran with</span><span className="tx-spacer" /><TxButton size="sm" onClick={onUse}>Use these settings</TxButton></div>
    <div className="mx-two"><Sec title="Run">{kv([["Started", dayYear(run.started ?? run.createdAt)], ["Finished", run.finished ? dayYear(run.finished) : "—"], ["Ran", `${minutes(run.time)}, ${run.windowsDone} of ${run.windowsTotal ?? run.windowsDone} windows`],
        ...(env.name ? [["Device", String(env.device)] as [string, ReactNode]] : []), ["Threads", env.threads ? `${env.threads}` : "—"], ["Peak memory", env.peakMemory ? `${(env.peakMemory / 1e9).toFixed(1)} GB` : "—"], ["Environment", env.name ? `${env.name} · Python ${env.python} · PyTorch ${env.torch} · Qlib ${env.qlib}` : `Research · ${env.device ?? "CPU"}${run.pipeline ? ` · standard pipeline ${run.pipeline.version}` : " · own code"}`],
        ["Scored", run.windows.length && run.windows[0].test ? `test ${month(run.windows[0].test.from)} – ${month(run.windows[run.windows.length - 1].test!.to)}` : "—"]])}</Sec>
      <Sec title="Dataset" sub={`${run.horizon ?? ""}D label`}>{kv([["Members", data.names ? `${data.names} names` : "—"], ["Period", run.dataFrom && run.dataTo ? `${month(run.dataFrom)} – ${month(run.dataTo)}` : "—"], ["Built in", run.inputs.join(", ") || "none"],
        ["Label", run.label], ["Split", `${String(run.params.window_mode ?? "rolling")}, train ${String(run.params.train_months ?? "")}m, valid ${String(run.params.valid_months ?? "")}m, every ${String(run.params.every_months ?? "")}m`],
        ["Gaps", `${run.reach ? run.reach.ahead - 1 : run.horizon} bars, the label`], ["Held out from", run.heldOutFrom ?? "none"]])}</Sec></div>
    <div className="mx-two"><Sec title="Parameters" sub={other ? `with #${other.number}` : ""}><Table head={["", `#${run.number}`, other ? `#${other.number}` : ""]} rows={params.map(([key, value]) => [reply.parameterLabels[key], same(value, other?.params[key]) || !other ? <span className="mx-t2">{String(value)}</span> : String(value), <span className="mx-t3">{other ? String(other.params[key] ?? "") : ""}</span>])} /></Sec>
      <Sec title="Metrics" sub="as logged"><Table head={["", `#${run.number}`, other ? `#${other.number}` : ""]} rows={logged.map(([key, value, then]) => [<span className="mx-t2 mx-num">{key}</span>, value, <span className="mx-t3">{then}</span>])} /></Sec></div>
    <Sec title="Files" sub={`kept with #${run.number}`}><Table head={["File", "", "Size"]} rows={[["models/window_01 … window_" + String(run.windowsDone).padStart(2, "0"), `${run.windowsDone} files`, ""], ["predictions", "", ""], ["settings.json", "every setting as run", ""]]} /></Sec>
    {codeProblem !== null && <TxFailure title={`The code of #${run.number} could not be read`} message={codeProblem} />}
    {code && code.files.length > 0 && <Sec title={code.pipeline ? `Standard pipeline ${code.pipeline.version} as #${run.number} ran it, read-only` : `Code of #${run.number}, read-only`}><pre className="mx-pre">{code.files[0].text}</pre></Sec>}
  </>;
}
