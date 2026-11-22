import { intlLocale } from "@talyx/sdk";
/* A strategy's Results: one page that scrolls, the sections in the contents on the left (the one on screen is marked), each as tall as its content. The charts come from the
   backend with their roles and read-outs; what could not be drawn says why in its place. */
import { t } from "../i18n";
import { withPicking, type Picking } from "./picking";
import { memo, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { TxButton, TxIcon, TxRegionHead, TxSegmented, TxSelect, TxTag } from "@talyx/sdk";
import { dayText, mdy, nf, pc, pct, sgn, tone, when } from "../factor/format";
import type { Figure, ResultsView as ResultsData, Stats, StrategyApi, StrategyRun } from "./api";
import { exitLabel, utcStamp } from "./TradesTable";
import { benchNames } from "./Work";
import { settingName } from "./names";
import type { Parameter } from "../objects";
import { Plot } from "../chart/Plot";

/** Where a section's title lands below the top of the scrolling box: just under the run's head, which stays on top. */
const titleAt = (root: Element) => (root.querySelector(".qx-rhead")?.getBoundingClientRect().height ?? 0) + 8;

export const SECTION_IDS = ["summary", "equity", "periods", "rolling", "longShort", "trades", "orders", "account", "sectors", "instruments", "style", "costs", "crisis", "settings"];

export function Why({text}: {text: string}) { return <p className="fx-why" role="status">{text}</p>; }

/** A chart from the backend in its box, or why it could not be drawn. */
export function Fig({fig, why, label, size}: {fig?: Figure | null; why?: string; label: string; size?: "s" | "l" | "m" | "dd"}) {
  if (!fig) return <Why text={why ?? t("This chart was not built")} />;
  if ("unavailable" in fig) return <Why text={t("{label} could not be drawn: {unavailable}", { label, unavailable: fig.unavailable })} />;
  return <div className="fx-rch" data-size={size}><Plot figure={fig} ariaLabel={label} /></div>;
}

function Block({title, sub, children}: {title: string; sub?: string; children: ReactNode}) { return <div className="qx-block"><TxRegionHead title={title} sub={sub} />{children}</div>; }
function Grid({head, rows, left = [0]}: {head: ReactNode[]; rows: ReactNode[][]; left?: number[]}) {
  return <div className="fx-rtable"><table className="fx-g">{head.length > 0 && <thead><tr>{head.map((item, index) => <th key={index} className={left.includes(index) ? "l" : undefined}>{item}</th>)}</tr></thead>}
    <tbody>{rows.map((row, index) => <tr key={index}>{row.map((cell, at) => <td key={at} className={left.includes(at) ? "l" : undefined}>{cell}</td>)}</tr>)}</tbody></table></div>;
}
function Sec({title, sub, children}: {title: string; sub?: string; children: ReactNode}) { return <section className="qx-sec"><TxRegionHead title={title} sub={sub} />{children}</section>; }
const num = (value: number | null | undefined, digits = 2) => value == null ? "—" : nf(value, digits);
const sgnCell = (value: number | null | undefined, digits = 0, unit = "") => value == null ? "—" : <span className={tone(value)}>{sgn(value, digits)}{unit}</span>;

// ---------------------------------------------------------------- the sections
/** The last day a run read, whether the record holds it as a day or as milliseconds. */
export const dataToText = (value: unknown): string => typeof value === "number" ? mdy(value) : typeof value === "string" && /^\d{4}-\d{2}-\d{2}/.test(value) ? dayText(value.slice(0, 10)) : "";

/** A run beside this one in Results: its label, key numbers, and the columns it adds to the tables. */
export interface ComparedRun {runId: string; number: number; label: string; stats: Stats | null; rows?: Record<string, number | null>; against?: Record<string, number | null>; tradeStats?: Record<string, any>; whatDiffers?: string[]}
const PERCENT = new Set(["Total return", "CAGR", "Volatility", "Max drawdown", "Daily VaR 95%", "Daily CVaR 95%", "Best day", "Worst day"]);
const UNSIGNED = new Set(["Volatility"]);  // a size, not a direction: no + in front
const statisticLabels: Record<string, string> = {
  "Total return": t("Total return"),
  "Volatility": t("Volatility"),
  "Max drawdown": t("Max drawdown"),
  "Daily VaR 95%": t("Daily VaR 95%"),
  "Daily CVaR 95%": t("Daily CVaR 95%"),
  "Best day": t("Best day"),
  "Worst day": t("Worst day"),
  "Positive days": t("Positive days"),
  "Probabilistic Sharpe": t("Probabilistic Sharpe"),
  "Sharpe": t("Sharpe"),
  "Sortino": t("Sortino"),
  "Calmar": t("Calmar"),
  "Skew": t("Skew"),
  "Excess kurtosis": t("Excess kurtosis"),
};
const rowName = (name: string) => name === "CVaR (95%)" ? "Daily CVaR 95%" : name;
const showRow = (name: string, value: number | null | undefined): ReactNode => {
  if (value == null) return "—";
  const label = rowName(name);
  if (label === "Probabilistic Sharpe") return pct(value, 0);
  if (label === "Positive days") return pct(value, 1);
  if (label === "Days") return nf(value, 0);
  if (UNSIGNED.has(label)) return pct(value, 1);
  if (PERCENT.has(label)) return pc(value, 1);
  return ["Skew", "Excess kurtosis"].includes(label) ? sgn(value, 2) : nf(value, 2);
};
const engineLabels: Record<string, string> = {
  "Engine's own returns": t("Engine's own returns"),
  "Engine general": t("Engine general"),
  "Engine PnL": t("Engine PnL"),
  "252 days a year, every calendar day, no risk-free rate": t("252 days a year, every calendar day, no risk-free rate"),
  "Average (Return)": t("Average (Return)"),
  "Average Loss (Return)": t("Average Loss (Return)"),
  "Average Win (Return)": t("Average Win (Return)"),
  "Long Ratio": t("Long Ratio"),
  "Avg Loser": t("Avg Loser"),
  "Avg Winner": t("Avg Winner"),
  "Expectancy": t("Expectancy"),
  "Max Loser": t("Max Loser"),
  "Max Winner": t("Max Winner"),
  "Min Loser": t("Min Loser"),
  "Min Winner": t("Min Winner"),
};
const engineValue = (name: string, value: number): string => /ratio|factor|kurtosis|skew/i.test(name) ? nf(value, 2) : /return|volatility/i.test(name) ? pc(value, 2) : nf(value, Math.abs(value) >= 100 ? 0 : 2);
const toned = (name: string, value: number | null | undefined): ReactNode => value == null ? "—" : ["Total return", "CAGR", "Best day", "Worst day"].includes(rowName(name)) ? <span className={tone(value)}>{showRow(name, value)}</span> : showRow(name, value);
export function Summary({data, run, compared = []}: {data: any; run?: StrategyRun; compared?: ComparedRun[]}) {
  const rows = (data.returnStatistics ?? []) as {name: string; run: number | null; benchmark: number | null}[];
  const tiles = data.tiles ?? {};
  const against = data.againstBenchmark ?? {};
  const names = benchNames(data.benchmarkLabel), bench = names.short;
  const first = compared[0];
  // the numbers are the table's, once; the engine's own figures that share a name with them (counted over calendar days) are left out.
  const engine = ((data.engine ?? []) as {group: string; name: string; value: number | null}[])
    .filter(item => item.value !== null && item.group.startsWith("Engine") && !/sharpe|sortino|volatility|calmar|drawdown|profit factor|win rate|risk return|kurtosis|skew/i.test(item.name));
  const groups = [...new Set(engine.map(item => item.group))];
  const period = run ? `${dataToText(run.settings.data.since)} – ${dataToText(run.dataTo ?? run.settings.data.until)}, ` : "";
  const rate = data.riskFree?.label ?? t("zero");
  const label = run ? `#${run.number}` : t("This run");
  const extra = [[t("Win rate"), run?.stats?.winRate ?? tiles.winRate, (value: number) => pct(value, 1)], [t("Profit factor"), run?.stats?.profitFactor, (value: number) => nf(value, 2)]] as const;
  return <>
    <Sec title={t("Summary")} sub={data.annualization === 365
      ? t("{period}annualized over {days} days, risk-free {rate}, benchmark {benchmark}", {period, days: data.annualization, rate, benchmark: names.long})
      : t("{period}annualized over {days} trading days, risk-free {rate}, benchmark {benchmark}", {period, days: data.annualization, rate, benchmark: names.long})}>
      {first && <div className="qx-differs"><b>{t("What differs")}</b><span>{compared.map(item => `${item.label}: ${(item.whatDiffers ?? []).join(", ") || t("same period")}`).join("; ")}</span></div>}</Sec>
    <div className="qx-grid2">
      <Sec title={t("Return statistics")}><Grid head={["", label, bench, ...compared.map(item => item.label)]}
        rows={[...rows.filter(item => item.name !== "Days").map(item => [statisticLabels[rowName(item.name)] ?? item.name, toned(item.name, item.run), item.benchmark == null ? "" : toned(item.name, item.benchmark), ...compared.map(other => toned(item.name, other.rows?.[item.name]))]),
          ...extra.map(([name, value, show]) => [name, value == null ? "—" : show(value as number), "", ...compared.map(other => other.stats && name === t("Win rate") && other.stats.winRate != null ? show(other.stats.winRate) : other.stats && name === t("Profit factor") && other.stats.profitFactor != null ? show(other.stats.profitFactor) : "")])]} /></Sec>
      <Sec title={t("Against {long}", { long: names.long })}><Grid head={["", label, ...compared.map(item => item.label)]} rows={[
        [t("Alpha, a year"), against.alpha == null ? "—" : <span key="a" className={tone(against.alpha)}>{pc(against.alpha, 1)}</span>, ...compared.map(other => other.against?.alpha == null ? "—" : pc(other.against.alpha, 1))],
        [t("Beta to {benchmark}", { benchmark: bench.replace(/ hold$/, "") }), num(against.beta, 2), ...compared.map(other => num(other.against?.beta, 2))], [t("Correlation"), num(against.correlation, 2), ...compared.map(other => num(other.against?.correlation, 2))],
        [t("Tracking error"), against.trackingError == null ? "—" : pct(against.trackingError, 1), ...compared.map(other => other.against?.trackingError == null ? "—" : pct(other.against.trackingError, 1))],
        [t("Information ratio"), num(against.informationRatio, 2), ...compared.map(other => num(other.against?.informationRatio, 2))]]} />
        </Sec>
    </div>
    {Object.keys(data.userStats ?? {}).length > 0 && <Block title={t("Strategy statistics")}><Grid head={["", t("Value")]} rows={Object.entries(data.userStats).map(([name, value]) => [name, num(Number(value), 4)])} /></Block>}
    {/* The engine's own figures, each under the way the engine counts it (its group says how, and the money's currency); none repeats a name above. */}
    {engine.length > 0 && <Sec title={t("The engine's own figures")}><div className="qx-engine">{groups.map(group => {
      const name = group.replace(/\s*\(.*\)$/, ""), detail = group.match(/\((.*)\)$/)?.[1] ?? "";
      return <Block key={group} title={engineLabels[name] ?? name} sub={engineLabels[detail] ?? detail}>
      <Grid head={["", label]} rows={engine.filter(item => item.group === group).map(item => [engineLabels[item.name] ?? item.name, engineValue(item.name, item.value as number)])} /></Block>; })}</div></Sec>}</>;
}
const day = (value: unknown): string | undefined => typeof value === "string" && /^\d{4}-\d{2}-\d{2}/.test(value) ? dayText(value.slice(0, 10)) : typeof value === "string" ? value : undefined;
export function EquitySection({data, missing, picking, title = t("Equity and drawdowns"), onDrawdown, multi}: {data: any; missing: Record<string, string>; picking?: Picking[]; title?: string; multi?: boolean; onDrawdown?(span: {n: number; from: number; to: number}): void}) {
  const equity = data.figures?.equity && !("unavailable" in data.figures.equity) ? withPicking(data.figures.equity, picking) : data.figures?.equity;
  return <><TxRegionHead title={title} sub={data.benchmarkLabel ? t("against {benchmarkLabel}", { benchmarkLabel: data.benchmarkLabel }) : undefined} />
    <Fig fig={equity} why={missing.equity} label={t("Equity")} size="m" /><Fig fig={data.figures?.drawdown} why={missing.drawdown} label={t("Underwater")} size="dd" />
    {data.drawdowns?.length > 0 && <Block title={t("Worst drawdowns")}><Grid head={["#", t("Depth"), t("Peak"), t("Trough"), t("Recovered"), t("Days"), ""]} left={[0, 2, 3, 4, 6]} rows={data.drawdowns.map((row: any, at: number) => [at + 1, pc(row.depth ?? row.drawdown, 1), day(row.peak) ?? mdy(row.from ?? row.start), day(row.trough) ?? mdy(row.to ?? row.end), day(row.recovered) ?? <span key="r" className="fx-t3">{t("not yet")}</span>, row.days ?? "",
      onDrawdown ? <button key="go" type="button" className="tx-btn qx-dd-go" data-variant="quiet" data-size="sm" title={(multi ? t("Show the rebalances in this drawdown") : t("Show the trades in this drawdown"))} onClick={() => onDrawdown({n: at + 1, from: Date.parse(String(row.peak).slice(0, 10) + "T00:00:00Z"), to: Date.parse(String(row.recovered ?? row.trough).slice(0, 10) + "T23:59:59Z")})}>{multi ? t("Rebalances") : t("Trades")} <TxIcon name="chevron" /></button> : ""])} /></Block>}</>;
}
/** A month's cell in the heat table: up or down colour, deeper the larger the month against the largest one; a flat month has none. */
const heat = (value: number, top: number) => Math.abs(value) < 5e-5 ? undefined : ({background:`color-mix(in srgb, var(${value >= 0 ? "--tx-up" : "--tx-down"}) ${Math.round(8 + 52 * Math.min(1, Math.abs(value) / (top || 1)))}%, transparent)`});
/** A month or year of the run: its trades on Work (, as a drawdown's). */
export interface Stretch {n?: number; label?: string; from: number; to: number}
export function Periods({data, onPeriod, multi}: {data: any; onPeriod?(stretch: Stretch): void; multi?: boolean}) {
  const months = Array.from({length: 12}, (_, month) => new Date(Date.UTC(2000, month, 1)).toLocaleDateString(intlLocale(), {month: "short", timeZone: "UTC"}));
  const what = multi ? "rebalances" : "trades";
  const go = (label: string, from: number, to: number) => onPeriod?.({label, from, to: to - 1});
  const cell = (text: string, title: string, label: string, from: number, to: number) => onPeriod
    ? <button type="button" className="qx-cellgo" title={t("{title}: show the {what}", { title, what })} onClick={() => go(label, from, to)}>{text}</button> : text;
  const table = (data.table ?? []) as {year: number; months: (number | null)[]; total: number | null; benchmark: number | null}[];
  const top = Math.max(0, ...table.flatMap(row => row.months.map(value => Math.abs(value ?? 0))));
  return <><div className="qx-grid2 qx-periods"><Sec title={t("Annual returns")}><Fig fig={data.figures?.yearly} label={t("Annual returns")} size="s" /></Sec>
    <Sec title={t("Monthly returns")} sub="%"><div className="fx-rtable"><table className="fx-g qx-heat" aria-label={t("Monthly returns")}><thead><tr><th />{months.map(name => <th key={name}>{name}</th>)}<th>{t("Year")}</th><th>{benchNames(data.benchmarkLabel).short}</th></tr></thead>
      <tbody>{table.map(row => <tr key={row.year}><th className="l">{cell(String(row.year), String(row.year), String(row.year), Date.UTC(row.year, 0, 1), Date.UTC(row.year + 1, 0, 1))}</th>
        {row.months.map((value, at) => value == null ? <td key={at} /> : <td key={at} style={heat(value, top)} title={pc(value, 2)}>{cell(sgn(value * 100, 1), `${months[at]} ${row.year} ${pc(value, 2)}`, `${months[at]} ${row.year}`, Date.UTC(row.year, at, 1), Date.UTC(row.year, at + 1, 1))}</td>)}
        <td>{sgnCell(row.total == null ? null : row.total * 100, 1, "%")}</td><td className="fx-t3">{row.benchmark == null ? "" : sgn(row.benchmark * 100, 1) + "%"}</td></tr>)}</tbody></table></div></Sec></div>
    </>;
}
export function Rolling({data, bench}: {data: any; bench?: string | null}) {
  if (data.longerThanRun) return <Why text={t("Window longer than the run")} />;
  // Side by side as the draft, and still one section: the three share the time axis, the cursor and one key in the section's head.
  const panes: [string, Figure | null | undefined, string][] = [[t("Sharpe"), data.figures?.sharpe, t("Rolling Sharpe")], [t("Beta to {benchmark}", { benchmark: benchNames(bench).short.replace(/ hold$/, "") }), data.figures?.beta, t("Rolling beta")], [t("Volatility"), data.figures?.volatility, t("Rolling volatility")]];
  return <div className="qx-grid3">{panes.map(([title, fig, label]) => <div key={label} className="qx-pane"><h4>{title}</h4><Fig fig={fig} label={label} size="s" /></div>)}</div>;
}
export function LongShort({data, multi}: {data: any; multi?: boolean}) {
  // The draft's Exposure: gross as an area under the net line, and the turnover beside it. A portfolio also draws each leg against the market above them.
  const exposure = <div className="qx-pane"><h4>{t("Gross and net exposure")}</h4><Fig fig={data.figures?.exposure} label={t("Exposure")} size="s" /></div>;
  const turnover = <div className="qx-pane"><h4>{t("Turnover")}</h4><Fig fig={data.figures?.turnover} label={t("Turnover")} size="s" /></div>;
  if (!multi) return <div className="qx-grid2">{exposure}{turnover}</div>;
  return <><div className="qx-grid2"><div className="qx-pane"><h4>{t("Each leg against the market")}</h4><Fig fig={data.figures?.legs} label={t("Legs")} size="s" /></div>{exposure}</div><div className="qx-grid1">{turnover}</div></>;
}
export function TradesSection({data, onTrade, multi, compared = []}: {data: any; onTrade?(id: number): void; multi?: boolean; compared?: ComparedRun[]}) {
  const split = data.split ?? {};
  const money = (value: number | null | undefined): ReactNode => value == null ? "—" : <span className={tone(value)}>{sgn(value, 2)}</span>;
  // A trade's number opens it on Work for this run's own columns; a compared run's trade numbers are that run's, so they stay text.
  const win = (value: {pnl: number; trade: number} | undefined, own = false): ReactNode => value ? <>{money(value.pnl)} {onTrade && own
    ? <button type="button" className="tx-btn" data-variant="quiet" data-size="sm" title={t("Show this trade on Work")} onClick={() => onTrade(value.trade)}>#{value.trade}</button>
    : <span className="fx-t3">#{value.trade}</span>}</> : "—";
  const cells = (show: (stats: any) => ReactNode): ReactNode[] => [show(split.all ?? {}), show(split.long ?? {}), show(split.short ?? {}), ...compared.map(other => show(other.tradeStats ?? {}))];
  const stat = (key: string, show: (value: any) => ReactNode) => (stats: any) => stats?.[key] == null ? "—" : show(stats[key]);
  const row = (label: string, show: (stats: any) => ReactNode): ReactNode[] => [label, ...cells(show)];
  return <>
    <Sec title={multi ? t("Positions") : t("Trades")} sub={multi ? t("round trips: entry to exit or side change, net of their own costs") : t("closed round trips")}><Grid head={[multi ? t("Round trips") : t("Statistics"), "All", t.c("position side", "Long"), t.c("position side", "Short"), ...compared.map(other => t("{label}, all", { label: other.label }))]} rows={[
      row(t("Trades"), stat("trades", value => nf(value, 0))), row(t("Net P&L"), stat("pnl", value => money(value))), row(t("Win rate"), stat("winRate", value => pct(value, 1))), row(t("Profit factor"), stat("profitFactor", value => nf(value, 2))),
      row(t("Payoff, average win ÷ loss"), stat("payoff", value => nf(value, 2))), row(t("Average win"), stat("averageWin", value => money(value))), row(t("Average loss"), stat("averageLoss", value => money(value))), row(t("Expectancy a trade"), stat("expectancy", value => money(value))),
      ...(["largestWin", "largestLoss"] as const).map(key => [key === "largestWin" ? t("Largest win") : t("Largest loss"), ...[split.all, split.long, split.short].map(stats => win(stats?.[key], true)),
        ...compared.map(other => win(other.tradeStats?.[key]))]), row(t("Most losses in a row"), stat("mostLossesInARow", value => String(value))),
      row(t("Bars held"), stat("barsHeld", value => nf(value, 1))), row(t("Bars held, winners"), stat("barsHeldWinners", value => nf(value, 1))), row(t("Bars held, losers"), stat("barsHeldLosers", value => nf(value, 1))),
      row(t("Average MAE"), stat("averageMae", value => pc(value, 2))), row(t("Average MFE"), stat("averageMfe", value => pc(value, 2)))]} /></Sec>
    <Block title={t("By exit")}><Grid head={[t.c("trade", "Exit"), t("Trades"), t("Net P&L"), t("Win rate"), t("Bars held")]} left={[0]} rows={(data.byExit ?? []).map((row: any) => [exitLabel(row.exit), row.trades, sgnCell(row.pnl, 0), pct(row.winRate, 0), num(row.averageBars, 1)])} /></Block>
    <div className="qx-grid2"><Sec title={multi ? t("Return a position") : t("Return a trade")} sub={t("% of notional")}><Fig fig={data.figures?.returns} label={t("Return of each trade")} size="s" /></Sec>
      <Sec title={multi ? t("Days held") : t("Bars held")}><Fig fig={data.figures?.held} label={t("Bars held")} size="s" /></Sec></div>
    <div className="qx-grid2"><Sec title={t("Adverse excursion and result")} sub={t("MAE · return")}><Fig fig={data.figures?.mae} label={t("Worst move against")} size="s" /></Sec><Sec title={t("Favourable excursion and result")}><Fig fig={data.figures?.mfe} label={t("Best move for")} size="s" /></Sec></div>
    </>;
}
const ORDER_TYPES: Record<string, string> = {
  "Market": t.c("order type", "Market"),
  "Limit": t.c("order type", "Limit"),
  "Stop market": t.c("order type", "Stop market"),
  "Stop limit": t.c("order type", "Stop limit"),
  "Take profit": t.c("order type", "Take profit"),
  "Take profit limit": t.c("order type", "Take profit limit"),
  "Trailing stop": t.c("order type", "Trailing stop"),
  "Trailing stop limit": t.c("order type", "Trailing stop limit"),
  "Market to limit": t.c("order type", "Market to limit")
};
const ORDER_STATES: Record<string, string> = {
  "All": t.c("order status", "All"),
  "Filled": t.c("order status", "Filled"),
  "Rejected": t.c("order status", "Rejected"),
  "Canceled": t.c("order status", "Canceled"),
  "Expired": t.c("order status", "Expired"),
  "Partly filled": t.c("order status", "Partly filled"),
  "Open": t.c("order status", "Open"),
  "Triggered": t.c("order status", "Triggered"),
  "Unfilled at end": t.c("order status", "Unfilled at end"),
  "Liquidation stop": t.c("order status", "Liquidation stop")
};

export function OrdersSection({api, runId, counts, onOrder}: {api: StrategyApi; runId: string; counts: Record<string, number>; onOrder?(order: Record<string, any>): void}) {
  const [status, setStatus] = useState("All");
  // All, then only the states the run has, in the backend's order ("Unfilled at end": sent on its last bar, or still working when its data ran out;
  // "Liquidation stop": TALYX's own stops at the liquidation price, apart from the strategy's orders).
  const all = Object.values(counts).reduce((sum, value) => sum + value, 0);
  const statuses = ["All", ...Object.keys(counts)];
  const [rows, setRows] = useState<Record<string, any>[]>([]);
  const [total, setTotal] = useState(0);
  const [next, setNext] = useState<number | null>(null);
  const load = (offset: number) => api.orders(runId, {status, offset, limit: 500}).then(page => {setRows(current => offset ? [...current, ...page.rows] : page.rows); setTotal(page.total); setNext(page.nextOffset);}, () => undefined);
  useEffect(() => {void load(0);}, [runId, status]);
  return <><div className="qx-tfilters"><TxSegmented label={t("Order status")} value={status} options={statuses.map(value => ({value, label: `${ORDER_STATES[value] ?? value} ${value === "All" ? all : counts[value]}`}))} onChange={setStatus} />
    <span className="tx-spacer" /><span className="fx-t3">{t("{total} of {all} orders", { total, all })}</span></div>
    <div className="fx-rtable"><table className="fx-g" aria-label={t("Orders")}><thead><tr><th className="l">{t("Placed, UTC")}</th><th className="l">{t("Side")}</th><th className="l">{t("Type")}</th><th>{t("Qty")}</th><th>{t("Price")}</th><th className="l">{t("Time in force")}</th><th className="l">{t("Status")}</th><th className="l">{t("Reason")}</th><th className="l">{t("Linked")}</th></tr></thead>
      <tbody>{rows.map(row => <tr key={row.id} onClick={() => onOrder?.(row)}><td className="l">{utcStamp(row.time)}</td><td className="l">{row.side}</td><td className="l">{ORDER_TYPES[row.type] ?? row.type}</td><td>{num(row.qty, 4)}</td><td>{row.price == null ? "—" : nf(row.price, 2)}</td>
        <td className="l">{row.tif}</td><td className="l" data-status={row.status}>{ORDER_STATES[row.status] ?? row.status}</td><td className="l">{row.reason ?? ""}</td><td className="l">{row.linked ?? ""}</td></tr>)}</tbody></table></div>
    {next !== null && <TxButton variant="quiet" size="sm" onClick={() => void load(next)}>{t("Load more")}</TxButton>}</>;
}
export function AccountSection({data}: {data: any}) {
  const events = (data.marginEvents ?? []) as {time: number; event: string; detail: string}[];
  return <><Fig fig={data.figures?.balance} label={t("Balance and margin")} />
    {events.length ? <Block title={t("Margin events")}><Grid head={[t("Time, UTC"), t("Event"), t("Detail")]} left={[0, 1, 2]} rows={events.map(row => [utcStamp(row.time), row.event, row.detail])} /></Block> : <p className="fx-t3">{t("No margin events")}</p>}</>;
}
export function SectorsSection({data}: {data: any}) {
  const sectors = (data.sectors ?? []) as {sector: string; net: number; gross: number; costs: number; names: number}[];
  const rank = (title: string, rows: any[]) => <Block title={title}><Grid head={[t("Name"), t("Net"), t("Costs"), t("Trades")]} rows={rows.map(row => [row.name, sgnCell(row.pnl, 0), num(row.fees, 0), row.trades])} /></Block>;
  return <>{sectors.length > 0 && <Block title={t("By sector")}><Grid head={[t("Sector"), t("Net"), t("Gross"), t("Costs"), t("Names")]} rows={sectors.map(row => [row.sector, sgnCell(row.net, 0), num(row.gross, 0), num(row.costs, 0), row.names])} /></Block>}
    {rank(t("Most made"), data.mostMade ?? [])}{rank(t("Most lost"), data.mostLost ?? [])}</>;
}
export function InstrumentsSection({data}: {data: any}) {
  const rows = (data.rows ?? []) as {instrument: string; trades: number; pnl: number; winRate: number | null; profitFactor: number | null; perTrade: number; fees: number; spread: number; impact: number; funding: number}[];
  const sum = (key: "trades" | "pnl" | "fees" | "spread" | "impact" | "funding") => rows.reduce((total, row) => total + row[key], 0);
  const cost = (value: number) => nf(-value, 2);  // a cost is not a loss to colour
  const shortName = (name: string) => name.split(/[/-]/)[0];
  return <><Grid head={[t("Contract"), t("Trades"), t("Net P&L"), t("Win rate"), t("Profit factor"), t("A trade"), t("Fees"), t.c("trading costs", "Spread"), t("Impact"), t("Funding")]}
    rows={[...rows.map(row => [shortName(row.instrument), row.trades, sgnCell(row.pnl, 2), row.winRate == null ? "—" : pct(row.winRate, 1), num(row.profitFactor, 2), sgnCell(row.perTrade, 2), cost(row.fees), cost(row.spread), cost(row.impact), cost(row.funding)]),
      [t("Both"), sum("trades"), sgnCell(sum("pnl"), 2), "", "", "", cost(sum("fees")), cost(sum("spread")), cost(sum("impact")), cost(sum("funding"))]]} />
    <Block title={t("Cumulative P&L by contract")}><Fig fig={data.figures?.cumulative} label={t("Cumulative P&L")} size="m" /></Block></>;
}
export function StyleSection({data}: {data: any}) {
  return <><div className="qx-sub"><TxTag tone="muted">{data.version}</TxTag></div><Grid head={[t("Style"), t("Exposure"), t("Contribution a year")]} left={[0]}
    rows={[...(data.exposures ?? []).map((row: any) => [row.style, num(row.exposure, 2), row.contribution == null ? "—" : pc(row.contribution, 1)]), [t("Specific"), "", data.specific == null ? "—" : pc(data.specific, 1)]]} /></>;
}
export function CostsSection({data, number, onCapacity}: {data: any; number?: number; onCapacity?(): void}) {
  const rows = (data.breakdown ?? []) as {name: string; amount: number; shareOfGross: number | null; shareOfCapital: number | null; off?: boolean}[];
  const capacity = data.capacity as {unavailable?: string} | {capital: number | string; cagr: number | null; sharpe: number | null}[] | undefined;
  const top = Math.max(...rows.map(row => Math.abs(row.amount)), 1e-9);
  const total = (name: string) => name === "Gross P&L" || name === "Net P&L";
  return <><div className="qx-grid2"><Sec title={t("From gross to net")} sub={data.currency || undefined}><div className="qx-bars" role="table" aria-label={t("From gross to net")}>{rows.map(row => <div key={row.name} className="qx-barrow" role="row" data-total={total(row.name) ? "" : undefined}>
      <span>{row.name === "Gross P&L" ? t("Gross P&L") : t(row.name)}</span>
      <span className="qx-bartrack"><i style={{width: `${Math.max(Math.abs(row.amount) / top * 100, row.amount ? 1 : 0)}%`}} data-kind={total(row.name) ? "total" : "cost"} /></span>
      <span className={total(row.name) && row.amount ? (row.amount > 0 ? "up" : "down") : undefined}>{row.off ? t("off") : nf(row.amount, 2)}</span>
      <span className="fx-t3">{total(row.name) ? (row.shareOfCapital == null ? "" : pc(row.shareOfCapital, 1)) : row.shareOfGross == null ? "" : pct(row.shareOfGross, 0)}</span></div>)}</div></Sec>
    <Sec title={t("Capacity")}>{Array.isArray(capacity) && capacity.length && data.capacityFigure ? <Fig fig={data.capacityFigure} label={t("Capacity")} size="s" />
      : <div className="qx-note2">{(number ? t("Not computed for #{number}.", { number }) : t("Not computed."))}{onCapacity && <TxButton size="sm" onClick={onCapacity}>{t("Rerun at larger capital")}</TxButton>}</div>}</Sec></div>
    {Array.isArray(data.sensitivity) && data.sensitivity.length > 0 && <Block title={t("Cost sensitivity")}><Grid head={[t("Costs"), t.c("performance", "Return"), "CAGR", t("Sharpe"), t("Max drawdown")]} left={[0]}
      rows={data.sensitivity.map((row: any) => [row.costs, row.totalReturn == null ? "—" : <span className={tone(row.totalReturn)}>{pc(row.totalReturn, 1)}</span>, row.cagr == null ? "—" : <span className={tone(row.cagr)}>{pc(row.cagr, 1)}</span>, num(row.sharpe, 2), row.maxDrawdown == null ? "—" : pc(row.maxDrawdown, 1)])} /></Block>}
        {data.doubled ? <Block title={t("Costs doubled")}><Grid head={["", t("As run"), t("Costs × 2")]} rows={[["CAGR", data.doubled.cagrNow == null ? "—" : pc(data.doubled.cagrNow, 1), data.doubled.cagr == null ? "—" : pc(data.doubled.cagr, 1)], [t("Sharpe"), num(data.doubled.sharpeNow), num(data.doubled.sharpe)]]} /></Block> : null}</>;
}
const crisisNames: Record<string, string> = {
  "2018 Q4 sell-off": t("2018 Q4 sell-off"),
  "COVID crash": t("COVID crash"),
  "2022 rate hikes": t("2022 rate hikes"),
  "2023 regional banks": t("2023 regional banks"),
  "Aug 2024 yen carry unwind": t("Aug 2024 yen carry unwind"),
  "2025 tariff shock": t("2025 tariff shock"),
  "May 2021 crash": t("May 2021 crash"),
  "LUNA collapse": t("LUNA collapse"),
  "FTX collapse": t("FTX collapse"),
  "Aug 2024 unwind": t("Aug 2024 unwind"),
  "Oct 2025 deleveraging": t("Oct 2025 deleveraging"),
};

export function CrisisSection({data, run, bench}: {data: any; run?: StrategyRun; bench?: string | null}) {
  // As the draft: the periods this backtest covers, with the run's number and the benchmark's short name as column heads.
  const rows = ((data.periods ?? []) as any[]).filter(row => row.covered);
  if (!rows.length) return <Why text={t("None inside this run's period")} />;
  return <Grid head={[t("Period"), t("From"), t("To"), run ? `#${run.number}` : t("This run"), benchNames(bench).short, t("Max drawdown inside")]} left={[0, 1, 2]} rows={rows.map(row =>
    [crisisNames[row.name] ?? row.name, day(row.from) ?? "", day(row.to) ?? "", sgnCell(row.return == null ? null : row.return * 100, 1, "%"), row.benchmark == null ? "—" : sgnCell(row.benchmark * 100, 1, "%"), row.maxDrawdown == null ? "—" : pc(row.maxDrawdown, 1)])} />;
}
/** A setting's value as the draft writes it: on/off, numbers with separators. */
const show = (value: unknown) => value === null || value === undefined ? "—" : typeof value === "boolean" ? (value ? t("on") : t("off"))
  : typeof value === "number" ? value.toLocaleString("en-US", {maximumFractionDigits: 4}) : typeof value === "object" ? JSON.stringify(value) : String(value);
export function SettingsSection({data, run, parameters, onUse}: {data: any; run: StrategyRun; parameters?: Parameter[]; onUse(): void}) {
  const layers = (data.layers ?? []) as {layer: string; setting: string; then: unknown; now: unknown}[];
  const differs = Object.keys(data.differs ?? {}).length;
  return <><Grid head={[t("Layer"), t("Setting"), `#${run.number}`, t.c("settings", "Now")]} left={[0, 1, 2, 3]} rows={layers.map(row => [t(row.layer), settingName(row.setting, parameters), show(row.then), <span key="n" data-differs={JSON.stringify(row.then) !== JSON.stringify(row.now) ? "" : undefined}>{show(row.now)}</span>])} />
    <div className="qx-note2"><TxButton size="sm" onClick={onUse} disabled={differs === 0}>{t("Use #{number}'s settings", { number: run.number })}</TxButton><span>{differs > 0 ? t("{differs} differ from the form now", { differs }) : t("Same as the form now")}</span></div></>;
}

/** The contents list names each section as the draft does; a portfolio calls two of them differently. */
export const SECTION_TITLES: Record<string, [string, string?]> = {summary: [t("Summary")], equity: [t("Equity and drawdowns")], periods: [t("Returns by period")], rolling: [t("Rolling")], longShort: [t("Exposure"), t("Legs and exposure")], trades: [t("Trades"), t("Positions")],
  orders: [t("Orders")], account: [t("Account")], sectors: [t("Sectors and stocks")], instruments: [t("Instruments")], style: [t("Style and attribution")], costs: [t("Costs and capacity")], crisis: [t("Crisis periods")], settings: [t("Settings")]};

export function ResultsView({api, run, picking, view, section, onSection, onStar, onNotebook, onOrder, onTrade, onDrawdown, onCapacity, onUse, compare, multi, picker, onWork, parameters, pending}: {
  parameters?: Parameter[];
  /** Another run is being read: this one stays as it is, marked, until that one is in. */
  pending?: boolean;
  api: StrategyApi; run: StrategyRun; picking?: Picking[]; view: ResultsData | null; section: string | undefined; onSection(id: string): void; onStar(): void; onNotebook?(): void; compare?: ReactNode; multi?: boolean; picker?: ReactNode; onWork?(): void;
  onOrder(order: Record<string, any>): void; onTrade(id: number): void; onDrawdown?(span: Stretch): void; onCapacity?(): void; onUse(): void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [current, setCurrent] = useState(section ?? "summary");
  // A jump puts the section's title the same distance under the run's head, whatever the section begins with.
  const land = (id: string): boolean => {
    const root = box.current, element = root?.querySelector(`#qx-sec-${id}`);
    if (!root || !element) return false;
    const title = element.querySelector(".tx-regionhead, h3") ?? element;
    root.scrollTop += title.getBoundingClientRect().top - root.getBoundingClientRect().top - titleAt(root);
    return true;
  };
  // The section on screen follows the scroll; a jump from outside scrolls to its section.
  useEffect(() => {if (section && land(section)) setCurrent(section);}, [section, view?.toc.length]);
  // The section on screen is the lowest one whose top has reached the top of the scrolling box (a jump sets it at once); two side by side: the first.
  const jumping = useRef(0);
  useEffect(() => {
    const root = box.current;
    if (!root) return;
    const follow = () => {
      if (Date.now() < jumping.current) return;
      const edge = root.getBoundingClientRect().top + titleAt(root) + 1;
      let found: string | null = null, at = -Infinity;
      root.querySelectorAll("section[id^=qx-sec-]").forEach(item => {const top = item.getBoundingClientRect().top; if (top <= edge && top > at + 1) {found = item.id.replace("qx-sec-", ""); at = top;}});
      if (found) setCurrent(found);
    };
    root.addEventListener("scroll", follow, {passive: true});
    return () => root.removeEventListener("scroll", follow);
  }, [view?.toc.length]);
  // The page's actions as stable functions that call the latest ones given: the sections draw again only when what they show changes, not on every render of the page.
  const latest = useRef({onOrder, onTrade, onDrawdown, onCapacity, onUse}); latest.current = {onOrder, onTrade, onDrawdown, onCapacity, onUse};
  const hasDrawdown = Boolean(onDrawdown), hasCapacity = Boolean(onCapacity);
  const actions = useMemo<SectionActions>(() => ({onOrder: order => latest.current.onOrder(order), onTrade: id => latest.current.onTrade(id), onUse: () => latest.current.onUse(),
    onDrawdown: hasDrawdown ? span => latest.current.onDrawdown?.(span) : undefined, onCapacity: hasCapacity ? () => latest.current.onCapacity?.() : undefined}), [hasDrawdown, hasCapacity]);
  if (view === null) return <div className="qx-results"><p className="fx-empty">{t("Reading the results…")}</p></div>;
  const titled = (item: {id: string; title: string}) => sectionTitle(item, multi);
  const goTo = (id: string) => {
    setCurrent(id);
    jumping.current = Date.now() + 700;
    land(id);
    onSection(id);
  };
  return <div className="qx-results">
    <nav className="qx-toc" aria-label={t("Sections")}>{view.toc.map(item => <TxButton key={item.id} variant="row" aria-current={current === item.id ? "true" : undefined} onClick={() => goTo(item.id)}>{titled(item)}</TxButton>)}</nav>
    <div className="qx-toc-select"><TxSelect aria-label={t("Section")} value={current} onChange={event => goTo(event.target.value)}>
      {view.toc.map(item => <option key={item.id} value={item.id}>{titled(item)}</option>)}
    </TxSelect></div>
    <div className="qx-rscroll" ref={box} aria-busy={pending || undefined}>
      <header className="qx-rhead">{onWork && <TxButton size="sm" variant="quiet" onClick={onWork}><TxIcon name="arrow-left" />{t("Work")}</TxButton>}{picker ?? <b>#{run.number}</b>}<span className="fx-t3">{when(run.createdAt)}</span>
        <span className="fx-t3 qx-per">{dataToText(run.settings.data.since)} – {dataToText(run.settings.data.until)}{dataToText(run.dataTo) ? t(", data to {dataTo}", { dataTo: dataToText(run.dataTo) }) : ""}</span>
        {compare}<span className="tx-spacer" />
        {onNotebook && <TxButton size="sm" variant="quiet" onClick={onNotebook}>{t("Notebook")}</TxButton>}<TxButton size="sm" variant="quiet" aria-pressed={run.starred} onClick={onStar}>{run.starred ? t("★ Kept") : t("☆ Keep")}</TxButton></header>
      <div className="qx-rsecs"><Sections api={api} run={run} view={view} picking={picking} multi={multi} parameters={parameters} actions={actions} /></div>
    </div>
  </div>;
}

/** Sections that keep the page's whole width where the others sit two to a row: wide tables, and several charts across. The rest pair in the contents'
 *  order (equity beside the returns by period, the account beside the code's plots, style beside costs, crisis periods beside the settings). */
const WIDE = new Set(["summary", "rolling", "longShort", "trades", "orders", "instruments"]);
interface SectionActions {onOrder(order: Record<string, any>): void; onTrade(id: number): void; onUse(): void; onDrawdown?(span: Stretch): void; onCapacity?(): void}
const sectionTitle = (item: {id: string; title: string}, multi?: boolean) => (multi ? SECTION_TITLES[item.id]?.[1] : undefined) ?? SECTION_TITLES[item.id]?.[0] ?? item.title;
const Sections = memo(function Sections({api, run, view, picking, multi, parameters, actions}: {api: StrategyApi; run: StrategyRun; view: ResultsData; picking?: Picking[]; multi?: boolean; parameters?: Parameter[]; actions: SectionActions}) {
  const {onOrder, onTrade, onUse, onDrawdown, onCapacity} = actions;
  const sections = view.sections, missing = view.missing;
  const titled = (item: {id: string; title: string}) => sectionTitle(item, multi);
  const body = (id: string): ReactNode => {
    const data = sections[id];
    if (data === undefined) return <Why text={missing[id] ?? t("This section was not built")} />;
    switch (id) {
      case "summary": return <Summary data={data} run={run} compared={view.compared} />;
      case "equity": return <EquitySection data={data} missing={missing} picking={picking} title={titled({id, title: ""})} onDrawdown={onDrawdown} multi={multi} />;
      case "periods": return <Periods data={data} onPeriod={onDrawdown} multi={multi} />;
      case "rolling": return <Rolling data={data} bench={sections.summary?.benchmarkLabel ?? sections.periods?.benchmarkLabel} />;
      case "longShort": return <LongShort data={data} multi={multi} />;
      case "trades": return <TradesSection data={data} onTrade={onTrade} multi={multi} compared={view.compared} />;
      case "orders": return <OrdersSection api={api} runId={run.id} counts={data.counts ?? {}} onOrder={onOrder} />;
      case "account": return <AccountSection data={data} />;
      case "sectors": return <SectorsSection data={data} />;
      case "instruments": return <InstrumentsSection data={data} />;
      case "style": return <StyleSection data={data} />;
      case "costs": return <CostsSection data={data} number={run.number} onCapacity={onCapacity} />;
      case "crisis": return <CrisisSection data={data} run={run} bench={sections.summary?.benchmarkLabel ?? sections.periods?.benchmarkLabel} />;
      case "settings": return <SettingsSection data={data} run={run} parameters={parameters} onUse={onUse} />;
      default: return null;
    }
  };
  return <>
      {view.toc.map(item => <section key={item.id} id={`qx-sec-${item.id}`} className="qx-section" data-wide={WIDE.has(item.id) ? "" : undefined}>{item.id !== "summary" && item.id !== "equity" && item.id !== "trades" && <TxRegionHead title={titled(item)} sub={item.id === "longShort" ? (multi ? undefined : t("share of equity, daily")) : item.id === "rolling" && sections.rolling?.window ? t("{days}-day window", { days: sections.rolling.window }) : item.id === "settings" ? t("of #{number}, against the form now", { number: run.number }) : undefined} />}{body(item.id)}</section>)}
      {Object.keys(view.figuresFailed).length > 0 && <section className="qx-section" data-wide=""><h3>{t("Charts that could not be drawn")}</h3>{Object.entries(view.figuresFailed).map(([key, why]) => <Why key={key} text={`${key}: ${why}`} />)}</section>}</>;
});
