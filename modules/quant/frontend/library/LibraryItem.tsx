/* One open item (library.js careHead, careHtml): its state and what to do about it on top, why under that, then Overview, Values, Production log and Versions. */
import { t } from "../i18n";
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
  return [{id: "overview", label: t("Overview")}, {id: "values", label: kind === "allocation" ? t("Weights") : t("Values")}, ...(oneOff || kind === "data" ? [] : [{id: "log" as const, label: t("Production log")}]), {id: "versions", label: t("Versions"), count: versions}];
}

/** What "More" holds: Frozen code and Open in a notebook (a new one with this version read in its first cell); the rest is on the head. */
export function moreItems(version: {state: string; kind: string}): TxMenuItem[] {
  return [...(version.kind === "data" ? [] : [{id: "code", label: t("Frozen code"), icon: "code" as const}]), {id: "notebook", label: t("Open in a notebook"), icon: "external" as const}] as TxMenuItem[];
}
/** Why Archive cannot be pressed now ("" when it can): only a stopped version nobody uses is archived. */
export const archiveBlock = (version: {state: string; version: number; users: unknown[]}) => version.state === "archived" ? t("Already archived")
  : version.state !== "stopped" ? t("Stop v{version} first; a stopped version nobody uses can be archived", { version: version.version }) : version.users.length ? t("Still in use") : "";

export interface ItemHandlers {
  onClose(): void; onFill(): void; filled: boolean; onTab(tab: TabId): void; onVersion(versionId: string): void; onCode(file?: string, line?: number, error?: string): void; codeOpen: boolean;
  onOpenObject(id: string): void; onOpenVersion(versionId: string): void; onOpenRun(objectId: string, runId: string): void; onSelectItem(itemId: string): void;
}

