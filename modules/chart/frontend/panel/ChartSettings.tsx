import { t } from "../i18n";
/* Chart settings (TradingView): a small dialog that leaves the chart undimmed, pages listed on the left, labels on the left
   and controls on the right; every change shows on the chart at once, Cancel puts the chart's display back. The settings
   live in the chart's display (the backend stores it and never reads it); a chart template keeps them under a name. */
import { useRef, useState } from "react";
import { TxButton, TxCheckbox, TxColorInput, TxDialog, TxField, TxSelect, type ModuleContext } from "@talyx/sdk";
import { commit, openOverlay, useStore } from "../store";
import { TemplateMenu } from "./TemplateMenu";
import { chartColors, chartColorsWith } from "../engine/theme";
import type { Appearance, Display } from "../wire";
import { ZONES } from "./BottomBar";

type Page = "symbol" | "status" | "scales" | "canvas" | "alerts";
const PAGES: { id: Page; label: string }[] = [
  { id: "symbol", label: t("Symbol") }, { id: "status", label: t("Status line") }, { id: "scales", label: t("Scales and lines") }, { id: "canvas", label: t("Canvas") }, { id: "alerts", label: t("Alerts") },
];
/** What a chart template keeps: the look, not the time zone. */
const TEMPLATE_KEYS = ["appearance", "precision", "scales", "statusLine", "alertLine"] as const;

export function ChartSettings({ ctx, panelId, chartId }: { ctx: ModuleContext; panelId: string; chartId: string }) {
  const display = useStore(state => state.charts[chartId]?.chart?.document.display);
  const original = useRef(display);
  const [page, setPage] = useState<Page>("symbol");
  const close = () => openOverlay(panelId, null);
  if (!display) return null;
  const update = (patch: Partial<Display>) => void commit(ctx, chartId, [{ type: "display.update", patch }]);
  const cancel = () => {
    if (JSON.stringify(original.current) !== JSON.stringify(display)) void commit(ctx, chartId, [{ type: "display.replace", display: original.current! }]);
    close();
  };
  return <TxDialog open dim={false} onOpenChange={open => { if (!open) cancel(); }} title={t("Chart settings")}
    footer={<>
      <TemplateMenu ctx={ctx} chartId={chartId} kind="chart" apply={content => update(content as Partial<Display>)}
        content={() => Object.fromEntries(TEMPLATE_KEYS.filter(key => display[key] !== undefined).map(key => [key, display[key]]))} />
      <span className="chart-tb-spacer" />
      <TxButton size="sm" onClick={cancel}>{t("Cancel")}</TxButton>
      <TxButton size="sm" variant="primary" onClick={close}>{t("Ok")}</TxButton>
    </>}>
    <div className="chart-settings">
      <nav className="chart-settings-nav" aria-label={t("Chart settings pages")}>
        {PAGES.map(item => <TxButton key={item.id} variant="row" size="sm" aria-current={page === item.id} onClick={() => setPage(item.id)}>{item.label}</TxButton>)}
      </nav>
      <div className="chart-form chart-settings-page">
        {page === "symbol" && <SymbolPage display={display} update={update} />}
        {page === "status" && <Checks values={display.statusLine} onChange={statusLine => update({ statusLine })} items={[
          ["title", t("Symbol")], ["ohlc", t("OHLC values")], ["change", t("Bar change values")], ["volume", t("Volume")],
          ["indicatorArguments", t("Indicator arguments")], ["indicatorValues", t("Indicator values")],
        ]} />}
        {page === "scales" && <Checks values={display.scales} onChange={scales => update({ scales })} items={[
          ["lastPriceLabel", t("Symbol last price label")], ["lastPriceLine", t("Symbol last price line")], ["countdown", t("Countdown to bar close")],
          ["indicatorLabels", t("Indicator last value labels")],
        ]} />}
        {page === "canvas" && <CanvasPage display={display} update={update} />}
        {page === "alerts" && <AlertsPage display={display} update={update} />}
      </div>
    </div>
  </TxDialog>;
}

/** Checkboxes over one display group, each on unless turned off. */
function Checks<K extends string>({ values, items, onChange }: { values: Partial<Record<K, boolean>> | undefined; items: [K, string][]; onChange(next: Partial<Record<K, boolean>>): void }) {
  return <>{items.map(([key, label]) => <TxCheckbox key={key} label={label} checked={values?.[key] !== false} onCheckedChange={checked => onChange({ ...values, [key]: checked } as Partial<Record<K, boolean>>)} />)}</>;
}

