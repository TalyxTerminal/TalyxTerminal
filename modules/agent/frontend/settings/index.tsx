import type { ComponentType } from "react";
import { createRoot } from "react-dom/client";
import { ModuleContextProvider, TxRoot, TxToastProvider, type ModuleContext } from "@talyx/sdk";
import { ModelsSection } from "./ModelsSection";
import { DefaultsPage } from "./DefaultsPage";
import { Knowledge, Personalization } from "./Personalization";
import { SpendingPage } from "./SpendingPage";
import { SearchSection, SkillsSection } from "./OtherSections";
import "./settings.css";
import "./pages.css";

const SECTIONS: Record<string, ComponentType<{ ctx: ModuleContext }>> = {
  general_settings: ({ctx})=><DefaultsPage ctx={ctx} kind="general"/>,
  chats_settings: ({ctx})=><DefaultsPage ctx={ctx} kind="chat"/>,
  bots_settings: ({ctx})=><DefaultsPage ctx={ctx} kind="bot"/>,
  models_settings: ModelsSection, search_settings: SearchSection, skills_settings: SkillsSection,
  personalization_settings: Personalization, knowledge_settings: Knowledge,
  usage_settings: SpendingPage, limit_settings: ({ctx})=><SpendingPage ctx={ctx} limit/>,
};

/** Agent's sections of Settings. */
export function provideSettings(ctx: ModuleContext): void {
  for (const [name, Section] of Object.entries(SECTIONS)) ctx.views.provide(name, (element, view) => {
    const root = createRoot(element);
    // Keep section menus inside the surrounding Settings dialog, including pointer and focus events.
    root.render(<ModuleContextProvider.Provider value={view}><TxRoot overlay={element}><TxToastProvider><Section ctx={view} /></TxToastProvider></TxRoot></ModuleContextProvider.Provider>);
    return { dispose: () => root.unmount() };
  });
}
