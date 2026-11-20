import { useEffect, useMemo, useState } from "react";
import { TxAlert, TxButton, TxDialog, TxIcon, TxIconButton, TxInput, TxRow, TxSegmented, TxSettingsGroup, TxSettingsPage, TxTextarea, type ModuleContext } from "@talyx/sdk";
import { AgentClient } from "../data/client";
import type { AgentMemory } from "../data/types";
import { settingsScope } from "./DefaultsPage";
import { errorText } from "./shared";

export function Personalization({ctx}:{ctx:ModuleContext}) {
  const client = useMemo(()=>new AgentClient(ctx),[ctx]), scope = settingsScope(ctx);
  const [text,setText] = useState(""), [saved,setSaved] = useState<string|null>(null), [notes,setNotes] = useState<AgentMemory[]>([]);
  const [editing,setEditing] = useState<string|null>(null), [body,setBody] = useState(""), [error,setError] = useState("");
  const [remove,setRemove] = useState<AgentMemory[] | null>(null);
  const readNotes = async () => {
    const all:AgentMemory[] = [];
    let offset:number|null = 0;
    while(offset!==null){const page=await client.memoryList({status:"active",offset});all.push(...page.items);offset=page.nextOffset;}
    setNotes(all);
  };
  const run = (value:Promise<unknown>) => {setError("");void value.catch(e=>setError(errorText(e)));};
  useEffect(()=>{run(client.settings<string>("instructions",{scope}).then(v=>{setSaved(v);setText(v);}));run(readNotes());},[client,scope]);
  const list = (title:string, items:AgentMemory[]) => <TxSettingsGroup title={title} action={items.length>0 && <TxButton size="sm" variant="quiet" onClick={()=>setRemove(items)}>Delete all…</TxButton>}>
    {items.map(note=><div key={note.id} className="sm-note">{editing===note.id
      ? <TxTextarea rows={2} autoFocus className="sm-note-edit" value={body} onChange={e=>setBody(e.target.value)}
          onBlur={()=>{setEditing(null);if(body!==note.body)run(client.memoryEdit(note.id,note.revision,body).then(readNotes));}}/>
      : <div className="sm-note-copy"><span className="sm-note-text">{note.body}</span><span className="sm-note-from">{note.sourceKind} · {new Date(note.createdAt*1000).toLocaleDateString()}</span></div>}
      <TxIconButton size="sm" icon="edit" label="Edit" onClick={()=>{setEditing(note.id);setBody(note.body);}}/>
      <TxIconButton size="sm" icon="trash" label="Delete" onClick={()=>setRemove([note])}/></div>)}</TxSettingsGroup>;
  const currentId = ctx.workspace.identity.get()?.workspaceId;
  const [workspace,setWorkspace] = useState<string|null>(null);
  useEffect(()=>{let live=true;
    if(currentId)run(ctx.actions.call<{workspaceId:string;name:string|null}[]>("platform.workspace.list_open",{}).then(rows=>{
      if(live)setWorkspace(rows.find(row=>row.workspaceId===currentId)?.name ?? null);
    }));
    return ()=>{live=false;};
  },[ctx,currentId]);
  const instructions = scope==="workspace" ? "Instructions for "+(workspace ?? "this workspace") : "Instructions";
  return <TxSettingsPage>{error&&<TxAlert title="Personalization could not be saved">{error}</TxAlert>}
    <section className="tx-settings-group"><header className="tx-settings-group-head"><div className="tx-settings-group-copy"><h4 className="tx-settings-group-title">{instructions}</h4></div></header>
      <TxTextarea rows={8} className="sm-instructions" aria-label={instructions} disabled={saved===null} value={text}
        onChange={e=>setText(e.target.value)} onBlur={()=>{if(text!==saved)run(client.settings<string>("instructions",{scope,text}).then(setSaved));}}/></section>
    {scope==="global"&&list("About you",notes.filter(n=>n.workspaceId===null))}
    {list(scope==="workspace"?"Remembered in this workspace":"This workspace"+(workspace?" · "+workspace:""),notes.filter(n=>n.workspaceId!==null))}
    <TxDialog open={remove!==null} onOpenChange={v=>{if(!v)setRemove(null);}} title="Delete these memories?" footer={<>
      <TxButton onClick={()=>setRemove(null)}>Cancel</TxButton><TxButton variant="primary" onClick={()=>{const rows=remove??[];setRemove(null);
        run((async()=>{try{for(const n of rows)await client.memoryEdit(n.id,n.revision,n.body,true);}finally{await readNotes();}})());}}>Delete</TxButton></>}/>
  </TxSettingsPage>;
}

type Folder = {id:string; path:string; writable:boolean};
type Folders = {folders:Folder[];workspaceFolder:string|null};
export function Knowledge({ctx}:{ctx:ModuleContext}) {
  const client=useMemo(()=>new AgentClient(ctx),[ctx]);
  const [data,setData]=useState<Folders>({folders:[],workspaceFolder:null}),[adding,setAdding]=useState(false),[path,setPath]=useState(""),[error,setError]=useState("");
  const request=(op:string,args={})=>client.settings<Folders>(op,args).then(v=>{setData(v);setError("");}).catch(e=>setError(errorText(e)));
  useEffect(()=>{void request("folders");},[client]);
  return <TxSettingsPage>{error&&<TxAlert title="Knowledge folders">{error}</TxAlert>}
    <TxSettingsGroup title="Folders" action={<TxButton size="sm" variant="primary" onClick={()=>{setPath("");setAdding(true);}}><TxIcon name="plus"/>Add folder</TxButton>}>
      {data.folders.map(f=><TxRow key={f.id} icon="folder" title={<span className="sm-path">{f.path}</span>}>
        <div className="sm-control"><TxSegmented label={f.path+" access"} size="sm" value={f.writable?"write":"read"}
          onChange={v=>void request("folder_access",{id:f.id,writable:v==="write"})} options={[{value:"read",label:"Read only"},{value:"write",label:"Read and write"}]}/>
          <TxIconButton size="sm" icon="trash" label={"Remove "+f.path} onClick={()=>void request("unbind_folder",{id:f.id})}/></div></TxRow>)}</TxSettingsGroup>
    {data.workspaceFolder && <TxSettingsGroup title="This workspace"><TxRow icon="folder" title={<span className="sm-path">{data.workspaceFolder}</span>} detail="Read and write">
      <TxButton size="sm" onClick={()=>void request("open_folder")}>Open</TxButton></TxRow></TxSettingsGroup>}
    <TxDialog open={adding} onOpenChange={setAdding} title="Add folder" footer={<>
      <TxButton onClick={()=>setAdding(false)}>Cancel</TxButton><TxButton variant="primary" disabled={!path.trim()} onClick={()=>{setAdding(false);void request("bind_folder",{path:path.trim(),writable:false});}}>Add folder</TxButton></>}>
      <TxInput aria-label="Folder path" autoFocus value={path} onChange={e=>setPath(e.target.value)} placeholder="Folder path"/></TxDialog>
  </TxSettingsPage>;
}
