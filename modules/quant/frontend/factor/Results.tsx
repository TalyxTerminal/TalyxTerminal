/* A factor's Results: the full report of one run, a tab at a time. Work's numbers come first; the tabs build in the background after them and say so.
   Every figure is the backend's Plotly figure shown by Plot; a figure that could not be drawn says why in its place. */
import { t } from "../i18n";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  TxButton, TxCompare, TxFailure, TxIcon, TxIconButton, TxLive, TxRegionHead, TxRunPicker, TxRunStrip, TxSkeleton, TxTabs, TxTag, type TxMenuItem,
} from "@talyx/sdk";
import type { DataSettings } from "../objects";
import {
  usable, type ResultsTabName, type FactorApi, type FactorCostsTab, type FactorEventTab, type FactorIcTab, type FactorIndustryTab, type FactorResultsState, type FactorResultsView,
  type FactorReturnsTab, type FactorRun, type FactorStyleTab, type FactorTurnoverTab, type ResultsFigure, type ResultsPageTab,
} from "./api";
import { dayText, mdy, my, nf, pc, pct, secondsText, sgn, tone, when } from "./format";
import { PublishButton, UseMenu, type ResultHandlers } from "./Result";
import { DataNotes } from "../shared/DataNotes";
import { Plot, type PlotPoint } from "../chart/Plot";

import { RunPrice } from "../shared/RunPrice";

export const RESULT_TABS: {value: ResultsPageTab; label: string}[] = [
  {value: "returns", label: t("Returns")}, {value: "price", label: t("On price")}, {value: "ic", label: "IC"}, {value: "industry", label: t("Industry and size")}, {value: "style", label: t("Style")},
  {value: "turnover", label: t("Turnover")}, {value: "costs", label: t("Costs")}, {value: "event", label: t("Event study")}, {value: "settings", label: t("Settings")},
];
const MAX_COMPARED = 4;
const BUILDING_NAME: Record<string, string> = {returns: t("the returns"), ic: t("the IC tables"), industry: t("industry and size"), style: t("style correlations"), turnover: "turnover", costs: "costs", event: t("the event study")};

export interface ResultsHandlers extends ResultHandlers {
  /** A day clicked in a chart: the values of that day, in Work. */
  showDay(ms: number): void;
  star(run: FactorRun): void;
  build(run: FactorRun, cell?: number): void;
  /** The run's settings back into the settings column, then Work. */
  reuse(run: FactorRun): void;
}

// ---------------------------------------------------------------- reading one tab
interface Loaded {key: string; view: FactorResultsView | null; error: string | null}

/** A tab is read only when it is the selected one and its build is done (or failed, which the answer explains); a sweep's combination is always read, its own state comes with the answer. */
export const wantsTab = (tab: ResultsPageTab, status: string | undefined, bycell: boolean) => tab !== "settings" && tab !== "price" && (bycell || status === "done" || status === "failed");

/** The tab asked for once its build is done (or, for a sweep's combination, always, since the combination's own state comes with the answer). */
function useTab(fapi: FactorApi, run: FactorRun, tab: ResultsPageTab, vs: string[], cell: number | undefined, version: number): Loaded & {results: FactorResultsState | null; loading: boolean} {
  const [loaded, setLoaded] = useState<Loaded>({key: "", view: null, error: null});
  const bycell = cell !== undefined;
  const status = tab === "settings" || tab === "price" ? undefined : run.results?.tabs?.[tab];
  const wanted = wantsTab(tab, status, bycell);
  // A combination's build is not pushed like a run's: while it builds, ask again.
  const [beat, setBeat] = useState(0);
  const building = bycell && (loaded.view?.results.state === "queued" || loaded.view?.results.state === "running");
  useEffect(() => {
    if (!building) return;
    const timer = window.setTimeout(() => setBeat(value => value + 1), 2500);
    return () => window.clearTimeout(timer);
  }, [building, beat]);
  const key = [run.id, tab, tab === "returns" ? vs.join(",") : "", cell ?? "", bycell ? `${version}.${beat}` : status].join("|");
  useEffect(() => {
    if (!wanted) return;
    let live = true;
    fapi.results(run.id, {tab: tab as ResultsTabName, vs: tab === "returns" && vs.length ? vs : undefined, cell}).then(
      view => {if (live) setLoaded({key, view, error: null});},
      reason => {if (live) setLoaded({key, view: null, error: reason instanceof Error ? reason.message : String(reason)});});
    return () => {live = false;};
  }, [key, wanted]);
  const current = loaded.key === key ? loaded : {key, view: null, error: null};
  const results = bycell ? current.view?.results ?? null : run.results ?? null;
  return {...current, results, loading: wanted && loaded.key !== key};
}

// ---------------------------------------------------------------- small parts
function Sec({title, sub, right, children}: {title: string; sub?: string; right?: ReactNode; children: ReactNode}) {
  return <section className="fx-rsec"><TxRegionHead title={title} sub={sub}>{right}</TxRegionHead>{children}</section>;
}
/** Sections side by side, as wide as `columns` says (a grid template); a narrow page puts them under each other. */
function Row({columns = "minmax(0, 1fr) minmax(0, 1fr)", children}: {columns?: string; children: ReactNode}) {
  return <div className="fx-rtwo" style={{["--fx-cols" as string]: columns}}>{children}</div>;
}
/** A chart and its table inside one section. */
function Pair({children}: {children: ReactNode}) { return <div className="fx-rpair">{children}</div>; }
/** A figure or table that could not be made, in its place and with the reason. */
function Why({text}: {text: string | undefined}) {
  // The backend writes a reason for every table and figure it could not make (tested): no reason, nothing to say.
  if (!text) return null;
  return <div className="fx-why"><TxIcon name="alert" /><span>{text}</span></div>;
}
/** A chart in the box of its size: the box takes its height from the page (`.fx-rch`, see factor.css), the chart fills it. A chart of rows (bars lying
    along names, a year a row) is as tall as its rows instead: `rows` of `row` pixels and `edge` for its axis. */
