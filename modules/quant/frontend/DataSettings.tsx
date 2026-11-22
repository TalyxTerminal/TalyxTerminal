import { t } from "./i18n";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ContractError, ModuleView, TxButton, TxParamRow, TxDialog, TxIcon, TxIconButton, TxInput, TxMenu, TxPopover, TxSegmented, TxSelect, TxSkeleton, type Json, type ModuleContext } from "@talyx/sdk";
import { createClient as marketClient, type ContractTypes as MarketTypes } from "../../market/public/ts/types";
import { RESEARCH_KINDS, TIMEFRAMES, dataSummary, monthsBack, plain, rangeLabel, lastFullDay, untilDay, useEvent, type Api, type DataDefaults, type DataSettings, type IndexMaterial,
  type CoverageItem, type IndexSelection, type Instrument, type Kind, type ResearchList, type ResearchListSummary, type Timeframe } from "./objects";
import { DayInput, dayShown } from "./shared/DayInput";

type Universe = "symbols" | "list" | "index" | "code";
const RANGES = [["6M", 6], ["1Y", 12], ["2Y", 24], ["5Y", 60], ["10Y", 120]] as const;
/** Members shown before "Show all": a long list stays one short block in the popover. */
const SHOWN = 24;
const fee = (rate: number) => `${plain(rate * 100, 4)}%`;
const message = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);

/** Coverage is per instrument; Market aggregates the source's base periods. */
export type CapabilityRow = CoverageItem;
const duration = (period: string): number => {
  const parsed = /^(\d+)([mhd])$/.exec(period);
  return parsed === null ? 0 : Number(parsed[1]) * ({m: 60_000, h: 3_600_000, d: 86_400_000}[parsed[2] as "m" | "h" | "d"]);
};
export function verifiedTimeframes(rows: CapabilityRow[]): Timeframe[] {
  const bases = rows.filter(row => row.capability === "ohlcv").flatMap(row => row.available.periods);
  return TIMEFRAMES.filter(period => bases.some(base => base === period ||
    duration(base) > 0 && period.endsWith("d") === base.endsWith("d") && duration(period) % duration(base) === 0));
}
/** Finer bars the venue can match orders on: verified, and shorter than the signal bars (5 minutes and 1 minute). */
export function fillChoices(verified: Timeframe[], timeframe: Timeframe | undefined): ("5m" | "1m")[] {
  const rank = timeframe === undefined ? TIMEFRAMES.length : TIMEFRAMES.indexOf(timeframe);
  return (["5m", "1m"] as const).filter(item => verified.includes(item) && TIMEFRAMES.indexOf(item) < rank);
}
const SERIES_NAMES: Record<string, string> = {funding_rate: t("Funding rate"), open_interest: t("Open interest")};
/** The series beyond prices Market has for this instrument, in words. */
export function extraSeriesOf(rows: CapabilityRow[]): string[] {
  return [...new Set(rows.filter(row => row.capability !== "ohlcv" && SERIES_NAMES[row.capability] &&
    (row.source !== null || row.local !== null)).map(row => SERIES_NAMES[row.capability]))];
}
/** Coverage belongs to one instrument, including where its stored history came from. */
export function useCapabilities(ctx: ModuleContext, instrument: Instrument | undefined) {
  const market = useMemo(() => marketClient(ctx), [ctx]);
  const [value, setValue] = useState<{key: string; rows: CapabilityRow[]; error: string | null} | null>(null);
  const key = instrument?.instrumentId ?? "";
  useEffect(() => {
    let live = true;
    if (instrument === undefined) return;
    void market.read_coverage({symbols:[instrument.instrumentId],limit:500}).then(found => {
      const capability = found.columns.findIndex(c => c.name === "capability");
      const available = found.columns.findIndex(c => c.name === "available");
      const source = found.columns.findIndex(c => c.name === "source");
      const local = found.columns.findIndex(c => c.name === "local");
      const rows: CapabilityRow[] = found.rows.filter(row => row[available]).map(row => ({
        capability: String(row[capability]), available: row[available] as CapabilityRow["available"],
        source: row[source], local: row[local]
      }));
      if (live) setValue({key, rows, error:null});
    }, reason => {if (live) setValue({key, rows: [], error: message(reason)});});
    return () => {live = false;};
  }, [market, key]);
  return value !== null && value.key === key ? value : {rows: null, error: null};
}

