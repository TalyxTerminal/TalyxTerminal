/* The Values tab (an allocation's Weights): one day of a version by name, a page at a time, with the day before; the names without a value and why (library.js valuesTab). */
import { t } from "../i18n";
import { useEffect, useRef, useState } from "react";
import { TxSearch, TxSegmented, TxTable, TxTd, TxTh, TxSkeleton } from "@talyx/sdk";
import { hm, mdy, pct, pp, sgn, tone } from "./format";
import type { ItemVersion, LibraryApi, ValuesArgs, ValuesDay } from "./api";

export const valuesQuery = (args: ValuesArgs): ValuesArgs => Object.fromEntries(Object.entries(args).filter(([, value]) => value !== undefined && value !== "")) as ValuesArgs;

/** The day shown, typed over in the app's writing ("Sep 21, 2018") or as 2018-09-21, as the factor's Values day is (not the browser's date box, which
 * writes the system's language and whose picker failed silently, ); the day on or before it that has values is read. */
function DayField({date, onDate}: {date: number; onDate(ms: number): void}) {
  const [text, setText] = useState<string | null>(null);
  const commit = () => {
    if (text === null) return;
    const typed = text.trim();
    const ms = /^\d{4}-\d{2}-\d{2}$/.test(typed) ? Date.parse(typed + "T00:00:00Z") : Date.parse(typed + " 00:00 UTC");
    setText(null);
    if (!Number.isNaN(ms) && ms !== date) onDate(ms);
  };
  return <input className="lb-dayfield" aria-label={t("Day")} value={text ?? mdy(date)} spellCheck={false} onFocus={event => {setText(mdy(date)); event.currentTarget.select();}}
    onChange={event => setText(event.target.value)} onBlur={commit} onKeyDown={event => {if (event.key === "Enter") event.currentTarget.blur(); else if (event.key === "Escape") {setText(null); event.currentTarget.blur();}}} />;
}

export function ValuesBar({day, order, q, onDate, onOrder, onQuery, onPage, weights = false}: {weights?: boolean; day: ValuesDay; order: "top" | "bottom" | "missing"; q: string; onDate(ms: number): void; onOrder(order: "top" | "bottom" | "missing"): void; onQuery(text: string): void; onPage(delta: number): void}) {
  const from = day.page * 50 + 1, to = Math.min(day.matching, (day.page + 1) * 50);
  return <div className="lb-vbar">
    <span className="lb-datenav"><button type="button" className="lb-step" aria-label={t("Day before")} disabled={day.previousDay === null} onClick={() => day.previousDay !== null && onDate(day.previousDay)}>‹</button>
      <DayField date={day.date} onDate={onDate} />
      <button type="button" className="lb-step" aria-label={t("Day after")} disabled={day.nextDay === null} onClick={() => day.nextDay !== null && onDate(day.nextDay)}>›</button></span>
    <span className="lb-t3">{t.rich("{have} of {total}", { have: <b className="lb-num lb-strong">{day.have}</b>, total: day.total })}</span>
    {day.readyAt !== null && day.dueAt !== null && <span className="lb-t3">{t.rich("ready {time}, {state}", { time: hm(day.readyAt), state: day.onTime ? t("on time") : <span className="lb-watch">{t("late")}</span> })}</span>}
    <span className="lb-sp" />
    {!weights && <><TxSegmented label={t.c("sorting", "Order")} value={order} onChange={onOrder} options={[{value: "top", label: t("Highest")}, {value: "bottom", label: t("Lowest")}, {value: "missing", label: t("Missing {missing}", { missing: day.missing })}]} />
    <TxSearch value={q} onValueChange={onQuery} placeholder={t("Name")} aria-label={t("Name")} />
    <span className="lb-pager"><button type="button" className="lb-step" aria-label={t("Previous page")} disabled={day.page === 0} onClick={() => onPage(-1)}>‹</button>{(day.matching === 0 ? t("0 of {matching}", { matching: day.matching }) : t("{from}–{to} of {matching}", { from, to, matching: day.matching }))}<button type="button" className="lb-step" aria-label={t("Next page")} disabled={day.page >= day.pages - 1} onClick={() => onPage(1)}>›</button></span></>}
  </div>;
}

