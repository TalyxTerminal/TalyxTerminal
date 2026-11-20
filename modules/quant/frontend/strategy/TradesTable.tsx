/* The trades of a run, a page at a time from the backend (the Work file carries the first 5,000). A click on a row picks the trade: the chart goes to it and the log shows it. */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { TxIcon, TxSegmented } from "@talyx/sdk";
import { mdy, nf, pc, tone } from "../factor/format";
import type { StrategyApi, Trade } from "./api";
import { useVirtual } from "./useVirtual";

export const TRADE_ROW = 26;

const EXITS = ["any", "stop", "trail", "target", "channel", "signal", "liquidation", "open at the end"];
export const exitLabel = (value: string) => value === "open at the end" ? "Open" : value[0].toUpperCase() + value.slice(1);

/** A trade's time in UTC as the draft writes it: "11-11 09:00", the year only in the hover. */
export const utcStamp = (ms: number | null) => {
  if (ms === null) return "—";
  const at = new Date(ms), two = (value: number) => String(value).padStart(2, "0");
  return `${two(at.getUTCMonth() + 1)}-${two(at.getUTCDate())} ${two(at.getUTCHours())}:${two(at.getUTCMinutes())}`;
};
const signed = (value: number | null, digits = 2) => value === null ? "—" : (value > 0 ? "+" : value < 0 ? "−" : "") + nf(Math.abs(value), digits);

export function TradesTable({api, runId, picked, onPick, extra, during, onClearDuring, from}: {api: StrategyApi; runId: string; currency: string; picked: number | null; onPick(trade: Trade): void; extra?: ReactNode; during?: {n?: number; label?: string; from: number; to: number} | null; onClearDuring?(): void; from?: number | null}) {
  const [side, setSide] = useState<"all" | "Long" | "Short" | "losing">("all");
  const [exit, setExit] = useState("any");
  const [rows, setRows] = useState<Trade[]>([]);
  const [total, setTotal] = useState(0);
  const [next, setNext] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const query = {...(side === "Long" || side === "Short" ? {side} : {}), ...(exit !== "any" ? {exit} : {}), ...(side === "losing" ? {losing: true} : {}), ...(during ? {since: during.from, until: during.to} : from ? {since: from} : {})};
  const load = (offset: number) => api.trades(runId, {...query, offset, limit: 500}).then(page => {
    setError(null);
    setRows(current => offset ? [...current, ...page.rows] : page.rows); setTotal(page.total); setNext(page.nextOffset);
  }, reason => setError(reason instanceof Error ? reason.message : String(reason)));
  useEffect(() => {void load(0);}, [runId, side, exit, during?.from, during?.to, from]);
  const loading = useRef(false);
  const view = useVirtual(rows.length, TRADE_ROW);
  // The next page is read when the reader nears the end of what is loaded.
  useEffect(() => {
    if (next === null || loading.current || view.end < rows.length - 40) return;
    loading.current = true;
    void load(next).finally(() => {loading.current = false;});
  }, [view.end, next, rows.length]);
  // A trade picked elsewhere (the chart, a Results link) is scrolled into view.
  const at = picked === null ? -1 : rows.findIndex(row => row.id === picked);
  useEffect(() => {if (at >= 0) view.scrollTo(at);}, [picked, at >= 0]);
  return <div className="qx-trades qx-blk">
    <div className="qx-bh"><h3>Trades</h3><span className="qx-cnt">{total}</span>
      <TxSegmented label="Side" value={side} options={[{value: "all", label: "All"}, {value: "Long", label: "Long"}, {value: "Short", label: "Short"}, {value: "losing", label: "Losing"}]} onChange={value => setSide(value as typeof side)} />
      <select className="qx-chip" aria-label="Exit" value={exit} onChange={event => setExit(event.target.value)}>{EXITS.map(item => <option key={item} value={item}>{item === "any" ? "Any exit" : "Exit: " + exitLabel(item).toLowerCase()}</option>)}</select>
      {during && <button type="button" className="qx-chip on" onClick={onClearDuring} aria-label={`Clear ${during.label ?? `drawdown ${during.n}`}`}>{during.label ?? `Drawdown ${during.n}`} <TxIcon name="close" /></button>}
      <span className="tx-spacer" />{extra}</div>
    {error && <p className="fx-empty" role="alert">{error}</p>}
    <div className="fx-rtable qx-scroll" ref={view.ref}><table className="fx-g" aria-label="Trades">
      <thead><tr><th className="l">#</th><th className="l">Side</th><th className="l">Entry, UTC</th><th className="l">Exit, UTC</th><th className="l">Exit by</th><th>P&amp;L</th><th>Return</th><th>Bars</th><th>MAE</th><th>MFE</th><th>Fees</th><th>Funding</th></tr></thead>
      <tbody>{view.before > 0 && <tr aria-hidden style={{height: view.before}} />}{rows.slice(view.start, view.end).map(row => <tr key={row.id} style={{height: TRADE_ROW}} data-selected={picked === row.id ? "" : undefined} onClick={() => onPick(row)}>
        <td className="l fx-t2">{row.id}</td><td className="l fx-t2">{row.side}</td><td className="l" title={mdy(row.entry)}>{utcStamp(row.entry)}</td><td className="l">{row.exit === null || row.exitBy === "open at the end" ? "—" : utcStamp(row.exit)}</td>
        <td className="l fx-t2">{row.exitBy === "open at the end" ? "open" : row.exitBy}</td><td className={tone(row.pnl)}>{signed(row.pnl)}</td><td className={tone(row.ret)}>{row.ret === null ? "—" : pc(row.ret, 2)}</td><td>{row.bars ?? ""}</td>
        <td>{row.mae === null ? "—" : pc(row.mae, 2)}</td><td>{row.mfe === null ? "—" : pc(row.mfe, 2)}</td>
        <td>{row.fees ? "−" + nf(Math.abs(row.fees), 2) : nf(0, 2)}</td><td className={tone(row.funding === null ? null : -row.funding)}>{signed(row.funding === null ? null : -row.funding)}</td></tr>)}{view.after > 0 && <tr aria-hidden style={{height: view.after}} />}</tbody>
    </table></div>
  </div>;
}
