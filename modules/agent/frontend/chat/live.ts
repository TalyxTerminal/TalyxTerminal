import type { AgentCard, AgentDefaults, AgentSettings, Conversation, ConversationSnapshot } from "../data/types";
import type { Chat, ChatActivity, Choices, Item, ModelChoice, Step, SubAgent } from "./model";

export function chatModelChoices(settings: AgentSettings): ModelChoice[] {
  return settings.models.filter(model => model.tools !== false).map(model => ({
    id: model.id, name: model.model, connection: settings.connections.find(conn => conn.id === model.connectionId)?.name ?? model.connectionId,
    window: model.contextWindow, levels: model.thinkingSchema.properties.effort?.enum ?? [], defaultLevel: model.thinkingDefault ?? "",
  }));
}

/** A new chat's choices, as the settings' defaults for new chats say: the model, its remembered thinking level, the permission. */
export function newChatChoices(defaults: AgentDefaults, models: ModelChoice[]): Choices {
  const model = models.find(m => m.id === defaults.modelId) ?? models[0];
  return {model: model?.id ?? "", thinking: model ? defaults.modelThinking[model.id] ?? model.defaultLevel : "",
    permission: defaults.defaultPermission, subagents: defaults.childrenEnabled, plan: false};
}

export function chatChoicesUpdate(changes: Partial<Choices>) {
  return { ...(changes.model !== undefined && { modelId: changes.model }), ...(changes.permission && {permission: changes.permission}),
    ...(changes.subagents !== undefined && {childrenEnabled: changes.subagents}), ...(changes.plan !== undefined && {planMode: changes.plan}),
    ...(changes.thinking !== undefined && {thinking: changes.thinking ? {effort: changes.thinking} : null}) };
}

export function activity(conversation: Conversation, failed = false): ChatActivity {
  return conversation.activity.waitingApproval ? "approval" : conversation.activity.running ? "running" : failed ? "failed" : "idle";
}

function describe(card: AgentCard, retryable = false): Item[] {
  const id = card.id;
  switch (card.type) {
    case "plan": return [{kind:"plan", id:id+"_text", title:"Proposed plan", text:card.text},{kind:"plan-ask",id,state:card.state}];
    case "answer": {
      if (card.role === "user") return [{kind:"user", id, text:card.text, files:card.materials,
        view:card.view && typeof card.view==="object" && !Array.isArray(card.view) && typeof card.view.summary==="string" ? card.view.summary : undefined}];
      return [{kind:"answer", id, text:card.text, refs:Object.fromEntries(card.references.map(ref =>
        [ref.id, {id:ref.id, label:ref.label, ...(ref.kind === "object" ? {target:ref.target} : ref.kind === "source" ? {url:ref.target.url} : {panel:ref.target})}])),
        sources:card.sources.map((source, i) => ({n:i+1,title:source.title,site:new URL(source.url).hostname,published:"",url:source.url}))}];
    }
    case "approval": {
      const name = card.action.split(".").at(-1)!.replaceAll("_"," ");
      const input = card.input && typeof card.input === "object" && !Array.isArray(card.input) ? card.input : {};
      const lines = Object.entries(input).map(([key,value]) => key.replaceAll("_"," ")+" · "+(typeof value === "string" ? value : JSON.stringify(value)));
      return [{kind:"approval",id,action:name[0].toUpperCase()+name.slice(1),target:card.action.includes(".") ? card.action.split(".")[0] : "",
        lines,canAlwaysAllow:!card.proposal.risks.includes("delete"),state:card.state === "consumed" ? "allowed" : card.state === "interrupted" ? "expired" : card.state,
        expires:card.state === "waiting" && card.expiresAt !== null ? card.expiresAt : undefined}];
    }
    case "changes": return [{kind:"changes", id, undone:card.undone, changes:card.items.map(item => ({
      id:item.callId+":"+(item.objectId ?? ""),label:item.label,what:item.kind,reversible:item.reversible,where:item.openTarget?.module ?? undefined,
      target:item.openTarget ?? undefined,path:item.objectId?.startsWith("/") ? item.objectId : undefined,
    }))}];
    case "todos": return [{kind:"todo",id,items:card.items.map(item => ({text:item.content,state:item.status === "completed" ? "done" : item.status === "in_progress" ? "active" : "open"}))}];
    case "summary": return [{kind:"result",id,title:card.title,subtitle:card.subtitle,target:card.openTarget,numbers:card.keyNumbers.map(n => ({
      label:n.label,value:[n.value,n.unit].filter(Boolean).join(" "),tone:n.direction === "none" ? undefined : n.direction}))}];
    case "error": return [{kind:"error",id,title:retryable ? "Run stopped" : "Error",text:card.message,retry:retryable}];
    case "compaction": return [{kind:"notice",id,text:"Conversation compacted"}];
    case "memory_saved": return card.changeId ? [{kind:"memory", id, text:card.memory.body, undone:card.memory.status !== "active"}]
      : [{kind:"notice",id,text:(card.memory.status === "active" ? "Remembered: " : "Forgot: ")+card.memory.body}];
    case "child_run": {
      const state: SubAgent["state"] = card.state === "completed" ? "done" : card.state === "queued" ? "queued" :
        ["failed","interrupted","cancelled"].includes(card.state) ? "failed" : "running";
      return [{kind:"agents",id,agents:[{id,title:card.title,model:card.modelId,thinking:String(card.thinking?.effort ?? ""),
        state,now:card.resultPreview ?? card.lastAction ?? "",cost:card.usage.flatMap(u=>Object.entries(u.amounts).map(([currency,amount]) => amount+" "+currency)).join(", ")}]}];
    }
    default: return [];
  }
}

