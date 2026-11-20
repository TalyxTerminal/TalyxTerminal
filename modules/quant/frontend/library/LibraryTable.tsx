/* The library's table (output/quant-design/library.js tableHtml): everything published, problems first, the state column the most visible; beside an open item it shrinks to state, name and the last 20. */
import { TxPeriods, TxSwitch, TxTable, TxTd, TxTh, type TxPeriodState } from "@talyx/sdk";
import { md, nf, pct, sgn, tone } from "./format";
import type { LibraryRow } from "./api";

export type { LibraryRow } from "./api";

export const KIND_LABEL: Record<LibraryRow["kind"], string> = {factor: "Factor", model: "Model", allocation: "Allocation", data: "One-off data"};
const FIX = new Set(["fail", "miss", "up"]);
const WATCH = new Set(["weak", "drift", "late"]);
const QUIET = new Set(["ok", "first", "stopped", "auto_off", "one_off"]);

/** A row's state word: what needs handling now is orange, what to watch amber, the rest quiet; a count of periods in a row follows. */
export function stateWord(row: LibraryRow): {tone: "fix" | "watch" | "ok" | "muted"; text: string} {
  const tone = FIX.has(row.state) ? "fix" : WATCH.has(row.state) ? "watch" : row.state === "ok" ? "ok" : "muted";
  return {tone, text: row.stateLabel + (row.stateCount > 1 ? ` ×${row.stateCount}` : "")};
}
const period = (result: string): TxPeriodState => result === "ok" ? "ok" : result === "late" ? "late" : "miss";
export const Strip = ({row}: {row: LibraryRow}) => <TxPeriods items={row.last20.map(item => period(item.result))} titles={row.last20.map(item => `${md(item.period)}: ${item.result === "ok" ? "on time" : item.result === "late" ? "late" : item.result === "up" ? "upstream missing" : item.result === "miss" ? "missing" : "failed"}${item.why ? `. ${item.why}` : ""}`)} />;

/** A main measure as the list writes it: an allocation's volatility as a share, unsigned and uncoloured; a signal's Rank IC signed. */
export const figure = (row: {kind: string}, value: number | null | undefined) => value == null ? "" : row.kind === "allocation" ? pct(value, 1) : sgn(value, 4);
export const figureTone = (row: {kind: string}, value: number | null | undefined) => row.kind === "allocation" ? "" : tone(value) ?? "";
/** After publication (library.js mainAfter): a dash with no values since, "collecting" until the settled periods make 60 trading days, then the figure. */
export function AfterCell({row}: {row: LibraryRow}) {
  if (row.kind === "data" || row.oneOff) return null;
  if (row.after === null && row.settled === 0 && row.pending === 0) return <span className="lb-t4" title="No values after publication">—</span>;
  if (row.collecting || (row.after === null && row.kind !== "allocation")) {
    return <span className="lb-collect" title={`After-publication figures need ${row.collectingNeed} trading days settled; ${row.settledDays} so far`}>collecting</span>;
  }
  return <>{figure(row, row.after)}</>;
}

