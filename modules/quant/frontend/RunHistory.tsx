import { t } from "./i18n";
import { useEffect, useState } from "react";
import { TxAlert, TxButton, TxCheckbox, TxDialog, TxIcon, TxIconButton, TxInput, TxMenu, TxTable, TxTd, TxTh } from "@talyx/sdk";
import { formatWhen } from "./format";
import { formatNumber } from "./format";
import { indexText, plain, percent, runStatus, type Api, type Comparison, type DataLabel, type Kind, type Parameter, type ParamValue, type RunPage, type RunSummary } from "./objects";

type Metric = {key: string; label: string; show(value: number): string; sign?: boolean};
const METRICS: Metric[] = [
  {key: "totalReturn", label: t.c("performance", "Return"), show: value => percent(value), sign: true},
  {key: "maximumDrawdown", label: t("Max DD"), show: value => percent(value)},
  {key: "dailySharpe", label: t("Sharpe"), show: value => plain(value, 2)},
  {key: "winRate", label: t("Win rate"), show: value => percent(value, 1).replace("+", "")},
  {key: "orders", label: t("Orders"), show: value => plain(value, 0)},
  {key: "positions", label: t("Positions"), show: value => plain(value, 0)},
  {key: "finalEquity", label: t("Final equity"), show: value => value.toLocaleString("en-US", {maximumFractionDigits: 2})},
];
/** The headline numbers research reports name (q.report.factor, model, allocation); anything else is shown by its own name. */
const RESEARCH: Metric[] = [
  {key: "icMean", label: t("Mean IC"), show: value => formatNumber(value, 3), sign: true},
  {key: "icIr", label: "IC IR", show: value => formatNumber(value, 3), sign: true},
  {key: "spreadBps", label: t("Top − bottom"), show: value => t("{value} bps", { value: formatNumber(value, 3) }), sign: true},
  {key: "observations", label: t("Observations"), show: value => formatNumber(value)},
];
const COLUMNS: Record<Kind, string[]> = {strategy: ["totalReturn", "maximumDrawdown", "dailySharpe", "orders"], factor: ["icMean", "icIr", "spreadBps"],
  model: [], allocation: [], notebook: [], library: []};
const metric = (key: string): Metric => [...METRICS, ...RESEARCH].find(item => item.key === key)
  ?? {key, label: key.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/_/g, " ").replace(/^./, first => first.toUpperCase()), show: value => formatNumber(value)};
/** The kind's own columns; for models and allocations the first numbers the report names. */
const columnsFor = (kind: Kind, page: RunPage) => COLUMNS[kind].length ? COLUMNS[kind].map(metric)
  : [...new Set(page.items.flatMap(item => Object.keys(item.metrics).filter(key => typeof item.metrics[key] === "number")))].slice(0, 3).map(metric);
const runLabel = (run: RunSummary) => run.runKind === "publish" ? t("Publish · {status}", { status: runStatus(run) }) : run.runKind === "sweep" ? t("Sweep · {status}", { status: runStatus(run) })
  : run.mode === "quick" ? t("Quick check · {status}", { status: runStatus(run) }) : runStatus(run);
const valueText = (value: ParamValue | null) => value === null ? "—" : typeof value === "number" ? plain(value) : String(value);
const symbolsText = (data: DataLabel) => data.list !== null ? `${data.list} (${data.symbols.length})` : data.index !== null ? indexText(data.index) : data.universe === "code" ? t("Rule in the code")
  : data.symbols.length > 3 ? `${data.symbols.slice(0, 2).join(", ")} +${data.symbols.length - 2}` : data.symbols.join(", ");
export const dataText = (data: DataLabel) => [symbolsText(data), data.timeframe, data.since && data.until ? `${data.since} – ${data.until}` : null].filter(Boolean).join(" · ");

/** Newest first, numbered from the first run; a run's number stays the same as more runs are added. */
export function runNumbers(page: RunPage): Map<string, number> {
  return new Map(page.items.map((item, index) => [item.id, page.total - page.offset - index]));
}

