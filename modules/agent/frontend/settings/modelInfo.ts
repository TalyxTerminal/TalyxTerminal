import type { AgentModel, ModelConnection } from "../data/types";

/* How models and their thinking levels read in settings and in the composer.
   Shared by both so a model is named the same everywhere. */

export const PROVIDERS = [
  { id: "openrouter", name: "OpenRouter", protocol: "openai", baseUrl: "https://openrouter.ai/api/v1" },
  { id: "alibaba", name: "Qwen", protocol: "openai", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", sites: [
    { id: "domestic", name: "China", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1" },
    { id: "international", name: "International", baseUrl: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1" },
  ] },
  { id: "anthropic", name: "Anthropic", protocol: "anthropic", baseUrl: "https://api.anthropic.com" },
  { id: "openai", name: "OpenAI", protocol: "openai", baseUrl: "https://api.openai.com/v1" },
  { id: "google", name: "Google", protocol: "google", baseUrl: "https://generativelanguage.googleapis.com" },
  { id: "deepseek", name: "DeepSeek", protocol: "openai", baseUrl: "https://api.deepseek.com" },
  { id: "xai", name: "xAI", protocol: "openai", baseUrl: "https://api.x.ai/v1" },
  { id: "moonshotai", name: "Kimi", protocol: "openai", baseUrl: "https://api.moonshot.ai/v1", sites: [
    { id: "domestic", name: "China", baseUrl: "https://api.moonshot.cn/v1" },
    { id: "international", name: "International", baseUrl: "https://api.moonshot.ai/v1" },
  ] },
  { id: "zhipuai", name: "Zhipu", protocol: "openai", baseUrl: "https://api.z.ai/api/paas/v4", sites: [
    { id: "domestic", name: "China", baseUrl: "https://open.bigmodel.cn/api/paas/v4" },
    { id: "international", name: "International", baseUrl: "https://api.z.ai/api/paas/v4" },
  ] },
] as const;

/** Providers whose own web search Agent uses instead of the TALYX search tool. */
export const NATIVE_SEARCH = new Set(["openai", "anthropic", "google", "xai"]);

export function compact(value: number): string {
  if (value >= 1_000_000) return `${+(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1000) return `${Math.round(value / 1000)}K`;
  return String(value);
}

export function modelName(model: AgentModel): string {
  return typeof model.profile.name === "string" && model.profile.name ? model.profile.name : model.model;
}

export function connectionLabel(connection: ModelConnection): string {
  return connection.kind === "local" ? `${connection.name} · this computer` : connection.name;
}

export interface ThinkingKnob {
  /** The one field of a thinking choice: `{ effort: level }`. */
  field: "effort";
  label: string;
  options: { value: string; label: string }[];
  /** The level the provider uses when nothing is sent. */
  defaultValue: string | null;
}

const LEVELS: Record<string, string> = {
  off: "Off", on: "On", minimal: "Minimal", low: "Low", medium: "Medium", high: "High", xhigh: "Extra high", max: "Max",
};

/** The model's own thinking levels; null for a model that can't think. */
export function thinkingKnob(model: AgentModel): ThinkingKnob | null {
  const effort = model.thinkingSchema.properties.effort;
  if (!effort) return null;
  return { field: "effort", label: "Thinking", defaultValue: model.thinkingDefault,
    options: effort.enum.map(value => ({ value, label: LEVELS[value] ?? value.charAt(0).toUpperCase() + value.slice(1) })) };
}

export function priceText(model: AgentModel): string | null {
  if (!model.price) return null;
  const amount = (value: string) => `$${+Number(value).toFixed(4)}`;
  const multiplier = model.price.multiplier && model.price.multiplier !== "1" ? ` × ${model.price.multiplier}` : "";
  return `${amount(model.price.input)} / ${amount(model.price.output)} per 1M tokens${multiplier}`;
}

export function abilities(model: AgentModel): string {
  const parts = [`${compact(model.contextWindow)} window`];
  if (model.tools === false) parts.push("No tools");
  if (model.thinkingSchema.properties.effort) parts.push("Thinking");
  if (model.images) parts.push("Images");
  if (model.pdf) parts.push("PDF");
  return parts.join(" · ");
}
