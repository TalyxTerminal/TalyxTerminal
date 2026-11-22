import { t } from "../i18n";
/* The screener tab: a saved screen picks the members of a watchlist whose latest closed bar on a timeframe meets every
   condition (prices, the active chart's indicator outputs, values). Run saves the screen first; progress and results
   come back on the workspace stream; a running screen can be cancelled. Matching rows, then failed ones; a click shows
   the symbol on the chart. */
import { useState } from "react";
import { TxButton, TxEmpty, TxIcon, TxIconButton, TxInput, TxMenu, TxProgress, TxSelect, TxTable, TxTd, TxTh, type ModuleContext } from "@talyx/sdk";
import { post } from "../api";
import { message, switchSymbol, useStore } from "../store";
import { timeframeLabel } from "../chart/timeframes";
import { TIMEFRAME_GROUPS } from "../panel/TimeframeMenu";
import { ConditionFields, indicatorSources, type Sources } from "../panel/conditions";
import type { Condition, ScreenSpec } from "../wire";

const MAX_CONDITIONS = 5;
const PRICES: Sources = new Map((["close", "open", "high", "low", "volume"] as const).map(field =>
  [`price:${field}`, { label: field[0].toUpperCase() + field.slice(1), operand: { kind: "price", field } }]));

const number = (value: number | null) => value === null ? "∅" : value.toLocaleString("en-US", { maximumFractionDigits: 6 });

