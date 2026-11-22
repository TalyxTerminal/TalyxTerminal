import { t } from "../i18n";
import "./publish.css";
/* Publishing a factor run into the library (output/quant-design/factor.js publishSheet): the name, what the next version changes against the last one, who stays on the last one, the checks to look at
   (they never block) and, once started, the steps with a way to stop; a value that reads later data is refused here with the time it changed at.
   Publish is there only when it can go out (a name another kind holds is said instead); Publish with no name puts the cursor in Name. */
import { useEffect, useRef, useState, type RefObject } from "react";
import { ContractError, TxButton, TxInput, TxSheet, TxSwitch, TxSkeleton, useTxToast, type ModuleContext } from "@talyx/sdk";
import { at, route } from "../types";
import { nf, pc, pp, sgn, tone } from "./format";
import { md } from "../library/format";

export interface PublishPlan {
  runId: string; name: string; version: number; runNumber: number; formula: string | null; files: string[]; checks: {name: string; value: number | string | null; need: string | null; state: string}[];
  previous: {version: number; publishedAt: number} | null;
  compare: {measure: string; previous: number | string | null; current: number | string | null; change: number | null; unit: "ic" | "t" | "pct" | "count" | "text"}[];
  users: {name: string; kind: string}[]; closest: {name: string; version: string; rho: number; itemId: string | null} | null; blocked: {reason: string} | null;
}
export interface PublishProgress { state: "running" | "failed" | "conflict" | "done"; stage?: "values" | "future" | "commit"; step?: number | null; of?: number | null; message?: string; code?: string }

export const publishToast = (name: string, version: number, run: number) => t("Published {name} v{version} from #{run}", { name, version, run });
const signed = (value: number) => (value < 0 ? "−" : "") + Math.abs(value).toFixed(2);
const stage = (progress: PublishProgress) => progress.stage === "values" ? t("Computing values for the whole period") : progress.stage === "future" ? t("Checking for future data {step} of {total}", { step: Math.max(1, progress.step ?? 0), total: progress.of ?? 3 }) : t("Writing to the library");

const figure = (unit: PublishPlan["compare"][number]["unit"], value: number | string | null) => value === null ? "—" : typeof value === "string" ? value : unit === "ic" ? sgn(value, 4) : unit === "t" ? nf(value, 2) : unit === "pct" ? pc(value, 2) : nf(value, 0);
const change = (row: PublishPlan["compare"][number]) => row.change === null ? "" : row.unit === "ic" ? sgn(row.change, 4) : row.unit === "t" ? sgn(row.change, 2) : row.unit === "pct" ? pp(row.change, 2) : sgn(row.change, 0);

