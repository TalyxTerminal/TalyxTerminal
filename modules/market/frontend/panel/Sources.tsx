import { Fragment, useMemo, useState } from "react";
import { TxButton, TxField, TxIcon, TxIconButton, TxInput, TxInspector, TxSection, TxSelect, TxSplit, TxStatus, TxSwitch, TxTable, TxTag, TxTd, TxTh, TxToolbar, TxTooltip } from "@talyx/sdk";
import {
  errorText, formatBytes, groupOf, kindLabel, libraryRows, listOf, marketCall, panelCall, testResultOf,
  type CoverageRow, type Library, type SelectionRow, type Source, type SourceTest, type TestResult,
} from "./data";
import { usePanelContext } from "./MarketPanel";

/** A failed test is orange only when it needs handling; a rate limit waits, and what is not set up is just off. */
const failTone = (result: TestResult) => result.tone === "watch" ? "review" : result.tone === "off" ? "off" : "failed";

/** One status a row: while testing, the test; once tested, the round trip or why it did not get through (detail on hover);
    never tested, the connection state. */
export function StatusCell({ source, result, testing }: { source: Source; result?: TestResult; testing: boolean }) {
  if (testing) return <TxStatus tone="busy" label="Testing" />;
  if (!result) return !source.enabled ? <TxStatus tone="off" label="Off" /> : <TxStatus tone={tone[source.status]} label={source.statusText} />;
  if (result.ms !== undefined && !result.fail) return <TxStatus tone="done" label={`${result.ms} ms`} className="market-latency" />;
  const status = <TxStatus tone={failTone(result)} label={result.fail ?? "Failed"} />;
  return result.detail ? <TxTooltip label={result.detail}>{status}</TxTooltip> : status;
}

interface Held { label: string; count: number; bytes: number }

const tone = { ok: "done", cooling: "review", unreachable: "failed", "needs-key": "off", "key-rejected": "failed", "app-off": "off", ended: "off" } as const;
const keyText = { none: "—", optional: "Optional", required: "Required", local: "Local app" } as const;

type Band = "attention" | "used" | "setup" | "ended" | "off";
export const bands: { id: Band; label: string }[] = [
  { id: "attention", label: "Needs attention" },
  { id: "used", label: "In use" },
  { id: "setup", label: "Not set up" },
  { id: "ended", label: "Subscription ended" },
  { id: "off", label: "Off" },
];

export function bandOf(source: Source): Band {
  if (!source.enabled) return "off";
  if (source.status === "ended") return "ended";
  if (source.status === "unreachable" || source.status === "key-rejected") return "attention";
  if (source.status === "needs-key" || source.status === "app-off") return "setup";
  return "used";
}

/** The sources by band, in the bands' order, empty bands left out: the panel's list and the settings page alike. */
export function groupsOf(sources: Source[]): { id: Band; label: string; sources: Source[] }[] {
  return bands.map(band => ({ ...band, sources: sources.filter(source => bandOf(source) === band.id) })).filter(band => band.sources.length > 0);
}

const marketText = (row: CoverageRow) => [...(row.scope.markets ?? []), ...(row.scope.venues ?? []).slice(0, 4)].map(item => groupOf(item, null).label).join(", ")
  + ((row.scope.venues?.length ?? 0) > 4 ? ` +${row.scope.venues!.length - 4}` : "");
/** A declared depth: a start date, or an ISO duration back from now (P90D, PT5000M) in days or hours. */
export const depthText = (depth: string | undefined) => {
  if (!depth) return "";
  if (!depth.startsWith("P")) return `From ${depth.slice(0, 4)}`;
  const [, years, days, hours, minutes] = /^P(?:(\d+)Y)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$/.exec(depth) ?? [];
  const total = Number(years ?? 0) * 525_600 + Number(days ?? 0) * 1440 + Number(hours ?? 0) * 60 + Number(minutes ?? 0);
  return total >= 2880 ? `${Math.round(total / 1440)} days back` : `${Math.round(total / 60)} hours back`;
};
const kindText = (row: CoverageRow) => row.kind === "bars"
  ? (row.periods ?? []).map(period => kindLabel("ohlcv", period.timeframe)).join(", ") || "Bars" : row.kind.replaceAll("_", " ");
const usedFor = (items: SelectionRow[]) => items.map(item => `${item.market} ${item.timeframes ?? item.kind}`).join(" · ");

