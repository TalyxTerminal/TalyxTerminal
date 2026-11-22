import { t } from "../i18n";
import type { ModuleContext } from "@talyx/sdk";
import { createContext, useContext } from "react";
import { agentRequest } from "../data/client";
import type { AgentMemory, ConversationSnapshot } from "../data/types";
import type { ModelChoice } from "../chat/model";
import type { Block, Bot, Group, KnowledgeChoice, SkillChoice } from "./model";

export function botRequest<T>(ctx: ModuleContext, operation: string, value: Record<string, unknown> = {}): Promise<T> {
  return agentRequest<T>(ctx, "bots", operation, value);
}

export interface Listing {
  bots: Bot[];
  groups: Group[];
  sections: string[];
}
export interface LiveBlock extends Block {
  snapshot?: ConversationSnapshot;
}
export interface LiveThread {
  id: string;
  running: boolean;
  blocks: LiveBlock[];
}
export interface BotLibrary {
  skills: SkillChoice[];
  knowledge: KnowledgeChoice[];
  channels: { id: string; name: string }[];
}
interface RailActions {
  models: ModelChoice[];
  library: BotLibrary;
  manageModels(): void;
  undo(id: string, direction: "undo" | "redo"): void;
  routine(op: "save_routine" | "test_routine", value: Record<string, unknown>): Promise<unknown>;
  upload(id: string, file: File): void;
  loadLibrary(): void;
  /** The bot's own memories from Agent's memory service; null when they could not be read (the failure is shown). */
  memories(id: string): Promise<AgentMemory[] | null>;
  /** Change a memory's text, or delete it with `remove`. */
  editMemory(note: AgentMemory, body: string, remove?: boolean): Promise<void>;
}
export const RailContext = createContext<RailActions | null>(null);
export function useRailActions() {
  const value = useContext(RailContext);
  if (!value) throw new Error(t("Bot rail requires a mounted panel"));
  return value;
}

export function readData(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}
