import { t } from "../i18n";
/* TradingView's right side: an icon bar (watchlist, alerts, object tree and data window) and the panel it opens, which
   can be closed from its icon and dragged wider on its edge. */
import type { ReactNode } from "react";
import { TxIconButton, TxSplit, TxTooltip, type ModuleContext } from "@talyx/sdk";
import { setUi, useStore, type PanelUi } from "../store";
import { WatchlistPanel } from "./WatchlistPanel";
import { AlertsPanel } from "./AlertsPanel";
import { ObjectsPanel } from "./ObjectsPanel";

type Panel = NonNullable<PanelUi["rightPanel"]>;
const PANELS: { id: Panel; icon: "list" | "bell" | "layers"; label: string }[] = [
  { id: "watchlist", icon: "list", label: t("Watchlist") }, { id: "alerts", icon: "bell", label: t("Alerts") }, { id: "objects", icon: "layers", label: t("Object tree and data window") },
];

/** The chart area with the open right panel beside it. */
export function RightPanelSplit({ ctx, panelId, children }: { ctx: ModuleContext; panelId: string; children: ReactNode }) {
  const panel = useStore(state => state.ui[panelId]?.rightPanel ?? null);
  const width = useStore(state => state.ui[panelId]?.rightWidth ?? 300);
  const side = panel === "watchlist" ? <WatchlistPanel ctx={ctx} panelId={panelId} /> : panel === "alerts" ? <AlertsPanel ctx={ctx} panelId={panelId} /> : panel === "objects" ? <ObjectsPanel ctx={ctx} panelId={panelId} /> : null;
  return <TxSplit side={side} sideHidden={!panel} sidePosition="end" width={width} onWidth={rightWidth => setUi(panelId, { rightWidth })}
    min={220} max={520} mainMin={360} sideLabel={PANELS.find(item => item.id === panel)?.label ?? t("Panel")}>{children}</TxSplit>;
}

export function RightRail({ panelId }: { panelId: string }) {
  const panel = useStore(state => state.ui[panelId]?.rightPanel ?? null);
  return <div className="chart-rail" role="toolbar" aria-label={t("Side panels")} aria-orientation="vertical">
    {PANELS.map(item => <TxTooltip key={item.id} label={item.label}>
      <TxIconButton variant="tool" icon={item.icon} label={item.label} aria-pressed={panel === item.id}
        onClick={() => setUi(panelId, { rightPanel: panel === item.id ? null : item.id })} />
    </TxTooltip>)}
  </div>;
}