export function Sources({ sources, selection, library, onChanged }: { sources: Source[]; selection: SelectionRow[]; library: Library | null; onChanged: () => Promise<void> }) {
  const ctx = usePanelContext();
  const [local, held] = useMemo(() => {
    const bytes: Record<string, number> = {}, sets: Record<string, Held[]> = {};
    for (const row of library ? libraryRows(library) : []) {
      if (!row.source) continue;
      bytes[row.source] = (bytes[row.source] ?? 0) + row.bytes;
      const list = sets[row.source] ??= [];
      const label = `${groupOf(row.venue, null).label} · ${kindLabel(row.kind, row.timeframe)}`;
      const item = list.find(other => other.label === label) ?? list[list.push({ label, count: 0, bytes: 0 }) - 1]!;
      item.count += 1; item.bytes += row.bytes;
    }
    return [bytes, sets];
  }, [library]);
  const [open, setOpen] = useState<string>(() => sources.find(source => bandOf(source) === "attention")?.id ?? sources[0]?.id ?? "");
  const [width, setWidth] = useState(452);
  const [results, setResults] = useState<Record<string, TestResult>>({});
  const [testing, setTesting] = useState<Set<string>>(new Set());
  const [testedAt, setTestedAt] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const current = sources.find(source => source.id === open);
  const uses = (id: string) => selection.filter(item => item.source?.sourceId === id);

  const finish = (ids: string[], tests: SourceTest[]) => {
    setResults(previous => ({ ...previous, ...Object.fromEntries(tests.map(test => [test.source, testResultOf(test)])) }));
    setTesting(previous => { const next = new Set(previous); ids.forEach(id => next.delete(id)); return next; });
  };
  const test = (ids: string[]) => {
    const all = ids.length > 1;
    setTesting(previous => new Set([...previous, ...ids]));
    setProblem(null);
    void marketCall(ctx, "write_source", { operation: all ? { op: "test_all" } : { op: "test", sources: listOf<[string, ...string[]]>(ids, 1, Infinity) } })
      .then(result => { finish(ids, result.tests); if (all) setTestedAt(new Date().toTimeString().slice(0, 5)); })
      .catch(error => { finish(ids, []); setProblem(errorText(error)); })
      .finally(() => void onChanged());
  };
  const use = (id: string, on: boolean) => {
    setProblem(null);
    void marketCall(ctx, "write_source", { operation: { op: on ? "enable" : "disable", source: id } })
      .catch(error => setProblem(errorText(error))).finally(() => void onChanged());
  };
  const configure = async (id: string, settings: Record<string, unknown>) => {
    setProblem(null);
    try { await panelCall(ctx, "configure", { source: id, settings }); test([id]); } catch (error) { setProblem(errorText(error)); }
    await onChanged();
  };
  const reconnect = (id: string) => void marketCall(ctx, "write_source", { operation: { op: "reconnect", source: id } })
    .catch(error => setProblem(errorText(error))).finally(() => void onChanged());
  const remove = (id: string) => void marketCall(ctx, "write_data", { operation: { op: "delete", sourceId: id } })
    .catch(error => setProblem(errorText(error))).finally(() => void onChanged());
  const answered = sources.filter(source => results[source.id]?.ms !== undefined).length;

  return <div className="market-sources-view">
    <TxSplit sidePosition="end" sideLabel="Source" width={width} onWidth={setWidth} min={400} max={720} mainMin={560} sideHidden={!current}
      side={current && <SourceDetail key={current.id} source={current} bytes={local[current.id] ?? 0} held={held[current.id] ?? []} uses={uses(current.id)}
        result={results[current.id]} testing={testing.has(current.id)} onTest={() => test([current.id])} onUse={on => use(current.id, on)}
        onConfigure={settings => configure(current.id, settings)} onReconnect={() => reconnect(current.id)} onDelete={() => remove(current.id)} onClose={() => setOpen("")} />}>
      <TxToolbar label="Sources" density="compact">
        <TxButton variant="tool" loading={testing.size > 1} onClick={() => test(sources.map(source => source.id))}><TxIcon name="refresh" />Test all</TxButton>
        {testedAt && testing.size === 0 && <span className="tx-toolbar-text tx-num">{answered} of {sources.length} answered · {testedAt}</span>}
        {problem && <TxStatus tone="failed" label={problem} />}
      </TxToolbar>
      <div className="market-list">
        <TxTable className="market-sources" stickyHead density="compact">
          <colgroup><col className="market-col-use" /><col className="market-col-name" /><col className="market-col-state" /><col /><col className="market-col-local" /><col className="market-col-key" /></colgroup>
          <thead><tr><TxTh>Use</TxTh><TxTh>Source</TxTh><TxTh>Status</TxTh><TxTh>Used for</TxTh><TxTh numeric>Local data</TxTh><TxTh>Key</TxTh></tr></thead>
          <tbody>{groupsOf(sources).map(({ sources: rows, ...band }) => {
            return <Fragment key={band.id}>
              <tr className="market-group"><TxTd colSpan={6}>{band.label}<span className="tx-muted tx-num market-group-count">{rows.length}</span></TxTd></tr>
              {rows.map(source => {
                const bytes = local[source.id] ?? 0;
                return <tr key={source.id} className="market-source-row" data-off={!source.enabled} data-selected={open === source.id} onClick={() => setOpen(source.id)}>
                  <TxTd onClick={event => event.stopPropagation()}><TxSwitch hideLabel label={`Use ${source.name}`} checked={source.enabled} onCheckedChange={on => use(source.id, on)} /></TxTd>
                  <TxTd><span className="market-source-name"><span className="tx-cell-name market-clip">{source.name}</span>{source.paid && <TxTag tone="muted">Paid</TxTag>}</span></TxTd>
                  <TxTd><StatusCell source={source} result={results[source.id]} testing={testing.has(source.id)} /></TxTd>
                  <TxTd className="market-clip tx-secondary" title={usedFor(uses(source.id))}>{!source.enabled || source.status === "ended" ? "" : usedFor(uses(source.id))}</TxTd>
                  <TxTd numeric className="tx-secondary">{bytes ? formatBytes(bytes) : <span className="tx-muted">—</span>}</TxTd>
                  <TxTd className="tx-secondary">{source.keySet ? "Set" : keyText[source.key]}</TxTd>
                </tr>;
              })}
            </Fragment>;
          })}</tbody>
        </TxTable>
      </div>
    </TxSplit>
  </div>;
}

