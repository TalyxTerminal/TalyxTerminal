import { t } from "../i18n";
import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  TxButton, TxDialog, TxIcon, TxIconButton, TxInput, TxPopover, TxProgress, TxRow, TxSearch, TxSelect, TxSettingsGroup, TxSwitch, TxTextarea,
} from "@talyx/sdk";
import { ModelButton, PermissionButton } from "../chat/Composer";

import { DragEdge, useDragWidth } from "../chat/resize";
import type { AgentMemory } from "../data/types";
import { useRailActions } from "./api";
import { Avatar } from "./Avatar";
import { BotPicker, LookPicker } from "./Create";
import { WakeIcon, wakeText } from "./wake";
import { displayTime } from "./model";
import type { Bot, Effect, Group, Outcome, Routine } from "./model";

/* The rail beside a bot's chat, copied from Grok Bot's: a stack of quiet
   sections (Routines with "+") and pages with a back arrow (Settings, a
   Routine). Settings opens from the bot's name in the head or its "…" menu
   in the sidebar; there is no gear here, which would read as TALYX's own
   settings. TALYX adds Runs and This month to the first page, and skills,
   knowledge, memory, always allowed and the spending limit to Settings.
   Its left edge drags to make it wider, and the width is kept. */

export type RailPage =
  | { kind: "overview" }
  | { kind: "settings"; bot: string }
  | { kind: "routine"; bot: string; routine: string }
  | { kind: "allowed"; bot: string }
  | { kind: "runs"; bot: string };

const OUTCOME: Record<Outcome, { icon: Parameters<typeof TxIcon>[0]["name"]; label: string }> = {
  done: { icon: "check", label: t("Done") }, quiet: { icon: "minus", label: t("Nothing to report") }, running: { icon: "loader", label: t("Running") },
  waiting: { icon: "circle", label: t("Waiting for you") }, failed: { icon: "x-circle", label: t("Failed") }, stopped: { icon: "stop", label: t("Stopped") },
  skipped: { icon: "pause", label: t("Skipped: paused") },
};

const money = (value: number, currency: string) => `${value.toFixed(2)} ${currency}`;
const byCurrency = (spend: { amounts: Record<string, string>; unpriced: boolean }) => {
  const values = Object.entries(spend.amounts).map(([currency, value]) => money(Number(value), currency));
  if (spend.unpriced) values.push(t("Unpriced usage"));
  return values.join(", ");
};

export function Rail({ page, bot, group, bots, onPage, onBot, onGroup, onPrefill }: {
  page: RailPage; bot: Bot | null; group: Group | null; bots: Map<string, Bot>;
  onPage: (page: RailPage) => void; onBot: (id: string, change: Partial<Bot>) => void;
  onGroup: (id: string, change: Partial<Group>) => void;
  /** Grok Bot makes and edits routines by talking: "+" and Edit put a sentence in the message box. */
  onPrefill: (text: string) => void;
}) {
  const subject = page.kind === "overview" ? bot : bots.get(page.bot)!;
  const back = page.kind === "overview" ? null
    : page.kind === "settings" ? { kind: "overview" as const }
    : { kind: "settings" as const, bot: page.bot };
  const title = { overview: "", settings: t("Settings"), routine: t("Routine"), allowed: t("Always allowed"), runs: t("Runs") }[page.kind];
  const drag = useDragWidth("bot-rail", 260, 560);
  return <aside className="bot-rail" aria-label={t("Details")} style={{width:drag.width ?? undefined}}>
    <DragEdge side="left" label={t("Resize the details")} drag={drag} />
    {back && <div className="bot-rail-head">
      <TxIconButton size="sm" icon="arrow-left" label={t("Back")} onClick={() => onPage(page.kind === "runs" || page.kind === "routine" || (page.kind === "settings" && group) ? { kind: "overview" } : back)} />
      <span className="bot-rail-title">{title}</span>
      <span className="bot-rail-gap" />
    </div>}
    <div className="bot-rail-body">
      {page.kind === "overview" && bot && <Overview bot={bot} onPage={onPage} onBot={change => onBot(bot.id, change)} onPrefill={onPrefill} />}
      {page.kind === "overview" && group && <Members group={group} bots={bots} onPage={onPage} onGroup={change => onGroup(group.id, change)} />}
      {page.kind === "settings" && subject && <Settings bot={subject} onPage={onPage} onBot={change => onBot(subject.id, change)} />}

      {page.kind === "routine" && subject && <RoutinePage bot={subject} routine={subject.routines.find(item => item.id === page.routine)!}
        onBot={change => onBot(subject.id, change)} onPrefill={onPrefill} onDeleted={() => onPage({ kind: "overview" })} />}
      {page.kind === "allowed" && subject && <Allowed bot={subject} onBot={change => onBot(subject.id, change)} />}
      {page.kind === "runs" && subject && <RunList bot={subject} all />}
    </div>
  </aside>;
}

