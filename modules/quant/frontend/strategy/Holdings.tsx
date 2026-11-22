/* A portfolio's tables: the rebalances (what it aimed for, what it held, what did not fill and why), what it holds now, and what each name contributed. */
import { t } from "../i18n";
import { useEffect, useState } from "react";
import { TxIcon, TxIconButton } from "@talyx/sdk";
import { mdy, nf, pct, tone } from "../factor/format";
import type { ContributionRow, Rebalance, StrategyApi, StrategyWork } from "./api";

export function RebalanceTable({api, runId, during, onClearDuring}: {api: StrategyApi; runId: string; during?: {n?: number; label?: string; from: number; to: number} | null; onClearDuring?(): void}) {
  const [rows, setRows] = useState<Rebalance[]>([]);
  const [total, setTotal] = useState(0);
  const [next, setNext] = useState<number | null>(null);
  const [at, setAt] = useState(0);
  const load = (offset: number) => api.rebalances(runId, {filter: "changed", offset, limit: 20, ...(during ? {since: during.from, until: during.to} : {})}).then(page => {
    setRows(current => offset ? [...current, ...page.rows] : page.rows); setTotal(page.total); setNext(page.nextOffset);
  }, () => undefined);
  useEffect(() => {setAt(0); void load(0);}, [runId, during?.from, during?.to]);
  useEffect(() => {if (next !== null && at >= rows.length - 1) void load(next);}, [at, next, rows.length]);
  const row = rows[Math.min(at, rows.length - 1)];
  // A ranked label's signals sit close together (0.4812, 0.4807): the decimals that tell them apart, up to four.
  const spread = row?.values ? Math.max(0, ...Object.values(row.values).filter((v): v is number => v != null)) - Math.min(0, ...Object.values(row.values).filter((v): v is number => v != null)) : 1;
  const digits = spread >= 1 ? 2 : spread >= 0.1 ? 3 : 4;
  const signed = (value: number) => (value > 0 ? "+" : value < 0 ? "−" : "") + nf(Math.abs(value), digits);
  const names = row ? [...new Set([...Object.keys(row.targets), ...Object.keys(row.actual).filter(name => row.actual[name])])] : [];
  // What is held comes first, the largest weight on top, then the rest by rank (1 is the top of the ranking); a name not held says so.
  const weight = (name: string) => Math.abs(row?.targets[name] || row?.actual[name] || 0);
  names.sort((a, b) => weight(b) - weight(a) || (row?.ranks?.[a] ?? Infinity) - (row?.ranks?.[b] ?? Infinity));
  const hasRank = Boolean(row && Object.keys(row.ranks ?? {}).length);
  return <div className="qx-rebalances">
    <div className="qx-tfilters"><TxIconButton label={t("Earlier rebalance")} icon="chevron" style={{transform: "rotate(180deg)"}} disabled={at <= 0} onClick={() => setAt(at - 1)} />
      <span className="qx-stepday">{row ? mdy(row.at) : "—"}</span><TxIconButton label={t("Later rebalance")} icon="chevron" disabled={at >= rows.length - 1} onClick={() => setAt(at + 1)} />
      {during && <button type="button" className="qx-chip on" onClick={onClearDuring} aria-label={t("Clear {label}", { label: during.label ?? t("drawdown {n}", { n: String(during.n) }) })}>{during.label ?? t("Drawdown {n}", { n: during.n ?? "" })} <TxIcon name="close" /></button>}
      <span className="tx-spacer" /><span className="fx-t3">{t("{total} rebalances", { total })}</span></div>
    {row && <div className="fx-rtable"><table className="fx-g" aria-label={t("Rebalance")}><thead><tr><th className="l">{t("Name")}</th><th className="l">{t("Side")}</th>{hasRank && <><th>{t("Rank")}</th><th>{t.c("portfolio", "Signal")}</th></>}<th>{t("After")}</th><th className="l">{t.c("execution", "Fill")}</th></tr></thead>
      <tbody>{names.map(name => {
        const target = row.targets[name] ?? 0, held = row.actual[name] ?? 0, miss = row.unfilled.find(item => item.name === name);
        return <tr key={name}><td className="l">{name}</td><td className="l fx-t2">{(target || held) < 0 ? t.c("position side", "Short") : (target || held) > 0 ? t.c("position side", "Long") : t("Not held")}</td>{hasRank && <><td>{row.ranks?.[name] ?? ""}</td><td className={tone(row.values?.[name] ?? null)}>{row.values?.[name] == null ? "" : signed(row.values[name])}</td></>}
          <td className={tone(held)}>{(held < 0 ? "−" : "") + pct(Math.abs(held), 2)}</td><td className="l fx-t2">{miss ? t("Not filled: {reason}", { reason: miss.reason }) : row.skipped ?? (target || held ? t("Filled") : "")}</td></tr>;})}</tbody></table></div>}
  </div>;
}

export function HoldingsTable({work}: {work: StrategyWork}) {
  const rows = work.multi?.holdings ?? [];
  return <div className="fx-rtable"><table className="fx-g" aria-label={t("Holdings")}><thead><tr><th className="l">{t("Name")}</th><th>{t("Weight")}</th><th>{t("P&L {currency}", { currency: work.currency })}</th><th>{t("Days")}</th></tr></thead>
    <tbody>{rows.map(row => <tr key={row.name}><td className="l">{row.name}</td><td>{pct(row.weight, 1)}</td><td className={tone(row.pnl)}>{row.pnl === null ? "" : nf(row.pnl, 2)}</td><td>{row.days === null ? "" : nf(row.days, 0)}</td></tr>)}</tbody></table></div>;
}

export function Contributions({rows, currency}: {rows: ContributionRow[]; currency: string}) {
  return <div className="fx-rtable"><table className="fx-g" aria-label={t("Contributions")}><thead><tr><th className="l">{t("Name")}</th><th>{t("Net {currency}", { currency })}</th><th>{t("Costs")}</th><th>{t("Trades")}</th></tr></thead>
    <tbody>{rows.map(row => <tr key={row.name}><td className="l">{row.name}</td><td className={tone(row.pnl)}>{row.pnl === null ? "" : nf(row.pnl, 0)}</td><td>{nf(row.fees ?? 0, 0)}</td><td>{row.trades}</td></tr>)}</tbody></table></div>;
}

export const Holdings = {Rebalances: RebalanceTable, Positions: HoldingsTable, Contributions};
