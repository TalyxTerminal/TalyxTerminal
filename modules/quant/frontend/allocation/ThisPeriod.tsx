/** This period's allocation beside every period's: the table that is the page's main view (weight, the published weight, the change, the risk share, the limit that binds),
    the trades it means, the monthly review of a published version, and the stacked history. Rows come from the run (or the trial); the history is the backend's figure. */
import { useRef, useState } from "react";
import { TxButton, TxIcon, TxInput, TxSegmented, TxTag } from "@talyx/sdk";
import { nf, pct, sgn } from "../shared/format";
import type { Figure, Row } from "./api";
import { Plot, type PlotPoint } from "../chart/Plot";
import { partColour } from "../chart/paint";

/** An asset's swatch is the colour of its band in the stacked chart: its group's colour, shaded from dark to light across the group's members (a lone member is the plain colour). */
export function assetColor(asset: string, rows: Row[], groups: string[]): string {
  const group = rows.find(item => item.asset === asset)?.group ?? "Other";
  const members = rows.filter(item => (item.group ?? "Other") === group);
  const at = members.findIndex(item => item.asset === asset);
  return partColour(Math.max(0, groups.indexOf(group)), members.length < 2 ? 0 : -0.5 + 0.8 * at / (members.length - 1));
}

const dpt = (value: number) => Math.abs(value) < .00005 ? <span className="al-dim">0.00</span> : <span className={value > 0 ? "al-up" : "al-down"}>{sgn(value * 100, 2)}</span>;
const kusd = (value: number) => { const size = Math.abs(value), sign = value < 0 ? "−" : ""; return size >= 1e6 ? `${sign}$${nf(size / 1e6, 2)}M` : size >= 1e3 ? `${sign}$${nf(size / 1e3, 1)}k` : `${sign}$${nf(size, 0)}`; };

export interface PeriodTableProps {
  lead: string; rows: Row[]; groups: string[]; reference: boolean; referenceLabel: string; referenceTitle?: string; base?: Row[] | null; baseLabel?: string;
  /** Assets whose prices in this period are their proxy's. */ proxied?: Set<string>;
  held?: string | null; onHold?: (asset: string) => void; onHeldWeight?: (weight: number) => void;
  /** The asset a check pointed at, its row selected. */ focus?: string | null;
}

