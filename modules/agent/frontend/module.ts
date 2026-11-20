import type { ModuleContext } from "@talyx/sdk";
import { provideSettings } from "./settings";
import { provideConversationPanel } from "./panel";

export function activate(ctx: ModuleContext): void {
  provideSettings(ctx);
  provideConversationPanel(ctx);
}
