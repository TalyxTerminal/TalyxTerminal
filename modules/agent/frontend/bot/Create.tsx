import { useRef, useState, type ReactNode } from "react";
import { TxButton, TxIcon, TxIconButton, TxInput, TxTabs, useTxToast } from "@talyx/sdk";
import { Avatar, SHAPES, TINTS } from "./Avatar";
import type { Bot, Look } from "./model";
import { readData } from "./api";

const PRESETS = [
  { name: "Pre-market brief", intro: "Overnight news and the watchlist before the open", look: {shape:"circle",tint:"blue"},
    duty:"Prepare a concise weekday pre-market report for this workspace. Check observed overnight data and the user's watchlist. State the data date and any unavailable inputs.",
    rrule:"FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=9;BYMINUTE=0;BYSECOND=0", zone:"America/New_York", when:"Weekdays at 09:00" },
  { name: "Weekly review", intro: "The week's market moves, alerts and research", look: {shape:"square",tint:"purple"},
    duty:"Review the week's research, observed market moves and alerts in this workspace. Give a brief account of meaningful findings and their sources.",
    rrule:"FREQ=WEEKLY;BYDAY=SU;BYHOUR=18;BYMINUTE=0;BYSECOND=0", zone:Intl.DateTimeFormat().resolvedOptions().timeZone, when:"Sunday at 18:00" },
] as const;

/* Making bots and groups, copied from Grok Bot: a new bot is a picture and a
   name, then a chat where it asks what its job is (or one of TALYX's
   presets); a group is the bots you pick in "To:". */

/** Grok Bot's picture picker: a Pick tab (a row of colours over a row of shapes) and an Upload tab. No generated pictures. */
export function LookPicker({ look, onLook }: { look: Look; onLook: (look: Look) => void }) {
  const file = useRef<HTMLInputElement>(null);
  const toast = useTxToast();
  const [tab, setTab] = useState<"pick" | "upload">(look.photo ? "upload" : "pick");
  return <div className="bot-look">
    <TxTabs label="Picture" value={tab} onChange={setTab} options={[{ value: "pick", label: "Pick" }, { value: "upload", label: "Upload" }]} />
    {tab === "pick" ? <>
      <div className="bot-look-row" role="group" aria-label="Colour">{TINTS.map(tint =>
        <TxButton key={tint} variant="quiet" className="bot-tint" aria-label={tint} aria-pressed={tint === look.tint && !look.photo} onClick={() => onLook({ shape: look.shape, tint })}>
          <span className="bot-tint-dot" data-tint={tint} />
        </TxButton>)}</div>
      <div className="bot-look-row" role="group" aria-label="Shape">{SHAPES.map(shape =>
        <TxButton key={shape} variant="quiet" className="bot-shape" aria-label={shape} aria-pressed={shape === look.shape && !look.photo} onClick={() => onLook({ shape, tint: look.tint })}>
          <Avatar look={{ shape, tint: look.tint }} size="sm" />
        </TxButton>)}</div>
    </> : look.photo
      ? <div className="bot-upload-done">
        <Avatar look={look} size="lg" />
        <span className="bot-upload-copy"><span className="bot-line-title">{look.photo.name}</span><span className="bot-line-sub">{look.photo.size}</span></span>
        <TxButton size="sm" onClick={() => onLook({ shape: look.shape, tint: look.tint })}>Remove</TxButton>
      </div>
      : <div className="bot-upload">
        <TxIcon name="download-box" />
        <TxInput ref={file} type="file" hidden accept="image/png,image/jpeg,image/webp,image/gif,image/bmp" onChange={event=>{
          const selected=event.target.files?.[0];
          if (selected) void readData(selected).then(data=>onLook({...look, photo:{name:selected.name,size:`${Math.ceil(selected.size/1024)} KB`,data}}))
            .catch(reason=>toast({title:"Image could not be read",detail:String(reason),tone:"failed"}));
        }} />
        <TxButton size="sm" onClick={() => file.current?.click()}>Choose an image…</TxButton>
      </div>}
  </div>;
}

export interface Draft { name: string; look: Look; intro?:string; duty?:string; rrule?:string; zone?:string; when?:string }

