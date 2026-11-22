import { t } from "../i18n";
/* Drawing settings (TradingView, opened by double-click): Style, Text, Coordinates and Visibility tabs in a small dialog
   that leaves the chart visible; changes show at once, Cancel puts the drawing back. Opened on the Text tab (a text tool
   just placed), the text starts focused and selected, so typing replaces it. */
import { useRef, useState } from "react";
import { TxButton, TxCheckbox, TxColorInput, TxDialog, TxField, TxSelect, TxTabs, type ModuleContext } from "@talyx/sdk";
import { commit, openOverlay, shownZone, useStore } from "../store";
import { overlayLevels, overlayTool } from "../drawings/registry";
import type { Drawing, DrawingStyle, OverlaySettings, TimeUnit, TimeframeBand } from "../drawings/types";
import { zoneOffset } from "../engine/timeZone";
import { BRICK_TYPES } from "../wire";
import { chartColors } from "../engine/theme";
import { TemplateMenu } from "./TemplateMenu";
import { DocumentInput, DocumentTextarea } from "./DocumentField";

type Tab = "style" | "text" | "coordinates" | "visibility";
const LINE_STYLES = ["solid", "dashed", "dotted"] as const;
const FILLED = new Set(["rect", "rotated_rect", "ellipse", "triangle", "parallel_channel", "regression_trend", "flat_top_bottom", "gann_box", "date_range", "price_range", "date_price_range", "long_position", "short_position"]);
const EXTENDS = new Set(["trendline", "info_line", "extended_line", "trend_angle", "parallel_channel", "regression_trend", "fib", "fib_extension", "fib_channel"]);
const UNITS: { key: TimeUnit; label: string; max: number }[] = [
  { key: "minutes", label: t("Minutes"), max: 59 }, { key: "hours", label: t("Hours"), max: 24 }, { key: "days", label: t("Days"), max: 366 },
  { key: "weeks", label: t("Weeks"), max: 52 }, { key: "months", label: t("Months"), max: 12 },
];