function Section({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return <section className="bot-rail-section">
    <div className="bot-rail-section-head"><span className="bot-section-title">{title}</span>{action}</div>
    {children}
  </section>;
}

function Overview({ bot, onPage, onBot, onPrefill }: { bot: Bot; onPage: (page: RailPage) => void; onBot: (change: Partial<Bot>) => void; onPrefill: (text: string) => void }) {
  return <>
    {/* What the bot is (duty, model, permission, budget) is one click away at the top, as in Grok Bot's details */}
    <TxButton variant="row" className="bot-line bot-settings-link" onClick={() => onPage({ kind: "settings", bot: bot.id })}>
      <TxIcon name="settings" /><span className="bot-line-copy"><span className="bot-line-title">{t("Settings")}</span>
        <span className="bot-line-sub">{bot.duty || t("No duty yet")}</span></span><TxIcon name="chevron" /></TxButton>
    <Section title={t("Routines")} action={<TxIconButton size="sm" icon="plus" label={t("New routine")} onClick={() => onPrefill(t("Set up a routine to "))} />}>
      {bot.routines.length === 0 && <p className="bot-rail-none">{t("No routines")}</p>}
      {/* Grok Bot's routine rows: a switch on each, "Paused" when off */}
      {bot.routines.map(routine => <div key={routine.id} className="bot-routine-row" data-off={!routine.active || bot.state === "paused" || undefined}>
        <TxButton variant="row" className="bot-line" onClick={() => onPage({ kind: "routine", bot: bot.id, routine: routine.id })}>
          <TxIcon name={routine.kind === "schedule" ? "clock" : "bolt"} />
          <span className="bot-line-copy"><span className="bot-line-title">{routine.name}</span>
            <span className="bot-line-sub">{!routine.active ? t("Paused") : bot.state === "paused" ? t("Paused with the bot") : routine.when[0]}</span></span>
        </TxButton>
        {/* While the bot is paused its routines are held too: the switch shows them off and waits for Resume */}
        <TxSwitch hideLabel label={t("{name} on", { name: routine.name })} checked={routine.active && bot.state !== "paused"} disabled={bot.state === "paused"}
          onCheckedChange={active => onBot({ routines: bot.routines.map(item => item.id === routine.id ? { ...item, active } : item) })} />
      </div>)}
    </Section>
    <Section title={t("Runs")} action={bot.runs.length > 4 ? <TxButton size="sm" variant="quiet" onClick={() => onPage({ kind: "runs", bot: bot.id })}>{t("Show all")}</TxButton> : undefined}>
      <RunList bot={bot} />
    </Section>
    <Section title={t("This month")}>
      <div className="bot-spend">
        <span className="bot-spend-total">{byCurrency(bot.spend) || money(0, bot.spend.currency)}</span>
        {bot.spend.limit !== null && <span className="bot-line-sub">{t("of {amount} limit", { amount: money(bot.spend.limit, bot.spend.currency) })}</span>}
      </div>
      {bot.spend.limit !== null && <TxProgress value={bot.spend.limit ? bot.spend.month / bot.spend.limit * 100 : 100} className="bot-meter" />}
      <div className="bot-spend-parts">{bot.spend.parts.map(part => <div key={part.label} className="bot-spend-part">
        <span className="bot-line-title">{part.label}</span><span className="bot-line-sub">{part.runs === 1 ? t("1 run") : t("{count} runs", { count: part.runs })}</span><span className="bot-spend-amount">{byCurrency(part) || "—"}</span>
      </div>)}</div>
    </Section>
  </>;
}

/** Runs, newest first. A run that said nothing but changed something or remembered something says so on its row
    ("2 changes · 1 memory"); the row opens to those things, each with Undo, like the changes card in a chat. */
function RunList({ bot, all = false }: { bot: Bot; all?: boolean }) {
  const [open, setOpen] = useState<string | null>(null);
  const {undo} = useRailActions();
  if (!bot.runs.length) return <p className="bot-rail-none">{t("No runs yet")}</p>;
  return <div className="bot-runs">{(all ? bot.runs : bot.runs.slice(0, 4)).map(run => {
    const body = <>
      <TxIcon name={OUTCOME[run.outcome].icon} className="bot-run-mark" />
      <span className="bot-line-copy">
        <span className="bot-line-title">{displayTime(run.at)}</span>
        <span className="bot-line-sub bot-run-wake"><WakeIcon wake={run.wake} />{wakeText(run.wake)}</span>
        {run.notice && <span className="bot-line-sub bot-run-notice">{t("Notification not sent:")} {run.notice.message}</span>}
        {run.effects?.length ? <span className="bot-run-left">{left(run.effects)}<TxIcon name={open === run.id ? "chevron-down" : "chevron"} /></span> : null}
      </span>
      <span className="bot-run-figures"><span>{run.cost || "—"}</span><span className="bot-line-sub">{run.outcome === "skipped" ? "" : run.duration}</span></span>
    </>;
    return <div key={run.id} className="bot-run-entry">
      {run.effects?.length
        ? <TxButton variant="row" className="bot-run bot-run-open" data-outcome={run.outcome} title={OUTCOME[run.outcome].label} aria-expanded={open === run.id}
          onClick={() => setOpen(open === run.id ? null : run.id)}>{body}</TxButton>
        : <div className="bot-run" data-outcome={run.outcome} title={OUTCOME[run.outcome].label}>{body}</div>}
      {open === run.id && run.effects && <div className="bot-effects">{run.effects.map(effect => <div key={effect.id} className="bot-effect" data-undone={effect.undone || undefined}>
        <span className="bot-line-copy"><span className="bot-line-title bot-effect-label">{effect.kind === "memory" ? t("Remembered: {label}", { label: effect.label }) : effect.label}</span>
          {effect.what && <span className="bot-line-sub">{effect.what}</span>}</span>
      </div>)}
        {run.effects.some(e=>e.reversible) && <TxButton size="sm" variant="quiet" onClick={()=>undo(run.id,run.effects!.filter(e=>e.reversible).every(e=>e.undone)?"redo":"undo")}>{run.effects.filter(e=>e.reversible).every(e=>e.undone)?t("Redo run"):t("Undo run")}</TxButton>}
      </div>}
    </div>;
  })}</div>;
}

/** "2 changes · 1 memory": what a quiet run left, still standing. */
function left(effects: Effect[]): string {
  const changes = effects.filter(item => item.kind === "change" && !item.undone).length, memory = effects.filter(item => item.kind === "memory" && !item.undone).length;
  return [changes && (changes === 1 ? t("1 change") : t("{count} changes", { count: changes })), memory && (memory === 1 ? t("1 memory") : t("{count} memories", { count: memory }))].filter(Boolean).join(" · ") || t("All undone");
}
/** A group's members, after Grok Bot's roster: each opens its settings; remove on hover; Add member is the same "To:" picker as a new group. */
function Members({ group, bots, onPage, onGroup }: { group: Group; bots: Map<string, Bot>; onPage: (page: RailPage) => void; onGroup: (change: Partial<Group>) => void }) {
  const [adding, setAdding] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  return <Section title={t("Members")}>
    {group.members.map(id => bots.get(id)!).map(member => <div key={member.id} className="bot-member-row">
      <TxButton variant="row" className="bot-line bot-member" onClick={() => onPage({ kind: "settings", bot: member.id })}>
        <Avatar look={member.look} size="md" state={member.state} />
        <span className="bot-line-copy"><span className="bot-line-title">{member.name}</span><span className="bot-line-sub">{member.intro}</span></span>
      </TxButton>
      {group.members.length > 2 && <span className="bot-member-remove"><TxIconButton size="sm" icon="close" label={t("Remove {name} from the group", { name: member.name })}
        onClick={() => onGroup({ members: group.members.filter(item => item !== member.id) })} /></span>}
    </div>)}
    <TxButton variant="row" className="bot-line bot-add" onClick={() => { setPicked([]); setAdding(true); }}><TxIcon name="plus" /><span className="bot-line-sub">{t("Add member")}</span></TxButton>
    <TxDialog open={adding} onOpenChange={setAdding} title={t("Add to {name}", { name: group.name })}
      footer={<><TxButton onClick={() => setAdding(false)}>{t("Cancel")}</TxButton>
        <TxButton variant="primary" disabled={!picked.length} onClick={() => { onGroup({ members: [...group.members, ...picked] }); setAdding(false); }}>{t("Add")}</TxButton></>}>
      <BotPicker bots={[...bots.values()].filter(bot => !bot.hidden && !group.members.includes(bot.id))} picked={picked} onPicked={setPicked} max={6 - group.members.length} autoFocus />
    </TxDialog>
  </Section>;
}


/** Grok Bot's Settings page: the picture, name, intro and duty, then what the bot is given (skills and knowledge, picked
    the way Grok Bot adds integrations), how it runs (model and thinking in the message box's own button, permission,
    always allowed), what it remembers, and the rest folded under More. Routines stay on the first page, as in Grok Bot. */
function Settings({ bot, onPage, onBot }: { bot: Bot; onPage: (page: RailPage) => void; onBot: (change: Partial<Bot>) => void }) {
  const [picking, setPicking] = useState(false);
  const [adding, setAdding] = useState<"skills" | "knowledge" | null>(null);
  const [more, setMore] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const cancelled = useRef(false);
  const [notes, setNotes] = useState<AgentMemory[]>([]);
  const {models,library,upload,manageModels,loadLibrary,memories,editMemory} = useRailActions();
  const chooser=useRef<HTMLInputElement>(null);
  useEffect(loadLibrary, []);
  // Its own memories, read again whenever the bot's listing changes (a run may have remembered something).
  const readNotes = () => { void memories(bot.id).then(items => { if (items) setNotes(items); }); };
  useEffect(readNotes, [bot]);
  const skills=bot.skills.map(name=>library.skills.find(s=>s.name===name) ?? {name,detail:""});
  return <div className="bot-settings">
    <div className="bot-settings-picture">
      <TxPopover open={picking} onOpenChange={setPicking} label={t("Picture")} align="start" className="bot-look-pop"
        trigger={<TxButton variant="quiet" className="bot-picture" aria-label={t("Change picture")}>
          <Avatar look={bot.look} size="xl" state={bot.state} /><span className="bot-picture-edit"><TxIcon name="edit" /></span>
        </TxButton>}>
        <LookPicker look={bot.look} onLook={look => onBot({ look })} />
      </TxPopover>
    </div>
    <label className="bot-field"><span>{t("Name")}</span><TxInput value={bot.name} onChange={event => onBot({ name: event.target.value })} /></label>
    <label className="bot-field"><span>{t("Intro for other bots")}</span><TxInput value={bot.intro} onChange={event => onBot({ intro: event.target.value })} /></label>
    <label className="bot-field"><span>{t("Duty")}</span><TxTextarea rows={7} value={bot.duty} onChange={event => onBot({ duty: event.target.value })} /></label>

    <div className="bot-field"><span>{t("Skills")}</span>
      <div className="bot-picks">
        {skills.map(skill => <div key={skill.name} className="bot-pick">
          <span className="bot-line-copy"><span className="bot-line-title">{skill.name}</span>{skill.detail && <span className="bot-line-sub">{skill.detail}</span>}</span>
          <span className="bot-pick-remove"><TxIconButton size="sm" icon="close" label={t("Remove {name}", { name: skill.name })} onClick={() => onBot({ skills: bot.skills.filter(item => item !== skill.name) })} /></span>
        </div>)}
        <TxButton variant="row" size="sm" className="bot-when-add" onClick={() => setAdding("skills")}><TxIcon name="plus" />{t("Add skills")}</TxButton>
      </div>
    </div>

    <div className="bot-field"><span>{t("Knowledge")}</span>
      <div className="bot-picks">
        {bot.knowledge.map(path => {
          const kind = library.knowledge.find(item => item.path === path)?.kind ?? "file";
          return <div key={path} className="bot-pick bot-pick-path">
            <TxIcon name={kind === "folder" ? "folder" : "file"} />
            <span className="bot-line-copy"><span className="bot-line-title">{leaf(path)}</span><span className="bot-line-sub">{parent(path)}</span></span>
            <span className="bot-pick-remove"><TxIconButton size="sm" icon="close" label={t("Remove {leaf}", { leaf: leaf(path) })} onClick={() => onBot({ knowledge: bot.knowledge.filter(item => item !== path) })} /></span>
          </div>;
        })}
        <div className="bot-picks-foot">
          <TxButton variant="row" size="sm" className="bot-when-add" onClick={() => setAdding("knowledge")}><TxIcon name="plus" />{t("Add from Knowledge")}</TxButton>
          <TxInput ref={chooser} type="file" hidden onChange={event=>{const file=event.target.files?.[0];if(file)upload(bot.id,file);}}/>
          <TxButton variant="row" size="sm" className="bot-when-add bot-upload-add" onClick={()=>chooser.current?.click()}><TxIcon name="paperclip" />{t("Upload…")}</TxButton>
        </div>
      </div>
    </div>

    <TxSettingsGroup title={t("Model and permission")} className="bot-card">
      <TxRow title={t("Model")}><ModelButton models={models} choices={{ model: bot.model, thinking: bot.thinking, subagents: bot.subagents ?? true }}
        onChoices={change => onBot({ ...change.model ? { model: change.model } : {}, ...change.thinking ? { thinking: change.thinking } : {}, ...change.subagents !== undefined ? { subagents: change.subagents } : {} })}
        onManageModels={manageModels} /></TxRow>
      <TxRow title={t("Permission")}><PermissionButton value={bot.permission} onChange={permission => onBot({ permission })} /></TxRow>
      <Drill title={t("Always allowed")} value={bot.allowed.length ? String(bot.allowed.length) : t("None")} onClick={() => onPage({ kind: "allowed", bot: bot.id })} />
    </TxSettingsGroup>

    <TxSettingsGroup title={t("Memory")} className="bot-card" action={<TxSwitch hideLabel label={t("Memory")} checked={bot.memoryOn} onCheckedChange={memoryOn => onBot({ memoryOn })} />}>
      {bot.memoryOn && !notes.length && <p className="bot-rail-none bot-memory-none">{t("Nothing remembered yet")}</p>}
      {bot.memoryOn && notes.map(note => editing === note.id
        ? <div key={note.id} className="bot-memory-edit">
          <TxTextarea rows={2} autoFocus value={draft} aria-label={t("Edit memory")} onChange={event => setDraft(event.target.value)}
            onBlur={() => { setEditing(null); if (!cancelled.current && draft !== note.body) void editMemory(note, draft).then(readNotes); cancelled.current = false; }}
            onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); event.currentTarget.blur(); } if (event.key === "Escape") { cancelled.current = true; event.currentTarget.blur(); } }} />
        </div>
        : <TxRow key={note.id} title={<span className="bot-note-text">{note.body}</span>} detail={displayTime(new Date(note.createdAt * 1000).toISOString())}>
          <span className="bot-memory-actions">
            <TxIconButton size="sm" icon="edit" label={t("Edit")} onClick={() => { setDraft(note.body); setEditing(note.id); }} />
            <TxIconButton size="sm" icon="trash" label={t("Forget this")} onClick={() => void editMemory(note, note.body, true).then(readNotes)} />
          </span>
        </TxRow>)}
    </TxSettingsGroup>

    <div className="bot-more-settings">
      <TxButton variant="quiet" size="sm" className="bot-more-toggle" aria-expanded={more} onClick={() => setMore(!more)}>{t("More")}<TxIcon name={more ? "chevron-down" : "chevron"} /></TxButton>
      {more && <TxSettingsGroup className="bot-card">
        <TxRow title={t("Notify when it needs you")}><TxSwitch hideLabel label={t("Notify when it needs you")} checked={bot.notify.needsYou} onCheckedChange={needsYou => onBot({ notify: { ...bot.notify, needsYou } })} /></TxRow>
        <TxRow title={t("Notify with results")}><TxSwitch hideLabel label={t("Notify with results")} checked={bot.notify.results} onCheckedChange={results => onBot({ notify: { ...bot.notify, results } })} /></TxRow>
        <TxRow title={t("Also send to")}><TxSelect aria-label={t("Also send to")} value={bot.notify.phone} onChange={event => onBot({ notify: { ...bot.notify, phone: event.target.value } })}>
          <option value="">{t("None")}</option>{library.channels.map(ch=><option key={ch.id} value={ch.id}>{ch.name}</option>)}</TxSelect></TxRow>
        <TxRow title={t("Monthly limit")} detail={t("{money} this month", { money: money(bot.spend.month, bot.spend.currency) })}>
          <TxInput className="bot-limit" inputMode="decimal" value={bot.spend.limit === null ? "" : String(bot.spend.limit)} placeholder={t("No limit")} suffix={bot.spend.currency}
            onChange={event => onBot({ spend: { ...bot.spend, limit: event.target.value ? Number(event.target.value) : null } })} />
        </TxRow>
      </TxSettingsGroup>}
    </div>

    {adding === "skills" && <Library title={t("Add skills to {name}", { name: bot.name })} search={t("Search skills")} onClose={() => setAdding(null)}
      groups={[{ name: t("Global"), place: "global" }, { name: t("This workspace"), place: "workspace" }].map(group => ({ name: group.name,
        rows: library.skills.filter(skill => skill.place === group.place).map(skill => ({ key: skill.name, title: skill.name, detail: skill.detail })) }))}
      picked={bot.skills} onPicked={skills => onBot({ skills })} />}
    {adding === "knowledge" && <Library title={t("Add knowledge to {name}", { name: bot.name })} search={t("Search folders and files")} onClose={() => setAdding(null)}
      groups={[...new Set(library.knowledge.map(item => item.folder))].map(root => ({ name: root,
        rows: library.knowledge.filter(item => item.folder === root).map(item => ({ key: item.path, title: item.label, detail: item.size,
          icon: item.kind === "folder" ? "folder" as const : "file" as const })) }))}
      picked={bot.knowledge} onPicked={knowledge => onBot({ knowledge })} />}
  </div>;
}

