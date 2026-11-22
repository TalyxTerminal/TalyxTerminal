import { t } from "../i18n";
import { ContractError, failure, type Json, type ModuleContext } from "@talyx/sdk";
import type { FileChoice } from "../chat/model";
import type { AgentMemory, AgentSettings, Attachment, ChildDetail, ChildRun, Conversation, ConversationEvent, ConversationListItem, ConversationSnapshot, MemoryStatus, PermissionRule, Reference, SearchSettings, Turn } from "./types";

/** The files a chat takes, by extension, as the backend accepts them: images, PDF and UTF-8 text (code reads as plain text). */
const ATTACHMENT_TYPES: Record<string, string> = {
  ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".pdf": "application/pdf",
  ".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv", ".json": "application/json", ".py": "text/plain", ".log": "text/plain",
};
export const ATTACHMENT_ACCEPT = Object.keys(ATTACHMENT_TYPES).join(",");
export function attachmentType(name: string): string | null {
  const dot = name.lastIndexOf(".");
  return dot < 0 ? null : ATTACHMENT_TYPES[name.slice(dot).toLowerCase()] ?? null;
}

/** One request to Agent's own backend (its private routes): {operation, value} in, the value or its refusal out. */
export async function agentRequest<T>(ctx: ModuleContext, route: "conversation" | "settings" | "bots", operation: string,
  value: Record<string, unknown>, signal?: AbortSignal): Promise<T> {
  const response = await ctx.routes.fetch(route, { method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ operation, value }), signal });
  if (!response.headers.get("content-type")?.includes("application/json"))
    throw failure("handler_error", `${route} ${operation}: ${response.status}`);
  const body = await response.json();
  if (response.ok) return body as T;
  if (body?.error) throw new ContractError(body.error);
  throw failure("handler_error", `${route} ${operation}: ${response.status}`);
}

