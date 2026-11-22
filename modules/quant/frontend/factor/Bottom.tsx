/* Under the result on a factor's Work: every run, the values of one day, and the library factors most like this one. */
import { t } from "../i18n";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { TxEmpty, TxIcon, TxRunTable, TxSkeleton, TxSegmented, TxTag, type TxRunColumn, type TxRunRow } from "@talyx/sdk";
import { type CrossSection, type FactorRun, type SimilarFactor } from "./api";
import { changeText } from "./draft";
import { clock, mdy, my, nf, pc, pct, sgn, tone } from "./format";

// ---------------------------------------------------------------- runs
/** The formula with the part that changed from the run before marked (whole names, not half of one). */
function formulaDiff(now: string, before: string | null): ReactNode {
  if (before === null || before === now) return now;
  let start = 0;
  while (start < now.length && now[start] === before[start]) start++;
  let tail = 0;
  while (tail < now.length - start && tail < before.length - start && now[now.length - 1 - tail] === before[before.length - 1 - tail]) tail++;
  while (start > 0 && /[\w$]/.test(now[start - 1])) start--;
  while (tail > 0 && /[\w$]/.test(now[now.length - tail])) tail--;
  return <>{now.slice(0, start)}<em>{now.slice(start, now.length - tail)}</em>{now.slice(now.length - tail)}</>;
}

function changesOf(run: FactorRun): ReactNode {
  if (run.kind === "sweep" && run.sweep) return <>{run.sweep.axes.map((axis, index) => <span key={axis.name}>{index ? " × " : ""}{axis.name === "costBps" ? t("cost") : axis.name} <em>{axis.values.join(", ")}</em></span>)}{t(", {total} runs", { total: run.sweep.total })}</>;
  if (run.kind === "heldout") {
    const {heldOutFrom, until} = run.settings.data;
    return <>{heldOutFrom && until ? t.rich("validation of #{number} on {period}", { number: run.ofNumber ?? "", period: <em>{my(Date.parse(heldOutFrom))} – {my(Date.parse(until))}</em> }) : t("validation of #{ofNumber}", { ofNumber: run.ofNumber ?? "" })}</>;
  }
  const items = run.changes.filter(change => change.what !== "formula" && change.what !== "code").map(changeText);
  if (!items.length) return run.changes.length ? null : <span className="fx-t4">{run.number === 1 ? t("first run") : t("same settings")}</span>;
  return <>{items.map((item, index) => <span key={index}>{index ? ", " : ""}{item.what} <em>{item.from} → {item.to}</em></span>)}</>;
}

const TAGS = (run: FactorRun, future: boolean): ReactNode => run.state === "failed" ? <TxTag tone="fix" title={run.failure?.title}>{t("Failed")}</TxTag>
  : future ? <TxTag tone="fix">{t("Future data")}</TxTag>
  : run.kind === "sweep" ? <TxTag>{t("Sweep")}</TxTag> : run.kind === "heldout" ? <TxTag icon="lock">{t("Held-out")}</TxTag>
  : ["queued", "preparing", "running", "cancel_requested"].includes(run.state) ? <TxTag tone="muted"><span className="tx-spin" />{run.state === "queued" ? t("Queued") : t("Running")}</TxTag>
  : run.state === "cancelled" ? <TxTag tone="muted">{t("Stopped")}</TxTag> : run.state === "interrupted" ? <TxTag tone="muted">{t("Interrupted")}</TxTag> : null;

type SortKey = "number" | "createdAt" | "rankIc" | "t" | "longShortNet" | "turnover" | "names";

/** The button that opens Results with the ticked runs drawn beside the selected one: there only once another run is ticked. */
export const compareLabel = (ticked: number): string | null => ticked > 0 ? t("Compare {count} in results", { count: ticked + 1 }) : null;

