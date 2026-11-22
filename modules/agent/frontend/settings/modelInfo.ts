import { t } from "../i18n";
import type { AgentModel, ModelConnection } from "../data/types";

/* How models and their thinking levels read in settings and in the composer.
   Shared by both so a model is named the same everywhere. */

export const PROVIDERS = [
  { id: "openrouter", name: "OpenRouter", protocol: "openai", baseUrl: "https://openrouter.ai/api/v1" },
  { id: "alibaba", name: "Qwen", protocol: "openai", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", sites: [
    { id: "domestic", name: t("China"), baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1" },
    { id: "international", name: t("International"), baseUrl: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1" },
  ] },
  { id: "anthropic", name: "Anthropic", protocol: "anthropic", baseUrl: "https://api.anthropic.com" },
  { id: "openai", name: "OpenAI", protocol: "openai", baseUrl: "https://api.openai.com/v1" },
  { id: "google", name: "Google", protocol: "google", baseUrl: "https://generativelanguage.googleapis.com" },
  { id: "deepseek", name: "DeepSeek", protocol: "openai", baseUrl: "https://api.deepseek.com" },
  { id: "xai", name: "xAI", protocol: "openai", baseUrl: "https://api.x.ai/v1" },
  { id: "moonshotai", name: "Kimi", protocol: "openai", baseUrl: "https://api.moonshot.ai/v1", sites: [
    { id: "domestic", name: t("China"), baseUrl: "https://api.moonshot.cn/v1" },
    { id: "international", name: t("International"), baseUrl: "https://api.moonshot.ai/v1" },
  ] },
  { id: "zhipuai", name: "Zhipu", protocol: "openai", baseUrl: "https://api.z.ai/api/paas/v4", sites: [
    { id: "domestic", name: t("China"), baseUrl: "https://open.bigmodel.cn/api/paas/v4" },
    { id: "international", name: t("International"), baseUrl: "https://api.z.ai/api/paas/v4" },
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
  return connection.kind === "local" ? t("{name} · this computer", { name: connection.name }) : connection.name;
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
  off: t("Off"), on: t("On"), minimal: t("Minimal"), low: t("Low"), medium: t("Medium"), high: t("High"), xhigh: t("Extra high"), max: t("Max"),
};

export const thinkingLabel = (value: string) => LEVELS[value] ?? value;

/** The model's own thinking levels; null for a model that can't think. */
export function thinkingKnob(model: AgentModel): ThinkingKnob | null {
  const effort = model.thinkingSchema.properties.effort;
  if (!effort) return null;
  return { field: "effort", label: t("Thinking"), defaultValue: model.thinkingDefault,
    options: effort.enum.map(value => ({ value, label: thinkingLabel(value) })) };
}

export function priceText(model: AgentModel): string | null {
  if (!model.price) return null;
  const amount = (value: string) => `$${+Number(value).toFixed(4)}`;
  const multiplier = model.price.multiplier && model.price.multiplier !== "1" ? ` × ${model.price.multiplier}` : "";
  return t("{input} / {output} per 1M tokens{multiplier}", { input: amount(model.price.input), output: amount(model.price.output), multiplier });
}

export function abilities(model: AgentModel): string {
  const parts = [t("{size} window", { size: compact(model.contextWindow) })];
  if (model.tools === false) parts.push(t("No tools"));
  if (model.thinkingSchema.properties.effort) parts.push(t("Thinking"));
  if (model.images) parts.push(t("Images"));
  if (model.pdf) parts.push("PDF");
  return parts.join(" · ");
}
