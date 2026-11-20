/** The allocation's Results: the full backtest of one run, a tab at a time (each read when it is on screen). Summary (growth against 60/40 and equal weight, drawdown, crises),
    weights and turnover at every period, risk (risk shares, weight against risk share, correlation, scenarios), returns, and every setting. Every chart is the backend's Plotly figure;
    one that could not be made says why in its place. */
import { TxButton, TxIcon, TxInput, TxKeyNumbers, TxRegionHead, TxSegmented, TxSelect, TxSkeleton, TxTabs } from "@talyx/sdk";
import { useEffect, useState, type ReactNode } from "react";
import { dayText, when } from "../factor/format";
import { nf, pc, pct } from "../shared/format";
import type { AllocationApi, AllocationRun, Figure, Tab } from "./api";
import { keyNumbers } from "./Work";
import { groupsDiffer } from "./ThisPeriod";
import { Plot } from "../chart/Plot";

export const RESULT_TABS: { value: Tab; label: string }[] = [{ value: "summary", label: "Summary" }, { value: "weights", label: "Weights and trades" }, { value: "risk", label: "Risk" },
  { value: "returns", label: "Returns" }, { value: "style", label: "Style and attribution" }, { value: "robust", label: "Robustness" }, { value: "settings", label: "Settings" }];
const signed = (v: number, d = 1) => <span className={v >= 0 ? "al-up" : "al-down"}>{pc(v, d)}</span>;
/** A section of a tab: its head (title, the measure in `sub`, its controls) and its body. `hint`, how to use it, is the title's hover text. */
const Section = ({ title, sub, hint, tools, children }: { title: string; sub?: string; hint?: string; tools?: ReactNode; children: ReactNode }) =>
  <section className="al-rsec">{hint ? <div className="tx-regionhead"><h3 title={hint}>{title}</h3>{sub && <span className="tx-regionsub" title={sub}>{sub}</span>}<span className="tx-spacer" /><span className="tx-regionkeys" data-tx-keys="" />{tools}</div>
    : <TxRegionHead title={title} sub={sub}>{tools}</TxRegionHead>}<div className="qv-pad">{children}</div></section>;

/** A chart; `height` in pixels for one whose rows set it (a heatmap of years or assets), else the height of its `size`. */
export function Fig({ fig, label, size, height }: { fig: Figure; label: string; size?: "s" | "l"; height?: number }) {
  if (!fig) return <p className="fx-why" role="status">{label} did not come out</p>;
  if ("why" in fig) return <p className="fx-why" role="status">{label} did not come out: {fig.why}</p>;
  return <div className="fx-rch" data-size={size} style={height ? { height } : undefined}><Plot figure={fig} ariaLabel={label} /></div>;
}

/** The height of a heatmap whose rows are its content (years, assets): a row a line of text and the axis under it, never under `least`. */
const rowsHeight = (fig: Figure, least: number) => !fig || "why" in fig ? undefined : Math.max(least, 34 * ((fig.data as { y?: unknown[] }[])[0]?.y?.length ?? 0) + 44);

const words = (value: unknown) => String(value).replace(/_/g, " ").replace(/^./, letter => letter.toUpperCase());
const blank = (value: unknown) => value === "" || value === null || value === undefined;
const money = (value: number) => `$${Math.round(value).toLocaleString("en-US")}`;

/** The run's settings as the draft writes them: one line each, in words, under Code settings, Run settings and Data. */
export interface DataFacts { mode?: string; heldFrom?: string | null; dataTo?: string; adjustment?: string;
  /** The settings' option labels as the draft shows them, by setting and value (the report wrote "Empirical" for the draft's "Sample"). */
  options?: Record<string, Record<string, string>> }
