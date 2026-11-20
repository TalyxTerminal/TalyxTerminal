import { useState } from "react";
import { TxSegmented, type ModuleContext } from "@talyx/sdk";
import { ChatPanel } from "../chat/ChatPanel";
import { BotPanel } from "../bot/BotPanel";

/* Chats and bots in one panel, switched like Claude's desktop app switches
   Chat and Code: two separate lists, never mixed. Two Agent panels side by
   side can show one each. The platform's module picker offers one panel per
   module, so this needs no platform change. The panel remembers its side. */

export type Mode = "chats" | "bots";

export function ModeSwitch({ mode, onMode }: { mode: Mode; onMode: (mode: Mode) => void }) {
  return <TxSegmented label="Chats or bots" size="sm" value={mode} onChange={onMode}
    options={[{ value: "chats", label: "Chats" }, { value: "bots", label: "Bots" }]} />;
}

export interface PanelState { mode?: Mode; conversationId?: string; objectId?: string; botLineId?: string }
export function AgentMode({ctx,initial,onState}: {ctx:ModuleContext;initial:PanelState;onState(next:PanelState):void}) {
  const [mode,setMode] = useState<Mode>(initial.mode ?? "chats");
  const [selected,setSelected] = useState(initial.conversationId ?? initial.objectId);
  const control = <ModeSwitch mode={mode} onMode={next => {setMode(next);onState({mode:next,conversationId:selected});}} />;
  return mode === "chats" ? <ChatPanel ctx={ctx} initial={selected} mode={control} onSelect={id => {
    setSelected(id ?? undefined);onState({mode,conversationId:id ?? undefined});
  }} /> : <BotPanel ctx={ctx} mode={control} initialLine={initial.botLineId} />;
}
