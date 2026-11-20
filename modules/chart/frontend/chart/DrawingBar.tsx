/* TradingView's floating toolbar for the selected drawing, at the top of its chart: line colour, width and style, then
   settings, an alert on it (for the tools alerts can follow), lock and remove. */
import { TxButton, TxColorInput, TxIconButton, TxMenu, TxTooltip, type ModuleContext } from "@talyx/sdk";
import { commit, openOverlay, useStore } from "../store";
import { chartColors } from "../engine/theme";
import { ALERT_TOOLS } from "../panel/AlertDialog";
import type { Drawing, DrawingStyle } from "../drawings/types";

const WIDTHS = [1, 2, 3, 4];
const STYLES: { id: NonNullable<DrawingStyle["lineStyle"]>; label: string }[] = [{ id: "solid", label: "Solid" }, { id: "dashed", label: "Dashed" }, { id: "dotted", label: "Dotted" }];

export function DrawingBar({ ctx, panelId, chartId }: { ctx: ModuleContext; panelId: string; chartId: string }) {
  const drawing = useStore(state => {
    const entry = state.charts[chartId], chart = entry?.chart;
    return entry?.selectedDrawing && chart ? chart.document.drawings[chart.drawingGroup]?.find(item => item.id === entry.selectedDrawing) : undefined;
  });
  if (!drawing) return null;
  const update = (patch: Partial<Pick<Drawing, "style" | "locked">>) => void commit(ctx, chartId, [{ type: "drawing.update", id: drawing.id, patch }]);
  const style = (change: DrawingStyle) => update({ style: { ...drawing.style, ...change } });
  const width = drawing.style.lineWidth ?? 2, line = drawing.style.lineStyle ?? "solid";
  return <div className="chart-drawing-bar tx-glass" role="toolbar" aria-label="Drawing">
    <TxColorInput aria-label="Line color" value={drawing.style.color ?? chartColors().series[0]} onValueChange={color => style({ color })} />
    <TxMenu label="Line width" items={WIDTHS.map(value => ({ id: String(value), label: `${value}px`, checked: value === width, onSelect: () => style({ lineWidth: value }) }))}
      trigger={<TxButton variant="tool" size="sm" aria-label="Line width">{width}px</TxButton>} />
    <TxMenu label="Line style" items={STYLES.map(item => ({ id: item.id, label: item.label, checked: item.id === line, onSelect: () => style({ lineStyle: item.id }) }))}
      trigger={<TxButton variant="tool" size="sm" aria-label="Line style">{STYLES.find(item => item.id === line)!.label}</TxButton>} />
    <span className="chart-tb-divider" />
    <TxTooltip label="Settings"><TxIconButton variant="tool" size="sm" icon="settings" label="Settings" onClick={() => openOverlay(panelId, { kind: "drawingSettings", chartId, drawingId: drawing.id })} /></TxTooltip>
    {ALERT_TOOLS.includes(drawing.tool) && <TxTooltip label="Add alert"><TxIconButton variant="tool" size="sm" icon="bell" label="Add alert" onClick={() => openOverlay(panelId, { kind: "alert", chartId, drawingId: drawing.id })} /></TxTooltip>}
    <TxTooltip label={drawing.locked ? "Unlock" : "Lock"}><TxIconButton variant="tool" size="sm" icon="lock" label={drawing.locked ? "Unlock" : "Lock"} tone={drawing.locked ? "active" : "default"} onClick={() => update({ locked: !drawing.locked })} /></TxTooltip>
    <TxTooltip label="Remove"><TxIconButton variant="tool" size="sm" icon="trash" label="Remove" onClick={() => void commit(ctx, chartId, [{ type: "drawing.delete", id: drawing.id }])} /></TxTooltip>
  </div>;
}
