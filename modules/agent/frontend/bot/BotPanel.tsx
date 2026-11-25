import { t } from "../i18n";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import {
  TxButton, TxContextMenu, TxDialog, TxIcon, TxIconButton, TxInput, TxMenu, TxSearch, useTxToast, type ModuleContext, type TxMenuItem,
} from "@talyx/sdk";
import { Composer, type Mention } from "../chat/Composer";
import { AnswerReference, ItemView, Working, type ItemActions } from "../chat/Items";
import { DragEdge, useDragWidth } from "../chat/resize";
import { Markdown, inline } from "../cards/markdown";

import type { ModelChoice } from "../chat/model";
import { AgentClient } from "../data/client";
import type { AgentMemory } from "../data/types";
import { chatFromSnapshot, chatModelChoices } from "../chat/live";
import { Avatar, GroupAvatar, type Dot } from "./Avatar";
import { NewBot, NewGroup, type Draft } from "./Create";
import { Rail, type RailPage } from "./Rail";
import { displayTime } from "./model";
import type { Block, Bot, Entry, Group, Thread } from "./model";
import { botRequest, RailContext, readData, type BotLibrary, type Listing, type LiveThread } from "./api";
import { WakeIcon, wakeText } from "./wake";

/* The Bot panel, copied from Grok Bot: bots and groups in a sidebar (pinned
   ones as tiles on top, the rest under the user's own sections, hidden ones
   folded at the bottom), the chosen chat in the middle with the bot's
   messages as bubbles, its routines and settings in a rail on the right.
   TALYX adds: each run marked with what woke it, run history and spend in the
   rail, Pause in the head. */

type View = { kind: "chat"; id: string } | { kind: "new-bot" } | { kind: "new-group" };

const NEW_DRAFT: Draft = { name: "", look: { shape: "circle", tint: "blue" } };