export function DrawingSettings({ ctx, panelId, chartId, drawingId, tab: opened }: { ctx: ModuleContext; panelId: string; chartId: string; drawingId: string; tab?: "text" }) {
  const drawing = useStore(state => { const chart = state.charts[chartId]?.chart; return chart?.document.drawings[chart.drawingGroup]?.find(item => item.id === drawingId); });
  const zone = useStore(state => shownZone(state.charts[chartId]));
  const bricks = useStore(state => BRICK_TYPES.includes(state.charts[chartId]?.chart?.document.chartType ?? "candles"));
  const original = useRef(drawing);
  const [tab, setTab] = useState<Tab>(opened ?? "style");
  const close = () => openOverlay(panelId, null);
  if (!drawing) return null;
  const tool = overlayTool(drawing.tool);
  /** `field`: a typed field, whose keystrokes waiting to be sent fold into one change. */
  const update = (patch: Partial<Pick<Drawing, "points" | "text" | "style" | "settings" | "visibility">>, field?: string) =>
    void commit(ctx, chartId, [{ type: "drawing.update", id: drawingId, patch }], field === undefined ? undefined : `${drawingId}.${field}`);
  const style = (change: DrawingStyle) => update({ style: { ...drawing.style, ...change } });
  const settings = (change: OverlaySettings) => update({ settings: { ...drawing.settings, ...change } });
  const cancel = () => {
    const before = original.current!;
    const keys = ["points", "text", "style", "settings", "visibility"] as const;
    if (keys.some(key => JSON.stringify(before[key]) !== JSON.stringify(drawing[key]))) update(Object.fromEntries(keys.map(key => [key, before[key]])));
    close();
  };
  const wall = (ms: number) => new Date(ms + zoneOffset(zone, ms)).toISOString().slice(0, 16);
  const fromWall = (text: string) => { const ms = Date.parse(`${text}:00Z`); return ms - zoneOffset(zone, ms); };
  const levels = tool.levels ? overlayLevels(drawing.tool, drawing.settings.levels) : [];
  const tabs: { value: Tab; label: string }[] = [{ value: "style", label: t("Style") }, ...(tool.textMode !== "none" && tool.textMode !== "generated" ? [{ value: "text" as const, label: t("Text") }] : []),
    { value: "coordinates", label: t("Coordinates") }, { value: "visibility", label: t("Visibility") }];
  return <TxDialog open dim={false} onOpenChange={open => { if (!open) cancel(); }} title={tool.label}
    footer={<>
      {/* A drawing template is one tool's look: style and settings, not its points or text. */}
      <TemplateMenu ctx={ctx} chartId={chartId} kind="drawing" matches={template => template.content.tool === drawing.tool}
        content={() => ({ tool: drawing.tool, style: drawing.style, settings: drawing.settings })}
        apply={content => update({ style: content.style as DrawingStyle, settings: content.settings as OverlaySettings })} />
      <span className="chart-tb-spacer" /><TxButton size="sm" onClick={cancel}>{t("Cancel")}</TxButton><TxButton size="sm" variant="primary" onClick={close}>{t("Ok")}</TxButton></>}>
    <TxTabs value={tab} onChange={setTab} label={t("Drawing settings")} options={tabs} />
    <div className="chart-form chart-settings-form">
      {tab === "style" && <>
        <TxField label={t("Line")} layout="inline"><div className="chart-style-row">
          <TxColorInput aria-label={t("Line color")} value={drawing.style.color ?? chartColors().series[0]} onValueChange={color => style({ color })} />
          <TxSelect aria-label={t("Line width")} value={String(drawing.style.lineWidth ?? 2)} onChange={event => style({ lineWidth: Number(event.target.value) })}>
            {[1, 2, 3, 4].map(width => <option key={width} value={width}>{width}{t("px")}</option>)}
          </TxSelect>
          <TxSelect aria-label={t("Line style")} value={drawing.style.lineStyle ?? "solid"} onChange={event => style({ lineStyle: event.target.value as DrawingStyle["lineStyle"] })}>
            {LINE_STYLES.map(item => <option key={item} value={item}>{{ solid: t("Solid"), dashed: t("Dashed"), dotted: t("Dotted") }[item]}</option>)}
          </TxSelect>
        </div></TxField>
        {FILLED.has(drawing.tool) && <TxField label={t("Background")} layout="inline"><div className="chart-style-row">
          <TxColorInput aria-label={t("Background color")} value={drawing.style.fillColor ?? ""} onValueChange={fillColor => style({ fillColor })} />
          <DocumentInput numeric type="number" aria-label={t("Background opacity")} min={0} max={100} suffix="%" value={String(Math.round((drawing.style.fillOpacity ?? 0.2) * 100))}
            onValue={text => update({ style: { ...drawing.style, fillOpacity: Math.min(100, Math.max(0, Number(text))) / 100 } }, "fillOpacity")} />
        </div></TxField>}
        {EXTENDS.has(drawing.tool) && <div className="chart-style-row">
          <TxCheckbox label={t("Extend left")} checked={drawing.settings.extendLeft === true} onCheckedChange={extendLeft => settings({ extendLeft })} />
          <TxCheckbox label={t("Extend right")} checked={drawing.settings.extendRight === true} onCheckedChange={extendRight => settings({ extendRight })} />
        </div>}
        {levels.length > 0 && <>
          <div className="chart-style-row">
            <TxCheckbox label={t("Prices")} checked={drawing.settings.showPrices !== false} onCheckedChange={showPrices => settings({ showPrices })} />
            <TxCheckbox label={t("Levels")} checked={drawing.settings.showLevels !== false} onCheckedChange={showLevels => settings({ showLevels })} />
            <TxCheckbox label={t("Background")} checked={drawing.settings.showBackground !== false} onCheckedChange={showBackground => settings({ showBackground })} />
            <TxCheckbox label={t("Reverse")} checked={drawing.settings.reverse === true} onCheckedChange={reverse => settings({ reverse })} />
          </div>
          <div className="chart-levels">
            {levels.map(value => {
              const own = drawing.settings.levelStyles?.find(item => item.value === value);
              const setLevel = (change: { visible?: boolean; color?: string }) => settings({ levelStyles: [
                ...(drawing.settings.levelStyles ?? []).filter(item => item.value !== value), { ...own, value, ...change }] });
              return <div key={value} className="chart-style-row">
                <TxCheckbox label={String(value)} checked={own?.visible !== false} onCheckedChange={visible => setLevel({ visible })} />
                <TxColorInput aria-label={t("Level {value} color", { value })} value={own?.color ?? drawing.style.color ?? ""} onValueChange={color => setLevel({ color })} />
              </div>;
            })}
          </div>
        </>}
      </>}
      {tab === "text" && <>
        {/* An emptied text removes it; a tool that needs text keeps its last one until the user types again. */}
        <DocumentTextarea aria-label={t("Text")} value={drawing.text ?? ""} rows={4} data-autofocus={opened === "text" || undefined}
          onFocus={opened === "text" ? event => event.currentTarget.select() : undefined}
          onValue={text => { if (text || tool.textMode !== "required") update({ text: text || null }, "text"); }} />
        <div className="chart-style-row">
          <TxColorInput aria-label={t("Text color")} value={drawing.style.textColor ?? drawing.style.color ?? ""} onValueChange={textColor => style({ textColor })} />
          <TxSelect aria-label={t("Font size")} value={String(drawing.style.fontSize ?? 14)} onChange={event => style({ fontSize: Number(event.target.value) })}>
            {[10, 11, 12, 14, 16, 20, 24, 28, 32, 40].map(size => <option key={size} value={size}>{size}</option>)}
          </TxSelect>
          <TxCheckbox label={t("Bold")} checked={drawing.style.fontWeight === 600} onCheckedChange={bold => style({ fontWeight: bold ? 600 : 400 })} />
          <TxCheckbox label={t("Italic")} checked={drawing.style.fontStyle === "italic"} onCheckedChange={italic => style({ fontStyle: italic ? "italic" : "normal" })} />
        </div>
      </>}
      {tab === "coordinates" && drawing.points.map((point, index) => <TxField key={index} label={`#${index + 1}`} layout="inline"><div className="chart-style-row">
        <DocumentInput numeric type="number" aria-label={t("Point {value} price", { value: index + 1 })} value={String(point.price)} step="any"
          onValue={text => { const price = Number(text); if (text !== "" && Number.isFinite(price)) update({ points: drawing.points.map((item, at) => at === index ? { ...item, price } : item) }, `price${index}`); }} />
        {bricks ? <DocumentInput numeric type="number" aria-label={t("Point {value} brick", { value: index + 1 })} value={String(point.time)} step={1}
          onValue={text => { if (text !== "") update({ points: drawing.points.map((item, at) => at === index ? { ...item, time: Math.round(Number(text)) } : item) }, `time${index}`); }} />
        : <DocumentInput type="datetime-local" aria-label={t("Point {value} time", { value: index + 1 })} value={wall(point.time)}
          onValue={text => { if (text) update({ points: drawing.points.map((item, at) => at === index ? { ...item, time: fromWall(text) } : item) }, `time${index}`); }} />}
      </div></TxField>)}
      {tab === "visibility" && UNITS.map(({ key: unit, label, max }) => {
        const band: TimeframeBand = drawing.visibility[unit] ?? { enabled: true, from: 1, to: max };
        const set = (change: Partial<TimeframeBand>, field?: string) => update({ visibility: { ...drawing.visibility, [unit]: { ...band, ...change } } }, field);
        return <div key={unit} className="chart-style-row">
          <TxCheckbox label={label} checked={band.enabled} onCheckedChange={enabled => set({ enabled })} />
          <DocumentInput numeric type="number" aria-label={t("{label} from", { label })} min={1} max={max} value={String(band.from)} disabled={!band.enabled} onValue={text => { if (text !== "") set({ from: Number(text) }, `${unit}.from`); }} />
          <DocumentInput numeric type="number" aria-label={t("{label} to", { label })} min={1} max={max} value={String(band.to)} disabled={!band.enabled} onValue={text => { if (text !== "") set({ to: Number(text) }, `${unit}.to`); }} />
        </div>;
      })}
    </div>
  </TxDialog>;
}
