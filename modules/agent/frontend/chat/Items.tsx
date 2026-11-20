import { useEffect, useState, type ReactNode } from "react";
import { TxAlert, TxButton, TxIcon, TxIconButton, TxInput, TxKeyNumbers, TxTag, TxTextarea } from "@talyx/sdk";
import { Markdown } from "../cards/markdown";
import { WakeIcon, wakeText } from "../bot/wake";
import type { Change, Item, ObjectRef, Step, StepIcon, SubAgent, TodoItem } from "./model";

/** A step's icon says what kind of thing it did, never which module it touched. */
const STEP_ICONS: Record<StepIcon, Parameters<typeof TxIcon>[0]["name"]> = { read: "file-search", change: "edit", delete: "trash", run: "terminal", send: "send" };

/* One chat's stream, in the chat panel: your messages on the right,
   the answer as plain text with copy and fork under it, the work as quiet
   lines (what it says between steps as text) folded to "Worked for …" with a rule under it, cards
   for what you act on (the question to allow, the plan, a result, the
   changes with undo and review). */

export type PaneSection = "agents" | "changes";

export interface ItemActions {
  open(ref: ObjectRef): void;
  decide(id: string, decision: "allow" | "always" | "deny", reason?: string): void;
  undo(id: string, undone: boolean): void;
  pane(section: PaneSection): void;
  copy(text: string): void;
  fork(itemId: string): void;
  failure(action: string): void;
  /** Plan mode's question: carry the plan out, or not. */
  plan?(id: string, accepted: boolean, reason?: string): void;
  retry?(id: string): void;
  /** Rewind, after Claude Code: the message being edited in place, and how to start, send (text) or cancel (null) the edit. */
  editing?: string | null;
  editFrom?(id: string): void;
  resend?(id: string, text: string | null): void;
}

export function AnswerReference({ reference, open }: { reference: ObjectRef; open: ItemActions["open"] }) {
  if (reference.url) return <a className="chat-link" href={reference.url} target="_blank" rel="noreferrer">{reference.label}</a>;
  return <TxTag onClick={() => open(reference)}>{reference.label}</TxTag>;
}

export function duration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60), rest = seconds % 60;
  return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
}

/** Whose stream it is: the "don't ask again" answer names the chat or the bot. */
export type Owner = "chat" | "bot";

export function Items({ items, actions, owner = "chat" }: { items: Item[]; actions: ItemActions; owner?: Owner }) {
  const lastAnswer = [...items].reverse().find(item => item.kind === "answer")?.id;
  return <>{items.map(item =>
    <div key={item.id} className="chat-item" data-kind={item.kind} data-last={item.id === lastAnswer || undefined}>{render(item, actions, owner)}</div>)}</>;
}

/** One item, for a stream that lays out its own messages (the Bot panel's bubbles) and shares the rest. */
export function ItemView({ item, actions, owner }: { item: Item; actions: ItemActions; owner: Owner }) {
  return <>{render(item, actions, owner)}</>;
}

