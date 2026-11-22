/* Publishing an allocation run into the library (the draft's publish sheet): the name, the version, the first period it will serve, the checks to look at (they never block), what the
   next version changes against the last one and who stays on the last one. A pool that holds a strategy run or a library item is refused here: the reason stands
   where Publish would be. Publish with no name puts the cursor in Name. */
import { t } from "../i18n";
import { useEffect, useRef, useState } from "react";
import { ContractError, TxButton, TxInput, TxSheet, TxSwitch, TxSkeleton, useTxToast } from "@talyx/sdk";
import "../factor/publish.css";
import { md } from "../library/format";
import { nf, pc, pp } from "../factor/format";
import { pct } from "../shared/format";
import type { AllocationApi, PublishPlan } from "./api";

type Compare = PublishPlan["compare"][number];
/** A figure of the comparison table: a quantity with a direction (a return, a drawdown, a gap to a benchmark) carries its sign, one without (volatility, turnover, a ratio) never does. */
export const compareFigure = (row: Compare, value: number | string | null): string => value === null ? "—" : typeof value === "string" ? value
  : row.unit === "pt" ? pp(value, row.digits ?? 2) : row.unit === "pct" ? (row.signed ? pc(value, row.digits ?? 2) : pct(value, row.digits ?? 2)) : nf(value, row.digits ?? 2);
/** The change column: always a difference, so always signed (points for shares, plain for a ratio). */
export const compareChange = (row: Compare): string => row.change === null ? "" : row.unit === "ratio" ? (row.change >= 0 ? "+" : "−") + Math.abs(row.change).toFixed(row.digits ?? 2) : pp(row.change, row.digits ?? 2);

export function PublishSheet({api, runId, runName, open, onClose, onPublished}: {api: AllocationApi; runId: string; runName: string; open: boolean; onClose(): void; onPublished(version: number): void}) {
  const toast = useTxToast();
  const [name, setName] = useState(runName);
  const [auto, setAuto] = useState(true);
  const [plan, setPlan] = useState<PublishPlan | null>(null);
  const [state, setState] = useState<{busy: boolean; error: string | null}>({busy: false, error: null});
  const [nameAsked, setNameAsked] = useState(false);
  const nameField = useRef<HTMLInputElement>(null);
  useEffect(() => {if (open) {setName(runName); setState({busy: false, error: null}); setNameAsked(false);}}, [open, runId]);
  useEffect(() => {
    if (!open) return;
    let live = true;
    const timer = setTimeout(() => void api.publishPlan(runId, name.trim() || runName).then(value => {if (live) {setPlan(value); setState(now => ({...now, error: null}));}},
      reason => {if (live) setState(now => ({...now, error: reason instanceof Error ? reason.message : String(reason)}));}), 200);  // the plan's read failing says why
    return () => {live = false; clearTimeout(timer);};
  }, [open, runId, name]);
  const submit = () => {
    if (plan === null || plan.blocked !== null || state.busy) return;
    if (!name.trim()) {setNameAsked(true); nameField.current?.focus(); return;}
    setState({busy: true, error: null});
    void api.publish(runId, name.trim(), auto).then(done => {toast({title: t("Published {name} v{version} from #{runNumber}", { name: done.name, version: done.version, runNumber: plan.runNumber })}); onPublished(done.version); onClose();},
      reason => setState({busy: false, error: reason instanceof ContractError || reason instanceof Error ? reason.message : String(reason)}));
  };
  return <TxSheet open={open} onOpenChange={value => {if (!value) onClose();}} title={t("Publish to library")} label={t("Publish")} footer={plan ? <div className="pb-foot">
    {state.error && <span className="pb-blocked" role="alert">{t("Not published: {error}", { error: state.error })}</span>}<span className="pb-sp" /><TxButton onClick={onClose}>{t("Cancel")}</TxButton>
    {plan.blocked === null && <TxButton variant="primary" disabled={state.busy} onClick={submit}>{t("Publish v{version}", { version: plan.version })}</TxButton>}</div> : undefined}>
    {plan ? <div className="pb al-pb">
      <div className="pb-row"><span>{t("Name")}</span><div><TxInput ref={nameField} autoFocus aria-label={t("Name")} value={name} disabled={state.busy} aria-invalid={nameAsked && !name.trim() || undefined} onChange={event => setName(event.target.value)} onKeyDown={event => {if (event.key === "Enter") submit();}} />
        {plan.blocked?.field === "name" && <div className="pb-block" role="alert">{plan.blocked.reason}</div>}</div></div>
      {plan.blocked && plan.blocked.field !== "name" && <div className="pb-row" data-solo=""><div className="pb-block" role="alert">{plan.blocked.reason}</div></div>}
      <div className="pb-row"><span>{t("Version")}</span><span>{t("v{version} from run #{runNumber}", { version: plan.version, runNumber: plan.runNumber })}</span></div>
      <div className="pb-row"><span>{t("First period")}</span><span>{plan.firstPeriod.label}</span></div>
      <div className="pb-row" data-solo=""><TxSwitch label={t("Produce every period automatically")} checked={auto} onCheckedChange={setAuto} disabled={state.busy} /></div>
      <h4>{t.rich("Checks to review {count}", { count: <span className="pb-t4">{plan.checks.length}</span> })}</h4>
      {plan.checks.length === 0 ? <div className="pb-t3 pb-ck">{t("None")}</div> : plan.checks.map(check => <div className="pb-ck" key={check.name}><i data-tone="watch" /><b>{check.name}</b><span className="pb-t2">{check.need ? t("{value}, need {need}", { value: check.value ?? "", need: check.need }) : check.value ?? ""}</span></div>)}
      {plan.previous ? <><h4>{t("Compared with v{version}, {publishedAt}", { version: plan.previous.version, publishedAt: md(plan.previous.publishedAt) })}</h4>
        <table className="pb-table"><thead><tr><th>{t("Measure")}</th><th>{`v${plan.previous.version}`}</th><th>{`v${plan.version}`}</th><th>{t("Change")}</th></tr></thead>
          <tbody>{plan.compare.map(row => <tr key={row.measure}><td>{row.measure}</td><td className="pb-t3">{compareFigure(row, row.previous)}</td><td>{compareFigure(row, row.current)}</td><td>{compareChange(row)}</td></tr>)}</tbody></table>
        {plan.users.length > 0 && <><h4>{t("Using v{version}", { version: plan.previous.version })}</h4><div className="pb-us">{plan.users.map(user => <div key={user.name}>{user.name} <span className="pb-t3">{user.kind}</span></div>)}
          <div className="pb-t3">{t("They stay on v{version} until each one switches.", { version: plan.previous.version })}</div></div></>}</>
        : plan.compare.length > 0 && <><h4>{t("Run #{runNumber}", { runNumber: plan.runNumber })}</h4>
          <table className="pb-table"><tbody>{plan.compare.map(row => <tr key={row.measure}><td>{row.measure}</td><td>{compareFigure(row, row.current)}</td></tr>)}</tbody></table></>}
    </div> : state.error ? <div className="pb-blocked" role="alert">{state.error}</div> : <TxSkeleton width="60%" />}
  </TxSheet>;
}