export function Fig({fig, why, label, size, rows, row = 28, edge = 44, onPoint}: {fig?: ResultsFigure; why?: string; label: string; size?: "s" | "l"; rows?: number; row?: number; edge?: number;
  onPoint?(point: PlotPoint): void}) {
  if (!fig) return <Why text={why} />;
  if (fig.unavailable) return <Why text={t("{title} could not be drawn: {unavailable}", { title: fig.title, unavailable: fig.unavailable })} />;
  if (!fig.figure) return <Why text={t("{title} was left out", { title: fig.title })} />;
  return <div className="fx-rch" data-size={rows ? "rows" : size} style={rows ? {height: edge + rows * row} : undefined}><Plot figure={fig.figure} ariaLabel={label} onPoint={onPoint} /></div>;
}
const dayOf = (point: PlotPoint): number | null => {
  const text = typeof point.x === "string" ? point.x : point.x instanceof Date ? point.x.toISOString() : null;
  const ms = text === null ? NaN : Date.parse(text.slice(0, 10) + "T00:00:00Z");
  return Number.isNaN(ms) ? null : ms;
};
type Unit = "day" | "hour" | "bar";
const PER: Record<Unit, string> = {day: t("day"), hour: t("hour"), bar: t("bar")};
const MANY: Record<Unit, string> = {day: t("days"), hour: t("hours"), bar: t("bars")};
/** "5D" for a holding of 5 days: the labels the backend gives for the periods, so hours read "5h". */
const suffixOf = (tab: {periodLabels: string[]}) => (tab.periodLabels[0] ?? "1D").replace(/^\d+/, "");
const labelOf = (tab: {periods: number[]; periodLabels: string[]}, days: number) => tab.periodLabels[tab.periods.indexOf(days)] ?? `${days}${suffixOf(tab)}`;
const holdingText = (tab: {holding: number; periods: number[]; periodLabels: string[]}) => labelOf(tab, tab.holding);
const SIGNED = (value: number | null | undefined, digits: number) => <span className={tone(value)}>{sgn(value, digits)}</span>;
const EMPTY = <span className="fx-t4">—</span>;

type Table = {head: ReactNode[] | null; rows: {key: string | number; cells: ReactNode[]; selected?: boolean}[]; left?: number[]; foot?: ReactNode};
/** A table of numbers: the columns in `left` (the first by default) read left to right, the rest are right-aligned. */
function Grid({head, rows, left = [0], foot}: Table) {
  return <div className="fx-rtable"><table className="fx-g">{head && <thead><tr>{head.map((item, index) => <th key={index} className={left.includes(index) ? "l" : undefined}>{item}</th>)}</tr></thead>}
    <tbody>{rows.map(row => <tr key={row.key} className={row.selected ? "fx-hl" : undefined}>{row.cells.map((cell, index) => <td key={index} className={left.includes(index) ? "l" : undefined}>{cell}</td>)}</tr>)}{foot}</tbody></table></div>;
}

/** The sections each tab has a place for; a `missing` entry for anything else would otherwise say nothing. */
const PLACED: Record<ResultsTabName, string[]> = {
  returns: ["groups", "groupStats", "returnsByPeriod", "meanByGroup", "violin", "factorWeighted", "spread"],
  ic: ["information", "daily", "distribution", "years", "monthly"],
  industry: ["sectors", "sizeAndPeriod", "decay", "method"],
  style: ["styles", "like", "residual"],
  turnover: ["turnover", "topBottom", "autocorrelation"],
  costs: ["split", "holdings"],
  event: ["event", "table"],
};
export const unplaced = (tab: {tab: ResultsTabName; missing: Record<string, string>}): [string, string][] => Object.entries(tab.missing).filter(([name]) => !PLACED[tab.tab].includes(name));
function Unplaced({tab}: {tab: {tab: ResultsTabName; missing: Record<string, string>}}) {
  const rest = unplaced(tab);
  return rest.length ? <section className="fx-rsec">{rest.map(([name, reason]) => <Why key={name} text={t("{name} was not built: {reason}", { name, reason })} />)}</section> : null;
}

