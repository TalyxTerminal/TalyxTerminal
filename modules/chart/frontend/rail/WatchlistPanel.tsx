/* The watchlist panel (TradingView): the list picker and add symbol on top, sections of rows with Last, Chg and Chg%
   (columns can be turned off), a details card for the row picked below. A click on a row shows it on the chart. */
import { useState } from "react";
import { TxButton, TxEmpty, TxIcon, TxIconButton, TxInput, TxMenu, TxTag, type ModuleContext, type TxMenuItem } from "@talyx/sdk";
import type { Instrument } from "@talyx/vocab";
import { post } from "../api";
import { compactZeros } from "../engine/precision";
import { message, openOverlay, switchSymbol, useStore } from "../store";
import type { Watchlist } from "../wire";
import { editWatchlist, removeFromWatchlist, useQuotes, type QuoteRow } from "./watchlists";

type Column = "last" | "change" | "changePercent";
const COLUMNS: { id: Column; label: string }[] = [{ id: "last", label: "Last" }, { id: "change", label: "Chg" }, { id: "changePercent", label: "Chg%" }];

const PRODUCTS: Record<string, string> = { spot: "Spot", swap: "Perpetual", future: "Futures", option: "Option", margin: "Margin" };
/** The kind of instrument as a word: crypto products by their market type, listings by their asset class. */
const kindOf = (instrument: Instrument) => {
  const kind = instrument.identityVersion === 2 ? instrument.assetClass : PRODUCTS[instrument.marketType] ?? instrument.marketType;
  return kind ? kind[0].toUpperCase() + kind.slice(1) : "";
};
const direction = (row: QuoteRow | undefined) => { const change = Number(row?.quote?.change ?? 0); return change > 0 ? "up" : change < 0 ? "down" : undefined; };
/** Market's decimal strings without trailing zeros (0.06129000 → 0.06129). */
const trim = (value: string | null | undefined) => value === null || value === undefined ? null : value.includes(".") ? value.replace(/\.?0+$/, "") : value;
/** Market's percent as TradingView shows it: two decimals. */
const percent = (value: string | null | undefined) => value === null || value === undefined ? "—" : `${Number(value) > 0 ? "+" : ""}${Number(value).toFixed(2)}%`;
/** A change as Market gives it, signed (Market forms it from the prices' decimals, so it carries the price's precision
    however small: BABYDOGE's -0.0000000000568 is not rounded away). */
const signed = (value: string | null | undefined) => {
  const exact = trim(value);
  return exact === null ? "—" : Number(exact) === 0 ? "0" : `${Number(exact) > 0 ? "+" : ""}${exact}`;
};
const cellsOf = (row: QuoteRow | undefined): Record<Column, string> => {
  const quote = row?.quote;
  return { last: trim(quote?.last ?? quote?.price) ?? "—", change: signed(quote?.change), changePercent: percent(quote?.changePercent) };
};