/** The chat panel's and the settings pages' requests. */
export class AgentClient {
  constructor(readonly ctx: ModuleContext) {}
  private post<T>(route: "conversation" | "settings", operation: string, value: Record<string, unknown>, signal?: AbortSignal): Promise<T> {
    return agentRequest<T>(this.ctx, route, operation, value, signal);
  }
  conversation<T>(operation: string, value: Record<string, unknown> = {}, options?: { signal?: AbortSignal }): Promise<T> {
    return this.post<T>("conversation", operation, value, options?.signal);
  }
  settings<T = Json>(operation = "read", value: Record<string, unknown> = {}, options?: { signal?: AbortSignal }): Promise<T> {
    return this.post<T>("settings", operation, value, options?.signal);
  }
  readSettings() { return this.settings<AgentSettings>(); }
  list(offset = 0) { return this.conversation<{ items: ConversationListItem[]; nextOffset: number | null }>("list", { offset }); }
  create(modelId?: string) { return this.conversation<Conversation>("create", modelId ? { modelId } : {}); }
  findFiles = (query: string, signal: AbortSignal) => this.conversation<FileChoice[]>("find_files", {query}, {signal});
  read(conversationId: string, before?: number) { return this.conversation<ConversationSnapshot>("read", { conversationId, ...(before === undefined ? {} : { before }) }); }
  update(conversationId: string, changes: Partial<Pick<Conversation, "title" | "modelId" | "thinking" | "permission" | "childrenEnabled" | "memoryEnabled" | "planMode" | "archived" | "pinned">>) { return this.conversation<Conversation>("update", { conversationId, changes }); }
  configureSearch(value: Omit<SearchSettings, "credential">) { return this.settings<Omit<SearchSettings, "credential">>("search", value); }
  // Unset filters are left out: the backend rejects undefined values. With botId, that bot's own memories.
  memoryList(filters: { botId?: string; status?: MemoryStatus; offset?: number } = {}) {
    return this.settings<{ items: AgentMemory[]; nextOffset: number | null }>("memory_list", Object.fromEntries(Object.entries(filters).filter(([, value]) => value !== undefined)));
  }
  memoryEdit(memoryId: string, expectedRevision: number, body: string, remove = false) { return this.settings<{ memory: AgentMemory }>("memory_edit", { memoryId, expectedRevision, body, delete: remove }); }
  permissionRules(conversationId: string) { return this.conversation<PermissionRule[]>("permission_rules", { conversationId }); }
  revokePermission(conversationId: string, ruleId: string) { return this.conversation<null>("revoke_permission", { conversationId, ruleId }); }
  childRead(conversationId: string, childRunId: string, after = 0) { return this.conversation<ChildDetail>("child_read", { conversationId, childRunId, after }); }
  childStop(conversationId: string, childRunId: string) { return this.conversation<ChildRun>("child_stop", { conversationId, childRunId }); }
  send(conversationId: string, text: string, options: { includeView?: boolean; attachments?: string[]; supplemental?: boolean } = {}) { return this.conversation<Turn>("send", { conversationId, text, ...options }); }
  stop(conversationId: string) { return this.conversation<Turn | { state: "idle" }>("stop", { conversationId }); }
  decide(approvalId: string, version: number, decision: "allow" | "allow_conversation" | "deny", reason = "") { return this.conversation("decide", { approvalId, version, decision, reason }); }
  delete(conversationId: string) { return this.conversation<null>("delete", { conversationId }); }
  events(conversationId: string, after: number) { return this.conversation<{ items: ConversationEvent[]; nextAfter: number | null }>("events", { conversationId, after }); }
  async upload(conversationId: string, file: File, signal?: AbortSignal): Promise<Attachment> {
    const upload = await this.conversation<{ id: string }>("upload_start", { conversationId, name: file.name, mime: attachmentType(file.name)!, size: file.size }, { signal });
    try {
      for (let offset = 0; offset < file.size; offset += 128 * 1024) {
        const bytes = new Uint8Array(await file.slice(offset, offset + 128 * 1024).arrayBuffer());
        let binary = "";
        for (const byte of bytes) binary += String.fromCharCode(byte);
        await this.conversation("upload_chunk", { conversationId, attachmentId: upload.id, offset, data: btoa(binary) }, { signal });
      }
      return await this.conversation<Attachment>("upload_finish", { conversationId, attachmentId: upload.id }, { signal });
    } catch (error) {
      await this.conversation("remove_attachment", { conversationId, attachmentId: upload.id });
      throw error;
    }
  }
  removeAttachment(conversationId: string, attachmentId: string) { return this.conversation("remove_attachment", { conversationId, attachmentId }); }
  /** Sends a few short requests to the model and answers what passed; aborting the signal stops waiting for it. */
  testConnection(modelId: string, signal: AbortSignal, thinking?: Record<string, Json>, testSearch = true) {
    return this.settings<Json>("test_connection", { modelId, ...(thinking ? { thinking } : {}), testSearch }, { signal });
  }
  refreshProfiles(signal: AbortSignal) { return this.settings<Json>("refresh_profiles", {}, { signal }); }
  async openPanel(target: { type: string; panelId: string }): Promise<void> {
    const panels = this.ctx.platform.panels;
    const existing = panels.list(target.type).find(panel => panel.panelId === target.panelId);
    if (existing) panels.activate(existing);
    else await panels.open({ type: target.type });
  }
  async openReference(referenceId: string): Promise<void> {
    const reference = await this.conversation<Reference>("reference", { referenceId });
    if (reference.workspaceId !== this.ctx.workspace.identity.get()?.workspaceId) throw failure("invalid_target", t("Open this reference in its original workspace"));
    if (reference.kind === "panel") return this.openPanel(reference.target);
    if (reference.kind === "object") {
      const {module, panelType, objectId} = reference.target;
      if (typeof module !== "string" || typeof panelType !== "string" || typeof objectId !== "string") throw failure("invalid_target", t("Unavailable panel target"));
      await this.ctx.platform.panels.openTarget({module, panelType, objectId});
      return;
    }
    const url = new URL(String(reference.target.url));
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw failure("invalid_input", t("Unsupported source URL"));
    window.open(url.href, "_blank", "noopener,noreferrer");
  }
}

export function createAgentClient(ctx: ModuleContext) { return new AgentClient(ctx); }