export function PublishBody({plan, name, onName, auto, onAuto, busy, onSubmit, nameField, nameAsked}: {plan: PublishPlan; name: string; onName(value: string): void; auto: boolean; onAuto(value: boolean): void; busy: boolean; onSubmit(): void;
  nameField: RefObject<HTMLInputElement>; nameAsked: boolean}) {
  const hasPrevious = plan.previous !== null;
  return <div className="pb">
    <div className="pb-row"><span>{t("Name")}</span><div><TxInput ref={nameField} autoFocus aria-label={t("Name")} value={name} disabled={busy} aria-invalid={nameAsked && !name.trim() || undefined} onChange={event => onName(event.target.value)} onKeyDown={event => {if (event.key === "Enter" && !busy) onSubmit();}} />
      {plan.blocked && <div className="pb-block" role="alert">{plan.blocked.reason}</div>}</div></div>
    <div className="pb-row"><span>{t("Version")}</span><span>{`v${plan.version}`} <span className="pb-t3">{t("from run #{runNumber}", { runNumber: plan.runNumber })}</span></span></div>
    <div className="pb-row"><span>{plan.formula !== null ? t("Formula") : t("Code")}</span>{plan.formula !== null ? <code className="pb-code">{plan.formula}</code> : <span className="pb-t2">{plan.files.join(", ") || t("factor.py as of the run")}</span>}</div>
    <div className="pb-row"><span>{t("New values")}</span><TxSwitch label={t("Every trading day, after the close")} checked={auto} onCheckedChange={onAuto} disabled={busy} /></div>
    <h4>{t.rich("Checks to review {count}", { count: <span className="pb-t4">{plan.checks.length}</span> })}</h4>
    {plan.checks.length === 0 ? <div className="pb-t3 pb-ck">{t("None")}</div> : plan.checks.map(check => <div className="pb-ck" key={check.name}><i data-tone="watch" /><b>{check.name}</b><span className="pb-t2">{check.need ? t("{value}, needs {need}", { value: check.value ?? "", need: check.need }) : check.value ?? ""}</span></div>)}
    {plan.closest && <div className="pb-closest" data-attention={Math.abs(plan.closest.rho) >= 0.7 ? "" : undefined}>{t.rich("Closest in the library · {name} {version} · {rho}", { name: <b>{plan.closest.name}</b>, version: plan.closest.version, rho: <span className="pb-num">{signed(plan.closest.rho)}</span> })}</div>}
    {hasPrevious && <><h4>{t("Compared with v{version}, {publishedAt}", { version: plan.previous!.version, publishedAt: md(plan.previous!.publishedAt) })}</h4>
      <table className="pb-table"><thead><tr><th>{t("Measure")}</th><th>{`v${plan.previous!.version}`}</th><th>{`v${plan.version}`}</th><th>{t("Change")}</th></tr></thead>
        <tbody>{plan.compare.map(row => <tr key={row.measure}><td>{row.measure}</td><td className="pb-t3">{figure(row.unit, row.previous)}</td><td>{figure(row.unit, row.current)}</td><td className={row.change !== null && (row.unit === "ic" || row.unit === "pct") ? tone(row.change) : undefined}>{change(row)}</td></tr>)}</tbody></table>
      {plan.users.length > 0 && <><h4>{t("Using v{version}", { version: plan.previous!.version })}</h4><div className="pb-us">{plan.users.map(user => <div key={user.name}>{user.name} <span className="pb-t3">{user.kind}</span></div>)}
        <div className="pb-t3">{t("They stay on v{version} until each one switches.", { version: plan.previous!.version })}</div></div></>}</>}
  </div>;
}

export function PublishFooter({plan, progress, onCancel, onPublish}: {plan: PublishPlan; progress: PublishProgress | null; onCancel(): void; onPublish(): void}) {
  if (progress?.state === "failed") return <div className="pb-foot"><div className="pb-failed"><div className="pb-stage">{stage(progress)}</div><div className="pb-blocked" role="alert">{t("Not published: {message}", { message: progress.message ?? "" })}</div></div><span className="pb-sp" /><TxButton onClick={onCancel}>{t.c("action", "Close")}</TxButton></div>;
  if (progress?.state === "conflict") return <div className="pb-foot"><span className="pb-blocked" role="alert">{progress.message}</span><span className="pb-sp" /><TxButton onClick={onCancel}>{t.c("action", "Close")}</TxButton></div>;
  if (progress) return <div className="pb-foot"><div className="pb-failed"><span className="pb-stage"><span className="qx-spin" />{stage(progress)}</span></div><span className="pb-sp" /><TxButton onClick={onCancel}>{t("Cancel")}</TxButton></div>;
  return <div className="pb-foot"><span className="pb-sp" /><TxButton onClick={onCancel}>{t("Cancel")}</TxButton>{plan.blocked === null && <TxButton variant="primary" onClick={onPublish}>{t("Publish v{version}", { version: plan.version })}</TxButton>}</div>;
}

interface RunRecord { state: string; progress?: {stage?: PublishProgress["stage"]; step?: number | null; of?: number | null}; message?: string | null; error?: {code?: string} | null; publish?: {versions?: string[]} }