export function WatchlistPanel({ ctx, panelId }: { ctx: ModuleContext; panelId: string }) {
  const watchlists = useStore(state => state.watchlists);
  const current = useStore(state => state.charts[state.layouts[panelId]?.active ?? ""]?.chart?.document.instrument);
  const target = ctx.initialState;
  const [chosen, setChosen] = useState<string | null>(() =>
    target && typeof target === "object" && !Array.isArray(target) && typeof target.objectId === "string" &&
    target.objectId.startsWith("watchlist:") ? target.objectId.slice("watchlist:".length) : null);
  const [picked, setPicked] = useState<string | null>(null);
  const [columns, setColumns] = useState<Column[]>(["last", "change", "changePercent"]);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [naming, setNaming] = useState<{ mode: "create" | "rename"; name: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const lists = Object.values(watchlists).sort((a, b) => a.name.localeCompare(b.name));
  const list = lists.find(item => item.id === chosen) ?? lists[0];
  const members = list ? list.sections.flatMap(section => section.members.map(member => member.instrument)) : [];
  const { quotes, error: quoteError } = useQuotes(ctx, members);
  const fail = (failed: unknown) => setError(message(failed));

  const saveName = () => {
    const name = naming!.name.trim();
    if (!name) return;
    const done = (value: Watchlist) => { setChosen(value.id); setNaming(null); };
    if (naming!.mode === "create") post(ctx, "/watchlists/create", { name }).then(done, fail);
    else editWatchlist(ctx, list!.id, () => ({ name })).then(() => setNaming(null), fail);
  };
  if (naming) return <div className="chart-rail-panel">
    <div className="chart-rail-head">
      <TxInput aria-label="List name" placeholder="List name" autoFocus value={naming.name} onChange={event => setNaming({ ...naming, name: event.target.value })}
        onKeyDown={event => { if (event.key === "Enter") saveName(); if (event.key === "Escape") setNaming(null); }} />
      <TxButton size="sm" variant="primary" disabled={!naming.name.trim()} onClick={saveName}>{naming.mode === "create" ? "Create" : "Rename"}</TxButton>
      <TxButton size="sm" onClick={() => setNaming(null)}>Cancel</TxButton>
    </div>
    {error && <p className="chart-alert-error" role="alert">{error}</p>}
  </div>;

  if (!list) return <div className="chart-rail-panel">
    <TxEmpty title="No watchlist" />
    <div className="chart-rail-center"><TxButton size="sm" variant="primary" onClick={() => setNaming({ mode: "create", name: "Watchlist" })}>Create watchlist</TxButton></div>
  </div>;

  const menu: TxMenuItem[] = [
    ...lists.map(item => ({ id: item.id, label: item.name, checked: item.id === list.id, onSelect: () => setChosen(item.id) })),
    { id: "create", label: "Create new list…", icon: "plus", separated: true, onSelect: () => setNaming({ mode: "create", name: "" }) },
    { id: "rename", label: "Rename…", icon: "edit", onSelect: () => setNaming({ mode: "rename", name: list.name }) },
    { id: "delete", label: "Delete list", icon: "trash", onSelect: () => post(ctx, "/watchlists/delete", { watchlistId: list.id }).then(() => setChosen(null), fail) },
  ];
  const columnMenu: TxMenuItem[] = COLUMNS.map(column => ({ id: column.id, label: column.label, checked: columns.includes(column.id),
    onSelect: () => setColumns(value => value.includes(column.id) ? value.filter(item => item !== column.id) : COLUMNS.map(item => item.id).filter(id => id === column.id || value.includes(id))) }));
  const shown = COLUMNS.filter(column => columns.includes(column.id));
  const pickedInstrument = members.find(item => item.instrumentId === picked) ?? members.find(item => item.instrumentId === current?.instrumentId);
  return <div className="chart-rail-panel">
    <div className="chart-rail-head">
      <TxMenu label="Watchlists" items={menu} trigger={<TxButton variant="quiet" size="sm" className="chart-rail-title">{list.name}<TxIcon name="chevron-down" /></TxButton>} />
      <span className="chart-tb-spacer" />
      <TxIconButton size="sm" icon="plus" label="Add symbol" onClick={() => openOverlay(panelId, { kind: "symbol", mode: "watchlist", query: "", watchlistId: list.id })} />
      <TxMenu label="Columns" align="end" items={columnMenu} trigger={<TxIconButton size="sm" icon="columns" label="Columns" />} />
    </div>
    {(error || quoteError) && <p className="chart-alert-error chart-rail-note" role="alert">{error ?? quoteError}</p>}
    {/* One grid for the header and every row (rows are subgrids). The symbol column is at least as wide as a common
        ticker (10ch: AVAXUSDT and the row's padding), so a rare long one (BABYDOGEUSDT) is cut until the rail is widened
        and the numbers get the width; Chg% (short) is never cut; Last and Chg are as wide as their longest value while
        there is room (tiny prices are compact, 0.0₅38766), and on a narrow rail only the longest are cut. */}
    <div className="chart-rail-list chart-watch-list" role="list" style={{ gridTemplateColumns: ["minmax(10ch, 1fr)", ...shown.map(column => column.id === "changePercent" ? "max-content" : "minmax(0, max-content)")].join(" ") }}>
      <div className="chart-watch-row chart-watch-columns" role="presentation">
        <span>Symbol</span>{shown.map(column => <span key={column.id}>{column.label}</span>)}
      </div>
      {members.length === 0 && <TxEmpty title="Empty list" />}
      {list.sections.map(section => <div key={section.id} role="group" aria-label={section.name || list.name}>
        {section.name && <TxButton variant="row" size="sm" className="chart-watch-section" aria-expanded={!collapsed[section.id]} onClick={() => setCollapsed(value => ({ ...value, [section.id]: !value[section.id] }))}>
          <TxIcon name={collapsed[section.id] ? "chevron" : "chevron-down"} />{section.name}
        </TxButton>}
        {!collapsed[section.id] && section.members.map(({ instrument }) => <Row key={instrument.instrumentId} instrument={instrument} row={quotes[instrument.instrumentId]} columns={columns}
          current={instrument.instrumentId === current?.instrumentId}
          onPick={() => { setPicked(instrument.instrumentId); switchSymbol(ctx, panelId, instrument.instrumentId); }}
          onRemove={() => void removeFromWatchlist(ctx, list.id, instrument.instrumentId).catch(fail)} />)}
      </div>)}
    </div>
    {pickedInstrument && <Details instrument={pickedInstrument} row={quotes[pickedInstrument.instrumentId]} />}
  </div>;
}

/** A row: tiny prices in compact notation (0.0₅38766) so they fit the rail; the details card shows them in full. */
function Row({ instrument, row, columns, current, onPick, onRemove }: { instrument: Instrument; row: QuoteRow | undefined; columns: Column[]; current: boolean; onPick(): void; onRemove(): void }) {
  const cells = cellsOf(row);
  return <div className="chart-watch-item" role="listitem" data-current={current || undefined}>
    <TxButton variant="row" size="sm" className="chart-watch-row" onClick={onPick} title={row?.problem?.message}>
      <span className="chart-watch-symbol" title={instrument.displaySymbol}>{instrument.displaySymbol}</span>
      {COLUMNS.filter(column => columns.includes(column.id)).map(column => <span key={column.id} className="chart-watch-value" data-direction={column.id === "last" ? undefined : direction(row)}>{compactZeros(cells[column.id])}</span>)}
    </TxButton>
    <TxIconButton size="sm" icon="close" label={`Remove ${instrument.displaySymbol}`} onClick={onRemove} />
  </div>;
}

/** TradingView's card under the list: the picked symbol's name, price and change, exchange and type. */
function Details({ instrument, row }: { instrument: Instrument; row: QuoteRow | undefined }) {
  const quote = row?.quote;
  return <section className="chart-watch-details" aria-label={`${instrument.displaySymbol} details`}>
    <div className="chart-watch-details-head"><strong>{instrument.displaySymbol}</strong><span className="chart-muted">{instrument.name ?? instrument.base ?? instrument.symbol}</span></div>
    <div className="chart-watch-details-price">
      <span className="chart-watch-big">{trim(quote?.last ?? quote?.price) ?? "—"}</span>
      <span className="chart-watch-value" data-direction={direction(row)}>{cellsOf(row).change} ({percent(quote?.changePercent)})</span>
    </div>
    <div className="chart-watch-details-meta">
      <span>{instrument.venueName ?? instrument.venueId}</span>
      <TxTag tone="muted">{kindOf(instrument)}</TxTag>
      {quote?.delayed && <TxTag tone="muted">Delayed</TxTag>}
      {row?.problem && <span className="chart-alert-error">{row.problem.message}</span>}
    </div>
  </section>;
}