// ---------------------------------------------------------------- Returns
export function ReturnsTab({tab, run, vs = [], runs = [], onVs = () => undefined, onDay = () => undefined}: {tab: FactorReturnsTab; run?: FactorRun; vs?: string[]; runs?: FactorRun[]; onVs?(ids: string[]): void; onDay?(ms: number): void}) {
  const per = PER[tab.unit], compared = vs.map(id => runs.find(item => item.id === id)).filter((item): item is FactorRun => Boolean(item));
  const items: TxMenuItem[] = runs.filter(item => usable(item) && item.id !== run?.id).map(item => ({
    id: item.id, label: `#${item.number}`, detail: (item.formula ?? "").slice(0, 40), checked: vs.includes(item.id),
    // every run can be ticked: past the most compared at once, the one ticked first gives way
    onSelect: () => onVs(vs.includes(item.id) ? vs.filter(other => other !== item.id) : [...vs, item.id].slice(-MAX_COMPARED))}));
  const compare = <><TxCompare run={compared.length ? compared.map(item => `#${item.number}`).join(", ") : null} items={items} /></>;
  const f = tab.figures, why = (name: string) => tab.missing[name];
  return <>
    <Sec title={t("Cumulative return by group")} right={compare}
      sub={t("{tab} holding", { tab: holdingText(tab) })}>
      <Fig fig={f.groups} why={why("groups")} label={t("Cumulative return by group")} size="l" onPoint={point => {const ms = dayOf(point); if (ms !== null) onDay(ms);}} /></Sec>
    <Row>
      <Sec title={t("Groups")} sub={t("factor values pooled over the period")}>{tab.groupStats
        ? <Grid head={[t("Group"), t("Min"), t("Max"), t("Mean"), t("Std"), t("Count"), "%"]} rows={tab.groupStats.map(g => ({key: g.group, cells: [`Q${g.group}`, sgn(g.min, 3), sgn(g.max, 3), sgn(g.mean, 3), nf(g.std, 3),
          <span className="fx-t3">{nf(g.count, 0)}</span>, <span className="fx-t3">{g.pct == null ? "" : `${nf(g.pct, 1)}%`}</span>]}))} />
        : <Why text={why("groupStats")} />}</Sec>
      <Sec title={t("Returns")} sub={t("by holding period, bps a {per}", { per })}>{tab.returnsByPeriod
        ? <Grid head={["", ...tab.returnsByPeriod.map(row => labelOf(tab, row.period))]} rows={[
          {key: "alpha", cells: [t("Alpha, a year"), ...tab.returnsByPeriod.map(row => <span className={tone(row.alpha)}>{pc(row.alpha, 2)}</span>)]},
          {key: "beta", cells: [t("Beta"), ...tab.returnsByPeriod.map(row => sgn(row.beta, 3))]},
          {key: "top", cells: [t("Top group, mean"), ...tab.returnsByPeriod.map(row => sgn(row.top, 1))]},
          {key: "bottom", cells: [t("Bottom group, mean"), ...tab.returnsByPeriod.map(row => sgn(row.bottom, 1))]},
          {key: "spread", cells: [<span title={t("Top minus bottom at 100% gross")}>{t.c("factor groups", "Spread")}</span>, ...tab.returnsByPeriod.map(row => SIGNED(row.spread, 1))]}]} />
        : <Why text={why("returnsByPeriod")} />}</Sec>
    </Row>
    <Row>
      <Sec title={t("Mean return by group")} sub={t("bps a {per}, for each holding period", { per })}><Fig fig={f.meanByGroup} why={why("meanByGroup")} label={t("Mean return by group")} /></Sec>
      <Sec title={t("Return by group, each day")} sub={t("bps a {per}", { per })}><Fig fig={f.violin} why={why("violin")} label={t("Return by group, each day")} /></Sec>
    </Row>
    <Row>
      <Sec title={t("Factor-weighted long–short")} sub={t("cumulative")}><Fig fig={f.factorWeighted} why={why("factorWeighted")} label={t("Factor-weighted long–short")} size="s" /></Sec>
      <Sec title={t("Top minus bottom group")} sub={t("{tab}, bps, 1-month mean", { tab: labelOf(tab, tab.spreadPeriod ?? tab.holding) })}><Fig fig={f.spread} why={why("spread")} label={t("Top minus bottom group")} size="s" /></Sec>
    </Row>
  </>;
}

// ---------------------------------------------------------------- IC
/** A figure on a time axis shown over one year only (a year picked on the IC tab); its own chart key, so the range is applied. */
function overYear(fig: ResultsFigure | undefined, year: number | null): ResultsFigure | undefined {
  if (!fig?.figure || year === null) return fig;
  const layout = fig.figure.layout ?? {};
  const key = (layout.meta as {key?: string} | undefined)?.key ?? "figure";
  return {...fig, figure: {...fig.figure, layout: {...layout, meta: {...(layout.meta as object), key: `${key}@${year}`},
    xaxis: {...(layout.xaxis as object), range: [`${year}-01-01`, `${year}-12-31`], autorange: false}}}};
}
const yearOf = (point: PlotPoint): number | null => {
  const year = parseInt(String(point.x).slice(0, 4), 10);
  return Number.isNaN(year) ? null : year;
};

