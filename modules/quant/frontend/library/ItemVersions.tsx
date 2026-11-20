/* The Versions tab: the list, a comparison of two (published figures, how alike the two cross-sections are, code and settings that differ), and who is on which (library.js versionsTable, compareHtml). */
import { useEffect, useState } from "react";
import { TxFailure, TxSkeleton, TxSwitch, TxTable, TxTd, TxTh } from "@talyx/sdk";
import { md, mdy, nf, pc, pp, sgn } from "./format";
import type { Compare, CompareSide, ItemDetail, ItemVersion, LibraryApi, UserRow } from "./api";
import { UsersBlock } from "./ItemOverview";
import { figure as measure, figureTone } from "./LibraryTable";

/** What a version is doing, as its chip says it (library.js verLabel). */
export const verLabel = (item: {state: string; auto: boolean; archivedAt: number | null; stoppedAt: number | null}) => item.state === "archived" ? `archived${item.archivedAt ? ` ${md(item.archivedAt)}` : ""}`
  : item.state === "stopped" ? `stopped${item.stoppedAt ? ` ${md(item.stoppedAt)}` : ""}` : item.state === "fixed" ? "one-off" : item.auto ? "producing" : "auto off";
/** A side's settled after-publication figure, as the list's After column writes it: nothing yet, collecting until enough periods settled, else the figure. */
function afterOf(side: {after: CompareSide["after"]}, need: number, kind: string) {
  if (side.after === null || (side.after.settled === 0 && side.after.pending === 0)) return <span className="lb-t4" title="No values after publication">—</span>;
  if ((side.after.settledDays ?? 0) < need || side.after.value == null) return <span className="lb-collect" title={`After-publication figures need ${need} trading days settled; ${side.after.settledDays ?? 0} so far`}>collecting</span>;
  return <span className={figureTone({kind}, side.after.value)}>{measure({kind}, side.after.value)}</span>;
}

export function CompareTable({compare, need, kind, onCode}: {compare: Compare; need: number; kind: string; onCode?(): void}) {
  const figure = (side: CompareSide | null | undefined, field: "value" | "t") => side?.published?.[field] == null ? "" : field === "t" ? nf(side.published[field] as number, 2) : measure({kind}, side.published.value as number);
  const [older, newer] = compare.a.version < compare.b.version ? [compare.a, compare.b] : [compare.b, compare.a];
  const overlap = compare.overlap, [first, second] = compare.a.version < compare.b.version ? [overlap?.namesA, overlap?.namesB] : [overlap?.namesB, overlap?.namesA];
  const onlyOlder = overlap ? (compare.a.version < compare.b.version ? overlap.onlyA : overlap.onlyB) : 0, onlyNewer = overlap ? (compare.a.version < compare.b.version ? overlap.onlyB : overlap.onlyA) : 0;
  const run = (side: CompareSide) => side.origin?.runNumber ? ` #${side.origin.runNumber}` : "";
  const Row = ({label, a, b, d}: {label: string; a?: React.ReactNode; b?: React.ReactNode; d?: React.ReactNode}) => <tr><TxTd>{label}</TxTd><TxTd align="right">{a}</TxTd><TxTd align="right">{b}</TxTd><TxTd align="right">{d}</TxTd></tr>;
  const ls = (side: CompareSide) => side.published?.ls;
  return <div className="lb-ov">
    <div><TxTable className="lb-pt"><thead><tr><TxTh>Published figures</TxTh><TxTh align="right">{`v${older.version}${run(older)}`}</TxTh><TxTh align="right">{`v${newer.version}${run(newer)}`}</TxTh><TxTh align="right">Change</TxTh></tr></thead>
      <tbody><Row label={older.measure ?? "Rank IC"} a={<span className={figureTone({kind}, older.published?.value)}>{figure(older, "value")}</span>} b={<span className={figureTone({kind}, newer.published?.value)}>{figure(newer, "value")}</span>}
        d={older.published?.value != null && newer.published?.value != null ? (kind === "allocation" ? pp(newer.published.value - older.published.value, 1) : sgn(newer.published.value - older.published.value, 4)) : ""} />
        <Row label="t" a={figure(older, "t")} b={figure(newer, "t")} d={older.published?.t != null && newer.published?.t != null ? sgn(newer.published.t - older.published.t, 2) : ""} />
        {(ls(older) != null || ls(newer) != null) && <Row label="Long–short, net, a year" a={ls(older) == null ? "" : pc(ls(older)!, 1)} b={ls(newer) == null ? "" : pc(ls(newer)!, 1)} d={ls(older) != null && ls(newer) != null ? pp(ls(newer)! - ls(older)!, 1) : ""} />}
        <Row label="After, settled" a={afterOf(older, need, kind)} b={afterOf(newer, need, kind)} /></tbody></TxTable></div>
    {overlap && <div><TxTable className="lb-pt"><thead><tr><TxTh>{`Where both produce, ${md(overlap.from)} – ${md(overlap.to)}`}</TxTh><TxTh /></tr></thead>
      <tbody><tr><TxTd>Rank correlation of the two cross-sections, mean a day</TxTd><TxTd align="right" style={{width: 90}}>{overlap.rankCorrelation == null ? "" : nf(overlap.rankCorrelation, 2)}</TxTd></tr>
        <tr><TxTd>Names a day</TxTd><TxTd align="right">{`${nf(first ?? 0, 0)} → ${nf(second ?? 0, 0)}`}</TxTd></tr>
        <tr><TxTd>{`Names only in v${older.version}`}</TxTd><TxTd align="right" className={onlyOlder > 0 ? "lb-watch" : ""}>{onlyOlder}</TxTd></tr>
        <tr><TxTd>{`Names only in v${newer.version}`}</TxTd><TxTd align="right">{onlyNewer}</TxTd></tr></tbody></TxTable></div>}
    <div className="lb-span2"><div className="lb-sub"><b title="Frozen with each version">Code and settings</b><span className="lb-sp" />{onCode && <button type="button" className="lb-link" onClick={onCode}>{`Open v${newer.version} frozen code`}</button>}</div>
      {compare.code.length === 0 && compare.settings.length === 0 ? <div className="lb-t3 lb-pad-s">No difference</div> : <div className="lb-diff">
        {compare.code.map(item => [<div key={item.file} data-kind="h">{`@@ ${item.file} @@ ${item.status}`}</div>, ...item.diff.split("\n").filter(Boolean).map((line, index) => <div key={`${item.file}-${index}`} data-kind={line.startsWith("+") ? "a" : line.startsWith("-") ? "r" : ""}>{line}</div>)])}
        {compare.settings.length > 0 && <div data-kind="h">@@ settings @@</div>}
        {compare.settings.map(item => [<div key={`${item.what}-r`} data-kind="r">{`− ${item.what}: ${item.from === null ? "—" : String(item.from)}`}</div>, <div key={`${item.what}-a`} data-kind="a">{`+ ${item.what}: ${String(item.to)}`}</div>])}</div>}</div>
  </div>;
}