/** The detail's test answer: works with the round trip, or the reason and its detail. */
function TestLine({ result, testing }: { result?: TestResult; testing: boolean }) {
  if (testing || !result) return <div className="market-test"><TxStatus tone="busy" label="Testing" /></div>;
  if (result.ms === undefined || result.fail) return <div className="market-test"><TxStatus tone={failTone(result)} label={result.detail ? `${result.fail} · ${result.detail}` : result.fail ?? "Failed"} /></div>;
  return <div className="market-test">
    <span className="market-fact-status"><TxStatus tone="done" label="Works" /><span className="tx-num market-latency">{result.ms} ms</span></span>
  </div>;
}

/** A source's declared settings, its key first (typed only, never shown back), saved together and then tested. */
export function SourceFields({ source, result, testing, onSave }: {
  source: Source; result?: TestResult; testing: boolean; onSave: (settings: Record<string, unknown>) => Promise<void>;
}) {
  const [draft, setDraft] = useState<Record<string, string>>({});
  const fields = source.settings.schema;
  const secrets = fields.filter(item => item.secret), plain = fields.filter(item => !item.secret);
  const keyed = source.key === "optional" || source.key === "required";
  const save = () => {
    const values: Record<string, unknown> = {};
    for (const item of fields) {
      const text = draft[item.name];
      if (text === undefined || text === "") continue;
      values[item.name] = item.type === "integer" ? Number(text) : text;
    }
    void onSave(values).then(() => setDraft({}));
  };
  return <>
    {secrets.map(item => <div key={item.name} className="market-key-row">
      <TxInput type="password" aria-label={item.label} value={draft[item.name] ?? ""} placeholder={source.keySet ? "•••••••••••••••• (set)" : `Paste ${item.label.toLowerCase()}`}
        autoComplete="off" onChange={event => setDraft({ ...draft, [item.name]: event.target.value })} />
    </div>)}
    {plain.map(item => <TxField key={item.name} label={item.label}>{item.choices
      ? <TxSelect value={draft[item.name] ?? String(source.settings.values[item.name] ?? "")} onChange={event => setDraft({ ...draft, [item.name]: event.target.value })}>
        {item.choices.map(choice => <option key={choice} value={choice}>{choice}</option>)}</TxSelect>
      : <TxInput numeric={item.type === "integer"} value={draft[item.name] ?? String(source.settings.values[item.name] ?? "")}
        onChange={event => setDraft({ ...draft, [item.name]: event.target.value })} />}</TxField>)}
    <div><TxButton size="sm" variant={source.keySet || !keyed ? "default" : "primary"} loading={testing} onClick={save}>Save and test</TxButton></div>
    {(result || testing) && <TestLine result={result} testing={testing} />}
  </>;
}