export function PeriodTable({ lead, rows, groups, reference, referenceLabel, referenceTitle, base: baseRows, baseLabel, proxied, held, onHold, onHeldWeight, focus }: PeriodTableProps) {
  const top = Math.max(...rows.map(row => Math.max(row.weight, row.riskShare)), 0.01), bar = (value: number) => Math.max(1, Math.round(value / top * 80));
  const baseOf = (asset: string) => baseRows?.find(row => row.asset === asset)?.weight ?? 0;
  const row = (r: Row) => <tr key={r.asset} className={focus === r.asset ? "al-selrow" : undefined}>
    <td className="l"><span className="al-ticker"><i style={{ background: assetColor(r.asset, rows, groups) }} />{r.asset}{proxied?.has(r.asset) && <span className="al-dim" title="Prices before its own start are its proxy's">·p</span>}</span></td>
    <td className="al-lead" data-held={held === r.asset ? "" : undefined} onClick={onHold ? () => onHold(r.asset) : undefined}
      title={onHold ? (held === r.asset ? "Held by hand; click to change or release" : `Click to hold ${r.asset} at a weight you set`) : undefined}>
      {r.bound && r.bound !== "group" && r.bound !== "zero" && <span className="al-cap" title={r.bound === "cap" ? "At its limit" : "A fallback that could not keep this limit"}>{r.bound}</span>}
      {held === r.asset && <TxIcon name="lock" />}{held === r.asset && onHeldWeight ? <span className="al-holdinput" onClick={event => event.stopPropagation()}><TxInput numeric defaultValue={nf(r.weight * 100, 2)} suffix="%"
        onBlur={event => { const v = parseFloat(event.target.value); if (v >= 0 && v <= 100) onHeldWeight(v / 100); }} onKeyDown={event => { if (event.key === "Enter") (event.target as HTMLInputElement).blur(); }} /></span> : pct(r.weight, 2)}
</td>
    {baseRows && <td className="al-dim">{dpt(r.weight - baseOf(r.asset))}</td>}
    {reference && <><td className="al-dim">{r.published == null ? "—" : pct(r.published, 2)}</td><td>{r.published == null ? "—" : dpt(r.weight - r.published)}</td></>}
    <td>{pct(r.riskShare, 1)}</td><td className="l al-bars"><i style={{ width: bar(r.weight) }} /><i data-risk="" style={{ width: bar(r.riskShare) }} /></td></tr>;
  const group = (name: string) => {
    const members = rows.filter(item => (item.group ?? "Other") === name);
    const out = members.map(item => row(item));
    if (members.length < 2) return out;
    const sum = (list: Row[], pick: (item: Row) => number) => list.reduce((total, item) => total + pick(item), 0);
    const weight = sum(members, item => item.weight), ref = sum(members, item => item.published ?? 0);
    return [...out, <tr key={`g-${name}`} className="al-subtotal"><td className="l">{name}</td>
      <td className="al-lead">{members.some(item => item.bound === "group") && <span className="al-cap" title="This group is at its limit">cap</span>}{pct(weight, 2)}</td>
      {baseRows && <td className="al-dim">{dpt(weight - sum(members, item => baseOf(item.asset)))}</td>}
      {reference && <><td className="al-dim">{pct(ref, 2)}</td><td>{dpt(weight - ref)}</td></>}
      <td>{pct(sum(members, item => item.riskShare), 1)}</td><td /></tr>];
  };
  return <table className="qv-table al-period"><thead><tr><th className="l">Asset</th><th>{lead}</th>{baseRows && <th title={`${lead} less ${baseLabel}`}>vs {baseLabel}</th>}
    {reference && <><th title={referenceTitle}>{referenceLabel}</th><th title={`${lead} less ${referenceLabel}`}>Change, pt</th></>}<th title="Share of predicted risk">Risk share</th>
    <th className="l"><span className="al-key"><i />Weight<i data-risk="" />Risk</span></th></tr></thead>
    <tbody>{groups.map(name => group(name))}</tbody></table>;
}

export function TradesTable({ rows, groups, lead, capital, costBps }: { rows: Row[]; groups: string[]; lead: string; capital: number; costBps: Record<string, number> }) {
  // An asset with no holding to start from (a trial past the run's last period) has no trade to show: not a buy of its whole weight from cash.
  const trades = rows.map((r, i) => { const amount = r.drifted == null ? null : (r.weight - r.drifted) * capital; return { r, i, amount, cost: amount === null ? 0 : Math.abs(amount) * (costBps[r.group ?? ""] ?? 0) / 1e4 }; });
  const known = trades.some(t => t.amount !== null);
  const each = trades.reduce((s, t) => s + Math.abs(t.amount ?? 0), 0) / 2, cost = trades.reduce((s, t) => s + t.cost, 0);
  return <table className="qv-table al-period"><thead><tr><th className="l">Asset</th><th title="The last period's holdings after this month's moves">Now</th><th>{lead}</th><th>Trade, pt</th><th>Amount</th><th>Cost</th></tr></thead>
    <tbody>{trades.map(t => <tr key={t.r.asset}><td className="l"><span className="al-ticker"><i style={{ background: assetColor(t.r.asset, rows, groups) }} />{t.r.asset}</span></td>
      <td className="al-dim">{t.r.drifted == null ? "—" : pct(t.r.drifted, 2)}</td><td className="al-lead">{pct(t.r.weight, 2)}</td><td>{t.r.drifted == null ? "" : dpt(t.r.weight - t.r.drifted)}</td>
      <td className={t.amount === null ? undefined : t.amount >= 0 ? "al-up" : "al-down"}>{t.amount === null ? "" : kusd(t.amount)}</td><td className="al-dim">{t.cost >= .5 ? `−$${nf(t.cost, 0)}` : ""}</td></tr>)}
      <tr className="al-subtotal"><td className="l">Portfolio</td><td className="al-dim">{known ? "100%" : "—"}</td><td className="al-lead">100%</td><td /><td className="al-dim" title="Bought, and the same sold">{known ? `${kusd(each)} each way` : ""}</td><td className="al-dim">{cost >= .5 ? `−$${nf(cost, 0)}` : ""}</td></tr></tbody></table>;
}

