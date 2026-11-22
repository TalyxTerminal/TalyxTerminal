import { t } from "../i18n";
/* TradingView's floating toolbar for the selected drawing, at the top of its chart: line colour, width and style, then
   settings, an alert on it (for the tools alerts can follow), lock and remove. */
import { TxButton, TxColorInput, TxIconButton, TxMenu, TxTooltip, type ModuleContext } from "@talyx/sdk";
import { commit, openOverlay, useStore } from "../store";
import { chartColors } from "../engine/theme";
import { ALERT_TOOLS } from "../panel/AlertDialog";
import type { Drawing, DrawingStyle } from "../drawings/types";

const WIDTHS = [1, 2, 3, 4];
const STYLES: { id: NonNullable<DrawingStyle["lineStyle"]>; label: string }[] = [{ id: "solid", label: t("Solid") }, { id: "dashed", label: t("Dashed") }, { id: "dotted", label: t("Dotted") }];

export function DrawingBar({ ctx, panelId, chartId }: { ctx: ModuleContext; panelId: string; chartId: string }) {
  const drawing = useStore(state => {
    const entry = state.charts[chartId], chart = entry?.chart;
    return entry?.selectedDrawing && chart ? chart.document.drawings[chart.drawingGroup]?.find(item => item.id === entry.selectedDrawing) : undefined;
  });
  if (!drawing) return null;
  const update = (patch: Partial<Pick<Drawing, "style" | "locked">>) => void commit(ctx, chartId, [{ type: "drawing.update", id: drawing.id, patch }]);
  const style = (change: DrawingStyle) => update({ style: { ...drawing.style, ...change } });
  const width = drawing.style.lineWidth ?? 2, line = drawing.style.lineStyle ?? "solid";
  return <div className="chart-drawing-bar tx-glass" role="toolbar" aria-label={t("Drawing")}>
    <TxColorInput aria-label={t("Line color")} value={drawing.style.color ?? chartColors().series[0]} onValueChange={color => style({ color })} />
    <TxMenu label={t("Line width")} items={WIDTHS.map(value => ({ id: String(value), label: t("{value}px", { value }), checked: value === width, onSelect: () => style({ lineWidth: value }) }))}
      trigger={<TxButton variant="tool" size="sm" aria-label={t("Line width")}>{width}{t("px")}</TxButton>} />
    <TxMenu label={t("Line style")} items={STYLES.map(item => ({ id: item.id, label: item.label, checked: item.id === line, onSelect: () => style({ lineStyle: item.id }) }))}
      trigger={<TxButton variant="tool" size="sm" aria-label={t("Line style")}>{STYLES.find(item => item.id === line)!.label}</TxButton>} />
    <span className="chart-tb-divider" />
    <TxTooltip label={t("Settings")}><TxIconButton variant="tool" size="sm" icon="settings" label={t("Settings")} onClick={() => openOverlay(panelId, { kind: "drawingSettings", chartId, drawingId: drawing.id })} /></TxTooltip>
    {ALERT_TOOLS.includes(drawing.tool) && <TxTooltip label={t("Add alert")}><TxIconButton variant="tool" size="sm" icon="bell" label={t("Add alert")} onClick={() => openOverlay(panelId, { kind: "alert", chartId, drawingId: drawing.id })} /></TxTooltip>}
    <TxTooltip label={drawing.locked ? t("Unlock") : t("Lock")}><TxIconButton variant="tool" size="sm" icon="lock" label={drawing.locked ? t("Unlock") : t("Lock")} tone={drawing.locked ? "active" : "default"} onClick={() => update({ locked: !drawing.locked })} /></TxTooltip>
    <TxTooltip label={t("Remove")}><TxIconButton variant="tool" size="sm" icon="trash" label={t("Remove")} onClick={() => void commit(ctx, chartId, [{ type: "drawing.delete", id: drawing.id }])} /></TxTooltip>
  </div>;
}