export function ScreenerTab({ ctx, panelId }: { ctx: ModuleContext; panelId: string }) {
  const screens = useStore(state => state.screens);
  const watchlists = useStore(state => state.watchlists);
  const catalog = useStore(state => state.catalog);
  const entry = useStore(state => state.charts[state.layouts[panelId].active]);
  const target = ctx.initialState;
  const [selected, setSelected] = useState<string | null>(() =>
    target && typeof target === "object" && !Array.isArray(target) && typeof target.objectId === "string" &&
    target.objectId.startsWith("screen:") ? target.objectId.slice("screen:".length) : null);
  const [draft, setDraft] = useState<ScreenSpec | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const list = Object.values(screens).sort((a, b) => a.name.localeCompare(b.name));
  const lists = Object.values(watchlists).sort((a, b) => a.name.localeCompare(b.name));
  const saved = selected === "new" ? null : list.find(item => item.id === selected) ?? list[0] ?? null;
  const last = entry ? entry.feed.bars.close[entry.feed.bars.close.length - 1] ?? 0 : 0;
  const fresh = (): ScreenSpec => ({
    name: t("Screen {number}", { number: list.length + 1 }), scope: { kind: "watchlist", watchlistId: lists[0].id }, timeframe: entry?.chart?.document.series.timeframe ?? "1d",
    conditions: [{ left: { kind: "price", field: "close" }, op: "greater", right: { kind: "value", value: last } }],
  });
  if (!lists.length) return <div className="chart-bottom-body"><TxEmpty title={t("No watchlist")} /></div>;
  const spec = draft ?? saved ?? fresh();
  const edit = (change: Partial<ScreenSpec>) => setDraft({ ...spec, ...change });
  const sources: Sources = new Map([...PRICES, ...(entry?.chart ? indicatorSources(entry, catalog, false) : [])]);
  const run = saved?.lastRun ?? null, running = run?.state === "running";
  const fail = (error: unknown) => setProblem(message(error));

  const start = async () => {
    setProblem(null);
    try {
      const screen = draft || !saved ? await post(ctx, "/screens/save", { ...(saved ? { screenId: saved.id, revision: saved.revision } : {}), spec }) : saved;
      setSelected(screen.id); setDraft(null);
      await post(ctx, "/screens/run", { screenId: screen.id });
    } catch (error) { fail(error); }
  };
  const remove = () => post(ctx, "/screens/delete", { screenId: saved!.id }).then(() => { setSelected(null); setDraft(null); }, fail);
  const setCondition = (index: number, condition: Condition) => edit({ conditions: spec.conditions.map((item, at) => at === index ? condition : item) });

  const matched = run ? run.rows.filter(row => row.matched) : [], failed = run ? run.rows.filter(row => row.error) : [];
  const columns = [...new Set([...matched, ...failed].flatMap(row => Object.keys(row.values)))];
  return <div className="chart-bottom-body">
    <div className="chart-bottom-toolbar" role="toolbar" aria-label={t("Screener")}>
      <TxMenu label={t("Screens")} items={[
        ...list.map(item => ({ id: item.id, label: item.name, checked: item.id === saved?.id, onSelect: () => { setSelected(item.id); setDraft(null); } })),
        { id: "new", label: t("New screen"), icon: "plus" as const, separated: list.length > 0, onSelect: () => { setSelected("new"); setDraft(null); } },
        ...(saved ? [{ id: "delete", label: t("Delete screen"), icon: "trash" as const, tone: "danger" as const, onSelect: () => void remove() }] : []),
      ]} trigger={<TxButton variant="tool" size="sm" aria-label={t("Screens")}><TxIcon name="list" /></TxButton>} />
      <TxInput className="chart-screen-name" aria-label={t("Screen name")} value={spec.name} onChange={event => edit({ name: event.target.value })} />
      <TxSelect aria-label={t("Watchlist")} value={spec.scope.kind === "watchlist" ? spec.scope.watchlistId : ""} onChange={event => edit({ scope: { kind: "watchlist", watchlistId: event.target.value } })}>
        {lists.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
      </TxSelect>
      <TxSelect aria-label={t("Timeframe")} value={spec.timeframe} onChange={event => edit({ timeframe: event.target.value })}>
        {TIMEFRAME_GROUPS.flatMap(([, timeframes]) => timeframes).map(timeframe => <option key={timeframe} value={timeframe}>{timeframeLabel(timeframe)}</option>)}
      </TxSelect>
      {running
        ? <TxButton size="sm" onClick={() => void post(ctx, "/screens/cancel", { screenId: saved!.id }).catch(fail)}>{t("Cancel")}</TxButton>
        : <TxButton size="sm" variant="primary" disabled={!spec.name.trim()} onClick={() => void start()}>{t("Run")}</TxButton>}
      {running && <TxProgress value={run.total ? run.done / run.total : null} label={`${run.done} / ${run.total}`} />}
      {problem && <span className="chart-alert-error" role="alert">{problem}</span>}
      <span className="chart-tb-spacer" />
      {run && !running && <span className="chart-muted">{run.state === "failed" ? run.error : t("{length} of {total} match", { length: matched.length, total: run.total })}</span>}
    </div>
    <div className="chart-screen-conditions">
      {spec.conditions.map((condition, index) => <div key={index} className="chart-screen-condition">
        <ConditionFields condition={condition} sources={sources} last={last} onChange={next => setCondition(index, next)} />
        {spec.conditions.length > 1 && <TxIconButton size="sm" icon="close" label={t("Remove condition")} onClick={() => edit({ conditions: spec.conditions.filter((_, at) => at !== index) })} />}
      </div>)}
      {spec.conditions.length < MAX_CONDITIONS && <TxButton size="sm" variant="quiet" onClick={() => edit({ conditions: [...spec.conditions, fresh().conditions[0]] })}><TxIcon name="plus" />{t("Condition")}</TxButton>}
    </div>
    {run && <div className="chart-bottom-view chart-screen-results">
      <TxTable density="compact" stickyHead>
        <thead><tr><TxTh>{t("Symbol")}</TxTh>{columns.map(column => <TxTh key={column} numeric>{column}</TxTh>)}<TxTh /></tr></thead>
        <tbody>
          {[...matched, ...failed].map(row => <tr key={row.instrument.instrumentId} className="chart-screen-row" onClick={() => switchSymbol(ctx, panelId, row.instrument.instrumentId)}>
            <TxTd>{row.instrument.displaySymbol}</TxTd>
            {columns.map(column => <TxTd key={column} numeric>{row.error ? "" : number(row.values[column] ?? null)}</TxTd>)}
            <TxTd>{row.error && <span className="chart-alert-error">{row.error}</span>}</TxTd>
          </tr>)}
        </tbody>
      </TxTable>
    </div>}
  </div>;
}