function render(item: Item, actions: ItemActions, owner: Owner): ReactNode {
  switch (item.kind) {
    case "user": return <div className="chat-user">
      {(item.view || item.files) && <div className="chat-user-context">
        {item.view && <span className="chat-chip"><TxIcon name="chart" /><span>{item.view}</span></span>}
        {item.files?.map(file => <TxButton key={file.id} variant="quiet" size="sm" className="chat-chip"
          onClick={() => actions.open({id:file.id,label:file.name,path:"/attachments/"+file.id})}><TxIcon name="file" /><span>{file.name}</span></TxButton>)}
      </div>}
      {actions.editing === item.id
        ? <EditBox text={item.text} onDone={text => actions.resend?.(item.id, text)} />
        : <><div className="chat-bubble">{item.text}</div>
          {item.undelivered && <div className="chat-meta chat-undelivered">Not read: you stopped the run first</div>}
          <div className="chat-actions">
            {actions.editFrom && <TxIconButton size="sm" icon="history" label="Edit from here" onClick={() => actions.editFrom!(item.id)} />}
            <TxIconButton size="sm" icon="copy" label="Copy" onClick={() => actions.copy(item.text)} />
          </div></>}
    </div>;
    case "work": return <Work seconds={item.seconds} running={item.running} interrupted={item.interrupted} steps={item.steps} />;
    case "todo": return <Todo items={item.items} />;
    case "agents": {
      const count = (state: SubAgent["state"]) => item.agents.filter(agent => agent.state === state).length;
      const parts = [`${item.agents.length} subagents`, count("running") && `${count("running")} running`, count("failed") && `${count("failed")} failed`].filter(Boolean);
      return <TxButton variant="quiet" size="sm" className="chat-group" onClick={() => actions.pane("agents")}><TxIcon name="workflow" />{parts.join(" · ")}<TxIcon name="chevron" /></TxButton>;
    }
    case "approval": return item.state === "waiting" ? <Ask item={item} actions={actions} owner={owner} />
      : <div className="chat-line">{item.state === "allowed" ? "Allowed" : item.state === "denied" ? "Denied" : "Expired, not done"} · {item.action}</div>;
    case "result": return <div className="chat-card">
      <div className="chat-card-head">
        <span className="chat-tile"><TxIcon name="flask" /></span>
        <span className="chat-card-titles"><span className="chat-card-title">{item.title}</span><span className="chat-card-sub">{item.subtitle}</span></span>
        <TxButton size="sm" onClick={() => actions.open({ id: item.id, label: item.title, target:item.target })}>Open</TxButton>
      </div>
      <div className="chat-card-body"><TxKeyNumbers items={item.numbers} columns={item.numbers.length} /></div>
    </div>;
    case "answer": return <div className="chat-answer-block">
      <div className="chat-answer">
        <Markdown text={item.text} renderers={{
          reference: (id, key) => item.refs[id] ? <AnswerReference key={key} reference={item.refs[id]} open={actions.open} /> : null,
          link: (label, url, key) => <a key={key} className="chat-link" href={url} target="_blank" rel="noreferrer">{label}</a>,
        }} />
        {!!item.sources?.length && <ol className="chat-sources">{item.sources.map(source => <li key={source.n}><a className="chat-link" href={source.url} target="_blank" rel="noreferrer">{source.title}</a> <span className="chat-meta">{[source.site,source.published].filter(Boolean).join(" · ")}</span></li>)}</ol>}
      </div>
      <div className="chat-actions">
        <TxIconButton size="sm" icon="copy" label="Copy" onClick={() => actions.copy(item.text)} />
        <TxIconButton size="sm" icon="fork" label="Fork from here" onClick={() => actions.fork(item.id)} />
      </div>
    </div>;
    case "changes": return <Changes id={item.id} changes={item.changes} undone={item.undone} actions={actions} />;
    case "memory": return <div className="chat-line" data-undone={item.undone || undefined}>
      <span className="chat-line-text">{item.undone ? "Forgot" : "Remembered"}: {item.text}</span>
      <TxButton size="sm" variant="quiet" onClick={() => actions.undo(item.id, !item.undone)}>{item.undone ? "Redo" : "Undo"}</TxButton>
    </div>;
    case "error": return <TxAlert title={item.title} actions={<>
      {item.retry && <TxButton size="sm" onClick={() => actions.retry?.(item.id)}><TxIcon name="refresh" />Retry</TxButton>}
      {item.action && <TxButton size="sm" variant="quiet" onClick={() => actions.failure(item.action!)}>{item.action}</TxButton>}
    </>}>{item.text}</TxAlert>;
    case "plan": return <div className="chat-card chat-plan-card">
      <div className="chat-plan-head">
        <TxIcon name="list" /><span className="chat-plan-label">Plan</span><span className="chat-spacer" />
        <TxIconButton size="sm" icon="download" label="Download" onClick={() => { const url = URL.createObjectURL(new Blob([item.text], {type:"text/markdown;charset=utf-8"})); const link = document.createElement("a"); link.href = url; link.download = "plan.md"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }} />
        <TxIconButton size="sm" icon="copy" label="Copy" onClick={() => actions.copy(item.text)} />
      </div>
      <div className="chat-plan-body">
        <h3 className="chat-plan-title">{item.title}</h3>
        <div className="chat-answer"><Markdown text={item.text} renderers={{ reference: (_, key) => <span key={key} />, link: (label, _, key) => <span key={key}>{label}</span> }} /></div>
      </div>
    </div>;
    case "plan-ask": return item.state === "waiting" ? <PlanAsk id={item.id} actions={actions} />
      : <div className="chat-line">{item.state === "accepted" ? "Carrying out the plan" : "Plan not carried out"}</div>;
    case "notice": return <div className="chat-notice"><span>{item.text}</span></div>;
    case "wake": return <div className="chat-mark"><span>{new Date(item.at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span><span>·</span><WakeIcon wake={item.wake} /><span>{wakeText(item.wake)}</span></div>;
  }
}

/** The turn's to-do list in the stream: one line with the count that opens to the list in place
    (open while one is under way). */
function Todo({ items }: { items: TodoItem[] }) {
  const [open, setOpen] = useState(items.some(entry => entry.state === "active"));
  const done = items.filter(entry => entry.state === "done").length;
  return <div className="chat-todo">
    <TxButton variant="quiet" size="sm" className="chat-group" aria-expanded={open} onClick={() => setOpen(!open)}>
      <TxIcon name="list" />{done} of {items.length} done<TxIcon name={open ? "chevron-down" : "chevron"} /></TxButton>
    {open && <ol className="chat-plan">{items.map(entry => <li key={entry.text} data-state={entry.state}>
      <TxIcon name={entry.state === "done" ? "check-circle" : entry.state === "active" ? "loader" : "circle"} /><span>{entry.text}</span>
    </li>)}</ol>}
  </div>;
}

/** A sent message back in an edit box in its place, with its original text, with Cancel and Send. */
function EditBox({ text, onDone }: { text: string; onDone(text: string | null): void }) {
  const [value, setValue] = useState(text);
  return <div className="chat-edit">
    <TxTextarea className="chat-edit-input" rows={3} autoFocus value={value} onChange={event => setValue(event.target.value)}
      onKeyDown={event => {
        if (event.key === "Escape") { event.stopPropagation(); onDone(null); }
        if (event.key === "Enter" && !event.shiftKey && value.trim()) { event.preventDefault(); onDone(value.trim()); }
      }} />
    <div className="chat-edit-actions">
      <TxButton size="sm" variant="quiet" onClick={() => onDone(null)}>Cancel</TxButton>
      <TxButton size="sm" variant="primary" disabled={!value.trim()} onClick={() => onDone(value.trim())}>Send</TxButton>
    </div>
  </div>;
}

/** The "No, and tell it what to do differently" line under the numbered answers, shared by both question cards: Enter sends it as the No. */
function Instead({ onNo }: { onNo(reason: string): void }) {
  const [instead, setInstead] = useState("");
  return <div className="chat-ask-other">
    <TxIcon name="edit" />
    <TxInput className="chat-ask-input" value={instead} onChange={event => setInstead(event.target.value)} placeholder="No, and tell it what to do differently"
      onKeyDown={event => { if (event.key === "Enter" && instead.trim()) onNo(instead.trim()); }} />
  </div>;
}

/** The numbered answers of an approval card, in order; the number keys pick them (ChatPanel). */
export function askOptions(canAlwaysAllow: boolean, owner: Owner): { key: "allow" | "always" | "deny"; label: string }[] {
  return [{ key: "allow", label: "Yes" }, ...canAlwaysAllow ? [{ key: "always" as const, label: `Yes, and don't ask again ${owner === "bot" ? "for this bot" : "in this chat"}` }] : [],
    { key: "deny", label: "No" }];
}

/** the chat panel's question card: numbered answers (Yes, Yes and don't ask again, No), a line to say what to do instead, × to dismiss. Number keys pick (ChatPanel). */
function Ask({ item, actions, owner }: { item: Extract<Item, { kind: "approval" }>; actions: ItemActions; owner: Owner }) {
  const options = askOptions(item.canAlwaysAllow, owner);
  return <div className="chat-card chat-ask">
    <div className="chat-ask-head"><span className="chat-card-title">{item.action}?</span><span className="chat-meta">{item.target}</span>
      <span className="chat-spacer" />{item.expires !== undefined && <span className="chat-meta">Expires at {new Date(item.expires * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>}
      <TxIconButton size="sm" icon="close" label="Dismiss" onClick={() => actions.decide(item.id, "deny")} /></div>
    <ul className="chat-ask-lines">{item.lines.map(line => <li key={line}>{line}</li>)}</ul>
    <div className="chat-ask-options">
      {options.map((option, index) => <TxButton key={option.key} variant="row" className="chat-ask-option" onClick={() => actions.decide(item.id, option.key)}>
        <span className="chat-ask-key">{index + 1}</span>{option.label}
      </TxButton>)}
      <Instead onNo={reason => actions.decide(item.id, "deny", reason)} />
    </div>
  </div>;
}

/** the chat panel's "Implement this plan?" card under the plan. */
function PlanAsk({ id, actions }: { id: string; actions: ItemActions }) {
  return <div className="chat-card chat-ask">
    <div className="chat-ask-head"><span className="chat-card-title">Implement this plan?</span><span className="chat-spacer" />
      <TxIconButton size="sm" icon="close" label="Dismiss" onClick={() => actions.plan?.(id, false)} /></div>
    <div className="chat-ask-options">
      <TxButton variant="row" className="chat-ask-option" onClick={() => actions.plan?.(id, true)}><span className="chat-ask-key">1</span>Yes, implement this plan</TxButton>
      <TxButton variant="row" className="chat-ask-option" onClick={() => actions.plan?.(id, false)}><span className="chat-ask-key">2</span>No</TxButton>
      <Instead onNo={reason => actions.plan?.(id, false, reason)} />
    </div>
  </div>;
}

/** the chat panel's "Edited N files" card: the turn's changes with their size, undo and review; the first few rows, then "Show N more". */
function Changes({ id, changes, undone, actions }: { id: string; changes: Change[]; undone: boolean; actions: ItemActions }) {
  const [all, setAll] = useState(false);
  const shown = all ? changes : changes.slice(0, 3);
  return <div className="chat-card chat-changes" data-undone={undone || undefined}>
    <div className="chat-card-head">
      <span className="chat-tile"><TxIcon name="edit" /></span>
      <span className="chat-card-titles">
        <span className="chat-card-title">{undone ? "Undid" : "Made"} {changes.length} {changes.length === 1 ? "change" : "changes"}</span>
      </span>
      <TxButton size="sm" variant="quiet" disabled={changes.every(c=>c.reversible===false)} onClick={() => actions.undo(id, !undone)}>{undone ? "Redo" : "Undo"}<TxIcon name={undone ? "redo" : "undo"} /></TxButton>
      <TxButton size="sm" onClick={() => actions.pane("changes")}>Review</TxButton>
    </div>
    {shown.map(change => <ChangeRow key={change.id} change={change} actions={actions} />)}
    {changes.length > 3 && <TxButton variant="quiet" size="sm" className="chat-more" onClick={() => setAll(!all)}>{all ? "Show less" : `Show ${changes.length - 3} more`}<TxIcon name="chevron-down" /></TxButton>}
  </div>;
}

export function ChangeRow({ change, actions }: { change: Change; actions: ItemActions }) {
  return <a className="chat-change" href="#" onClick={event => { event.preventDefault(); actions.open(change); }}>
    <span className="chat-change-label">{change.label}</span><span className="chat-change-what">{change.what}</span>
  </a>;
}

/** While it runs, a turn's work shows only its latest steps (more are a click away). */
const LIVE_STEPS = 3;

/** The turn's work under one line that stays in place: "Working" with the time while it runs, over its latest steps;
    once done, "Worked for …" (no chevron) with a rule under it, the steps folded. */
function Work({ seconds, running, interrupted, steps }: { seconds: number; running: boolean; interrupted?: boolean; steps: Step[] }) {
  const [open, setOpen] = useState(false);
  const elapsed = useElapsed(seconds, running);
  const failed = steps.filter(step => step.state === "failed").length;
  const earlier = running && !open ? Math.max(0, steps.length - LIVE_STEPS) : 0;
  return <div className="chat-worked" data-interrupted={interrupted || undefined}>
    <TxButton variant="quiet" size="sm" className="chat-worked-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
      {running
        ? <><span className="chat-shimmer">Working</span><span className="chat-meta">{duration(elapsed)}</span></>
        : <span className="chat-worked-label">{interrupted ? `Stopped by you after ${duration(seconds)}` : `Worked for ${duration(seconds)}`}</span>}
      {failed > 0 && <span className="chat-failed-count"> · {failed} failed</span>}
    </TxButton>
    {(running || open) && <div className="chat-work">
      {earlier > 0 && <span className="chat-work-earlier">{earlier} earlier {earlier === 1 ? "step" : "steps"}</span>}
      <Steps steps={earlier ? steps.slice(-LIVE_STEPS) : steps} />
    </div>}
  </div>;
}

/** Steps as shown in the chat: what it says between steps as text, each step as a line that opens to its detail. */
export function Steps({ steps }: { steps: Step[] }) {
  return <div className="chat-steps">{steps.map(part => part.note ? <div key={part.id} className="chat-note chat-answer"><Markdown text={part.label} renderers={{
        reference: (_, key) => <span key={key} />, link: (label, url, key) => <a key={key} className="chat-link" href={url} target="_blank" rel="noreferrer">{label}</a> }} /></div>
    : <StepRow key={part.id} step={part} />)}</div>;
}

/** The last line while the turn runs "Working": how long it has been at it. */
export function Working({ since }: { since: number }) {
  const elapsed = useElapsed(since);
  return <div className="chat-working"><span className="chat-shimmer">Working</span><span className="chat-meta">{duration(elapsed)}</span></div>;
}

function StepRow({ step }: { step: Step }) {
  const [open, setOpen] = useState(false);
  const body = <>{step.icon && <TxIcon name={STEP_ICONS[step.icon]} className="chat-step-icon" />}<span className="chat-step-label">{step.label}</span></>;
  return <div className="chat-step" data-state={step.state}>
    {step.detail
      ? <TxButton variant="row" size="sm" className="chat-step-row" aria-expanded={open} onClick={() => setOpen(!open)}>{body}</TxButton>
      : <div className="chat-step-row">{body}</div>}
    {open && <pre className="chat-code">{step.detail}</pre>}
  </div>;
}

function useElapsed(start: number, live = true): number {
  const [value, setValue] = useState(start);
  useEffect(() => {
    if (!live) return;
    const timer = setInterval(() => setValue(current => current + 1), 1000);
    return () => clearInterval(timer);
  }, [live]);
  return value;
}