export function settingRows(settings: Record<string, any>, run: AllocationRun, facts?: DataFacts): [string, string?][] {
  const pool: { group?: string; ticker?: string }[] = settings.pool ?? [];
  const say = (name: string) => facts?.options?.[name]?.[String(settings[name])] ?? words(settings[name]);
  const groups = new Set(pool.map(item => item.group ?? "Other"));
  const limits = (settings.group_limits ?? []).filter((row: Record<string, any>) => !blank(row.max) || !blank(row.min));
  const rows: [string, string?][] = [[`Code settings, ${facts?.mode === "code" ? "project code" : "standard flow"}`], ["Asset pool", `${pool.length} ${pool.length === 1 ? "asset" : "assets"} in ${groups.size} ${groups.size === 1 ? "group" : "groups"}`], ["Objective", say("objective")]];
  if (settings.risk_measure) rows.push(["Risk measure", say("risk_measure")]);
  rows.push(["Returns", `${settings.align === "weekly" ? "Weekly" : settings.align === "monthly" ? "Monthly" : "Daily"}, close to close`], ["Lookback", `${settings.lookback} days`], ["Covariance", say("covariance")]);
  if (!blank(settings.max_weight)) rows.push(["Each asset", `at most ${settings.max_weight}%`]);
  rows.push(["By group", limits.length ? limits.map((row: Record<string, any>) => `${row.group}${blank(row.min) ? "" : ` at least ${row.min}%`}${blank(row.max) ? "" : ` at most ${row.max}%`}`).join(", ") : "no limits"], ["Long / short", `${settings.long_cap ?? 100}% long, ${settings.short_cap ?? 0}% short, ${settings.net ?? 100}% net`],
    ["Rebalance", `${say("frequency")}, at the period-end close`]);
  rows.push(["Costs by group", `${(settings.costs ?? []).map((row: Record<string, any>) => `${row.group} ${row.bps}`).join(", ")} bps`]);
  rows.push(["Run settings, platform"]);
  if (!blank(settings.capital)) rows.push(["Capital", money(Number(settings.capital))]);
  rows.push(["Benchmarks", [pool.some(row => row.ticker === "SPY") && pool.some(row => row.ticker === "IEF") ? "60/40 (SPY / IEF)" : "", "Equal weight"].filter(Boolean).join(", ") + ` · ${say("frequency")}`], ["Cost multiplier", `${settings.cost_multiple ?? 1} ×`]);
  if (facts?.heldFrom) rows.push(["Held out from", dayText(facts.heldFrom)]);
  rows.push(["Data"]);
  if (facts?.adjustment) rows.push(["Source", facts.adjustment === "raw" ? "Raw closes" : "Adjusted closes"]);
  if (run.data?.dataFrom) rows.push(["Research period", `${dayText(run.data.dataFrom)} – ${dayText(run.data.dataTo ?? run.data.dataFrom)}`]);
  if (run.data?.dataTo) rows.push(["Data to", dayText(run.data.dataTo)]);
  rows.push(["Ran", `${when(run.createdAt)}${run.seconds != null ? `, ${nf(run.seconds, 1)} s` : ""}`]);
  return rows;
}

export interface ShockControl { targets: string[]; added: number; onAdd(target: string, pct: number): void }

/** The line under the scenarios table: an asset or a group, a percent, and Add. A percent that is no shock (0, −100% or below, not a number) is marked in its box. */
export function AddShock({ shock }: { shock: ShockControl }) {
  const [target, setTarget] = useState("");
  const [pct, setPct] = useState("-20");
  const value = Number(pct);
  const bad = !Number.isFinite(value) || value === 0 || value <= -100;
  if (shock.targets.length === 0) return null;
  const chosen = target || shock.targets[0];
  return <div className="al-shock"><span className="al-dim">Add a shock</span><TxSelect aria-label="Shock target" value={chosen} onChange={event => setTarget(event.target.value)}>{shock.targets.map(name => <option key={name} value={name}>{name}</option>)}</TxSelect>
    <TxInput numeric aria-label="Shock percent" value={pct} suffix="%" aria-invalid={bad || undefined} title={bad ? "A move above −100%, not 0" : undefined} onChange={event => setPct(event.target.value)} />
    {!bad && <TxButton size="sm" onClick={() => shock.onAdd(chosen, value)}>Add</TxButton>}
</div>;
}