/** Settings that make sense for a new symbol: hourly bars for crypto, daily otherwise, two years. */
export function withSymbol(data: DataSettings, instrument: Instrument): DataSettings {
  const symbols = [...(data.symbols ?? []).filter(item => item.instrument.instrumentId !== instrument.instrumentId), {instrument}];
  return {...data, symbols, timeframe: data.timeframe ?? (instrument.assetClass === "crypto" ? "1h" : "1d"),
    since: data.since ?? monthsBack(24), until: data.until ?? "latest"};
}
/** Where research on an index starts unless set: long enough for a model's default three years of training and the windows after it. */
const RESEARCH_SINCE = "2016-01-01";
/** What a new factor starts with, as the draft shows a new one: S&P 500 members, daily, from Jan 2016 to the latest day; null when Market has no such index (nothing is made up). */
export function defaultResearchData(items: IndexMaterial[]): DataSettings | null {
  const found = items.find(item => item.index.symbol === "^GSPC");
  return found === undefined ? null : {index: selectionOf(found), timeframe: "1d", since: RESEARCH_SINCE, until: "latest"};
}
/** The last day of the data: the latest close (resolved when a run starts, per each name's calendar) or a date of one's own. */
export function UntilField({until, since, onChange}: {until: string | undefined; since: string | undefined; onChange(until: string): void}) {
  const fixed = until !== undefined && until !== "latest";
  return <span className="qx-data-until">
    <TxSelect aria-label={t("To")} value={fixed ? "date" : "latest"} onChange={event => onChange(event.target.value === "latest" ? "latest" : fixed ? until! : lastFullDay())}>
      <option value="latest">{t("Latest close")}</option>
      <option value="date">{t("A date")}</option>
    </TxSelect>
    {fixed && <DayInput label={t("To")} value={until!} min={since} onChange={day => {if (day) onChange(day);}} />}
  </span>;
}
/** The same settings choosing the symbols another way; whatever chose them before is dropped. */
function choose(data: DataSettings, universe: Universe, list?: string, index?: IndexSelection): DataSettings {
  const rest = Object.fromEntries(Object.entries(data).filter(([key]) => !["symbols", "list", "index", "universe"].includes(key))) as DataSettings;
  const base = {...rest, timeframe: data.timeframe ?? "1d", since: data.since ?? (universe === "index" ? RESEARCH_SINCE : monthsBack(24)), until: data.until ?? "latest"};
  return universe === "symbols" ? {...base, symbols: []} : universe === "list" ? {...base, list} : universe === "index" ? {...base, index} : {...base, universe: "code"};
}
/** Market's history boundary when a range starts too early: its reason names the earliest bar; the settings and the failed run show the same one. */
export interface HistoryBoundary {earliestAt: number; earliestDate?: string; reason: string}
export function historyBoundary(code: string, details: unknown): HistoryBoundary | null {
  if (code !== "history_unavailable") return null;
  const value = details as HistoryBoundary;
  return {earliestAt: value.earliestAt, earliestDate: value.earliestDate, reason: value.reason};
}
/** The first whole UTC day the history covers, the earliest a range can start on. */
const firstFullDay = (at: number) => new Date(Math.ceil(at / 86_400_000) * 86_400_000).toISOString().slice(0, 10);
export function HistoryStart({boundary, onStart}: {boundary: HistoryBoundary; onStart?(since: string): void}) {
  return <div className="qx-history-start">
    <span>{boundary.reason}</span>
    {onStart && <TxButton size="sm" onClick={() => onStart((boundary.earliestDate ?? firstFullDay(boundary.earliestAt)))}>{t("Start on {earliestDate}", { earliestDate: (boundary.earliestDate ?? firstFullDay(boundary.earliestAt)) })}</TxButton>}
  </div>;
}
const universeOf = (data: DataSettings): Universe => data.list ? "list" : data.index ? "index" : data.universe === "code" ? "code" : "symbols";
const selectionOf = (item: IndexMaterial): IndexSelection => ({instrument: item.index});
const sameIndex = (item: IndexMaterial, selection: IndexSelection | undefined) =>
  selection !== undefined && item.index.instrumentId === selection.instrument.instrumentId;

