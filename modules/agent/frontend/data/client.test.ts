import { describe, expect, it, vi } from "vitest";
import type { ModuleContext } from "@talyx/sdk";
import { AgentClient } from "./client";

describe("Agent's requests to its own routes", () => {
  it("posts the operation as JSON, and turns a refusal into its code", async () => {
    const answers = [
      Response.json({ id: "turn" }),
      Response.json({ error: { code: "not_found", message: "No such conversation" } }, { status: 404 }),
      new Response("Internal Server Error", { status: 500 }),
    ];
    const fetch = vi.fn(async () => answers.shift()!);
    const client = new AgentClient({ routes: { fetch } } as unknown as ModuleContext);
    await expect(client.send("conversation", "hello", { includeView: false })).resolves.toEqual({ id: "turn" });
    const [route, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(route).toBe("conversation");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ operation: "send", value: { conversationId: "conversation", text: "hello", includeView: false } });
    await expect(client.read("gone")).rejects.toMatchObject({ code: "not_found" });
    await expect(client.read("broken")).rejects.toMatchObject({ code: "handler_error" });
  });
});
