import { displayIndicator, indicatorLabel } from "../indicatorText";
import { t } from "../i18n";
/* Indicator settings (TradingView): a small dialog that does not dim the chart, Inputs and Style tabs, labels on the
   left and controls on the right; every change shows on the chart at once, Cancel puts back what was there. */
import { Fragment, useRef, useState } from "react";
import { TxButton, TxCheckbox, TxColorInput, TxDialog, TxField, TxInput, TxMenu, TxSelect, TxTabs, type ModuleContext } from "@talyx/sdk";
import { commit, openOverlay, shownZone, useStore } from "../store";
import { NA_COLOR } from "../engine/pinePrimitives";
import { zoneOffset } from "../engine/timeZone";
import type { IndicatorInput, PlotStyle, Scalar } from "../wire";
import { DocumentInput } from "./DocumentField";

const SOURCES = ["open", "high", "low", "close", "hl2", "hlc3", "ohlc4", "hlcc4"];
const WIDTHS = [1, 2, 3, 4];
const LINE_STYLES: NonNullable<PlotStyle["lineStyle"]>[] = ["solid", "dashed", "dotted"];

/** A colour param as PyneCore reads it (`Color(text)` takes six or eight hex digits): `#RRGGBBAA`. */
function pineHex(value: string): string {
  const hex = value.slice(1).toUpperCase();
  const full = hex.length <= 4 ? [...hex].map(digit => digit + digit).join("") : hex;
  return `#${full.padEnd(8, "F")}`;
}

/** An input.time param (UTC ms) as a date and time on the chart's clock; 0 is none (an empty field). */
function TimeInput({ input, value, zone, onChange }: { input: IndicatorInput; value: Scalar; zone: string; onChange(value: Scalar): void }) {
  const ms = Number(value);
  const wall = ms ? new Date(ms + zoneOffset(zone, ms)).toISOString().slice(0, 16) : "";
  return <TxInput type="datetime-local" aria-label={input.title} value={wall} onChange={event => {
    const text = event.target.value;
    if (!text) { onChange(0); return; }
    const at = Date.parse(`${text}:00Z`);
    if (Number.isFinite(at)) onChange(at - zoneOffset(zone, at));
  }} />;
}

function InputControl({ input, value, zone, onChange }: { input: IndicatorInput; value: Scalar; zone: string; onChange(value: Scalar): void }) {
  if (input.type === "time") return <TimeInput input={input} value={value} zone={zone} onChange={onChange} />;
  if (input.type === "bool") return <TxCheckbox label={input.title} checked={value === true} onCheckedChange={onChange} />;
  if (input.type === "color") return <TxColorInput aria-label={input.title} value={String(value)} onValueChange={next => onChange(pineHex(next))} />;
  // The chosen option goes back as the script declared it (a number stays a number, an enum member is its value).
  const options = input.type === "source" ? SOURCES : input.options as Scalar[] | null | undefined;
  if (options?.length) return <TxSelect aria-label={input.title} value={String(value)} onChange={event => onChange(options.find(option => String(option) === event.target.value)!)}>
    {options.map(option => <option key={String(option)} value={String(option)}>{String(option)}</option>)}
  </TxSelect>;
  if (input.type === "int" || input.type === "float") return <DocumentInput numeric type="number" aria-label={input.title} value={String(value)}
    min={input.min ?? undefined} max={input.max ?? undefined} step={input.step ?? (input.type === "int" ? 1 : "any")}
    onValue={text => { const next = Number(text); if (text !== "" && Number.isFinite(next)) onChange(input.type === "int" ? Math.round(next) : next); }} />;
  return <DocumentInput aria-label={input.title} value={String(value)} onValue={onChange} />;
}

