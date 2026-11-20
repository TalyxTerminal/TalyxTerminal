/* The log of a run in simulated time, a page at a time from the backend and filtered there by kind; a picked trade scrolls to and marks its own lines. */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { TxIcon, TxMenu } from "@talyx/sdk";
import { utcStamp } from "./TradesTable";
import type { LogLine, StrategyApi } from "./api";
import { useVirtual } from "./useVirtual";

export const LOG_ROW = 22;

export const KINDS = ["Signal", "Order", "Stop", "Funding", "Trade", "Skip", "Error", "Info", "Partial", "Unfilled", "Not filled", "Liquidation stop"];

/** An instrument as the log names it: its base ("BTC" for BTC/USDT:USDT). */
const short = (instrument: string | null) => instrument ? instrument.split(/[/:.-]/)[0] : "";

export function LogList({api, runId, trade, kinds, onKinds, onTrade, extra, from}: {api: StrategyApi; runId: string; trade: number | null; kinds: string[]; onKinds(kinds: string[]): void; onTrade(id: number): void; extra?: ReactNode; from?: number | null}) {
  const [rows, setRows] = useState<LogLine[]>([]);
  const [, setTotal] = useState(0);
  const [next, setNext] = useState<number | null>(null);
  const loading = useRef(false);
  // One kind at a time is filtered in the backend; several are read as the union of their pages.
  const load = async (offset: number) => {
    const since = from ? {since: from} : {};
    const pages = kinds.length === 1 ? [await api.log(runId, {kind: kinds[0], offset, limit: 1000, ...since})] : [await api.log(runId, {offset, limit: 1000, ...since})];
    const page = pages[0];
    const rowsIn = kinds.length > 1 ? page.rows.filter(row => kinds.includes(row.kind)) : page.rows;
    setRows(current => offset ? [...current, ...rowsIn] : rowsIn); setTotal(page.total); setNext(page.nextOffset);
  };
  useEffect(() => {void load(0).catch(() => undefined);}, [runId, kinds.join("|"), from]);
  const view = useVirtual(rows.length, LOG_ROW, 400);
  const first = trade === null ? -1 : rows.findIndex(row => row.trade === trade);
  useEffect(() => {if (first >= 0) view.scrollTo(first);}, [trade, first >= 0]);
  // The next page is read when the reader nears the end of what is loaded.
  useEffect(() => {
    if (next === null || loading.current || view.end < rows.length - 200) return;
    loading.current = true;
    void load(next).catch(() => undefined).finally(() => {loading.current = false;});
  }, [view.end, next, rows.length]);
  return <div className="qx-log">
    <div className="qx-bh" role="group" aria-label="Kinds"><h3>Log, UTC</h3><TxMenu label="Kinds" items={KINDS.map(kind => ({id: kind, label: kind, checked: kinds.includes(kind), onSelect: () => onKinds(kinds.includes(kind) ? kinds.filter(item => item !== kind) : [...kinds, kind])}))}
      trigger={<button type="button" className="tx-btn" data-variant="quiet" data-size="sm">{kinds.length === 0 ? "All kinds" : kinds.length === 1 ? kinds[0] : `${kinds.length} kinds`}<TxIcon name="chevron" /></button>} /><span className="tx-spacer" />{extra}</div>
    <div className="qx-loglines qx-scroll" role="log" ref={view.ref}>{view.before > 0 && <div aria-hidden style={{height: view.before}} />}{rows.slice(view.start, view.end).map((row, at) => { const index = view.start + at; return <div key={index} className="qx-logline" style={{height: LOG_ROW}} data-kind={row.kind} data-hit={trade !== null && row.trade === trade ? "" : undefined}
      onClick={() => row.trade !== undefined && onTrade(row.trade)}><span className="fx-t3">{utcStamp(row.t)}</span><span className="fx-t3">{short(row.instrument)}</span><span className="qx-logkind">{row.kind}</span><span title={row.text}>{row.text}</span></div>; })}{view.after > 0 && <div aria-hidden style={{height: view.after}} />}</div>
  </div>;
}
