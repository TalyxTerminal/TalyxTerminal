import { intlLocale } from "@talyx/sdk";
import { t } from "../i18n";
import { useEffect, useState } from "react";
import { TxAlert, TxButton, TxInput, TxKeyNumbers, TxProgress, TxRow, TxSegmented, TxSelect, TxSettingsGroup, TxSettingsPage,
  TxSwitch, TxTable, TxTd, TxTh, type ModuleContext } from "@talyx/sdk";
import { SettingsData, errorText } from "./shared";
import type { AgentClient } from "../data/client";
import type { AgentSettings, UsageGroup } from "../data/types";

const ownerKind: Record<string, string> = { Chat: t("Chat"), Bot: t("Bot") };
const months = () => Array.from({length:12},(_,n)=>{const now=new Date();return new Date(now.getFullYear(),now.getMonth()-n,1);});
const monthId = (d:Date) => d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0");
const monthLabel = (d:Date) => d.toLocaleDateString(intlLocale(),{month:"long",year:"numeric"});
const money = (n:number,currency:string) => `${n.toLocaleString("en-US", {minimumFractionDigits:2, maximumFractionDigits:2})} ${currency}`;
const count = (n:number) => n.toLocaleString("en-US");

export function SpendingPage({ctx,limit=false}:{ctx:ModuleContext;limit?:boolean}) {
  return <SettingsData ctx={ctx}>{(settings,client,refresh)=>limit
    ? <Limit settings={settings} client={client} refresh={refresh} models={()=>ctx.platform.settings.open("agent.models")}/>
    : <Usage settings={settings} client={client}/>}</SettingsData>;
}
function Usage({settings,client}:{settings:AgentSettings;client:AgentClient}){
  const [by,setBy]=useState<"day"|"workspace"|"owner"|"model">("owner"),[month,setMonth]=useState(monthId(new Date()));
  const [rows,setRows]=useState<(UsageGroup & {label?:string;kind?:string})[]>([]),[error,setError]=useState("");
  useEffect(()=>{let live=true;void client.settings<{groups:typeof rows}>("read_usage",{groupBy:by,month}).then(data=>{if(live){setRows(data.groups);setError("");}})
    .catch(e=>{if(live)setError(errorText(e));});return ()=>{live=false;};},[client,by,month]);
  const totals: Record<string, number> = {};
  for (const row of rows) for (const [currency, amount] of Object.entries(row.amounts))
    totals[currency] = (totals[currency] ?? 0) + Number(amount);
  const amounts = (values: Record<string, string | number>) => Object.entries(values)
    .map(([currency, amount]) => money(Number(amount), currency)).join(", ");
  const total=(get:(r:UsageGroup)=>number)=>rows.reduce((a,r)=>a+get(r),0);
  return <TxSettingsPage>{error&&<TxAlert title={t("Usage")}>{error}</TxAlert>}
    <div className="sm-control"><TxSelect aria-label={t("Month")} className="sm-select" value={month} onChange={e=>setMonth(e.target.value)}>
      {months().map(d=><option value={monthId(d)} key={monthId(d)}>{monthLabel(d)}</option>)}</TxSelect></div>
    <TxKeyNumbers items={[{label:month,value:amounts(totals) || (rows.length ? t("No price") : money(0, settings.usage.currency))},{label:t("Requests"),value:count(total(r=>r.requests))},
      {label:t("Input tokens"),value:count(total(r=>r.inputTokens))},{label:t("Output tokens"),value:count(total(r=>r.outputTokens))}]}/>
    <TxSettingsGroup title={t("Spending")} action={<TxSegmented label={t("Group by")} size="sm" value={by} onChange={setBy}
      options={[{value:"day",label:t("Day")},{value:"workspace",label:t("Workspace")},{value:"owner",label:t("Chat or bot")},{value:"model",label:t("Model")}]}/>}>
      <div className="sm-table"><TxTable><thead><tr><TxTh>{{day:t("Day"),workspace:t("Workspace"),owner:t("Chat or bot"),model:t("Model")}[by]}</TxTh>
        {(by==="owner"||by==="model")&&<TxTh>{by==="model"?t("Connection"):t("Kind")}</TxTh>}{[t("Requests"),t("Input"),t("Output"),t("Cost")].map(label=><TxTh key={label} numeric>{label}</TxTh>)}</tr></thead>
        <tbody>{rows.map(r=>{const m=by==="model"?settings.models.find(m=>m.id===r.key):undefined;return <tr key={r.key}>
          <TxTd>{r.label??m?.model??(r.key||t("Unassigned"))}</TxTd>{(by==="owner"||by==="model")&&<TxTd>{r.kind ? ownerKind[r.kind] ?? r.kind : settings.connections.find(c=>c.id===m?.connectionId)?.name??"—"}</TxTd>}
          <TxTd numeric>{count(r.requests)}</TxTd><TxTd numeric>{count(r.inputTokens)}</TxTd><TxTd numeric>{count(r.outputTokens)}</TxTd><TxTd numeric>{amounts(r.amounts) || t("No price")}</TxTd>
        </tr>;})}</tbody></TxTable></div></TxSettingsGroup>
  </TxSettingsPage>;
}
// Spent is in the limit's currency, other currencies converted; null while missingRates have no exchange rate.
type Spending = {spent:string|null;missingRates:string[];bots:{groupId:string;name:string;currency:string;monthlyLimit:string;spent:string|null;missingRates:string[]}[];unpriced:UsageGroup[]};
function Limit({settings,client,refresh,models}:{settings:AgentSettings;client:AgentClient;refresh:()=>Promise<void>;models:()=>void}){
  const limit=settings.usage.monthlyLimit, currency=settings.usage.currency;
  const [enabled,setEnabled]=useState(limit!==null),[amount,setAmount]=useState(limit??""),[data,setData]=useState<Spending|null>(null),[error,setError]=useState("");
  useEffect(()=>{void client.settings<Spending>("spending").then(setData).catch(e=>setError(errorText(e)));},[client,limit]);
  const save=(v:string|null)=>{void client.settings("usage",{...settings.usage,monthlyLimit:v}).then(refresh).catch(e=>setError(errorText(e)));};
  const meter=(title:string,used:{spent:string|null;missingRates:string[]},ceiling:number,unit=currency)=>{
    if(used.spent===null)return <div key={title} className="sm-meter"><div className="sm-meter-copy"><span className="sm-meter-title">{title}</span>
      <span className="sm-meter-sub">{t("No exchange rate for {currencies} yet", { currencies: used.missingRates.join(", ") })}</span></div></div>;
    const spent=Number(used.spent),percent=ceiling>0?spent/ceiling*100:100;return <div key={title} className="sm-meter"><div className="sm-meter-copy"><span className="sm-meter-title">{title}</span>
    <span className="sm-meter-sub">{t("{spent} of {limit}", { spent: money(spent,unit), limit: money(ceiling,unit) })}</span></div><TxProgress value={Math.min(100,percent)} className="sm-meter-bar"/><span className="sm-meter-share">{t("{percent}% used", { percent: Math.round(percent) })}</span></div>;};
  return <TxSettingsPage>{error&&<TxAlert title={t("Spending limit")}>{error}</TxAlert>}
    <TxSettingsGroup title={t("Monthly limit")}><TxRow title={t("Limit all chats and bots")}><TxSwitch hideLabel label={t("Limit all chats and bots")} checked={enabled} onCheckedChange={on=>{setEnabled(on);if(!on)save(null);else if(amount!=="")save(amount);}}/></TxRow>
      {enabled&&<TxRow title={t("Amount")}><TxInput className="sm-amount" aria-label={t("Amount")} inputMode="decimal" value={amount} suffix={currency} onChange={e=>setAmount(e.target.value)}
        onBlur={()=>{if(amount.trim()!==""&&amount!==limit)save(amount);}}/></TxRow>}{enabled&&limit!==null&&data&&meter(t("This month"),data,Number(limit))}</TxSettingsGroup>
    <TxSettingsGroup title={t("Bots with their own limit")}>{data?.bots.map(b=>meter(b.name,b,Number(b.monthlyLimit),b.currency))}</TxSettingsGroup>
    {enabled&&<TxSettingsGroup title={t("Not counted")}>{data?.unpriced.map(g=><TxRow key={g.key} title={settings.models.find(m=>m.id===g.key)?.model??g.key} detail={t("{count} requests this month", { count: g.tokenOnlyRequests })}>
      <TxButton size="sm" onClick={models}>{t("Set price…")}</TxButton></TxRow>)}</TxSettingsGroup>}</TxSettingsPage>;
}