export function BotPanel({ ctx, mode, initialLine }: { ctx: ModuleContext; mode?: ReactNode; initialLine?: string }) {
  const toast = useTxToast();
  const [bots, setBots] = useState<Bot[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [sections, setSections] = useState<string[]>([]);
  const [models, setModels] = useState<ModelChoice[]>([]);
  /** What the composer can attach: the summary of the view the user is looking at, if any. */
  const [viewSummary, setViewSummary] = useState<string | null>(null);
  const [library, setLibrary] = useState<BotLibrary>({ skills: [], knowledge: [], channels: [] });
  const [folded, setFolded] = useState<string[]>([]);
  const [view, setView] = useState<View>({ kind: "new-bot" });
  const [raw, setRaw] = useState<LiveThread | null>(null);
  const initial = useRef(true);
  const [client] = useState(() => new AgentClient(ctx));

  const [rail, setRail] = useState<{ open: boolean; page: RailPage }>({ open: false, page: { kind: "overview" } });
  const [search, setSearch] = useState("");
  const [showHidden, setShowHidden] = useState(false);
  const [menu, setMenu] = useState<{ id: string; x: number; y: number } | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(NEW_DRAFT);
  const [deleting, setDeleting] = useState<Entry | null>(null);
  const [reply, setReply] = useState<{ bot: string; text: string } | null>(null);
  const [prefill, setPrefill] = useState<{ text: string; n: number } | undefined>(undefined);
  const [moreUnreads, setMoreUnreads] = useState(false);
  const sideWidth = useDragWidth("bot-list", 200, 420);
  const root = useRef<HTMLDivElement>(null);
  const stream = useRef<HTMLDivElement>(null);
  const side = useRef<HTMLDivElement>(null);

  const byId = new Map(bots.map(bot => [bot.id, bot]));
  const current = view.kind === "chat" ? view.id : null;
  const bot = current ? byId.get(current) ?? null : null;
  const group = current ? groups.find(item => item.id === current) ?? null : null;
  const thread: Thread | null =
    raw && raw.id === current
      ? {
          ...raw,
          blocks: raw.blocks.map((block) => ({
            ...block,
            items: block.snapshot ? chatFromSnapshot(block.snapshot, models).items : block.items,
          })),
        }
      : null;
  const fail = useCallback(
    (reason: unknown) =>
      toast({
        title: t("Bot request failed"),
        detail: reason instanceof Error ? reason.message : String(reason),
        tone: "failed",
      }),
    [toast],
  );
  const pending = useRef<Promise<unknown>>(Promise.resolve());
  const command = useCallback(
    <T,>(op: string, value: Record<string, unknown> = {}) => {
      const work = pending.current.then(() => botRequest<T>(ctx, op, value));
      pending.current = work.catch(fail);
      return work;
    },
    [ctx, fail],
  );
  const version = useRef(0);
  const refresh = useCallback(async () => {
    const request = ++version.current;
    const [listing, line] = await Promise.all([
      botRequest<Listing>(ctx, "list"),
      current ? botRequest<LiveThread>(ctx, "read", { id: current }) : null,
    ]);
    if (request !== version.current) return;
    setBots(listing.bots);
    setGroups(listing.groups);
    setSections(listing.sections);
    setRaw(line);
    if (initial.current) {
      initial.current = false;
      const entries = [...listing.bots, ...listing.groups];
      const first = entries.find((e) => e.id === initialLine) ?? entries.find((e) => !e.hidden);
      if (first) setView({ kind: "chat", id: first.id });
      else if (listing.bots.length || listing.groups.length) setView({ kind: "chat", id: "" });
    }
  }, [ctx, current, initialLine]);
  useEffect(() => {
    void client
      .conversation<{ view: string | null }>("composer")
      .then(value => setViewSummary(value.view))
      .catch(fail);
  }, [client, fail]);
  useEffect(() => {
    void client
      .readSettings()
      .then((data) => setModels(chatModelChoices(data)))
      .catch(fail);
  }, [client, fail]);
  useEffect(() => {
    let active = true,
      timer: number | undefined;
    const socket = ctx.routes.websocket("bot-events");
    const load = () => {
      if (!active) return;
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        void refresh().catch(fail);
      }, 40);
    };
    socket.addEventListener("message", load);
    void refresh().catch(fail);
    return () => {
      active = false;
      version.current++;
      window.clearTimeout(timer);
      socket.close();
    };
  }, [ctx, refresh, fail]);
  useEffect(() => {
    const update = () =>
      void botRequest(ctx, "presence", {
        id: ctx.visible.get() && document.visibilityState === "visible" ? current : null,
        panelId: ctx.panelRef?.panelId,
      }).catch(fail);
    const unwatch = ctx.visible.watch(update);
    update();
    document.addEventListener("visibilitychange", update);
    return () => {
      unwatch();
      document.removeEventListener("visibilitychange", update);
      void botRequest(ctx, "presence", { id: null, panelId: ctx.panelRef?.panelId }).catch(fail);
    };
  }, [ctx, current, fail]);
  const loadLibrary = () => {
    void botRequest<BotLibrary>(ctx, "library").then(setLibrary).catch(fail);
  };
  const mutate = (op: string, value: Record<string, unknown>) => {
    void command(op, value)
      .then(refresh)
      .catch(() => undefined);
  };

  const wide = () => (root.current?.clientWidth ?? 0) > 1000;

  useLayoutEffect(() => setRail(value => ({ ...value, open: wide() })), []);
  // Narrowing past the rail's room closes it: it never covers the chat on its own
  useEffect(() => {
    let was = wide();
    const observer = new ResizeObserver(() => { const now = wide(); if (was && !now) setRail(value => ({ ...value, open: false })); was = now; });
    observer.observe(root.current!);
    return () => observer.disconnect();
  }, []);
  useLayoutEffect(() => { stream.current?.scrollTo({ top: stream.current.scrollHeight }); }, [current, thread?.blocks.length]);

  const changeBot = (id: string, change: Partial<Bot>) => {
    const previous = byId.get(id);
    if (!previous) return;
    if (change.routines) {
      const removed = previous.routines.find((r) => !change.routines!.some((n) => n.id === r.id));
      if (removed) mutate("delete_routine", { id: removed.id });
      else {
        const changed = change.routines.find(
          (r) => r.active !== previous.routines.find((n) => n.id === r.id)?.active,
        );
        if (changed) mutate("toggle_routine", { id: changed.id, active: changed.active });
      }
      return;
    }
    const { state, spend, ...fields } = change;
    setBots((existing) => existing.map((b) => (b.id === id ? { ...b, ...change } : b)));
    mutate("patch", {
      id,
      changes: { ...fields, ...(state && { paused: state === "paused" }), ...(spend && { limit: spend.limit }) },
    });
  };
  const changeGroup = (id: string, changes: Partial<Group>) => {
    mutate("patch", { id, changes });
  };

  const choose = (next: View) => {
    setView(next); setReply(null); setPrefill(undefined);
    setRail({ open: next.kind === "chat" && wide(), page: { kind: "overview" } });

  };
  const actions: ItemActions = {
    open: (ref) => {
      if (ref.target) void ctx.platform.panels.openTarget(ref.target).catch(fail);
      else if (ref.panel) void client.openPanel(ref.panel).catch(fail);
      else if (ref.path) {
        const block = raw?.blocks.find(b => b.snapshot?.cards.some(c => c.type === "answer" && c.attachments.includes(ref.id)));
        if (block?.snapshot) void client.conversation("open_file", {conversationId:block.snapshot.conversation.id,path:ref.path}).catch(fail);
      } else if (ref.url) window.open(ref.url, "_blank", "noopener,noreferrer");
      else void client.openReference(ref.id).catch(fail);
    },
    pane: () => setRail({ open: true, page: { kind: "overview" } }),
    copy: (text) => {
      void navigator.clipboard.writeText(text).catch(fail);
    },
    retry: (cardId) => {
      const block = raw?.blocks.find((b) => b.snapshot?.cards.some((c) => c.id === cardId));
      if (block?.snapshot) mutate("retry", { id: block.id, cardId });
    },
    fork: () => undefined,
    failure: () => ctx.platform.settings.open("agent.models"),
    decide: (id, decision, reason) => {
      const card = raw?.blocks
        .flatMap((b) => b.snapshot?.cards ?? [])
        .find((c) => c.id === id && c.type === "approval");
      if (card?.type === "approval")
        void client
          .decide(id, card.version, decision === "always" ? "allow_conversation" : decision, reason)
          .then(refresh)
          .catch(fail);
    },
    undo: (id, undone) => {
      const block = raw?.blocks.find((b) => b.snapshot?.cards.some((c) => c.id === id));
      if (!block?.snapshot) return;
      // A "Remembered" line undoes that one memory, as in a chat; the changes card undoes the whole run.
      if (block.snapshot.cards.some((c) => c.id === id && c.type === "memory_saved"))
        void client
          .conversation("memory_decision", { conversationId: block.snapshot.conversation.id, cardId: id, direction: undone ? "undo" : "redo" })
          .then(refresh)
          .catch(fail);
      else mutate("revert_run", { id: block.id, direction: undone ? "undo" : "redo" });
    },
  };
  /** A bot's own memories, every page of them, from Agent's memory service. */
  const memories = async (id: string) => {
    const all: AgentMemory[] = [];
    for (let offset: number | null = 0; offset !== null; ) {
      const page = await client.memoryList({ botId: id, status: "active", offset });
      all.push(...page.items);
      offset = page.nextOffset;
    }
    return all;
  };

  const entries: Entry[] = [
    ...bots.map(item => ({ kind: "bot" as const, bot: item })),
    ...groups.map(item => ({ kind: "group" as const, group: item })),
  ];
  const facts = (entry: Entry) => entry.kind === "bot" ? entry.bot : entry.group;
  const match = (entry: Entry) => !search || facts(entry).name.toLowerCase().includes(search.toLowerCase());
  const pinned = entries.filter(entry => facts(entry).pinned && !facts(entry).hidden && match(entry));
  const listed = entries.filter(entry => !facts(entry).pinned && !facts(entry).hidden && match(entry)).sort((a, b) => Date.parse(facts(b).last.at) - Date.parse(facts(a).last.at));
  const hidden = entries.filter(entry => facts(entry).hidden && match(entry));
  const looks = (members: string[]) => members.map(id => byId.get(id)!.look);
  const dotOf = (entry: Entry): Dot => entry.kind === "bot" && entry.bot.state === "approval" ? "act" : facts(entry).unread ? "unread" : undefined;
  const allHidden = !search && entries.length > 0 && entries.every(entry => facts(entry).hidden);

  // Grok Bot's "More unreads": a pill at the foot of the sidebar while an unread row is out of view below.
  const checkUnreads = () => {
    const element = side.current;
    if (!element) return;
    const bottom = element.getBoundingClientRect().bottom;
    setMoreUnreads(below(element, bottom).length > 0);
  };
  useEffect(() => {
    checkUnreads();
    const observer = new ResizeObserver(checkUnreads);
    if (side.current) observer.observe(side.current);
    return () => observer.disconnect();
  });

  /** Rejects when the message did not go: the box puts the text back, and the toast says why. */
  const send = (text: string, includeView = false, paths: string[] = [], uploads: File[] = []) => {
    if (!current) return Promise.resolve();
    const args = {
      id: current,
      text,
      reply,
      includeView: !!viewSummary && includeView,
    };
    const work = (async () => {
      const attachments: Record<string, string[]> = {};
      if (paths.length || uploads.length) {
        const recipients = await botRequest<Record<string, string>>(ctx, "recipients", args);
        for (const [member, conversationId] of Object.entries(recipients)) {
          const ids = paths.length
            ? await client.conversation<string[]>("attach_paths", { conversationId, paths })
            : [];
          for (const file of uploads) ids.push((await client.upload(conversationId, file)).id);
          attachments[member] = ids;
        }
      }
      const { skipped } = await botRequest<{ skipped: { botId: string; name: string; reason: string }[] }>(ctx, "send", { ...args, attachments });
      setReply(null);
      // Sent to the others: the message is posted, so nothing is put back to send again.
      for (const item of skipped) toast({ title: t("{name} didn't get this message", { name: item.name }), detail: item.reason, tone: "info" });
      void refresh().catch(fail);
    })();
    work.catch(fail);
    return work;
  };
  const stopRun = () => {
    if (current) mutate("stop", { id: current });
  };
  const create = (made: Draft) => {
    void command<{ id: string }>("create", { ...made })
      .then(async (result) => {
        if (made.rrule)
          await command("save_routine", {
            botId: result.id,
            name: made.name,
            instruction: made.duty,
            when: made.when,
            zone: made.zone,
            rrule: made.rrule,
          });
        choose({ kind: "chat", id: result.id });
        setDraft(NEW_DRAFT);
      })
      .catch(() => undefined);
  };

  const setEntry = (entry: Entry, change: Partial<Bot & Group>) => entry.kind === "bot" ? changeBot(entry.bot.id, change) : changeGroup(entry.group.id, change);
  const newSection = (entry?: Entry) => {
    let name = t("New section"), n = 2;
    while (sections.includes(name)) name = t("New section {number}", { number: n++ });
    mutate("section",{name});
    if (entry) setEntry(entry, { section: name, pinned: false });
    setRenaming(`section:${name}`);
  };
  const entryMenu = (id: string): TxMenuItem[] => {
    const entry = entries.find(item => facts(item).id === id)!;
    const value = facts(entry);
    return [
      ...entry.kind === "bot" ? [{ id: "settings", label: t("Settings"), icon: "settings" as const, onSelect: () => { setView({ kind: "chat", id }); setRail({ open: true, page: { kind: "settings", bot: id } }); } }] : [],
      { id: "rename", label: t("Rename"), icon: "edit", onSelect: () => setRenaming(id) },
      { id: "pin", label: value.pinned ? t("Unpin") : t("Pin"), icon: "bookmark", separated: true, onSelect: () => setEntry(entry, { pinned: !value.pinned }) },
      { id: "hide", label: value.hidden ? t("Show in sidebar") : t("Hide from sidebar"), icon: value.hidden ? "eye" : "eye-off", onSelect: () => setEntry(entry, { hidden: !value.hidden }) },
      ...entry.kind === "bot" ? [{ id: "pause", label: entry.bot.state === "paused" ? t("Resume") : t("Pause"), icon: entry.bot.state === "paused" ? "play" as const : "pause" as const,
        onSelect: () => changeBot(id, { state: entry.bot.state === "paused" ? "idle" : "paused" }) }] : [],
      // Grok Bot's "Move to": the user's sections, the unnamed rest, or a new section
      ...sections.map((section, index) => ({ id: `move-${section}`, label: section, icon: "folder" as const, group: index === 0 ? t("Move to") : undefined, separated: index === 0,
        checked: value.section === section && !value.pinned, onSelect: () => setEntry(entry, { section, pinned: false }) })),
      { id: "move-none", label: t("No section"), icon: "minus", group: sections.length ? undefined : t("Move to"), separated: !sections.length, checked: !value.section && !value.pinned,
        onSelect: () => setEntry(entry, { section: "", pinned: false }) },
      { id: "move-new", label: t("New section…"), icon: "plus", onSelect: () => newSection(entry) },
      { id: "delete", label: entry.kind === "bot" ? t("Delete bot…") : t("Delete group…"), icon: "trash", tone: "danger" as const, separated: true, onSelect: () => setDeleting(entry) },
    ];
  };
  const sectionMenu = (section: string): TxMenuItem[] => [
    { id: "rename", label: t("Rename section"), icon: "edit", onSelect: () => setRenaming(`section:${section}`) },
    { id: "delete", label: t("Delete section"), icon: "trash", tone: "danger", separated: true, onSelect: () => {
      mutate("section",{old:section});
    } },
  ];
  const rename = (id: string, name: string) => {
    if (name.trim()) mutate("patch",{id,changes:{name:name.trim()}});
    setRenaming(null);
  };
  const renameSection = (from: string, to: string) => {
    const name = to.trim();
    if (name && name !== from && !sections.includes(name)) {
      mutate("section",{old:from,name});
    }
    setRenaming(null);
  };
  /** The "…" on a sidebar entry, shown on hover like the chat panel. */
  const more = (id: string, name: string) => <span className="bot-more"><TxMenu label={name} align="end" items={entryMenu(id)}
    trigger={<TxIconButton size="sm" icon="more" label={t("More for {name}", { name })} />} /></span>;
  const context = (id: string) => (event: React.MouseEvent) => { event.preventDefault(); setMenu({ id, x: event.clientX, y: event.clientY }); };

  const row = (entry: Entry) => {
    const value = facts(entry);
    const picture = entry.kind === "bot" ? <Avatar look={entry.bot.look} state={entry.bot.state} dot={dotOf(entry)} /> : <GroupAvatar looks={looks(entry.group.members)} dot={dotOf(entry)} />;
    if (renaming === value.id) return <div key={value.id} className="bot-entry bot-renaming">{picture}
      <TxInput autoFocus defaultValue={value.name} aria-label={t("Name")} className="bot-rename"
        onBlur={event => rename(value.id, event.target.value)} onKeyDown={event => { if (event.key === "Enter") rename(value.id, event.currentTarget.value); if (event.key === "Escape") setRenaming(null); }} />
    </div>;
    return <div key={value.id} className="bot-entry">
      <TxButton variant="row" className="bot-row" aria-current={value.id === current || undefined} data-unread={value.unread || undefined}
        title={value.name} onClick={() => choose({ kind: "chat", id: value.id })} onContextMenu={context(value.id)}>
        {picture}
        <span className="bot-row-copy">
          <span className="bot-row-top"><span className="bot-row-name">{value.name}</span><span className="bot-row-time">{displayTime(value.last.at)}</span></span>
          <span className="bot-row-bottom"><span className="bot-row-text">{value.last.text.replace(/\[\[ref:([^\]]+)\]\]/g,(_,id:string)=>"@"+(byId.get(id)?.name ?? id))}</span></span>
        </span>
      </TxButton>
      {more(value.id, value.name)}
    </div>;
  };
  /** The entry for what is being made, selected while its page is open, like Grok Bot's "New Bot" row. */
  const making = view.kind !== "chat" && <div className="bot-entry">
    <TxButton variant="row" className="bot-row" aria-current>
      {view.kind === "new-bot" ? <Avatar look={draft.look} /> : <span className="bot-new-mark"><TxIcon name="messages" /></span>}
      <span className="bot-row-copy"><span className="bot-row-top"><span className="bot-row-name">
        {view.kind === "new-bot" ? draft.name || (bots.length ? t("New bot") : t("Create your first bot")) : t("New group")}</span></span></span>
    </TxButton>
  </div>;

  const head = bot ?? group;
  const running = thread?.running ?? false;
  const memberMentions: Mention[] = group ? [
    ...group.members.map(id => byId.get(id)!).map(member => ({ id: member.id, name: member.name, detail: member.intro, lead: <Avatar look={member.look} size="xs" /> })),
    { id: "everyone", name: "everyone", detail: t("Every bot in this group"), lead: <GroupAvatar looks={looks(group.members)} size="sm" /> },
  ] : [];
  const unsectioned = listed.filter(entry => !facts(entry).section || !sections.includes(facts(entry).section!));
  const nothingMatches = search && !pinned.length && !listed.length && !hidden.length;

  return <div ref={root} className="bot-panel">
    <aside className="bot-side" aria-label={t("Bots and groups")} style={{width:sideWidth.width ?? undefined}}>
      <DragEdge side="right" label={t("Resize the bot list")} drag={sideWidth} />
      {mode && <div className="bot-mode">{mode}</div>}
      <div className="bot-side-head">
        <TxSearch className="bot-search" value={search} onValueChange={setSearch} placeholder={t("Search")} />
        <TxMenu label={t("New")} align="end" items={[
          { id: "bot", label: t("New bot"), icon: "plus", onSelect: () => choose({ kind: "new-bot" }) },
          { id: "group", label: t("New group"), icon: "messages", onSelect: () => choose({ kind: "new-group" }) },
          { id: "section", label: t("New section"), icon: "list", separated: true, onSelect: () => newSection() },
        ]} trigger={<TxIconButton size="sm" icon="plus" label={t("New")} className="bot-new" />} />
      </div>
      <div className="bot-side-wrap">
        <div className="bot-side-body" ref={side} onScroll={checkUnreads}>
          {making}
          {nothingMatches && <p className="bot-side-none">{t("No bots or groups match “{query}”", { query: search })}</p>}
          {!bots.length && !groups.length && view.kind !== "chat" && <p className="bot-side-none bot-side-empty">{t("No chats yet")}</p>}
          {pinned.length > 0 && <div className="bot-pins">{pinned.map(entry => {
            const value = facts(entry);
            return <div key={value.id} className="bot-entry bot-pin-entry">
              <TxButton variant="row" className="bot-pin" aria-current={value.id === current || undefined} data-unread={value.unread || undefined} title={value.name}
                onClick={() => choose({ kind: "chat", id: value.id })} onContextMenu={context(value.id)}>
                {entry.kind === "bot" ? <Avatar look={entry.bot.look} size="lg" state={entry.bot.state} dot={dotOf(entry)} /> : <GroupAvatar looks={looks(entry.group.members)} dot={dotOf(entry)} />}
                <span className="bot-pin-name">{value.name}</span>
              </TxButton>
              {more(value.id, value.name)}
            </div>;
          })}</div>}
          {unsectioned.length > 0 && <div className="bot-rows">{unsectioned.map(row)}</div>}
          {sections.map(section => {
            const rows = listed.filter(entry => facts(entry).section === section);
            if (search && !rows.length) return null;
            const isFolded = folded.includes(section);
            return <section key={section} className="bot-section" aria-label={section}>
              {renaming === `section:${section}`
                ? <TxInput autoFocus defaultValue={section} aria-label={t("Section name")} className="bot-section-rename"
                  onBlur={event => renameSection(section, event.target.value)}
                  onKeyDown={event => { if (event.key === "Enter") renameSection(section, event.currentTarget.value); if (event.key === "Escape") setRenaming(null); }} />
                : <div className="bot-section-head">
                  <TxButton variant="row" size="sm" className="bot-section-toggle" aria-expanded={!isFolded}
                    onClick={() => setFolded(all => isFolded ? all.filter(item => item !== section) : [...all, section])}>
                    <span className="bot-section-title">{section}</span><span className="bot-fold-indicator"><TxIcon name={isFolded ? "chevron" : "chevron-down"} /></span>
                  </TxButton>
                  <span className="bot-section-more"><TxMenu label={section} align="end" items={sectionMenu(section)} trigger={<TxIconButton size="sm" icon="more" label={t("More for {section}", { section })} />} /></span>
                </div>}
              {!isFolded && rows.length > 0 && <div className="bot-rows">{rows.map(row)}</div>}
            </section>;
          })}
          {hidden.length > 0 && <div className="bot-hidden">
            <TxButton variant="row" size="sm" className="bot-hidden-head" title={t("Hidden bots")} aria-expanded={showHidden} onClick={() => setShowHidden(!showHidden)}>
              <span>{t("Hidden bots")}</span><span className="bot-row-time">{hidden.length}</span>
            </TxButton>
            {showHidden && <div className="bot-rows">{hidden.map(row)}</div>}
          </div>}
        </div>
        {moreUnreads && <div className="bot-more-unreads"><TxButton size="sm" variant="quiet" onClick={() => {
          const element = side.current!;
          below(element, element.getBoundingClientRect().bottom)[0]?.scrollIntoView({ block: "center", behavior: "smooth" });
        }}><TxIcon name="arrow-down" />{t("More unreads")}</TxButton></div>}
      </div>
    </aside>

    <div className="bot-main">
      {view.kind === "new-bot" && <NewBot draft={draft} onDraft={setDraft} onCreate={create} mode={mode} />}
      {view.kind === "new-group" && <NewGroup bots={bots.filter(item => !item.hidden)} mode={mode} onCreate={members => {
        void command<{id:string}>("create_group",{members}).then(({id})=>choose({kind:"chat",id})).catch(()=>undefined);
      }} />}
      {view.kind === "chat" && allHidden && <div className="bot-all-hidden">
        <p className="bot-all-hidden-title">{t("All your bots are hidden")}</p>
        <TxButton onClick={() => setShowHidden(true)}>{t("Show hidden bots")}</TxButton>
      </div>}
      {view.kind === "chat" && !allHidden && head && thread && <>
        <div className="bot-head">
          {mode && <span className="bot-head-mode">{mode}</span>}
          {/* The name opens details: a bot's Settings, a group's members */}
          {bot
            ? <TxButton variant="tool" size="sm" className="bot-title" title={t("Settings")} onClick={() => setRail({ open: true, page: { kind: "settings", bot: bot.id } })}>
              <Avatar look={bot.look} size="xs" /><span className="bot-title-name">{head.name}</span>
            </TxButton>
            : <TxButton variant="tool" size="sm" className="bot-title" title={t("Members")} onClick={() => setRail({ open: true, page: { kind: "overview" } })}>
              <GroupAvatar looks={looks(group!.members)} size="sm" /><span className="bot-title-name">{head.name}</span>
            </TxButton>}
          {bot?.state === "paused" && <span className="bot-head-state">{t("Paused")}</span>}
          <span className="bot-spacer" />
          {/* Stop lives in the message box, as in the chat panel; the head keeps Pause, which also holds the routines */}
          {bot && (bot.state === "paused"
            ? <TxButton size="sm" variant="quiet" onClick={() => changeBot(bot.id, { state: "idle" })}><TxIcon name="play" />{t("Resume")}</TxButton>
            : <TxButton size="sm" variant="quiet" onClick={() => changeBot(bot.id, { state: "paused" })}><TxIcon name="pause" />{t("Pause")}</TxButton>)}
          {group && group.members.some(id => byId.get(id)?.state === "running") && <TxButton size="sm" variant="quiet"
            onClick={stopRun}>{t("Stop all")}</TxButton>}
          {bot && <TxIconButton size="sm" icon="settings" label={t("Bot settings")} tone={rail.open && rail.page.kind === "settings" ? "active" : "default"}
            onClick={() => setRail({ open: true, page: { kind: "settings", bot: bot.id } })} />}
          <TxIconButton size="sm" icon="columns" label={rail.open ? t("Hide details") : t("Show details")} tone={rail.open ? "active" : "default"}
            onClick={() => setRail({ open: !rail.open, page: { kind: "overview" } })} />
        </div>
        <div className="bot-body">
          <div className="bot-center">
            <div className="bot-stream" ref={stream}>
              <div className="bot-column">
                {thread.blocks.length === 0 && <p className="bot-empty">{group ? t("Say something to the group, or @ a bot") : t("No messages yet")}</p>}
                {thread.blocks.map((block, index) => <BlockView key={block.id} block={block} previous={thread.blocks[index - 1]} bots={byId} self={bot?.id} inGroup={group !== null} actions={actions}
                  onReply={(speaker, text) => setReply({ bot: speaker, text })} />)}
                {running && <Working since={Math.max(0,Math.floor(Date.now()/1000-(raw?.blocks.at(-1)?.snapshot?.turns[0]?.startedAt ?? Date.now()/1000)))} />}
              </div>
            </div>
            <div className="bot-column bot-foot">
              <Composer key={current} bare placeholder={t("Message {name}", { name: head.name })} mentions={group ? memberMentions : undefined} prefill={prefill}
                extra={reply && <span className="chat-strip-item bot-replying"><TxIcon name="message" />{t("Replying to")} {byId.get(reply.bot)?.name}
                  <span className="bot-replying-text">{reply.text}</span>
                  <TxIconButton size="sm" icon="close" label={t("Don't reply")} onClick={() => setReply(null)} /></span>}
                choices={{ model: bot?.model ?? models[0]?.id ?? "", thinking: bot?.thinking ?? "", subagents: true, permission: bot?.permission ?? "auto", plan: false }}
                models={models} commands={[]} findFiles={client.findFiles} view={viewSummary} running={running} queued={[]} allowedCount={0}
                onChoices={() => undefined} onSend={send} onQueue={send} onQueued={() => undefined} onStop={stopRun}
                onManageModels={() => ctx.platform.settings.open("agent.models")} />
            </div>
          </div>
          {rail.open && <RailContext.Provider value={{models,library,loadLibrary,routine:(op,args)=>command(op,args).then(refresh),manageModels:()=>ctx.platform.settings.open("agent.models"),undo:(id,direction)=>mutate("revert_run",{id,direction}),upload:(id,file)=>{void readData(file).then(data=>command("upload",{id,name:file.name,data:data.split(",")[1]})).then(loadLibrary).catch(fail);},
            memories:id=>memories(id).catch(reason=>{fail(reason);return null;}),
            editMemory:(note,body,remove=false)=>client.memoryEdit(note.id,note.revision,body,remove).then(()=>undefined,reason=>{fail(reason);})}}><Rail page={rail.page} bot={bot} group={group} bots={byId} onPage={page => setRail({ open: true, page })}
            onBot={changeBot} onGroup={changeGroup} onPrefill={text => setPrefill(value => ({ text, n: (value?.n ?? 0) + 1 }))} /></RailContext.Provider>}
        </div>
      </>}
    </div>
    {menu && <TxContextMenu open onOpenChange={open => { if (!open) setMenu(null); }} point={{ x: menu.x, y: menu.y }} label={t("Bot")} items={entryMenu(menu.id)} />}
    {/* Grok Bot's delete: says what goes, offers Hide instead */}
    <TxDialog open={deleting !== null} onOpenChange={open => { if (!open) setDeleting(null); }} title={deleting ? t("Delete {facts}?", { facts: facts(deleting).name }) : ""}
      footer={deleting && <>
        <TxButton onClick={() => setDeleting(null)}>{t("Cancel")}</TxButton>
        <TxButton onClick={() => { setEntry(deleting, { hidden: true }); setDeleting(null); }}>{t("Hide instead")}</TxButton>
        <TxButton variant="primary" onClick={() => {
          const id = facts(deleting).id;
          void command("delete",{id}).then(()=>{if(current===id)choose({kind:"new-bot"});return refresh();}).catch(()=>undefined);
          setDeleting(null);
        }}>{t("Delete")}</TxButton></>}>
      <p className="bot-dialog-text">{deleting?.kind === "bot"
        ? t("Its chat, settings, memory, routines and run history are erased, and it leaves its groups. This can't be undone. Hiding keeps all of it out of the sidebar instead.")
        : t("The group's chat is erased. Its bots stay. This can't be undone.")}</p>
    </TxDialog>
  </div>;
}

