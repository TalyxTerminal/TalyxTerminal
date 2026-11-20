import { createContext } from "react";

export interface CommMessage {type: "comm"; msgType: "comm_open" | "comm_msg" | "comm_close"; commId: string; data: unknown; target?: string}

/** The widget traffic of one notebook panel: the kernel's messages are kept (opens) and handed to every widget frame; a frame's go back through `send`. */
export class WidgetBus {
  readonly opened = new Map<string, CommMessage>();
  private listeners = new Set<(message: CommMessage) => void>();
  constructor(private sender: (commId: string, data: unknown) => void) {}
  receive(message: CommMessage) {
    if (message.msgType === "comm_open") this.opened.set(message.commId, message);
    else if (message.msgType === "comm_close") this.opened.delete(message.commId);
    else this.fold(message);
    for (const listener of this.listeners) listener(message);
  }
  /** A state update is folded into the kept open: a frame that starts after it (interact sets its box's children just after opening it) builds the
   *  model as it is now, not as it was opened (an empty box, no slider). */
  private fold(message: CommMessage) {
    const kept = this.opened.get(message.commId);
    const data = message.data as {method?: string; state?: Record<string, unknown>} | null;
    if (kept === undefined || data?.method !== "update" || !data.state) return;
    const opened = kept.data as {state?: Record<string, unknown>};
    this.opened.set(message.commId, {...kept, data: {...opened, state: {...opened.state, ...data.state}}});
  }
  /** A new kernel process: the models of the old one are gone. */
  reset() {this.opened.clear();}
  send(commId: string, data: unknown) {this.sender(commId, data);}
  subscribe(listener: (message: CommMessage) => void) {this.listeners.add(listener); return () => void this.listeners.delete(listener);}
}

export const WidgetBusContext = createContext<WidgetBus | null>(null);
