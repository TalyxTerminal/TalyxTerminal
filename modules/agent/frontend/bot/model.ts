import { intlLocale } from "@talyx/sdk";
import { t } from "../i18n";
import type { Item, Permission } from "../chat/model";

/* Bot panel data from Agent's private routes. */

/** A bot's picture: one of TALYX's shapes in one of its colours, with two eyes (after Grok Bot's). */
export type Shape = "circle" | "blob" | "square" | "pill" | "triangle" | "hexagon" | "cloud" | "drop";
export type Tint = "brown" | "red" | "yellow" | "green" | "teal" | "blue" | "purple" | "pink" | "grey";
/** `photo`: the user's own picture (Grok Bot's Upload tab), shown instead of the shape. */
export interface Look { shape: Shape; tint: Tint; photo?: { name: string; size: string; data: string } }

export type BotState = "idle" | "running" | "approval" | "paused" | "failed";

/** What woke a bot for one run; the stream marks each run with it. */
export type Wake =
  | { kind: "you" }
  | { kind: "mention"; name: string; bot: string }
  | { kind: "schedule"; routine: string }
  /** A module event the routine listens for, under the name the module declares ("Chart alert", "Market filing"). */
  | { kind: "event"; event: string; routine: string }
  /** A schedule that came due while TALYX was closed or the computer slept, run once on return. */
  | { kind: "catchup"; routine: string; due: string }
  /** A run cut off by a restart, run again from where it stopped. */
  | { kind: "resumed"; stopped: string };

export type Outcome = "done" | "quiet" | "running" | "waiting" | "failed" | "stopped" | "skipped";
/** What a run left behind without saying anything: a change or something remembered, each undoable. */
export interface Effect { id: string; kind: "change" | "memory"; label: string; what?: string; undone?: boolean; reversible?: boolean }
export interface Run { id: string; at: string; wake: Wake; outcome: Outcome; duration: string; cost: string; tokens: string; effects?: Effect[];
  /** A notification about the run that did not go out, and why. */
  notice: { action: string; code: string; message: string } | null }

export interface Routine {
  id: string; name: string; kind: "schedule" | "event";
  /** In words, with its time zone: "Weekdays at 09:00 New York", "Chart alert BTC RSI(14) below 30 · 4h". */
  when: string[];
  active: boolean; instruction: string; next?: string;
}

/** This month's spend by what woke the bot: each routine, your messages, other bots. */
export interface SpendPart { label: string; runs: number; unpriced: boolean; amounts: Record<string, string> }

export interface Bot {
  id: string; name: string; look: Look; intro: string; duty: string; state: BotState;
  pinned: boolean; hidden: boolean; unread: boolean; last: { at: string; text: string };
  /** The sidebar section the user put it in, like Grok Bot's; none is the unnamed rest. */
  section?: string;
  /** Skills picked from the library (by name) and knowledge picked from the knowledge base (by path); none to begin with. */
  skills: string[]; knowledge: string[];
  model: string; thinking: string; subagents?: boolean; permission: Permission; allowed: string[];
  /** Its own memories live in Agent's memory service (the rail reads them there). */
  memoryOn: boolean;
  notify: { needsYou: boolean; results: boolean; phone: string };
  routines: Routine[]; runs: Run[];
  /** `month` is this month's spend in the limit's currency, other currencies converted; null while `missingRates` have no exchange rate. */
  spend: { currency: string; unpriced: boolean; amounts: Record<string, string>; month: number | null; missingRates: string[]; limit: number | null; parts: SpendPart[] };
}

/** A skill a bot can be given: one of the global skills or one of this workspace's. */
export interface SkillChoice { name: string; detail: string; place: "global" | "workspace" }
/** A folder or file in the knowledge base a bot can be given: in a global knowledge folder (its name first) or the workspace's. */
export interface KnowledgeChoice { path: string; label: string; folder: string; kind: "folder" | "file"; size: string; place: "global" | "workspace" }

export interface Group { id: string; name: string; members: string[]; pinned: boolean; hidden: boolean; unread: boolean; last: { at: string; text: string }; section?: string }

/** One stretch of a thread: what woke it and what came of it. `bot` says who speaks, in a group. */
export interface Block {
  id: string; at: string; wake?: Wake; bot?: string; items: Item[];
  /** Your message was a reply to this bot message, like Grok Bot's Reply: a quoted line over it. */
  reply?: { bot: string; text: string };
}

export interface Thread { id: string; blocks: Block[]; running: boolean }

export type Entry = { kind: "bot"; bot: Bot } | { kind: "group"; group: Group };

export function displayTime(value: string): string {
  const date = new Date(value);
  const today = new Date();
  if (date.toDateString() === today.toDateString()) return date.toLocaleTimeString(intlLocale(), {hour:"2-digit", minute:"2-digit"});
  today.setDate(today.getDate()-1);
  const day = date.toDateString() === today.toDateString() ? t("Yesterday") : date.toLocaleDateString(intlLocale(), {month:"short",day:"numeric"});
  return day+" "+date.toLocaleTimeString(intlLocale(), {hour:"2-digit",minute:"2-digit"});
}
