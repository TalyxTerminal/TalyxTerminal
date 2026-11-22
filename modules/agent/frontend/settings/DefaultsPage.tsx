import { thinkingLabel } from "./modelInfo";
import { t } from "../i18n";
import { useEffect, useState, type ReactNode } from "react";
import { TxAlert, TxButton, TxRow, TxSelect, TxSettingsGroup, TxSettingsPage, TxSwitch, TxTooltip, type ModuleContext } from "@talyx/sdk";
import { SettingsData, errorText } from "./shared";
import { chatModelChoices } from "../chat/live";
import type { AgentClient } from "../data/client";
import type { AgentDefaults, AgentSettings } from "../data/types";

type Kind = "general" | "chat" | "bot";
type Scope = "global" | "workspace";
export function settingsScope(ctx: ModuleContext): Scope {
  return (ctx.props as {scope?: string} | null)?.scope === "workspace" ? "workspace" : "global";
}
interface Defaults extends AgentDefaults {
  autoCompact:boolean; compactAt:number; memoryTokens:number; botApprovalMinutes:number; botRunMinutes:number;
  botFailureLimit:number; botRoundLimit:number; chatNotification:string; notifyNeedsYou:boolean; notifyResults:boolean; notifyChannel:string;
}
type Data = { defaults: Defaults; overrides: Partial<Defaults>; channels: {id:string; name:string}[] };
type Option = [string | number, string];

export function DefaultsPage({ctx,kind}: {ctx:ModuleContext;kind:Kind}) {
  return <SettingsData ctx={ctx}>{(settings,client)=><DefaultsForm scope={settingsScope(ctx)} kind={kind} settings={settings} client={client}/>}</SettingsData>;
}

function DefaultsForm({scope,kind,settings,client}:{scope:Scope;kind:Kind;settings:AgentSettings;client:AgentClient}) {
  const [page,setPage] = useState<Data | null>(null), [error,setError] = useState("");
  const load = () => client.settings<Data>("page",{scope,kind}).then(setPage);
  useEffect(()=>{void load().catch(e=>setError(errorText(e)));},[scope,kind,client]);
  if (!page) return error ? <TxAlert title={t("Settings could not be read")}>{error}</TxAlert> : null;
  const d = page.defaults, models = chatModelChoices(settings);
  const save = (key:keyof Defaults, value:unknown, reset=false) => {
    setError("");
    void client.settings("set_defaults",{scope,kind,...reset ? {reset:key} : {changes:{[key]:value}}}).then(load).catch(e=>setError(errorText(e)));
  };
  const row = (key:keyof Defaults, title:string, control:ReactNode, tip?:string) => <TxRow title={tip
    ? <TxTooltip label={tip}><span className="sm-tip" tabIndex={0}>{title}</span></TxTooltip> : title}>
    <div className="sm-control">{scope==="workspace" && key in page.overrides && <TxButton size="sm" variant="quiet" onClick={()=>save(key,null,true)}>{t("Reset to global")}</TxButton>}{control}</div>
  </TxRow>;
  const select = (key:keyof Defaults,title:string,options:Option[],tip?:string) => row(key,title,
    <TxSelect aria-label={title} className="sm-select" value={String(d[key] ?? "")} onChange={e=>save(key,options.find(o=>String(o[0])===e.target.value)?.[0])}>
      {options.map(([v,label])=><option value={v} key={v}>{label}</option>)}</TxSelect>,tip);
  const toggle = (key:keyof Defaults,title:string) => row(key,title,
    <TxSwitch hideLabel label={title} checked={d[key]===true} onCheckedChange={v=>save(key,v)}/>);
  const model = (key:"modelId"|"childModelId"|"summaryModelId",title:string,empty:string) => row(key,title,
    <TxSelect aria-label={title} className="sm-select" value={d[key] ?? ""} onChange={e=>save(key,e.target.value || null)}>
      <option value="">{empty}</option>
      {[...new Set(models.map(m=>m.connection))].map(c=><optgroup key={c} label={c}>{models.filter(m=>m.connection===c).map(m=><option key={m.id} value={m.id}>{m.name}</option>)}</optgroup>)}
    </TxSelect>);
  const current = models.find(m=>m.id===d.modelId);
  return <TxSettingsPage>{error && <TxAlert title={t("Settings could not be saved")}>{error}</TxAlert>}
    {kind==="general" ? <>
      <TxSettingsGroup title={t("Models for helpers")}>{model("childModelId",t("Subagents"),t("Same as the chat or bot"))}{model("summaryModelId",t("Summaries and memory"),t("Same as the chat or bot"))}</TxSettingsGroup>
      <TxSettingsGroup title={t("Context")}>{toggle("autoCompact",t("Compact automatically"))}{d.autoCompact && select("compactAt",t("Compact at"),[60,70,80,90].map(v=>[v,v+"%"]))}</TxSettingsGroup>
      <TxSettingsGroup title={t("Memory")}>{toggle("memoryEnabled",t("Use memory"))}{d.memoryEnabled && select("memoryTokens",t("Always in context"),[1000,2000,4000,8000].map(v=>[v,t("{tokens} tokens", { tokens: v.toLocaleString("en-US") })]))}</TxSettingsGroup>
    </> : <>
      <TxSettingsGroup title={kind==="chat"?t("New chats"):t("New bots")}>{model("modelId",t("Model"),t("Choose a model"))}
        {row("modelThinking",t("Thinking"),<TxSelect aria-label={t("Thinking")} className="sm-select" value={d.modelThinking[current?.id ?? ""] ?? current?.defaultLevel ?? ""} onChange={e=>current&&save("modelThinking",{...d.modelThinking,[current.id]:e.target.value})}>
          {current?.levels.map(v=><option key={v} value={v}>{thinkingLabel(v)}</option>)}</TxSelect>)}
        {select("defaultPermission",t("Permission"),[["read_only",t("Read only")],["auto",t("Auto")],["full",t("Full access")]],t("Read only asks before changes. Auto asks before deleting your things. Python runs with your local account's permissions."))}
        {toggle("childrenEnabled",t("Subagents"))}</TxSettingsGroup>
      {kind==="chat" ? <TxSettingsGroup title={t("Notifications")}>{select("chatNotification",t("When a chat finishes"),[["never",t("Never")],["background",t("Only when TALYX is in the background")],["always",t("Always")]])}</TxSettingsGroup> : <>
        <TxSettingsGroup title={t("Notifications for new bots")}>{toggle("notifyNeedsYou",t("When it needs you"))}{toggle("notifyResults",t("With results"))}
          {select("notifyChannel",t("Also send to"),[["",t("None")],...page.channels.map(c=>[c.id,c.name] as Option)])}</TxSettingsGroup>
        <TxSettingsGroup title={t("Work without you")}>{select("botApprovalMinutes",t("Approvals expire after"),[[10,t("10 minutes")],[30,t("30 minutes")],[60,t("1 hour")],[240,t("4 hours")]])}
          {select("botRunMinutes",t("Longest run"),[[30,t("30 minutes")],[60,t("1 hour")],[180,t("3 hours")],[360,t("6 hours")],[0,t("No limit")]])}
          {select("botFailureLimit",t("Pause a bot after"),[[2,t("2 failures")],[3,t("3 failures")],[5,t("5 failures")],[0,t("Never")]])}
          {select("botRoundLimit",t("Bot-to-bot turns"),[50,100,200,500].map(v=>[v,t("{count} turns", { count: v })]))}</TxSettingsGroup></>}
    </>}</TxSettingsPage>;
}
