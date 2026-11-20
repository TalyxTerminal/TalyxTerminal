/* One open item (library.js careHead, careHtml): its state and what to do about it on top, why under that, then Overview, Values, Production log and Versions. */
import { useEffect, useMemo, useRef, useState } from "react";
import { TxButton, TxIcon, TxIconButton, TxMenu, TxSwitch, TxSkeleton, useTxToast, type TxMenuItem } from "@talyx/sdk";
import { md, mdy, sgn } from "./format";
import { useEvent } from "../objects";
import type { ModuleContext } from "@talyx/sdk";
import { REPUBLISH, type ItemDetail, type ItemVersion, type LibraryApi, type UserRow } from "./api";
import { IssueBlock } from "./IssueBlock";
import { KIND_LABEL } from "./LibraryTable";
import { ItemOverview } from "./ItemOverview";
import { ItemValues } from "./ItemValues";
import { ItemLog } from "./ItemLog";
import { ItemVersions, verLabel } from "./ItemVersions";
import { StopSheet } from "./StopSheet";
import { ArchiveSheet } from "./ArchiveSheet";
import { useWantedAuto } from "./wanted";

export type TabId = "overview" | "values" | "log" | "versions";

export interface Tab { id: TabId; label: string; count?: number }

export function tabsFor({kind, oneOff, versions}: {kind: string; oneOff: boolean; versions: number}): Tab[] {
  return [{id: "overview", label: "Overview"}, {id: "values", label: kind === "allocation" ? "Weights" : "Values"}, ...(oneOff || kind === "data" ? [] : [{id: "log" as const, label: "Production log"}]), {id: "versions", label: "Versions", count: versions}];
}

/** What "More" holds: Frozen code and Open in a notebook (a new one with this version read in its first cell); the rest is on the head. */
export function moreItems(version: {state: string; kind: string}): TxMenuItem[] {
  return [...(version.kind === "data" ? [] : [{id: "code", label: "Frozen code", icon: "code" as const}]), {id: "notebook", label: "Open in a notebook", icon: "external" as const}] as TxMenuItem[];
}
/** Why Archive cannot be pressed now ("" when it can): only a stopped version nobody uses is archived. */
export const archiveBlock = (version: {state: string; version: number; users: unknown[]}) => version.state === "archived" ? "Already archived"
  : version.state !== "stopped" ? `Stop v${version.version} first; a stopped version nobody uses can be archived` : version.users.length ? "Still in use" : "";

export interface ItemHandlers {
  onClose(): void; onFill(): void; filled: boolean; onTab(tab: TabId): void; onVersion(versionId: string): void; onCode(file?: string, line?: number, error?: string): void; codeOpen: boolean;
  onOpenObject(id: string): void; onOpenVersion(versionId: string): void; onOpenRun(objectId: string, runId: string): void; onSelectItem(itemId: string): void;
}

const buildOptions = (kind: string, version: number): {kind: "model" | "strategy" | "allocation"; label: string}[] =>
  kind === "factor" ? [{kind: "model", label: `New model with v${version} as an input`}, {kind: "strategy", label: `New strategy that reads v${version}`}]
  : kind === "model" ? [{kind: "strategy", label: `New strategy on v${version}'s predictions`}] : kind === "allocation" ? [{kind: "strategy", label: `New strategy that rebalances to v${version}`}] : [{kind: "strategy", label: "New strategy that reads it"}];