/** The sheet itself: plans without computing, publishes, follows the steps and tells the page when the version is in. */
export function PublishSheet({ctx, runId, runName, open, onClose, onPublished}: {ctx: ModuleContext; runId: string; runName: string; open: boolean; onClose(): void; onPublished(version: number): void}) {
  const toast = useTxToast();
  const [name, setName] = useState(runName);
  const [auto, setAuto] = useState(true);
  const [plan, setPlan] = useState<PublishPlan | null>(null);
  const [progress, setProgress] = useState<PublishProgress | null>(null);
  const [publication, setPublication] = useState<{id: string; name: string} | null>(null);  // the publication run and the name it published under
  const [nameAsked, setNameAsked] = useState(false);
  const nameField = useRef<HTMLInputElement>(null);
  useEffect(() => {if (open) {setName(runName); setProgress(null); setPublication(null); setNameAsked(false);}}, [open, runId]);
  useEffect(() => {
    if (!open) return;
    let live = true;
    const timer = setTimeout(() => void route<PublishPlan>(ctx, "GET", at`/object-runs/${runId}/publish-plan`, {name: name.trim() || runName}).then(value => {if (live) setPlan(value);}, () => undefined), 200);
    return () => {live = false; clearTimeout(timer);};
  }, [open, runId, name]);
  useEffect(() => {
    if (publication === null) return;
    const published = publication;
    let live = true;
    const tick = async () => {
      const record = await route<RunRecord>(ctx, "GET", at`/object-runs/${published.id}`).catch(() => null);
      if (!live) return;
      if (record === null) {timer = setTimeout(tick, 2000); return;}  // a read that failed is read again; the publication goes on
      if (record.state === "completed") {
        const versions = record.publish?.versions ?? [];
        const version = await route<{version: number}>(ctx, "GET", at`/library/versions/${versions[0] ?? ""}`).catch(() => null);
        toast({title: publishToast(published.name, version?.version ?? plan?.version ?? 1, plan?.runNumber ?? 0)});
        onPublished(version?.version ?? plan?.version ?? 1); onClose(); return;
      }
      if (record.state === "failed" || record.state === "cancelled" || record.state === "interrupted") {setProgress({state: "failed", stage: record.progress?.stage, step: record.progress?.step, of: record.progress?.of, message: record.message ?? "", code: record.error?.code}); return;}
      setProgress({state: "running", stage: record.progress?.stage, step: record.progress?.step, of: record.progress?.of});
      timer = setTimeout(tick, 600);
    };
    let timer = setTimeout(tick, 300);
    return () => {live = false; clearTimeout(timer);};
  }, [publication]);
  const submit = () => {
    if (plan === null || plan.blocked !== null || progress !== null) return;
    if (!name.trim()) {setNameAsked(true); nameField.current?.focus(); return;}
    setProgress({state: "running", stage: "values"});
    route<{id: string}>(ctx, "POST", at`/object-runs/${runId}/publish`, {name: name.trim(), auto}).then(started => setPublication({id: started.id, name: name.trim()}), reason => {
      const message = reason instanceof ContractError || reason instanceof Error ? reason.message : String(reason);
      setProgress({state: reason instanceof ContractError && reason.code === "conflict" ? "conflict" : "failed", message});
    });
  };
  const cancel = () => {
    if (publication === null || progress?.state !== "running") {onClose(); return;}
    // Closed once the stop is confirmed; a stop that failed says why and the sheet stays.
    route(ctx, "POST", at`/object-runs/${publication.id}/cancel`).then(onClose, reason => setProgress({state: "failed", message: reason instanceof Error ? reason.message : String(reason)}));
  };
  return <TxSheet open={open} onOpenChange={value => {if (!value) cancel();}} title={t("Publish to library")} label={t("Publish")} footer={plan ? <PublishFooter plan={plan} progress={progress} onCancel={cancel} onPublish={submit} /> : undefined}>
    {plan ? <PublishBody plan={plan} name={name} onName={setName} auto={auto} onAuto={setAuto} busy={progress !== null} onSubmit={submit} nameField={nameField} nameAsked={nameAsked} /> : <TxSkeleton width="60%" />}
  </TxSheet>;
}