export function LibraryTable({rows, selected, compact = false, onSelect, onAuto}: {rows: LibraryRow[]; selected: string | null; compact?: boolean; onSelect(itemId: string): void; onAuto(row: LibraryRow, on: boolean): void}) {
  if (compact) return <TxTable className="lb-table" data-compact="">
    <colgroup><col style={{width: 136}} /><col /><col style={{width: 90}} /></colgroup>
    <thead><tr><TxTh>State</TxTh><TxTh>Name</TxTh><TxTh>Last 20</TxTh></tr></thead>
    <tbody>{rows.map(row => {
      const word = stateWord(row);
      return <tr key={row.itemId} className="lb-click" data-quiet={QUIET.has(row.state) ? "" : undefined} data-selected={row.itemId === selected} onClick={() => onSelect(row.itemId)}>
        <TxTd><span className="lb-state" data-tone={word.tone}>{word.text}</span></TxTd><TxTd className="lb-name" title={`${row.name} · ${KIND_LABEL[row.kind]}`}><b>{row.name}</b></TxTd>
        <TxTd>{row.oneOff ? "" : <Strip row={row} />}</TxTd></tr>;
    })}</tbody></TxTable>;
  return <TxTable className="lb-table">
    <colgroup><col style={{width: 172}} /><col /><col style={{width: 104}} /><col style={{width: 84}} /><col style={{width: 70}} /><col style={{width: 64}} /><col style={{width: 100}} /><col style={{width: 104}} /><col style={{width: 80}} /><col style={{width: 80}} /><col style={{width: 66}} /><col style={{width: 66}} /><col style={{width: 56}} /><col style={{width: 52}} /></colgroup>
    <thead><tr><TxTh>State</TxTh><TxTh>Name</TxTh><TxTh>Kind</TxTh><TxTh title="Versions that produce a new value each period">Producing</TxTh><TxTh title="Latest period with a value">Latest</TxTh><TxTh align="right" title="Names with a value on the latest period">Names</TxTh><TxTh title="One cell a period: on time, late, failed or missing">Last 20</TxTh>
      <TxTh>Main measure</TxTh><TxTh align="right" title="From the run it was published from">Published</TxTh><TxTh align="right" title="Settled periods after publication only">After</TxTh><TxTh align="right" title="Periods after publication whose outcome is known">Settled</TxTh>
      <TxTh align="right" title="Periods still waiting for their forward return">Pending</TxTh><TxTh align="right" title="Objects on any version">Users</TxTh><TxTh align="right" title="Auto-update: a new value every period">Auto</TxTh></tr></thead>
    <tbody>{rows.map(row => {
      const word = stateWord(row), quiet = QUIET.has(row.state);
      const producing = row.versions.filter(version => version.state === "producing" || version.state === "fixed");
      const shown = row.versions.find(version => version.versionId === row.shownVersionId);
      const after = !row.oneOff && row.kind !== "data";
      return [<tr key={row.itemId} className="lb-click" data-quiet={quiet ? "" : undefined} data-selected={row.itemId === selected} onClick={() => onSelect(row.itemId)}>
          <TxTd><span className="lb-state" data-tone={word.tone}>{word.text}</span></TxTd><TxTd className="lb-name" title={row.name}><b>{row.name}</b></TxTd><TxTd className="lb-kind">{KIND_LABEL[row.kind]}</TxTd>
          <TxTd className="lb-num">{producing.map(version => `v${version.version}`).join(", ") || (row.oneOff ? "v1" : <span className="lb-t4">none</span>)}</TxTd>
          <TxTd className="lb-t2">{row.latestPeriod === null ? "" : md(row.latestPeriod)}</TxTd><TxTd align="right" className="lb-num">{row.latestNames === null ? "" : nf(row.latestNames, 0)}</TxTd>
          <TxTd>{row.oneOff ? "" : row.last20.length === 0 ? <span className="lb-t4">0</span> : <Strip row={row} />}</TxTd>
          <TxTd className="lb-kind">{row.measure ?? ""}</TxTd><TxTd align="right" className={`lb-num ${figureTone(row, row.published)}`}>{figure(row, row.published)}</TxTd>
          <TxTd align="right" className={`lb-num ${figureTone(row, row.after)}`}><AfterCell row={row} /></TxTd>
          <TxTd align="right" className="lb-num">{after ? nf(row.settled, 0) : ""}</TxTd><TxTd align="right" className="lb-num lb-t3">{after && row.kind !== "allocation" ? row.pending : ""}</TxTd>
          <TxTd align="right" className="lb-num" title={row.usersList.map(user => `${user.name} (${user.kind}, v${user.version})`).join("\n") || "Nobody"}>{row.users || <span className="lb-t4">0</span>}</TxTd>
          <TxTd align="right" onClick={event => event.stopPropagation()}>{row.oneOff || shown?.state !== "producing" ? "" : <TxSwitch label="Auto-update" hideLabel checked={shown.auto}
            onCheckedChange={on => onAuto(row, on)} />}</TxTd></tr>];
    })}</tbody></TxTable>;
}
