import { t } from "../i18n";
/* The chart's right-click menus (TradingView): on a drawing, its settings, lock, hide, an alert on it and remove; on the
   chart, an alert at the price under the pointer, reset view, the object tree, remove objects and settings; on the price
   scale, Auto, invert, the scale mode, which side it is on, its labels and lines. */
import { TxContextMenu, type ModuleContext, type TxMenuItem } from "@talyx/sdk";
import { chartEntry, commit, openOverlay, removeObjects, setAutoScale, setUi, useStore } from "../store";
import type { ChartView, ScaleMode } from "../engine/ChartView";
import { overlayTool } from "../drawings/registry";
import { formatPrice, shownFormat } from "../engine/precision";
import { ALERT_TOOLS } from "./AlertDialog";

export interface ChartMenuAt { chartId: string; x: number; y: number; drawingId?: string; price?: number | null; scale?: true }

const MODES: { id: ScaleMode; label: string }[] = [{ id: "regular", label: t("Regular") }, { id: "percent", label: t("Percent") }, { id: "indexed", label: t("Indexed to 100") }, { id: "log", label: t("Logarithmic") }];

/** Auto on or off: the view fits the price scale, the store shows it. */
export function setAuto(view: ChartView, chartId: string, auto: boolean): void { view.setScale({ auto }); setAutoScale(chartId, auto); }

export function ChartMenu({ ctx, panelId, at, onClose, view }: { ctx: ModuleContext; panelId: string; at: ChartMenuAt; onClose(): void; view(chartId: string): ChartView | null }) {
  const chart = useStore(state => state.charts[at.chartId]?.chart);
  if (!chart) return null;
  const { chartId } = at, document = chart.document;
  let items: TxMenuItem[];
  const scale = chart.document.display.scale ?? {}, scales = chart.document.display.scales ?? {};
  const display = (patch: Record<string, unknown>) => void commit(ctx, chartId, [{ type: "display.update", patch }]);
  const drawing = at.drawingId ? (document.drawings[chart.drawingGroup] ?? []).find(item => item.id === at.drawingId) : undefined;
  const shown = at.scale ? view(chartId) : null;
  if (shown) {
    const auto = chartEntry(chartId).autoScale, side = scale.side ?? "right";
    const scaleItem = (key: keyof typeof scales, label: string, group?: string) =>
      ({ id: key, label, group, checked: scales[key] !== false, onSelect: () => display({ scales: { ...scales, [key]: scales[key] === false } }) });
    items = [
      { id: "auto", label: t("Auto (fits data to screen)"), checked: auto, onSelect: () => setAuto(shown, chartId, !auto) },
      { id: "invert", label: t("Invert scale"), checked: scale.invert === true, onSelect: () => display({ scale: { ...scale, invert: scale.invert !== true } }) },
      ...MODES.map((mode, index) => ({ id: mode.id, label: mode.label, separated: index === 0, checked: (scale.mode ?? "regular") === mode.id, onSelect: () => display({ scale: { ...scale, mode: mode.id } }) })),
      { id: "side", label: side === "right" ? t("Move scale to left") : t("Move scale to right"), separated: true, onSelect: () => display({ scale: { ...scale, side: side === "right" ? "left" : "right" } }) },
      scaleItem("lastPriceLabel", t("Symbol last price"), t("Labels")), scaleItem("indicatorLabels", t("Indicators last value")), scaleItem("countdown", t("Countdown to bar close")),
      scaleItem("lastPriceLine", t("Symbol last price line"), t("Lines")),
      { id: "settings", label: t("More settings…"), icon: "settings", separated: true, onSelect: () => openOverlay(panelId, { kind: "settings", chartId }) },
    ];
  } else if (drawing) {
    items = [
      { id: "settings", label: t("Settings…"), icon: "settings", onSelect: () => openOverlay(panelId, { kind: "drawingSettings", chartId, drawingId: drawing.id }) },
      ...(ALERT_TOOLS.includes(drawing.tool) ? [{ id: "alert", label: t("Add alert on {value}…", { value: drawing.name || overlayTool(drawing.tool).label }), icon: "bell" as const, onSelect: () => openOverlay(panelId, { kind: "alert", chartId, drawingId: drawing.id }) }] : []),
      { id: "lock", label: drawing.locked ? t("Unlock") : t("Lock"), icon: "lock", separated: true, onSelect: () => void commit(ctx, chartId, [{ type: "drawing.update", id: drawing.id, patch: { locked: !drawing.locked } }]) },
      { id: "hide", label: t("Hide"), icon: "eye-off", onSelect: () => void commit(ctx, chartId, [{ type: "drawing.update", id: drawing.id, patch: { visible: false } }]) },
      { id: "remove", label: t("Remove"), icon: "trash", tone: "danger", separated: true, onSelect: () => void commit(ctx, chartId, [{ type: "drawing.delete", id: drawing.id }]) },
    ];
  } else {
    const drawings = (document.drawings[chart.drawingGroup] ?? []).length, indicators = document.indicators.length;
    const precision = shownFormat(chartEntry(chartId).feed.priceFormat, document.display.precision).precision;
    items = [
      ...(at.price !== undefined && at.price !== null ? [{ id: "alert", label: t("Add alert on {displaySymbol} at {formatPrice}", { displaySymbol: document.instrument.displaySymbol, formatPrice: formatPrice(at.price, precision) }), icon: "bell" as const,
        onSelect: () => openOverlay(panelId, { kind: "alert", chartId, price: Number(at.price!.toFixed(precision)) }) }] : []),
      { id: "reset", label: t("Reset chart view"), icon: "restore", separated: at.price !== undefined && at.price !== null, onSelect: () => { const shownView = view(chartId); if (shownView) { shownView.resetView(); setAutoScale(chartId, true); } } },
      { id: "tree", label: t("Object tree"), icon: "layers", onSelect: () => setUi(panelId, { rightPanel: "objects" }) },
      ...(drawings ? [{ id: "drawings", label: drawings === 1 ? t("Remove 1 drawing") : t("Remove {count} drawings", { count: drawings }), icon: "trash" as const, separated: true, onSelect: () => void removeObjects(ctx, chartId, "drawings") }] : []),
      ...(indicators ? [{ id: "indicators", label: indicators === 1 ? t("Remove 1 indicator") : t("Remove {count} indicators", { count: indicators }), icon: "trash" as const, separated: !drawings, onSelect: () => void removeObjects(ctx, chartId, "indicators") }] : []),
      { id: "settings", label: t("Settings…"), icon: "settings", separated: true, onSelect: () => openOverlay(panelId, { kind: "settings", chartId }) },
    ];
  }
  return <TxContextMenu open onOpenChange={open => { if (!open) onClose(); }} point={{ x: at.x, y: at.y }} label={shown ? t("Price scale") : drawing ? t("Drawing") : t("Chart")} items={items} />;
}