export function NewBot({ draft, onDraft, onCreate, mode }: { draft: Draft; onDraft(draft: Draft): void; onCreate: (draft: Draft) => void; mode?: ReactNode }) {
  return <>
    <div className="bot-head">{mode && <span className="bot-head-mode">{mode}</span>}<Avatar look={draft.look} size="xs" /><span className="bot-title-name">{draft.name || "New bot"}</span></div>
    <div className="bot-create">
      <div className="bot-create-form">
        <Avatar look={draft.look} size="xl" />
        <LookPicker look={draft.look} onLook={look => onDraft({ ...draft, look })} />
        <label className="bot-field"><span>Name</span><TxInput value={draft.name} placeholder="New bot" onChange={event => onDraft({ ...draft, name: event.target.value })} /></label>
        <TxButton variant="primary" disabled={!draft.name.trim()} onClick={() => onCreate({ ...draft, name: draft.name.trim() })}>Get started</TxButton>
      </div>
      <div className="bot-presets">
        <span className="bot-section-title">Presets</span>
        <div className="bot-preset-grid">{PRESETS.map(preset => <div key={preset.name} className="bot-preset-card"><TxButton variant="row" className="bot-preset"
          onClick={() => onCreate({ ...preset, look: { ...preset.look } })}>
          <Avatar look={preset.look} size="lg" />
          <span className="bot-preset-copy"><span className="bot-preset-name">{preset.name}</span><span className="bot-preset-intro">{preset.intro}</span></span>
        </TxButton></div>)}</div>
      </div>
    </div>
  </>;
}

/** Grok Bot's "To:" line: the picked bots as chips, a name field, the matching bots in a list under it while you type. */
export function BotPicker({ bots, picked, onPicked, max = 6, autoFocus }: { bots: Bot[]; picked: string[]; onPicked(picked: string[]): void; max?: number; autoFocus?: boolean }) {
  const [find, setFind] = useState("");
  const [open, setOpen] = useState(Boolean(autoFocus));
  const byId = new Map(bots.map(bot => [bot.id, bot]));
  const matches = bots.filter(bot => !picked.includes(bot.id) && bot.name.toLowerCase().includes(find.toLowerCase()));
  return <div className="bot-picker">
    <div className="bot-to">
      <span className="bot-to-label">To:</span>
      {picked.map(id => byId.get(id) && <span key={id} className="bot-to-chip"><Avatar look={byId.get(id)!.look} size="xs" />{byId.get(id)!.name}
        <TxIconButton size="sm" icon="close" label={`Remove ${byId.get(id)!.name}`} onClick={() => onPicked(picked.filter(item => item !== id))} /></span>)}
    </div>
    <TxInput className="bot-to-input" value={find} autoFocus={autoFocus} placeholder={picked.length >= max ? `Up to ${max} bots` : "Add a bot by name"} disabled={picked.length >= max}
      onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)} onChange={event => { setFind(event.target.value); setOpen(true); }} />
    {open && matches.length > 0 && <div className="bot-pick-drop" role="listbox">{matches.map(bot =>
      <TxButton key={bot.id} variant="row" className="bot-pick-row" role="option" onMouseDown={event => event.preventDefault()}
        onClick={() => { onPicked([...picked, bot.id]); setFind(""); }}>
        <Avatar look={bot.look} size="md" state={bot.state} />
        <span className="bot-row-copy"><span className="bot-row-name">{bot.name}</span><span className="bot-row-text">{bot.intro}</span></span>
      </TxButton>)}</div>}
  </div>;
}

/** New group: only "To:"; the name follows the members until you rename it, like Grok Bot. */
export function NewGroup({ bots, onCreate, mode }: { bots: Bot[]; onCreate: (members: string[]) => void; mode?: ReactNode }) {
  const [picked, setPicked] = useState<string[]>([]);
  return <>
    <div className="bot-head">{mode && <span className="bot-head-mode">{mode}</span>}<TxIcon name="messages" /><span className="bot-title-name">New group</span></div>
    <div className="bot-create">
      <div className="bot-group-form">
        <BotPicker bots={bots} picked={picked} onPicked={setPicked} autoFocus />
        <div className="bot-group-actions"><TxButton variant="primary" disabled={picked.length < 2} onClick={() => onCreate(picked)}>Create group</TxButton></div>
      </div>
    </div>
  </>;
}
