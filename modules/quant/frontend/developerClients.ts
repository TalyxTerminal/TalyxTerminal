import type { ModuleContext } from "@talyx/sdk";
import { createClient } from "../../developer/public/ts/types";
type Operations = ReturnType<typeof createClient>;
type Methods<P extends string> = { [K in keyof Operations as K extends `${P}.${infer Method}` ? Method : never]: Operations[K] };
function methods<P extends "documents">(ctx: ModuleContext, prefix: P): Methods<P> {
  const client = createClient(ctx);
  return new Proxy({} as Methods<P>, { get: (_target, method: string) => client[(prefix + "." + method) as keyof Operations] });
}
export const documentsClient = (ctx: ModuleContext) => {
  const client = methods(ctx, "documents");
  return new Proxy(client, { get: (target, method: keyof typeof client) => {
    if (method === "open") return async (input: Parameters<typeof client.open>[0]) => {
      const receipt = await target.open(input);
      return target.read({ documentId: receipt.documentId });
    };
    if (method === "edit") return async (input: Parameters<typeof client.edit>[0]) => {
      const receipt = await target.edit(input);
      return target.read({ documentId: receipt.documentId });
    };
    if (method === "list") return async (input: Parameters<typeof client.list>[0]) => {
      const result = await target.list(input);
      while (result.nextCursor) {
        const page = await target.list({ ...input, cursor: result.nextCursor });
        result.documents.push(...page.documents); result.nextCursor = page.nextCursor;
      }
      return result;
    };
    return target[method];
  } }) as Omit<typeof client, "open" | "edit"> & {
    open(input: Parameters<typeof client.open>[0]): ReturnType<typeof client.read>;
    edit(input: Parameters<typeof client.edit>[0]): ReturnType<typeof client.read>;
  };
};
