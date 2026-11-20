/* TradingView's bottom panel under the chart (the screener, the indicator editor). Its tabs sit in the bottom bar; a tab
   opens the panel under the bar; the panel's top edge drags its height; the open tab, or the chevron, closes it. */
import { useRef } from "react";
import { TxButton, TxIconButton, type ModuleContext } from "@talyx/sdk";
import { setUi, useStore, type PanelUi } from "../store";
import { EditorTab } from "./EditorTab";
import { ScreenerTab } from "./ScreenerTab";

type Tab = NonNullable<PanelUi["bottomPanel"]>;
const TABS: { id: Tab; label: string }[] = [{ id: "screener", label: "Screener" }, { id: "editor", label: "Editor" }];
const MIN_HEIGHT = 140;

/** The panel's tabs, for the bottom bar. */
export function BottomTabs({ panelId }: { panelId: string }) {
  const tab = useStore(state => state.ui[panelId].bottomPanel);
  const open = (id: Tab | null) => setUi(panelId, { bottomPanel: id });
  return <div className="chart-bottom-tabs" role="tablist" aria-label="Bottom panel">
    {TABS.map(item => <TxButton key={item.id} variant="tool" size="sm" role="tab" aria-selected={tab === item.id} aria-pressed={tab === item.id}
      onClick={() => open(tab === item.id ? null : item.id)}>{item.label}</TxButton>)}
    {tab && <TxIconButton variant="tool" size="sm" icon="chevron-down" label="Close panel" onClick={() => open(null)} />}
  </div>;
}

export function BottomPanel({ ctx, panelId }: { ctx: ModuleContext; panelId: string }) {
  const tab = useStore(state => state.ui[panelId].bottomPanel);
  const height = useStore(state => state.ui[panelId].bottomHeight);
  const section = useRef<HTMLElement>(null);
  if (!tab) return null;
  return <section ref={section} className="chart-bottom" style={{ height }} aria-label="Bottom panel">
    <div className="chart-bottom-edge" role="separator" aria-orientation="horizontal" aria-label="Bottom panel height"
      onPointerDown={event => { event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); }}
      onPointerMove={event => {
        if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
        const bounds = section.current!.getBoundingClientRect(), room = section.current!.parentElement!.getBoundingClientRect().height - 160;
        setUi(panelId, { bottomHeight: Math.round(Math.max(MIN_HEIGHT, Math.min(room, bounds.bottom - event.clientY))) });
      }}
      onPointerUp={event => event.currentTarget.releasePointerCapture(event.pointerId)} />
    {tab === "screener" && <ScreenerTab ctx={ctx} panelId={panelId} />}
    {tab === "editor" && <EditorTab ctx={ctx} panelId={panelId} />}
  </section>;
}