const leaf = (path: string) => path.slice(path.lastIndexOf("/") + 1);
const parent = (path: string) => path.slice(0, path.lastIndexOf("/"));

/** Grok Bot's way of adding integrations: a dialog with a search over the library in groups, Add on each row (Added once
    in, which takes it out again). Changes apply as they are made; Done closes. */
function Library({ title, search, groups, picked, onPicked, onClose }: {
  title: string; search: string; picked: string[]; onPicked(picked: string[]): void; onClose(): void;
  groups: { name: string; rows: { key: string; title: string; detail: string; icon?: "folder" | "file" }[] }[];
}) {
  const [query, setQuery] = useState("");
  const shown = groups.map(group => ({ ...group, rows: group.rows.filter(row => !query || `${row.title} ${row.detail}`.toLowerCase().includes(query.toLowerCase())) }))
    .filter(group => group.rows.length);
  return <TxDialog open onOpenChange={open => { if (!open) onClose(); }} title={title} size="standard" footer={<TxButton variant="primary" onClick={onClose}>{t("Done")}</TxButton>}>
    <div className="bot-library">
      <TxSearch value={query} onValueChange={setQuery} placeholder={search} autoFocus />
      {!shown.length && <p className="bot-rail-none">{t("Nothing matches “{query}”", { query })}</p>}
      {shown.map(group => <section key={group.name} className="bot-library-group" aria-label={group.name}>
        <div className="bot-section-title bot-library-head">{group.name}</div>
        {group.rows.map(row => {
          const added = picked.includes(row.key);
          return <div key={row.key} className="bot-library-row">
            {row.icon && <TxIcon name={row.icon} />}
            <span className="bot-line-copy"><span className="bot-line-title">{row.title}</span><span className="bot-line-sub">{row.detail}</span></span>
            <TxButton size="sm" variant={added ? "quiet" : "default"} aria-pressed={added}
              onClick={() => onPicked(added ? picked.filter(item => item !== row.key) : [...picked, row.key])}>{added ? <><TxIcon name="check" />{t("Added")}</> : t("Add")}</TxButton>
          </div>;
        })}
      </section>)}
    </div>
  </TxDialog>;
}
function Drill({ title, value, onClick }: { title: string; value: string; onClick: () => void }) {
  return <TxButton variant="row" className="bot-drill" onClick={onClick}>
    <span className="bot-drill-title">{title}</span><span className="bot-line-sub">{value}</span><TxIcon name="chevron" />
  </TxButton>;
}