/** Market's indices with a historical membership source. */
export function useIndexMaterials(ctx: ModuleContext) {
  const market = useMemo(() => marketClient(ctx), [ctx]);
  const [items, setItems] = useState<IndexMaterial[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = async () => {
    const items: IndexMaterial[] = [];
    let cursor: string | null = null;
    do {
      const found = await market.read_coverage({data:"members",limit:100,cursor});
      const records = found.rows.map(row => Object.fromEntries(found.columns.map((c,i) => [c.name,row[i]])));
      if (records.length) {
        const ids = records.map(row => String(row.instrumentId)) as [string, ...string[]];
        let next: string | null = null, i = 0;
        do {
          const page = await market["instruments.get"]({symbols:ids,limit:100,cursor:next});
          for (const match of page.items) {
            const record = records[i++];
            if (match.instrument) items.push({index:match.instrument,source:record.source as {name:string},
              start:record.start as string|null,end:record.end as string|null});
          }
          next = page.nextCursor;
        } while (next);
      }
      cursor = found.nextCursor;
    } while (cursor);
    setItems(items); setError(null);
  };
  useEffect(() => {void load().catch(reason => setError(message(reason)));}, [market]);
  return {items, error};
}

/** Research lists every workspace shares, kept current with the backend's change event. */
export function useResearchLists(ctx: ModuleContext, api: Api) {
  const [items, setItems] = useState<ResearchListSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => api.lists().then(value => {setItems(value.items); setError(null);}, reason => setError(message(reason)));
  useEffect(() => {void load();}, [api]);
  useEvent(ctx, "quant.research_lists_changed", () => void load());
  return {items, error};
}

/** The toolbar summary of the data settings; opening it edits them in place, each change saved at once. */
/** Each save starts once the one before has answered, so an older one cannot land after a newer one. */
export function savesInTurn<T>(onSave: (next: T) => Promise<unknown>, onError: (reason: unknown) => void): (next: T) => void {
  let last: Promise<unknown> = Promise.resolve();
  return next => {last = last.then(() => onSave(next)).catch(onError);};
}

export function DataSettingsChip({ctx, api, kind, data, defaults, defaultsError, boundary, open, onOpenChange, onSave, trigger, children, alsoBuilt}: {
  ctx: ModuleContext; api: Api; kind: Kind; data: DataSettings; defaults: DataDefaults | null; defaultsError: string | null;
  /** The range starts before the history the source has. */
  boundary: HistoryBoundary | null;
  open: boolean; onOpenChange(open: boolean): void; onSave(data: DataSettings): Promise<void>;
  /** A page's own chip in place of the summary, and what it adds under the settings (a factor's data limits and extra series). */
  trigger?: ReactNode; children?: ReactNode;
  /** The larger bars the code builds from the signal bars (e.g. "4 hours, 1 day from 1 hour"), read from the code; shown only when there are some. */
  alsoBuilt?: string | null;
}) {
  const {items: lists, error: listsError} = useResearchLists(ctx, api);
  const indexes = useIndexMaterials(ctx);
  const summary = dataSummary(data, lists, indexes.items);
  const research = RESEARCH_KINDS.includes(kind);
  const first = (data.symbols ?? [])[0]?.instrument;
  const {rows: capabilities, error: capabilityError} = useCapabilities(ctx, research ? undefined : first);
  const verified = capabilities === null || first === undefined ? null : verifiedTimeframes(capabilities);
  const [searching, setSearching] = useState<"symbols" | "list" | null>(null);
  const [listDialog, setListDialog] = useState<"create" | "rename" | "delete" | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Saves go one after another, each from the settings the one before left: symbols added in quick succession all stay (four of eleven were lost,
  // each add starting again from settings that did not have the one before).
  const latest = useRef(data);
  useEffect(() => {latest.current = data;}, [data]);
  // The symbols last chosen: looking at a research list or an index and coming back to Symbols brings them back instead of an empty pool.
  const kept = useRef(data.symbols ?? []);
  if (data.symbols?.length) kept.current = data.symbols;
  const saves = useRef<(next: DataSettings) => void>();
  const saveNow = useRef(onSave); saveNow.current = onSave;
  saves.current ??= savesInTurn(next => saveNow.current(next), reason => {setPicked(null); setError(message(reason));});
  // What was picked shows at once; the saved settings replace it when they come back (picking Index waited 0.8 s and more on the save, ).
  const [picked, setPicked] = useState<DataSettings | null>(null);
  useEffect(() => setPicked(null), [data]);
  const save = (next: DataSettings) => {setError(null); latest.current = next; setPicked(next); saves.current!(next);};
  const [added, setAdded] = useState<string[]>([]);
  const symbols = data.symbols ?? [];
  const universe = universeOf(picked ?? data);
  const range = data.since && data.until ? rangeLabel(data.since, data.until) : "";
  const {detail: list, error: listError} = useListDetail(ctx, api, data.list);
  const searchProps = useMemo<Json>(() => ({placeholder: t("Search symbol")}), []);
  const searchEvents = useMemo(() => ({
    selected: (raw: Json) => {
      const {instrument, series} = raw as unknown as MarketTypes["view:search_box:selected"];
      if (series !== null) {setError(t("Choose a Market instrument; research does not use imported or derived series.")); setSearching(null); onOpenChange(true); return;}
      const target = searching;
      // Symbols: the search stays open for the next one (an eleven-asset pool took eleven round trips); a list member closes it as before.
      if (target === "symbols") {save(withSymbol(latest.current, instrument)); setAdded(current => current.includes(instrument.displaySymbol) ? current : [...current, instrument.displaySymbol]); return;}
      setSearching(null); onOpenChange(true);
      if (list !== null && !list.members.some(member => member.instrument.instrumentId === instrument.instrumentId))
        void api.updateList(list.id, {members: [...list.members.map(member => ({instrument: member.instrument})), {instrument}]}).catch(reason => setError(message(reason)));
    },
    closed: () => {setSearching(null); onOpenChange(true);},
  }), [data, searching, list]);
  const universes: {value: Universe; label: string}[] = [{value: "symbols", label: t("Symbols")}, {value: "list", label: t("Research list")}, {value: "index", label: t("Index")},
    ...(research ? [{value: "code" as const, label: t("Rule in the code")}] : [])];
  const pickUniverse = (value: Universe) => {
    if (value === universe) return;
    if (value === "list" && lists !== null && lists.length === 0) {setListDialog("create"); return;}
    if (value === "index") {
      if (indexes.items === null) return;
      if (indexes.items.length === 0) {setError(t("Market keeps historical members for no index.")); return;}
      // Index members are stocks: daily bars suit them, whatever the symbols used before.
      save({...choose(data, "index", undefined, selectionOf(indexes.items[0])), timeframe: "1d"});
      return;
    }
    if (value === "symbols") {save({...choose(data, "symbols"), symbols: kept.current}); return;}
    save(choose(data, value, value === "list" && lists !== null ? lists[0].id : undefined));
  };
  const index = indexes.items === null ? undefined : indexes.items.find(item => sameIndex(item, (picked ?? data).index));
  return <>
    <TxPopover open={open} onOpenChange={onOpenChange} label={t("Data settings")} className="qx-data"
      trigger={trigger ?? <button type="button" className="qx-chip" data-need={summary === null}><TxIcon name="list" /><span className="qx-chip-text">{summary ?? t("Set data")}</span></button>}>
      <div className="qx-data-grid">
        <span className="qx-data-key">{t("Universe")}</span>
        <TxSegmented label={t("Universe")} value={universe} options={universes} onChange={pickUniverse} />
        {universe === "symbols" && <>
          <span className="qx-data-key">{t("Symbols")}</span>
          <div className="qx-data-symbols">
            {symbols.map(item => <span key={item.instrument.instrumentId} className="qx-symbol">
              <span>{item.instrument.displaySymbol}</span><span className="qx-muted">{item.instrument.venueId}</span>
              <TxIconButton size="sm" label={t("Remove {displaySymbol}", { displaySymbol: item.instrument.displaySymbol })} icon="close"
                onClick={() => save({...latest.current, symbols: (latest.current.symbols ?? []).filter(other => other.instrument.instrumentId !== item.instrument.instrumentId)})} />
            </span>)}
            <TxButton size="sm" onClick={() => {onOpenChange(false); setAdded([]); setSearching("symbols");}}><TxIcon name="plus" />{symbols.length ? t("Add") : t("Choose symbol")}</TxButton>
          </div>
        </>}
        {universe === "list" && <>
          <span className="qx-data-key">{t("List")}</span>
          <div className="qx-data-list">
            <TxSelect aria-label={t("Research list")} value={data.list} onChange={event => save(choose(data, "list", event.target.value))}>
              {(lists ?? []).map(item => <option key={item.id} value={item.id}>{item.name} · {item.count}</option>)}
            </TxSelect>
            <TxButton size="sm" onClick={() => setListDialog("create")}><TxIcon name="plus" />{t("New list")}</TxButton>
            <TxMenu label={t("List actions")} align="end" items={[
              {id: "rename", label: t("Rename…"), icon: "edit", onSelect: () => setListDialog("rename")},
              {id: "delete", label: t("Delete…"), icon: "trash", tone: "danger", onSelect: () => setListDialog("delete")},
            ]} trigger={<TxIconButton size="sm" label={t("List actions")} icon="more" />} />
          </div>
          <span className="qx-data-key">{t("Members")}</span>
          {list === null ? listError ? <span className="qx-error">{listError}</span> : <TxSkeleton width="50%" /> : <ListMembers list={list} onAdd={() => {onOpenChange(false); setSearching("list");}}
            onRemove={id => void api.updateList(list.id, {members: list.members.filter(member => member.instrument.instrumentId !== id).map(member => ({instrument: member.instrument}))})
              .catch(reason => setError(message(reason)))} />}
        </>}
        {universe === "index" && <>
          <span className="qx-data-key">{t("Index")}</span>
          <div className="qx-data-list">
            <TxSelect aria-label={t("Historical index")} value={index === undefined ? "" : index.index.instrumentId}
              onChange={event => {const next = indexes.items!.find(item => item.index.instrumentId === event.target.value)!; save(choose(data, "index", undefined, selectionOf(next)));}}>
              {index === undefined && <option value="">{indexes.items === null ? t("Loading…") : t("Not found in Market")}</option>}
              {(indexes.items ?? []).map(item => <option key={item.index.instrumentId} value={item.index.instrumentId}>{indexLabel(item)}</option>)}
            </TxSelect>
          </div>
          <span className="qx-data-key">{t("Members")}</span>
          {index === undefined ? <span className="qx-muted">{indexes.error ?? "—"}</span> : <IndexFacts ctx={ctx} item={index} at={untilDay(data) ?? lastFullDay()} asOf={data.index?.asOf} />}
        </>}
        {universe === "code" && <>
          <span className="qx-data-key">{t("Rule")}</span>
          <span className="qx-muted">{t("Picked by the code")}</span>
        </>}
        <span className="qx-data-key">{t("Interval")}</span>
        <TxSegmented label={t("Interval")} value={data.timeframe ?? ("" as Timeframe)} options={(verified === null ? TIMEFRAMES : TIMEFRAMES.filter(value => verified.includes(value) || value === data.timeframe)).map(value => ({value, label: value}))}
          onChange={timeframe => save({...data, timeframe, ...(data.fillBars ? {fillBars: undefined} : {})})} />
        <span className="qx-data-key">{t("Range")}</span>
        <div className="qx-data-range">
          <TxSegmented label={t("Range")} value={range} options={RANGES.map(([label]) => ({value: label, label}))}
            onChange={label => save({...data, since: monthsBack(RANGES.find(item => item[0] === label)![1]), until: "latest"})} />
          <DayInput label={t("From")} value={data.since ?? null} max={untilDay(data)} onChange={day => {if (day) save({...data, since: day});}} />
          <UntilField until={data.until} since={data.since} onChange={until => save({...data, until})} />
        </div>
        {boundary !== null && <><span className="qx-data-key" /><HistoryStart boundary={boundary} onStart={since => save({...data, since})} /></>}
        {research && <>
          <span className="qx-data-key">{t("Prices")}</span>
          <TxSegmented label={t("Prices")} value={data.adjustment ?? "adjusted"} options={[{value: "adjusted", label: t("Adjusted for splits and dividends")}, {value: "raw", label: t("Raw")}]}
            onChange={adjustment => save({...data, adjustment})} />
        </>}
        {kind === "strategy" && verified !== null && fillChoices(verified, data.timeframe).length > 0 && <>
          <span className="qx-data-key">{t("Fill with")}</span>
          <TxSelect aria-label={t("Fill with")} value={data.fillBars ?? ""} onChange={event => save({...data, fillBars: (event.target.value || undefined) as DataSettings["fillBars"]})}>
            <option value="">{t("Same bars")}</option>{fillChoices(verified, data.timeframe).map(item => <option key={item} value={item}>{item === "5m" ? t("5 minutes") : t("1 minute")}</option>)}</TxSelect>
        </>}
        {kind === "strategy" && alsoBuilt && <><span className="qx-data-key">{t("Also built")}</span><span className="qx-data-defaults">{alsoBuilt}</span></>}
        {kind === "strategy" && first !== undefined && capabilities !== null && extraSeriesOf(capabilities).length > 0 && <>
          <span className="qx-data-key">{t("Extra series")}</span><span className="qx-data-defaults">{extraSeriesOf(capabilities).join(", ")}</span></>}
        {kind === "strategy" && <><span className="qx-data-key">{t("Held out from")}</span><span className="qx-data-defaults">{data.heldOutFrom ? dayShown(data.heldOutFrom) : t("none")}</span></>}
        {!research && (defaults !== null || defaultsError !== null || boundary !== null) && <>
          <span className="qx-data-key">{t("Venue")}</span>
          <span className="qx-data-defaults">{defaults ? <>
            {[...new Set(defaults.instruments.map(item => `${item.venueLabel} ${item.marketType === "swap" ? "perpetual" : item.marketType}`))].join(", ")}
            {" · "}{defaults.account.fee === null ? (defaults.instruments.some(item => item.assetClass !== "equity" && item.assetClass !== "fund") ? t("Fees per symbol, from Market") : t("Stock commission and taxes follow instrument rules")) : <>{t("Fees {maker} maker / {taker} taker", { maker: fee(defaults.account.fee.maker), taker: fee(defaults.account.fee.taker) })}</>}
          </> : boundary !== null ? "—" : defaultsError}</span>
        </>}
      </div>
      {children}
      {(error ?? listsError ?? capabilityError) && <p role="alert" className="qx-error">{error ?? listsError ?? capabilityError}</p>}
      <div className="qx-data-foot">
        <span className="tx-spacer" />
        <TxButton variant="primary" onClick={() => onOpenChange(false)}>{t("Done")}</TxButton>
      </div>
    </TxPopover>
    <TxDialog open={searching !== null} onOpenChange={value => {if (!value) {setSearching(null); onOpenChange(true);}}} title={searching === "list" && list !== null ? t("Add to {name}", { name: list.name }) : t("Add symbols")} size="standard"
      footer={searching === "symbols" ? <><span className="qx-muted">{added.length ? t("Added {added}", { added: added.join(", ") }) : ""}</span><span className="tx-spacer" />
        <TxButton size="sm" variant="primary" onClick={() => {setSearching(null); onOpenChange(true);}}>{t("Done")}</TxButton></> : undefined}>
      <div className="qx-search-host">{searching !== null && <ModuleView id="market.search_box" context={ctx} props={searchProps} on={searchEvents} />}</div>
    </TxDialog>
    {/* A dialog opened from the data settings goes back to them when it closes, as the symbol search does. */}
    {listDialog !== null && <ListDialog action={listDialog} api={api} list={list} names={(lists ?? []).map(item => item.name)} onClose={() => {setListDialog(null); onOpenChange(true);}}
      onCreated={created => {setListDialog(null); onOpenChange(true); save(choose(data, "list", created.id));}}
      onDelete={async () => {
        // This page selects the list, so it lets go of it first; if something else still selects it, the deletion is refused and the choice comes back.
        const listId = list!.id;
        await onSave(choose(data, "symbols"));
        try {await api.deleteList(listId);} catch (reason) {await onSave(data); throw reason;}
        setListDialog(null); onOpenChange(true);
      }} />}
  </>;
}

function indexLabel(item: IndexMaterial) {
  const range = item.start === null ? "" : " · " + item.start.slice(0, 7) + " – " + (item.end === null ? "latest" : item.end.slice(0, 7));
  return (item.index.name ?? item.index.displaySymbol) + " · " + item.source.name + range;
}
/** The universe, the members and the range as rows in a page's own settings group (the wide and full model layouts): the same state and saving as the popover. */
export function DataInline({ctx, api, data, onSave}: {ctx: ModuleContext; api: Api; data: DataSettings; onSave(data: DataSettings): Promise<void>}) {
  const {items: lists} = useResearchLists(ctx, api);
  const indexes = useIndexMaterials(ctx);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<DataSettings | null>(null);
  useEffect(() => setPicked(null), [data]);
  const save = (next: DataSettings) => {setError(null); setPicked(next); void onSave(next).catch(reason => {setPicked(null); setError(message(reason));});};
  const universe = universeOf(picked ?? data);
  const pick = (value: Universe) => {
    if (value === universe) return;
    if (value === "list") {if (lists !== null && lists.length > 0) save(choose(data, "list", lists[0].id)); return;}
    if (value === "index") {if (indexes.items !== null && indexes.items.length > 0) save({...choose(data, "index", undefined, selectionOf(indexes.items[0])), timeframe: "1d"}); return;}
    save(choose(data, value));
  };
  const index = indexes.items === null ? undefined : indexes.items.find(item => sameIndex(item, (picked ?? data).index));
  const options: {value: Universe; label: string}[] = [{value: "symbols", label: t("Symbols")}, {value: "list", label: t("Research list")}, {value: "index", label: t("Index")}];
  return <>
    <TxParamRow label={t("Universe")}><TxSegmented label={t("Universe")} value={universe} options={options} onChange={pick} /></TxParamRow>
    {universe === "index" && <TxParamRow label={t("Members")}>{indexes.items === null && !indexes.error ? <TxSkeleton width="60%" /> : index === undefined ? <span className="qx-muted">{indexes.error ?? t("Not found in Market")}</span> : <IndexFacts ctx={ctx} item={index} at={untilDay(data) ?? lastFullDay()} asOf={data.index?.asOf} />}</TxParamRow>}
    {universe === "list" && <TxParamRow label={t("Members")}><span className="qx-muted">{t("{count} names", { count: (lists ?? []).find(item => item.id === data.list)?.count ?? "—" })}</span></TxParamRow>}
    <TxParamRow label={t("From")}><DayInput label={t("From")} value={data.since ?? null} max={untilDay(data)} onChange={day => {if (day) save({...data, since: day});}} /></TxParamRow>
    <TxParamRow label={t("To")}><UntilField until={data.until} since={data.since} onChange={until => save({...data, until})} /></TxParamRow>
    {error && <p role="alert" className="qx-error">{error}</p>}
  </>;
}

function IndexFacts({ctx, item, at, asOf}: {ctx: ModuleContext; item: IndexMaterial; at: string; asOf?: number}) {
  const market = useMemo(() => marketClient(ctx), [ctx]);
  const [counts, setCounts] = useState<{mapped: number; unmapped: number} | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true; setCounts(null); setError(null);
    void (async () => {
      const time = Date.parse(at + "T23:59:59.999Z");
      const cutoff = new Date(asOf ?? time).toISOString();
      const mapped = new Set<string>(); let unmapped = 0, cursor: string | null = null;
      do {
        const page = await market.read_research({data:"members",index:{symbol:item.index.instrumentId,at},asOf:cutoff,limit:5000,cursor});
        const column = page.columns.findIndex(c => c.name === "instrumentId");
        for (const row of page.rows) if (typeof row[column] === "string") mapped.add(row[column] as string);
        unmapped = page.unmapped; cursor = page.nextCursor;
      } while (cursor !== null);
      if (live) setCounts({mapped: mapped.size, unmapped});
    })().catch(reason => {if (live) setError(message(reason));});
    return () => {live = false;};
  }, [market, item.index.instrumentId, at, asOf]);
  return <span className="qx-data-defaults">
    {error !== null ? error : counts === null ? <TxSkeleton width="12em" /> : <>{t("{mapped} of {mapped2} members have Market symbols", { mapped: counts.mapped, mapped2: counts.mapped + counts.unmapped })}</>}
  </span>;
}