export function RunHistory({api, page, numbers, parameters, kind, shownId, onShow, onMore, onApplied}: {
  api: Api; page: RunPage; numbers: Map<string, number>; parameters: Parameter[]; kind: Kind; shownId: string | null;
  onShow(runId: string): void; onMore(): void; onApplied(run: RunSummary): Promise<void>;
}) {
  const [picked, setPicked] = useState<string[]>([]);
  const [comparing, setComparing] = useState<[string, string] | null>(null);
  const [applying, setApplying] = useState<RunSummary | null>(null);
  const [noting, setNoting] = useState<RunSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const label = (name: string) => parameters.find(item => item.name === name)?.label ?? name;
  const columns = columnsFor(kind, page);
  // At half width only the first numbers stay: three for a backtest, two for research.
  const narrow = kind === "strategy" ? 3 : 2;
  const annotate = (run: RunSummary, input: {note?: string; starred?: boolean}) => void api.annotate(run.id, input).catch(reason => setError(String(reason)));
  if (comparing) return <RunComparison api={api} ids={comparing} numbers={numbers} label={label} kind={kind} onBack={() => setComparing(null)} />;
  return <div className="qx-runs">
    <div className="qx-runs-bar">
      <span className="qx-muted">{page.total === 1 ? t("1 run") : t("{total} runs", { total: page.total })}</span>
      <span className="tx-spacer" />
      {picked.length > 0 && <span className="qx-muted">{picked.length === 2 ? t("Two runs selected") : t("Select one more run to compare")}</span>}
      {picked.length === 2 && <TxButton size="sm" onClick={() => setComparing([picked[0], picked[1]])}><TxIcon name="layers" />{t("Compare")}</TxButton>}
    </div>
    {error && <TxAlert title={t("The run history could not be changed")}>{error}</TxAlert>}
    <TxTable className="qx-runs-table">
      <thead><tr><TxTh aria-label={t("Select")} /><TxTh aria-label={t("Star")} /><TxTh>{t("Time")}</TxTh><TxTh>{t.c("run record", "Run")}</TxTh><TxTh>{t("Parameters")}</TxTh><TxTh className="qx-wide">{t("Data")}</TxTh>
        {columns.map((item, index) => <TxTh key={item.key} align="right" className={index >= narrow ? "qx-wide" : undefined}>{item.label}</TxTh>)}<TxTh className="qx-wide">{t("Note")}</TxTh><TxTh aria-label={t("Actions")} /></tr></thead>
      <tbody>{page.items.map(run => <tr key={run.id} aria-selected={run.id === shownId} className="qx-click" onClick={() => onShow(run.id)}>
        <TxTd onClick={event => event.stopPropagation()}><TxCheckbox name={t("Select run {number}", { number: String(numbers.get(run.id)) })} checked={picked.includes(run.id)}
          onCheckedChange={checked => setPicked(current => checked ? [...current.filter(id => id !== run.id), run.id].slice(-2) : current.filter(id => id !== run.id))} /></TxTd>
        <TxTd onClick={event => event.stopPropagation()}><TxIconButton size="sm" className="qx-star" data-on={run.starred} label={run.starred ? t("Unstar") : t("Star")} icon="star"
          onClick={() => annotate(run, {starred: !run.starred})} /></TxTd>
        <TxTd className="tx-num qx-nowrap">{formatWhen(run.createdAt)}</TxTd>
        <TxTd className="qx-nowrap" title={run.runKind === "publish" ? t("Published as {name}", { name: run.publish!.name }) : undefined}><span className="tx-num">{numbers.get(run.id)}</span> · <span data-state={run.state}>{runLabel(run)}</span></TxTd>
        {run.sweep !== null ? <TxTd className="qx-params-cell">{t("{parameters} · {total} backtests", { parameters: run.sweep.parameters.map(label).join(", "), total: run.sweep.total })}</TxTd>
          : <TxTd className="qx-params-cell" title={Object.entries(run.params).map(([name, value]) => `${label(name)} ${valueText(value)}`).join(" · ")}>
            {Object.entries(run.params).map(([name, value], index) => <span key={name} data-changed={name in run.parameterDiff}>{index ? " · " : ""}{label(name)} {valueText(value)}</span>)}
          </TxTd>}
        <TxTd className="qx-wide qx-nowrap">{dataText(run.data)}</TxTd>
        {columns.map((item, index) => <TxTd key={item.key} align="right" className={index >= narrow ? "tx-num qx-wide" : "tx-num"} data-sign={item.sign && typeof run.metrics[item.key] === "number" ? Math.sign(run.metrics[item.key]) : undefined}>
          {typeof run.metrics[item.key] === "number" ? item.show(run.metrics[item.key]) : "—"}</TxTd>)}
        <TxTd className="qx-wide" onClick={event => event.stopPropagation()}><Note value={run.note} onSave={note => annotate(run, {note})} /></TxTd>
        <TxTd onClick={event => event.stopPropagation()}><TxMenu label={t("Run {numbers} actions", { numbers: numbers.get(run.id) ?? "" })} align="end" items={[
          {id: "show", label: t("Show report"), icon: "eye", onSelect: () => onShow(run.id)},
          {id: "note", label: run.note ? t("Edit note…") : t("Add note…"), icon: "edit", detail: run.note || undefined, onSelect: () => setNoting(run)},
          // a run that stopped before its code was kept has no version to go back to
          ...(run.codeDigest !== null ? [{id: "apply", label: t("Use this version"), icon: "undo" as const, onSelect: () => setApplying(run)}] : []),
        ]} trigger={<TxIconButton size="sm" label={t("Run {numbers} actions", { numbers: numbers.get(run.id) ?? "" })} icon="more" />} /></TxTd>
      </tr>)}</tbody>
    </TxTable>
    {page.nextOffset !== null && <div className="qx-runs-more"><TxButton size="sm" variant="quiet" onClick={onMore}>{t("Show older runs")}</TxButton></div>}
    {noting !== null && <NoteDialog run={noting} number={numbers.get(noting.id)} onClose={() => setNoting(null)} onSave={note => {annotate(noting, {note}); setNoting(null);}} />}
    <TxDialog open={applying !== null} onOpenChange={open => {if (!open) setApplying(null);}} title={applying ? t("Use run {numbers}'s version?", { numbers: numbers.get(applying.id) ?? "" }) : ""}
      footer={<><TxButton onClick={() => setApplying(null)}>{t("Cancel")}</TxButton><TxButton variant="primary" onClick={() => {
        const run = applying!; setApplying(null); void onApplied(run).catch(reason => setError(String(reason)));
      }}>{t("Use this version")}</TxButton></>}>
      <p>{t("The code and parameter values go back to what run {numbers} used. The current code stays in Developer's file history.", { numbers: applying ? numbers.get(applying.id) ?? "" : "" })}</p>
    </TxDialog>
  </div>;
}