/** Grok Bot's Routine page: Active, Delete (asks first), Edit (by talking) and Test on top, then name, instruction, when, history. */
function RoutinePage({ bot, routine, onBot, onPrefill, onDeleted }: { bot: Bot; routine: Routine; onBot: (change: Partial<Bot>) => void; onPrefill: (text: string) => void; onDeleted: () => void }) {
  const [deleting, setDeleting] = useState(false);
  const api=useRailActions();
  const [testing, setTesting] = useState(false);
  const runs = bot.runs.filter(run => "routine" in run.wake && run.wake.routine === routine.name);
  return <div className="bot-settings">
    <div className="bot-routine-top">
      <TxSwitch label={t("Active")} checked={routine.active} onCheckedChange={active => onBot({ routines: bot.routines.map(item => item.id === routine.id ? { ...item, active } : item) })} />
      <span className="bot-spacer" />
      <TxButton size="sm" onClick={() => onPrefill(t("Edit your routine: {name} — ", { name: routine.name }))}>{t("Edit")}</TxButton>
      <TxButton size="sm" onClick={() => setDeleting(true)}>{t("Delete")}</TxButton>
      <TxButton size="sm" variant="primary" disabled={testing} onClick={() => { setTesting(true); void api.routine("test_routine",{id:routine.id}).finally(()=>setTesting(false)).catch(()=>undefined); }}>{testing ? t("Running…") : t("Test run")}</TxButton>
    </div>
    <TxDialog open={deleting} onOpenChange={setDeleting} title={t("Delete {name}?", { name: routine.name })}
      footer={<><TxButton onClick={() => setDeleting(false)}>{t("Cancel")}</TxButton>
        <TxButton variant="primary" onClick={() => { onBot({ routines: bot.routines.filter(item => item.id !== routine.id) }); setDeleting(false); onDeleted(); }}>{t("Delete")}</TxButton></>}>
      <p className="bot-dialog-text">{t("{name} stops running it. Its past runs stay in the history.", { name: bot.name })}</p>
    </TxDialog>
    <label className="bot-field"><span>{t("Name")}</span><TxInput defaultValue={routine.name} onBlur={event=>{if(event.target.value!==routine.name)void api.routine("save_routine",{id:routine.id,name:event.target.value}).catch(()=>undefined);}} /></label>
    <label className="bot-field"><span>{t("Instruction")}</span><TxTextarea rows={4} defaultValue={routine.instruction} onBlur={event=>{if(event.target.value!==routine.instruction)void api.routine("save_routine",{id:routine.id,instruction:event.target.value}).catch(()=>undefined);}} /></label>
    <div className="bot-field"><span>{routine.kind === "schedule" ? t("When to run") : t("When it fires")}</span>
      <div className="bot-when">
        {routine.when.map(when => <div key={when} className="bot-when-row"><TxIcon name={routine.kind === "schedule" ? "clock" : "bolt"} /><span>{when}</span></div>)}
        <TxButton variant="row" size="sm" className="bot-when-add" onClick={()=>onPrefill(t("Add another trigger for {name}: ", { name: routine.name }))}><TxIcon name="plus" />{t("Add another")}</TxButton>
      </div>
    </div>
    {routine.next && <div className="bot-field"><span>{t("Next run")}</span><span className="bot-field-value">{displayTime(routine.next)}</span></div>}
    <div className="bot-field"><span>{t("Run history")}</span>
      {runs.length ? <div className="bot-runs">{runs.map(run => <div key={run.id} className="bot-run" data-outcome={run.outcome} title={OUTCOME[run.outcome].label}>
        <TxIcon name={OUTCOME[run.outcome].icon} className="bot-run-mark" />
        <span className="bot-line-copy"><span className="bot-line-title">{displayTime(run.at)}</span><span className="bot-line-sub">{OUTCOME[run.outcome].label}</span></span>
        <span className="bot-run-figures"><span>{run.cost || "—"}</span><span className="bot-line-sub">{run.tokens && t("{tokens} tokens", { tokens: run.tokens })}</span></span>
      </div>)}</div> : <p className="bot-rail-none">{t("No runs yet")}</p>}
    </div>
  </div>;
}

function Allowed({ bot, onBot }: { bot: Bot; onBot: (change: Partial<Bot>) => void }) {
  if (!bot.allowed.length) return <p className="bot-rail-none">{t("Nothing is always allowed")}</p>;
  return <div className="bot-notes">{bot.allowed.map(action => <div key={action} className="bot-allowed">
    <span className="bot-line-title">{action}</span><span className="bot-spacer" />
    <TxIconButton size="sm" icon="trash" label={t("Remove {action}", { action })} onClick={() => onBot({ allowed: bot.allowed.filter(item => item !== action) })} />
  </div>)}</div>;
}
