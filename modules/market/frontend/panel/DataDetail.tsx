import { t } from "../i18n";
import { useEffect, useMemo, useState } from "react";
import { intlLocale, TxButton, TxField, TxIcon, TxIconButton, TxInspector, TxPeriods, TxSection, TxSegmented, TxStatus, TxTable, TxTd, TxTh, type TxPeriodState } from "@talyx/sdk";
import { errorText, formatBytes, groupOf, panelCall, type Series } from "./data";
import { chartable, isStock, SeriesStatus, usePanelContext } from "./MarketPanel";

const months = Array.from({ length: 12 }, (_, month) => new Date(2000, month, 1).toLocaleDateString(intlLocale(), { month: "short" }));

interface DetailGap { from: string; to: string; fillable: boolean }
interface DetailAction { date: string; events: { kind: string; ratio: string | null; amount: string | null; currency: string | null }[]; factor: string }
interface Row { time: string; open: number; high: number; low: number; close: number; volume?: number }
interface Detail {
  source: string | null; rows: number; bytes: number; first: string | null; last: string | null; gaps: DetailGap[]; actions: DetailAction[];
  codes: { code: string; from: string | null; until: string | null; name: string | null }[]; latest: Row[];
}

interface Line { label: string; cells: TxPeriodState[]; titles: string[]; keys: string[] }

/** One line a year and one cell a month; over 16 years, one line a decade and one cell a year. Before the first row and after
    the last are empty, a cell holding a gap that can be filled is marked. */
function coverage(series: Series, gaps: DetailGap[]): Line[] {
  if (!series.from || !series.to) return [];
  const first = Number(series.from.slice(0, 4)), last = Number(series.to.slice(0, 4));
  const fillable = gaps.filter(gap => gap.fillable);
  const state = (key: string): [TxPeriodState, string] => {
    if (key < series.from.slice(0, key.length) || key > series.to.slice(0, key.length)) return ["none", t("no data")];
    const hit = fillable.filter(gap => gap.from.slice(0, key.length) <= key && gap.to.slice(0, key.length) >= key);
    return hit.length ? ["miss", t("missing {ranges}", { ranges: hit.map(gap => `${gap.from.slice(0, 10)} – ${gap.to.slice(0, 10)}`).join(", ") })] : ["ok", t("stored")];
  };
  if (last - first > 16) {
    const start = Math.floor(first / 10) * 10;
    return Array.from({ length: Math.floor(last / 10) - start / 10 + 1 }, (_, index) => {
      const decade = start + index * 10;
      const keys = Array.from({ length: 10 }, (_, year) => String(decade + year));
      const states = keys.map(state);
      return { label: t("{decade}s", { decade }), keys, cells: states.map(([cell]) => cell), titles: states.map(([, text], year) => `${keys[year]}: ${text}`) };
    }).reverse();
  }
  return Array.from({ length: last - first + 1 }, (_, index) => {
    const year = first + index;
    const keys = months.map((_, month) => `${year}-${String(month + 1).padStart(2, "0")}`);
    const states = keys.map(state);
    return { label: String(year), keys, cells: states.map(([cell]) => cell), titles: states.map(([, text], month) => `${months[month]} ${year}: ${text}`) };
  }).reverse();
}

/** Gaps; each opens in Chart at its first day. */
function Periods({ gaps, chart }: { gaps: DetailGap[]; chart?: (date: string) => void }) {
  return <TxTable density="compact">
    <thead><tr><TxTh>{t("From")}</TxTh><TxTh>{t("To")}</TxTh><TxTh>{t("Reason")}</TxTh>{chart && <TxTh />}</tr></thead>
    <tbody>{gaps.map(gap => <tr key={gap.from}>
      <TxTd className="tx-num">{gap.from.slice(0, 16)}</TxTd><TxTd className="tx-num">{gap.to.slice(0, 16)}</TxTd>
      <TxTd className="tx-cell-wrap tx-secondary">{gap.fillable ? t("Not fetched yet") : t("The source has no rows")}</TxTd>
      {chart && <TxTd className="market-open-cell"><TxIconButton variant="tool" size="sm" icon="chart" label={t("Open in Chart at {from}", { from: gap.from.slice(0, 10) })} onClick={() => chart(gap.from)} /></TxTd>}
    </tr>)}</tbody>
  </TxTable>;
}

