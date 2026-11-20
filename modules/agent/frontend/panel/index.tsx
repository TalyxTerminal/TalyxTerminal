import { createRoot } from "react-dom/client";
import { ModuleContextProvider, TxRoot, TxToastProvider, type Json, type ModuleContext } from "@talyx/sdk";
import { AgentMode, type PanelState } from "./Mode";
import { installAgentNavigation } from "../navigation";
import "./panel.css";
import "../chat/chat.css";
import "../bot/bot.css";

/** The conversation panel: one chat at a time, with the workspace's chats beside it or in its title. A chat it names
    that is not this workspace's (a copied workspace's panel) gives way to that chat's copy, or to none. */
export function provideConversationPanel(ctx: ModuleContext): void {
  ctx.panels.provide("chat", (element, panel) => {
    installAgentNavigation(panel);
    const root = createRoot(element);
    element.style.height = "100%";
    let state = (panel.initialState ?? {}) as PanelState;
    let workspaceId = panel.workspace.identity.get()!.workspaceId;
    const render = () => {
      root.render(<ModuleContextProvider.Provider value={panel}><TxRoot overlay={panel.overlayRoot} className="agent-root"><TxToastProvider>
        <AgentMode key={workspaceId} ctx={panel} initial={state} onState={next => {state=next;panel.panel!.stateChanged({derived:true});}} />
      </TxToastProvider></TxRoot></ModuleContextProvider.Provider>);
    };
    const stop = panel.workspace.identity.watch(identity => {
      if (identity !== null && identity.workspaceId !== workspaceId) { workspaceId = identity.workspaceId; render(); }
    });
    render();
    // A new chat has no conversation id yet: unset fields are left out, so the panel state stays plain JSON.
    const captureState = () => Object.fromEntries(Object.entries(state).filter(([, value]) => value !== undefined)) as Json;
    return { captureState, dispose: () => { stop(); root.unmount(); } };
  });
}