export function IcTab({tab, onDay = () => undefined}: {tab: FactorIcTab; onDay?(ms: number): void}) {
  const f = tab.figures, why = (name: string) => tab.missing[name];
  const years = tab.years, rows = (tab.figures.monthly?.figure?.data?.[0] as {y?: unknown[]} | undefined)?.y?.length ?? 8;
  // a year picked in the table or the bars shows that year's daily IC; a day of it opens that day's cross-section in Work.
  const [year, setYear] = useState<number | null>(null);
  const pick = (picked: number | null) => setYear(picked === year ? null : picked);
  const daily = useMemo(() => overYear(f.daily, year), [f.daily, year]);
  const yearsFig = useMemo(() => year === null || !f.years?.figure ? f.years : {...f.years, figure: {...f.years.figure,
    data: (f.years.figure.data ?? []).map(trace => ({...trace, selectedpoints: ((trace as {x?: unknown[]}).x ?? []).flatMap((x, at) => String(x) === String(year) ? [at] : []),
      unselected: {marker: {opacity: .35}}}))}}, [f.years, year]);
  // The year table and the months beside it read row by row: a year is a row of each.
  const information = <Sec title={t("Information coefficient")} sub={t("daily rank IC against later returns")}>{tab.information
      ? <Grid head={["", t("Mean"), t("Std"), t("Risk-adj. IC"), t("t, Newey–West"), "p", t("Skew"), t("Kurtosis")]} rows={tab.information.map(row => ({key: row.period, selected: row.period === tab.holding,
        cells: [labelOf(tab, row.period), SIGNED(row.mean, 4), nf(row.std, 4), nf(row.riskAdjusted, 3), nf(row.tOverlap, 2), <span className="fx-t3">{nf(row.pOverlap, 4)}</span>,
          <span className="fx-t3">{sgn(row.skew, 2)}</span>, <span className="fx-t3">{sgn(row.kurtosis, 2)}</span>]}))} />
      : <Why text={why("information")} />}</Sec>;
  return <>
    <Row columns="minmax(0, 1.9fr) minmax(0, 1fr) minmax(0, 1fr)">
      {information}
      <Sec title={t("Distribution")} sub={t("daily rank IC")}><Fig fig={f.histogram} why={why("histogram")} label={t("Distribution of rank IC")} size="s" /></Sec>
      <Sec title={t("Against a normal distribution")} sub={t("quantiles of daily rank IC")}><Fig fig={f.qq} why={why("qq")} label={t("Against a normal distribution")} size="s" /></Sec>
    </Row>
    <Sec title={t("Rank IC each day")} sub={t("{tab}; line: 1-month mean", { tab: holdingText(tab) })}
      right={year !== null && <button type="button" className="qx-chip" aria-label={t("Show every year, not only {year}", { year })} onClick={() => setYear(null)}>{year}<TxIcon name="close" /></button>}>
      <Fig fig={daily} why={why("daily")} label={t("Rank IC each day")} onPoint={point => {const ms = dayOf(point); if (ms !== null) onDay(ms);}} /></Sec>
    <Row columns="minmax(0, 1fr) minmax(0, 1.4fr)">
      <Sec title={t("By year")} sub={years && years.length ? t("long–short net of the run's cost") : undefined}>{years
        ? <Grid head={[t("Year"), t("Rank IC"), "t", t("L–S net"), t("Sharpe"), t("Turnover")]} rows={years.map(row => ({key: row.year, selected: row.year === year,
          cells: [<button type="button" className="fx-cellgo" title={(row.whole ? t("{year}: its daily IC", { year: row.year }) : t("{year}, part of the year: {days} days: its daily IC", { year: row.year, days: nf(row.days, 0) }))} onClick={() => pick(row.year)}>
              {row.year}{!row.whole && <span className="fx-t3">{t(" part")}</span>}</button>,
            SIGNED(row.rankIc, 4), <span className="fx-t3">{sgn(row.t, 2)}</span>, <span className={tone(row.longShortNet)}>{pc(row.longShortNet, 1)}</span>,
            <span className="fx-t3">{sgn(row.sharpe, 2)}</span>, <span className="fx-t3">{pct(row.turnover, 0)}</span>]}))}
          foot={tab.heldOut ? <tr><td className="l fx-t3">{t("{from} on", { from: my(tab.heldOut.from) })}</td><td className="l fx-t3" colSpan={5}><span className="fx-heldrow"><TxIcon name="lock" />{t("held out")}</span></td></tr> : undefined} />
        : <Why text={why("years")} />}</Sec>
      <Sec title={t("Monthly mean rank IC")}><Fig fig={f.monthly} why={why("monthly")} label={t("Monthly mean rank IC")} rows={rows} row={26} edge={30} /></Sec>
    </Row>
    {/* One year is the table's one row: the bars are drawn from two years on. */}
    {(years?.length ?? 0) > 1 && <Sec title={t("Rank IC by year")}><Fig fig={yearsFig} why={why("yearsFigure")} label={t("Rank IC by year")} size="s" onPoint={point => pick(yearOf(point))} /></Sec>}
  </>;
}