/** The monthly review of a published version: accept as computed, apply the trial as this period's one-off (with a reason), or change the method.
    Apply with no reason written marks the reason field and puts the cursor in it (the library keeps a one-off only with why). */
export interface ReviewProps { waiting?: boolean; label: string; edits: string[]; reason: string; decided: string | null; since: string | null; computed?: string | null; onReason: (text: string) => void; onAccept: () => void; onOneOff: () => void; onNewVersion: () => void; onDiscard: () => void }
export function ReviewBox({ waiting, label, edits, reason, decided, since, computed, onReason, onAccept, onOneOff, onNewVersion, onDiscard }: ReviewProps) {
  const field = useRef<HTMLInputElement>(null);
  const [asked, setAsked] = useState(false);
  const missing = asked && !reason.trim();
  const apply = () => {if (reason.trim()) onOneOff(); else {setAsked(true); field.current?.focus();}};
  if (decided) return <div className="al-reviewed"><TxIcon name="check" />{decided}</div>;
  return <div className="al-review">
    <div className="al-review-head"><b>{label} review</b><TxTag tone="watch">Waiting</TxTag>{computed && <span className="al-dim">{computed}</span>}<span className="tx-spacer" />{since && <span className="al-dim">{since}</span>}</div>
    {edits.length > 0 && <div className="al-review-trial"><span>Trial: <b>{edits.join(", ")}</b></span>
      <TxInput ref={field} value={reason} aria-invalid={missing || undefined} placeholder={missing ? "Write why this period differs" : "Reason, kept with this period in the library"} onChange={event => onReason(event.target.value)} /></div>}
    <div className="al-review-actions">
      {edits.length ? <TxButton variant="primary" size="sm" disabled={waiting} onClick={apply}>Apply as this period's one-off adjustment</TxButton> : <TxButton variant="primary" size="sm" onClick={onAccept}>Accept as computed</TxButton>}
      <TxButton size="sm" onClick={onNewVersion}>Change method → new version</TxButton>
      {edits.length > 0 && <TxButton variant="quiet" size="sm" onClick={onDiscard}>Discard trial</TxButton>}</div></div>;
}

/** Whether a by-group figure has more than one group to stack: with one, it is a single band filling the whole chart and says nothing, so the assets are shown instead. */
export const groupsDiffer = (figure: Figure) => !figure || "why" in figure || (figure.data as unknown[]).length > 1;

/** Every period's target stacked, by group or by asset (the backend's figure). Clicking a bar shows that period in the table. */
export function HistoryChart({ by, onBy, figures, onSelect }: { by: "group" | "asset"; onBy: (by: "group" | "asset") => void; figures: { group: Figure; asset: Figure }; onSelect: (date: string) => void }) {
  const grouped = groupsDiffer(figures.group);
  const figure = by === "group" && grouped ? figures.group : figures.asset;
  return <div className="al-history">
    <div className="tx-regionhead"><h3 title="Click a period to show it in the table">Every period</h3><span className="tx-spacer" /><span className="tx-regionkeys" data-tx-keys="" />
      {grouped && <TxSegmented label="Stack by" value={by} onChange={onBy} options={[{ value: "group", label: "Group" }, { value: "asset", label: "Asset" }]} />}</div>
    {!figure ? <p className="fx-why" role="status">This chart did not come out</p> : "why" in figure ? <p className="fx-why" role="status">This chart did not come out: {figure.why}</p>
      : <div className="fx-rch"><Plot figure={figure} ariaLabel="Target weights at every period, stacked" onPoint={(point: PlotPoint) => onSelect(String(point.x))} /></div>}
  </div>;
}


/** What the trial says beside its tag: the first one waits for the research process to start; a later one is solving; a finished one shows how long it took
 * from the request to the answer (the solver's own time, a few ms, always read "0.0 s"). */
export function trialStatus(state: { busy: boolean; engineWarm?: boolean; seconds?: number }, hasResult: boolean): string {
  if (state.busy) return state.engineWarm === false && !hasResult ? "Starting the research process…" : "Solving…";
  return hasResult && state.seconds !== undefined ? `${state.seconds.toFixed(2)} s` : "";
}

