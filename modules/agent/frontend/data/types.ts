import type { Json } from "@talyx/sdk";
import type { ProviderSite, SearchConfiguration } from "./providerTypes";
import type { Wake } from "../bot/model";

export type Permission = "read_only" | "auto" | "full";
export type TurnState = "queued" | "running" | "waiting_approval" | "waiting_business" | "stopping" | "completed" | "failed" | "cancelled" | "interrupted" | "budget_exceeded";
export interface ContextUsage { estimatedInputTokens: number | null; providerInputTokens: number | null; window: number; outputReserve: number; observedAt: number; isEstimate: boolean }
export interface ConversationActivity { running: boolean; waitingApproval: boolean }
export interface Conversation { planMode?: boolean; archived?: boolean; pinned?: boolean; queued?: string[]; id: string; workspaceId: string; title: string; modelId: string | null; thinking: Record<string, Json> | null; permission: Permission; memoryEnabled: boolean; childrenEnabled: boolean; context: ContextUsage | null; createdAt: number; updatedAt: number; activity: ConversationActivity; /** The conversation it is a copy of, in a copied workspace. */ copiedFrom?: string }
export interface ConversationListItem extends Conversation { activeTurn: Turn | null; eventWatermark: number; unread: boolean }
export interface Turn { id: string; conversationId: string; workspaceId: string; state: TurnState; text: string; createdAt: number; startedAt: number | null; finishedAt: number | null; error: Json;
  /** What started it: you, or (a run that woke by itself) its schedule or event. */
  wake: { kind: "user" } | Wake;
  /** Messages added while it ran that it has not read yet. */
  inbox: { messageId: string }[] }
export interface Source { id: string; url: string; title: string }
export type Reference = { id: string; workspaceId: string; label: string } & (
  | { kind: "source"; target: { url: string } }
  | { kind: "object"; target: { module: string; panelType: string; objectId: string } }
  | { kind: "panel"; target: { type: string; panelId: string } }
);
export interface Attachment { id: string; name: string; mime: string; size: number; digest: string; state: "complete" }
/** Fixed when a card or step is first written: the order it reads in never changes after that. */
export interface Stamp { createdAt: number; orderKey: string; updatedAt: number }
export interface Step extends Stamp { id: string; callId?: string; title: string; action?: string; op?: string | null; startedAt: number; finishedAt: number | null; state: string; details: Json; error?: Json }
/** What an approval asks: the action and its effects beyond reading (write, delete, code, external, funds). */
export interface Proposal { risks: string[] }
export type AgentCard = Stamp & {turnId?: string} & (
  | { id: string; type: "plan"; text: string; state: "waiting" | "accepted" | "skipped" }
  | { id: string; type: "changes"; items: {callId: string; label: string; objectId: string | null; kind: string; reversible: boolean; openTarget: {module: string; panelType: string; objectId: string} | null}[]; undone: boolean }
  | { id: string; type: "todos"; items: {content: string; status: "pending" | "in_progress" | "completed"}[] }
  | { id: string; type: "summary"; title: string; subtitle: string; keyNumbers: {label: string; value: string; unit: string; direction: "up" | "down" | "none"}[]; openTarget: {module: string; panelType: string; objectId: string} }
  | { id: string; type: "answer"; role: "user" | "assistant"; modelStepId?: string | null; text: string; references: Reference[]; sources: Source[]; view: Json; attachments: string[]; materials?:{id:string;name:string}[] }
  | { id: string; type: "process_group"; title: string; steps: Step[] }
  | { id: string; type: "thinking"; text: string; startedAt: number; finishedAt: number | null }
  | { id: string; type: "approval"; version: number; turnId: string; conversationId: string; childRunId: string | null; toolCallId: string; callId: string; action: string; input: Json; proposal: Proposal; state: "waiting" | "allowed" | "denied" | "consumed" | "interrupted" | "expired"; decision: string | null; reason: string; createdAt: number; expiresAt: number | null }
  | { id: string; type: "task" | "result"; callId: string; state: string; action: string; op: string | null; title: string; task?: {progress?: Json}; result: Json; summary: Json; error: Json; openTarget: {module: string; panelType: string; objectId: string} | null }
  | { id: string; type: "compaction"; startedAt: number; finishedAt: number; text: string }
  | ({ type: "child_run" } & ChildRun)
  | { id: string; type: "memory_saved"; changeId: string | null; memory: AgentMemory }
  | { id: string; type: "error"; code: string; message: string; details?: Json });