/** The selected list's members, read again whenever any list changes. */
function useListDetail(ctx: ModuleContext, api: Api, listId: string | undefined) {
  const [detail, setDetail] = useState<ResearchList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = (id: string) => api.readList(id).then(value => {setDetail(value); setError(null);}, reason => setError(message(reason)));
  useEffect(() => {setDetail(null); setError(null); if (listId) void load(listId);}, [listId]);
  useEvent(ctx, "quant.research_lists_changed", payload => {if (listId && (payload as {listId: string}).listId === listId) void load(listId);});
  return {detail: detail !== null && detail.id === listId ? detail : null, error};
}

function ListMembers({list, onAdd, onRemove}: {list: ResearchList; onAdd(): void; onRemove(instrumentId: string): void}) {
  const [all, setAll] = useState(false);
  const shown = all ? list.members : list.members.slice(0, SHOWN);
  return <div className="qx-data-symbols">
    {shown.map(member => <span key={member.instrument.instrumentId} className="qx-symbol">
      <span>{member.displaySymbol}</span><span className="qx-muted">{member.venue}</span>
      <TxIconButton size="sm" label={t("Remove {displaySymbol} from {name}", { displaySymbol: member.displaySymbol, name: list.name })} icon="close" onClick={() => onRemove(member.instrument.instrumentId)} />
    </span>)}
    {list.members.length > SHOWN && <TxButton size="sm" variant="quiet" onClick={() => setAll(!all)}>{all ? t("Show fewer") : t("Show all {membersCount}", { membersCount: list.members.length })}</TxButton>}
    <TxButton size="sm" onClick={onAdd}><TxIcon name="plus" />{t("Add symbol")}</TxButton>
  </div>;
}

