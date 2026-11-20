import { beforeEach, expect, it, vi } from "vitest";
import type { ModuleContext } from "@talyx/sdk";

const hooks = vi.hoisted(() => ({ effects: [] as Array<() => void | (() => void)> }));
vi.mock("react", async original => ({
  ...(await original<typeof import("react")>()),
  useEffect: (effect: () => void | (() => void)) => { hooks.effects.push(effect); },
}));
import { installAgentNavigation, useAgentNavigation } from "./navigation";

beforeEach(() => { hooks.effects.length = 0; });

function panel() {
  const handle = vi.fn(), report = vi.fn();
  const ctx = { actions: { handle }, view: { report } } as unknown as ModuleContext;
  installAgentNavigation(ctx);
  return { ctx, handle, report, navigate: handle.mock.calls[0][1] as (input: unknown) => Promise<unknown> };
}

it("registers the panel action while mounting and selects through the rendered panel", async () => {
  const { ctx, handle, report, navigate } = panel();
  expect(handle.mock.calls[0][0]).toBe("navigate_conversation");
  await expect(navigate({ state: { conversationId: "conversation" } })).rejects.toThrow("not mounted");
  const controls = { select: vi.fn(), reveal: vi.fn() };
  useAgentNavigation(ctx, controls, { kind: "conversation", id: "conversation", label: "Research" });
  const unmount = hooks.effects[0]() as () => void;
  hooks.effects[1]();
  await navigate({ state: { conversationId: "conversation", messageId: "message" } });
  expect(controls.select).toHaveBeenCalledWith("conversation");
  expect(controls.reveal).toHaveBeenLastCalledWith("message");
  await navigate({ state: { conversationId: "conversation" } });
  expect(controls.reveal).toHaveBeenLastCalledWith(null);
  expect(report.mock.calls[0][0].openTargets).toEqual([{ kind: "conversation", key: "conversation", label: "Research" }]);
  unmount();
  await expect(navigate({ state: { conversationId: "conversation" } })).rejects.toThrow("not mounted");
});

it("propagates selection failures instead of reporting a successful navigation", async () => {
  const { ctx, navigate } = panel();
  const controls = { select: vi.fn().mockRejectedValue(new Error("Conversation was deleted")), reveal: vi.fn() };
  useAgentNavigation(ctx, controls, null);
  hooks.effects[0]();
  await expect(navigate({ state: { conversationId: "gone" } })).rejects.toThrow("deleted");
  expect(controls.reveal).not.toHaveBeenCalled();
});
