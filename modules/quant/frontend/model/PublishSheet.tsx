/* Publishing a model run into the library: the one model it publishes, the factors that go with it, what it changes against the last version and who stays on that one;
   once started, the steps (the future-data check of the model, of each factor it brings, the write) with a way to stop. A refusal is written where the button was.
   Publish is there only when it can go out: a name another kind holds, or a stopped input with new values on, is said instead of it; Publish with no name puts the cursor in Name. */
import { t } from "../i18n";
import "../factor/publish.css";
import { useEffect, useRef, useState } from "react";
import { ContractError, TxButton, TxInput, TxSheet, TxSwitch, TxSkeleton, useTxToast } from "@talyx/sdk";
import type { ModelApi, ModelRun, Plan } from "./api";
import { dayYear, pct, plainPct, sgn } from "./format";

interface Publication {state: string; stage: string; message: string | null; versions: string[]; future?: {passed: boolean; title: string} | null; factor?: {name: string; n: number; of: number; cut?: number; cuts?: number} | null; futureOf?: {n: number; of: number} | null}
type Progress = {state: "running" | "failed" | "conflict"; publication?: Publication; message?: string};

const stageText = (publication: Publication | undefined) => {
  if (!publication || publication.stage === "future") return publication?.futureOf && publication.futureOf.of > 1 ? t("Checking for future data: source {source} of {total}", { source: publication.futureOf.n, total: publication.futureOf.of }) : t("Checking for future data");
  if (publication.stage === "factors" && publication.factor) return t("Checking {name} for future data {step} of {steps} (factor {factor} of {factors})", { name: publication.factor.name, step: publication.factor.cut ?? 1, steps: publication.factor.cuts ?? 3, factor: publication.factor.n, factors: publication.factor.of });
  return t("Writing to the library");
};
/** One measure, one format, in all three columns: Rank IC to four places, t to two, long–short and turnover as percentages (turnover unsigned). */
const shown = (measure: string, number: number | null, change = false) => number === null ? "—" : measure === "t" ? sgn(number, 2)
  : measure === "Long–short net" ? pct(number, 1) : measure === "Turnover" ? (change ? pct(number, 1) : plainPct(number, 1)) : sgn(number, 4);

