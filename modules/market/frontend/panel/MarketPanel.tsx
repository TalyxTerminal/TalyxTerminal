import { t } from "../i18n";
import { Fragment, useCallback, useContext, useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";
import {
  ModuleContextProvider, TxButton, TxDialog, TxEmpty, TxIcon, TxIconButton, TxMenu, TxProgress, TxSearch, TxSegmented, TxSplit, TxStatus, TxTable, TxTabs, TxTag, TxTd, TxTh,
  TxToolbar, TxToolbarDivider, TxTooltip, type ModuleContext, type TxMenuItem,
} from "@talyx/sdk";
import {
  datasetFacts, datasetsFor, errorText, formatBytes, formatCount, listOf, marketCall, panelCall, selectionsOf, seriesOf, sourceOf, tasksOf,
  type Dataset, type DeleteKinds, type FetchInput, type JobRow, type Library, type Series, type Source, type SourcesAnswer, type Task,
} from "./data";
import { axisFor, axisFrom, AxisTicks, CoverageBar, CoverageDensity } from "./Coverage";
import { DataDetail } from "./DataDetail";
import { ExportDialog } from "./ExportDialog";
import { GetDataDialog, starters, type Pickable } from "./GetDataDialog";
import { ImportDialog } from "./ImportDialog";
import { Sources } from "./Sources";

type Tab = "library" | "sources";

export function usePanelContext(): ModuleContext {
  const ctx = useContext(ModuleContextProvider);
  if (!ctx) throw new Error("The Market panel needs its module context");
  return ctx;
}

/** The library, jobs and sources, read from Market and read again while jobs run. */
function usePanelData(ctx: ModuleContext) {
  const [library, setLibrary] = useState<Library | null>(null);
  const [jobs, setJobs] = useState<JobRow[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [selection, setSelection] = useState<SourcesAnswer["selection"]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const running = useRef(false);
  /** Panel calls still going (a fetch answers when it ends; its job appears in the list only once Market has planned it). */
  const working = useRef(0);
  const track = useCallback(<T,>(work: Promise<T>) => {
    working.current += 1;
    return work.finally(() => { working.current -= 1; });
  }, []);
  const reload = useCallback(async () => {
    try {
      const [lib, jobList, list] = await Promise.all([
        panelCall<Library>(ctx, "library"), panelCall<{ jobs: JobRow[] }>(ctx, "jobs"), panelCall<SourcesAnswer>(ctx, "sources"),
      ]);
      setLibrary(lib); setJobs(jobList.jobs); setSources(list.sources.map(sourceOf)); setSelection(list.selection);
      setProblem(null);
      running.current = jobList.jobs.some(job => ["running", "waiting", "queued"].includes(job.state));
    } catch (error) { setProblem(errorText(error)); }
  }, [ctx]);
  const reloadJobs = useCallback(async () => {
    try {
      const jobList = await panelCall<{ jobs: JobRow[] }>(ctx, "jobs");
      setJobs(jobList.jobs);
      running.current = jobList.jobs.some(job => ["running", "waiting", "queued"].includes(job.state));
      if (!running.current && !working.current) void reload();
    } catch (error) { setProblem(errorText(error)); }
  }, [ctx, reload]);
  useEffect(() => {
    void reload();
    // While a fetch runs: its progress every 2 s; the library (thousands of rows, built in the Host) every 10 s.
    // A hidden panel asks nothing (redrawing its table delayed the live charts beside it); shown again, it reads all.
    let tick = 0, hidden = !ctx.visible.get();
    const stopInterval = ctx.setInterval(() => {
      if (hidden || (!running.current && !working.current)) return;
      tick += 1;
      void (tick % 5 === 0 ? reload() : reloadJobs());
    }, 2000);
    const stopWatch = ctx.visible.watch(visible => {
      if (visible && hidden) void reload();
      hidden = !visible;
    });
    return () => { stopInterval(); stopWatch(); };
  }, [ctx, reload, reloadJobs]);
  return { library, jobs, sources, selection, problem, reload, track };
}

export function MarketPanel() {
  const ctx = usePanelContext();
  const data = usePanelData(ctx);
  const [tab, setTab] = useState<Tab>("library");
  const attention = data.sources.filter(source => source.enabled && (source.status === "unreachable" || source.status === "key-rejected")).length;
  return <div className="market-panel">
    <div className="market-tabs">
      <TxTabs value={tab} onChange={setTab} label={t.c("module", "Market")} options={[
        { value: "library", label: t("Library") },
        { value: "sources", label: t("Sources"), marked: attention ? t("{count} need attention", { count: attention }) : undefined },
      ]} />
    </div>
    {tab === "library" ? <Library data={data} /> : <Sources sources={data.sources} selection={data.selection} library={data.library} onChanged={data.reload} />}
  </div>;
}

/* ── Status: said only when something is not normal ──────────────────────── */

export const statusTone = { current: "done", behind: "review", updating: "busy", failed: "failed", delisted: "off", ended: "off" } as const;
const statusLabel = { current: "", behind: t("Behind"), updating: t("Updating"), failed: t("Failed"), delisted: t("Delisted"), ended: t("Source ended") } as const;

export function SeriesStatus({ series }: { series: Series }) {
  if (series.status === "current") return null;
  const status = <TxStatus tone={statusTone[series.status]} label={series.status === "delisted" ? t("Delisted {to}", { to: series.to }) : statusLabel[series.status]} />;
  return series.statusText ? <TxTooltip label={series.statusText}>{status}</TxTooltip> : status;
}

/** A dataset says the worst thing in it, counted; nothing when all is well. */
function DatasetStatus({ set }: { set: Dataset }) {
  const { failed, behind, updating, ended } = set.count;
  if (failed) return <TxStatus tone="failed" label={t("{failed} failed", { failed })} />;
  if (behind) return <TxStatus tone="review" label={t("{behind} behind", { behind })} />;
  if (updating) return <TxStatus tone="busy" label={t("{updating} updating", { updating })} />;
  if (ended === set.series.length) return <TxStatus tone="off" label={t("Source ended")} />;
  return null;
}

const attention = (series: Series) => series.status === "failed" || series.status === "behind" || series.gapCount > 0;
const matches = (series: Series, words: string[]) => {
  const text = `${series.code} ${series.name} ${series.kind} ${series.source} ${series.identity?.map(item => item.code).join(" ") ?? ""}`.toLowerCase();
  return words.every(word => text.includes(word));
};
const formerCodes = (series: Series) => series.identity?.filter(item => item.code !== series.code).map(item => item.to ? t("{code} until {end}", { code: item.code, end: item.to.slice(0, 4) }) : item.code).join(", ");
export const isStock = (series: Series) => ["us", "cn", "hk"].includes(series.group) && series.capability === "ohlcv";
export const chartable = (series: Series) => series.capability === "ohlcv";

/** "Open in Chart": a new chart of the series (its period, an imported series as itself), at a date when given. */
export function openInChart(ctx: ModuleContext, series: Series, date?: string): void {
  const period = series.timeframe ? `/${series.timeframe}` : "";
  const imported = series.importId ? `;import=${series.importId}` : "";
  void ctx.platform.panels.openTarget({ module: "chart", panelType: "main",
    objectId: `instrument:${series.instrumentId}${period}${date ? `@${date.slice(0, 10)}` : ""}${imported}` });
}

type Show = "all" | "attention" | "delisted";

function Library({ data }: { data: ReturnType<typeof usePanelData> }) {
  const ctx = usePanelContext();
  const names = useMemo(() => Object.fromEntries(data.sources.map(source => [source.id, source.name])), [data.sources]);
  const updating = useMemo(() => new Set<string>(), []);
  const all = useMemo(() => data.library ? seriesOf(data.library, names, updating) : [], [data.library, names, updating]);
  const datasets = useMemo(() => datasetsFor(all), [all]);
  const tasks = useMemo(() => tasksOf(data.jobs), [data.jobs]);
  const alpaca = data.sources.some(source => source.id === "alpaca" && source.enabled && source.status === "ok" && source.keySet === true);
  const [place, setPlace] = useState<string>("all");
  const [show, setShow] = useState<Show>("all");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<string | null>(null);
  const [width, setWidth] = useState(452);
  const [getting, setGetting] = useState<Pickable | true | null>(null);
  const [importing, setImporting] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const list = useRef<HTMLDivElement>(null);

  const set = datasets.find(item => item.id === place) ?? null;
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const scope = set ? set.series : all;
  const filtered = scope.filter(series => (!words.length || matches(series, words))
    && (show === "all" || (show === "attention" ? attention(series) : series.status === "delisted")));
  const listing = set !== null || words.length > 0;
  const detail = all.find(series => series.id === open);
  const chosen = all.filter(series => selected.has(series.id));
  const exportScope = chosen.length > 1 ? chosen : set ? set.series : chosen;
  const updateScope = chosen.length ? chosen : set ? set.series : all;

  const goTo = (id: string) => { setPlace(id); setShow("all"); setOpen(null); setSelected(new Set()); setQuery(""); };
  const pick = (id: string, event: MouseEvent | null) => {
    if (event && (event.ctrlKey || event.metaKey)) {
      const next = new Set(selected);
      if (next.has(id)) next.delete(id); else next.add(id);
      setSelected(next);
      return;
    }
    if (event && event.shiftKey && open !== null) {
      const [a, b] = [filtered.findIndex(row => row.id === open), filtered.findIndex(row => row.id === id)].sort((x, y) => x - y);
      setSelected(new Set(filtered.slice(a, b + 1).map(row => row.id)));
      return;
    }
    setSelected(new Set([id]));
    setOpen(id);
  };
  const step = (delta: number) => {
    const index = filtered.findIndex(row => row.id === open);
    const next = filtered[Math.max(0, Math.min(filtered.length - 1, index + delta))];
    if (next) pick(next.id, null);
  };
  const keys = (event: KeyboardEvent) => {
    if (!listing) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); step(event.key === "ArrowDown" ? 1 : -1); }
    if (event.key === "Escape" && open !== null) setOpen(null);
  };
  useEffect(() => {
    if (open !== null) list.current?.querySelector(`[data-id="${CSS.escape(open)}"]`)?.scrollIntoView({ block: "nearest" });
  }, [open]);

  /** Fetch what is missing for these series up to now (Market only asks the source for what it does not hold). */
  const run = (work: Promise<unknown>) => {
    setProblem(null);
    void data.track(work).catch(error => setProblem(errorText(error))).finally(() => void data.reload());
    window.setTimeout(() => void data.reload(), 300);
  };
  const update = (rows: Series[]) => {
    const fetchable = rows.filter(row => !row.importId);
    const groups = new Map<string, Series[]>();
    for (const row of fetchable) groups.set(`${row.capability}:${row.timeframe}`, [...(groups.get(`${row.capability}:${row.timeframe}`) ?? []), row]);
    for (const items of groups.values()) {
      const first = items[0]!;
      run(selectionsOf(ctx, items).then(selections => marketCall(ctx, "fetch", {
        symbols: listOf<NonNullable<FetchInput["symbols"]>>(selections.map(item => item.instrument.instrumentId), 1, 10000),
        data: [first.capability === "ohlcv" ? { kind: "bars", timeframe: first.timeframe ?? "1d" } : { kind: first.capability }],
        start: first.from })));
    }
  };
  const remove = async (rows: Series[]) => {
    const imports = [...new Set(rows.filter(row => row.importId).map(row => row.importId!))];
    const stored = rows.filter(row => !row.importId);
    for (const importId of imports) await marketCall(ctx, "write_data", { operation: { op: "delete", importId } });
    if (stored.length) await marketCall(ctx, "write_data", { operation: { op: "delete",
      symbols: [...new Set(stored.map(row => `${row.market}:${row.code}`))],
      kinds: listOf<DeleteKinds>([...new Map(stored.map(row => [`${row.capability}:${row.timeframe}`,
        row.capability === "ohlcv" ? { kind: "bars", timeframe: row.timeframe ?? "1d" } : { kind: row.capability }])).values()], 0, 20) } });
  };

  const counts = { attention: scope.filter(attention).length, delisted: scope.filter(series => series.status === "delisted").length };
  const switcher: TxMenuItem[] = datasets.map((item, index) => ({
    id: item.id, label: item.kind, hint: item.series.length.toLocaleString("en-US"), checked: item.id === place, onSelect: () => goTo(item.id),
    group: index === 0 || datasets[index - 1].market !== item.market ? item.market : undefined,
  }));
  const taskList = tasks.length > 0 && <div className="market-tasks">{tasks.map(task => <TaskRow key={task.id} task={task}
    onCancel={() => run(panelCall(ctx, "cancel", { jobId: task.id }))} onResume={() => run(panelCall(ctx, "resume", { jobId: task.id }))} />)}</div>;
  const problemLine = (problem ?? data.problem) && <div className="market-problem" role="alert"><TxStatus tone="failed" label={problem ?? data.problem ?? ""} /></div>;

  if (data.library === null) return <div className="market-library">{problemLine || <TxEmpty title={t("Reading the local library")} />}</div>;

  if (all.length === 0) return <div className="market-library">
    {problemLine}
    {taskList}
    <Start onPick={item => setGetting(item ?? true)} onImport={() => setImporting(true)} />
    {importing && <ImportDialog onClose={() => setImporting(false)} onImported={() => void data.reload()} />}
    {getting && <GetDataDialog initial={getting === true ? undefined : getting} alpaca={alpaca} onClose={() => setGetting(null)} onGet={work => run(work)} />}
  </div>;

  const body = !listing ? <div className="market-list" ref={list}><Overview datasets={datasets} onOpen={goTo} /></div>
    : <div className="market-list" ref={list} tabIndex={0} onKeyDown={keys} aria-label={t("Series")}>
      {set && <DatasetHead set={set} />}
      {(set && (counts.attention || counts.delisted)) ? <div className="market-show">
        <TxSegmented size="sm" value={show} onChange={setShow} label={t("Show")} options={[
          { value: "all", label: t("All {seriesCount}", { seriesCount: set.series.length.toLocaleString("en-US") }) },
          ...(counts.attention ? [{ value: "attention" as const, label: t("Needs attention {attention}", { attention: counts.attention }) }] : []),
          ...(counts.delisted ? [{ value: "delisted" as const, label: t("Delisted {delisted}", { delisted: counts.delisted }) }] : []),
        ]} />
      </div> : null}
      <SeriesTable rows={filtered} set={set} narrow={!!detail} selected={selected} onPick={pick} onChart={series => openInChart(ctx, series)} />
      {filtered.length === 0 && <TxEmpty title={words.length ? t("Nothing matches “{query}”", { query: query.trim() }) : t("Nothing here")} />}
    </div>;

  return <div className="market-library">
    <TxToolbar label={t("Library")} density="compact">
      <nav className="market-crumbs" aria-label={t("Path")}>
        {set ? <>
          <TxButton variant="tool" onClick={() => goTo("all")}>{t("All data")}</TxButton>
          <span className="market-crumb-sep" aria-hidden="true">/</span>
          <TxMenu label={t("Switch dataset")} items={switcher} trigger={<TxButton variant="tool" className="market-crumb-current">{set.market} · {set.kind}<TxIcon name="chevron-down" /></TxButton>} />
        </> : <TxMenu label={t("Open dataset")} items={switcher} trigger={<TxButton variant="tool" className="market-crumb-current">{t("All data")}<TxIcon name="chevron-down" /></TxButton>} />}
      </nav>
      <TxToolbarDivider />
      <TxButton variant="tool-primary" onClick={() => setGetting(true)}><TxIcon name="plus" />{t("Get data")}</TxButton>
      <TxButton variant="tool" disabled={!updateScope.some(row => !row.importId)} onClick={() => update(updateScope)}><TxIcon name="refresh" />{chosen.length > 1 ? t("Update {chosenCount}", { chosenCount: chosen.length.toLocaleString("en-US") }) : set ? t("Update") : t("Update all")}</TxButton>
      <TxButton variant="tool" onClick={() => setImporting(true)}><TxIcon name="download" />{t("Import")}</TxButton>
      <TxButton variant="tool" disabled={exportScope.length === 0} onClick={() => setExporting(true)}><TxIcon name="arrow-up-right" />{t("Export")}</TxButton>
      <TxButton variant="tool" disabled={chosen.length === 0} onClick={() => setDeleting(true)}><TxIcon name="trash" />{t("Delete")}</TxButton>
      <span className="tx-spacer" />
      <TxSearch className="market-filter" value={query} onValueChange={value => { setQuery(value); setOpen(null); }}
        placeholder={set ? t("Filter {market} · {kind}", { market: set.market, kind: set.kind }) : t("Code, former code or name")} aria-label={t("Filter local data")} />
    </TxToolbar>
    {problemLine}
    {taskList}
    <TxSplit sidePosition="end" sideLabel="Series detail" width={width} onWidth={setWidth} min={400} max={760} mainMin={560} sideHidden={!detail}
      side={detail && <DataDetail key={detail.id} series={detail} onClose={() => setOpen(null)} onStep={step}
        onFill={() => update([detail])} onChart={date => openInChart(ctx, detail, date)} />}>
      {body}
    </TxSplit>
    {importing && <ImportDialog onClose={() => setImporting(false)} onImported={() => void data.reload()} />}
    {exporting && <ExportDialog title={chosen.length > 1 || !set ? t("{exportScopeCount} series", { exportScopeCount: exportScope.length.toLocaleString("en-US") }) : `${set.market} · ${set.kind}`}
      series={exportScope} adjustable={exportScope.some(isStock)} onClose={() => setExporting(false)} />}
    {getting && <GetDataDialog initial={getting === true ? undefined : getting} alpaca={alpaca} onClose={() => setGetting(null)} onGet={work => run(work)} />}
    <TxDialog open={deleting} onOpenChange={setDeleting} title={t("Delete {chosenCount} series?", { chosenCount: chosen.length.toLocaleString("en-US") })} footer={<>
      <TxButton size="sm" variant="quiet" onClick={() => setDeleting(false)}>{t("Cancel")}</TxButton>
      <TxButton size="sm" variant="primary" onClick={() => { setDeleting(false); setSelected(new Set()); setOpen(null); run(remove(chosen)); }}>{t("Delete {bytes}", { bytes: formatBytes(chosen.reduce((sum, row) => sum + row.bytes, 0)) })}</TxButton>
    </>}>
      {chosen.slice(0, 6).map(row => <div key={row.id}>{row.code} · {row.kind}</div>)}
      {chosen.length > 6 && <div className="tx-muted">{t("and {chosenCount} more", { chosenCount: chosen.length - 6 })}</div>}
      <div className="tx-secondary">{chosen.every(row => row.importId)
        ? t("The instruments stay; imported rows come back only by importing the files again.")
        : chosen.some(row => row.importId)
          ? t("The instruments stay; downloaded rows are fetched again when needed; imported rows come back only by importing the files again.")
          : t("The instruments stay; downloaded rows are fetched again when needed.")}</div>
    </TxDialog>
  </div>;
}

