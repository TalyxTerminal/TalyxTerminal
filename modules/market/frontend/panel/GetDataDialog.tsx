import { t } from "../i18n";
import { useEffect, useState, type ReactNode } from "react";
import { TxButton, TxCheckbox, TxDialog, TxField, TxIconButton, TxSearch, TxSegmented } from "@talyx/sdk";
import type { Instrument } from "@talyx/vocab";
import { createClient } from "../../public/ts/types";
import { formatBytes, listOf, marketCall, panelCall, spans, type FetchInput, type FetchKind, type FetchTarget } from "./data";
import { usePanelContext } from "./MarketPanel";

type Span = typeof spans[number]["value"];
type Market = "us" | "cn" | "hk" | "crypto";
const marketName: Record<Market, string> = { us: "US", cn: t("A shares"), hk: "HK", crypto: t("Crypto") };

/** Something to fetch: an index's members over time, or one instrument. */
export interface Pickable { id: string; label: string; detail: string; count: number; market: Market; target: FetchTarget }

/** Indexes whose historical members Market holds (the members are fetched first, delisted ones included). */
export const starters: Pickable[] = [
  { id: "spx-members", label: t("S&P 500 members"), detail: t("Index members since 1996, delisted included"), count: 500, market: "us", target: { index: "^GSPC" } },
  { id: "csi300", label: t("CSI 300 members"), detail: "000300.SH", count: 300, market: "cn", target: { index: "CN:000300.SH" } },
  { id: "csi500", label: t("CSI 500 members"), detail: "000905.SH", count: 500, market: "cn", target: { index: "CN:000905.SH" } },
  { id: "sse50", label: t("SSE 50 members"), detail: "000016.SH", count: 50, market: "cn", target: { index: "CN:000016.SH" } },
];

type SearchMatches = { query: string; items: Pickable[] };

export function offersFor(query: string, found: SearchMatches | null, picked: Pickable[]) {
  const text = query.trim();
  const matches = !text ? starters : found?.query === text ? found.items : [];
  return matches.filter(item => !picked.some(other => other.id === item.id)).slice(0, 40);
}

function marketOf(instrument: Instrument): Market {
  if (instrument.identityVersion !== 2) return "crypto";
  return instrument.venueId === "CN" ? "cn" : instrument.venueId === "HK" ? "hk" : "us";
}

/** Per kind of data and market: what Market fetches it as. A kind no picked market has is not offered. */
const plan: Record<string, { label: string; kind: FetchKind; markets: Partial<Record<Market, string>> }> = {
  Daily: { label: t("Daily"), kind: { kind: "bars", timeframe: "1d" }, markets: { us: t("Yahoo, or a keyed source"), cn: t("Baostock, or Tushare with a token"), hk: "Yahoo", crypto: t("The exchange") } },
  "1 hour": { label: t("1 hour"), kind: { kind: "bars", timeframe: "1h" }, markets: { us: t("Yahoo (2 years), Alpaca (from 2016)"), crypto: t("The exchange") } },
  "1 minute": { label: t("1 minute"), kind: { kind: "bars", timeframe: "1m" }, markets: { us: t("Yahoo (30 days), Alpaca (from 2016)"), crypto: t("The exchange's archives") } },
  "Corporate actions": { label: t("Corporate actions"), kind: { kind: "actions" }, markets: { us: "Yahoo", cn: "Baostock", hk: "Yahoo" } },
  Funding: { label: t("Funding"), kind: { kind: "funding_rate" }, markets: { crypto: t("The exchange") } },
  "Open interest": { label: t("Open interest"), kind: { kind: "open_interest" }, markets: { crypto: t("The exchange") } },
};

/** Rows a year per instrument, and how many years back the source goes at most; a local estimate, not a source query.
 *  US intraday reaches back to 2016 with an Alpaca key; without one Yahoo gives two years of hours and 30 days of minutes. */
const perYear = (alpaca: boolean): Record<string, Partial<Record<Market, [number, number]>>> => ({
  Daily: { us: [252, 64], cn: [242, 36], hk: [247, 26], crypto: [365, 7] },
  "1 hour": { us: [1_638, alpaca ? 10.8 : 2], crypto: [8_760, 7] },
  "1 minute": { us: [98_280, alpaca ? 10.8 : 30 / 365], crypto: [525_600, 7] },
  "Corporate actions": { us: [5, 64], cn: [2, 36], hk: [2, 26] },
  Funding: { crypto: [1_095, 7] },
  "Open interest": { crypto: [105_120, 7] },
});
const ROW_BYTES = 38;
const spanYears = { "1y": 1, "5y": 5, "10y": 10, max: 99 } as const;

function PickRow({ item, children }: { item: Pickable; children?: ReactNode }) {
  return <span className="market-pick">
    <span className="tx-cell-name">{item.label}</span><span className="tx-muted market-clip">{item.detail}</span>
    <span className="tx-num tx-secondary">{item.count > 1 ? item.count.toLocaleString("en-US") : ""}</span>{children}
  </span>;
}