export function ItemVersions({api, detail, current, need, onSelect, onAuto, onOpenUser, onCode}: {api: LibraryApi; detail: ItemDetail; current: ItemVersion; need: number; onSelect(versionId: string): void; onAuto(version: ItemVersion, on: boolean): void; onOpenUser(user: UserRow): void; onCode(): void}) {
  const comparable = detail.versions.filter(version => version.state !== "archived");
  const [other, setOther] = useState<string | null>(null);
  const [compare, setCompare] = useState<Compare | string | null>(null);   // a string: why the comparison could not be read
  const partner = comparable.find(version => version.versionId === other && version.versionId !== current.versionId) ?? comparable.find(version => version.versionId !== current.versionId) ?? null;
  useEffect(() => {
    setCompare(null);
    if (partner === null) return;
    void api.compare(current.versionId, partner.versionId).then(setCompare, reason => setCompare(reason instanceof Error ? reason.message : String(reason)));
  }, [current.versionId, partner?.versionId]);
  const signal = detail.summary.kind !== "data";
  return <div className="lb-pad lb-vtab">
    <TxTable className="lb-table"><colgroup><col style={{width: 40}} /><col style={{width: 130}} /><col /><col style={{width: 80}} /><col style={{width: 80}} /><col style={{width: 120}} /><col style={{width: 50}} /><col style={{width: 50}} /></colgroup>
      <thead><tr><TxTh>Ver.</TxTh><TxTh>Published</TxTh><TxTh>Settings</TxTh><TxTh align="right" title="At publication">{detail.summary.measure ?? "Published"}</TxTh><TxTh align="right" title="The same measure after publication, settled">After</TxTh><TxTh>State</TxTh><TxTh align="right">Auto</TxTh><TxTh align="right">Users</TxTh></tr></thead>
      <tbody>{detail.versions.map(version => <tr key={version.versionId} className="lb-click" data-selected={version.versionId === current.versionId} onClick={() => onSelect(version.versionId)}>
        <TxTd className="lb-num lb-t2">{`v${version.version}`}</TxTd><TxTd className="lb-t2">{mdy(version.publishedAt)}{version.origin?.runNumber ? <span className="lb-t3">{` #${version.origin.runNumber}`}</span> : null}</TxTd>
        <TxTd className="lb-t2" title={version.settingsText}>{version.settingsText}</TxTd><TxTd align="right" className={`lb-num ${figureTone(detail.summary, version.pub?.value)}`}>{measure(detail.summary, version.pub?.value)}</TxTd>
        <TxTd align="right" className="lb-num">{version.state === "archived" || version.state === "fixed" ? "" : afterOf(version, need, detail.summary.kind)}</TxTd>
        <TxTd><span className="lb-state" data-tone={version.state === "producing" && version.auto ? "ok" : undefined}>{verLabel(version)}</span></TxTd>
        <TxTd align="right" onClick={event => event.stopPropagation()}>{version.state === "producing" && signal ? <TxSwitch label="Auto-update" hideLabel checked={version.auto} onCheckedChange={on => onAuto(version, on)} /> : ""}</TxTd>
        <TxTd align="right" className="lb-num">{version.users.length || <span className="lb-t4">0</span>}</TxTd></tr>)}</tbody></TxTable>
    {comparable.length >= 2 && <>
      <div className="lb-sub lb-wrap"><b>{`Compare v${current.version} and v${partner?.version ?? ""}`}</b><span className="lb-t3">with</span>
        {comparable.filter(version => version.versionId !== current.versionId).map(version => <button type="button" key={version.versionId} className="lb-vchip" data-on={version.versionId === partner?.versionId} onClick={() => setOther(version.versionId)}><b>{`v${version.version}`}</b>{verLabel(version)}</button>)}</div>
      {typeof compare === "string" ? <TxFailure title="The comparison could not be read" message={compare} /> : compare ? <CompareTable compare={compare} need={need} kind={detail.summary.kind} onCode={onCode} /> : <div className="lb-pad-s"><TxSkeleton width="40%" /></div>}</>}
    <UsersBlock detail={detail} onOpenUser={onOpenUser} />
  </div>;
}