type Adjust = "raw" | "split" | "split_dividend";
const price = (value: number) => value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 6 });

const eventText = (action: DetailAction) => action.events.map(event => event.kind === "split" || event.kind === "stock_dividend"
  ? t("Split {ratio}:1", { ratio: String(event.ratio) }) : event.kind === "cash_dividend" ? t("Dividend {amount}", { amount: `${event.amount}${event.currency ? ` ${event.currency}` : ""}` }) : event.kind).join(", ") || t("Adjustment");

export function DataDetail({ series, onClose, onStep, onFill, onChart }: { series: Series; onClose: () => void; onStep: (delta: number) => void;
  onFill: () => void; onChart: (date?: string) => void }) {
  const ctx = usePanelContext();
  const [picked, setPicked] = useState<string | null>(null);
  const [adjust, setAdjust] = useState<Adjust>("raw");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const end = picked === null ? null : picked.length === 4 ? `${Number(picked) + 1}-01-01` : nextMonth(picked);
  useEffect(() => {
    let live = true;
    setProblem(null);
    panelCall<Detail>(ctx, "detail", { instrumentId: series.instrumentId, capability: series.capability, timeframe: series.timeframe, importId: series.importId,
      adjustment: adjust === "raw" ? null : adjust, limit: 40, ...(end ? { before: String(Date.parse(`${end}T00:00:00Z`) * 1_000_000) } : {}) })
      .then(value => { if (live) setDetail(value); }).catch(error => { if (live) setProblem(errorText(error)); });
    return () => { live = false; };
  }, [ctx, series.id, series.instrumentId, series.capability, series.timeframe, series.importId, adjust, end]);
  const gaps = detail?.gaps ?? [];
  const lines = useMemo(() => coverage(series, gaps), [series, gaps]);
  const fillable = gaps.filter(gap => gap.fillable);
  const market = groupOf(series.market, null).label;
  const imported = series.group === "imported";
  const stock = isStock(series) && !imported;

  return <TxInspector header={<>
    <span className="market-detail-title"><span className="tx-cell-name">{series.code}</span><span className="tx-secondary market-clip">{series.kind}</span></span>
    <span className="tx-spacer" />
    {chartable(series) && <TxButton variant="tool" size="sm" onClick={() => onChart()}><TxIcon name="chart" />{t("Open in Chart")}</TxButton>}
    <TxIconButton size="sm" icon="arrow-up" label={t("Previous series")} onClick={() => onStep(-1)} />
    <TxIconButton size="sm" icon="arrow-down" label={t("Next series")} onClick={() => onStep(1)} />
    <TxIconButton size="sm" icon="close" label={t("Close detail")} onClick={onClose} />
  </>}>
    <TxSection>
      {series.name && <TxField label={t("Name")}><span>{series.name}</span></TxField>}
      <TxField label={imported ? t("Imported from") : t("Source")}><span className={imported ? "tx-num" : undefined}>{imported ? series.source : `${series.source} · ${market}`}</span></TxField>
      <TxField label={t("Range")}><span className="tx-num">{series.from} → {series.to}</span></TxField>
      <TxField label={t("Rows")}><span className="tx-num">{series.rows.toLocaleString("en-US")} · {formatBytes(series.bytes)}</span></TxField>
      {stock && <TxField label={t("Stored")}><span>{detail?.actions.length ? t("Unadjusted, {actionsCount} corporate actions", { actionsCount: detail.actions.length }) : t("Unadjusted, no corporate actions")}</span></TxField>}
      {series.status !== "current" && <TxField label={t("Status")}><span className="market-fact-status"><SeriesStatus series={series} />{series.statusText && <span className="tx-secondary">{series.statusText}</span>}</span></TxField>}
      {problem && <TxStatus tone="failed" label={problem} />}
    </TxSection>
    <TxSection title={t("Coverage")} actions={picked && <TxButton size="sm" variant="quiet" onClick={() => setPicked(null)}>{t("Latest rows")}</TxButton>}>
      <div className="market-coverage">{lines.map(line => <div key={line.label} className="market-coverage-year">
        <span className="tx-num tx-muted market-coverage-label">{line.label}</span>
        <TxPeriods large items={line.cells} titles={line.titles} selected={picked !== null && line.keys.includes(picked) ? line.keys.indexOf(picked) : undefined}
          onSelect={index => { if (line.cells[index] !== "none") setPicked(line.keys[index]!); }} />
      </div>)}</div>
    </TxSection>
    <TxSection title={t("Gaps · {fillableCount}", { fillableCount: fillable.length })} actions={fillable.length > 0 && !imported && <TxButton size="sm" onClick={onFill}><TxIcon name="refresh" />{(fillable.length === 1 ? t("Fill gap") : t("Fill {fillableCount} gaps", { fillableCount: fillable.length }))}</TxButton>}>
      {fillable.length > 0 ? <Periods gaps={fillable} chart={chartable(series) ? onChart : undefined} /> : <span className="tx-secondary">{t("None")}</span>}
    </TxSection>
    {detail && detail.codes.length > 1 && <TxSection title={t("Codes over time")}>
      <TxTable density="compact">
        <thead><tr><TxTh>{t("From")}</TxTh><TxTh>{t("To")}</TxTh><TxTh>{t("Code")}</TxTh><TxTh>{t("Name")}</TxTh></tr></thead>
        <tbody>{detail.codes.map(item => <tr key={`${item.code}:${item.from}`}>
          <TxTd className="tx-num">{item.from ?? ""}</TxTd><TxTd className="tx-num">{item.until ?? t("now")}</TxTd><TxTd className="tx-cell-name">{item.code}</TxTd><TxTd className="tx-cell-wrap">{item.name ?? ""}</TxTd>
        </tr>)}</tbody>
      </TxTable>
    </TxSection>}
    {stock && <TxSection title={t("Corporate actions · {actionsCount}", { actionsCount: detail?.actions.length ?? 0 })}>
      {detail?.actions.length ? <TxTable density="compact">
        <thead><tr><TxTh>{t("Ex-date")}</TxTh><TxTh>{t("Event")}</TxTh><TxTh numeric>{t("Factor")}</TxTh></tr></thead>
        <tbody>{[...detail.actions].reverse().map(action => <tr key={action.date}><TxTd className="tx-num">{action.date}</TxTd><TxTd>{eventText(action)}</TxTd><TxTd numeric>{action.factor}</TxTd></tr>)}</tbody>
      </TxTable> : <span className="tx-secondary">{t("None stored")}</span>}
    </TxSection>}
    <TxSection title={picked ? t("Rows to {picked}", { picked }) : t("Latest rows")} actions={stock && <TxSegmented size="sm" value={adjust} onChange={setAdjust} label={t("Adjustment")} options={[
      { value: "raw", label: t("Raw") }, { value: "split", label: t("Splits") }, { value: "split_dividend", label: t("Splits + dividends") },
    ]} />}>
      <TxTable density="compact">
        <thead><tr><TxTh>{t("Time")}</TxTh><TxTh numeric>{t.c("price", "Open")}</TxTh><TxTh numeric>{t.c("price", "High")}</TxTh><TxTh numeric>{t.c("price", "Low")}</TxTh><TxTh numeric>{t.c("price", "Close")}</TxTh><TxTh numeric>{t("Volume")}</TxTh></tr></thead>
        <tbody>{[...(detail?.latest ?? [])].reverse().map(row => <tr key={row.time}>
          <TxTd className="tx-num tx-secondary">{row.time.replace("T", " ").replace("Z", "").slice(0, 16)}</TxTd><TxTd numeric>{price(Number(row.open))}</TxTd><TxTd numeric>{price(Number(row.high))}</TxTd>
          <TxTd numeric>{price(Number(row.low))}</TxTd><TxTd numeric>{price(Number(row.close))}</TxTd><TxTd numeric>{row.volume === undefined ? "" : Number(row.volume).toLocaleString("en-US")}</TxTd>
        </tr>)}</tbody>
      </TxTable>
    </TxSection>
  </TxInspector>;
}

function nextMonth(key: string): string {
  const [year, month] = key.split("-").map(Number) as [number, number];
  return month === 12 ? `${year + 1}-01-01` : `${year}-${String(month + 1).padStart(2, "0")}-01`;
}
