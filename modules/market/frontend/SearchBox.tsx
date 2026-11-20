import { useEffect, useId, useRef, useState } from "react";
import { TxButton, TxEmpty, TxIconButton, TxSearch, TxSelect, TxSkeleton, TxTabs, TxTag, type ModuleContext } from "@talyx/sdk";
import type { Instrument, SeriesId } from "@talyx/vocab";
import { createClient, type ContractTypes } from "../public/ts/types";

export type SearchViewProps = ContractTypes["view:search_box"];
type SearchOutput = ContractTypes["instruments.search"]["output"];
type Result = SearchOutput["results"][number];
type CatalogState = SearchOutput["venues"][number];
type CallerAction = NonNullable<SearchViewProps["callerActions"]>[number];

const assets = [
  { value: "", label: "All" }, { value: "crypto", label: "Crypto" }, { value: "equity", label: "Stocks" },
  { value: "fund", label: "Funds" }, { value: "index", label: "Indices" }, { value: "fx", label: "Forex" },
];
const products: Record<string, string> = { spot: "Spot", swap: "Perpetual", future: "Futures", option: "Options" };
const kinds: Record<string, string> = { equity: "Stock", fund: "Fund", index: "Index", fx: "Forex", commodity: "Commodity" };
const families: Record<string, string> = { listings: "listings", ...Object.fromEntries(Object.entries(products).map(([key, label]) => [key, label.toLowerCase()])) };

const dateOf = (ns: string | null) => ns === null ? "" : new Date(Number(BigInt(ns) / 1_000_000n)).toISOString().slice(0, 10);

/** The code with the typed part marked, as TradingView's search does. */
function Matched({ text, query }: { text: string; query: string }) {
  const typed = query.trim().toUpperCase().replace(/[\s/:-]/g, "");
  const at = typed ? text.toUpperCase().indexOf(typed) : -1;
  if (at < 0) return <>{text}</>;
  return <>{text.slice(0, at)}<mark className="market-search-match">{text.slice(at, at + typed.length)}</mark>{text.slice(at + typed.length)}</>;
}
const kindLabel = (instrument: Instrument) => instrument.identityVersion === 2 ? kinds[instrument.assetClass] ?? instrument.assetClass : products[instrument.marketType] ?? instrument.marketType;

/** Catalog loading or failing, in the user's words; nothing when every catalog answered. */
function CatalogFeedback({ states, onRetry }: { states: CatalogState[]; onRetry: () => void }) {
  const failed = states.filter(item => item.state === "failed"), loading = states.filter(item => item.state === "loading");
  const name = (item: CatalogState) => `${item.venueName} ${families[item.family] ?? item.family}`;
  // A region block is answered the same way every time: say so in a few words, not the source's whole reply.
  const why = (item: CatalogState) => item.problem?.code === "forbidden" && item.problem.status
    ? `blocked from your region, HTTP ${item.problem.status}` : item.problem?.message ?? "failed";
  return <>
    {!!loading.length && <p role="status" className="tx-hint">Still loading: {loading.map(name).join(", ")}</p>}
    {!!failed.length && <div role="alert" className="market-search-source-error tx-hint">
      <span>Could not load {failed.slice(0, 3).map(item => `${name(item)} (${why(item)})`).join("; ")}{failed.length > 3 ? ` and ${failed.length - 3} more` : ""}</span>
      <TxButton size="sm" variant="quiet" onClick={onRetry}>Retry</TxButton>
    </div>}
  </>;
}