export function RunsView({runs, mode, selected, compared, ticked, futureOf, onSelect, onOpen, onTick, onStar}: {
  runs: FactorRun[]; mode: "formula" | "code"; selected: string | null; compared: string | null; ticked: Set<string>; futureOf(run: FactorRun): boolean;
  onSelect(runId: string): void; onOpen(runId: string): void; onTick(runId: string, on: boolean): void; onStar(run: FactorRun): void;
}) {
  const [sort, setSort] = useState<[SortKey, number]>(["number", -1]);
  if (!runs.length) return null;
  const value = (run: FactorRun, key: SortKey): number | null => key === "number" ? run.number : key === "createdAt" ? run.createdAt : run.numbers ? (run.numbers as unknown as Record<string, number | null>)[key] ?? null : null;
  const list = runs.slice().sort((a, b) => {
    const x = value(a, sort[0]), y = value(b, sort[0]);
    return (x === null ? 1 : 0) - (y === null ? 1 : 0) || ((x ?? 0) - (y ?? 0)) * sort[1];
  });
  const previous = (run: FactorRun) => runs.find(item => item.number < run.number && item.kind === "run" && item.formula !== null && item.state !== "failed") ?? null;
  const columns: TxRunColumn[] = [
    {key: "number", label: "#", kind: "id", sortable: true, sorted: sort[0] === "number"},
    {key: "createdAt", label: t("Time"), kind: "time", sortable: true, sorted: sort[0] === "createdAt", hideBelow: 640},
    {key: "fx", label: mode === "formula" || list.some(run => run.mode === "formula") ? t("Formula and settings") : t("Change"), kind: "change", align: "left"},
    {key: "rankIc", label: t("Rank IC"), sortable: true, sorted: sort[0] === "rankIc"},
    {key: "t", label: "t", sortable: true, sorted: sort[0] === "t", hideBelow: 560},
    {key: "longShortNet", label: t("L–S net"), sortable: true, sorted: sort[0] === "longShortNet"},
    {key: "turnover", label: t("Turnover"), sortable: true, sorted: sort[0] === "turnover", hideBelow: 700},
    {key: "names", label: t("Names"), sortable: true, sorted: sort[0] === "names", hideBelow: 760},
    {key: "tags", label: t("Tags"), kind: "text", align: "left"},
    {key: "star", label: "", kind: "text"},
  ];
  const rows: TxRunRow[] = list.map(run => {
    const future = futureOf(run);
    const numbers = run.numbers && (run.kind !== "sweep") ? run.numbers : null;
    const show = (number: number | null | undefined, text: (value: number) => string) => future ? <span className="fx-t4">—</span> : number == null || run.state !== "completed" ? "" : text(number);
    const before = previous(run);
    const codeChange = run.changes.find(change => change.what === "code");
    // A run keeps the kind it was made as: those before the factor became code still show their formula and its changes.
    const formula = run.mode === "formula" ? <><code>{formulaDiff(run.formula ?? "", run.kind === "run" && before ? before.formula : null)}</code><span className="fx-sx">{changesOf(run)}</span></>
      : <span className="fx-cx">{codeChange ? <>{t.rich("code {to}", { to: <em>{String(codeChange.to ?? "changed")}</em> })}{run.changes.length > 1 ? ", " : ""}</> : null}{changesOf(run)}</span>;
    return {
      id: run.id, selected: run.id === selected, compared: run.id === compared, checked: ticked.has(run.id),
      cells: {
        number: <>#{run.number}{run.version !== null && <TxTag tone="version">v{run.version}</TxTag>}</>, createdAt: clock(run.createdAt), fx: formula,
        rankIc: <><span className={future ? undefined : tone(numbers?.rankIc)}>{show(numbers?.rankIc, value => sgn(value, 4))}</span>
          {numbers && !future && numbers.rankIc != null && <span className="fx-hz" data-on={numbers.holding !== 5 ? "" : undefined}>{numbers.holding}D</span>}</>,
        t: show(numbers?.t, value => nf(value, 2)), longShortNet: <span className={future ? undefined : tone(numbers?.longShortNet)}>{show(numbers?.longShortNet, value => pc(value, 2))}</span>,
        turnover: run.kind === "heldout" ? "" : show(numbers?.turnover, value => pct(value, 0)), names: show(numbers?.names, value => nf(value, 0)),
        tags: TAGS(run, future),
        star: <button type="button" className="fx-star" data-on={run.starred ? "" : undefined} title={run.starred ? t("Kept: its values by day stay") : t("Keep")}
          onClick={event => {event.stopPropagation(); onStar(run);}}><TxIcon name="star" /></button>,
      },
    };
  });
  return <div className="fx-pad fx-runs">
    <TxRunTable columns={columns} rows={rows} onRow={onSelect} onOpen={onOpen} onCheck={(runId, on) => onTick(runId, on)}
      onSort={key => setSort(current => [key as SortKey, current[0] === key ? -current[1] : -1])} />
  </div>;
}