export function GetDataDialog({ initial, alpaca, onClose, onGet }: { initial?: Pickable; alpaca: boolean; onClose: () => void; onGet: (work: Promise<unknown>) => void }) {
  const ctx = usePanelContext();
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<SearchMatches | null>(null);
  const [picked, setPicked] = useState<Pickable[]>(initial ? [initial] : []);
  const [kinds, setKinds] = useState<string[]>(["Daily", "Corporate actions"]);
  const [span, setSpan] = useState<Span>("10y");
  const [disk, setDisk] = useState<{ drive: string; free: number } | null>(null);
  useEffect(() => { void panelCall<{ drive: string; free: number }>(ctx, "disk").then(setDisk).catch(() => setDisk(null)); }, [ctx]);
  useEffect(() => {
    const text = query.trim();
    if (!text) return;
    let live = true;
    const timer = window.setTimeout(() => {
      void createClient(ctx)["instruments.search"]({ query: text, limit: 30 }).then(page => {
        if (!live) return;
        const items = page.results.map(result => {
          const instrument = result.instrument as Instrument;
          return { id: instrument.instrumentId, label: instrument.identityVersion === 2 ? instrument.symbol : `${instrument.venueId}:${instrument.symbol}`,
            detail: instrument.name ?? instrument.displaySymbol ?? "", count: 1, market: marketOf(instrument), target: { symbol: instrument.instrumentId } };
        });
        setFound({ query: text, items });
      }).catch(() => { if (live) setFound(null); });
    }, 150);
    return () => { live = false; window.clearTimeout(timer); };
  }, [ctx, query]);
  const offered = offersFor(query, found, picked);
  const markets = [...new Set(picked.map(item => item.market))];
  const available = Object.keys(plan).filter(kind => !markets.length || markets.some(market => plan[kind]!.markets[market]));
  const chosen = kinds.filter(kind => available.includes(kind));
  const total = picked.reduce((sum, item) => sum + item.count, 0);
  const estimate = picked.reduce((sum, item) => sum + chosen.reduce((inner, kind) => {
    const rate = perYear(alpaca)[kind]![item.market];
    return rate ? inner + item.count * rate[0] * Math.min(spanYears[span], rate[1]) * ROW_BYTES : inner;
  }, 0), 0);
  const tooBig = disk !== null && estimate > disk.free;
  const get = () => {
    const years = spanYears[span];
    const start = span === "max" ? null : new Date(Date.UTC(new Date().getUTCFullYear() - years, new Date().getUTCMonth(), new Date().getUTCDate())).toISOString().slice(0, 10);
    const data = listOf<NonNullable<FetchInput["data"]>>(chosen.map(kind => plan[kind]!.kind), 1, 20);
    const symbols = picked.flatMap(({ target }) => "symbol" in target ? [target.symbol] : []);
    const requests = picked.flatMap(({ target }) => "index" in target
      ? [marketCall(ctx, "fetch", { index: { symbol: target.index }, data, start })] : []);
    if (symbols.length) requests.push(marketCall(ctx, "fetch", {
      symbols: listOf<NonNullable<FetchInput["symbols"]>>(symbols, 1, 10000), data, start,
    }));
    onGet(Promise.all(requests));
    onClose();
  };
  return <TxDialog open onOpenChange={value => { if (!value) onClose(); }} title={t("Get data")} size="wide" footer={<>
    {tooBig ? <span className="market-disk tx-num">{t("Needs ≈ {estimate}; {drive} has {free} free", { estimate: formatBytes(estimate), drive: disk!.drive, free: formatBytes(disk!.free) })}</span>
      : <span className="tx-secondary tx-num">{total ? `${total === 1 ? t("1 instrument") : t("{count} instruments", { count: total.toLocaleString("en-US") })} × ${chosen.length === 1 ? t("1 kind") : t("{count} kinds", { count: chosen.length })}` : ""}</span>}
    <span className="tx-spacer" />
    <TxButton size="sm" variant="quiet" onClick={onClose}>{t("Cancel")}</TxButton>
    {estimate > 0 && !tooBig && <span className="tx-num tx-secondary">≈ {formatBytes(estimate)}</span>}
    <TxButton size="sm" variant="primary" disabled={!picked.length || !chosen.length || tooBig} onClick={get}>{total ? (total === 1 ? t("Get {total} instrument", { total: total.toLocaleString("en-US") }) : t("Get {total} instruments", { total: total.toLocaleString("en-US") })) : t("Get")}</TxButton>
  </>}>
    <div className="market-get">
      <div className="market-get-col">
        <TxField label={t("Instruments")}><TxSearch value={query} onValueChange={setQuery} placeholder={t("Index, exchange, code or name")} autoFocus /></TxField>
        {picked.length > 0 && <div className="market-get-list market-get-picked">{picked.map(item => <PickRow key={item.id} item={item}>
          <TxIconButton size="sm" icon="close" label={t("Remove {label}", { label: item.label })} onClick={() => setPicked(picked.filter(other => other !== item))} />
        </PickRow>)}</div>}
        <div className="market-get-list market-get-offered">{offered.map(item => <TxButton key={item.id} variant="row" onClick={() => { setPicked([...picked, item]); setQuery(""); }}>
          <PickRow item={item} />
        </TxButton>)}</div>
      </div>
      <div className="market-get-col">
        <TxField label={t("Data")}>
          <div className="market-kinds">{available.map(kind => {
            const from = markets.flatMap(market => plan[kind]!.markets[market] ? [`${markets.length > 1 ? `${marketName[market]} · ` : ""}${plan[kind]!.markets[market]}`] : []);
            return <div key={kind} className="market-kind">
              <TxCheckbox label={plan[kind]!.label} checked={kinds.includes(kind)} onCheckedChange={on => setKinds(on ? [...kinds, kind] : kinds.filter(item => item !== kind))} />
              <span className="market-kind-from">{from.map(line => <span key={line} className="tx-secondary market-clip" title={line}>{line}</span>)}</span>
            </div>;
          })}</div>
        </TxField>
        <TxField label={t("Period")}><TxSegmented value={span} onChange={setSpan} label={t("Period")} options={spans.map(item => ({ value: item.value, label: item.label }))} /></TxField>
      </div>
    </div>
  </TxDialog>;
}
