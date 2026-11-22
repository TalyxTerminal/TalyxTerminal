import { t } from "../i18n";
import { useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ModuleContextProvider, ModuleView, TxButton, TxFailure, TxIcon, TxPopover, TxSearch, TxSkeleton, TxTable, type Json } from "@talyx/sdk";
import { createClient, type LayerAttribution, type LayerContributor, type ReadRun, type RunChartLayer, type RunChartSection } from "../../public/ts/types";
import { mdy, nf, pc } from "../factor/format";
import type { Trade } from "../strategy/api";
import "./runPrice.css";

type LayerInstrument = NonNullable<RunChartLayer["instruments"]>[number];

function Contributors({items, selected, pick, attribution}: {attribution: LayerAttribution; items: LayerContributor[]; selected: string; pick(item: LayerInstrument): void}) {
  const sectors = new Map<string, LayerContributor[]>();
  for (const item of items) {
    const members = sectors.get(item.sector);
    if (members) members.push(item);
    else sectors.set(item.sector, [item]);
  }
  const total = attribution.net;
  return <TxTable className="qx-contribution-table" density="compact"><thead><tr><th>{t("Name")}</th><th>{t("P&L ({currency})", { currency: attribution.currency })}</th><th>{t("Share")}</th><th>{t("Trades")}</th><th>{t("Weight")}</th></tr></thead>
    {Array.from(sectors, ([sector, members]) => <tbody key={sector}>
      <tr><th colSpan={5} scope="rowgroup"><span className="qx-sector-total"><span>{sector}</span><span>{nf(members.reduce((sum, item) => sum + item.pnl, 0), 2)}</span></span></th></tr>
      {[...members].sort((a, b) => b.pnl - a.pnl).map(item => <tr key={item.instrumentId} aria-selected={item.instrumentId === selected} onClick={() => pick(item)}>
        <td><TxButton size="sm" variant="row" onClick={event => {event.stopPropagation(); pick(item);}}>{item.symbol}</TxButton></td>
        <td>{nf(item.pnl, 2)}</td><td>{total ? pc(item.pnl / total, 1) : "—"}</td><td>{item.trades}</td><td>{item.weight == null ? "—" : pc(item.weight, 1)}</td>
      </tr>)}</tbody>)}
    {attribution.remainder !== 0 && <tfoot><tr><th>{t("Unattributed")}</th><td>{nf(attribution.remainder, 2)}</td><td colSpan={3} /></tr></tfoot>}</TxTable>;
}

/** `head`: the page's own title bar for the chart; the instrument picker and the shown period join it. */
export function RunPrice({runId, compareTo, trade, initialSymbol, portfolio = false, head}: {
  runId: string; compareTo?: string; trade?: Trade | null; initialSymbol?: string; portfolio?: boolean;
  head?: {className: string; title: ReactNode; tools: ReactNode};
}) {
  const ctx = useContext(ModuleContextProvider)!;
  const [request, setRequest] = useState<{instrumentId?: string; around?: string}>({instrumentId: initialSymbol});
  const [snapshot, setSnapshot] = useState<RunChartLayer | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false), [query, setQuery] = useState("");
  const [focus, setFocus] = useState<string>();
  useEffect(() => {
    if (!trade) return;
    const time = new Date(trade.entry).toISOString();
    setFocus(time);
    const inside = snapshot && trade.entry >= Date.parse(snapshot.window.from) && trade.entry <= Date.parse(snapshot.window.to);
    const instrumentId = snapshot?.instruments?.find(item => item.key === trade.nativeInstrument)?.instrumentId ?? trade.nativeInstrument;
    if (!inside || instrumentId !== snapshot?.instrumentId) setRequest({instrumentId, around: time});
  }, [trade]);
  useEffect(() => {
    let active = true;
    setError("");
    setSnapshot(null);
    createClient(ctx).read_run({runId, part: "chartLayer", includeInstruments: true, ...request, compareTo: compareTo?.split(",") as ReadRun["compareTo"]}).then(found => {
      if (active) setSnapshot((found as RunChartSection).data);
    }, reason => {if (active) setError(reason.message);});
    return () => {active = false;};
  }, [ctx, runId, compareTo, request]);
  const props = useMemo(() => snapshot ? {layer: snapshot.layer as Json, ...(focus ? {focusTime: focus} : {})} : null, [snapshot, focus]);
  if (error) return <TxFailure title={t("Could not open the run on price")} message={error} />;
  if (!snapshot || !props) return <TxSkeleton width="45%" />;
  const items = snapshot.instruments!, selected = items.find(item => item.instrumentId === snapshot.instrumentId)!;
  const grouped = portfolio && items.length > 1 && snapshot.attribution !== undefined;
  const pick = (item: LayerInstrument) => {setOpen(false); setQuery(""); setFocus(undefined); setRequest({instrumentId: item.instrumentId});};
  const list = grouped && <Contributors attribution={snapshot.attribution!} items={items as LayerContributor[]} selected={selected.instrumentId} pick={pick} />;
  const picker = items.length > 1 && <div className="qx-price-pick"><TxPopover label={t("Choose instrument")} open={open} onOpenChange={setOpen}
    trigger={<TxButton size="sm" variant="quiet" aria-label={t("Choose instrument, {symbol}", { symbol: selected.symbol })}>{selected.symbol}<TxIcon name="chevron" /></TxButton>}>
    {list || <><TxSearch value={query} onValueChange={setQuery} placeholder={t("Find instrument")} />
      <div className="qx-price-options">{items.filter(i => i.symbol.toLowerCase().includes(query.toLowerCase())).map(item =>
        <TxButton size="sm" variant="row" key={item.instrumentId} aria-current={item.instrumentId === selected.instrumentId || undefined} onClick={() => pick(item)}>{item.symbol}</TxButton>)}</div></>}
  </TxPopover></div>;
  const note = snapshot.truncated && <span className="fx-t3 qx-price-note">{t("Showing {start} – {end}", { start: mdy(Date.parse(snapshot.window.from)), end: mdy(Date.parse(snapshot.window.to)) })}</span>;
  return <div className="qx-runprice" data-portfolio={grouped || undefined}>
    {head ? <div className={head.className}>{head.title}{picker}{note}{head.tools}</div>
      : (picker || note) && <div className="qx-price-head">{picker}{note}</div>}
    <div className="qx-price-body"><ModuleView id="chart.layer_chart" props={props} />
      {grouped && <aside aria-label={t("Contributions by sector")}>{list}</aside>}
    </div></div>;
}