export function IndicatorSettings({ ctx, panelId, chartId, indicatorId }: { ctx: ModuleContext; panelId: string; chartId: string; indicatorId: string }) {
  const indicator = useStore(state => state.charts[chartId]?.chart?.document.indicators.find(item => item.id === indicatorId));
  const catalogEntry = useStore(state => indicator && state.catalog?.find(item => item.source === indicator.definition.source && item.id === indicator.definition.id));
  const meta = useStore(state => state.charts[chartId]?.feed.indicators.get(indicatorId)?.meta ?? []);
  const zone = useStore(state => shownZone(state.charts[chartId]));
  const original = useRef(indicator);
  const definition = catalogEntry && displayIndicator(catalogEntry);
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
  const plots = meta.filter(item => item.kind === "plot" || item.kind === "hline" || item.kind === "fill" || item.kind === "bgcolor" || item.kind === "candle" || item.kind === "bar");
  return <TxDialog open dim={false} onOpenChange={open => { if (!open) cancel(); }} title={indicator.name ?? definition?.title ?? indicator.definition.id}
    footer={<>
      <TxMenu label={t("Defaults")} items={[{ id: "reset", label: t("Reset settings"), onSelect: () => patch({ params: Object.fromEntries(inputs.map(input => [input.id, input.default as Scalar])), styles: {} }) }]}
        trigger={<TxButton size="sm">{t("Defaults")}</TxButton>} />
      <span className="chart-tb-spacer" />
      <TxButton size="sm" onClick={cancel}>{t("Cancel")}</TxButton>
      <TxButton size="sm" variant="primary" onClick={close}>{t("Ok")}</TxButton>
    </>}>
    <TxTabs value={tab} onChange={setTab} label={t("Indicator settings")} options={[{ value: "inputs", label: t("Inputs") }, { value: "style", label: t("Style") }]} />
    <div className="chart-form chart-settings-form">
      {tab === "inputs" && (inputs.length ? inputs.map((input, index) => <Fragment key={input.id}>
        {/* A group's title above its first input (Pine's input group). */}
        {input.group && input.group !== inputs[index - 1]?.group && <div className="chart-settings-group">{indicatorLabel(indicator.definition.source, input.group)}</div>}
        {input.type === "bool"
          ? <InputControl input={input} value={indicator.params[input.id] ?? input.default as Scalar} zone={zone} onChange={value => setParam(input.id, value)} />
          : <TxField label={input.title} hint={input.tooltip ?? undefined} layout="inline">
            <InputControl input={input} value={indicator.params[input.id] ?? input.default as Scalar} zone={zone} onChange={value => setParam(input.id, value)} />
          </TxField>}
      </Fragment>) : <p className="chart-muted">{t("This indicator has no inputs.")}</p>)}
      {tab === "style" && plots.map(item => {
        const style = indicator.styles[item.id] ?? {};
        // The script's colour as the picker reads it (#RRGGBBAA); none for na.
        const color = style.color ?? (item.color && item.color !== NA_COLOR ? item.color : "");
        return <div key={item.id} className="chart-style-row">
          <TxCheckbox label={indicatorLabel(indicator.definition.source, item.title ?? item.id)} checked={style.visible !== false} onCheckedChange={visible => setStyle(item.id, { visible })} />
          <TxColorInput aria-label={t("{value} color", { value: indicatorLabel(indicator.definition.source, item.title ?? item.id) })} value={color} onValueChange={value => setStyle(item.id, { color: value })} />
          {item.kind === "plot" || item.kind === "hline" ? <>
            <TxSelect aria-label={t("Line width")} value={String(style.lineWidth ?? item.linewidth ?? 1)} onChange={event => setStyle(item.id, { lineWidth: Number(event.target.value) })}>
              {WIDTHS.map(width => <option key={width} value={width}>{width}{t("px")}</option>)}
            </TxSelect>
            <TxSelect aria-label={t("Line style")} value={style.lineStyle ?? item.linestyle ?? "solid"} onChange={event => setStyle(item.id, { lineStyle: event.target.value as PlotStyle["lineStyle"] })}>
              {LINE_STYLES.map(lineStyle => <option key={lineStyle} value={lineStyle!}>{{ solid: t("Solid"), dashed: t("Dashed"), dotted: t("Dotted") }[lineStyle!]}</option>)}
            </TxSelect>
          </> : null}
        </div>;
      })}
    </div>
  </TxDialog>;
}
