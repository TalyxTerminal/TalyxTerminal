/* One run's Results, a tab at a time: Windows, Returns, Information, Turnover, Inputs, Robustness, Values and Settings. Every figure is the backend's Plotly figure shown
   by Plot, taken by its key; one that could not be drawn says why in its place. Tables are read from the run's results. */
import { t } from "../../i18n";
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
  {value: "windows", label: t("Windows")}, {value: "returns", label: t("Returns")}, {value: "information", label: t("Information")}, {value: "turnover", label: t("Turnover")},
  {value: "inputs", label: t("Inputs")}, {value: "robustness", label: t("Robustness")}, {value: "values", label: t("Values")}, {value: "price", label: t("On price")}, {value: "settings", label: t("Settings")},
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
  if (!found) return <div className="mx-why"><TxIcon name="alert" /><span>{t("This figure was not drawn: {missing}", { missing: missing[id] ?? t("no data for it") })}</span></div>;
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
  const compareItems: TxMenuItem[] = [{id: "none", label: t("No comparison"), onSelect: () => onVs(null)}, ...runs.filter(item => item.id !== run.id && item.status === "completed")
    .map(item => ({id: item.id, label: `#${item.number} · ${algoLabel(item.algo)} · ${item.label}`, onSelect: () => onVs(item.id)}))];
  const fig = (id: string, label: string) => <Fig id={id} figures={figures} missing={missing} label={label} />;
  // the keys of a chart sit in its section head, drawn by the shared chart layer; a second row of them here would say the same twice
  const legend = (_id: string): ReactNode => null;
  const runItems: TxMenuItem[] = runs.filter(item => item.status === "completed" || item.id === run.id).map(item => ({id: item.id, label: `#${item.number} · ${algoLabel(item.algo)} · ${item.label}`, onSelect: () => onSelect(item.id)}));
  const what = `${algoLabel(run.algo)}${differences(run, null) ? `, ${differences(run, null)}` : ""}, ${run.horizon ?? ""}D`;
  const head = <div className="mx-rhead"><TxRunPicker run={`#${run.number}`} items={runItems} /><span className="mx-nw">{what}</span><span className="mx-t3 mx-nw">{day(run.createdAt)}{run.time ? ` · ${minutes(run.time)}` : ""}</span>
    <span className="mx-t3 mx-nw">{run.dataFrom && run.dataTo ? `${month(run.dataFrom)} – ${month(run.dataTo)}` : ""}</span><span className="mx-t3 mx-nw">{run.dataTo ? t("data to {dataTo}", { dataTo: dayYear(run.dataTo) }) : ""}</span>
    <TxCompare run={other ? `#${other.number}` : null} items={compareItems} />{run.status === "running" && <TxTag tone="muted">{t("Running")}</TxTag>}{run.heldOut && <TxTag tone="muted">{t("Held-out")}</TxTag>}
    <span className="tx-spacer" /><button type="button" className="mx-btn" data-on={run.starred ? "" : undefined} onClick={() => onStar(run)}><TxIcon name="star" />{t("Star")}</button>{done && <TxButton size="sm" variant="primary" onClick={() => onPublish(run)}>{t("Publish")}</TxButton>}</div>;
  const body = () => {
    if (!done) return <div className="mx-none">{run.status === "failed" && run.failure ? <TxFailure title={run.failure.title} message={run.failure.detail ?? run.failure.message} /> : run.status === "running" ? t("Results appear when the run has finished its windows.") : t("No results yet.")}</div>;
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
  const what = algorithm === "lightgbm" ? t("share of split gain") : algorithm === "ridge" || algorithm === "lasso" || algorithm === "linear" ? t("share of |coefficient| on ranked inputs") : algorithm && deepIds.has(algorithm) ? t("share of the fall in validation Rank IC when an input is shuffled within each day (permutation)") : t("share of importance");
  // An algorithm that trains on its validation rows keeps no validation IC, and one fitted in one step no rounds: those parts are left out, not shown empty.
  const validated = rows.length === 0 || rows.some(w => num(w.validIc) !== null);
  const rounds = rows.some(w => w.best != null);
  const curves = has("model.windows.learning") || rounds;
  const features = (importance?.features ?? []) as string[];
  const extra = [
    validated && <Sec key="vt" title={t("Validation against test")} sub={t("each dot a window; under the line the test was worse")}>{fig("model.windows.valid-test", t("Validation against test"))}</Sec>,
    curves && <Sec key="lc" title={t("Learning curves")} sub={(deepIds.has(String(algorithm)) ? t("last 4 windows, validation loss relative to epoch 0; dot is where it stopped") : t("last 4 windows, validation loss relative to round 0; dot is where it stopped"))}>{fig("model.windows.learning", t("Learning curves"))}</Sec>,
  ].filter(Boolean);
  return <>
    <div className="mx-two"><Sec title={t("Each window")} sub={validated ? t("validation and test Rank IC; dotted, the test mean") : t("test Rank IC; dotted, the mean")} right={legend("model.windows.each")}>{fig("model.windows.each", t("Rank IC by window"))}</Sec>
      <Sec title={t("Windows")} sub={t("Rank IC in each part")}>{rows.length === 0 ? <div className="mx-none">{t("This run's code keeps no windows (no q.record); numbers are for the whole period.")}</div>
        : <div className="mx-scroll"><Table head={["#", t("Test period"), t("Train"), ...(validated ? [t("Valid")] : []), t("Test"), ...(rounds ? [t("Round")] : []), t("Rows"), t("Time")]} rows={rows.map(w => [<span className="mx-t2">{w.k}</span>, w.test ? `${month(w.test.from)} – ${month(w.test.to)}` : "",
          sgn(num(w.trainIc), 4), ...(validated ? [up(num(w.validIc), sgn(num(w.validIc), 4))] : []), up(num(w.testIc), sgn(num(w.testIc), 4)), ...(rounds ? [w.best ?? ""] : []), `${Math.round((w.rows ?? 0) / 1000)}k`, minutes(w.secs)])} /></div>}</Sec></div>
    {extra.length > 0 && <div className={extra.length > 1 ? "mx-two" : undefined}>{extra}</div>}
    <Sec title={t("Importance by window")} sub={features.length >= 20 ? t("{what}; the 20 inputs with the most on average", { what }) : what}>{importance ? <div className="mx-fig" style={{height: Math.max(features.length, 3) * 22 + 40}}>{fig("model.windows.importance", t("Importance by window"))}</div> : fig("model.windows.importance", t("Importance by window"))}</Sec>
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
    <Sec title={t("Cumulative return by group")} sub={t("{days}-day rebalance, relative to all names, net long–short in white", { days })} right={legend("model.returns.groups")}>{fig("model.returns.groups", t("Cumulative return by group"))}</Sec>
    <div className="mx-two">
      <Sec title={t("Long–short and long–average")} sub={t("groups by prediction, {days}-day rebalance; average is all names", { days })}><Table head={["", t("Annual"), t("Sharpe"), t("Max DD"), t("Alpha"), t("Beta")]} rows={rows.map(row => [row.name, up(num(row.annual), pct(num(row.annual), 2)), sgn(num(row.sharpe), 2), down(pct(num(row.maxDrawdown))),
        num(row.alpha) === null ? "" : up(num(row.alpha), pct(num(row.alpha))), num(row.beta) === null ? "" : sgn(num(row.beta), 2)])} /></Sec>
      <Sec title={t("Top {n} against the average", { n: top.n ?? 50 })} sub={t("excess return a day; cost {costBps} bps a trade", { costBps: returns.costBps ?? "" })}>{top.before && <Table head={["", t("Mean, bps"), t("Std, bps"), t("Annualized"), t("Info. ratio"), t("Max DD")]} rows={(["before", "after"] as const).map(key => [key === "before" ? t("Before cost") : t("After cost"),
        sgn(num(top[key].meanBps), 2), num(top[key].stdBps)?.toFixed(1) ?? "—", up(num(top[key].annual), pct(num(top[key].annual), 2)), sgn(num(top[key].ir), 2), down(pct(num(top[key].maxDrawdown)))])} />}</Sec></div>
    <Sec title={t("Cumulative long–short and long–average")} sub={t("gross, net long–short in white")} right={legend("model.returns.ls-cumulative")}>{fig("model.returns.ls-cumulative", t("Cumulative long–short and long–average"))}</Sec>
    <div className="mx-two"><Sec title={t("Mean return by group")} sub={(spread !== null ? t("relative to all names, bps a bar over {days} bars; spread {spread} bps", { days, spread: sgn(spread, 2) }) : t("relative to all names, bps a bar over {days} bars", { days }))}>{fig("model.returns.group-mean", t("Mean return by group"))}</Sec>
      <Sec title={t("Top {n}, excess over the average", { n: top.n ?? 50 })} sub={t("cumulative, before and after cost")} right={legend("model.returns.top-excess")}>{fig("model.returns.top-excess", t("Top N excess over the average"))}</Sec></div>
    <div className="mx-two"><Sec title={t("Drawdown")} sub={t("net of cost")} right={legend("model.returns.ls-drawdown")}>{fig("model.returns.ls-drawdown", t("Drawdown"))}</Sec>
      <Sec title={t("Rank of the top and bottom {n}", { n: top.n ?? 50 })} sub={t("realised {days}-day percentile, 3-month mean", { days })} right={legend("model.returns.top-rank")}>{fig("model.returns.top-rank", t("Rank of the top and bottom"))}</Sec></div>
    <div className="mx-two"><Sec title={t("Rebalance every")} sub={t("same predictions, cost per trade on both legs")}><Table head={[t("Every"), t("Gross"), t("Turnover"), t("Costs"), t("Net"), t("Sharpe"), t("Break-even")]}
      rows={rebalance.map(row => [`${row.days}D`, up(num(row.gross), pct(num(row.gross), 2)), plainPct(num(row.turnover), 1), down(pct(num(row.costs) === null ? null : -(row.costs), 2)), up(num(row.lsNet), pct(num(row.lsNet), 2)), sgn(num(row.sharpe), 2), row.breakeven === null ? "none" : row.breakeven.toFixed(1)])} /></Sec>
      <Sec title={t("Net by cost per trade")} sub={t("long–short, a year")}>{fig("model.returns.net-by-cost", t("Net by cost per trade"))}</Sec></div>
    <Sec title={t("Portfolio backtest")} sub={t("Qlib: top 50, drop 5 a day, after cost; on request")} right={legend("model.returns.portfolio")}>
      {portfolio?.state === "done" ? <>{fig("model.returns.portfolio", t("Portfolio backtest"))}{portfolio.risk && <Table head={["", t("Mean"), t("Std"), t("Annualized return"), t("Information ratio"), t("Max drawdown")]} rows={Object.entries(portfolio.risk).map(([name, found]) =>
        [name, sgn(num(found.mean), 5), num(found.std)?.toFixed(5) ?? "—", pct(num(found.annualized_return), 2), sgn(num(found.information_ratio), 2), pct(num(found.max_drawdown))])} />}</>
        : portfolio?.state === "running" ? <div className="mx-none"><span className="tx-spin" />{t(" Running the backtest")}</div>
        : <div className="mx-usebar"><span className="mx-t3">{portfolio?.state === "failed" ? t("Failed: {message}", { message: portfolio.message ?? "" }) : t("Not run for #{number}", { number: run.number })}</span><span className="tx-spacer" /><TxButton size="sm" onClick={onRunBacktest}>{t("Run the backtest")}</TxButton></div>}</Sec>
  </>;
}
function InformationTab({run, results, fig, legend}: {run: ModelRun; results: Record<string, any>; fig: FigFn; legend: (id: string) => ReactNode}) {
  const table = (results.information?.table?.rows ?? []) as {name: string; mean: number; std: number; ir: number; t: number | null; p: number | null; skew: number; kurtosis: number; positive: number}[];
  const days = run.horizon ?? 5;
  const pfmt = (p: number | null) => p === null ? "" : p < 0.0001 ? "< 0.0001" : p.toFixed(4);
  return <>
    <Sec title={t("IC and Rank IC")} sub={t("daily, against the {days}-day label; t and p with Newey–West errors over {days2} lags", { days, days2: days - 1 })}><Table head={["", t("Mean"), t("Std"), "ICIR", "t", "p", t("Skew"), t("Excess kurtosis"), t("Above 0")]}
      rows={table.map(row => [row.name, up(num(row.mean), sgn(num(row.mean), 4)), num(row.std)?.toFixed(4) ?? "—", num(row.ir)?.toFixed(2) ?? "—", row.t === null ? "—" : row.t.toFixed(2), pfmt(row.p), sgn(num(row.skew), 2), sgn(num(row.kurtosis), 2), plainPct(num(row.positive))])} /></Sec>
    <Sec title={t("IC and Rank IC each day")} sub={t("bars are daily Rank IC; lines the 1-month means; click a day to see its values")} right={legend("model.information.ic")}>{fig("model.information.ic", t("IC and Rank IC each day"))}</Sec>
    <div className="mx-two"><Sec title={t("Distribution")} sub={t("daily Rank IC")}>{fig("model.information.distribution", t("Distribution of the daily Rank IC"))}</Sec><Sec title={t("Against a normal distribution")} sub={t("quantiles of daily Rank IC")}>{fig("model.information.qq", t("Quantiles against a normal distribution"))}</Sec></div>
    <div className="mx-two"><Sec title={t("Monthly mean Rank IC")}>{fig("model.information.monthly", t("Rank IC by month"))}</Sec><Sec title={t("Decay")} sub={t("Rank IC against the return over 1 to 20 days from the next open")}>{fig("model.information.decay", t("Decay"))}</Sec></div>
    <Sec title={t("Inside each sector")} sub={t("Rank IC among the names of one sector")}>{fig("model.information.industry", t("Rank IC inside each sector"))}</Sec>
  </>;
}
function TurnoverTab({run, results, fig, legend}: {run: ModelRun; results: Record<string, any>; fig: FigFn; legend: (id: string) => ReactNode}) {
  const groups = (results.turnover?.groups ?? []) as {group: number; turnover: number}[];
  const days = run.horizon ?? 5;
  return <><Sec title={t("Turnover")} sub={t("share of each group replaced at each {days}-day rebalance; rank autocorrelation of the prediction a day apart", { days })}><Table head={["", ...groups.map(item => `Q${item.group}`), t("Autocorrelation")]}
      rows={[[`${days}D`, ...groups.map(item => plainPct(num(item.turnover), 1)), num(results.turnover?.autocorr)?.toFixed(3) ?? "—"]]} /></Sec>
    <Sec title={t("Top and bottom group turnover")} sub={t("every {days} days", { days })} right={legend("model.turnover.top-bottom")}>{fig("model.turnover.top-bottom", t("Turnover of the top and bottom group"))}</Sec>
    <Sec title={t("Prediction autocorrelation")} sub={t("rank correlation with the day before")}>{fig("model.turnover.autocorr", t("Prediction autocorrelation"))}</Sec></>;
}
function InputsTab({run, results, fig, legend}: {run: ModelRun; results: Record<string, any>; fig: FigFn; legend: (id: string) => ReactNode}) {
  const inputs = results.inputs ?? {};
  const best = inputs.baselines, mine = num(run.metrics?.ric);
  const rows: [string, string, number | null][] = best ? [[t("Best single input"), best.bestInput?.name ?? "", num(best.bestInput?.ic)], [t("Equal-weight inputs"), t("rank average of the inputs, signed"), num(best.equalWeight?.mean ?? best.equalWeight)],
    [t("Linear model"), t("ridge on the same inputs"), num(best.ridge)], [`#${run.number}`, algoLabel(run.algo), mine]] : [];
  const biggest = Math.max(...rows.map(item => Math.abs(item[2] ?? 0)), 1e-9);
  const list = (inputs.table ?? []) as {feature: string; source: string; share: number; split: number | null; ownIc: number | null; t: number | null; missing: number | null}[];
  const [all, setAll] = useState(false);
  const shown = all ? list : list.slice(0, 25);
  const tops = Math.max(...list.map(item => item.share), 1e-9);
  const after = inputs.destyled;
  return <>
    <div className="mx-two"><Sec title={t("Against simpler ways")} sub={t("test Rank IC, {horizon}D", { horizon: run.horizon ?? 5 })}><Table head={["", "", t("Rank IC"), "", t("#{number} adds", { number: run.number })]} rows={rows.map(([label, what, value], index) => [label, <span className="mx-t2 mx-clip">{what}</span>, up(value, sgn(value, 4)),
        <span className="mx-bar1" style={{width: `${Math.abs(value ?? 0) / biggest * 100}%`, background: index === 3 ? "var(--tx-text)" : "#5F8DD4"}} />, index === 3 || value === null || mine === null ? "" : up(mine - value, sgn(mine - value, 4))])} /></Sec>
      <Sec title={t("Like the library")} sub={t("mean daily rank correlation; library limit 0.70")}>{fig("model.inputs.library", t("Rank correlation with the library"))}</Sec></div>
    <div className="mx-two"><Sec title={t("Style exposure")} sub={t("mean daily rank correlation of the prediction; limit 0.30")}>{fig("model.inputs.styles", t("Style exposure"))}</Sec><Sec title={t("Sector tilt")} sub={t("mean prediction percentile in each sector, less 50")}>{fig("model.inputs.industry", t("Sector tilt"))}</Sec></div>
    <Sec title={t("After removing styles")} sub={t("Rank IC once size, momentum, volatility, liquidity, beta and sector are taken out each day")} right={legend("model.inputs.destyled")}>
      <div className="mx-after"><div className="mx-stats"><div><span className="mx-t3">{t("Before")}</span><b className="mx-num">{up(num(after?.of), sgn(num(after?.of), 4))}</b></div>
        <div><span className="mx-t3">{t("After")}</span><b className="mx-num">{up(num(after?.rankIc), sgn(num(after?.rankIc), 4))}</b><span className="mx-t3">{num(after?.of) && num(after?.rankIc) !== null ? t("{percent}% kept", { percent: Math.round((after.rankIc / after.of) * 100) }) : ""}</span></div></div>
        <div className="mx-fig mx-xs">{fig("model.inputs.destyled", t("Rank IC before and after styles"))}</div></div></Sec>
    <Sec title={t("Inputs")} sub={t("{listCount} used, by importance in #{number}; own Rank IC on the test period, signed as before the first window", { listCount: list.length, number: run.number })}><Table head={[t("Rank"), t("Input"), t("Source"), t("Gain"), "", t("Splits"), t("Own Rank IC"), "t", t("Missing")]}
      rows={shown.map((item, index) => [<span className="mx-t3">{index + 1}</span>, item.feature, <span className="mx-t2">{item.source}</span>, plainPct(num(item.share), 1), <span className="mx-bar1" style={{width: `${item.share / tops * 100}%`, background: "#83B1F7"}} />,
        item.split === null || item.split === undefined ? "" : plainPct(item.split, 1), item.ownIc === null ? "" : up(item.ownIc, sgn(item.ownIc, 4)), item.t === null ? "" : Math.abs(item.t).toFixed(2), plainPct(num(item.missing), 1)])} />
      {!all && list.length > 25 && <TxButton size="sm" variant="quiet" onClick={() => setAll(true)}>{t("All {listCount}", { listCount: list.length })}</TxButton>}</Sec>
  </>;
}
function RobustnessTab({run, results, fig, legend, reply, onHeldOut}: {run: ModelRun; results: Record<string, any>; fig: FigFn; legend: (id: string) => ReactNode; reply: ModelResultsReply; onHeldOut(): void}) {
  const robust = results.robustness ?? {};
  const years = (robust.years ?? []) as {year: number; rankIc: number; t: number | null; lsNet: number | null; sharpe: number | null; partial: boolean}[];
  const held = robust.heldOut as {numbers?: Record<string, number | null>; number?: number; state?: string; window?: number; windows?: number} | null;
  const here = reply.run.metrics ?? {};
  const subsets = [...(robust.bySize ?? []).map((row: any) => ({name: t("{count} names", { count: String(row.part) }), ic: row.rankIc})), ...(robust.byLiquidity ?? []).map((row: any) => ({name: `${row.part}`, ic: row.rankIc}))] as {name: string; ic: number | null}[];
  const cover = robust.coverage as {median: number; min: number} | undefined;
  return <>
    <div className="mx-two"><Sec title={t("By year")} sub={t("net of {costBps} bps", { costBps: results.returns?.costBps ?? 5 })}><Table head={[t("Year"), t("Rank IC"), "t", t("L–S net"), t("Sharpe")]} rows={years.map(row => [<>{row.year}{row.partial ? <span className="mx-t3">{t(" part")}</span> : null}</>,
        up(num(row.rankIc), sgn(num(row.rankIc), 4)), <span className="mx-t2">{row.t === null ? "—" : sgn(row.t, 2)}</span>, up(num(row.lsNet), pct(num(row.lsNet), 2)), sgn(num(row.sharpe), 2)])} /></Sec>
      <Sec title={t("Rank IC by year")}>{fig("model.robustness.years", t("Rank IC by year"))}</Sec></div>
    <div className="mx-two"><Sec title={t("Held out")} sub={held?.numbers ? t("{windows} windows retrained on schedule, scored once", { windows: held.windows ?? "" }) : ""}>{held?.numbers ? <><Table head={["", t("Rank IC"), "t", t("L–S net")]} rows={[[t("Test"), up(num(here.ric), sgn(num(here.ric), 4)), num(here.t)?.toFixed(2) ?? "—", up(num(here.lsNet), pct(num(here.lsNet), 2))],
        [<b>{t("Held out")}</b>, up(num(held.numbers.ric), sgn(num(held.numbers.ric), 4)), num(held.numbers.t)?.toFixed(2) ?? "—", up(num(held.numbers.lsNet), pct(num(held.numbers.lsNet), 2))]]} />{fig("model.robustness.held-out", t("Held-out net long–short"))}</>
      : held ? <div className="mx-none"><span className="tx-spin" />{(held.windows ? t(" Retraining held-out window {window} of {windows} on schedule", { window: held.window ?? 1, windows: held.windows }) : t(" Retraining held-out window {window} on schedule", { window: held.window ?? 1 }))}</div>
      : run.heldOutFrom ? <div className="mx-usebar"><span className="mx-t2"><TxIcon name="lock" />{t(" {date} onward is held out; nothing on this page uses it.", { date: dayYear(Date.parse(run.heldOutFrom)) })}</span><span className="tx-spacer" /><TxButton size="sm" onClick={onHeldOut}>{t("Validate on held-out")}</TxButton></div>
      : <div className="mx-none">{t("No held-out period is set.")}</div>}</Sec>
      <Sec title={t("Subsets")} sub={t("Rank IC, {horizon} days", { horizon: run.horizon ?? 5 })}><Table head={["", ""]} rows={subsets.map(row => [row.name, up(num(row.ic), sgn(num(row.ic), 4))])} /></Sec></div>
    <Sec title={t("Coverage")} sub={cover ? t("names with a prediction, weekly; mean {median}, lowest {min}", { median: cover.median, min: cover.min }) : t("names with a prediction, weekly")} right={legend("model.robustness.coverage")}>{fig("model.robustness.coverage", t("Coverage"))}</Sec>
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
    <div className="mx-usebar"><span className="mx-t2">{t("Everything #{number} ran with", { number: run.number })}</span><span className="tx-spacer" /><TxButton size="sm" onClick={onUse}>{t("Use these settings")}</TxButton></div>
    <div className="mx-two"><Sec title={t.c("run record", "Run")}>{kv([[t("Started"), dayYear(run.started ?? run.createdAt)], [t("Finished"), run.finished ? dayYear(run.finished) : "—"], [t("Ran"), t("{time}, {done} of {total} windows", { time: minutes(run.time), done: run.windowsDone, total: run.windowsTotal ?? run.windowsDone })],
        ...(env.name ? [[t("Device"), String(env.device)] as [string, ReactNode]] : []), [t("Threads"), env.threads ? `${env.threads}` : "—"], [t("Peak memory"), env.peakMemory ? t("{value} GB", { value: (env.peakMemory / 1e9).toFixed(1) }) : "—"], [t("Environment"), env.name ? `${env.name} · Python ${env.python} · PyTorch ${env.torch} · Qlib ${env.qlib}` : run.pipeline ? t("Research · {device} · standard pipeline {version}", { device: env.device ?? "CPU", version: run.pipeline.version }) : t("Research · {device} · own code", { device: env.device ?? "CPU" })],
        [t("Scored"), run.windows.length && run.windows[0].test ? t("test {start} – {end}", { start: month(run.windows[0].test.from), end: month(run.windows[run.windows.length - 1].test!.to) }) : "—"]])}</Sec>
      <Sec title={t("Dataset")} sub={t("{horizon}D label", { horizon: run.horizon ?? "" })}>{kv([[t("Members"), data.names ? t("{count} names", { count: String(data.names) }) : "—"], [t("Period"), run.dataFrom && run.dataTo ? `${month(run.dataFrom)} – ${month(run.dataTo)}` : "—"], [t("Built in"), run.inputs.join(", ") || "none"],
        [t("Label"), run.label], [t("Split"), t("{mode}, train {train}m, valid {valid}m, every {interval}m", { mode: String(run.params.window_mode ?? "rolling"), train: String(run.params.train_months ?? ""), valid: String(run.params.valid_months ?? ""), interval: String(run.params.every_months ?? "") })],
        [t("Gaps"), t("{bars} bars, the label", { bars: String(run.reach ? run.reach.ahead - 1 : run.horizon) })], [t("Held out from"), run.heldOutFrom ?? "none"]])}</Sec></div>
    <div className="mx-two"><Sec title={t("Parameters")} sub={other ? t("with #{number}", { number: other.number }) : ""}><Table head={["", `#${run.number}`, other ? `#${other.number}` : ""]} rows={params.map(([key, value]) => [reply.parameterLabels[key], same(value, other?.params[key]) || !other ? <span className="mx-t2">{String(value)}</span> : String(value), <span className="mx-t3">{other ? String(other.params[key] ?? "") : ""}</span>])} /></Sec>
      <Sec title={t("Metrics")} sub={t("as logged")}><Table head={["", `#${run.number}`, other ? `#${other.number}` : ""]} rows={logged.map(([key, value, then]) => [<span className="mx-t2 mx-num">{key}</span>, value, <span className="mx-t3">{then}</span>])} /></Sec></div>
    <Sec title={t("Files")} sub={t("kept with #{number}", { number: run.number })}><Table head={[t("File"), "", t("Size")]} rows={[["models/window_01 … window_" + String(run.windowsDone).padStart(2, "0"), t("{count} files", { count: run.windowsDone }), ""], ["predictions", "", ""], ["settings.json", t("every setting as run"), ""]]} /></Sec>
    {codeProblem !== null && <TxFailure title={t("The code of #{number} could not be read", { number: run.number })} message={codeProblem} />}
    {code && code.files.length > 0 && <Sec title={code.pipeline ? t("Standard pipeline {version} as #{number} ran it, read-only", { version: code.pipeline.version, number: run.number }) : t("Code of #{number}, read-only", { number: run.number })}><pre className="mx-pre">{code.files[0].text}</pre></Sec>}
  </>;
}