// ---------------------------------------------------------------- Industry and size
const NEUTRAL: Record<string, string> = {"industry+size": t("industry and size"), industry: "industry", none: "nothing"};
const EXTREMES: Record<string, string> = {mad3: t("Clipped at 3 median absolute deviations each day"), pct1: t("Clipped at the 1st and 99th percentile each day"), none: t("Not clipped")};
const SCALE: Record<string, string> = {zscore: t("Z-scored each day"), rank: t("Ranked each day"), none: t("Not scaled")};
export function IndustryTab({tab}: {tab: FactorIndustryTab}) {
  const f = tab.figures, why = (name: string) => tab.missing[name], decay = tab.decay, suffix = suffixOf(tab);
  const days = [1, 2, 3, 5, 10, 20];
  const taken = NEUTRAL[decay?.neutralize ?? ""] ?? decay?.neutralize ?? t("industry and size");
  const change = (before: number | null | undefined, after: number | null | undefined) => before == null || after == null ? "" : sgn(after - before, 4);  // the difference, as many places as the IC
  const sectors = tab.sectors ?? [];
  return <>
    <Sec title={t("Inside each sector")} sub={t("rank IC, {tab}", { tab: holdingText(tab) })}>
      {sectors.length ? <Pair>
        <Fig fig={f.sectors} why={why("sectors")} label={t("Rank IC inside each sector")} rows={sectors.length} />
        <Grid head={[t("Sector"), t("Rank IC"), t("t, Newey–West"), t("Days"), t("Names")]} rows={[...sectors].reverse().map(row => ({key: row.sector,
          cells: [row.sector, SIGNED(row.rankIc, 4), <span className="fx-t3">{sgn(row.t, 2)}</span>, nf(row.days, 0), nf(row.names, 0)]}))} />
      </Pair> : <Fig fig={f.sectors} why={why("sectors")} label={t("Rank IC inside each sector")} />}</Sec>
    {!decay ? <Sec title={t("Before and after taking out {taken}", { taken })} sub={t("rank IC")}><div className="fx-rwhy"><Why text={why("decay")} /></div></Sec>
      : decay.before.every(value => value == null) && decay.after.every(value => value == null)
        ? <Sec title={t("Before and after taking out {taken}", { taken })} sub={t("rank IC")}><div className="fx-rwhy"><Why text={t("No holding day has a rank IC to compare")} /></div></Sec>
        : <Row>
          <Sec title={t("Before and after taking out {taken}", { taken })} sub={t("rank IC")}>
            <Grid head={[t("Holding"), t("Before"), t("After"), t("Change")]} rows={days.map(d => ({key: d, selected: d === tab.holding, cells: [`${d}${suffix}`, SIGNED(decay.before[d - 1], 4), SIGNED(decay.after[d - 1], 4),
              <span className="fx-t3">{change(decay.before[d - 1], decay.after[d - 1])}</span>]}))} /></Sec>
          <Sec title={t("IC by holding days")} sub={t("after and before")}><Fig fig={f.decay} why={why("decay")} label={t("IC by holding days, after and before")} /></Sec>
        </Row>}
    <Row>
      <Sec title={t("By size and period")} sub={t("rank IC, {tab}", { tab: holdingText(tab) })}>{tab.sizeAndPeriod?.length
        ? <Grid head={null} rows={tab.sizeAndPeriod.map(row => ({key: row.name, cells: [row.name, SIGNED(row.rankIc, 4)]}))} />
        : <Why text={why("sizeAndPeriod")} />}</Sec>
      <Sec title={t("How it was done")}>{!tab.method ? <Why text={why("method")} /> :
        <Grid left={[0, 1]} head={null} rows={[
          {key: "industry", cells: [t("Industry"), <span className="fx-t2 fx-wrap">{tab.method.industry}</span>]}, {key: "size", cells: [t("Size"), <span className="fx-t2 fx-wrap">{tab.method.size}</span>]},
          {key: "extremes", cells: [t("Extremes"), <span className="fx-t2 fx-wrap">{EXTREMES[tab.method.extremes] ?? tab.method.extremes}</span>]},
          {key: "scale", cells: [t("Scale"), <span className="fx-t2 fx-wrap">{SCALE[tab.method.scale] ?? tab.method.scale}</span>]},
          {key: "neutralize", cells: [t("Taken out"), <span className="fx-t2 fx-wrap">{NEUTRAL[tab.method.neutralize] ?? tab.method.neutralize}</span>]}]} />}</Sec>
    </Row>
  </>;
}

// ---------------------------------------------------------------- Style
export function StyleTab({tab}: {tab: FactorStyleTab}) {
  const f = tab.figures, why = (name: string) => tab.missing[name], r = tab.residual;
  return <>
    <Row>
      <Sec title={t("Styles")} sub={tab.version}>{tab.styles?.length
        ? <Grid head={[t("Style"), t("Correlation"), t("Measured as")]} left={[0, 2]} rows={tab.styles.map(style => ({key: style.style, cells: [style.style, style.rho == null ? EMPTY : sgn(style.rho, 2),
          <span className="fx-t3 fx-left">{style.measuredAs}</span>]}))} />
        : <Why text={why("styles") ?? why("residual")} />}</Sec>
      <Sec title={t("Rank IC after taking out the price styles")} sub={r && r.styles.length ? r.styles.join(", ") : undefined}>{r && r.rankIc != null
        ? <Grid head={["", t("After"), t("Before"), "t"]} rows={[{key: "ic", cells: [t("Rank IC"), SIGNED(r.rankIc, 4), SIGNED(r.before, 4), <span className="fx-t3">{sgn(r.t, 2)}</span>]}]} />
        : <Why text={why("residual")} />}</Sec>
    </Row>
    <Sec title={t("Like this factor")} sub={t("mean daily rank correlation")}>
      <Fig fig={f.like} why={why("like")} label={t("Like this factor")} rows={tab.like?.length ?? 8} /></Sec>
  </>;
}

// ---------------------------------------------------------------- Turnover
export function TurnoverTab({tab}: {tab: FactorTurnoverTab}) {
  const f = tab.figures, why = (name: string) => tab.missing[name], groups = tab.groups ?? [];
  return <>
    <Row columns="minmax(0, 1fr) minmax(0, 1.6fr)">
      <Sec title={t("Turnover")} sub={t("share of each group replaced at each rebalance; rank autocorrelation")}>{tab.turnover
        ? <Grid head={["", ...groups.map(q => `Q${q}`), t("Rank autocorr.")]} rows={tab.turnover.map(row => ({key: row.period, selected: row.period === tab.holding,
          cells: [labelOf(tab, row.period), ...groups.map((_, i) => pct(row.groups[i], 1)), nf(row.autocorrelation, 3)]}))} />
        : <Why text={why("turnover")} />}</Sec>
      <Sec title={t("Factor rank autocorrelation")} sub={t("against the ranks {holding} {unit} earlier", { holding: tab.holding, unit: MANY[tab.unit] })}><Fig fig={f.autocorrelation} why={why("autocorrelation")} label={t("Rank autocorrelation")} size="s" /></Sec>
    </Row>
    <Sec title={t("Top and bottom group turnover")} sub={t("every {holding} {unit}", { holding: tab.holding, unit: MANY[tab.unit] })}><Fig fig={f.topBottom} why={why("topBottom")} label={t("Top and bottom group turnover")} /></Sec>
  </>;
}

