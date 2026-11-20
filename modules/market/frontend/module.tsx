import { createRoot } from "react-dom/client";
import { ModuleContextProvider, TxRoot, type Json, type ModuleContext } from "@talyx/sdk";
import { SearchBox, type SearchViewProps } from "./SearchBox";
import { MarketPanel } from "./panel/MarketPanel";
import { marketPanelStyles } from "./panel/styles";
import { marketStyles } from "./styles";
import { SourcesSettings } from "./SourcesSettings";

export async function activate(ctx: ModuleContext): Promise<void> {
  ctx.views.provide("search_box", (element, view) => {
    const root = createRoot(element);
    let props = view.props as SearchViewProps;
    const render = () => root.render(<ModuleContextProvider.Provider value={view}><TxRoot overlay={view.overlayRoot}><style>{marketStyles}</style>
      <SearchBox ctx={view} props={props}
        onSelected={(instrument, series) => view.emit("selected", { instrument, series } as unknown as Json)}
        onCallerAction={(action, instrument) => view.emit("caller_action", { actionId: action.id, instrument } as unknown as Json)}
        onClosed={() => view.emit("closed", {})} /></TxRoot></ModuleContextProvider.Provider>);
    render();
    return { update: next => { props = next as SearchViewProps; render(); }, dispose: () => root.unmount() };
  });
  ctx.views.provide("sources_settings", (element, view) => {
    const root = createRoot(element);
    root.render(<ModuleContextProvider.Provider value={view}><TxRoot overlay={view.overlayRoot}>
      <style>{marketPanelStyles}</style><SourcesSettings ctx={view} /></TxRoot></ModuleContextProvider.Provider>);
    return { dispose: () => root.unmount() };
  });
  ctx.panels.provide("main", (element, panel) => {
    const root = createRoot(element);
    element.style.height = "100%";
    root.render(<ModuleContextProvider.Provider value={panel}><TxRoot overlay={panel.overlayRoot} style={{ height: "100%", minHeight: 0 }}>
      <style>{marketPanelStyles}</style><MarketPanel /></TxRoot></ModuleContextProvider.Provider>);
    return { dispose: () => root.unmount() };
  });
}