// ---------------------------------------------------------------- values on a day
/** The day shown, typed over in the app's writing ("Sep 21, 2018") or as 2018-09-21; the day on or before it that has bars is read. */
function DayField({date, onDate}: {date: number; onDate(date: number): void}) {
  const [text, setText] = useState<string | null>(null);
  const commit = () => {
    if (text === null) return;
    const typed = text.trim();
    const ms = /^\d{4}-\d{2}-\d{2}$/.test(typed) ? Date.parse(typed + "T00:00:00Z") : Date.parse(typed + " 00:00 UTC");
    setText(null);
    if (!Number.isNaN(ms) && ms !== date) onDate(ms);
  };
  return <input className="fx-dayfield" aria-label={t("Day")} value={text ?? mdy(date)} spellCheck={false} onFocus={event => {setText(mdy(date)); event.currentTarget.select();}}
    onChange={event => setText(event.target.value)} onBlur={commit} onKeyDown={event => {if (event.key === "Enter") event.currentTarget.blur(); else if (event.key === "Escape") {setText(null); event.currentTarget.blur();}}} />;
}

export function ValuesBar({cross, order, query, flagged, onOrder, onQuery, onDate, onFlag}: {
  cross: CrossSection | null; order: "top" | "bottom" | "missing"; query: string; flagged: number | null;
  onOrder(order: "top" | "bottom" | "missing"): void; onQuery(query: string): void; onDate(date: number): void; onFlag(): void;
}) {
  if (cross === null) return null;
  const missing = cross.rows.filter(row => row.missing).length;
  const flag = cross.rows.find(row => row.flag);
  return <div className="fx-vtop">
    <div className="fx-vbar">
      <span className="fx-datenav"><button type="button" aria-label={t("Day before")} disabled={cross.previous === null} onClick={() => cross.previous !== null && onDate(cross.previous)}><TxIcon name="arrow-left" /></button>
        <DayField date={cross.date} onDate={onDate} /><button type="button" aria-label={t("Day after")} disabled={cross.next === null} onClick={() => cross.next !== null && onDate(cross.next)}><TxIcon name="arrow-right" /></button></span>
      <span className="fx-t3 fx-cnt2">{t.rich("{members} members, {count} with a value, #{run}", { members: <b className="fx-num">{cross.members}</b>, count: <b className="fx-num">{cross.count}</b>, run: cross.run })}</span>
      {!flag && flagged !== null && <button type="button" className="fx-chip" data-tone="watch" onClick={onFlag}><span className="fx-dot" />{flagged === 1 ? t("1 flagged price") : t("{flagged} flagged prices", { flagged })}</button>}
      <span className="tx-spacer" />
      <span className="fx-small"><TxSegmented label={t.c("sorting", "Order")} value={order} onChange={onOrder} options={[{value: "top", label: t("Highest")}, {value: "bottom", label: t("Lowest")}, {value: "missing", label: t("Missing {missing}", { missing })}]} /></span>
      <span className="fx-srch"><TxIcon name="search" /><input value={query} placeholder={t("Name")} aria-label={t("Find a name")} onChange={event => onQuery(event.target.value)} /></span>
    </div>
    {flag && <div className="fx-note"><span className="fx-dot" /><span><b>{flag.ticker}</b> {flag.flag}</span></div>}
  </div>;
}

const TITLES: Record<string, string> = {"$close": "$close"};

