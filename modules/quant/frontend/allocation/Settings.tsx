/** The allocation's two layers of settings: the standard flow's declarations (pool, objective, risk estimate, limits, rebalance, costs) and the platform's run settings. A value changed
    since the shown run has the orange edge; a value the failed run tripped on is marked too. A row shows only where the chosen objective's class accepts it. */
import { t } from "../i18n";
import { TxButton, TxCheckbox, TxIcon, TxInput, TxParamGroup, TxParamLayer, TxParamRow, TxParamValue, TxParams, TxPopover, TxSegmented, TxSelect, TxSheet, TxSkeleton } from "@talyx/sdk";
import { useEffect, useState, type ReactNode } from "react";
import type { Parameter, ParamValue } from "../objects";
import type { PoolRow, Prep } from "./api";
import { visible } from "./settingsRules";
import { mdy } from "../shared/format";
import { DayInput } from "../shared/DayInput";

type Values = Record<string, any>;
const num = (value: unknown) => String(value ?? "");
/** A day the backend sends as 2024-11-06, shown as Nov 6, 2024; anything else as it came. */
/** The unit a risk cap is written in, as the engine applies it: a volatility (and a variance, as its volatility) a year, CVaR a period's loss, a drawdown over the lookback. */
export const riskCapUnit = (measure: string, align: string) =>
  ["standard_deviation", "semi_deviation", "variance"].includes(measure) ? t("% a year") : measure === "cvar" ? (align === "weekly" ? t("% a week") : align === "monthly" ? t("% a month") : t("% a day")) : t("% in lookback");

export const shownDay = (value: string | undefined) => /^\d{4}-\d{2}-\d{2}$/.test(value ?? "") ? mdy(new Date(`${value}T00:00:00Z`)) : value ?? "";
/** The numbers in a typed list ("126, 252, 504"); anything that is not a number is left out. */
export const listValues = (text: string): number[] => text.split(",").map(item => item.trim()).filter(Boolean).map(Number).filter(Number.isFinite);

export interface SettingsProps {
  prep: Prep; values: Values; changed: Set<string>; runLabel: string | null;
  /** What else differs from the run shown, outside these settings: "assets", "dates" (an added BTC still read "Same as #2"). */
  otherChanges?: string[]; bad?: string[]; flash?: string | null; commonStart?: string | null; heldOutFrom?: string | null; dataLimits?: [string, string][];
  /** Lists typed into a number setting ("126, 252, 504"): the run becomes a sweep; the setting itself keeps its one value. */
  lists?: Record<string, string>; onList?: (name: string, text: string | undefined) => void;
  /** Where the held-out part starts (2024-11-01), set here as in the other three blocks' run settings; null clears it. */
  heldOut?: string | null; onHeldOut?: (day: string | null) => void;
  onValue: (name: string, value: ParamValue) => void; onRevert: () => void; onRevertRow?: (...names: string[]) => void; onEditPool: () => void;
}

export interface LinkCandidate {kind: "run" | "library"; runId?: string; name: string; from?: string; to?: string}
const ALIGN: [string, string][] = [["common", t("Common days")], ["week", t("Weekly")], ["month", t("Monthly")]];