// ---------------------------------------------------------------- Costs
export function CostsTab({tab, onHolding}: {tab: FactorCostsTab; onHolding?(holding: number): void}) {
  const f = tab.figures, why = (name: string) => tab.missing[name], s = tab.split;
  const costs = s ? (s.tradingLong ?? 0) + (s.tradingShort ?? 0) + (s.borrow ?? 0) : 0;
  // Costs are not coloured (design.md 11-13: red and green only for the sign of a return); the net and gross keep theirs.
  const part = (label: string, value: number | null | undefined) => ({key: label, cells: [<span className="fx-t3">{label}</span>, <span>{value == null ? "" : pc(-value, 2)}</span>,
    <span className="fx-t3">{value == null || !costs ? "" : pct(value / costs, 0)}</span>]});
  return <>
    <Row>
      <Sec title={t("Where the cost goes")} sub={s ? t("long–short, a year, {costBps} bps a trade, borrow {borrowPct}% a year", { costBps: nf(s.costBps, 1), borrowPct: nf(s.borrowPct, 1) }) : undefined}>{s
        ? <Grid head={["", t("A year"), t("Share")]} rows={[{key: "gross", cells: [t("Gross"), <span className={tone(s.gross)}>{pc(s.gross, 2)}</span>, ""]},
          part(t("Trading, long leg"), s.tradingLong), part(t("Trading, short leg"), s.tradingShort), part(t("Borrow, short leg"), s.borrow),
          {key: "net", cells: [t("Net"), <span className={tone(s.net)}>{pc(s.net, 2)}</span>, ""]},
          {key: "even", cells: [<span className="fx-t3">{t("Break-even, a trade")}</span>, s.breakEven == null ? "" : s.breakEven < 0 ? t("loses before costs") : t("{value} bps", { value: nf(s.breakEven, 1) }), ""]}]} />
        : <Why text={why("split")} />}</Sec>
      <Sec title={t("Net by cost a trade")} sub={t("long–short, a year")}><Fig fig={f.byCost} why={why("byCost")} label={t("Net return by cost a trade")} size="s" /></Sec>
    </Row>
    <Sec title={t("Holding period")} sub={t("same factor, the cost a trade on both legs")}>{tab.holdings
      ? <Grid head={[t("Holding"), t("Rank IC"), t("Gross"), t("Turnover"), t("Costs"), t("Net"), t("Sharpe"), t("Break-even")]} rows={tab.holdings.map(row => ({key: row.holding, selected: row.holding === tab.holding,
        cells: [onHolding && row.holding !== tab.holding ? <button type="button" className="fx-cellgo" title={t("Run at a {tab} holding", { tab: labelOf(tab, row.holding) })} onClick={() => onHolding(row.holding)}>{labelOf(tab, row.holding)}</button>
          : labelOf(tab, row.holding), SIGNED(row.rankIc, 4), <span className={tone(row.gross)}>{pc(row.gross, 1)}</span>, <span className="fx-t3">{pct(row.turnover, 0)}</span>, <span>{pc(row.costs, 1)}</span>,
          <span className={tone(row.net)}>{pc(row.net, 1)}</span>, nf(row.sharpe, 2), <span className="fx-t3">{row.breakEven == null ? "" : row.breakEven < 0 ? t("loses before costs") : t("{breakEven} bps", { breakEven: nf(row.breakEven, 1) })}</span>]}))} />
      : <Why text={why("holdings")} />}</Sec>
  </>;
}

// ---------------------------------------------------------------- Event study
export function EventTab({tab}: {tab: FactorEventTab}) {
  const f = tab.figures, why = (name: string) => tab.missing[name], suffix = suffixOf(tab);
  // What the returns are measured against follows the run's Raw / Demeaned and Group neutral settings.
  const against = tab.basis === "raw" ? "" : tab.basis === "group" ? t(", relative to the names of the same industry") : t(", relative to all names");
  return <Row columns="minmax(0, 1.7fr) minmax(0, 1fr)">
    <Sec title={t("Around the signal")} sub={t("mean cumulative return by group, 5 {unit} before to 20 after{against}", { unit: MANY[tab.unit], against })}><Fig fig={f.event} why={why("event")} label={t("Event study by group")} size="l" /></Sec>
    <Sec title={t("After the signal")} sub={t("cumulative{against}", { against })}>{tab.table
      ? <Grid head={[t("Group"), t("5 {unit} into it", { unit: MANY[tab.unit] }), ...[1, 5, 10, 20].map(d => t("{count}{unit} after", { count: d, unit: suffix }))]} rows={tab.table.map(row => ({key: row.group,
        cells: [`Q${row.group}`, <span className="fx-t3">{pc(row.before, 2)}</span>, ...[row.after1, row.after5, row.after10, row.after20].map(value => <span className={tone(value)}>{pc(value, 2)}</span>)]}))} />
      : <Why text={why("table") ?? why("event")} />}</Sec>
  </Row>;
}