export interface ConversationSnapshot { conversation: Conversation; cards: AgentCard[]; turns: Turn[]; watermark: number; nextBefore: number | null }
export interface ConversationEvent { conversationId: string; turnId: string | null; seq: number; type: "card" | "turn" | "conversation" | "child_detail" | "rewind"; payload: Json; activity: ConversationActivity; at: number }

export interface CredentialStatus { configured: boolean; mask: string | null }
export interface ModelConnection { id: string; name: string; providerId: string; kind: "official" | "relay" | "local"; protocol: "openai" | "anthropic" | "google" | "ollama"; site: ProviderSite | null; baseUrl: string; secretRef: string | null; credential: CredentialStatus }
export interface ModelPrice { input: string; output: string; version: string; currency?: string; cache_read?: string | null; cache_write?: string | null; cache_write_5m?: string | null; cache_write_1h?: string | null; search?: Record<string, string> | null; multiplier?: string }
export interface ModelConfiguration { id: string; connectionId: string; model: string; profileProvider?: string | null; profileModel?: string | null; contextWindow?: number | null; maxOutputTokens?: number | null; tools?: boolean | null; images?: boolean | null; pdf?: boolean | null; thinkingSchema?: Record<string, Json> | null; price?: ModelPrice | null; priceMultiplier?: string; showPrice?: boolean; searchMode?: "provider" | "talyx" }
export interface AgentModel { id: string; connectionId: string; model: string; profileProvider: string; profileModel: string; profile: Record<string, Json>; overrides: ModelConfiguration; contextWindow: number; maxOutputTokens: number; tools: boolean | null; images: boolean | null; pdf: boolean | null; thinkingSchema: { type: "object"; properties: { effort?: { enum: string[] } }; additionalProperties: false }; thinkingDefault: string | null; price: ModelPrice | null; searchMode: "provider" | "talyx" }
export interface AgentSettings {
  connections: ModelConnection[];
  models: AgentModel[];
  search: SearchSettings;
  defaults: AgentDefaults;
  skills: { version: string; items: { name: string; description: string; path: string }[] };
  permissions: { defaultPermission: Permission };
  usage: { timezone: string; monthlyLimit: string | null; currency: string };
}

export interface AgentDefaults { modelId: string | null; defaultPermission: Permission; summaryModelId: string | null; childModelId: string | null; childThinking: { effort: string } | null; childrenEnabled: boolean; memoryEnabled: boolean; modelThinking: Record<string, string> }
export interface SearchSettings extends SearchConfiguration { credential: CredentialStatus }
export interface UsageGroup { key: string; requests: number; inputTokens: number; outputTokens: number; cacheReadTokens: number; reasoningTokens: number; unconfirmedRequests: number; tokenOnlyRequests: number; unpricedRequests: number; amounts: Record<string, string>; unpricedSearchUnits: Record<string, number>; unpricedPriceDimensions: string[] }
export interface ChildRun { id: string; conversationId: string; workspaceId: string; turnId: string; title: string; description: string; lastAction: string | null; modelId: string; thinking: Record<string, Json> | null; state: "queued" | "running" | "waiting_approval" | "waiting_business" | "stopping" | "completed" | "failed" | "cancelled" | "interrupted"; steps: number; businessRunning: boolean; detailCursor: number; createdAt: number; finishedAt: number | null; resultPreview: string | null; usage: UsageGroup[]; context?: ContextUsage }
export interface ChildDetail { child: ChildRun; items: { seq: number; card: AgentCard & { childRunId: string } }[]; nextAfter: number | null }
export type MemoryStatus = "active" | "deleted";
/** One memory: about you (global), this workspace's, or a bot's own (`botId`). `sourceKind` is who wrote it last. */
export interface AgentMemory { id: string; revision: number; scope: "global" | "workspace" | "bot"; workspaceId: string | null; botId: string | null; body: string; sourceKind: "user" | "inference"; createdAt: number; status: MemoryStatus; frequent: boolean }
/** An action the user allowed for a whole chat ("Allow for this chat"). */
export interface PermissionRule { id: string; conversationId: string; action: string; risks: string[]; approvalId: string; createdAt: number }