const FIX: Record<string, string> = { limits: "Limits", objective: "Objective", pool: "Asset pool" };
function Body({ tab, data, run, compared, onPeriod, onSetting, onValidate, shock, facts }: { facts?: DataFacts; shock?: ShockControl; tab: Tab; data: Record<string, any>; run: AllocationRun; compared: AllocationRun | null; onPeriod: (date: string) => void; onSetting?: (group: string) => void; onValidate?: () => void }) {
  const [by, setBy] = useState<"group" | "asset">("group");
  const [held, setHeld] = useState(false);
  if (tab === "summary") {
    const names: string[] = data.crises?.length ? Object.keys(data.crises[0]).filter((key: string) => key !== "window" && key !== "from") : [];
    const note = data.notes as { trackingError: number | null; traded: number; periods: number; to?: string } | undefined;
    // What the number is made of sits under it in place of the change against the compared run, as the draft has it.
    const under: Record<string, string | undefined> = { "vs 60/40": note?.trackingError != null ? `tracking error ${pct(note.trackingError, 2)}` : undefined,
      Turnover: note ? `${note.traded} of ${note.periods} periods traded` : undefined };
    const numbers = [...keyNumbers(run.metrics ?? undefined, compared, () => undefined).map(item => under[item.label] ? { ...item, delta: under[item.label] } : item),
      ...(run.metrics ? [{ label: "Cost drag", value: `−${pct(run.metrics.costDrag, 2)}`, unit: "a year", tone: "down" as const }] : [])];
    return <><TxKeyNumbers items={numbers} columns={4} />
      <Section title="Growth of 1" sub={`compounded, net of costs${note?.to ? `, to ${dayText(note.to)}` : ""}`}><Fig fig={data.growth} label="Growth of 1" size="l" /><Fig fig={data.drawdown} label="Drawdown" size="s" /></Section>
      <div className="al-grid2">
        {data.crises?.length > 0 && <Section title="Crisis periods" sub="return over each window"><table className="qv-table"><thead><tr><th className="l">Window</th>{names.map(name => <th key={name}>{name}</th>)}</tr></thead>
          <tbody>{data.crises.map((row: Record<string, any>) => <tr key={row.window} className="al-click" title="Open the weights held then" onClick={() => onPeriod(row.from)}><td className="l">{row.window}</td>{names.map(name => <td key={name}>{signed(row[name])}</td>)}</tr>)}</tbody></table></Section>}
        {data.years?.length > 0 && <Section title="By year" sub="net of costs"><table className="qv-table"><thead><tr><th className="l">Year</th>{yearNames(data.years).map(name => <th key={name}>{name}</th>)}<th>vs EW</th></tr></thead>
          <tbody>{data.years.map((row: Record<string, any>) => <tr key={row.year} className="al-click" title="Open the weights held then" onClick={() => onPeriod(`${row.year}-01-01`)}><td className="l">{row.year}</td>{yearNames(data.years).map(name => <td key={name}>{signed(row[name])}</td>)}<td>{row.vsEqual == null ? "" : `${row.vsEqual >= 0 ? "+" : "−"}${Math.abs(row.vsEqual * 100).toFixed(2)} pt`}</td></tr>)}</tbody></table></Section>}
      </div></>;
  }
  const stackBy = <TxSegmented label="Stack by" value={by} onChange={setBy} options={[{ value: "group", label: "By group" }, { value: "asset", label: "By asset" }]} />;
  if (tab === "weights") {
    const grouped = groupsDiffer(data.byGroup);   // one group stacks into one band filling the chart: the assets are shown instead
    const byGroup = grouped && by === "group";
    return <>
    <Section title="Weights over time" hint="Click a date to open that period in Work" tools={<>{grouped && stackBy}
      <TxSegmented label="Weights shown" value={held ? "held" : "target"} onChange={value => setHeld(value === "held")} options={[{ value: "target", label: "Target" }, { value: "held", label: "Held" }]} /></>}>
      <Fig fig={held ? (byGroup ? data.heldByGroup : data.heldWeights) : byGroup ? data.byGroup : data.weights} label="Weights at every period" size="l" /></Section>
    <Section title="Turnover at each period" sub="one-way share of the portfolio traded, and its cost"><Fig fig={data.turnover} label="Turnover" size="s" /></Section>
    {Array.isArray(data.periods) && <Section title="Periods" sub={`${data.periods.length}; cost in bps of the portfolio`}><table className="qv-table"><thead><tr><th className="l">Date</th><th>Turnover</th><th>Cost, bps</th><th className="l">Limits that bind</th><th className="l">Solver</th></tr></thead>
      <tbody>{data.periods.map((row: Record<string, any>) => <tr key={row.date} className="al-click" onClick={() => onPeriod(row.date)}><td className="l">{dayText(row.date)}</td><td>{row.traded ? pct(row.turnover, 1) : <span className="al-dim">held</span>}</td>
        <td>{row.traded ? <span className="al-down">−{nf(row.cost, 2)}</span> : ""}</td><td className="l al-dim">{row.limits}</td>
        <td className="l">{row.status === "optimal" ? "solved" : <span className="al-warn">{row.note || row.status}</span>}{row.fix && onSetting && <TxButton variant="quiet" size="sm" onClick={event => { event.stopPropagation(); onSetting(row.fix); }}>{FIX[row.fix]}</TxButton>}</td></tr>)}</tbody></table></Section>}</>;
  }
  if (tab === "risk") {
    const grouped = groupsDiffer(data.riskSharesByGroup);
    return <>
    <Section title="Risk share over time" sub="share of predicted variance at every period" tools={grouped ? stackBy : undefined}>
      <Fig fig={grouped && by === "group" ? data.riskSharesByGroup : data.riskShares} label="Risk shares" size="l" /></Section>
    <div className="al-grid2"><Section title="Weight and risk share" sub="latest period"><Fig fig={data.weightRisk} label="Weight and risk share" size="s" /></Section>
      <Section title="Predicted and realized volatility" sub="predicted at each period, realized over the next one"><Fig fig={data.volCalibration} label="Predicted and realized volatility" size="s" /></Section></div>
    <div className="al-grid2"><Section title="Efficient frontier" sub="newest window, historical means, the run's limits"><Fig fig={data.frontier} label="Efficient frontier" size="s" /></Section>
      <Section title="Risk measures" sub="whole in-sample series">{data.measures ? <table className="qv-table"><tbody>{data.measures.map((row: Record<string, number | string>) => <tr key={row.label as string}><td className="l">{row.label}</td><td>{row.plain ? pct(row.value as number, 2) : pc(row.value as number, 2)}</td></tr>)}</tbody></table> : null}</Section></div>
    <div className="al-grid2"><Section title="Correlation" sub="over the last lookback"><Fig fig={data.corr} label="Correlation" height={rowsHeight(data.corr, 220)} /></Section>
    <Section title="Scenarios" sub="today's weights through each window, and shocks">{Array.isArray(data.scenarios)
      ? <table className="qv-table"><thead><tr><th className="l">Scenario</th><th className="l">Kind</th><th>Portfolio</th><th className="l">Worst asset</th></tr></thead>
        <tbody>{data.scenarios.map((row: Record<string, any>) => <tr key={row.scenario}><td className="l">{row.scenario}</td><td className="l al-dim">{row.kind}</td><td>{signed(row.portfolio)}</td><td className="l">{row.worst}</td></tr>)}</tbody></table>
      : <p className="fx-why" role="status">Scenarios did not come out{data.scenarios?.why ? `: ${data.scenarios.why}` : ""}</p>}
      {shock && <AddShock shock={shock} />}</Section></div></>;
  }
  if (tab === "returns") return <>
    <div className="al-grid2"><Section title="Calendar years" sub="net of costs"><Fig fig={data.annual} label="Calendar years" size="s" /></Section>
      <Section title="Contribution by asset" sub="sum of held weight × daily return, pt"><Fig fig={data.contribution} label="Contribution by asset" size="s" /></Section></div>
    <Section title="Monthly returns" sub="net of costs"><Fig fig={data.monthly} label="Monthly returns" height={rowsHeight(data.monthly, 150)} /></Section>
    <Section title="Rolling Sharpe" sub="over the year before each day"><Fig fig={data.rolling} label="Rolling" /></Section>
    <Section title="Distribution of daily returns" sub="share of days"><Fig fig={data.distribution} label="Distribution of daily returns" /></Section></>;
  if (tab === "style") {
    if (data.noStocks) return <Section title="Style and attribution"><p className="fx-why" role="status">Styles describe stocks; this pool has none</p></Section>;
    const style = data.rows || data.why ? data : null;  // the tab's file is the style block itself
    if (!style || style.why) return <Section title="Style and attribution"><p className="fx-why" role="status">{style?.why ? `Styles did not come out: ${style.why}` : "Styles did not come out"}</p></Section>;
    return <>
      <Section title="Style exposure" sub={style.version}>{style.rows.length ? <table className="qv-table"><thead><tr><th className="l">Style</th><th>Average exposure</th><th>Now</th><th>Contribution a year</th></tr></thead>
        <tbody>{style.rows.map((row: Record<string, number | string | null>) => <tr key={row.style as string}><td className="l">{row.style}</td><td>{row.average == null ? "" : nf(row.average as number, 2)}</td><td>{row.now == null ? "" : nf(row.now as number, 2)}</td><td>{row.contribution == null ? "" : signed(row.contribution as number, 2)}</td></tr>)}</tbody></table>
        : <p className="fx-why" role="status">Price styles need at least three stocks in the pool and a year of history</p>}
        {style.volumeWhy && <p className="fx-why" role="status">{`Styles from traded amounts are left out: ${style.volumeWhy}`}</p>}</Section>
      {style.groups && <Section title="By group against 60/40" sub="stocks 60, bonds 40, a year"><table className="qv-table"><thead><tr><th className="l">Group</th><th>Allocation effect</th><th>Selection effect</th><th>Total</th></tr></thead>
        <tbody>{style.groups.map((row: Record<string, number | string>) => <tr key={row.group as string}><td className="l">{row.group}</td><td>{signed(row.allocation as number, 2)}</td><td>{signed(row.selection as number, 2)}</td><td>{signed(row.total as number, 2)}</td></tr>)}</tbody></table></Section>}</>;
  }
  if (tab === "robust") return <>
    <div className="al-grid2"><Section title="Held out" sub={data.heldOut ? "scored once" : undefined}>{data.heldOut ? <><table className="qv-table"><thead><tr><th className="l" /><th>CAGR</th><th>Volatility</th><th>Sharpe</th><th>Max DD</th></tr></thead>
      <tbody>{([["In-sample", data.heldOut.inSample], [`Held out, ${dayText(data.heldOut.from)} – ${dayText(data.heldOut.to)}`, data.heldOut.held]] as [string, Record<string, number>][]).map(([label, m]) => <tr key={label}><td className="l">{label}</td><td>{pc(m.cagr, 2)}</td><td>{pct(m.vol, 2)}</td><td>{m.sharpe == null ? "" : nf(m.sharpe, 2)}</td><td>{pc(m.maxDrawdown, 1)}</td></tr>)}</tbody></table>
      <Fig fig={data.heldOut.figure} label="Growth of 1, held out" size="s" /></>
      : <div className="al-heldask"><TxIcon name="lock" /><span>{facts?.heldFrom ? `${dayText(facts.heldFrom)}${facts.dataTo && facts.dataTo >= facts.heldFrom ? ` – ${dayText(facts.dataTo)}` : " on"} is held out; nothing here uses it.` : "Held out; nothing here uses it."}</span>{onValidate && <TxButton size="sm" onClick={onValidate}>Validate on held-out</TxButton>}</div>}</Section>
    <Section title="Costs" sub="the same run with costs scaled">{data.costs ? <table className="qv-table"><thead><tr><th className="l">Costs</th><th>CAGR</th><th>Sharpe</th><th>Max drawdown</th></tr></thead>
      <tbody>{data.costs.map((row: Record<string, number | string>) => <tr key={row.label as string} className={row.label === "As set" ? "al-selrow" : undefined}><td className="l">{row.label}</td><td>{signed(row.cagr as number, 2)}</td><td>{row.sharpe == null ? "" : nf(row.sharpe as number, 2)}</td><td>{signed(row.maxDrawdown as number, 1)}</td></tr>)}</tbody></table>
      : <p className="fx-why" role="status">Skipped: the run took too long for the cost reruns. Run again for them.</p>}</Section></div>
    <Section title="Sensitivity to the estimate" sub="the same run with one estimate setting changed">{data.sensitivity?.length ? <table className="qv-table"><thead><tr><th className="l">Changed</th><th title="Average one-way difference in target weights a period">Weights move, pt</th><th>Sharpe, this</th><th>Sharpe, changed</th><th>Difference</th></tr></thead>
      <tbody>{data.sensitivity.map((row: Record<string, number | string>) => <tr key={row.name as string}><td className="l">{row.label ?? row.name}</td><td>{nf((row.move as number) * 100, 1)}</td><td>{row.sharpe == null ? "" : nf(row.sharpe as number, 2)}</td><td>{row.changed == null ? "" : nf(row.changed as number, 2)}</td>
        <td>{row.sharpe == null || row.changed == null ? "" : <span className={(row.changed as number) >= (row.sharpe as number) ? "al-up" : "al-down"}>{((row.changed as number) - (row.sharpe as number) >= 0 ? "+" : "−") + Math.abs((row.changed as number) - (row.sharpe as number)).toFixed(2)}</span>}</td></tr>)}</tbody></table>
      : <p className="fx-why" role="status">Skipped: the run took too long for the sensitivity reruns. Run again for them.</p>}</Section></>;
  return <Section title="Settings of this run"><table className="qv-table al-kv"><tbody>{settingRows(data.settings ?? {}, run, facts).map(([label, value], index) => value === undefined
    ? <tr key={index} className="al-sgh"><td className="l" colSpan={2}>{label}</td></tr> : <tr key={index}><td className="l al-dim">{label}</td><td className="l al-wrap">{value}</td></tr>)}</tbody></table></Section>;
}