function groupsOf(datasets: Dataset[]): [string, Dataset[]][] {
  const out: [string, Dataset[]][] = [];
  for (const set of datasets) {
    const last = out[out.length - 1];
    if (last && last[0] === set.market) last[1].push(set); else out.push([set.market, [set]]);
  }
  return out;
}

/* ── All data: one row a dataset ─────────────────────────────────────────── */

function Overview({ datasets, onOpen }: { datasets: Dataset[]; onOpen: (id: string) => void }) {
  const axis = axisFrom(datasets.map(set => set.from).sort()[0] ?? "2006");
  return <TxTable className="market-sets" stickyHead density="compact">
    <colgroup><col className="market-col-set" /><col className="market-col-count" /><col /><col className="market-col-date" /><col className="market-col-gaps" />
      <col className="market-col-source" /><col className="market-col-size" /><col className="market-col-status" /></colgroup>
    <thead><tr><TxTh>{t("Data")}</TxTh><TxTh numeric>{t("Instruments")}</TxTh><TxTh aria-label={t("Coverage")}><AxisTicks axis={axis} /></TxTh><TxTh>{t("Latest")}</TxTh><TxTh numeric>{t("Gaps")}</TxTh><TxTh>{t("Source")}</TxTh><TxTh numeric>{t("Size")}</TxTh><TxTh /></tr></thead>
    <tbody>{groupsOf(datasets).map(([market, items]) => <Fragment key={market}>
      <tr className="market-group"><TxTd colSpan={8}><span className="market-group-label">{market}</span><span className="tx-num market-group-count">{formatBytes(items.reduce((sum, item) => sum + item.bytes, 0))}</span></TxTd></tr>
      {items.map(set => <tr key={set.id} onClick={() => onOpen(set.id)} className="market-set-row">
        <TxTd><span className="tx-cell-name market-clip" title={set.group === "imported" ? set.sources[0] : undefined}>{set.kind}</span></TxTd>
        <TxTd numeric className="market-strong">{set.series.length.toLocaleString("en-US")}</TxTd>
        <TxTd title={`${set.from.slice(0, 10)} → ${set.to.slice(0, 10)}`}><CoverageDensity series={set.series} axis={axis} /></TxTd>
        <TxTd className="tx-num tx-secondary">{set.to.slice(0, 10)}</TxTd>
        <TxTd numeric>{set.gaps ? <span className="market-gaps">{set.gaps}</span> : <span className="market-faint">—</span>}</TxTd>
        <TxTd className="market-clip tx-muted" title={set.sources.join(", ")}>{set.group === "imported" ? t("Imported") : set.sources.join(", ")}</TxTd>
        <TxTd numeric className="tx-secondary">{formatBytes(set.bytes)}</TxTd>
        <TxTd><DatasetStatus set={set} /></TxTd>
      </tr>)}
    </Fragment>)}</tbody>
  </TxTable>;
}