function ColorPair({ label, up, down, onChange }: { label: string; up: string; down: string; onChange(up: string, down: string): void }) {
  return <TxField label={label} layout="inline">
    <span className="chart-style-row">
      <TxColorInput aria-label={t("{label} up", { label })} value={up} onValueChange={value => onChange(value, down)} />
      <TxColorInput aria-label={t("{label} down", { label })} value={down} onValueChange={value => onChange(up, value)} />
    </span>
  </TxField>;
}

function SymbolPage({ display, update }: { display: Display; update(patch: Partial<Display>): void }) {
  const look = display.appearance ?? {}, colors = chartColorsWith(look);
  const set = (change: Appearance) => update({ appearance: { ...look, ...change } });
  const local = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return <>
    <ColorPair label={t("Body")} up={colors.up} down={colors.down} onChange={(up, down) => set({ up, down })} />
    <ColorPair label={t("Borders")} up={colors.borderUp} down={colors.borderDown} onChange={(borderUp, borderDown) => set({ borderUp, borderDown })} />
    <ColorPair label={t("Wick")} up={colors.wickUp} down={colors.wickDown} onChange={(wickUp, wickDown) => set({ wickUp, wickDown })} />
    <TxField label={t("Precision")} layout="inline">
      <TxSelect aria-label={t("Precision")} value={display.precision === null || display.precision === undefined ? "default" : String(display.precision)}
        onChange={event => update({ precision: event.target.value === "default" ? null : Number(event.target.value) })}>
        <option value="default">{t("Default")}</option>
        {Array.from({ length: 9 }, (_, decimals) => <option key={decimals} value={decimals}>{(10 ** -decimals).toFixed(decimals)}</option>)}
      </TxSelect>
    </TxField>
    <TxField label={t("Time zone")} layout="inline">
      <TxSelect aria-label={t("Time zone")} value={display.timeZone ?? "exchange"} onChange={event => update({ timeZone: event.target.value })}>
        <option value="exchange">{t("Exchange")}</option>
        {[...new Set([local, ...ZONES])].map(zone => <option key={zone} value={zone}>{zone.replace(/_/g, " ")}</option>)}
      </TxSelect>
    </TxField>
  </>;
}

function CanvasPage({ display, update }: { display: Display; update(patch: Partial<Display>): void }) {
  const look = display.appearance ?? {}, colors = chartColorsWith(look);
  const set = (change: Appearance) => update({ appearance: { ...look, ...change } });
  return <>
    <TxField label={t("Background")} layout="inline"><TxColorInput aria-label={t("Background")} value={colors.background} onValueChange={background => set({ background })} /></TxField>
    <TxCheckbox label={t("Vertical grid lines")} checked={look.vertGrid !== false} onCheckedChange={vertGrid => set({ vertGrid })} />
    <TxCheckbox label={t("Horizontal grid lines")} checked={look.horzGrid !== false} onCheckedChange={horzGrid => set({ horzGrid })} />
    <TxCheckbox label={t("Symbol watermark")} checked={look.watermark === true} onCheckedChange={watermark => set({ watermark })} />
  </>;
}

/** TradingView's Alerts page: how the lines of alerts on this symbol are drawn, one control a row like the other pages. */
function AlertsPage({ display, update }: { display: Display; update(patch: Partial<Display>): void }) {
  const line = display.alertLine ?? {};
  const set = (change: NonNullable<Display["alertLine"]>) => update({ alertLine: { ...line, ...change } });
  return <>
    <TxField label={t("Line color")} layout="inline"><TxColorInput aria-label={t("Alert line color")} value={line.color ?? chartColors().accent} onValueChange={color => set({ color })} /></TxField>
    <TxField label={t("Line width")} layout="inline">
      <TxSelect aria-label={t("Alert line width")} value={String(line.width ?? 1)} onChange={event => set({ width: Number(event.target.value) })}>
        {[1, 2, 3, 4].map(width => <option key={width} value={width}>{width}{t("px")}</option>)}
      </TxSelect>
    </TxField>
    <TxField label={t("Line style")} layout="inline">
      <TxSelect aria-label={t("Alert line style")} value={line.style ?? "dashed"} onChange={event => set({ style: event.target.value as "solid" | "dashed" | "dotted" })}>
        <option value="solid">{t("Solid")}</option><option value="dashed">{t("Dashed")}</option><option value="dotted">{t("Dotted")}</option>
      </TxSelect>
    </TxField>
  </>;
}