/** An allocation's weights as published and on this day, one row an asset, with the change in points. */
export function WeightsTable({weights}: {weights: NonNullable<ValuesDay["weights"]>}) {
  const then = new Map(weights.published.map(item => [item.instrumentId, item])), now = new Map(weights.thisPeriod.map(item => [item.instrumentId, item]));
  const rows = [...new Set([...then.keys(), ...now.keys()])].map(id => ({id, name: (now.get(id) ?? then.get(id))!.name, then: then.get(id)?.weight ?? null, now: now.get(id)?.weight ?? null}))
    .sort((a, b) => (b.now ?? b.then ?? 0) - (a.now ?? a.then ?? 0));
  return <TxTable className="lb-weights"><thead><tr><TxTh>{t("Asset")}</TxTh><TxTh align="right">{t("Weight, published {publishedDate}", { publishedDate: mdy(weights.publishedDate) })}</TxTh><TxTh align="right">{t("Weight, this period")}</TxTh><TxTh align="right">{t("Change")}</TxTh></tr></thead>
    <tbody>{rows.map(row => <tr key={row.id}><TxTd className="lb-num">{row.name}</TxTd><TxTd align="right" className="lb-num">{row.then === null ? "" : pct(row.then, 1)}</TxTd><TxTd align="right" className="lb-num">{row.now === null ? "" : pct(row.now, 1)}</TxTd>
      <TxTd align="right" className="lb-num lb-t3">{row.then === null || row.now === null ? "" : Math.abs(row.now - row.then) < .0005 ? t("0.0 pt") : pp(row.now - row.then, 1)}</TxTd></tr>)}</tbody></TxTable>;
}

export function ItemValues({api, version, kind, date, onDate, onLog}: {api: LibraryApi; version: ItemVersion; kind: string; date: number | null; onDate(ms: number | null): void; onLog(): void}) {
  const [order, setOrder] = useState<"top" | "bottom" | "missing">("top");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const [day, setDay] = useState<ValuesDay | null>(null);
  const [error, setError] = useState<string | null>(null);
  const request = useRef(0);
  useEffect(() => {
    const mine = ++request.current;
    api.values(version.versionId, valuesQuery({date: date ?? undefined, order, q, page})).then(value => {if (mine === request.current) {setDay(value); setError(null);}}, reason => {if (mine === request.current) setError(reason instanceof Error ? reason.message : String(reason));});
  }, [version.versionId, date, order, q, page, version.cursor]);
  if (error !== null) return <div className="lb-calm" role="alert">{t("No values to show: {error}", { error })}</div>;
  if (day === null) return <div className="lb-pad"><TxSkeleton width="40%" /></div>;
  if (day.rows.length === 0 && day.have === 0) return <div className="lb-calm">{day.date ? t("No values for {date}", { date: mdy(day.date) }) : t("No values yet")}<button type="button" className="lb-link" onClick={onLog}>{t("Production log")}</button></div>;
  const weights = kind === "allocation";
  return <div>
    <ValuesBar weights={weights} day={day} order={order} q={q} onDate={ms => {onDate(ms); setPage(0);}} onOrder={value => {setOrder(value); setPage(0);}} onQuery={text => {setQ(text); setPage(0);}} onPage={delta => setPage(Math.max(0, page + delta))} />
    <div className="lb-pad lb-vtab">{weights && day.weights ? <WeightsTable weights={day.weights} /> : order === "missing"
      ? <TxTable><thead><tr><TxTh>{t("Name")}</TxTh><TxTh>{t("Sector")}</TxTh><TxTh>{t("Why no value")}</TxTh></tr></thead><tbody>{(day.rows as unknown as {name: string; sector: string | null; why: string}[]).map(row => <tr key={row.name}><TxTd className="lb-num">{row.name}</TxTd><TxTd className="lb-t3">{row.sector ?? ""}</TxTd><TxTd className="lb-t2">{row.why}</TxTd></tr>)}</tbody></TxTable>
      : <TxTable><thead><tr><TxTh align="right" style={{width: 52}}>{t("Rank")}</TxTh><TxTh>{t("Name")}</TxTh><TxTh>{t("Sector")}</TxTh><TxTh align="right">{kind === "model" ? t("Prediction") : weights ? t("Weight") : t("Value")}</TxTh><TxTh align="right">{t("Day before")}</TxTh><TxTh align="right">{t("Change")}</TxTh></tr></thead>
        <tbody>{day.rows.map(row => <tr key={row.instrumentId}><TxTd align="right" className="lb-t3 lb-num">{row.rank}</TxTd><TxTd className="lb-num">{row.name}</TxTd><TxTd className="lb-t3">{row.sector ?? ""}</TxTd>
          <TxTd align="right" className={`lb-num ${weights ? "" : tone(row.value) ?? ""}`}>{weights ? pct(row.value, 1) : sgn(row.value, 2)}</TxTd><TxTd align="right" className="lb-num lb-t2">{row.previous == null ? "" : weights ? pct(row.previous, 1) : sgn(row.previous, 2)}</TxTd>
          <TxTd align="right" className="lb-num lb-t3">{row.previous == null ? "" : weights ? pp(row.value - row.previous, 1) : sgn(row.value - row.previous, 2)}</TxTd></tr>)}</tbody></TxTable>}</div>
  </div>;
}
