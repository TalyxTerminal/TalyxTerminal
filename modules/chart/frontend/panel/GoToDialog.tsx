import { t } from "../i18n";
/* Go to (TradingView): a date, or a custom range, brought into view on the chart (older bars are fetched first). */
import { useState } from "react";
import { TxButton, TxDialog, TxField, TxInput, TxTabs } from "@talyx/sdk";
import { focusChart, openOverlay, shownZone, useStore } from "../store";
import { zoneOffset } from "../engine/timeZone";

const today = () => new Date().toISOString().slice(0, 10);

export function GoToDialog({ panelId, chartId }: { panelId: string; chartId: string }) {
  const [tab, setTab] = useState<"date" | "range">("date");
  const [date, setDate] = useState(today()), [time, setTime] = useState("");
  const [from, setFrom] = useState(today()), [to, setTo] = useState(today());
  const zone = useStore(state => shownZone(state.charts[chartId]));
  // The dates are read on the chart's clock.
  const at = (wall: string) => { const ms = Date.parse(`${wall}Z`); return ms - zoneOffset(zone, ms); };
  const close = () => openOverlay(panelId, null);
  const go = () => {
    if (tab === "date") focusChart(chartId, { at: at(`${date}T${time || "12:00"}:00`) });
    else focusChart(chartId, { from: at(`${from}T00:00:00`), to: at(`${to}T23:59:59`) });
    close();
  };
  const valid = tab === "date" ? Boolean(date) : Boolean(from && to && from <= to);
  return <TxDialog open onOpenChange={open => { if (!open) close(); }} title={t("Go to")}
    footer={<><TxButton size="sm" onClick={close}>{t("Cancel")}</TxButton><TxButton size="sm" variant="primary" disabled={!valid} onClick={go}>{t("Go to")}</TxButton></>}>
    <TxTabs value={tab} onChange={setTab} label={t("Go to")} options={[{ value: "date", label: t("Date") }, { value: "range", label: t("Custom range") }]} />
    <div className="chart-form">
      {tab === "date" ? <>
        <TxField label={t("Date")} layout="inline"><TxInput type="date" value={date} onChange={event => setDate(event.target.value)} /></TxField>
        <TxField label={t("Time")} layout="inline"><TxInput type="time" value={time} onChange={event => setTime(event.target.value)} /></TxField>
      </> : <>
        <TxField label={t("Start")} layout="inline"><TxInput type="date" value={from} onChange={event => setFrom(event.target.value)} /></TxField>
        <TxField label={t("End")} layout="inline"><TxInput type="date" value={to} onChange={event => setTo(event.target.value)} /></TxField>
      </>}
    </div>
  </TxDialog>;
}