/** `published` is the library name this model last published under: a new version goes to the same item unless the user names another. */
export function PublishSheet({mapi, run, open, onClose, onPublished, published}: {mapi: ModelApi; run: ModelRun; open: boolean; onClose(): void; onPublished(): void; published?: string | null}) {
  const toast = useTxToast();
  const [name, setName] = useState(published ?? run.name ?? "");
  const [auto, setAuto] = useState(true);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [nameAsked, setNameAsked] = useState(false);
  const nameField = useRef<HTMLInputElement>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [publication, setPublication] = useState<string | null>(null);
  useEffect(() => {if (open) {setName(published ?? run.name ?? ""); setProgress(null); setPublication(null); setNameAsked(false);}}, [open, run.id]);
  useEffect(() => {
    if (!open) return;
    let live = true;
    const timer = setTimeout(() => void mapi.plan(run.id, name.trim() || undefined).then(next => {if (live) {setPlan(next); setPlanError(null);}},
      reason => {if (live) setPlanError(reason instanceof Error ? reason.message : String(reason));}), 200);
    return () => {live = false; clearTimeout(timer);};
  }, [open, run.id, name]);
  useEffect(() => {
    if (publication === null) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      let found: Publication;
      try {
        found = await mapi.publication(publication) as Publication;
      } catch (reason) {   // the publication goes on in Quant; the sheet says it lost sight of it, and reading again is a reopen away
        if (live) setProgress({state: "conflict", message: t("Quant did not answer about this publication: {message}", { message: reason instanceof Error ? reason.message : String(reason) })});
        return;
      }
      if (!live) return;
      if (found.state === "completed") {toast({title: t("Published {name} v{version} from #{number}", { name: plan?.name ?? name, version: plan?.version ?? 1, number: run.number })}); onPublished(); onClose(); return;}
      if (found.state === "failed" || found.state === "cancelled") {setProgress({state: "failed", publication: found, message: found.message ?? t("The publication stopped.")}); return;}
      setProgress({state: "running", publication: found});
      timer = setTimeout(tick, 600);
    };
    timer = setTimeout(tick, 300);
    return () => {live = false; clearTimeout(timer);};
  }, [publication]);
  // A stopped input stops only a version that would make new values; a name another kind holds stops any.
  const stop = plan !== null && plan.blocked !== null && (plan.blocked.field === "name" || auto) ? plan.blocked : null;
  const submit = () => {
    if (plan === null || stop !== null || progress !== null) return;
    if (!name.trim()) {setNameAsked(true); nameField.current?.focus(); return;}
    setProgress({state: "running"});
    mapi.publish(run.id, {name: name.trim(), autoUpdate: auto}).then(started => setPublication(started.id), reason => {
      setProgress({state: reason instanceof ContractError && reason.code === "conflict" ? "conflict" : "failed", message: reason instanceof Error ? reason.message : String(reason)});
    });
  };
  const cancel = () => {
    if (publication === null || progress?.state !== "running") {onClose(); return;}
    // Cancel stops the publication itself (nothing is added to the library), then closes
    mapi.stopPublication(publication).then(() => onClose(), reason => setProgress({state: "failed", message: reason instanceof Error ? reason.message : String(reason)}));
  };
  const footer = progress?.state === "failed" || progress?.state === "conflict"
    ? <div className="pb-foot"><div className="pb-failed"><div className="pb-stage">{stageText(progress.publication)}</div><div className="pb-blocked" role="alert">{progress.state === "failed" ? t("Not published: {message}", { message: progress.message ?? "" }) : progress.message}</div></div><span className="pb-sp" /><TxButton onClick={onClose}>{t.c("action", "Close")}</TxButton></div>
    : progress ? <div className="pb-foot"><div className="pb-failed"><span className="pb-stage"><span className="qx-spin" />{stageText(progress.publication)}</span></div><span className="pb-sp" /><TxButton onClick={cancel}>{t("Cancel")}</TxButton></div>
    : <div className="pb-foot"><span className="pb-sp" /><TxButton onClick={onClose}>{t("Cancel")}</TxButton>{plan !== null && stop === null && <TxButton variant="primary" onClick={submit}>{t("Publish v{version}", { version: plan.version })}</TxButton>}</div>;
  return <TxSheet open={open} onOpenChange={next => {if (!next) cancel();}} title={t("Publish to library")} label={t("Publish")} footer={footer}>
    {plan === null ? planError !== null ? <div className="pb-blocked" role="alert">{planError}</div> : <TxSkeleton width="60%" /> : <div className="pb">
      <div className="pb-row"><span>{t("Name")}</span><div><TxInput ref={nameField} autoFocus aria-label={t("Name")} value={name} disabled={progress !== null} aria-invalid={nameAsked && !name.trim() || undefined}
        onChange={event => setName(event.target.value)} onKeyDown={event => {if (event.key === "Enter") submit();}} />
        {stop && stop.field === "name" && <div className="pb-block" role="alert">{stop.reason}</div>}</div></div>
      {stop && stop.field === "inputs" && <div className="pb-row" data-solo=""><div className="pb-block" role="alert">{stop.reason}</div></div>}
      <div className="pb-row"><span>{t("Version")}</span><span>{t("v{version} from run #{number}", { version: plan.version, number: plan.number })}</span></div>
      <div className="pb-row"><span>{t("Publishes")}</span><div>{t("Model · a prediction for each name, each day")}<div className="pb-t3">{t("Checked for future data first, then written")}</div></div></div>
      <div className="pb-row"><span>{t("New values")}</span><div><TxSwitch label={t("Produce a new value each period")} checked={auto} onCheckedChange={setAuto} disabled={progress !== null} />
        <div className="pb-t3">{(plan.schedule.every ? t("Retrains · every {every} months", { every: plan.schedule.every }) : t("Retrains · on its schedule"))}</div></div></div>
      {(plan.alsoPublished.length > 0 || plan.pinned.length > 0) && <><h4>{t("Published with it")}</h4>
        {plan.alsoPublished.map(item => <div key={item.runId} className="pb-ck"><i data-tone="watch" /><b>{item.name}</b><span className="pb-t2">{t("v1, from factor run #{fromRun} · not in the library yet", { fromRun: item.fromRun })}</span></div>)}
        {plan.pinned.length > 0 && <div className="pb-t3 pb-ck" title={plan.pinned.map(pin => `${pin.name} v${pin.version}`).join("\n")}>{plan.pinned.length} {plan.pinned.length > 1 ? t("library factors stay at the versions used") : t("library factor stays at the version used")}</div>}
        {plan.pinned.filter(pin => pin.state === "stopped" || pin.state === "archived").map(pin => <div key={pin.versionId} className="pb-ck"><i data-tone="fix" /><b>{pin.name} v{pin.version}</b><span className="pb-t2">{t("is {state}", { state: pin.state ?? "" })}</span></div>)}</>}
      <h4>{t.rich("Checks to review {count}", { count: <span className="pb-t4">{plan.checksToReview.length}</span> })}</h4>
      {plan.checksToReview.length === 0 ? <div className="pb-t3 pb-ck">{t("None")}</div> : plan.checksToReview.map(check => <div className="pb-ck" key={check.name}><i data-tone="watch" /><b>{check.name}</b><span className="pb-t2">{check.value}</span></div>)}
      {plan.previous && <><h4>{t("Compared with v{version}", { version: plan.previous.version })}</h4>
        <table className="pb-table"><thead><tr><th>{t("Measure")}</th><th>{`v${plan.previous.version}`}</th><th>{`v${plan.version}`}</th><th>{t("Change")}</th></tr></thead>
          <tbody>{plan.compare.map(row => <tr key={row.measure}><td>{row.measure}</td><td className="pb-t3">{shown(row.measure, row.previous)}</td><td>{shown(row.measure, row.current)}</td><td>{row.change === null ? row.note ?? "" : shown(row.measure, row.change, true)}</td></tr>)}</tbody></table>
        {plan.paramsDiff.length > 0 && <div className="pb-t3">{plan.paramsDiff.slice(0, 6).map(item => `${item.name} ${String(item.previous)} → ${String(item.current)}`).join(" · ")}</div>}
        {plan.users.length > 0 && <><h4>{t("Using v{version}", { version: plan.previous.version })}</h4><div className="pb-us">{plan.users.map(user => <div key={user.name}>{user.name} <span className="pb-t3">{user.kind}</span></div>)}
          <div className="pb-t3">{t("They stay on v{version} until each one switches.", { version: plan.previous.version })}</div></div></>}</>}
    </div>}
  </TxSheet>;
}
void dayYear;