/** The compared run's own line (the main one of its figure, or its drawdown) added to this figure with the compare role. */
function addLine(figure: Figure, other: Figure, number: number): Figure {
  if (!figure || "why" in figure || !other || "why" in other) return figure;
  const own = (other.data as any[]).find(trace => trace.meta?.role === "main" || trace.meta?.role === "drawdown");
  if (!own) return figure;
  return { ...figure, data: [...(figure.data as any[]), { ...own, name: `#${number}`, meta: { ...own.meta, role: "compare", key: "compare" } }] } as Figure;
}

export interface ResultsProps { api: AllocationApi; run: AllocationRun; compared: AllocationRun | null; tab: Tab; onTab(tab: Tab): void; onPeriod(date: string): void; onSetting?(group: string): void; strip: ReactNode; onValidate?: () => void; shock?: ShockControl; facts?: DataFacts }
export function Results({ api, run, compared, tab, onTab, onPeriod, onSetting, strip, onValidate, shock, facts }: ResultsProps) {
  const [data, setData] = useState<{ runId: string; tab: Tab; body: Record<string, any> } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [compareError, setCompareError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setError(null);
    setCompareError(null);
    // On Summary the compared run's line goes into Growth and Drawdown, dashed and named by its number.
    const withCompared = async (body: Record<string, any>) => {
      if (tab !== "summary" || !compared || compared.id === run.id) return body;
      // a compared run that cannot be read says so above the page, not a line quietly missing
      const other = await api.results(compared.id, "summary").catch(reason => { if (live) setCompareError(`#${compared.number} can't be drawn beside it: ${reason instanceof Error ? reason.message : String(reason)}`); return null; });
      return other ? { ...body, growth: addLine(body.growth, other.growth, compared.number), drawdown: addLine(body.drawdown, other.drawdown, compared.number) } : body;
    };
    void api.results(run.id, tab).then(withCompared).then(body => { if (live) setData({ runId: run.id, tab, body }); }, reason => { if (live) { setData(null); setError(reason instanceof Error ? reason.message : String(reason)); } });
    return () => { live = false; };
  }, [run.id, tab, shock?.added, compared?.id]);
  return <div className="qv-main">{strip}
    <TxTabs value={tab} options={RESULT_TABS} onChange={onTab} />
    <div className="qv-scroll">{compareError && <p className="fx-why" role="alert">{compareError}</p>}{error ? <p className="fx-why" role="alert">{error}</p> : data && data.runId === run.id && data.tab === tab ? <Body tab={tab} data={data.body} run={run} compared={compared} onPeriod={onPeriod} onSetting={onSetting} onValidate={onValidate} shock={shock} facts={facts} /> : <div className="fx-rskel" aria-busy="true"><TxSkeleton width="45%" /><TxSkeleton width="70%" /><TxSkeleton className="fx-rskel-plot" /></div>}</div></div>;
}
export const _nf = nf;
const yearNames = (rows: Record<string, any>[]) => Object.keys(rows[0] ?? {}).filter(key => key !== "year" && key !== "vsEqual");