/* ── One dataset: its facts, then one row a series ───────────────────────── */

function DatasetHead({ set }: { set: Dataset }) {
  const facts = datasetFacts(set);
  return <div className="market-head">
    <div className="market-stats">
      <Stat value={set.series.length.toLocaleString("en-US")} label={set.count.delisted ? t("instruments · {delisted} delisted", { delisted: set.count.delisted }) : t("instruments")} />
      <Stat value={`${set.from.slice(0, 10)} → ${set.to.slice(0, 10)}`} />
      <Stat value={formatCount(set.rows)} label={t("rows")} />
      <Stat value={formatBytes(set.bytes)} />
      {set.gaps > 0 && <Stat value={set.gaps.toLocaleString("en-US")} label={set.gaps === 1 ? t("gap") : t("gaps")} tone="fix" />}
      <DatasetStatus set={set} />
    </div>
    <dl className="market-facts">
      <Fact label={t("Source")}>{set.group === "imported" ? <span className="tx-num">{set.sources[0]}</span> : set.sources.join(", ")}</Fact>
      {facts.adjustment && <Fact label={t("Adjustment")}>{facts.adjustment}</Fact>}
      {facts.bias && <Fact label={t("Survivorship")}>{facts.bias}</Fact>}
    </dl>
  </div>;
}