export function LibraryItem({ctx, api, itemId, versionId, tab, refresh, handlers}: {ctx: ModuleContext; api: LibraryApi; itemId: string; versionId: string | undefined; tab: TabId; refresh: number; handlers: ItemHandlers}) {
  const toast = useTxToast();
  const [fetched, setDetail] = useState<ItemDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [metric, setMetric] = useState<"ic" | "ls">("ic");
  const [span, setSpan] = useState<"since" | "all">("since");
  const [period, setPeriod] = useState<number | null>(null);
  const [valuesDate, setValuesDate] = useState<number | null>(null);
  const [sheet, setSheet] = useState<"stop" | "archive" | null>(null);
  const [tick, setTick] = useState(0);
  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => {setPeriod(null); setValuesDate(null);}, [itemId, versionId]);
  useEffect(() => {
    let live = true;
    api.item(itemId, {...(versionId ? {versionId} : {}), metric, span}).then(value => {if (live) {setDetail(value); setError(null);}}, reason => {if (live) setError(reason instanceof Error ? reason.message : String(reason));});
    return () => {live = false;};
  }, [itemId, versionId, metric, span, refresh, tick]);
  useEvent(ctx, "quant.library_changed", () => setTick(value => value + 1));
  const wanted = useWantedAuto(fetched?.versions ?? []);
  const detail = useMemo(() => fetched === null ? null : {...fetched, versions: wanted.apply(fetched.versions)}, [fetched, wanted.apply]);
  const version = useMemo<ItemVersion | null>(() => detail === null ? null : detail.versions.find(item => item.versionId === detail.versionId) ?? detail.versions[0] ?? null, [detail]);
  if (error !== null && detail === null) return <div className="lb-calm" role="alert">{error}</div>;
  if (detail === null || version === null) return <div className="lb-skel" aria-busy="true"><TxSkeleton width="30%" /><TxSkeleton width="55%" /><TxSkeleton width="70%" /><TxSkeleton className="lb-skel-plot" /></div>;
  const row = detail.summary, kind = row.kind;
  const failed = (action: Promise<unknown>, said: string) => action.then(() => {toast({title: said}); setTick(value => value + 1);}, reason => toast({title: reason instanceof Error ? reason.message : String(reason), tone: "failed"}));
  // Retry is offered only where it can work: a producing version with auto-update on, whose failure a retry can mend (one published before a change says to publish again).
  const retry = version.state === "producing" && version.auto && version.failure?.code !== REPUBLISH ? () => void failed(api.retry(version.versionId), `Retrying ${row.name} v${version.version} now`) : null;
  const resume = () => void failed(api.resume(version.versionId), `Producing ${row.name} v${version.version} again; the periods it missed are made now`);
  const create = (what: "model" | "strategy" | "allocation") => void api.createFrom(version.versionId, what).then(created => {toast({title: `Created ${created.name}`, detail: `${row.name} pinned at v${version.version}`}); handlers.onOpenObject(created.id);},
    reason => toast({title: reason instanceof Error ? reason.message : String(reason), tone: "failed"}));
  const notebook = () => void api.openInNotebook(row.name, version.version, kind).then(opened => handlers.onOpenObject(opened.notebookId), reason => toast({title: reason instanceof Error ? reason.message : String(reason), tone: "failed"}));
  const act = (id: string) => {if (id === "notebook") notebook(); else if (id === "stop") setSheet("stop"); else if (id === "resume") resume(); else if (id === "archive") setSheet("archive"); else if (id === "code") handlers.onCode();};
  const openUser = (user: UserRow) => {if (user.objectId) handlers.onOpenObject(user.objectId); else if (user.libraryVersionId) handlers.onOpenVersion(user.libraryVersionId);};
  const issue = detail.issue !== null && row.shownVersionId === version.versionId ? detail.issue : null;
  const tabs = tabsFor({kind, oneOff: detail.oneOff, versions: detail.versions.length});
  const current: TabId = tabs.some(item => item.id === tab) ? tab : "overview";
  const runNumber = version.origin?.runNumber;
  const need = row.collectingNeed;
  const archiveUsers = version.users;
  const fixes = issue !== null && (issue.kind === "fail" || issue.kind === "miss");
  return <div className="lb-care">
    <div className="lb-chd">
      <div className="lb-t1"><h3 title={row.name}>{row.name}</h3><span className="lb-sp" />
        {fixes && retry && <TxButton size="sm" variant="primary" onClick={retry}><TxIcon name="refresh" />Retry now</TxButton>}
        {issue?.kind === "up" && row.inputs[0] && <TxButton size="sm" variant="primary" onClick={() => handlers.onOpenVersion(row.inputs[0].versionId)}>Go to the input</TxButton>}
        {kind !== "data" && version.state === "producing" && <TxButton size="sm" onClick={() => setSheet("stop")}><TxIcon name="stop" />{`Stop v${version.version}`}</TxButton>}
        {kind !== "data" && version.state === "stopped" && <TxButton size="sm" onClick={resume}><TxIcon name="refresh" />{`Produce v${version.version} again`}</TxButton>}
        {kind !== "data" && version.state === "stopped" && <TxButton size="sm" onClick={() => setSheet("archive")}><TxIcon name="archive" />Archive</TxButton>}
        <TxMenu label="New from this" align="end" items={buildOptions(kind, version.version).map(option => ({id: option.kind, label: option.label, onSelect: () => create(option.kind)}))} trigger={<TxButton size="sm"><TxIcon name="plus" />New from this</TxButton>} />
        {kind !== "data" && version.origin?.objectId && version.origin.runId && <TxButton size="sm" title="Back to the run it was published from" onClick={() => handlers.onOpenRun(version.origin!.objectId!, version.origin!.runId!)}>{`Run #${runNumber ?? ""}`}</TxButton>}
        <TxMenu label="More" align="end" items={moreItems({state: version.state, kind}).map(item => ({...item, onSelect: () => act(item.id)}))} trigger={<TxIconButton label="More" icon="more" />} />
      </div>
      <div className="lb-t2r">{detail.versions.map(item => <button type="button" key={item.versionId} className="lb-vchip" data-on={item.versionId === version.versionId} onClick={() => handlers.onVersion(item.versionId)} title={`Published ${mdy(item.publishedAt)}${item.origin?.runNumber ? ` from #${item.origin.runNumber}` : ""}`}>
          <b>{`v${item.version}`}</b>{verLabel(item)}{item.users.length ? ` · ${item.users.length} using` : ""}</button>)}
        {kind !== "data" && version.state === "producing" && <span className="lb-autoc">Auto-update <TxSwitch label="Auto-update" hideLabel checked={version.auto} onCheckedChange={on => void failed(wanted.ask(version.versionId, on, () => api.setAuto(version.versionId, on)), on ? `Auto-update on for ${row.name} v${version.version}` : `Auto-update off for ${row.name} v${version.version}`)} /></span>}
        <span className="lb-meta">{`${KIND_LABEL[kind]} · ${row.freq.toLowerCase()}${row.universe ? ` · ${row.universe}` : ""}`}</span></div>
    </div>
    {issue ? <IssueBlock issue={issue} onLog={() => handlers.onTab("log")} onOpenCode={(file, line) => handlers.onCode(file, line, issue.rows.find(item => item.label === "Error")?.text)} />
      : version.state === "stopped" ? <div className="lb-iss" data-tone="calm"><div className="lb-iss-head"><b>{`Stopped${version.stoppedAt ? ` ${mdy(version.stoppedAt)}` : ""} by you`}</b></div>
        <div className="lb-why"><span>Values</span><span>{`${version.lastDatetime ? `to ${mdy(version.lastDatetime)}` : ""}; nothing after`}</span><span>Using it</span><span>{version.users.length ? version.users.map(user => `${user.name} ${user.kind.toLowerCase()}`).join(", ") : "nobody, so it can be archived"}</span></div></div>
      : version.state === "producing" && !version.auto ? <div className="lb-iss" data-tone="calm"><div className="lb-iss-head"><b>{(version.after?.settled ?? 0) + (version.after?.pending ?? 0) > 0 ? "Auto-update off" : "Auto-update off since publication"}</b></div>
        <div className="lb-why"><span>Values</span><span>{`to ${mdy(version.lastDatetime ?? version.publishedAt)}; ${(version.after?.settled ?? 0) + (version.after?.pending ?? 0) > 0 ? "nothing after" : "no after-publication record"}`}</span>
          <span>Turning it on</span><span>{`fills the periods after ${md(version.lastDatetime ?? version.publishedAt)} as late, then a value every close`}</span></div></div> : null}
    <div className="lb-ctabs" role="tablist">{tabs.map(item => <button type="button" role="tab" key={item.id} className="lb-tab" data-selected={item.id === current} onClick={() => handlers.onTab(item.id)}>{item.label}{item.count ? <i>{item.count}</i> : null}</button>)}
      <span className="lb-sp" /><TxIconButton label={handlers.filled ? "Show the list" : "Fill with this item"} icon={handlers.filled ? "restore" : "maximize"} onClick={handlers.onFill} /><TxIconButton label="Close (Esc)" icon="close" onClick={handlers.onClose} /></div>
    <div className="lb-scroll" ref={scroller}>
      {current === "overview" && <ItemOverview detail={detail} version={version} need={need} metric={metric} span={span} period={period} handlers={{onMetric: setMetric, onSpan: setSpan, onPeriod: setPeriod, onValues: label => {setValuesDate(label); handlers.onTab("values");},
        onLog: () => handlers.onTab("log"), onRetry: retry, onOpenUser: openUser, onOpenVersion: handlers.onOpenVersion}} />}
      {current === "values" && (version.state === "archived" ? <div className="lb-calm">{`v${version.version} was archived; its values were dropped.`}</div> : <ItemValues api={api} version={version} kind={kind} date={valuesDate} onDate={setValuesDate} onLog={() => handlers.onTab("log")} />)}
      {current === "log" && <ItemLog api={api} version={version} auto={version.auto} onRetry={retry} onOpenCode={(file, line) => handlers.onCode(file, line)} />}
      {current === "versions" && <ItemVersions api={api} detail={detail} current={version} need={need} onCode={() => handlers.onCode()} onSelect={handlers.onVersion} onAuto={(item, on) => void failed(wanted.ask(item.versionId, on, () => api.setAuto(item.versionId, on)), `Auto-update ${on ? "on" : "off"} for ${row.name} v${item.version}`)} onOpenUser={openUser} />}
      <div style={{height: 16}} />
    </div>
    <StopSheet api={api} open={sheet === "stop"} name={row.name} versionId={version.versionId} version={version.version} onClose={() => setSheet(null)} onOpenUser={user => {setSheet(null); openUser(user);}}
      onDone={preview => {setSheet(null); toast({title: `Stopped ${row.name} v${version.version}`, detail: preview.users.length ? `${preview.users.length} ${preview.users.length === 1 ? "user keeps" : "users keep"} the values` : "nobody used it"}); setTick(value => value + 1);}} />
    <ArchiveSheet api={api} open={sheet === "archive"} name={row.name} versionId={version.versionId} version={version.version} users={archiveUsers} block={version.state !== "stopped" ? archiveBlock({...version, users: []}) : ""} stoppedAt={version.stoppedAt} run={runNumber ?? null}
      onOpenUser={user => {setSheet(null); openUser(user);}}
      figures={`${version.measure ?? ""} ${version.pub?.value == null ? "" : sgn(version.pub.value, 4)}`.trim()} models={kind === "model"} onClose={() => setSheet(null)} onDone={() => {setSheet(null); toast({title: `Archived ${row.name} v${version.version}`}); setTick(value => value + 1);}} />
  </div>;
}