type Work = Extract<Item, {kind: "work"}>;
type TimedStep = { at: number; order: string; step: Step };

function workSteps(card: Extract<AgentCard, {type: "thinking" | "process_group"}>, answer?: Extract<AgentCard, {type:"answer"}>): TimedStep[] {
  if (card.type === "thinking") {
    // Thinking folds to one line; a click opens the whole of it.
    return [{at: card.startedAt, order: card.orderKey, step: {id: card.id, label: card.text, detail: card.text}}];
  }
  return card.steps.flatMap((step): TimedStep[] => {
    const details = step.details !== null && typeof step.details === "object" && !Array.isArray(step.details) ? step.details : {};
    const text = typeof details.text === "string" ? details.text : "";
    if (card.id.startsWith("model_") && step.state === "completed" && (!text || (answer?.modelStepId ? step.id === answer.modelStepId : text.trim() === answer?.text.trim()))) return [];
    const effects = Array.isArray(details.effects) ? details.effects : [];
    const icon = effects.includes("delete") ? "delete" : effects.includes("external") ? "send" : effects.includes("code") ? "run" : effects.includes("write") ? "change" : "read";
    const isNote = !step.action && text.length > 0;
    return [{at: step.startedAt, order: step.orderKey, step: {
      id: step.id, label: isNote ? text : step.title, note: isNote, icon,
      state: step.state === "running" || step.state === "failed" ? step.state : undefined,
      detail: isNote ? undefined : JSON.stringify(step.error ?? step.details, null, 2),
    }}];
  });
}

export function childSteps(cards: AgentCard[]): Step[] {
  return cards.flatMap(card => card.type === "thinking" || card.type === "process_group" ? workSteps(card) : [])
    .sort((a,b)=>a.at-b.at || a.order.localeCompare(b.order)).map(s=>s.step);
}

/** Model calls, reasoning and tool steps share the disclosure of the turn that owns them. A message added to a running
    turn sits where it came in: the work before it, the message, then the work after it steering. A message
    the turn never got to (it was stopped first) says so. */