function Stat({ value, label, tone }: { value: string; label?: string; tone?: "fix" }) {
  return <span className="market-stat" data-tone={tone}><b className="tx-num">{value}</b>{label && <span>{label}</span>}</span>;
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return <div className="market-fact"><dt>{label}</dt><dd>{children}</dd></div>;
}

function SeriesTable({ rows, set, narrow, selected, onPick, onChart }: { rows: Series[]; set: Dataset | null; narrow: boolean; selected: Set<string>;
  onPick: (id: string, event: MouseEvent) => void; onChart: (series: Series) => void }) {
  const axis = useMemo(() => axisFor(set ? set.series : rows), [set, rows]);
  const kind = set === null;
  return <TxTable className="market-series" stickyHead density="compact" data-narrow={narrow ? "" : undefined}>
    <colgroup><col className="market-col-instrument" />{kind && <col className="market-col-kind" />}<col />
      {!narrow && <><col className="market-col-date" /><col className="market-col-rows" /></>}<col className="market-col-gaps" />{!narrow && <col className="market-col-size" />}
      <col className="market-col-status" /><col className="market-col-open" /></colgroup>
    <thead><tr><TxTh>{t("Instrument")}</TxTh>{kind && <TxTh>{t("Data")}</TxTh>}<TxTh aria-label={t("Coverage")}><AxisTicks axis={axis} /></TxTh>
      {!narrow && <><TxTh>{t("To")}</TxTh><TxTh numeric>{t("Rows")}</TxTh></>}<TxTh numeric>{t("Gaps")}</TxTh>{!narrow && <TxTh numeric>{t("Size")}</TxTh>}<TxTh /><TxTh /></tr></thead>
    <tbody>{rows.map(series => {
      const former = formerCodes(series);
      return <tr key={series.id} data-id={series.id} data-selected={selected.has(series.id)} onClick={event => onPick(series.id, event)}>
        <TxTd><span className="market-instrument" title={series.name}><span className="tx-cell-name">{series.code}</span>{former && <TxTag tone="muted">{former}</TxTag>}<span className="market-name">{series.name}</span></span></TxTd>
        {kind && <TxTd className="market-clip tx-secondary">{series.group === "imported" ? t("Imported") : `${series.market}`} · {series.kind}</TxTd>}
        <TxTd><CoverageBar series={series} axis={axis} /></TxTd>
        {!narrow && <><TxTd className="tx-num tx-secondary">{series.to}</TxTd><TxTd numeric className="tx-muted">{formatCount(series.rows)}</TxTd></>}
        <TxTd numeric>{series.gapCount ? <span className="market-gaps">{series.gapCount}</span> : <span className="market-faint">—</span>}</TxTd>
        {!narrow && <TxTd numeric className="tx-muted">{formatBytes(series.bytes)}</TxTd>}
        <TxTd><SeriesStatus series={series} /></TxTd>
        <TxTd className="market-open" onClick={event => event.stopPropagation()}>
          {chartable(series) && <TxIconButton variant="tool" size="sm" icon="chart" label={t("Open {code} in Chart", { code: series.code })} onClick={() => onChart(series)} />}
        </TxTd>
      </tr>;
    })}</tbody>
  </TxTable>;
}

