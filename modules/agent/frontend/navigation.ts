import { t } from "./i18n";
import { useEffect } from "react";
import type { Json, ModuleContext } from "@talyx/sdk";

export interface AgentNavigationState { conversationId: string; messageId?: string }
export interface AgentNavigationControls {
  select(id: string): void | Promise<void>;
  reveal(id: string | null): void;
}
export interface AgentNavigationSelection { kind: "conversation"; id: string; label: string }

/** The panel's current selection callbacks, by panel. */
const mounted = new WeakMap<ModuleContext, { controls: AgentNavigationControls | null }>();

/** Registers the panel action while the panel mounts, as the window requires;
    it selects through whatever callbacks the rendered panel last supplied. */
export function installAgentNavigation(ctx: ModuleContext): void {
  const slot = { controls: null as AgentNavigationControls | null };
  mounted.set(ctx, slot);
  ctx.actions.handle("navigate_conversation", async input => {
    const state = (input as unknown as { state: AgentNavigationState }).state;
    const controls = slot.controls;
    if (!controls) throw new Error("The Agent panel is not mounted");
    await controls.select(state.conversationId);
    if (!slot.controls) throw new Error("The Agent panel closed while selecting its object");
    slot.controls.reveal(state.messageId ?? null);
    return state as unknown as Json;
  });
}

/** The panel supplies its existing selection callbacks; navigation never remounts it. */
export function useAgentNavigation(ctx: ModuleContext, controls: AgentNavigationControls, selection: AgentNavigationSelection | null): void {
  const slot = mounted.get(ctx)!;
  useEffect(() => {
    slot.controls = controls;
    return () => { slot.controls = null; };
  });
  useEffect(() => {
    ctx.view.report({ summary: selection ? t("Agent · {label}", { label: selection.label }) : t("Agent · No conversation selected"),
      refs: selection ? { kind: selection.kind, key: selection.id } : {},
      facts: selection ? [`Selected ${selection.kind}: ${selection.label}`] : [],
      openTargets: selection ? [{ kind: selection.kind, key: selection.id, label: selection.label }] : [] });
  }, [ctx, selection?.kind, selection?.id, selection?.label]);
}
