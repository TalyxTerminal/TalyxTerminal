import { useEffect, useRef, type ReactNode } from "react";
import { TxButton, TxIcon } from "@talyx/sdk";
import type { ItemActions, PaneSection } from "./Items";
import { DragEdge, useDragWidth } from "./resize";
import type { Chat, SubAgent, Task } from "./model";

/* The chat's side panel, in the chat panel's summary card: one
   rounded card with its own ground, floating beside the chat (over it when
   the panel is narrow), grey section headings over plain rows, a hairline
   between sections. Three sections only: what is running (subagents and the
   module work the chat started, running ones first and largest), what the
   last turn changed, and the files you attached. A click opens the thing
   where it lives: a module object in its module's panel, a file in its
   folder, a subagent's conversation in the chat area. Plan and to-dos
   stay in the stream, sources are links in the answers, context is the ring
   in the message box. Opened and closed only by the button in the head. */

type Work = { id: string; title: string; state: SubAgent["state"] | Task["state"]; now: string; kind: string; open(): void };

export function SidePane({ chat, focus, actions, agent, onAgent }: {
  /** What the stream asked to see; `n` changes on every ask, so asking twice works. */
  chat: Chat; focus: { section: PaneSection; n: number } | null; actions: ItemActions; agent: string | null; onAgent(agentId: string): void;
}) {
  const drag = useDragWidth("chat-rail", 240, 560);
  const sections = useRef<Record<PaneSection, HTMLElement | null>>({ agents: null, changes: null });
  useEffect(() => { if (focus) sections.current[focus.section]?.scrollIntoView({ block: "nearest" }); }, [focus?.n]);

  // Running first, then the rest in the order they started tasks.
  const work: Work[] = [
    ...chat.items.flatMap(item => item.kind === "agents" ? item.agents : []).map(agent => ({ id: agent.id, title: agent.title, state: agent.state, now: agent.state === "done" ? agent.cost : agent.now,
      kind: "Subagent", open: () => onAgent(agent.id) })),
    ...(chat.tasks ?? []).map(task => ({ id: task.id, title: task.title, state: task.state, now: task.now, kind: task.module,
      open: () => actions.open({ id: task.id, label: task.title, where: task.module, target:task.target }) })),
  ];
  const running = work.filter(item => item.state === "running");
  const rest = work.filter(item => item.state !== "running");
  // What the last turn that changed anything changed, still standing (an undone card has nothing to show).
  const last = [...chat.items].reverse().find(item => item.kind === "changes");
  const changes = last?.kind === "changes" && !last.undone ? last.changes : [];
  const files = chat.items.flatMap(item => item.kind === "user" ? item.files ?? [] : []);

  return <aside className="chat-rail" aria-label="Chat summary" style={drag.style("--chat-rail-width")}>
    <DragEdge side="left" label="Resize the summary" drag={drag} />
    <div className="chat-rail-body">
      {work.length > 0 && <Section title="Tasks" figure={running.length ? `${running.length} running` : undefined} refTo={element => { sections.current.agents = element; }}>
        {running.map(item => <TxButton key={item.id} variant="row" className="chat-rail-row chat-rail-live" aria-current={item.id === agent || undefined} onClick={item.open}>
          <TxIcon name="loader" className="chat-rail-icon chat-spin" />
          <span className="chat-rail-copy"><span className="chat-rail-title">{item.title}</span><span className="chat-rail-now">{item.now}</span></span>
          <span className="chat-rail-meta">{item.kind}</span>
        </TxButton>)}
        {rest.map(item => <TxButton key={item.id} variant="row" className="chat-rail-row" data-state={item.state} aria-current={item.id === agent || undefined} onClick={item.open}
          title={item.state === "failed" || item.state === "queued" ? item.now : undefined}>
          <TxIcon name={DONE_ICONS[item.state]} className="chat-rail-icon" />
          <span className="chat-rail-title">{item.title}</span>
          <span className="chat-rail-meta">{item.state === "failed" ? "Failed" : item.state === "queued" ? "Waiting" : item.now}</span>
        </TxButton>)}
      </Section>}
      {changes.length > 0 && <Section title="Changes" refTo={element => { sections.current.changes = element; }}>
        {changes.map(change => <TxButton key={change.id} variant="row" className="chat-rail-row" onClick={() => actions.open(change)}>
          <TxIcon name={change.path ? "file" : "box"} className="chat-rail-icon" />
          <span className="chat-rail-title chat-rail-name">{change.label}</span>
          <span className="chat-rail-what">{change.what}</span>
        </TxButton>)}
      </Section>}
      {files.length > 0 && <Section title="Attached">
        {files.map(file => <TxButton key={file.id} variant="row" className="chat-rail-row" onClick={() => actions.open({ id: file.id, label: file.name, path:"/attachments/"+file.id })}>
          <TxIcon name="paperclip" className="chat-rail-icon" /><span className="chat-rail-title">{file.name}</span>
        </TxButton>)}
      </Section>}
      {!work.length && !changes.length && !files.length && <p className="chat-rail-none">Nothing running, changed or attached</p>}
    </div>
  </aside>;
}

const DONE_ICONS: Record<SubAgent["state"] | Task["state"], "check-circle" | "x-circle" | "circle" | "loader"> = { done: "check-circle", failed: "x-circle", queued: "circle", running: "loader" };

/** A section as in the chat panel's card: a grey heading (a figure on its right) over plain rows. */
function Section({ title, figure, children, refTo }: { title: string; figure?: ReactNode; children: ReactNode; refTo?: (element: HTMLElement | null) => void }) {
  return <section ref={refTo} className="chat-rail-section">
    <div className="chat-rail-heading"><span>{title}</span>{figure && <span className="chat-rail-figure">{figure}</span>}</div>
    {children}
  </section>;
}