// ---------------------------------------------------------------- Settings: what the run ran, from its record
function SettingsTab({run, results, about, universe, handlers}: {run: FactorRun; results: FactorResultsState | null; about: FactorResultsState["about"]; universe(data: DataSettings): string; handlers: ResultsHandlers}) {
  const s = run.settings.run, data = run.settings.data;
  const grouping = s.grouping.method === "equal" ? t("{count}, equal weight", { count: s.grouping.count }) : s.grouping.method === "cuts" ? t("cut at {cuts}", { cuts: s.grouping.cuts }) : t("positive and negative");
  const leave = [s.leaveOut.halted && t("halted names"), s.leaveOut.newListings && t("listed under 60 days"), s.leaveOut.illiquid && t("lowest 20% by dollar volume")].filter(Boolean).join("; ");
  const days = (count: number) => `${count} ${count === 1 ? PER[unitOfData(data)] : MANY[unitOfData(data)]}`;
  const BARS: Record<string, string> = {"1d": t("Daily"), "1h": t("Hourly"), "4h": t("4 hours"), "1w": t("Weekly")};
  const rows = (items: [string, ReactNode][]) => <div className="fx-rtable"><table className="fx-g"><tbody>{items.filter(([, value]) => value !== null && value !== "").map(([label, value]) =>
    <tr key={label}><td className="l fx-t3 fx-kcol">{label}</td><td className="l fx-wrap">{value}</td></tr>)}</tbody></table></div>;
  const versions = about?.versions, env = versions ? [versions.python && `Python ${versions.python.split(".").slice(0, 2).join(".")}`, versions.qlib && `Qlib ${versions.qlib}`, versions.alphalens && `Alphalens ${versions.alphalens}`].filter(Boolean).join(", ") : "";
  const adjust = data.adjustment === "raw" ? t("raw prices") : t("split- and dividend-adjusted");
  // the period the run computed, which stops before the held-out part, as the data button says.
  const ranTo = data.heldOutFrom ? new Date(Date.parse(data.heldOutFrom + "T00:00:00Z") - 86_400_000).toISOString().slice(0, 10) : data.until!;
  return <Row>
    <Sec title={t("What #{number} ran", { number: run.number })} right={<><TxButton size="sm" onClick={() => handlers.reuse(run)}>{t("Use these settings")}</TxButton></>}>{rows([
      [run.mode === "code" ? t("Code") : t("Formula"), run.mode === "code" ? <span className="fx-t2">{run.changes.find(change => change.what === "code") ? (run.codeDigest ? t("factor.py as saved for this run ({codeDigest})", { codeDigest: run.codeDigest.slice(0, 8) }) : t("factor.py as saved for this run")) : t("factor.py as saved for this run")}</span> : <code>{run.formula}</code>],
      [t("Leave out"), leave || "nothing"], [t("Holding"), days(s.holding)], [t("Groups"), `${grouping}${s.grouping.withinIndustry ? t(", inside each industry") : ""}`], [t("Delay"), days(s.delay)],
      [t("Extremes"), s.extremes === "mad3" ? t("Clip at 3 MAD each day") : s.extremes === "pct1" ? t("Clip at 1% / 99% each day") : t("None")],
      [t("Scale"), s.scale === "zscore" ? t("Z-score each day") : s.scale === "rank" ? t("Rank each day") : t("None")],
      [t("Neutralize"), s.neutralize === "industry+size" ? t("Industry + size") : s.neutralize === "industry" ? t("Industry") : t("None")],
      [t("Cost"), t("{cost} bps a trade, borrow {borrow}% a year", { cost: nf(s.costBps, 1), borrow: nf(s.borrowPct, 1) })]])}</Sec>
    <Sec title={t("Data and environment")}>{rows([
      [t("Universe"), universe(data)], [t("Bars"), `${BARS[data.timeframe ?? ""] ?? data.timeframe ?? ""}, ${adjust}`], [t("Period"), data.since && data.until ? `${dayText(data.since)} – ${dayText(ranTo)}` : ""],
      [t("Held out"), data.heldOutFrom ? t("from {date}, not used", { date: dayText(data.heldOutFrom) }) : "none"], [t("Data to"), run.data?.dataTo ? t("{date}, last close used", { date: mdy(run.data.dataTo) }) : ""],
      [t("Names"), run.data?.names ? (run.data.requested && run.data.requested > run.data.names ? t("{count} with prices, {omitted} left out", { count: nf(run.data.names, 0), omitted: nf(run.data.requested - run.data.names, 0) }) : t("{count} with prices", { count: nf(run.data.names, 0) })) : ""],
      [t("Data limits"), run.data?.notes?.length ? <>{run.data.notes.map((note, index) => <div key={index}>{note}</div>)}</> : ""], [t("Styles"), about?.styles ?? ""],
      [t("Environment"), env ? t("Research, {environment}", { environment: env }) : ""], [t("Took"), (results?.seconds != null ? t("{time} for Work, {results} for Results", { time: secondsText(run.seconds), results: secondsText(results.seconds) }) : t("{time} for Work", { time: secondsText(run.seconds) }))]])}</Sec>
  </Row>;
}
const unitOfData = (data: DataSettings): Unit => data.timeframe === "1h" || data.timeframe === "1m" || data.timeframe === "5m" || data.timeframe === "15m" ? "hour" : "day";

// ---------------------------------------------------------------- the page
/** What the Results take on screen while they are read: the tabs' line, a row of key numbers and a chart. */
export function ResultsSkeleton() {
  return <div className="fx-rskel" aria-busy="true"><TxSkeleton width="45%" /><TxSkeleton width="70%" /><TxSkeleton className="fx-rskel-plot" /></div>;
}

function Wait({text, value}: {text: string; value?: number | null}) {
  return <div className="fx-rwait"><span className="tx-spin" />{text}{value != null && <span className="fx-num fx-t3">{Math.round(value * 100)}%</span>}</div>;
}

interface ResultsProps {
  fapi: FactorApi; selected: FactorRun | null; runs: FactorRun[]; tab: ResultsPageTab; onTab(tab: ResultsPageTab): void;
  vs: string[]; onVs(ids: string[]): void; cell?: number; universe(data: DataSettings): string; handlers: ResultsHandlers;
}
/** `run` is the run the Results are of: the selected one, or for a held-out validation the run it scored, or the last run with numbers. */
export function ResultsView(props: ResultsProps & {run: FactorRun | null}) {
  if (props.run === null) return null;  // the page offers Results only once a run has finished
  return <ResultsBody {...props} run={props.run} />;
}

