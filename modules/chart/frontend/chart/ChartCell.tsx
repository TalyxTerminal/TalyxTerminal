/* One chart of a layout: the engine's canvas with its legend over it. The binding does the drawing; React only places it. */
import { useEffect, useRef, useState } from "react";
import type { ModuleContext } from "@talyx/sdk";
import { useStore } from "../store";
import { ChartBinding, type BindingEvents } from "./binding";
import { Legend } from "./Legend";
import { DrawingBar } from "./DrawingBar";
import { ReplayBar, ReplayPicker } from "./Replay";
import { PricePlus } from "./PricePlus";

export interface CellProps {
  ctx: ModuleContext;
  panelId: string;
  chartId: string;
  active: boolean;
  onActivate(): void;
  events: BindingEvents;
  bindings: Map<string, ChartBinding>;
  onIndicatorSettings(chartId: string, indicatorId: string): void;
}

export function ChartCell({ ctx, panelId, chartId, active, onActivate, events, bindings, onIndicatorSettings }: CellProps) {
  const canvas = useRef<HTMLDivElement>(null);
  const ui = useStore(state => state.ui[panelId]);
  const ready = useStore(state => state.charts[chartId]?.chart !== null && state.charts[chartId] !== undefined);
  const binding = useRef<ChartBinding | null>(null);
  const [paneTops, setPaneTops] = useState(new Map<string, number>());
  useEffect(() => {
    if (!ready) return;
    const created = new ChartBinding(canvas.current!, ctx, chartId, ui, events, setPaneTops);
    binding.current = created; bindings.set(chartId, created);
    return () => { created.dispose(); bindings.delete(chartId); binding.current = null; };
  }, [ready, chartId]);
  useEffect(() => { binding.current?.setUi(ui); }, [ui]);
  return <div className="chart-cell" data-active={active} onMouseDownCapture={onActivate}>
    <div ref={canvas} className="chart-canvas" />
    <Legend ctx={ctx} panelId={panelId} chartId={chartId} onIndicatorSettings={id => onIndicatorSettings(chartId, id)} tops={paneTops} />
    <DrawingBar ctx={ctx} panelId={panelId} chartId={chartId} />
    {ui.replayPick === chartId && <ReplayPicker ctx={ctx} panelId={panelId} chartId={chartId} binding={() => binding.current} />}
    <ReplayBar ctx={ctx} panelId={panelId} chartId={chartId} />
    <PricePlus panelId={panelId} chartId={chartId} binding={() => binding.current} />
  </div>;
}