function SourceDetail({ source, bytes, held, uses, result, testing, onTest, onUse, onConfigure, onReconnect, onDelete, onClose }: {
  source: Source; bytes: number; held: Held[]; uses: SelectionRow[]; result?: TestResult; testing: boolean; onTest: () => void; onUse: (on: boolean) => void;
  onConfigure: (settings: Record<string, unknown>) => Promise<void>; onReconnect: () => void; onDelete: () => void; onClose: () => void;
}) {
  const fields = source.settings.schema;
  const keyed = source.key === "optional" || source.key === "required";
  const live = source.coverage.some(row => row.live !== "none");
  const lastResult = result ?? (source.lastTest ? testResultOf(source.lastTest) : undefined);
  return <TxInspector header={<>
    <span className="tx-cell-name">{source.name}</span>{source.paid && <TxTag tone="muted">Paid</TxTag>}
    <span className="tx-spacer" />
    <TxSwitch label="Use" checked={source.enabled} onCheckedChange={onUse} />
    <TxIconButton size="sm" icon="close" label="Close source" onClick={onClose} />
  </>}>
    <TxSection>
      <span className="market-source-status">{source.enabled ? <TxStatus tone={tone[source.status]} label={source.statusText} /> : <TxStatus tone="off" label="Off" />}
        <span className="tx-spacer" />
        {source.status === "unreachable" && <TxButton size="sm" variant="quiet" onClick={onReconnect}><TxIcon name="refresh" />Reconnect</TxButton>}
        {!fields.length && <TxButton size="sm" loading={testing} onClick={onTest}>Test</TxButton>}</span>
      {!fields.length && (lastResult || testing) && <TestLine result={lastResult} testing={testing} />}
    </TxSection>
    {fields.length > 0 && <TxSection title={source.key === "local" ? "Connection" : keyed ? "Key" : "Settings"}>
      <SourceFields source={source} result={lastResult} testing={testing} onSave={onConfigure} />
    </TxSection>}
    {uses.length > 0 && source.enabled && <TxSection title="Market uses it for">
      <TxTable density="compact">
        <tbody>{uses.map(item => <tr key={item.market + item.kind + (item.timeframes ?? "")}>
          <TxTd className="tx-cell-name">{item.market} · {item.timeframes ?? item.kind}</TxTd><TxTd className="tx-secondary tx-cell-wrap">{item.reason}</TxTd>
        </tr>)}</tbody>
      </TxTable>
    </TxSection>}
    <TxSection title="Gives">
      {source.coverage.length ? <TxTable density="compact">
        <thead><tr><TxTh>Market</TxTh><TxTh>Data</TxTh><TxTh>History</TxTh>{live && <TxTh>Live</TxTh>}</tr></thead>
        <tbody>{source.coverage.map((row, index) => <tr key={index}>
          <TxTd className="tx-cell-wrap">{marketText(row)}</TxTd><TxTd className="tx-cell-wrap">{kindText(row)}</TxTd>
          <TxTd className="tx-secondary">{row.historyRole === "recent_only" ? "Recent only" : depthText(row.periods?.[0]?.depth)}</TxTd>
          {live && <TxTd className="tx-secondary">{row.live === "none" ? "—" : row.live}</TxTd>}
        </tr>)}</tbody>
      </TxTable> : <span className="tx-secondary">{source.status === "needs-key" ? "Set the key to see what it gives" : "Nothing declared"}</span>}
    </TxSection>
    {bytes > 0 && <TxSection title="Local data" actions={<TxButton size="sm" variant={source.status === "ended" ? "default" : "quiet"} onClick={onDelete}><TxIcon name="trash" />Delete {formatBytes(bytes)}</TxButton>}>
      {held.length > 0 && <TxTable density="compact">
        <tbody>{held.map(item => <tr key={item.label}>
          <TxTd className="tx-cell-name">{item.label}</TxTd><TxTd numeric className="tx-secondary">{item.count.toLocaleString("en-US")}</TxTd><TxTd numeric className="tx-secondary">{formatBytes(item.bytes)}</TxTd>
        </tr>)}</tbody>
      </TxTable>}
    </TxSection>}
  </TxInspector>;
}