export function SearchBox({ ctx, props, onSelected, onCallerAction, onClosed, autoFocus = true }: {
  ctx: ModuleContext; props: SearchViewProps; autoFocus?: boolean;
  onSelected: (instrument: Instrument, series: SeriesId | null) => void;
  onCallerAction?: (action: CallerAction, instrument: Instrument) => void;
  onClosed?: () => void;
}) {
  const [query, setQuery] = useState(props.query ?? "");
  const [asset, setAsset] = useState(""), [product, setProduct] = useState("");
  const [results, setResults] = useState<Result[]>([]), [states, setStates] = useState<CatalogState[]>([]);
  const [cursor, setCursor] = useState<string | null>(null), [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null), [retry, setRetry] = useState(0);
  const [active, setActive] = useState<string | null>(null);
  const latest = useRef(0), listId = useId(), list = useRef<HTMLDivElement>(null);
  const search = useRef(createClient(ctx)["instruments.search"]);
  useEffect(() => setQuery(props.query ?? ""), [props.query]);
  const request = (after: string | null): ContractTypes["instruments.search"]["input"] => ({
    query: query.trim(), limit: 50, cursor: after,
    ...(props.markets?.length ? { market: props.markets } : {}),
    ...(props.venues?.length ? { venue: props.venues } : {}),
    ...(props.environment ? { environment: props.environment } : {}),
    ...(props.constraint ? { constraint: props.constraint } : {}),
    // The Crypto tab includes the crypto venues' stock tokens and contracts.
    ...(asset ? { assetClass: asset === "crypto" ? ["crypto", "tokenized_equity"] : [asset] } : {}),
    ...(product ? { marketType: product } : {}),
  } as ContractTypes["instruments.search"]["input"]);
  useEffect(() => {
    const id = ++latest.current;
    setActive(null); setError(null);
    if (!query.trim()) { setResults([]); setStates([]); setCursor(null); setLoading(false); return; }
    setLoading(true);
    const timer = setTimeout(async () => {
      try {
        const page = await search.current(request(null));
        if (latest.current !== id) return;
        setResults(page.results); setStates(page.venues); setCursor(page.nextCursor);
      } catch (failure) {
        if (latest.current === id) setError(failure instanceof Error ? failure.message : String(failure));
      } finally { if (latest.current === id) setLoading(false); }
    }, 120);
    return () => clearTimeout(timer);
  }, [query, asset, product, retry, props.markets, props.venues, props.environment, props.constraint]);
  const more = async () => {
    const id = latest.current;
    try {
      const page = await search.current(request(cursor));
      if (latest.current !== id) return;
      setResults(value => [...value, ...page.results]); setCursor(page.nextCursor);
    } catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)); }
  };
  useEffect(() => { if (active) list.current?.querySelector(`[id="${listId}-${active}"]`)?.scrollIntoView({ block: "nearest" }); }, [active, listId]);
  const allowed = props.constraint ? new Set(props.constraint.products) : null;
  return <div data-market-search="" className="market-search">
    <TxSearch value={query} onValueChange={setQuery} autoFocus={autoFocus} placeholder={props.placeholder ?? "Search instruments"} aria-label="Search instruments"
      role="combobox" aria-autocomplete="list" aria-expanded={results.length > 0} aria-controls={listId}
      aria-activedescendant={active ? `${listId}-${active}` : undefined}
      onKeyDown={event => {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          if (!results.length) return;
          const index = results.findIndex(item => item.instrument.instrumentId === active);
          const step = event.key === "ArrowDown" ? 1 : -1;
          setActive(results[index < 0 ? (step > 0 ? 0 : results.length - 1) : (index + step + results.length) % results.length].instrument.instrumentId);
        } else if (event.key === "Enter") {
          const chosen = results.find(item => item.instrument.instrumentId === active) ?? results[0];
          if (chosen) { event.preventDefault(); onSelected(chosen.instrument, null); }
        } else if (event.key === "Escape") onClosed?.();
      }} />
    {!props.constraint && <TxTabs value={asset} options={assets} onChange={setAsset} />}
    {(asset === "" || asset === "crypto") && <div className="market-search-filters">
      <TxSelect value={product} aria-label="Product type" onChange={event => setProduct(event.target.value)}>
        <option value="">All products</option>
        {Object.entries(products).filter(([id]) => !allowed || allowed.has(id as "spot")).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
      </TxSelect>
    </div>}
    <CatalogFeedback states={states} onRetry={() => setRetry(value => value + 1)} />
    {loading && !results.length && <div className="tx-search-loading"><TxSkeleton width="70%" /></div>}
    {error && <div role="alert" className="tx-error">{error} <TxButton size="sm" variant="quiet" onClick={() => setRetry(value => value + 1)}>Retry</TxButton></div>}
    {!query.trim() && <TxEmpty title="Search for a symbol" text="Type a code or a name, such as BTCUSDT, AAPL or 00700." />}
    {!loading && !error && query.trim() && !results.length && !states.some(item => item.state !== "ready") && <TxEmpty title="No matches" text={`Nothing matches "${query.trim()}".`} />}
    {!!results.length && <div ref={list} id={listId} role="listbox" aria-label="Instruments" className="market-search-results">
      {results.map(result => {
        const instrument = result.instrument, id = instrument.instrumentId;
        return <div key={id} className="market-search-item">
          <div className="market-search-row" data-active={id === active || undefined} data-current={id === props.currentInstrumentId || undefined}>
            <TxButton id={`${listId}-${id}`} role="option" aria-selected={id === active} aria-current={id === props.currentInstrumentId || undefined} className="market-search-select" variant="quiet"
              onClick={() => onSelected(instrument, null)} onFocus={() => setActive(id)}>
              <span className="market-search-symbol" title={instrument.displaySymbol}><Matched text={instrument.displaySymbol} query={query} /></span>
              <span className="market-search-name" title={instrument.name ?? instrument.base ?? instrument.symbol}>
                {instrument.name ?? instrument.base ?? instrument.symbol}
                {result.formerCode && <small> · former code {result.formerCode.code}{result.formerCode.until ? `, until ${dateOf(result.formerCode.until)}` : ""}</small>}
              </span>
              <span className="market-search-kind"><TxTag tone="muted">{kindLabel(instrument)}</TxTag>{instrument.retiredAt || instrument.delistedAt ? <TxTag tone="muted">{instrument.identityVersion === 2 ? "Delisted" : "Retired"}</TxTag> : null}</span>
              <span className="market-search-venue">{result.local && <><small>Local data</small>{" "}</>}{instrument.venueName}</span>
            </TxButton>
            {props.callerActions?.map(action => {
              const on = action.activeInstrumentIds?.includes(id);
              return <TxIconButton key={action.id} size="sm" label={`${action.label} ${instrument.displaySymbol}`} icon="plus"
                aria-pressed={on} tone={on ? "active" : "default"} onClick={() => onCallerAction?.(action, instrument)} />;
            })}
          </div>
          {result.series.map(item => <div key={item.series.seriesId} className="market-search-row market-search-series">
            <TxButton className="market-search-select" variant="quiet" onClick={() => onSelected(instrument, item.series)}>
              <span className="tx-code">{instrument.displaySymbol}</span><span>{item.label}</span><span /><span />
            </TxButton>
          </div>)}
        </div>;
      })}
      {cursor !== null && <TxButton size="sm" variant="quiet" onClick={() => void more()}>More matches</TxButton>}
    </div>}
  </div>;
}