/* ── Nothing stored yet: the usual starting sets, each one click from Get data ── */

function Start({ onPick, onImport }: { onPick: (item?: Pickable) => void; onImport: () => void }) {
  return <div className="market-start">
    <div className="market-start-title">{t("No market data on this computer")}</div>
    <div className="market-start-list">{starters.map(item => <TxButton key={item.id} variant="row" onClick={() => onPick(item)}>
      <span className="market-pick"><span className="tx-cell-name">{item.label}</span><span className="tx-muted market-clip">{item.detail}</span>
        <span className="tx-num tx-secondary">{item.count > 1 ? item.count.toLocaleString("en-US") : ""}</span><TxIcon name="chevron" /></span>
    </TxButton>)}</div>
    <div className="market-start-more">
      <TxButton variant="primary" onClick={() => onPick()}><TxIcon name="plus" />{t("Other instruments")}</TxButton>
      <TxButton onClick={onImport}><TxIcon name="download" />{t("Import a folder")}</TxButton>
    </div>
  </div>;
}

/* ── Fetch jobs ──────────────────────────────────────────────────────────── */

function TaskRow({ task, onCancel, onResume }: { task: Task; onCancel: () => void; onResume: () => void }) {
  const failed = task.state === "failed", paused = task.state === "interrupted";
  return <div className="market-task" data-state={task.state}>
    <span className="tx-cell-name market-clip" title={task.title}>{task.title}</span>
    <TxProgress value={task.done / task.total * 100} />
    <span className="tx-num tx-secondary market-task-count">{task.done.toLocaleString("en-US")} / {task.total.toLocaleString("en-US")}</span>
    <span className="tx-num tx-muted market-task-size">{task.rows !== undefined ? t("{rows} rows · {bytes}", { rows: formatCount(task.rows), bytes: formatBytes(task.bytes ?? 0) }) : ""}</span>
    <TxTooltip label={task.detail}><TxStatus tone={failed ? "failed" : paused ? "off" : task.state === "waiting" ? "review" : "busy"} label={task.text} /></TxTooltip>
    <span className="market-task-actions">
      {failed ? <TxIconButton size="sm" icon="close" label={t("Dismiss")} onClick={onCancel} />
        : paused ? <><TxButton size="sm" onClick={onResume}>{t("Resume")}</TxButton><TxIconButton size="sm" icon="close" label={t("Cancel")} onClick={onCancel} /></>
        : <TxButton size="sm" variant="quiet" onClick={onCancel}>{t("Cancel")}</TxButton>}
    </span>
  </div>;
}
