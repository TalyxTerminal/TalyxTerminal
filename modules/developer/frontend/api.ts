import { ContractError, failure, type ModuleContext } from "@talyx/sdk";

/** Developer's own backend routes; failures arrive as contract errors. */
export async function privateRequest<T>(ctx: ModuleContext, path: string, init?: RequestInit): Promise<T> {
  const response = await ctx.routes.fetch(path, init);
  const value = await response.json();
  if (!response.ok) throw value.error ? new ContractError(value.error) : failure("handler_error", typeof value.detail === "string" ? value.detail : "Developer could not do this.");
  return value as T;
}
export const jsonRequest = (value: unknown, method = "POST"): RequestInit => ({ method, headers: { "content-type": "application/json" }, body: JSON.stringify(value) });
