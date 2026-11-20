/* Indicator settings (TradingView): a small dialog that does not dim the chart, Inputs and Style tabs, labels on the
   left and controls on the right; every change shows on the chart at once, Cancel puts back what was there. */
import { useRef, useState } from "react";
import { TxButton, TxCheckbox, TxColorInput, TxDialog, TxField, TxMenu, TxSelect, TxTabs, type ModuleContext } from "@talyx/sdk";
import { commit, openOverlay, useStore } from "../store";
import { pineColor } from "../engine/pinePrimitives";
import type { IndicatorInput, PlotStyle, Scalar } from "../wire";
import { DocumentInput } from "./DocumentField";

const SOURCES = ["open", "high", "low", "close", "hl2", "hlc3", "ohlc4", "hlcc4"];
const WIDTHS = [1, 2, 3, 4];
const LINE_STYLES: NonNullable<PlotStyle["lineStyle"]>[] = ["solid", "dashed", "dotted"];

function InputControl({ input, value, onChange }: { input: IndicatorInput; value: Scalar; onChange(value: Scalar): void }) {
  if (input.type === "bool") return <TxCheckbox label={input.title} checked={value === true} onCheckedChange={onChange} />;
  if (input.type === "color") return <TxColorInput aria-label={input.title} value={String(value)} onValueChange={onChange} />;
  const options = input.type === "source" ? SOURCES : input.options?.map(String);
  if (options?.length) return <TxSelect aria-label={input.title} value={String(value)} onChange={event => onChange(event.target.value)}>
    {options.map(option => <option key={option} value={option}>{option}</option>)}
  </TxSelect>;
  if (input.type === "int" || input.type === "float") return <DocumentInput numeric type="number" aria-label={input.title} value={String(value)}
    min={input.min ?? undefined} max={input.max ?? undefined} step={input.step ?? (input.type === "int" ? 1 : "any")}
    onValue={text => { const next = Number(text); if (text !== "" && Number.isFinite(next)) onChange(input.type === "int" ? Math.round(next) : next); }} />;
  return <DocumentInput aria-label={input.title} value={String(value)} onValue={onChange} />;
}

export function IndicatorSettings({ ctx, panelId, chartId, indicatorId }: { ctx: ModuleContext; panelId: string; chartId: string; indicatorId: string }) {
  const indicator = useStore(state => state.charts[chartId]?.chart?.document.indicators.find(item => item.id === indicatorId));
  const definition = useStore(state => indicator && state.catalog?.find(item => item.source === indicator.definition.source && item.id === indicator.definition.id));
  const meta = useStore(state => state.charts[chartId]?.feed.indicators.get(indicatorId)?.meta ?? []);
  const original = useRef(indicator);
  const [tab, setTab] = useState<"inputs" | "style">("inputs");
  const close = () => openOverlay(panelId, null);
  if (!indicator) return null;
  const patch = (change: { params?: Record<string, Scalar>; styles?: Record<string, PlotStyle> }, field?: string) =>
    void commit(ctx, chartId, [{ type: "indicator.update", id: indicatorId, patch: change }], field === undefined ? undefined : `${indicatorId}.${field}`);
  // One input's changes waiting to be sent fold into its newest value.
  const setParam = (id: string, value: Scalar) => patch({ params: { ...indicator.params, [id]: value } }, id);
  const setStyle = (id: string, change: PlotStyle) => patch({ styles: { ...indicator.styles, [id]: { ...indicator.styles[id], ...change } } });
  const cancel = () => {
    const before = original.current!;
    if (JSON.stringify([before.params, before.styles]) !== JSON.stringify([indicator.params, indicator.styles])) patch({ params: before.params, styles: before.styles });
    close();
  };
  const inputs = definition?.inputs ?? [];
  const plots = meta.filter(item => item.kind === "plot" || item.kind === "hline" || item.kind === "fill" || item.kind === "bgcolor");
  return <TxDialog open dim={false} onOpenChange={open => { if (!open) cancel(); }} title={indicator.name ?? definition?.title ?? indicator.definition.id}
    footer={<>
      <TxMenu label="Defaults" items={[{ id: "reset", label: "Reset settings", onSelect: () => patch({ params: Object.fromEntries(inputs.map(input => [input.id, input.default as Scalar])), styles: {} }) }]}
        trigger={<TxButton size="sm">Defaults</TxButton>} />
      <span className="chart-tb-spacer" />
      <TxButton size="sm" onClick={cancel}>Cancel</TxButton>
      <TxButton size="sm" variant="primary" onClick={close}>Ok</TxButton>
    </>}>
    <TxTabs value={tab} onChange={setTab} label="Indicator settings" options={[{ value: "inputs", label: "Inputs" }, { value: "style", label: "Style" }]} />
    <div className="chart-form chart-settings-form">
      {tab === "inputs" && (inputs.length ? inputs.map(input => input.type === "bool"
        ? <InputControl key={input.id} input={input} value={indicator.params[input.id] ?? input.default as Scalar} onChange={value => setParam(input.id, value)} />
        : <TxField key={input.id} label={input.title} hint={input.tooltip ?? undefined} layout="inline">
          <InputControl input={input} value={indicator.params[input.id] ?? input.default as Scalar} onChange={value => setParam(input.id, value)} />
        </TxField>) : <p className="chart-muted">This indicator has no inputs.</p>)}
      {tab === "style" && plots.map(item => {
        const style = indicator.styles[item.id] ?? {};
        const color = style.color ?? pineColor(item.color ?? null) ?? "";
        return <div key={item.id} className="chart-style-row">
          <TxCheckbox label={item.title ?? item.id} checked={style.visible !== false} onCheckedChange={visible => setStyle(item.id, { visible })} />
          <TxColorInput aria-label={`${item.title ?? item.id} color`} value={color} onValueChange={value => setStyle(item.id, { color: value })} />
          {item.kind === "plot" || item.kind === "hline" ? <>
            <TxSelect aria-label="Line width" value={String(style.lineWidth ?? item.linewidth ?? 1)} onChange={event => setStyle(item.id, { lineWidth: Number(event.target.value) })}>
              {WIDTHS.map(width => <option key={width} value={width}>{width}px</option>)}
            </TxSelect>
            <TxSelect aria-label="Line style" value={style.lineStyle ?? "solid"} onChange={event => setStyle(item.id, { lineStyle: event.target.value as PlotStyle["lineStyle"] })}>
              {LINE_STYLES.map(lineStyle => <option key={lineStyle} value={lineStyle!}>{lineStyle![0].toUpperCase() + lineStyle!.slice(1)}</option>)}
            </TxSelect>
          </> : null}
        </div>;
      })}
    </div>
  </TxDialog>;
}