export function ValuesTable({cross, order, query, picked, onPick}: {
  cross: CrossSection | null; order: "top" | "bottom" | "missing"; query: string; picked: string | null; onPick(ticker: string): void;
}) {
  const ref = useRef<HTMLTableRowElement>(null);
  useEffect(() => {ref.current?.scrollIntoView({block: "center"});}, [picked, cross?.date]);
  const rows = useMemo(() => {
    if (cross === null) return [];
    const have = cross.rows.filter(row => !row.missing), missing = cross.rows.filter(row => row.missing);
    let list = order === "missing" ? missing : have.slice().sort((a, b) => order === "top" ? (a.rank ?? 0) - (b.rank ?? 0) : (b.rank ?? 0) - (a.rank ?? 0));
    if (query.trim()) list = list.filter(row => row.ticker.toLowerCase().startsWith(query.trim().toLowerCase()));
    return list;
  }, [cross, order, query]);
  if (cross === null) return <div className="fx-empty">{t("No values yet")}</div>;
  const inputs = cross.inputNames;
  const number = (value: number | null | undefined) => value == null ? "" : Math.abs(value) >= 1000 ? nf(value, 0) : Math.abs(value) >= 1 ? nf(value, 2) : nf(value, 4);
  if (order === "missing") return <table className="fx-g"><thead><tr><th className="l">{t("Name")}</th><th className="l">{t("Sector")}</th><th className="l">{t("Why no value")}</th>{inputs.map(name => <th key={name} title={TITLES[name] ?? name}>{name}</th>)}</tr></thead>
    <tbody>{rows.map(row => <tr key={row.ticker} data-unmapped={row.unmapped ? "" : undefined}><td className="l fx-tk">{row.ticker}</td><td className="l fx-sec">{row.sector ?? ""}</td><td className="l fx-t2">{row.missing}</td>
      {inputs.map(name => <td key={name} className="fx-t2">{number(row.inputs[name])}</td>)}</tr>)}</tbody></table>;
  return <table className="fx-g"><thead><tr><th style={{width: 44}}>{t("Rank")}</th><th className="l">{t("Name")}</th><th className="l">{t("Sector")}</th>
    {inputs.map(name => <th key={name} title={name}>{name}</th>)}<th title={t("The value before preprocessing")}>{t("Raw")}</th><th title={t("After the run's extremes, scale and neutralize")}>{t("After")}</th>
    <th>{t("Group")}</th><th title={t("Return over the next {holding} days", { holding: cross.holding })}>{t("Next {holding}D", { holding: cross.holding })}</th></tr></thead>
    <tbody>{rows.map(row => <tr key={row.ticker} ref={row.ticker === picked ? ref : undefined} data-flag={row.flag ? "" : undefined} data-pick={row.ticker === picked ? "" : undefined} title={row.flag} onClick={() => onPick(row.ticker)}>
      <td className="fx-t3">{row.rank ?? ""}</td><td className="l fx-tk">{row.ticker}</td><td className="l fx-sec">{row.sector ?? ""}</td>
      {inputs.map(name => <td key={name} className={row.flag ? "fx-attn" : "fx-t2"}>{number(row.inputs[name])}</td>)}
      <td className="fx-t2">{sgn(row.raw, Math.abs(row.raw ?? 0) >= 1 ? 2 : 4)}</td><td className={tone(row.after)}>{sgn(row.after, 2)}</td><td className="fx-t2">{row.group ? `Q${row.group}` : ""}</td>
      <td className={tone(row.next)}>{row.next == null ? "" : pc(row.next, 1)}</td></tr>)}</tbody></table>;
}

// ---------------------------------------------------------------- similar in the library
export function SimilarView({items: all, run, holding}: {items: SimilarFactor[] | null; run: FactorRun | null; holding: number}) {
  if (run === null) return <TxEmpty title={t("Similar factors show after a run")} />;
  if (all === null) return <div className="fx-pad"><TxSkeleton width="60%" /></div>;
  const items = all.filter(item => !item.style);   // the built-in styles are on Results' Style page, not in the library
  if (!items.length) return <TxEmpty title={t("No library factor shares days and names with #{number}", { number: run.number })} />;
  const bar = (value: number) => <span className="fx-cbar"><span className="fx-z" /><i style={{left: `${50 + Math.min(0, value) * 50}%`, width: `${Math.abs(value) * 50}%`}} /><b style={{left: "85%"}} /><b style={{left: "15%"}} /></span>;
  return <div className="fx-pad fx-sim"><table className="fx-g"><thead><tr><th className="l">{t("Factor")}</th><th className="l">{t("Version")}</th><th className="l" colSpan={2} title={t("Mean daily rank correlation")}>{t("Correlation with #{number}", { number: run.number })}</th>
    <th title={t("Share of names in both top groups, median day")}>{t("Top group overlap")}</th><th>{t("Rank IC, {holding}D", { holding })}</th><th className="l">{t("Used by")}</th></tr></thead>
    <tbody>{items.map(item => <tr key={item.name}><td className="l">{item.name}</td><td className="l fx-t2">{typeof item.version === "number" ? `v${item.version}` : item.version}</td>
      <td className="l">{bar(item.rho)}</td><td className={Math.abs(item.rho) >= .7 ? "fx-watch" : undefined}>{sgn(item.rho, 2)}</td><td>{pct(item.topOverlap, 0)}</td>
      <td className={tone(item.rankIc)}>{sgn(item.rankIc, 4)}</td><td className="l fx-t2">{item.usedBy.length ? item.usedBy.join(", ") : <span className="fx-t4">—</span>}</td></tr>)}</tbody></table></div>;
}