/** The name a new list gets when none is typed, as a new object gets one: "List 1", or the next number no list has. */
export const nextListName = (names: string[]) => {
  const taken = new Set(names.map(name => name.trim().toLowerCase()));
  let at = 1;
  while (taken.has(t("List {number}", { number: at }).toLowerCase())) at += 1;
  return t("List {number}", { number: at });
};

/** Create, rename or delete a research list. Deleting one that something's data settings still select is refused, with who selects it. */
function ListDialog({action, api, list, names, onClose, onCreated, onDelete}: {
  action: "create" | "rename" | "delete"; api: Api; list: ResearchList | null; names: string[]; onClose(): void; onCreated(list: ResearchList): void; onDelete(): Promise<void>;
}) {
  const [name, setName] = useState(action === "rename" && list !== null ? list.name : "");
  // An empty name creates the next "List N" (shown as the placeholder); an emptied rename keeps the name it had.
  const fallback = action === "create" ? nextListName(names) : list?.name ?? "";
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true); setError(null);
    try {
      if (action === "create") onCreated(await api.createList(name.trim() || fallback));
      else if (action === "rename") {await api.updateList(list!.id, {name: name.trim() || fallback}); onClose();}
      else await onDelete();
    } catch (reason) {
      setError(reason instanceof ContractError && reason.code === "conflict" ? t("Not deleted. {reason}", { reason: reason.message }) : message(reason));
    } finally {setBusy(false);}
  };
  const title = action === "create" ? t("New research list") : action === "rename" ? t("Rename research list") : t("Delete research list");
  return <TxDialog open onOpenChange={value => {if (!value) onClose();}} title={title}
    footer={<><TxButton onClick={onClose}>{t("Cancel")}</TxButton><TxButton variant="primary" onClick={() => {if (!busy) void submit();}}>
      {action === "create" ? t("Create") : action === "rename" ? t("Rename") : t("Delete")}</TxButton></>}>
    {action === "delete" ? <p>{t("Delete {value}? This page stops using it; runs that used it keep the members they ran with.", { value: list === null ? t("this list") : list.name })}</p>
      : <>
        <TxInput autoFocus aria-label={t("Name")} placeholder={fallback} value={name} onChange={event => setName(event.target.value)}
          onKeyDown={event => {if (event.key === "Enter" && !busy) void submit();}} />
      </>}
    {error && <p role="alert" className="qx-error">{error}</p>}
  </TxDialog>;
}