/** Unread entries whose middle is below `bottom` (the list's lower edge on screen). */
function below(list: HTMLElement, bottom: number): HTMLElement[] {
  return [...list.querySelectorAll<HTMLElement>("[data-unread]")].filter(row => { const box = row.getBoundingClientRect(); return box.top + box.height / 2 > bottom; });
}


function BlockView({ block, previous, bots, self, inGroup, actions, onReply }: {
  block: Block; previous?: Block; bots: Map<string, Bot>; self?: string; inGroup: boolean; actions: ItemActions; onReply(bot: string, text: string): void;
}) {
  const speaker = block.bot ? bots.get(block.bot)! : null;
  const sameDay = previous && new Date(previous.at).toDateString() === new Date(block.at).toDateString();
  const showTime = !previous || !sameDay || block.wake && block.wake.kind !== "you";
  const mention = (id: string, key: string) => bots.has(id)
    ? <span key={key} className="bot-mention"><Avatar look={bots.get(id)!.look} size="xs" />{bots.get(id)!.name}</span>
    : <span key={key}>{id}</span>;
  const own = block.items.filter(item => item.kind !== "user");
  return <>
    {showTime && <div className="bot-mark">
      <span>{displayTime(block.at)}</span>
      {block.wake && block.wake.kind !== "you" && <><span>·</span><WakeIcon wake={block.wake} /><span>{wakeText(block.wake)}</span></>}
    </div>}
    {block.items.filter(item => item.kind === "user").map(item => item.kind === "user" && <div key={item.id} className="chat-user">
      {block.reply && <div className="bot-quote"><TxIcon name="message" /><span className="bot-quote-name">{bots.get(block.reply.bot)?.name}</span><span className="bot-quote-text">{block.reply.text}</span></div>}
      {item.view && <div className="chat-user-context"><span className="chat-chip"><TxIcon name="chart" /><span>{item.view}</span></span></div>}
      <div className="chat-bubble">{inline(item.text, { reference: mention, link: (label, url, key) => <a key={key} className="chat-link" href={url}>{label}</a> })}</div>
    </div>)}
    {own.length > 0 && <div className="bot-said" data-group={inGroup || undefined}>
      {inGroup && speaker && <Avatar look={speaker.look} size="sm" className="bot-said-avatar" />}
      <div className="bot-said-items">
        {inGroup && (speaker || block.bot?.startsWith("former:")) && <span className="bot-said-name">{speaker?.name ?? block.bot?.slice(7)}</span>}
        {own.map(item => item.kind === "answer"
          ? <div key={item.id} className="bot-bubble-wrap">
            <Bubble>
              <Markdown text={item.text} renderers={{
                reference: (id, key) => bots.has(id) ? mention(id, key) : item.refs[id] ? <AnswerReference key={key} reference={item.refs[id]} open={actions.open} /> : id,
                link: (label, url, key) => <a key={key} className="chat-link" href={url} target="_blank" rel="noreferrer">{label}</a>,
              }} />
              {item.sources && <ol className="chat-sources">{item.sources.map(source => <li key={source.n}><a className="chat-link" href={"url" in source && typeof source.url==="string" ? source.url : undefined} target="_blank" rel="noreferrer">{source.title}</a> <span className="chat-meta">{source.site} · {source.published}</span></li>)}</ol>}
            </Bubble>
            {/* Grok Bot's Reply on a message: what you send next quotes it */}
            <span className="bot-bubble-actions">
              <TxIconButton size="sm" icon="message" label={t("Reply")} onClick={() => onReply(block.bot ?? self ?? "", plain(item.text))} />
              <TxIconButton size="sm" icon="copy" label={t("Copy")} onClick={() => actions.copy(item.text)} />
            </span>
          </div>
          : <div key={item.id} className="bot-item" data-kind={item.kind}><ItemView item={item} actions={actions} owner="bot" /></div>)}
      </div>
    </div>}
  </>;
}

/** A message's first line without its marks, for the reply quote. */
function plain(text: string): string {
  return text.replace(/\[\[ref:[^\]]+\]\]/g, "").replace(/（）|\(\)/g, "").replace(/[*#|`>-]/g, "").split("\n").find(line => line.trim())?.trim().slice(0, 90) ?? "";
}

/** A bot's message, as Grok Bot draws it: a bubble on the left. */
function Bubble({ children }: { children: ReactNode }) {
  return <div className="bot-bubble chat-answer">{children}</div>;
}
