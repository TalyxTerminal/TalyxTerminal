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
  if (!page) return error ? <TxAlert title="Settings could not be read">{error}</TxAlert> : null;
  const d = page.defaults, models = chatModelChoices(settings);
  const save = (key:keyof Defaults, value:unknown, reset=false) => {
    setError("");
    void client.settings("set_defaults",{scope,kind,...reset ? {reset:key} : {changes:{[key]:value}}}).then(load).catch(e=>setError(errorText(e)));
  };
  const row = (key:keyof Defaults, title:string, control:ReactNode, tip?:string) => <TxRow title={tip
    ? <TxTooltip label={tip}><span className="sm-tip" tabIndex={0}>{title}</span></TxTooltip> : title}>
    <div className="sm-control">{scope==="workspace" && key in page.overrides && <TxButton size="sm" variant="quiet" onClick={()=>save(key,null,true)}>Reset to global</TxButton>}{control}</div>
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
  return <TxSettingsPage>{error && <TxAlert title="Settings could not be saved">{error}</TxAlert>}
    {kind==="general" ? <>
      <TxSettingsGroup title="Models for helpers">{model("childModelId","Subagents","Same as the chat or bot")}{model("summaryModelId","Summaries and memory","Same as the chat or bot")}</TxSettingsGroup>
      <TxSettingsGroup title="Context">{toggle("autoCompact","Compact automatically")}{d.autoCompact && select("compactAt","Compact at",[60,70,80,90].map(v=>[v,v+"%"]))}</TxSettingsGroup>
      <TxSettingsGroup title="Memory">{toggle("memoryEnabled","Use memory")}{d.memoryEnabled && select("memoryTokens","Always in context",[1000,2000,4000,8000].map(v=>[v,v.toLocaleString("en-US")+" tokens"]))}</TxSettingsGroup>
    </> : <>
      <TxSettingsGroup title={kind==="chat"?"New chats":"New bots"}>{model("modelId","Model","Choose a model")}
        {row("modelThinking","Thinking",<TxSelect aria-label="Thinking" className="sm-select" value={d.modelThinking[current?.id ?? ""] ?? current?.defaultLevel ?? ""} onChange={e=>current&&save("modelThinking",{...d.modelThinking,[current.id]:e.target.value})}>
          {current?.levels.map(v=><option key={v}>{v}</option>)}</TxSelect>)}
        {select("defaultPermission","Permission",[["read_only","Read only"],["auto","Auto"],["full","Full access"]],"Read only asks before changes. Auto asks before deleting your things. Python runs with your local account's permissions.")}
        {toggle("childrenEnabled","Subagents")}</TxSettingsGroup>
      {kind==="chat" ? <TxSettingsGroup title="Notifications">{select("chatNotification","When a chat finishes",[["never","Never"],["background","Only when TALYX is in the background"],["always","Always"]])}</TxSettingsGroup> : <>
        <TxSettingsGroup title="Notifications for new bots">{toggle("notifyNeedsYou","When it needs you")}{toggle("notifyResults","With results")}
          {select("notifyChannel","Also send to",[["","None"],...page.channels.map(c=>[c.id,c.name] as Option)])}</TxSettingsGroup>
        <TxSettingsGroup title="Work without you">{select("botApprovalMinutes","Approvals expire after",[[10,"10 minutes"],[30,"30 minutes"],[60,"1 hour"],[240,"4 hours"]])}
          {select("botRunMinutes","Longest run",[[30,"30 minutes"],[60,"1 hour"],[180,"3 hours"],[360,"6 hours"],[0,"No limit"]])}
          {select("botFailureLimit","Pause a bot after",[[2,"2 failures"],[3,"3 failures"],[5,"5 failures"],[0,"Never"]])}
          {select("botRoundLimit","Bot-to-bot turns",[50,100,200,500].map(v=>[v,v+" turns"]))}</TxSettingsGroup></>}
    </>}</TxSettingsPage>;
}