/** The pool's rows with a strategy run or a library item added as one more asset: its own group, no currency, and the run it reads. */
const optionLabels: Record<string, string> = {
  "common": t("Common days"),
  "weekly": t("Weekly"),
  "monthly": t("Monthly"),
  "equal_weight": t("Equal weight"),
  "inverse_volatility": t("Inverse volatility"),
  "min_risk": t("Minimum risk"),
  "max_ratio": t("Maximum ratio"),
  "max_utility": t("Maximum utility"),
  "max_return": t("Maximum return"),
  "risk_parity": t("Risk parity"),
  "risk_budget": t("Risk budget by group"),
  "max_diversification": t("Maximum diversification"),
  "hrp": t("Hierarchical risk parity"),
  "herc": t("Hierarchical equal risk contribution"),
  "nco": t("Nested clusters"),
  "benchmark_tracking": t("Benchmark tracking"),
  "schur": t("Schur complementary"),
  "variance": t("Variance"),
  "semi_variance": t("Semi variance"),
  "standard_deviation": t("Standard deviation"),
  "semi_deviation": t("Semi deviation"),
  "mean_absolute_deviation": t("Mean absolute deviation"),
  "cvar": t("CVaR"),
  "evar": t("EVaR"),
  "worst_realization": t("Worst realization"),
  "cdar": t("CDaR"),
  "max_drawdown": t("Max drawdown"),
  "average_drawdown": t("Average drawdown"),
  "edar": t("EDaR"),
  "first_lower_partial_moment": t("First lower partial moment"),
  "ulcer_index": t("Ulcer index"),
  "gini_mean_difference": t("Gini mean difference"),
  "historical": t("Historical"),
  "shrunk": t("Shrunk"),
  "equilibrium": t("Equilibrium"),
  "pearson": t("Pearson"),
  "kendall": t("Kendall"),
  "spearman": t("Spearman"),
  "covariance": t("Covariance"),
  "mutual_information": t("Mutual information"),
  "ward": t("Ward"),
  "single": t("Single"),
  "complete": t("Complete"),
  "average": t("Average"),
  "empirical": t("Sample"),
  "ledoit_wolf": t("Ledoit-Wolf shrinkage"),
  "oas": t("Oracle approximating shrinkage"),
  "ew": t("Exponentially weighted"),
  "gerber": t("Gerber"),
  "denoise": t("Denoised"),
  "detone": t("Detoned"),
  "graphical_lasso": t("Graphical lasso"),
  "geodesic": t("Geodesic shrinkage"),
  "tbill": t("13-week T-bill"),
  "fixed": t("Fixed"),
  "zero": t("Zero"),
  "quarterly": t("Quarterly"),
};

export function withLink(rows: Values[], item: LinkCandidate): Values[] {
  return [...rows.filter(row => row.ticker !== item.name), { ticker: item.name, group: t("Strategies"), currency: "", from: item.from ?? "", proxy: "", kind: item.kind, runId: item.runId ?? "" }];
}

/** The asset pool's side sheet: one row for each asset with its group, currency, first day and the proxy that stands in before it, the way to add a strategy run or a library item as an
    asset, and how assets that trade on different days are lined up. */
