import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  TxButton, TxContextMenu, TxDialog, TxIcon, TxIconButton, TxInput, TxMenu, TxSearch, useTxToast, type ModuleContext, type TxMenuItem,
} from "@talyx/sdk";
import { Composer } from "./Composer";
import { Items, Steps, Working, askOptions, type ItemActions, type PaneSection } from "./Items";
import { ScheduledPage } from "./Scheduled";
import { SidePane } from "./SidePane";
import { DragEdge, useDragWidth } from "./resize";
import type { ChatListItem, Choices, Command, Schedule } from "./model";
import { useAgentConversation, useAgentSettings, useConversations } from "../data/useAgent";
import { activity, chatChoicesUpdate, chatFromSnapshot, chatModelChoices, childSteps, newChatChoices } from "./live";
import type { AgentCard } from "../data/types";
import { useAgentNavigation } from "../navigation";
import { botRequest } from "../bot/api";
import { ChatRules } from "../panel/ChatRules";

/* The Agent panel, with a sidebar: chats on the left (in the title menu when the
   panel is narrow), the chosen chat in the middle, the message box under it,
   the overview rail on the right (Claude Code's session rail).
   Data comes from the module's private routes. */

/** "Now", "3m", "52m", "2h", "1d", "13d" chat list. */
export function age(seconds: number, now = Date.now() / 1000): string {
  const delta = now - seconds;
  if (delta < 60) return "Now";
  if (delta < 3600) return `${Math.floor(delta / 60)}m`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h`;
  return `${Math.floor(delta / 86400)}d`;
}

/** The list's groups: pinned first, then by day, like chat clients. */
function groupOf(item: ChatListItem): string {
  if (item.pinned) return "Pinned";
  const start = new Date(); start.setHours(0, 0, 0, 0);
  const today = start.getTime() / 1000;
  if (item.updatedAt >= today) return "Today";
  if (item.updatedAt >= today - 86400) return "Yesterday";
  if (item.updatedAt >= today - 7 * 86400) return "Previous 7 days";
  return "Older";
}
const GROUPS = ["Pinned", "Today", "Yesterday", "Previous 7 days", "Older"];

const RAIL_KEY = "talyx.agent.chatRail";

type Confirm = { title: string; text: string; action: string; run: () => void };

/** A name edited in place: Enter or leaving it keeps it, Esc puts it back. Focus is taken after the click that opened it has settled,
    so that click's own focus change doesn't count as leaving. */
function RenameInput({ value, className, onDone }: { value: string; className: string; onDone(value: string | null): void }) {
  const input = useRef<HTMLInputElement>(null);
  const ready = useRef(false);
  useEffect(() => { const timer = setTimeout(() => { input.current?.focus(); input.current?.select(); ready.current = true; }, 60); return () => clearTimeout(timer); }, []);
  return <TxInput ref={input} className={className} defaultValue={value} aria-label="Name"
    onBlur={event => { if (ready.current) onDone(event.target.value); }}
    onKeyDown={event => { if (event.key === "Enter") onDone(event.currentTarget.value); if (event.key === "Escape") { event.stopPropagation(); onDone(null); } }} />;
}

export function ChatPanel({ ctx, mode, initial, onSelect }: { ctx: ModuleContext; mode?: ReactNode; initial?: string; onSelect?(id: string | null): void }) {
  const toast = useTxToast();
  const [selected, setSelected] = useState<string | null>(null);
  const { client, snapshot, error, loadEarlier } = useAgentConversation(ctx, selected);
  useEffect(() => {
    if (!initial || initial === selected) return;
    let active = true;
    void client.conversation<string|null>("restore_selection",{conversationId:initial}).then(id => {
      if (active) { setSelected(id); if (id !== initial) onSelect?.(id); }
    }).catch(reason => { if (active) toast({title:String(reason),tone:"failed"}); });
    return () => { active=false; };
  },[client,initial]);
  const settings = useAgentSettings(ctx);
  const conversations = useConversations(ctx, client);
  const models = useMemo(() => settings.value ? chatModelChoices(settings.value) : [], [settings.value]);
  const noModels = !models.length;
  const list: ChatListItem[] = useMemo(() => (conversations.items ?? []).map(c => ({...c, activity:activity(c,c.activeTurn?.state === "failed")})), [conversations.items]);
  const [draft, setDraft] = useState<Choices>({model:"",thinking:"",permission:"auto",subagents:true,plan:false});
  useEffect(() => { if (settings.value) setDraft(newChatChoices(settings.value.defaults, models)); }, [settings.value, models]);
  const [workspaceName,setWorkspaceName] = useState("this workspace");
  const [view,setView] = useState<string|null>(null);
  const [rules,setRules] = useState(0);
  const [showRules,setShowRules] = useState(false);
  const failed = (reason: unknown) => toast({title:reason instanceof Error ? reason.message : String(reason),tone:"failed"});
  const run = (work: Promise<unknown>) => { void work.catch(failed); };
  useEffect(() => { const problem=error ?? settings.error ?? conversations.error; if(problem) failed(problem); },[error,settings.error,conversations.error]);
  useEffect(() => {
    let live=true;
    run(client.conversation<{view:string|null;name:string|null}>("composer").then(data => {if(live){setView(data.view);setWorkspaceName(data.name ?? "this workspace");}}));
    return () => {live=false;};
  },[client,selected]);
  useEffect(() => { if(selected) run(client.permissionRules(selected).then(rows=>setRules(rows.length))); else setRules(0); },[client,selected,snapshot?.watermark]);
  const commands: Command[] = [{name:"plan",detail:"Plan first",icon:"list",kind:"command"},{name:"compact",detail:"Summarize earlier messages",icon:"archive",kind:"command"},{name:"new",detail:"Start a new chat",icon:"compose",kind:"command"},
    ...(settings.value?.skills.items ?? []).map(s=>({name:s.name,detail:s.description,icon:"book" as const,kind:"skill" as const}))];
  const [listHidden, setListHidden] = useState(false);
  const [listView, setListView] = useState<"chats" | "archived">("chats");
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  // the chat panel's "Scheduled": every chat's schedule on one page, in the chat's place
  const [scheduledOpen, setScheduledOpen] = useState(false);
  const [search, setSearch] = useState<string | null>(null);
  const [matches,setMatches] = useState<string[]>([]);
  useEffect(()=>{
    let live=true;
    if(!search?.trim()){setMatches([]);return;}
    const timer=setTimeout(()=>run(client.conversation<string[]>("search",{query:search}).then(ids=>{if(live)setMatches(ids);})),150);
    return ()=>{live=false;clearTimeout(timer);};
  },[client,search]);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [menu, setMenu] = useState<{ id: string; x: number; y: number } | null>(null);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  // The summary opens and closes by its button (or a line in the stream that points into it) and stays as left, like
  // the chat panel's; switching chats keeps it. Remembered across visits (mockup: localStorage).
  const [pane, setPane] = useState<{ open: boolean; focus: { section: PaneSection; n: number } | null }>({ open: false, focus: null });
  const listWidth = useDragWidth("chat-list", 200, 420);
  const [agentView, setAgentView] = useState<string | null>(null);
  const [childCards,setChildCards] = useState<AgentCard[]>([]);
  const childCursor = useRef({id:"",seq:0,cards:new Map<string,AgentCard>()});
  const child = snapshot?.cards.find(c=>c.type==="child_run" && c.id===agentView);
  const detailCursor = child?.type==="child_run" ? child.detailCursor : 0;
  useEffect(()=>{
    if(!selected || !agentView){setChildCards([]);return;}
    let live=true;
    if(childCursor.current.id!==agentView)childCursor.current={id:agentView,seq:0,cards:new Map()};
    const current=childCursor.current;
    run((async()=>{
      let more=true;
      while(live && more){
        const page=await client.childRead(selected,agentView,current.seq);
        if(!live)return;
        for(const item of page.items){current.cards.set(item.card.id,item.card);current.seq=item.seq;}
        more=page.nextAfter!==null;
      }
      if(live)setChildCards([...current.cards.values()]);
    })());
    return ()=>{live=false;};
  },[selected,agentView,detailCursor,client]);
  const [editing, setEditing] = useState<string | null>(null);
  const [rewind, setRewind] = useState<{ id: string; text: string } | null>(null);
  const [away, setAway] = useState(false);
  const [width, setWidth] = useState(1200);
  const stream = useRef<HTMLDivElement>(null);
  const root = useRef<HTMLDivElement>(null);

  const current = snapshot && snapshot.conversation.id === selected ? chatFromSnapshot(snapshot,models) : null;
  const refreshSchedules = () => botRequest<Schedule[]>(ctx,"schedules").then(setSchedules);
  useEffect(()=>{run(refreshSchedules());},[ctx,scheduledOpen]);
  const choose = (id: string | null) => {
    setSelected(id); onSelect?.(id); setAgentView(null); setEditing(null); setScheduledOpen(false);
    setPane(value => ({ ...value, focus: null }));
  };
  useAgentNavigation(ctx,{select:choose,reveal:id=>{if(id) document.getElementById(id)?.scrollIntoView({block:"center"});}},
    current ? {kind:"conversation",id:current.id,label:current.title} : null);
  const openScheduled = () => { choose(null); setScheduledOpen(true); };
  const scheduled = new Set(schedules.map(schedule => schedule.chat));
  const showPane = (open: boolean, focus: { section: PaneSection; n: number } | null = null) => { localStorage.setItem(RAIL_KEY, open ? "1" : "0"); setPane({ open, focus }); };
  useLayoutEffect(() => setPane({ open: !noModels && (root.current?.clientWidth ?? 0) > 1000 && localStorage.getItem(RAIL_KEY) === "1", focus: null }), []);
  useLayoutEffect(() => { stream.current?.scrollTo({ top: stream.current.scrollHeight }); setAway(false); }, [selected, agentView]);
  // Narrowed past the point where the rail fits beside the chat: close it rather than let it cover the chat.
  useEffect(() => {
    const element = root.current!;
    let last = element.clientWidth;
    const observer = new ResizeObserver(() => {
      const next = element.clientWidth;
      if (last > 1000 && next <= 1000) setPane(value => ({ ...value, open: false }));
      last = next; setWidth(next);
    });
    observer.observe(element);
    setWidth(element.clientWidth);
    return () => observer.disconnect();
  }, []);
  const narrow = width < 480;

  const patch = (id: string, change: Partial<ChatListItem>) => run(client.update(id,change).then(conversations.refresh));
  const remove = (id: string) => run(client.delete(id).then(()=>{if(id===selected) choose(null);return conversations.refresh();}));
  const archive = (id: string, archived: boolean) => {
    patch(id, { archived, pinned: false });
    if (archived && id === selected) choose(null);
    toast({ title: archived ? "Archived" : "Restored", tone: "info" });
  };
  const askDelete = (item: ChatListItem) => setConfirm({ title: "Delete this chat?", text: `"${item.title}" and its messages are deleted. What it changed in TALYX stays.`,
    action: "Delete", run: () => remove(item.id) });
  const entryMenu = (item: ChatListItem): TxMenuItem[] => item.archived ? [
    { id: "restore", label: "Restore", icon: "undo", onSelect: () => archive(item.id, false) },
    { id: "delete", label: "Delete…", icon: "trash", tone: "danger", separated: true, onSelect: () => askDelete(item) },
  ] : [
    { id: "pin", label: item.pinned ? "Unpin" : "Pin", icon: "bookmark", onSelect: () => patch(item.id, { pinned: !item.pinned }) },
    { id: "rename", label: "Rename", icon: "edit", onSelect: () => setRenaming(`list:${item.id}`) },
    { id: "archive", label: "Archive", icon: "archive", onSelect: () => archive(item.id, true) },
    { id: "delete", label: "Delete…", icon: "trash", tone: "danger", separated: true, onSelect: () => askDelete(item) },
  ];

  const waiting = current?.items.find(item => (item.kind === "approval" || item.kind === "plan-ask") && item.state === "waiting");
  const decideApproval = (id:string, decision:"allow"|"always"|"deny", reason?:string) => {
    const card=snapshot?.cards.find(c=>c.id===id);
    if(card?.type==="approval") run(client.decide(id,card.version,decision==="always"?"allow_conversation":decision,reason));
  };
  const decidePlan = (id:string, accepted:boolean, reason?:string) => run(client.conversation("plan_decision",{conversationId:selected,cardId:id,accepted,...(reason && {reason})}));
  const stopTurn=()=>{if(selected) run(client.stop(selected));};
  const actions: ItemActions = {
    open: ref=>{if(ref.target) run(ctx.platform.panels.openTarget(ref.target)); else if(ref.panel) run(client.openPanel(ref.panel)); else if(ref.path) run(client.conversation("open_file",{conversationId:selected,path:ref.path})); else run(client.openReference(ref.id));},
    pane: section=>showPane(true,{section,n:(pane.focus?.n ?? 0)+1}),
    copy: text=>run(navigator.clipboard.writeText(text).then(()=>toast({title:"Copied",tone:"info"}))),
    fork: itemId=>run(client.conversation<{id:string}>("fork",{conversationId:selected,itemId}).then(c=>{choose(c.id);return conversations.refresh();})),
    failure: ()=>ctx.platform.settings.open("agent.models"), decide:decideApproval, plan:decidePlan,
    retry: id=>run(client.conversation("retry",{conversationId:selected,cardId:id})),
    undo:(id,undone)=>{const c=snapshot?.cards.find(card=>card.id===id);
      if(c?.type==="memory_saved") run(client.conversation("memory_decision",{conversationId:selected,cardId:id,direction:undone?"undo":"redo"}));
      else run(client.conversation("revert",{conversationId:selected,turnId:id.replace(/^changes_/,""),direction:undone?"undo":"redo"}));},
    editing,
    editFrom: id => setEditing(id),
    resend: (id, text) => {
      if (text === null) { setEditing(null); return; }
      const at = current!.items.findIndex(item => item.id === id);
      const later = current!.items.slice(at + 1).filter(item => item.kind === "changes" && !item.undone);
      if (later.length) setRewind({ id, text }); else rewindTo(id, text, false);
    },
  };
  /** Claude Code's rewind: the chat goes back to before the message, which is sent again as edited. */
  const rewindTo = (id: string, text: string, undoChanges: boolean) => {
    run(client.conversation<{id:string}>("rewrite",{conversationId:selected,itemId:id,text,undoChanges}).then(c=>choose(c.id)));
    setEditing(null); setRewind(null); setAway(false);
    requestAnimationFrame(() => stream.current?.scrollTo({ top: stream.current.scrollHeight }));
  };

  // Keys, after Claude Code's: Esc stops, the number keys answer the question card, Ctrl+N starts a chat.
  useEffect(() => {
    const keys = (event: KeyboardEvent) => {
      const typing = event.target instanceof HTMLElement && /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName);
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "n") { event.preventDefault(); choose(null); return; }
      if (event.key === "Escape" && current?.activity === "running" && !editing) { stopTurn(); return; }
      if (typing || !waiting || !/^[123]$/.test(event.key)) return;
      if (waiting.kind === "approval") { const pick = askOptions(waiting.canAlwaysAllow, "chat")[Number(event.key) - 1]; if (pick) decideApproval(waiting.id, pick.key); }
      else if (event.key !== "3") decidePlan(waiting.id, event.key === "1");
    };
    document.addEventListener("keydown", keys);
    return () => document.removeEventListener("keydown", keys);
  });

  const visible = useMemo(() => list.filter(item => (listView === "archived") === Boolean(item.archived)
    && (!search || item.title.toLowerCase().includes(search.toLowerCase()) || matches.includes(item.id))), [list, search, listView,matches]);
  const archivedCount = list.filter(item => item.archived).length;
  const running = current !== null && (current.activity === "running" || (current.activity === "approval" && current.items.some(item => item.kind === "work" && item.running)));
  const title = current ? current.title : scheduledOpen ? "Scheduled" : "New chat";
  const agent = agentView && current ? current.items.flatMap(item => item.kind === "agents" ? item.agents : []).find(item => item.id === agentView) ?? null : null;

  const send = (text:string, includeView=true, paths:string[]=[], uploads:File[]=[]) => { const work = (async()=>{
    if(text === "/new"){choose(null);return;}
    const id=selected ?? (await client.create(draft.model)).id;
    if(!selected) {await client.update(id,chatChoicesUpdate(draft));choose(id);}
    if(text==="/plan"){await client.update(id,{planMode:true});return;}
    const attachments=paths.length ? await client.conversation<string[]>("attach_paths",{conversationId:id,paths}) : [];
    for(const file of uploads) attachments.push((await client.upload(id,file)).id);
    await client.send(id,text,{includeView,attachments});
    await conversations.refresh();
  })(); work.catch(failed); return work; };
  const stop=(id:string|null)=>{if(selected) run(id ? client.childStop(selected,id) : client.stop(selected));};
  const entry = current ? list.find(item => item.id === current.id) : undefined;
  const chatMenu = [
    ...list.filter(item => !item.archived).map((item, index) => ({ id: item.id, label: item.title, hint: age(item.updatedAt), checked: item.id === selected, group: index === 0 ? "Chats" : undefined, onSelect: () => choose(item.id) })),
    { id: "new", label: "New chat", icon: "compose" as const, separated: true, onSelect: () => choose(null) },
    { id: "scheduled", label: "Scheduled", icon: "clock" as const, checked: scheduledOpen, onSelect: openScheduled },
  ];
  const headMenu: TxMenuItem[] = [
    ...narrow ? [{ id: "new", label: "New chat", icon: "compose" as const, onSelect: () => choose(null) },
      ...current ? [{ id: "details", label: pane.open ? "Hide summary" : "Show summary", icon: "columns" as const, onSelect: () => showPane(!pane.open) }] : []] : [],
    ...current && entry ? [
      { id: "pin", label: entry.pinned ? "Unpin" : "Pin", icon: "bookmark" as const, separated: narrow, onSelect: () => patch(entry.id, { pinned: !entry.pinned }) },
      { id: "rename", label: "Rename", icon: "edit" as const, onSelect: () => setRenaming(`head:${entry.id}`) },
      { id: "export", label: "Export as Markdown", icon: "download" as const, onSelect: () => run(client.conversation("export",{conversationId:entry.id}).then(()=>toast({title:"Markdown saved",tone:"info"}))) },
      { id: "archive", label: "Archive", icon: "archive" as const, onSelect: () => archive(entry.id, true) },
      { id: "delete", label: "Delete…", icon: "trash" as const, tone: "danger" as const, separated: true, onSelect: () => askDelete(entry) },
    ] : [],
  ];
  const rename = (id: string, value: string) => { if (value.trim()) patch(id, { title: value.trim() }); setRenaming(null); };
  const renameInput = (id: string, value: string, className: string) => <RenameInput className={className} value={value} onDone={next => next === null ? setRenaming(null) : rename(id, next)} />;
  const shownTitle = entry?.title ?? title;

  return <div ref={root} className="chat-panel" data-list-hidden={listHidden || undefined}>
    <aside className="chat-list" aria-label="Chats" style={listWidth.style("--chat-list-width")}>
      <DragEdge side="right" label="Resize the chat list" drag={listWidth} />
      {mode && <div className="chat-mode">{mode}</div>}
      {listView === "archived"
        ? <div className="chat-list-head"><TxIconButton size="sm" icon="arrow-left" label="Back to chats" onClick={() => setListView("chats")} /><span className="chat-list-heading">Archived</span></div>
        : <div className="chat-list-head">
          <TxButton variant="row" size="sm" className="chat-new" aria-current={(selected === null && !scheduledOpen) || undefined} onClick={() => choose(null)}><TxIcon name="compose" />New chat</TxButton>
          <TxIconButton size="sm" icon="search" label="Search chats" tone={search !== null ? "active" : "default"} onClick={() => setSearch(search === null ? "" : null)} />
          <TxIconButton size="sm" icon="sidebar" label="Hide chats" onClick={() => setListHidden(true)} />
        </div>}
      {listView === "chats" && <TxButton variant="row" size="sm" className="chat-nav" aria-current={scheduledOpen || undefined} onClick={openScheduled}>
        <TxIcon name="clock" /><span className="chat-nav-label">Scheduled</span>{schedules.some(item => item.on) && <span className="chat-meta">{schedules.filter(item => item.on).length}</span>}</TxButton>}
      {search !== null && listView === "chats" && <TxSearch className="chat-search" value={search} onValueChange={setSearch} placeholder="Search chats" autoFocus />}
      <div className="chat-list-items">
        {visible.length === 0 && <p className="chat-list-none">{listView === "archived" ? "No archived chats" : search ? "No chats match" : "No chats yet"}</p>}
        {(listView === "archived" ? ["Archived"] : GROUPS).map(group => {
          const rows = visible.filter(item => listView === "archived" || groupOf(item) === group);
          if (!rows.length) return null;
          return <Fragment key={group}>
            {listView === "chats" && <div className="chat-list-group">{group}</div>}
            {rows.map(item => renaming === `list:${item.id}`
              ? <div key={item.id} className="chat-entry chat-renaming">{renameInput(item.id, item.title, "chat-rename")}</div>
              : <div key={item.id} className="chat-entry">
                <TxButton variant="row" size="sm" className="chat-list-item" aria-current={item.id === selected || undefined} data-unread={item.unread || undefined}
                  onClick={() => choose(item.id)} onContextMenu={event => { event.preventDefault(); setMenu({ id: item.id, x: event.clientX, y: event.clientY }); }}>
                  <span className="chat-list-title">{item.title}</span>
                  {scheduled.has(item.id) && <TxIcon name="clock" className="chat-list-clock" aria-label="Scheduled" />}
                  <span className="chat-list-end">
                    {item.activity === "running" ? <TxIcon name="loader" className="chat-spin" />
                      : item.activity === "failed" ? <TxIcon name="alert" className="chat-failed-mark" aria-label="Failed" />
                      : item.activity === "approval" || item.unread ? <span className="chat-dot" data-tone={item.activity === "approval" ? "act" : undefined} />
                      : <span className="chat-list-age">{age(item.updatedAt)}</span>}
                  </span>
                </TxButton>
                <span className="chat-entry-more"><TxMenu label={item.title} align="end" items={entryMenu(item)} trigger={<TxIconButton size="sm" icon="more" label={`${item.title} actions`} />} /></span>
              </div>)}
          </Fragment>;
        })}
      </div>
      {listView === "chats" && archivedCount > 0 && <TxButton variant="row" size="sm" className="chat-archived-link" onClick={() => setListView("archived")}>
        <TxIcon name="archive" /><span>Archived</span><span className="chat-meta">{archivedCount}</span></TxButton>}
    </aside>
    <div className="chat-main">
      <div className="chat-head">
        {mode && <span className="chat-head-mode">{mode}</span>}
        <TxIconButton size="sm" icon="sidebar" label="Show chats" className="chat-show-list" onClick={() => setListHidden(false)} />
        {entry && renaming === `head:${entry.id}`
          ? renameInput(entry.id, entry.title, "chat-title-rename")
          : <>
            <TxMenu label="Chats" items={chatMenu} trigger={<TxButton variant="tool" size="sm" className="chat-title chat-title-menu"><span>{shownTitle}</span><TxIcon name="chevron-down" /></TxButton>} />
            {/* Claude Code: click the title to rename it */}
            {current ? <TxButton variant="tool" size="sm" className="chat-title chat-title-static" title="Rename" onClick={() => entry && setRenaming(`head:${entry.id}`)}><span>{shownTitle}</span></TxButton>
              : <span className="chat-title chat-title-static chat-title-plain">{title}</span>}
          </>}
        {headMenu.length > 0 && <TxMenu label="Chat" items={headMenu} trigger={<TxIconButton size="sm" icon="more" label="Chat actions" />} />}
        <span className="chat-spacer" />
        {!narrow && <TxIconButton size="sm" icon="compose" label="New chat" className="chat-head-new" onClick={() => choose(null)} />}
        {current && !narrow && <TxIconButton size="sm" icon="columns" label={pane.open ? "Hide summary" : "Show summary"} tone={pane.open ? "active" : "default"}
          onClick={() => showPane(!pane.open)} />}
      </div>
      <div className="chat-body">
        <div className="chat-center">
          {agent && current
            ? <div className="chat-stream" ref={stream}>
              {/* A subagent's own conversation, opened from the rail subagent threads */}
              <div className="chat-column chat-agent-view">
                <div className="chat-agent-bar">
                  <TxButton size="sm" variant="quiet" onClick={() => setAgentView(null)}><TxIcon name="arrow-left" />{current.title}</TxButton>
                </div>
                <div className="chat-agent-heading"><span className="chat-card-title">{agent.title}</span><span className="chat-meta">Subagent · {agent.model} · {agent.thinking} · {agent.cost}</span></div>
                <div className="chat-user"><div className="chat-bubble">{agent.title}</div></div>
                {childCards.length ? <div className="chat-work"><Steps steps={childSteps(childCards)} /></div> : agent.state==="queued" && <div className="chat-line">Waiting to start</div>}
                {agent.state === "running" && child && <Working since={Math.max(0,Math.round(Date.now()/1000-child.createdAt))} />}
                {agent.state === "done" && <div className="chat-line">{agent.now}</div>}
                {agent.state === "failed" && <div className="chat-line chat-line-failed">{agent.now}</div>}
                {agent.state === "running" && <div><TxButton size="sm" onClick={() => stop(agent.id)}><TxIcon name="stop" />Stop this subagent</TxButton></div>}
              </div>
            </div>
            : current
            ? <div className="chat-stream" ref={stream} onScroll={event => { const element = event.currentTarget; setAway(element.scrollHeight - element.scrollTop - element.clientHeight > 120); }}>
              <div className="chat-column" key={current.id}>
                {snapshot?.nextBefore != null && <TxButton variant="quiet" size="sm" onClick={loadEarlier}>Earlier messages</TxButton>}
                <Items items={current.items} actions={actions} />
                {/* Until the turn's first step arrives, its work has no line of its own yet */}
                {current.activity === "running" && !current.items.some(item => item.kind === "work" && item.running) && <Working since={0} />}
              </div>
            </div>
            : scheduledOpen
            ? <ScheduledPage schedules={schedules} titles={Object.fromEntries(list.map(item => [item.id, item.title]))}
              onToggle={(id,on) => run(botRequest(ctx,"toggle_routine",{id,active:on}).then(refreshSchedules))} onOpen={id => choose(id)} />
            : <div className="chat-stream chat-start"><div className="chat-column">
              {noModels
                ? <div className="chat-connect">
                  <TxIcon name="messages" className="chat-start-icon" />
                  <p className="chat-start-title">Connect a model to start</p>
                  <TxButton onClick={() => ctx.platform.settings.open("agent.models")}>Add a model</TxButton>
                </div>
                : <div className="chat-connect">
                  <TxIcon name="messages" className="chat-start-icon" />
                  <p className="chat-start-title">What should we do in <span className="chat-start-place">{workspaceName}</span>?</p>
                </div>}
            </div></div>}
          {!noModels && !agent && !scheduledOpen && <div className="chat-column chat-foot">
            {away && <TxIconButton className="chat-jump" variant="outline" icon="arrow-down" label="Jump to latest"
              onClick={() => stream.current!.scrollTo({ top: stream.current!.scrollHeight, behavior: "smooth" })} />}
            <Composer key={current?.id ?? "new"} choices={current ? current.choices : draft} context={current?.context} models={models} commands={commands} findFiles={client.findFiles}
              notice={waiting ? "Sending a message skips the question above" : undefined}
              view={view} running={running} queued={current ? current.queued : []} allowedCount={rules}
              onAllowed={selected ? () => setShowRules(true) : undefined}
              onChoices={changes => current ? run(client.update(current.id,chatChoicesUpdate(changes))) : setDraft({...draft,...changes})}
              onSend={send} onQueue={text=>selected && run(client.conversation("queue",{conversationId:selected,text}))}
              onQueued={queued=>selected && run(client.conversation("queue",{conversationId:selected,queued}))}
              onSteer={text=>send(text)}
              onStop={stopTurn}
              onManageModels={() => ctx.platform.settings.open("agent.models")} />
          </div>}
        </div>
        {current && pane.open && <SidePane key={current.id} chat={current} focus={pane.focus} actions={actions} agent={agentView} onAgent={id => setAgentView(id)} />}
      </div>
    </div>
    {menu && <TxContextMenu open onOpenChange={open => { if (!open) setMenu(null); }} point={{ x: menu.x, y: menu.y }} label="Chat"
      items={entryMenu(list.find(item => item.id === menu.id)!)} />}
    {showRules && selected && <ChatRules client={client} conversationId={selected} onClose={()=>{setShowRules(false);run(client.permissionRules(selected).then(rows=>setRules(rows.length)));}}/>}
    <TxDialog open={confirm !== null} onOpenChange={open => { if (!open) setConfirm(null); }} title={confirm?.title ?? ""}
      footer={<><TxButton onClick={() => setConfirm(null)}>Cancel</TxButton><TxButton variant="primary" onClick={() => { confirm!.run(); setConfirm(null); }}>{confirm?.action}</TxButton></>}>
      <p className="chat-dialog-text">{confirm?.text}</p>
    </TxDialog>
    <TxDialog open={rewind !== null} onOpenChange={open => { if (!open) setRewind(null); }} title="Also undo the changes made after this message?"
      footer={<><TxButton onClick={() => rewindTo(rewind!.id, rewind!.text, false)}>Keep changes</TxButton>
        <TxButton variant="primary" onClick={() => rewindTo(rewind!.id, rewind!.text, true)}>Undo changes too</TxButton></>}>
      {rewind && current && <ul className="chat-dialog-list">{current.items.slice(current.items.findIndex(item => item.id === rewind.id) + 1)
        .flatMap(item => item.kind === "changes" && !item.undone ? item.changes : []).map(change => <li key={change.id}><span>{change.label}</span><span className="chat-meta">{change.what}</span></li>)}</ul>}
    </TxDialog>
  </div>;
}