export function chatItems(snapshot: Pick<ConversationSnapshot, "cards" | "turns">): Item[] {
  const entries: (Item | {turn: string})[] = [];
  const turns = new Map(snapshot.turns.map(turn => [turn.id, turn]));
  const busy = snapshot.turns.some(turn=>["queued","running","stopping","waiting_approval","waiting_business"].includes(turn.state));
  const answers = new Map(snapshot.cards.flatMap(card => card.type==="answer" && card.role==="assistant"
    ? [[card.turnId || snapshot.turns[0]?.id,card] as const] : []));
  // Messages added to a turn after it began, by turn: where they split its work.
  const added = new Map<string, {id: string; at: number}[]>();
  const groups = new Map<string, {steps: TimedStep[]; first: number; last: number}>();
  // A turn that woke by itself (the chat's schedule) is marked above its first card.
  const marked = new Set<string>();
  for (const card of snapshot.cards) {
    const woken = turns.get(card.turnId ?? "");
    if (woken && woken.wake.kind !== "user" && !marked.has(woken.id)) {
      marked.add(woken.id);
      entries.push({kind:"wake", id:"wake_" + woken.id, at:woken.createdAt, wake:woken.wake});
    }
    if (card.type !== "process_group" && card.type !== "thinking") {
      const turn = turns.get(card.turnId ?? "");
      const state = turn?.state;
      const described = describe(card,!busy && card.id === "error_"+card.turnId && (state === "failed" || state === "budget_exceeded"));
      if (card.type === "answer" && card.role === "user" && turn && card.id !== "user_" + turn.id) {
        added.set(turn.id, [...added.get(turn.id) ?? [], {id: card.id, at: card.createdAt}]);
        if (state === "cancelled" && turn.inbox.some(message => message.messageId === card.id))
          for (const item of described) if (item.kind === "user") item.undelivered = true;
      }
      entries.push(...described);
      continue;
    }
    // A Bot thread block contains exactly one run, including messages saved before turnId was exposed.
    const owner = card.turnId || (snapshot.turns.length === 1 ? snapshot.turns[0].id : undefined);
    if (!owner) continue;
    let group = groups.get(owner);
    if (!group) {
      group = {steps:[], first:card.createdAt, last:card.updatedAt};
      groups.set(owner, group);
      entries.push({turn: owner});
    }
    group.steps.push(...workSteps(card, answers.get(owner)));
    group.first = Math.min(group.first, card.createdAt);
    group.last = Math.max(group.last, card.updatedAt);
  }
  const segments = new Map<string, Work[]>();
  for (const [id, group] of groups) {
    const turn = turns.get(id);
    const steps = group.steps.sort((a,b) => a.at - b.at || a.order.localeCompare(b.order));
    const cuts = (added.get(id) ?? []).map(message => message.at);
    const parts: TimedStep[][] = cuts.map(() => []).concat([[]]);
    for (const entry of steps) parts[cuts.filter(at => at <= entry.at).length].push(entry);
    const end = turn?.finishedAt ?? group.last;
    const lastPart = parts.map(part => part.length > 0).lastIndexOf(true);
    segments.set(id, parts.map((part, index) => {
      const start = index === 0 ? turn?.startedAt ?? group.first : cuts[index - 1];
      const until = index === lastPart ? end : index < cuts.length ? cuts[index] : end;
      return {kind:"work", id:"work_" + id + (index ? "_" + index : ""), seconds:Math.max(0, Math.round(until - start)),
        running: index === lastPart && !!turn && turn.finishedAt === null && turn.state !== "waiting_approval" && turn.state !== "waiting_business",
        interrupted: index === lastPart && turn?.state === "cancelled", steps: part.map(entry => entry.step)};
    }));
  }
  const after = new Map([...added].flatMap(([turn, messages]) => messages.map((message, index) => [message.id, segments.get(turn)?.[index + 1]] as const)));
  const items: Item[] = [];
  for (const entry of entries) {
    if ("turn" in entry) { const first = segments.get(entry.turn)?.[0]; if (first) items.push(first); continue; }
    items.push(entry);
    const next = after.get(entry.id);
    if (next) items.push(next);
  }
  return items.filter(item=>item.kind!=="work" || item.steps.length>0);
}

function taskStatus(card: Extract<AgentCard,{type:"task"|"result"}>) {
  const progress = card.task?.progress;
  if (card.type !== "task" || progress == null) return card.state;
  if (typeof progress === "string" || typeof progress === "number") return String(progress);
  if (typeof progress === "object" && !Array.isArray(progress)) {
    return [progress.stage, progress.progress].filter(value => typeof value === "string" || typeof value === "number").join(" · ") || card.state;
  }
  return card.state;
}

export function chatFromSnapshot(snapshot: ConversationSnapshot, models: ModelChoice[]): Chat {
  const c = snapshot.conversation, lastTurn = snapshot.turns.at(-1);
  return {id:c.id,title:c.title,activity:activity(c,lastTurn?.state === "failed"),items:chatItems(snapshot),
    queued:c.queued ?? [], choices:{model:c.modelId ?? "",thinking:String(c.thinking?.effort ?? models.find(m => m.id === c.modelId)?.defaultLevel ?? ""),
      plan:c.planMode ?? false,subagents:c.childrenEnabled,permission:c.permission},
    context:{used:c.context?.providerInputTokens ?? c.context?.estimatedInputTokens ?? 0, window:c.context?.window ?? models.find(m=>m.id === c.modelId)?.window ?? 0},
    tasks:snapshot.cards.flatMap(card => card.type === "task" || card.type === "result" ? [{
      id:card.callId,title:card.title,module:card.action.split(".")[0],state:card.type === "task" ? "running" : card.state === "completed" ? "done" : "failed",now:taskStatus(card),target:card.openTarget ?? undefined,
    }] : [])};
}
