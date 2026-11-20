import type { ModelPrice } from "./types";

export type ProviderSite = "domestic" | "international";
export interface ConnectionTemplate {
  providerId: string;
  name: string;
  protocol: "openai" | "anthropic" | "google";
  baseUrl: string;
  site: ProviderSite | null;
}
export interface SearchConfiguration {
  mode: "auto" | "fixed";
  provider: "exa" | "tavily" | "deepseek" | "moonshotai" | "zhipuai";
  secretRef: string | null;
  connectionId: string | null;
  prices: Record<string, ModelPrice>;
}
export interface SearchSource {
  id: string;
  url: string;
  title: string;
  publishedAt: string | null;
  datePrecision: "date" | "datetime" | null;
  publishedRaw: string | null;
}
export interface GroupBudget {
  groupId: string;
  workspaceId: string;
  version: number;
  month: string;
  currency: string;
  monthlyLimit: string;
  spent: string;

  paused: boolean;
  reason: string | null;
}