const buildOptions = (kind: string, version: number): {kind: "model" | "strategy" | "allocation"; label: string}[] =>
  kind === "factor" ? [{kind: "model", label: t("New model with v{version} as an input", { version })}, {kind: "strategy", label: t("New strategy that reads v{version}", { version })}]
  : kind === "model" ? [{kind: "strategy", label: t("New strategy on v{version}'s predictions", { version })}] : kind === "allocation" ? [{kind: "strategy", label: t("New strategy that rebalances to v{version}", { version })}] : [{kind: "strategy", label: t("New strategy that reads it")}];

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
  const retry = version.state === "producing" && version.auto && version.failure?.code !== REPUBLISH ? () => void failed(api.retry(version.versionId), t("Retrying {name} v{version} now", { name: row.name, version: version.version })) : null;
  const resume = () => void failed(api.resume(version.versionId), t("Producing {name} v{version} again; the periods it missed are made now", { name: row.name, version: version.version }));
  const create = (what: "model" | "strategy" | "allocation") => void api.createFrom(version.versionId, what).then(created => {toast({title: t("Created {name}", { name: created.name }), detail: t("{name} pinned at v{version}", { name: row.name, version: version.version })}); handlers.onOpenObject(created.id);},
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
        {fixes && retry && <TxButton size="sm" variant="primary" onClick={retry}><TxIcon name="refresh" />{t("Retry now")}</TxButton>}
        {issue?.kind === "up" && row.inputs[0] && <TxButton size="sm" variant="primary" onClick={() => handlers.onOpenVersion(row.inputs[0].versionId)}>{t("Go to the input")}</TxButton>}
        {kind !== "data" && version.state === "producing" && <TxButton size="sm" onClick={() => setSheet("stop")}><TxIcon name="stop" />{t("Stop v{version}", { version: version.version })}</TxButton>}
        {kind !== "data" && version.state === "stopped" && <TxButton size="sm" onClick={resume}><TxIcon name="refresh" />{t("Produce v{version} again", { version: version.version })}</TxButton>}
        {kind !== "data" && version.state === "stopped" && <TxButton size="sm" onClick={() => setSheet("archive")}><TxIcon name="archive" />{t("Archive")}</TxButton>}
        <TxMenu label={t("New from this")} align="end" items={buildOptions(kind, version.version).map(option => ({id: option.kind, label: option.label, onSelect: () => create(option.kind)}))} trigger={<TxButton size="sm"><TxIcon name="plus" />{t("New from this")}</TxButton>} />
        {kind !== "data" && version.origin?.objectId && version.origin.runId && <TxButton size="sm" title={t("Back to the run it was published from")} onClick={() => handlers.onOpenRun(version.origin!.objectId!, version.origin!.runId!)}>{t("Run #{runNumber}", { runNumber: runNumber ?? "" })}</TxButton>}
        <TxMenu label={t("More")} align="end" items={moreItems({state: version.state, kind}).map(item => ({...item, onSelect: () => act(item.id)}))} trigger={<TxIconButton label={t("More")} icon="more" />} />
      </div>
      <div className="lb-t2r">{detail.versions.map(item => <button type="button" key={item.versionId} className="lb-vchip" data-on={item.versionId === version.versionId} onClick={() => handlers.onVersion(item.versionId)} title={(item.origin?.runNumber ? t("Published {publishedAt} from #{runNumber}", { publishedAt: mdy(item.publishedAt), runNumber: item.origin.runNumber }) : t("Published {publishedAt}", { publishedAt: mdy(item.publishedAt) }))}>
          <b>{`v${item.version}`}</b>{verLabel(item)}{item.users.length ? t(" · {usersCount} using", { usersCount: item.users.length }) : ""}</button>)}
        {kind !== "data" && version.state === "producing" && <span className="lb-autoc">{t("Auto-update")} <TxSwitch label={t("Auto-update")} hideLabel checked={version.auto} onCheckedChange={on => void failed(wanted.ask(version.versionId, on, () => api.setAuto(version.versionId, on)), on ? t("Auto-update on for {name} v{version}", { name: row.name, version: version.version }) : t("Auto-update off for {name} v{version}", { name: row.name, version: version.version }))} /></span>}
        <span className="lb-meta">{`${KIND_LABEL[kind]} · ${row.freq.toLowerCase()}${row.universe ? ` · ${row.universe}` : ""}`}</span></div>
    </div>
    {issue ? <IssueBlock issue={issue} onLog={() => handlers.onTab("log")} onOpenCode={(file, line) => handlers.onCode(file, line, issue.rows.find(item => item.label === "Error")?.text)} />
      : version.state === "stopped" ? <div className="lb-iss" data-tone="calm"><div className="lb-iss-head"><b>{(version.stoppedAt ? t("Stopped {stoppedAt} by you", { stoppedAt: mdy(version.stoppedAt) }) : t("Stopped by you"))}</b></div>
        <div className="lb-why"><span>{t("Values")}</span><span>{(version.lastDatetime ? t("to {lastDatetime}; nothing after", { lastDatetime: mdy(version.lastDatetime) }) : t("; nothing after"))}</span><span>{t("Using it")}</span><span>{version.users.length ? version.users.map(user => `${user.name} ${user.kind.toLowerCase()}`).join(", ") : t("nobody, so it can be archived")}</span></div></div>
      : version.state === "producing" && !version.auto ? <div className="lb-iss" data-tone="calm"><div className="lb-iss-head"><b>{(version.after?.settled ?? 0) + (version.after?.pending ?? 0) > 0 ? t("Auto-update off") : t("Auto-update off since publication")}</b></div>
        <div className="lb-why"><span>{t("Values")}</span><span>{((version.after?.settled ?? 0) + (version.after?.pending ?? 0) > 0 ? t("to {lastDatetime}; nothing after", { lastDatetime: mdy(version.lastDatetime ?? version.publishedAt) }) : t("to {lastDatetime}; no after-publication record", { lastDatetime: mdy(version.lastDatetime ?? version.publishedAt) }))}</span>
          <span>{t("Turning it on")}</span><span>{t("fills the periods after {lastDatetime} as late, then a value every close", { lastDatetime: md(version.lastDatetime ?? version.publishedAt) })}</span></div></div> : null}
    <div className="lb-ctabs" role="tablist">{tabs.map(item => <button type="button" role="tab" key={item.id} className="lb-tab" data-selected={item.id === current} onClick={() => handlers.onTab(item.id)}>{item.label}{item.count ? <i>{item.count}</i> : null}</button>)}
      <span className="lb-sp" /><TxIconButton label={handlers.filled ? t("Show the list") : t("Fill with this item")} icon={handlers.filled ? "restore" : "maximize"} onClick={handlers.onFill} /><TxIconButton label={t("Close (Esc)")} icon="close" onClick={handlers.onClose} /></div>
    <div className="lb-scroll" ref={scroller}>
      {current === "overview" && <ItemOverview detail={detail} version={version} need={need} metric={metric} span={span} period={period} handlers={{onMetric: setMetric, onSpan: setSpan, onPeriod: setPeriod, onValues: label => {setValuesDate(label); handlers.onTab("values");},
        onLog: () => handlers.onTab("log"), onRetry: retry, onOpenUser: openUser, onOpenVersion: handlers.onOpenVersion}} />}
      {current === "values" && (version.state === "archived" ? <div className="lb-calm">{t("v{version} was archived; its values were dropped.", { version: version.version })}</div> : <ItemValues api={api} version={version} kind={kind} date={valuesDate} onDate={setValuesDate} onLog={() => handlers.onTab("log")} />)}
      {current === "log" && <ItemLog api={api} version={version} auto={version.auto} onRetry={retry} onOpenCode={(file, line) => handlers.onCode(file, line)} />}
      {current === "versions" && <ItemVersions api={api} detail={detail} current={version} need={need} onCode={() => handlers.onCode()} onSelect={handlers.onVersion} onAuto={(item, on) => void failed(wanted.ask(item.versionId, on, () => api.setAuto(item.versionId, on)), (on ? t("Auto-update on for {name} v{version}", { name: row.name, version: item.version }) : t("Auto-update off for {name} v{version}", { name: row.name, version: item.version })))} onOpenUser={openUser} />}
      <div style={{height: 16}} />
    </div>
    <StopSheet api={api} open={sheet === "stop"} name={row.name} versionId={version.versionId} version={version.version} onClose={() => setSheet(null)} onOpenUser={user => {setSheet(null); openUser(user);}}
      onDone={preview => {setSheet(null); toast({title: t("Stopped {name} v{version}", { name: row.name, version: version.version }), detail: preview.users.length ? (preview.users.length === 1 ? t("{usersCount} user keeps the values", { usersCount: preview.users.length }) : t("{usersCount} users keep the values", { usersCount: preview.users.length })) : t("nobody used it")}); setTick(value => value + 1);}} />
    <ArchiveSheet api={api} open={sheet === "archive"} name={row.name} versionId={version.versionId} version={version.version} users={archiveUsers} block={version.state !== "stopped" ? archiveBlock({...version, users: []}) : ""} stoppedAt={version.stoppedAt} run={runNumber ?? null}
      onOpenUser={user => {setSheet(null); openUser(user);}}
      figures={`${version.measure ?? ""} ${version.pub?.value == null ? "" : sgn(version.pub.value, 4)}`.trim()} models={kind === "model"} onClose={() => setSheet(null)} onDone={() => {setSheet(null); toast({title: t("Archived {name} v{version}", { name: row.name, version: version.version })}); setTick(value => value + 1);}} />
  </div>;
}