export function PoolSheet({ open, pool, rows, align, currency, candidates, onClose, onChange, onSymbols, onAlign, onCurrency, onOpenLinks }: {
  open: boolean; pool: PoolRow[]; rows: Values[]; align: string; currency: string; candidates: {runs: LinkCandidate[]; library: LinkCandidate[]} | null;
  onClose(): void; onChange(rows: Values[]): void; onSymbols(): void; onAlign(value: string): void; onCurrency(value: string): void; onOpenLinks(): void;
}) {
  const [listing, setListing] = useState<"run" | "library" | null>(null);
  const row = (ticker: string) => rows.find(item => item.ticker === ticker) ?? {};
  const set = (item: PoolRow, patch: Values) => onChange([...rows.filter(other => other.ticker !== item.ticker), { ticker: item.ticker, group: item.group, currency: item.currency ?? "", from: "", proxy: "", kind: "", runId: "", ...row(item.ticker), ...patch }]);
  const links = pool.filter(item => (item as PoolRow & {kind?: string}).kind);
  const markets = pool.filter(item => !(item as PoolRow & {kind?: string}).kind);
  const chosen = new Set(links.map(item => item.ticker));
  const listed = listing === "run" ? candidates?.runs ?? [] : candidates?.library ?? [];
  const shown = listed.filter(item => !chosen.has(item.name));  // one already in the pool is not offered again
  return <TxSheet open={open} title={t("Asset pool")} label={t("asset pool")} onOpenChange={next => { if (!next) onClose(); }} footer={<><TxButton onClick={onSymbols}><TxIcon name="plus" />{t("Add or remove assets")}</TxButton><span className="tx-spacer" /><TxButton variant="primary" onClick={onClose}>{t("Done")}</TxButton></>}>
    <div className="al-poolsheet">
      <table className="al-mini"><thead><tr><th>{t("Asset")}</th><th>{t("Group")}</th><th>{t("Currency")}</th><th>{t("From")}</th><th>{t("Proxy before")}</th></tr></thead>
        <tbody>{markets.map(item => <tr key={item.ticker}><td title={item.name}>{item.ticker}</td>
          <td><TxInput aria-label={t("Group of {ticker}", { ticker: item.ticker })} value={row(item.ticker).group ?? item.group} onChange={event => set(item, { group: event.target.value })} /></td>
          <td className="al-dim">{item.currency ?? ""}</td>
          <td><TxInput aria-label={t("From of {ticker}", { ticker: item.ticker })} placeholder={t("first price")} value={row(item.ticker).from ?? ""} onChange={event => set(item, { from: event.target.value })} /></td>
          <td><TxInput aria-label={t("Proxy of {ticker}", { ticker: item.ticker })} placeholder={t("none")} value={row(item.ticker).proxy ?? ""} onChange={event => set(item, { proxy: event.target.value })} /></td></tr>)}
          {links.map(item => <tr key={item.ticker}><td title={item.name}>{item.ticker}</td><td><TxInput aria-label={t("Group of {ticker}", { ticker: item.ticker })} value={row(item.ticker).group ?? item.group} onChange={event => onChange(rows.map(other => other.ticker === item.ticker ? { ...other, group: event.target.value } : other))} /></td>
            <td className="al-dim">{(item as PoolRow & {kind?: string}).kind === "run" ? t("run") : t("library")}</td><td className="al-dim">{shownDay(item.from)}</td>
            <td><TxButton variant="quiet" size="sm" onClick={() => onChange(rows.filter(other => other.ticker !== item.ticker))}>{t("Remove")}</TxButton></td></tr>)}</tbody></table>
      <div className="al-poolbtns"><TxButton size="sm" onClick={() => { onOpenLinks(); setListing(listing === "run" ? null : "run"); }}>{t("Add a strategy run…")}</TxButton>
        <TxButton size="sm" onClick={() => { onOpenLinks(); setListing(listing === "library" ? null : "library"); }}>{t("Add from the library…")}</TxButton></div>
      {listing && <div className="al-links" role="list" aria-label={listing === "run" ? t("Strategy runs") : t("Library items")}>
        {candidates === null ? <TxSkeleton width="50%" /> : shown.length === 0 ? <span className="al-dim">{listed.length > 0 ? t("All of them are in the pool") : listing === "run" ? t("No finished strategy run in this workspace") : t("No published allocation in the library yet")}</span>
          : shown.map(item => <button type="button" key={item.runId ?? item.name} className="al-linkrow" onClick={() => { onChange(withLink(rows, item)); setListing(null); }}>
            <span>{item.name}</span><span className="al-dim">{shownDay(item.from)} – {shownDay(item.to)}</span></button>)}</div>}
      <div className="al-poolrow"><span>{t("Align")}</span><TxSegmented label={t("Align")} value={align} onChange={onAlign} options={ALIGN.map(([value, label]) => ({ value, label }))} /></div>
      <div className="al-poolrow"><span>{t("Currency")}</span><TxInput aria-label={t("Currency")} value={currency} onChange={event => onCurrency(event.target.value)} /></div>
    </div>
  </TxSheet>;
}

/** The held-out start as a date box with the lock, saved when the box is left. */
/** The held-out start, typed as the app writes days (not the browser's date box, design.md 11-13). */
function HeldOut({ value, onChange }: { value: string | null; onChange(day: string | null): void }) {
  return <span className="fx-held"><TxIcon name="lock" /><DayInput label={t("Held out from")} value={value} empty onChange={onChange} /></span>;
}