function ResultsBody({fapi, run, selected, runs, tab, onTab, vs, onVs, cell, universe, handlers}: ResultsProps & {run: FactorRun}) {
  const compared = vs.filter(id => runs.some(item => item.id === id && usable(item) && item.id !== run.id));
  const data = useTab(fapi, run, tab, compared, cell, run.updatedAt);
  // Which tools made the Results is in the state a tab read brings; the Settings tab shows what was read so far.
  const [seen, setSeen] = useState<{run: string; about: FactorResultsState["about"]} | null>(null);
  const fresh = data.view?.results.about;
  useEffect(() => {if (fresh) setSeen({run: run.id, about: fresh});}, [fresh, run.id]);
  const about = run.results?.about ?? (seen !== null && seen.run === run.id ? seen.about : undefined);
  const building = run.resultsProgress != null;
  const heldout = selected !== null && selected.kind === "heldout" && selected.id !== run.id ? selected : null;
  const pickRuns: TxMenuItem[] = runs.filter(item => usable(item)).map(item => ({id: item.id, label: `#${item.number}`, detail: (item.formula ?? "").slice(0, 40), checked: item.id === run.id, onSelect: () => handlers.select(item.id)}));
  const strip = <TxRunStrip picker={<TxRunPicker run={`#${run.number}`} items={pickRuns} />} when={when(run.createdAt)}
    meta={[run.mode === "code" ? "factor.py" : run.formula ?? "", ...(heldout ? [t("held-out #{number} has no Results of its own", { number: heldout.number })] : [])].filter(Boolean)}
    tags={<>{run.version !== null && <TxTag tone="version">v{run.version}</TxTag>}{run.kind === "heldout" && <TxTag icon="lock">{t("Held-out")}</TxTag>}
      {run.kind !== "heldout" && run.heldOut && <button type="button" className="fx-vtag" title={t("Open the held-out run #{number}", { number: run.heldOut.number })} onClick={() => handlers.select(run.heldOut!.runId)}><TxTag icon="lock">{t("Validated by #{number}", { number: run.heldOut.number })}</TxTag></button>}
      {building && <span className="fx-livetag"><TxLive label={t("Building")} value={run.resultsProgress} /></span>}</>}
    actions={<><DataNotes notes={run.data?.notes ?? []} />
      <TxIconButton label={run.starred ? t("Kept") : t("Keep")} icon="star" size="sm" data-on={run.starred ? "" : undefined} onClick={() => handlers.star(run)} />
      <UseMenu run={run} label={`#${run.number}`} onCreate={handlers.create} cell={cell} onNotebook={handlers.notebook} /><PublishButton run={run} onPublish={handlers.publish} /></>} />;
  return <div className="fx-results">
    {strip}
    <TxTabs value={tab} onChange={onTab} options={RESULT_TABS} />
    <div className="fx-rscroll" data-chart-link="">{body()}</div>
  </div>;

  function body(): ReactNode {
    if (tab === "price") return <RunPrice key={run.id} runId={run.id} />;
    if (tab === "settings") return <SettingsTab run={run!} results={run!.results ?? null} about={about} universe={universe} handlers={handlers} />;
    const results = data.results;
    const status = results?.tabs?.[tab];
    if (data.error) return <TxFailure tone="watch" title={t("Could not read this tab")} message={data.error} />;
    const view = data.view?.tab ?? null;
    if (view !== null && view.tab === tab) {
      if (view.failed) return <TxFailure title={t("{label} could not be built", { label: RESULT_TABS.find(item => item.value === tab)?.label ?? "" })} message={view.failed} actions={<TxButton size="sm" onClick={() => handlers.build(run!, cell)}><TxIcon name="refresh" />{t("Build again")}</TxButton>} />;
      return <>
        {view.tab === "returns" && <ReturnsTab tab={view} run={run!} vs={compared} runs={runs} onVs={onVs} onDay={handlers.showDay} />}
        {view.tab === "ic" && <IcTab tab={view} onDay={handlers.showDay} />}
        {view.tab === "industry" && <IndustryTab tab={view} />}
        {view.tab === "style" && <StyleTab tab={view} />}
        {view.tab === "turnover" && <TurnoverTab tab={view} />}
        {view.tab === "costs" && <CostsTab tab={view} onHolding={handlers.holding} />}
        {view.tab === "event" && <EventTab tab={view} />}
        <Unplaced tab={view} />
      </>;
    }
    if (data.loading || results === null) return <ResultsSkeleton />;
    if (results.state === "none") return <div className="fx-rwait"><TxIcon name="alert" />{results.message ?? t("This run has no Results")}</div>;
    if (results.state === "queued" || results.state === "running" || status === "waiting" || status === "running")
      return <Wait text={t("Building {section}", { section: BUILDING_NAME[tab] ?? tab })} value={results.progress} />;
    if (results.state === "notbuilt") return <TxFailure tone="watch" title={cell !== undefined ? t("This combination has no Results yet") : t("#{number} has no Results yet", { number: run!.number })}
      message={results.message ?? undefined} actions={<TxButton size="sm" onClick={() => handlers.build(run!, cell)}>{t("Build Results")}</TxButton>} />;
    return <TxFailure title={results.state === "interrupted" ? t("The Results were interrupted when Quant closed") : t("The Results could not be built")} message={results.message}
      actions={<TxButton size="sm" onClick={() => handlers.build(run!, cell)}><TxIcon name="refresh" />{t("Build again")}</TxButton>} />;
  }
}
