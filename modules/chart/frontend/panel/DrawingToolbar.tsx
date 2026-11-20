/* The left drawing toolbar (TradingView): the cursor, then one button per tool group showing the last tool used (its
   arrow opens the group); below, magnet, keep drawing, lock all, hide all and remove. */
import { useState } from "react";
import { TxButton, TxIcon, TxIconButton, TxMenu, TxPopover, TxTooltip, type ModuleContext } from "@talyx/sdk";
import { OVERLAY_GROUPS, OVERLAY_TOOL_REGISTRY, type OverlayGroup, type OverlayType } from "../drawings/registry";
import { commit, removeObjects, setUi, useStore } from "../store";
import { DrawingToolGlyph, ChartGlyph } from "../chart/icons";

const GROUP_NAMES: Record<OverlayGroup, string> = {
  lines: "Lines", channels: "Channels and pitchforks", fib_gann: "Fibonacci and Gann", geometry: "Shapes and brushes",
  annotation: "Text and notes", patterns: "Patterns", measurement_position: "Prediction and measurement",
};
/** TradingView's shortcuts for the tools that have one. */
export const TOOL_SHORTCUTS: Partial<Record<OverlayType, string>> = { trendline: "Alt+T", hline: "Alt+H", vline: "Alt+V", fib: "Alt+F", rect: "Alt+Shift+R", cross_line: "Alt+C" };

function GroupButton({ panelId, group, last, armed, onLast }: { panelId: string; group: OverlayGroup; last: OverlayType; armed: OverlayType | null; onLast(tool: OverlayType): void }) {
  const [open, setOpen] = useState(false);
  const tools = OVERLAY_TOOL_REGISTRY.filter(tool => tool.group === group);
  const shown = tools.some(tool => tool.id === armed) ? armed! : last;
  const arm = (tool: OverlayType | null) => setUi(panelId, { tool });
  return <div className="chart-dt-group">
    <TxTooltip label={OVERLAY_TOOL_REGISTRY.find(tool => tool.id === shown)!.label}>
      <TxButton variant="tool" size="sm" aria-pressed={armed === shown} aria-label={OVERLAY_TOOL_REGISTRY.find(tool => tool.id === shown)!.label}
        onClick={() => arm(armed === shown ? null : shown)}><DrawingToolGlyph type={shown} /></TxButton>
    </TxTooltip>
    <TxPopover open={open} onOpenChange={setOpen} label={GROUP_NAMES[group]} side="right"
      trigger={<TxButton variant="tool" size="sm" className="chart-dt-more" aria-label={`${GROUP_NAMES[group]} tools`}><TxIcon name="chevron" /></TxButton>}>
      <div className="chart-dt-menu" role="menu">
        <div className="chart-dt-menu-title">{GROUP_NAMES[group]}</div>
        {tools.map(tool => <TxButton key={tool.id} variant="row" size="sm" role="menuitemradio" aria-checked={armed === tool.id}
          onClick={() => { setOpen(false); onLast(tool.id as OverlayType); arm(tool.id as OverlayType); }}>
          <DrawingToolGlyph type={tool.id as OverlayType} /><span>{tool.label}</span>{TOOL_SHORTCUTS[tool.id as OverlayType] && <kbd>{TOOL_SHORTCUTS[tool.id as OverlayType]}</kbd>}
        </TxButton>)}
      </div>
    </TxPopover>
  </div>;
}

export function DrawingToolbar({ ctx, panelId }: { ctx: ModuleContext; panelId: string }) {
  const ui = useStore(state => state.ui[panelId]);
  const active = useStore(state => state.layouts[panelId].active);
  const chart = useStore(state => state.charts[active]?.chart);
  const [last, setLast] = useState<Record<OverlayGroup, OverlayType>>(() => Object.fromEntries(OVERLAY_GROUPS.map(group =>
    [group, OVERLAY_TOOL_REGISTRY.find(tool => tool.group === group)!.id])) as Record<OverlayGroup, OverlayType>);
  const drawings = chart ? chart.document.drawings[chart.drawingGroup] ?? [] : [];
  const allLocked = drawings.length > 0 && drawings.every(drawing => drawing.locked);
  const removeAll = (what: "drawings" | "indicators" | "all") => void removeObjects(ctx, active, what);
  return <div className="chart-drawbar" role="toolbar" aria-label="Drawing tools" aria-orientation="vertical">
    <TxTooltip label="Cross"><TxButton variant="tool" size="sm" aria-pressed={ui.tool === null} aria-label="Cross" onClick={() => setUi(panelId, { tool: null })}><ChartGlyph name="cursor-cross" /></TxButton></TxTooltip>
    <span className="chart-dt-divider" />
    {OVERLAY_GROUPS.map(group => <GroupButton key={group} panelId={panelId} group={group} last={last[group]} armed={ui.tool} onLast={tool => setLast({ ...last, [group]: tool })} />)}
    <span className="chart-dt-spacer" />
    <TxMenu label="Magnet" items={[
      { id: "off", label: "Magnet off", checked: ui.magnet === "off", onSelect: () => setUi(panelId, { magnet: "off" }) },
      { id: "weak", label: "Weak magnet", checked: ui.magnet === "weak", onSelect: () => setUi(panelId, { magnet: "weak" }) },
      { id: "strong", label: "Strong magnet", checked: ui.magnet === "strong", onSelect: () => setUi(panelId, { magnet: "strong" }) },
    ]} trigger={<TxIconButton variant="tool" size="sm" icon="magnet" label="Magnet" tone={ui.magnet === "off" ? "default" : "active"} />} />
    <TxIconButton variant="tool" size="sm" icon="edit" label="Keep drawing" tone={ui.keepDrawing ? "active" : "default"} onClick={() => setUi(panelId, { keepDrawing: !ui.keepDrawing })} />
    {drawings.length > 0 && <TxIconButton variant="tool" size="sm" icon="lock" label={allLocked ? "Unlock all drawings" : "Lock all drawings"} tone={allLocked ? "active" : "default"}
      onClick={() => void commit(ctx, active, [{ type: "drawing.bulk", locked: !allLocked }])} />}
    <TxIconButton variant="tool" size="sm" icon={ui.drawingsHidden ? "eye-off" : "eye"} label={ui.drawingsHidden ? "Show all drawings" : "Hide all drawings"} tone={ui.drawingsHidden ? "active" : "default"}
      onClick={() => setUi(panelId, { drawingsHidden: !ui.drawingsHidden })} />
    {(drawings.length > 0 || (chart?.document.indicators.length ?? 0) > 0) && <TxMenu label="Remove" items={[
      ...(drawings.length ? [{ id: "drawings", label: `Remove ${drawings.length} drawing${drawings.length > 1 ? "s" : ""}`, onSelect: () => removeAll("drawings") }] : []),
      ...(chart?.document.indicators.length ? [{ id: "indicators", label: `Remove ${chart.document.indicators.length} indicator${chart.document.indicators.length > 1 ? "s" : ""}`, onSelect: () => removeAll("indicators") }] : []),
      ...(drawings.length && chart?.document.indicators.length ? [{ id: "all", label: "Remove drawings and indicators", tone: "danger" as const, onSelect: () => removeAll("all") }] : []),
    ]} trigger={<TxIconButton variant="tool" size="sm" icon="trash" label="Remove objects" />} />}
  </div>;
}