export function Settings({ prep, values, changed, runLabel, otherChanges = [], bad = [], flash, commonStart, heldOutFrom, heldOut = null, onHeldOut, dataLimits = [], lists = {}, onList, onValue, onRevert, onRevertRow, onEditPool }: SettingsProps) {
  const caps = prep.capabilities[values.objective];
  const decl = (name: string): Parameter | undefined => prep.groups.flatMap(group => group.settings).find(item => item.name === name);
  const shows = (name: string) => { const item = decl(name); return item ? visible(item, values, caps) : false; };
  const options = (name: string) => (decl(name)?.options ?? []).map(option => <option key={String(option.value)} value={String(option.value)}>{optionLabels[String(option.value)] ?? option.label}</option>);
  const rowReset = (...names: string[]) => names.some(name => changed.has(name) || lists[name] !== undefined) ? () => onRevertRow?.(...names) : undefined;
  const ch = (...keys: string[]) => keys.some(key => changed.has(key) || bad.includes(key));
  const mark = (key: string, row: ReactNode) => bad.includes(key) ? <div className="al-bad-row">{row}</div> : row;
  // Every group starts as one line of summary; a jump to it (a check, a failed run's field) opens it.
  const [opened, setOpened] = useState<Set<string>>(new Set());
  useEffect(() => {if (flash) setOpened(current => current.has(flash) ? current : new Set([...current, flash]));}, [flash]);
  const group = (id: string, title: string, summary: string, keys: string[], children: ReactNode) => {
    const open = opened.has(id) || keys.some(key => bad.includes(key));
    return <div className={flash === id ? "al-flash" : undefined} id={`al-g-${id}`}>
      <TxParamGroup key={open ? "open" : "shut"} defaultOpen={open} title={title} summary={summary} changed={ch(...keys)}>{children}</TxParamGroup></div>;
  };
  const label = (name: string) => optionLabels[String(values[name])] ?? decl(name)?.options?.find(option => String(option.value) === String(values[name]))?.label ?? String(values[name] ?? "");
  const blank = (value: unknown) => value === "" || value === null || value === undefined;
  const select = (name: string, label: string, hint?: string) => <TxParamRow label={label} hint={hint} changed={ch(name)} onRevert={rowReset(name)}><TxSelect value={String(values[name])} onChange={event => onValue(name, event.target.value)}>{options(name)}</TxSelect></TxParamRow>;
  const text = (name: string, label: string, suffix?: string, hint?: string, placeholder?: string) => mark(name, <div id={`fx-row-${name}`}><TxParamRow label={label} hint={hint} changed={ch(name)} onRevert={rowReset(name)}><TxInput numeric={name !== "linear" && name !== "benchmark" && !name.endsWith("parameters")}
    value={num(values[name])} suffix={suffix} placeholder={placeholder} onChange={event => onValue(name, event.target.value)} /></TxParamRow></div>);
  const number = (name: string, label: string, suffix?: string, hint?: string) => {
    const typed = lists[name];
    const many = typed === undefined ? 0 : listValues(typed).length;
    return <TxParamRow label={label} hint={hint} changed={ch(name) || typed !== undefined} onRevert={rowReset(name)}><TxInput numeric={typed === undefined} value={typed ?? num(values[name])} suffix={many > 1 ? `×${many}` : suffix} aria-label={label}
      title={t("Several values, comma-separated, make a sweep")}
      onChange={event => {
        const text = event.target.value;
        if (text.includes(",")) {onList?.(name, text); return;}
        onList?.(name, undefined);
        const v = Number(text);
        if (text !== "" && Number.isFinite(v)) onValue(name, v);
      }} /></TxParamRow>;
  };
  const [limitsOpen, setLimitsOpen] = useState(false);
  const names = Array.from(new Set(prep.pool.map(item => item.group)));
  const limits: Values[] = names.map(name => (values.group_limits ?? []).find((row: Values) => row.group === name) ?? { group: name, max: "", min: "", count: "" });
  const budget: Values[] = names.map(name => (values.budget ?? []).find((row: Values) => row.group === name) ?? { group: name, share: 0 });
  const shares = budget.reduce((sum, row) => sum + Number(row.share || 0), 0);
  const setRow = (field: string, rows: Values[], name: string, key: string, value: string) => onValue(field, rows.map(row => row.group === name ? { ...row, [key]: value } : row) as unknown as ParamValue);
  const limitSummary = [blank(values.max_weight) ? "" : t("each ≤ {max}%", { max: String(values.max_weight) }), ...limits.filter(row => !blank(row.max) || !blank(row.min))
    .map(row => `${row.group}${blank(row.min) ? "" : ` ≥ ${row.min}%`}${blank(row.max) ? "" : ` ≤ ${row.max}%`}`)].filter(Boolean).join(", ") || t("none");
  const costSummary = names.length ? t("{costs} bps", { costs: names.map(name => `${name} ${Number((values.costs ?? []).find((item: Values) => item.group === name)?.bps ?? 0)}`).join(", ") }) : "";
  const runSummary = [Number(values.capital) > 0 ? `$${Number(values.capital).toLocaleString("en-US")}` : "", label("risk_free"), Number(values.cost_multiple ?? 1) !== 1 ? t("costs ×{multiple}", { multiple: String(values.cost_multiple) }) : "",
    heldOutFrom ? t("held out from {date}", { date: heldOutFrom }) : ""].filter(Boolean).join(", ");
  // "2 settings and dates changed since #16 · Revert": one sentence; Revert puts back the settings (the data settings have their own chip).
  const what = [...(changed.size ? [changed.size === 1 ? t("{count} setting", { count: changed.size }) : t("{count} settings", { count: changed.size })] : []), ...otherChanges].join(t(" and "));
  return <TxParams footer={what ? <span>{t("{changes} changed since {runLabel}", { changes: what.replace(/^./, first => first.toUpperCase()), runLabel: runLabel ?? "" })}{changed.size > 0 && <> · <button type="button" className="al-link" onClick={onRevert}>{t("Revert")}</button></>}</span>
    : runLabel ? <span>{t("Same as {runLabel}", { runLabel })}</span> : undefined}>
    <TxParamLayer title={t("Code settings")} source={t("standard flow")} changed={changed.size} onReset={onRevert} />
    {group("pool", t("Asset pool"), names.length === 1 ? t("{assets} assets, 1 group", {assets: prep.pool.length}) : t("{assets} assets, {groups} groups", {assets: prep.pool.length, groups: names.length}), ["pool", "align", "currency"], <>
      <TxParamRow label={t("Assets")}><span className="al-inline"><TxParamValue text>{(names.length === 1 ? t("{poolCount} in {namesCount} group", { poolCount: prep.pool.length, namesCount: names.length }) : t("{poolCount} in {namesCount} groups", { poolCount: prep.pool.length, namesCount: names.length }))}</TxParamValue><TxButton variant="quiet" size="sm" onClick={onEditPool}>{t("Edit")}</TxButton></span></TxParamRow>
      {dataLimits.length > 0 && <TxParamRow wide><TxPopover open={limitsOpen} onOpenChange={setLimitsOpen} label={t("Data limits")} align="start"
        trigger={<TxButton variant="quiet" size="sm"><TxIcon name="info" />{t("Data limits {dataLimitsCount}", { dataLimitsCount: dataLimits.length })}</TxButton>}>
        <div className="al-limits">{dataLimits.map(([name, text]) => <div key={name} className="al-limit"><span className="al-dim">{name}</span><span>{text}</span></div>)}</div></TxPopover></TxParamRow>}
      {commonStart && <TxParamRow label={t("Common start")} hint={t("The first day every asset in the pool has a price")}><TxParamValue>{commonStart}</TxParamValue></TxParamRow>}
    </>)}
    {group("objective", t("Objective"), [label("objective"), caps?.measure ? label("risk_measure").toLowerCase() : ""].filter(Boolean).join(", "), ["objective", "risk_measure", "expected_returns", "min_return", "budget", "robust_returns", "robust_covariance", "confidence", "l1", "l2", "objective_parameters", "distance", "linkage"], <>
      {select("objective", t("Objective"))}
      {caps?.budget && <TxParamRow wide changed={ch("budget")} onRevert={rowReset("budget")}><table className="tx-params-mini"><thead><tr><th>{t("Risk budget")}</th><th>{t("Share of risk")}</th></tr></thead><tbody>{budget.map(row => <tr key={row.group}><td>{row.group}</td>
        <td><TxInput numeric value={num(row.share)} suffix="%" onChange={event => setRow("budget", budget, row.group, "share", event.target.value)} /></td></tr>)}</tbody></table>
        {Math.abs(shares - 100) > 1e-6 && <div className="al-bad">{t("Shares add up to {shares}%", { shares })}</div>}</TxParamRow>}
      {caps?.tree && <>{select("distance", t("Distance"))}{["hrp", "herc", "schur"].includes(values.objective) && select("linkage", t("Linkage"))}</>}
      {caps?.measure && select("risk_measure", t("Risk measure"), t("What the objective spreads or reduces"))}
      {values.objective === "min_risk" && text("min_return", t("Return at least"), t("% a year"), t("The least risk that still expects this return"), "none")}
      {(caps?.returns || (values.objective === "min_risk" && values.min_return !== "" && values.min_return != null)) && select("expected_returns", t("Expected returns"), t("How the objective estimates each asset's return"))}
      {(caps?.robustReturns || caps?.robustCovariance) && <TxParamRow wide><span className="al-inline">{caps.robustReturns && <TxCheckbox checked={Boolean(values.robust_returns)} onCheckedChange={next => onValue("robust_returns", next)} label={t("Robust returns")} />}
        {caps.robustCovariance && values.risk_measure === "variance" && <TxCheckbox checked={Boolean(values.robust_covariance)} onCheckedChange={next => onValue("robust_covariance", next)} label={t("Robust covariance")} />}</span></TxParamRow>}
      {(values.robust_returns || values.robust_covariance) && number("confidence", t("Confidence"), "%")}
      {caps?.regularization && <TxParamRow label={t("Regularization")} hint={t("Penalties that pull weights toward equal")}><span className="al-inline"><TxInput numeric aria-label="L1" suffix="L1" placeholder="0" value={num(values.l1)}
        onChange={event => { const v = Number(event.target.value); if (Number.isFinite(v)) onValue("l1", v); }} /><TxInput numeric aria-label="L2" suffix="L2" placeholder="0" value={num(values.l2)} onChange={event => { const v = Number(event.target.value); if (Number.isFinite(v)) onValue("l2", v); }} /></span></TxParamRow>}
      {text("objective_parameters", t("Objective options"), undefined, t("key=value; key=value, the objective's own settings"), "none")}
    </>)}
    {group("risk", t("Risk estimate"), t("{days} days, {covariance}", {days: num(values.lookback), covariance: label("covariance").toLowerCase()}), ["lookback", "covariance", "covariance_parameters"], <>
      <TxParamRow label={t("Returns")}><TxParamValue text>{t("Daily, close to close")}</TxParamValue></TxParamRow>
      {number("lookback", t("Lookback"), "days")}
      {select("covariance", t("Covariance"))}
      {text("covariance_parameters", t("Estimator options"), undefined, t("key=value; key=value, the covariance estimate's own settings"), "none")}
    </>)}
    {group("limits", t("Limits"), limitSummary, ["max_weight", "min_weight", "group_limits", "cardinality", "threshold", "max_turnover", "max_tracking", "risk_cap", "linear", "long_cap", "short_cap", "net", "cost_aware"], <>
      {text("max_weight", t("Each asset"), t("% at most"))}
      {Number(values.short_cap) > 0 && text("min_weight", t("Each asset"), t("% at least"))}
      {bad.includes("group_limits") || names.length > 0 ? mark("group_limits", <TxParamRow wide changed={ch("group_limits")} onRevert={rowReset("group_limits")}><table className="tx-params-mini"><thead><tr><th>{t("By group")}</th><th>{t("Min")}</th><th>{t("Max")}</th>{caps?.count && <th>{t("At most")}</th>}</tr></thead>
        <tbody>{limits.map(row => <tr key={row.group}><td>{row.group}</td>
          <td><TxInput numeric placeholder="0" value={num(row.min)} suffix="%" onChange={event => setRow("group_limits", limits, row.group, "min", event.target.value)} /></td>
          <td><TxInput numeric placeholder="100" value={num(row.max)} suffix="%" onChange={event => setRow("group_limits", limits, row.group, "max", event.target.value)} /></td>
          {caps?.count && <td><TxInput numeric placeholder={t("any")} value={num(row.count)} onChange={event => setRow("group_limits", limits, row.group, "count", event.target.value)} /></td>}</tr>)}</tbody></table></TxParamRow>) : null}
      {shows("cardinality") && text("cardinality", t("At most"), "n", t("Hold no more than this many assets"), t("no limit"))}
      {shows("threshold") && text("threshold", t("If held"), "%", t("An asset that is held at all holds at least this much"), "any")}
      {shows("max_turnover") && text("max_turnover", t("Turnover"), "%", t("Each asset's weight moves at most this much at a rebalance; the first period buys in freely"), t("no limit"))}
      {shows("max_tracking") && text("max_tracking", t("Tracking"), "%", t("Tracking error against the benchmark (Run settings), a year, at most"), t("no limit"))}
      {caps && caps.riskCaps.length > 0 && <TxParamRow label={t("Risk cap")} changed={ch("risk_cap", "risk_cap_measure")} onRevert={rowReset("risk_cap", "risk_cap_measure")}><span className="al-inline"><TxSelect value={String(values.risk_cap_measure)} onChange={event => onValue("risk_cap_measure", event.target.value)}>
        {options("risk_cap_measure").filter(option => caps.riskCaps.includes(String(option.props.value)))}</TxSelect><TxInput numeric placeholder={t("no limit")} suffix={riskCapUnit(String(values.risk_cap_measure), String(values.align ?? "common"))} value={num(values.risk_cap)} onChange={event => onValue("risk_cap", event.target.value)} /></span></TxParamRow>}
      {caps?.short ? <TxParamRow wide changed={ch("long_cap", "short_cap", "net")} onRevert={rowReset("long_cap", "short_cap", "net")}><table className="tx-params-mini"><thead><tr><th>{t("Long ≤")}</th><th>{t("Short ≤")}</th><th>{t("Net")}</th></tr></thead><tbody><tr>
        {(["long_cap", "short_cap", "net"] as const).map(name => <td key={name}><TxInput numeric aria-label={name} value={num(values[name])} suffix="%" onChange={event => { const v = Number(event.target.value); if (Number.isFinite(v)) onValue(name, v); }} /></td>)}</tr></tbody></table></TxParamRow>
        : <TxParamRow label={t("Long / short")}><TxParamValue text>{t("Long only, fully invested")}</TxParamValue></TxParamRow>}
      {text("linear", t("Other"), undefined, t("Linear constraints, one a line, such as SPY + QQQ <= 0.4 or Bonds >= 0.2"), "none")}
      {caps?.costAware && <TxParamRow wide changed={ch("cost_aware")} onRevert={rowReset("cost_aware")}><TxCheckbox checked={Boolean(values.cost_aware)} onCheckedChange={next => onValue("cost_aware", next)} label={t("Count costs in the objective")} /></TxParamRow>}
    </>)}
    {group("rebalance", t("Rebalance"), label("frequency"), ["frequency"], <>
      {select("frequency", t("When"))}
      <TxParamRow label={t("At")}><TxParamValue text>{t("Month-end close; trades at the next close")}</TxParamValue></TxParamRow>
    </>)}
    {group("costs", t("Costs by group"), costSummary, ["costs"], <>
      <TxParamRow wide changed={ch("costs")} onRevert={rowReset("costs")}><table className="tx-params-mini"><thead><tr><th>{t("Group")}</th><th>{t("bps a trade")}</th></tr></thead><tbody>{names.map(name => { const row = (values.costs ?? []).find((item: Values) => item.group === name) ?? { group: name, bps: 0 };
        return <tr key={name}><td>{name}</td><td><TxInput numeric value={num(row.bps)} onChange={event => { const v = Number(event.target.value); if (!Number.isFinite(v)) return; const rows = (values.costs ?? []).filter((item: Values) => item.group !== name);
          onValue("costs", [...rows, { group: name, bps: v }] as unknown as ParamValue); }} /></td></tr>; })}</tbody></table></TxParamRow>
    </>)}
    <TxParamLayer title={t("Run settings")} source={t("platform")} />
    {group("run", t.c("run settings", "Run"), runSummary, ["cost_multiple", "capital", "risk_free", "risk_free_pct", "benchmark"], <>
      {number("capital", t("Capital"), "USD", t("What a rebalance's trades are sized on"))}
      {select("risk_free", t("Risk-free"), t("What the Sharpe and a maximum ratio are over: the 13-week US T-bill (^IRX) of each day, a fixed rate, or zero"))}
      {values.risk_free === "fixed" && number("risk_free_pct", t("Rate"), t("% a year"))}
      {text("benchmark", t("Benchmark"), undefined, t("What benchmark tracking and the tracking error follow: Market symbols with weights, in the pool or not"), "SPY 60%, IEF 40%")}
      <TxParamRow label={t("Also")}><TxParamValue text>{t("Equal weight, same costs")}</TxParamValue></TxParamRow>
      {number("cost_multiple", t("Costs"), t("× the above"))}
      {onHeldOut ? <TxParamRow label={t("Held out from")} hint={t("Runs and checks stop before it; Validate on held-out scores one run on it")}><HeldOut value={heldOut} onChange={onHeldOut} /></TxParamRow>
        : heldOutFrom && <TxParamRow label={t("Held out from")}><TxParamValue icon="lock">{heldOutFrom}</TxParamValue></TxParamRow>}
    </>)}
  </TxParams>;
}
