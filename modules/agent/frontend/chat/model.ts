/* Chat and summary data projected from Agent's private conversation routes. */

import type { Wake } from "../bot/model";

export type ChatActivity = "idle" | "running" | "approval" | "failed";
export type Permission = "read_only" | "auto" | "full";

export interface ChatListItem { id: string; title: string; updatedAt: number; activity: ChatActivity; unread: boolean; pinned?: boolean; archived?: boolean }

/** An object in TALYX or a file the answer names; clicking it opens it where it lives: `where` is the module whose
    panel opens it. */
export interface ObjectRef { id: string; label: string; where?: string; target?: { module: string; panelType: string; objectId: string }; panel?: { type: string; panelId: string }; path?: string; url?: string }
export interface WebSource { n: number; title: string; site: string; published: string; url?: string }

/** What a step does, by its side effect only (never by module): reads, changes, deletes, runs code, sends or reaches outside. */
export type StepIcon = "read" | "change" | "delete" | "run" | "send";

export interface Step {
  id: string;
  label: string;
  state?: "running" | "failed";
  /** What a click on the step shows: the call's input and a slice of its result. */
  detail?: string;
  icon?: StepIcon;
  /** What the AI says between steps while it works; shown as text, not as a step. */
  note?: boolean;
}

export interface SubAgent { id: string; title: string; model: string; thinking: string; state: "queued" | "running" | "done" | "failed"; now: string; cost: string }
export interface TodoItem { text: string; state: "done" | "active" | "open" }
/** One thing a turn changed: a module object or a file, and what was done to it. */
export interface Change extends ObjectRef { what: string; reversible?: boolean }

/** Long work the chat started in a module (a backtest, a factor run); it goes on after the turn ends. `now`: what it is
    doing while it runs, how it ended once done. */
export interface Task { id: string; title: string; module: string; state: "running" | "done" | "failed"; now: string; target?: ObjectRef["target"] }

export type Item =
  | { kind: "user"; id: string; text: string; view?: string; files?: {id:string;name:string}[];
      /** Added to a running turn that was stopped before it read the message. */
      undelivered?: boolean }
  /** The work of one turn: listed while it runs, folded to "Worked for …" once the answer is in. */
  | { kind: "work"; id: string; seconds: number; running: boolean; steps: Step[]; interrupted?: boolean }
  /** The turn's to-do list, in the stream: one line that opens to the list. */
  | { kind: "todo"; id: string; items: TodoItem[] }
  | { kind: "agents"; id: string; agents: SubAgent[] }
  /** `expires`: work that woke without you (a schedule, a module event, a bot in a group) stops waiting at that time, then it is "expired". */
  | { kind: "approval"; id: string; action: string; target: string; lines: string[]; canAlwaysAllow: boolean; state: "waiting" | "allowed" | "denied" | "expired"; expires?: number }
  | { kind: "result"; id: string; title: string; subtitle: string; target?: ObjectRef["target"]; numbers: { label: string; value: string; tone?: "up" | "down" }[] }
  | { kind: "answer"; id: string; text: string; refs: Record<string, ObjectRef>; sources?: WebSource[] }
  | { kind: "changes"; id: string; changes: Change[]; undone: boolean }
  | { kind: "memory"; id: string; text: string; undone: boolean }
  | { kind: "error"; id: string; title: string; text: string; action?: string; retry?: boolean }
  /** Plan mode's result Plan card, then the question whether to carry it out. */
  | { kind: "plan"; id: string; title: string; text: string }
  | { kind: "plan-ask"; id: string; state: "waiting" | "accepted" | "skipped" }
  /** A line across the stream: the chat was compacted. */
  | { kind: "notice"; id: string; text: string }
  /** What started a turn that you didn't: the chat's schedule, marked like a bot's runs. */
  | { kind: "wake"; id: string; at: number; wake: Wake };

export interface Choices { model: string; thinking: string; subagents: boolean; permission: Permission; plan: boolean }
export interface Chat {
  id: string; title: string; activity: ChatActivity; items: Item[]; queued: string[]; choices: Choices; context: { used: number; window: number };
  tasks?: Task[];
}

/** A model the user connected, by connection, with the levels it takes. */
/** A chat's schedule scheduled tasks: when in words with its time zone, the next run, on or paused. */
export interface Schedule { id: string; chat: string; name: string; when: string; next: string; on: boolean }

export interface ModelChoice { id: string; connection: string; name: string; window: number; levels: string[]; defaultLevel: string }
export interface Command { name: string; detail: string; icon: "list" | "archive" | "compose" | "book" | "history"; kind: "command" | "skill" }
export interface FileChoice { path: string; place: "workspace" | "knowledge" }