function NoteDialog({run, number, onClose, onSave}: {run: RunSummary; number: number | undefined; onClose(): void; onSave(note: string): void}) {
  const [text, setText] = useState(run.note);
  return <TxDialog open onOpenChange={open => {if (!open) onClose();}} title={t("Note on run {number}", { number: number ?? "" })}
    footer={<><TxButton onClick={onClose}>{t("Cancel")}</TxButton><TxButton variant="primary" onClick={() => onSave(text.trim())}>{t("Save")}</TxButton></>}>
    <TxInput autoFocus aria-label={t("Note")} value={text} onChange={event => setText(event.target.value)} onKeyDown={event => {if (event.key === "Enter") onSave(text.trim());}} />
  </TxDialog>;
}

function Note({value, onSave}: {value: string; onSave(value: string): void}) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  const commit = () => {if (text.trim() !== value) onSave(text.trim());};
  return <input className="qx-note" value={text} placeholder={t("Add note")} aria-label={t("Note")} onChange={event => setText(event.target.value)} onBlur={commit}
    onKeyDown={event => {if (event.key === "Enter") event.currentTarget.blur(); if (event.key === "Escape") setText(value);}} />;
}

/** Two runs side by side: results, parameters, data and whether the code changed. The older run is the baseline. */
function RunComparison({api, ids, numbers, label, kind, onBack}: {api: Api; ids: [string, string]; numbers: Map<string, number>; label(name: string): string; kind: Kind; onBack(): void}) {
  const [older, newer] = [...ids].sort((a, b) => (numbers.get(a) ?? 0) - (numbers.get(b) ?? 0));
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    void api.compare(newer, older).then(value => {if (live) setComparison(value);}, reason => {if (live) setError(String(reason));});
    return () => {live = false;};
  }, [newer, older]);
  const a = numbers.get(older), b = numbers.get(newer);
  const parameters = comparison ? [...new Set([...Object.keys(comparison.previous.params), ...Object.keys(comparison.current.params)])] : [];
  const known = kind === "strategy" ? METRICS.map(item => item.key) : null;
  const keys = comparison ? (known ?? Object.keys(comparison.metrics)).filter(key => key in comparison.metrics && (comparison.metrics[key].current !== null || comparison.metrics[key].previous !== null)) : [];
  return <div className="qx-compare">
    <div className="qx-runs-bar"><TxButton size="sm" variant="quiet" onClick={onBack}><TxIcon name="undo" />{t("Runs")}</TxButton><span className="qx-muted">{t("Run {a} vs run {b}", { a: a ?? "", b: b ?? "" })}</span></div>
    {error && <TxAlert title={t("The runs could not be compared")}>{error}</TxAlert>}
    {comparison && <TxTable className="qx-compare-table">
      <thead><tr><TxTh /><TxTh align="right">{t("Run {a}", { a: a ?? "" })}</TxTh><TxTh align="right">{t("Run {b}", { b: b ?? "" })}</TxTh><TxTh align="right">{t("Change")}</TxTh></tr></thead>
      <tbody>
        <tr className="qx-compare-group"><TxTd colSpan={4}>{t("Results")}</TxTd></tr>
        {keys.map(key => {const item = metric(key), row = comparison.metrics[key]; return <tr key={key}>
          <TxTd>{item.label}</TxTd>
          <TxTd align="right" className="tx-num">{row.previous === null ? "—" : item.show(row.previous)}</TxTd>
          <TxTd align="right" className="tx-num">{row.current === null ? "—" : item.show(row.current)}</TxTd>
          <TxTd align="right" className="tx-num" data-sign={row.difference === null ? undefined : Math.sign(row.difference)}>{row.difference === null ? "—"
            : key === "totalReturn" || key === "maximumDrawdown" || key === "winRate" ? (row.difference > 0 ? t("+{difference} pt", { difference: plain(row.difference * 100, 2) }) : t("{difference} pt", { difference: plain(row.difference * 100, 2) })) : `${row.difference > 0 ? "+" : ""}${item.show(row.difference)}`}</TxTd>
        </tr>;})}
        <tr className="qx-compare-group"><TxTd colSpan={4}>{t("Parameters")}</TxTd></tr>
        {parameters.map(name => <tr key={name} data-changed={name in comparison.parameterDiff}>
          <TxTd>{label(name)}</TxTd>
          <TxTd align="right" className="tx-num">{valueText(name in comparison.previous.params ? comparison.previous.params[name] : null)}</TxTd>
          <TxTd align="right" className="tx-num">{valueText(name in comparison.current.params ? comparison.current.params[name] : null)}</TxTd>
          <TxTd align="right">{name in comparison.parameterDiff ? t("Changed") : ""}</TxTd>
        </tr>)}
        <tr className="qx-compare-group"><TxTd colSpan={4}>{t("Data and code")}</TxTd></tr>
        <tr data-changed={comparison.dataChanged}><TxTd>{t("Data")}</TxTd><TxTd align="right">{dataText(comparison.dataDiff.previous)}</TxTd><TxTd align="right">{dataText(comparison.dataDiff.current)}</TxTd><TxTd align="right">{comparison.dataChanged ? t("Changed") : t("Same")}</TxTd></tr>
        <tr data-changed={comparison.codeChanged}><TxTd>{t("Code")}</TxTd><TxTd /><TxTd /><TxTd align="right">{comparison.codeChanged ? t("Changed") : t("Same")}</TxTd></tr>
      </tbody>
    </TxTable>}
  </div>;
}
