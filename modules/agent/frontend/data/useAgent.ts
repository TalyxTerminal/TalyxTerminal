import { useEffect, useMemo, useRef, useState } from "react";
import type { ModuleContext, Subscription } from "@talyx/sdk";
import { AgentClient } from "./client";
import type { AgentCard, AgentSettings, ConversationListItem, ConversationSnapshot, Turn } from "./types";

export function useAgentConversation(ctx: ModuleContext, conversationId: string | null) {
  const client = useMemo(() => new AgentClient(ctx), [ctx]);
  const [snapshot, setSnapshot] = useState<ConversationSnapshot | null>(null);
  const earlier = useRef<() => void>(() => {});
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    let alive = true, subscription: Subscription | undefined, watermark = 0;
    let current: ConversationSnapshot | null = null;
    let tail = Promise.resolve();
    setSnapshot(null); setError(null);
    // Reset here rather than on cleanup: cleanup also runs while the module unloads.
    if (ctx.panelRef) ctx.platform.panels.setStatus(ctx.panelRef, { state: "idle", text: "", navigation: null });
    if (!conversationId) return;
    const presence = () => {
      if (alive && ctx.panelRef) void client.conversation("presence", { conversationId,
        panelId:ctx.panelRef.panelId,active: document.hasFocus(), visible: ctx.visible.get() && document.visibilityState === "visible" }).catch(setError);
    };
    const unwatchActive = ctx.active.watch(presence), unwatchVisible = ctx.visible.watch(presence);
    document.addEventListener("visibilitychange", presence);
    window.addEventListener("focus", presence); window.addEventListener("blur", presence);
    presence();
    const publish = () => {
      if (!alive || !current) return;
      setSnapshot({ ...current, cards: [...current.cards], turns: [...current.turns] });
      if (ctx.panelRef) {
        const waiting = current.conversation.activity.waitingApproval;
        const busy = current.conversation.activity.running;
        ctx.platform.panels.setStatus(ctx.panelRef, { state: waiting ? "attention" : busy ? "running" : "idle",
          text: waiting ? "Approval needed" : busy ? "Agent is running" : "",
          navigation: { conversationId } });
      }
    };
    const sync = async () => {
      if (!alive || !current) return;
      let page;
      do {
        page = await client.events(conversationId, watermark);
        if (!alive) return;
        for (const event of page.items) {
          if (event.seq <= watermark) continue;
          watermark = event.seq;
          if (event.type === "rewind") {
            current = await client.read(conversationId);
            watermark = current.watermark;
          } else if (event.type === "card") {
            const value = { ...event.payload as unknown as AgentCard, turnId: event.turnId ?? "" };
            const index = current.cards.findIndex(card => card.id === value.id);
            if (index < 0) current.cards.push(value); else current.cards[index] = value;
          } else if (event.type === "turn") {
            const value = event.payload as unknown as Turn;
            const index = current.turns.findIndex(turn => turn.id === value.id);
            if (index < 0) current.turns.push(value); else current.turns[index] = value;
          } else if (event.type === "conversation") current.conversation = event.payload as unknown as ConversationSnapshot["conversation"];
          current.conversation.activity = event.activity;
        }
      } while (page.nextAfter !== null);
      current.watermark = watermark;
      publish();
      // What just arrived is on screen if the chat is: it counts as read.
      if (ctx.visible.get() && document.visibilityState === "visible") presence();
    };
    const queue = () => { tail = tail.then(sync).catch(value => { if (alive) setError(value); }); };
    earlier.current = () => {
      const before = current?.nextBefore;
      if (before == null) return;
      tail = tail.then(async () => {
        if (!alive || !current || current.nextBefore !== before) return;
        const page = await client.read(conversationId, before);
        if (!alive) return;
        current.cards = [...page.cards, ...current.cards];
        current.turns = [...new Map([...page.turns, ...current.turns].map(t => [t.id,t])).values()].sort((a,b)=>a.createdAt-b.createdAt);
        current.nextBefore = page.nextBefore;
        publish();
      }).catch(value => { if (alive) setError(value); });
    };
    void (async () => {
      // Events are not kept: when Agent's process restarted, the stream is opened again and the chat reads what it missed.
      subscription = await ctx.events.subscribe("agent.conversation_changed", payload => {
        if ((payload as { conversationId?: string }).conversationId === conversationId) queue();
      }, { onReopen: queue });
      if (!alive) { await subscription.close(); return; }
      current = await client.read(conversationId);
      watermark = current.watermark;
      publish();
      queue();
    })().catch(value => { if (alive) setError(value); });
    return () => {
      alive = false;
      if(ctx.panelRef)void client.conversation("presence",{conversationId,panelId:ctx.panelRef.panelId,active:false,visible:false}).catch(setError);
      unwatchActive(); unwatchVisible(); document.removeEventListener("visibilitychange", presence);
      window.removeEventListener("focus", presence); window.removeEventListener("blur", presence);
      if (subscription) void subscription.close();
    };
  }, [client, conversationId, ctx]);
  return { client, snapshot, error, loadEarlier: () => earlier.current() };
}

/** A view can be disposed before its subscribe request settles (e.g. closing a settings section). */
export function subscribeAndLoad(start: () => Promise<Subscription>, load: () => Promise<void>, onError: (error: unknown) => void) {
  let live = true, subscription: Subscription | undefined;
  void (async () => {
    const opened = await start();
    if (!live) { await opened.close(); return; }
    subscription = opened;
    await load();
  })().catch(error => { if (live) onError(error); });
  return () => {
    live = false;
    if (subscription) void subscription.close().catch(onError);
  };
}

export function useAgentSettings(ctx: ModuleContext) {
  const client = useMemo(() => new AgentClient(ctx), [ctx]);
  const [value, setValue] = useState<AgentSettings | null>(null), [error, setError] = useState<unknown>(null);
  const refresh = async () => { try { setValue(await client.readSettings()); setError(null); } catch (failure) { setError(failure); } };
  useEffect(() => {
    // A model added on the settings page reaches an open chat panel at once.
    return subscribeAndLoad(() => ctx.events.subscribe("agent.settings_changed", refresh, { onReopen: refresh }), refresh, setError);
  }, [client]);
  return { client, value, error, refresh };
}

/** Every chat of this workspace, kept current while any of them changes. */
export function useConversations(ctx: ModuleContext, client: AgentClient) {
  const [items, setItems] = useState<ConversationListItem[] | null>(null), [error, setError] = useState<unknown>(null);
  const load = useMemo(() => async () => {
    const all: ConversationListItem[] = [];
    let offset: number | null = 0;
    while (offset !== null) {
      const page = await client.list(offset);
      all.push(...page.items);
      offset = page.nextOffset;
    }
    return all.sort((a, b) => b.updatedAt - a.updatedAt);
  }, [client]);
  const refresh = useMemo(() => () => load().then(value => { setItems(value); setError(null); }).catch(setError), [load]);
  useEffect(() => {
    let alive = true, timer: ReturnType<typeof setTimeout> | undefined;
    const later = () => { clearTimeout(timer); timer = setTimeout(() => { if (alive) void refresh(); }, 250); };
    const stop = subscribeAndLoad(() => ctx.events.subscribe("agent.conversation_changed", payload => {
      if ((payload as { workspaceId: string }).workspaceId === ctx.workspace.identity.get()?.workspaceId) later();
    }, { onReopen: later }), refresh, setError);
    return () => { alive = false; clearTimeout(timer); stop(); };
  }, [ctx, refresh]);
  return { items, error, refresh };
}
