/* A strategy's page: one row on top (name, data, Run), the settings column (the code's own, then the platform's), Work or Results in the middle, the code area beside, and
   under Work the runs, the trades or the portfolio's tables and the log. The state of every run comes from the backend; what the page keeps is what the user is looking at. */
import { t } from "../i18n";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { TxButton, TxCompare, TxFailure, TxRunPicker, TxSkeleton, TxTag, TxIcon, TxIconButton, TxMenu, TxRunButton, TxRunProgress, TxSegmented, useTxToast, type ModuleContext, type TxMenuItem } from "@talyx/sdk";
import { DataSettingsChip, historyBoundary, type HistoryBoundary } from "../DataSettings";
import { ContractError } from "@talyx/sdk";
import { FirstRun, NoResultsYet, type FirstLine } from "../shared/FirstRun";
import { dataReady, openCommonCode, periodText, plain, universeText, useEvent, useThrottled, type Api, type DataDefaults, type LibraryItemSummary, type ObjectDetail, type ParamValue, type RunView as OldRunView } from "../objects";
import type { ShellState } from "../QuantShell";
import { CodeArea, type CodeMark, fileUri } from "../shared/CodeArea";
import { elapsed, mdy, when } from "../factor/format";
import { strategyApi, usable, isActive, FETCH_STEPS, type StrategyRun, type ResultsView as ResultsData, type RunSettings, type RunSettingsView, type RunView, type StrategyRunRow, type InputCard, type Trade } from "./api";
import { FailedBlock, FailureLocals } from "./FailedBlock";
import { Holdings } from "./Holdings";
import { LogList } from "./LogList";
import { MultiChart } from "./MultiChart";
import { RunPrice } from "../shared/RunPrice";
import { restoreNotice } from "../shared/restored";
import { ResultsView, dataToText } from "./Results";
import { SettingsColumn, differing } from "./Settings";
import { TradesTable } from "./TradesTable";
import { ChecksStrip, KeyNumbersRow, LiveRun, RunsView } from "./Work";
import { DataNotes } from "../shared/DataNotes";
import { HeldView } from "./validation/HeldView";
import { ValidationPane } from "./validation/Pane";
import { validationApi } from "./validation/api";
import { useValidation, WALK_DEFAULT, type WalkSettings } from "./validation/useValidation";
import "../factor/factor.css";
import "./validation/validation.css";
import { at, route } from "../types";
import "./strategy.css";

/** The stored run settings with a patch put in (a group is merged, null removes a value). */
const UNIT_MS: Record<string, number> = {m: 60_000, h: 3_600_000, d: 86_400_000, w: 7 * 86_400_000};
/** One bar of a timeframe ("15m", "1d"); an order picked in Results finds its trade within a bar of its time. */
const barMs = (timeframe: string): number => {
  const found = /^(\d+)([mhdw])$/.exec(timeframe);
  return found ? Number(found[1]) * UNIT_MS[found[2]] : 3_600_000;
};
/** The button that lets one block fill the work area, and gives the room back. */
function MaxButton({id, maxed, onMax}: {id: string; maxed: string | null; onMax(id: string | null): void}) {
  return <TxIconButton size="sm" label={maxed === id ? t("Restore") : t("Fill the work area")} icon={maxed === id ? "restore" : "maximize"} onClick={() => onMax(maxed === id ? null : id)} />;
}

export function mergeRun(stored: Record<string, any>, patch: Record<string, any>): Record<string, any> {
  const out: Record<string, any> = {...stored};
  for (const [key, value] of Object.entries(patch)) {
    if (value === null) delete out[key];
    else if ((key === "exchange" || key === "costs") && typeof value === "object") {
      const merged = {...(out[key] ?? {}), ...value};
      for (const [name, item] of Object.entries(merged)) if (item === null) delete merged[name];
      if (Object.keys(merged).length) out[key] = merged; else delete out[key];
    } else out[key] = value;
  }
  return out;
}
/** What the head's run button says, and what a progress bar shows: the stage, how far the simulation is, how many trades so far. */
/** A walk-forward, or a sweep with no combination picked, has no Results of its own: each window or combination is a run of its own. */
export const noOwnResults = (run: StrategyRun | null) => run !== null && (run.kind === "sweep" || run.kind === "walk") && run.shownRunId === run.id;

const WORDS: Record<string, string> = {code: t("Saving the code"), data: t("Fetching prices"), environment: t("Preparing the environment"), backtest: t("Backtesting"), run: t("Backtesting"), sweep: t("Running the combinations"), walk: t("Walking forward")};

export function progressText(run: RunView["run"] | null): {stage: string; count?: string; value: number | null} {
  if (run === null) return {stage: t("Starting"), value: null};
  const words = WORDS;
  const progress = run.progress;
  const fetching = progress?.fetch && (progress.stage ?? run.stage) === "data" ? progress.fetch : null;
  if (fetching) {
    // While the data is fetched: which step, how many names of all, and the names left out so far (Market refused them or has no prices).
    const left = fetching.leftOut.length ? t("{count} left out: {names}{more}", { count: fetching.leftOut.length, names: fetching.leftOut.slice(0, 3).join(", "), more: fetching.leftOut.length > 3 ? ", …" : "" }) : undefined;
    if (fetching.waitingFor) {
              const stage = fetching.waitingUntil == null ? t("Waiting for {source}", { source: fetching.waitingFor })
          : t("Waiting for {source} until {time} UTC", { source: fetching.waitingFor, time: new Date(Number(BigInt(fetching.waitingUntil) / 1_000_000n)).toISOString().slice(11, 16) });
      return {stage, count: left, value: null};
    }
    const step = fetching.message || FETCH_STEPS[fetching.step];
    return {stage: fetching.total ? t("{step}: {done} of {total}", { step, done: fetching.done, total: fetching.total }) : step, count: left, value: fetching.total ? fetching.done / fetching.total : null};
  }
  if (progress && run.state === "running" && progress.simulatedTo) return {stage: t("Backtesting to {simulatedTo}", { simulatedTo: mdy(progress.simulatedTo) }), count: t("{count} trades", { count: progress.tradesSoFar }), value: progress.value};
  return {stage: words[run.stage ?? ""] ?? words[run.message ?? ""] ?? t("Starting"), value: null};
}

/** The head while another run is shown: what the going run's row says (its step, and for a sweep how many combinations are done), not "Starting" throughout. */
export function rowProgress(row: StrategyRunRow | null): {stage: string; count?: string; value: number | null} {
  if (row === null) return progressText(null);
  const stage = WORDS[row.stage ?? ""] ?? t("Starting");
  return row.sweep && row.stage !== "data" && row.stage !== "environment" && row.stage !== "code"
    ? {stage, count: t("{done} of {total}", { done: row.sweep.done, total: row.sweep.total }), value: row.sweep.total ? row.sweep.done / row.sweep.total : null} : {stage, value: null};
}

export function StrategyPage({ctx, api, detail, onDetail, state, update, title, menu, onOpenObject}: {
  ctx: ModuleContext; api: Api; detail: ObjectDetail; onDetail(detail: ObjectDetail): void; state: ShellState; update(patch: Partial<ShellState>): void;
  title: ReactNode; menu: TxMenuItem[]; onLibrary(versionId: string): void; onOpenObject(id: string): void;
}) {
  const sapi = useMemo(() => strategyApi(ctx), [ctx]);
  const toast = useTxToast();
  const [error, setError] = useState<string | null>(null);
  const fail = (reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason));
  const data = detail.settings.data;
  // what a run trades decides its account, exchange and costs, so run settings wait for the data (not a crypto account before an index is chosen).
  const dataChosen = Boolean((data as {index?: unknown}).index || (data as {list?: unknown}).list || ((data as {symbols?: unknown[]}).symbols ?? []).length);
  const pageView = state.strategyView === "results" ? "results" : "work";

  // ---------------------------------------------------------------- runs: the list polled by change, the selected one's view
  const [runs, setRuns] = useState<StrategyRunRow[] | null>(null);
  const since = useRef<number | undefined>(undefined);
  const loading = useRef<Promise<void> | null>(null), again = useRef(false);
  const loadRuns = useCallback((full = false): Promise<void> => {
    if (loading.current) {again.current = true; return loading.current;}
    const go = async () => {
      const page = await sapi.runs(detail.id, full ? undefined : since.current);
      since.current = page.now - 2000;
      setRuns(current => {
        if (page.complete || current === null) return page.items;
        const byId = new Map(current.map(item => [item.id, item]));
        for (const item of page.items) byId.set(item.id, item);
        return [...byId.values()].sort((a, b) => b.number - a.number);
      });
    };
    loading.current = go().catch(fail).finally(() => {loading.current = null; if (again.current) {again.current = false; void loadRuns();}});
    return loading.current;
  }, [sapi, detail.id]);
  useEffect(() => {since.current = undefined; setRuns(null); void loadRuns(true);}, [detail.id]);
  // A running run reports several times a second: the list is read once a second at most. What the user does reads at once.
  const runsChanged = useThrottled(() => void loadRuns());
  useEvent(ctx, "quant.object_run_changed", payload => {if ((payload as {objectId?: string}).objectId === detail.id) runsChanged();}, () => void loadRuns(true));
  const list = runs ?? [];
  const selected = list.find(item => item.id === state.runId) ?? list[0] ?? null;
  const active = list.find(isActive) ?? null;
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [chosen, setChosen] = useState<string[] | null>(null);  // the runs picked in the Compare menu (up to three); an empty list asks for no comparison
  const previous = selected === null ? null : list.find(item => item.number < selected.number && usable(item)) ?? null;
  const pickedIds = chosen !== null ? chosen : ticked.size ? [...ticked] : previous ? [previous.id] : [];
  const comparedRuns = pickedIds.slice(0, 3).map(id => list.find(item => item.id === id && usable(item))).filter((item): item is NonNullable<typeof item> => Boolean(item) && item?.id !== selected?.id);
  const compared = comparedRuns[0] ?? null;
  const [view, setView] = useState<RunView | null>(null);
  const vs = usable(selected) && comparedRuns.length ? comparedRuns.map(item => item.id).join(",") : undefined;
  // A sweep, walk-forward or cost rerun that ends changes the shown run's checks (its row kept offering "Rerun at 2× costs" until a reload): read it again then.
  const ended = list.reduce((count, item) => count + (isActive(item) ? 0 : 1), 0);
  useEffect(() => {
    let live = true;
    if (selected === null) {setView(null); return;}
    // A run that is going shows its progress: read again a second after the last answer, never two reads at once.
    let timer: ReturnType<typeof setTimeout> | undefined;
    const read = (): Promise<void> => sapi.run(selected.id, vs).then(value => {if (live) setView(value);}, fail)
      .finally(() => {if (live && isActive(selected)) timer = setTimeout(() => void read(), 1000);});
    void read();
    return () => {live = false; if (timer) clearTimeout(timer);};
  }, [selected?.id, selected?.state, vs, ended]);
  const run = view !== null && view.run.id === selected?.id ? view.run : null;
  const ownless = noOwnResults(run);
  const work = view !== null && view.run.id === selected?.id ? view.work : null;

  // ---------------------------------------------------------------- the platform's settings, as they stand and as edited
  const [settings, setSettings] = useState<RunSettingsView | null>(null);
  const settingsKey = JSON.stringify([detail.settings.data, detail.settings.account]);
  const loadSettings = () => sapi.runSettings(detail.id).then(setSettings, fail);
  useEffect(() => {void loadSettings();}, [detail.id, settingsKey]);
  const saving = useRef<{timer: ReturnType<typeof setTimeout>; save(): void} | null>(null);
  const [stored, setStored] = useState<Record<string, any>>({});
  useEffect(() => setStored(settings?.stored ?? {}), [settings?.stored && JSON.stringify(settings.stored)]);
  const edit = (patch: Record<string, unknown>) => {
    const next = mergeRun(stored, patch);
    setStored(next);
    setSettings(current => current === null ? current : {...current, effective: mergeEffective(current.effective, patch)});
    if (saving.current) clearTimeout(saving.current.timer);
    const save = () => {saving.current = null; void api.settings(detail.id, {run: next}).then(onDetail, fail).then(loadSettings);};
    saving.current = {timer: setTimeout(save, 500), save};
  };
  useEffect(() => () => {saving.current?.save();}, []);
  const [params, setParams] = useState<Record<string, ParamValue>>({...detail.settings.params});
  useEffect(() => setParams({...detail.settings.params}), [detail.id]);
  const editParam = (name: string, value: ParamValue) => {
    const next = {...params, [name]: value};
    setParams(next);
    void api.settings(detail.id, {params: Object.fromEntries(Object.entries(next).filter(([key]) => detail.parameters.some(item => item.name === key)))}).then(onDetail, fail);
  };
  const base = run;
  const changed = useMemo(() => {
    if (settings === null || base === null) return new Set<string>();
    // A run still preparing its data does not know what it trades, so its run settings are the defaults of another kind: nothing reads as changed yet.
    const out = !["queued", "preparing"].includes(base.state) ? differing(settings.effective, base.settings.run) : new Set<string>();
    out.delete("currency"); out.delete("exchange.maxLeverage"); out.delete("costs.feeSource");
    const costs = (settings.effective as {costs?: Record<string, unknown>}).costs ?? {};
    for (const key of Object.keys(costs)) if (costs[key] === null) out.delete(`costs.${key}`);  // left to Market: the run holds the number it came to
    for (const item of detail.parameters) if (JSON.stringify(params[item.name] ?? item.default) !== JSON.stringify(base.settings.params[item.name] ?? item.default)) out.add(`param:${item.name}`);
    return out;
  }, [settings, base?.id, params, detail.parameters]);
  const revert = () => {
    if (base === null) return;
    saving.current && clearTimeout(saving.current.timer);
    setParams({...base.settings.params});
    void api.settings(detail.id, {run: stored_of(base.settings.run, settings), params: base.settings.params}).then(onDetail, fail).then(loadSettings);
  };
  const [inputs, setInputs] = useState<InputCard[]>([]);
  const loadInputs = () => sapi.inputs(detail.id).then(found => setInputs(found.items), fail);  // a failed read says so
  useEffect(() => {void loadInputs();}, [detail.id, settingsKey]);
  const [library, setLibrary] = useState<LibraryItemSummary[] | null>(null);
  const needsLibrary = detail.parameters.some(item => item.type === "library" || item.type === "pick");
  useEffect(() => {if (needsLibrary) void api.library().then(value => setLibrary(value.items), fail);}, [needsLibrary]);

  // ---------------------------------------------------------------- Results (read when they are on screen)
  // Kept with what they were read for: going back to Results on the same run shows them at once, and another run's are never shown as this one's.
  const [read, setRead] = useState<{key: string; runId: string; value: ResultsData | string} | null>(null);
  const waitsForRun = selected !== null && (selected.kind === "sweep" || selected.kind === "walk") && run === null;  // only these need the run to know whose report to read
  const resultsWanted = pageView === "results" && selected !== null && !ownless && !waitsForRun && (selected.state === "completed" || selected.state === "failed" || selected.state === "cancelled");
  const resultsKey = selected === null ? "" : `${selected.id}|${selected.state}|${vs ?? ""}|${ended}`;  // the Costs section fills from the reruns as they end
  useEffect(() => {
    let live = true;
    if (!resultsWanted || (read?.key === resultsKey && typeof read.value !== "string")) return;
    const runId = selected!.id;
    void sapi.results(runId, undefined, comparedRuns.map(item => item.id)).then(value => {
      if (live) setRead({key: resultsKey, runId, value});
    }, reason => {
      if (live) setRead({key: resultsKey, runId, value: reason instanceof Error ? reason.message : String(reason)});
    });
    return () => {live = false;};
  }, [resultsWanted, resultsKey]);
  const results = read !== null && read.runId === selected?.id && typeof read.value !== "string" ? read.value : null;
  const readError = read?.key === resultsKey && typeof read.value === "string" ? read.value : null;
  // While the next run's report is read, the one on screen stays (its own name over its own numbers), so the page neither collapses nor jumps.
  const shownReport = useRef<{run: StrategyRun; results: ResultsData; picking?: {name: string; to: string | null}[]; multi: boolean} | null>(null);
  if (run !== null && results !== null) shownReport.current = {run, results, picking: work?.usedToPick, multi: work?.kind === "multi"};
  const held = resultsWanted && (run === null || results === null) && shownReport.current?.run.objectId === detail.id ? shownReport.current : null;
  const report: {run: StrategyRun; results: ResultsData | null; picking?: {name: string; to: string | null}[]; multi: boolean} | null = held ?? (run === null ? null : {run, results, picking: work?.usedToPick, multi: work?.kind === "multi"});

  // ---------------------------------------------------------------- the chart: the backend's equity figure; one symbol's run names its instrument
  const [oldRun, setOldRun] = useState<OldRunView | null>(null);
  const single = work?.kind === "single";
  // A finished run has its chart; so does one that stopped (failed, stopped, interrupted) with the part it ran kept.
  const charted = selected !== null && (selected.state === "completed" || selected.state === "failed" || ((selected.state === "cancelled" || selected.state === "interrupted") && Boolean(work?.partial)));
  useEffect(() => {
    let live = true;
    if (!single || selected === null || !charted) {setOldRun(null); return;}
    void api.run(selected.id).then(value => {if (live) setOldRun(value);}, reason => {if (live) fail(reason);});
    return () => {live = false;};
  }, [single, selected?.id, selected?.state, charted]);
  const source = oldRun?.data?.sources[0] ?? null;
  const [trade, setTrade] = useState<Trade | null>(null);
  useEffect(() => setTrade(null), [selected?.id]);
  const pick = (next: Trade) => {setTrade({...next}); update({strategyTrade: next.id});};
  // a trade or an order picked on Results opens Work on that trade: the chart moves to it, the trades table and the log follow.
  // A trade the page asks for by its id or a moment: read from the run's trades, not kept with the run.
  const findTrade = (runId: string, input: {id?: number; since?: number; until?: number}, match: (item: Trade) => boolean) =>
    sapi.trades(runId, {...input, limit: 500}).then(page => page.rows.find(match) ?? null);
  const toTrade = (next: Trade | null, tab?: "log") => {
    if (next !== null) setTrade(next);
    update({strategyView: undefined, strategyDrawdown: undefined, strategyTab: tab, ...(next !== null ? {strategyTrade: next.id} : {})});
  };

  // ---------------------------------------------------------------- the head
  const [defaults, setDefaults] = useState<{value: DataDefaults | null; error: string | null; boundary: HistoryBoundary | null}>({value: null, error: null, boundary: null});
  useEffect(() => {
    let live = true;
    if (!dataReady(data) || (!(data.symbols ?? []).length && !data.index)) {setDefaults({value: null, error: null, boundary: null}); return;}
    void api.defaults(detail.id).then(value => {if (live) setDefaults({value, error: null, boundary: null});}, reason => {
      if (live) setDefaults({value: null, error: reason instanceof Error ? reason.message : String(reason), boundary: reason instanceof ContractError ? historyBoundary(reason.code, reason.value.details) : null});
    });
    return () => {live = false;};
  }, [detail.id, settingsKey]);
  const [dataOpen, setDataOpen] = useState(false);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {if (active === null) return; setNow(Date.now()); const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer);}, [active?.id]);
  const start = async () => {
    if (!dataReady(data)) {setDataOpen(true); return;}
    setError(null);
    saving.current?.save();
    const row = await sapi.start(detail.id);
    update({runId: row.id, strategyView: undefined});
    await loadRuns();
  };
  const sapi_use_and_run = async (sweepId: string, index: number) => {
    await validationApi(ctx).use(sweepId, index);
    onDetail(await api.read(detail.id));
    await start();
  };
  const blocked = detail.errors.length > 0 ? t("Fix the code error shown next to the settings first") : null;
  const progress = active !== null && run?.id === active.id ? progressText(run) : rowProgress(active);
  const goSection = (section: string) => update({strategyView: "results", strategyResults: section});
  const [ranges, setRanges] = useState<Record<string, string>>({});
  useEffect(() => setRanges({}), [detail.id]);
  const [walkSettings, setWalkSettings] = useState<WalkSettings>(WALK_DEFAULT);
  const validation = useValidation({ctx, walkSettings, sweepSource: run?.of ?? null, objectId: detail.id, parameters: detail.parameters, ranges, selected: run, usable: usable(selected), backtestId: list.find(item => usable(item))?.id ?? null, update: update as (patch: Record<string, unknown>) => void,
    reload: () => loadRuns(), fail, showResults: goSection});
  const setRange = (name: string, text: string | null) => setRanges(current => {const next = {...current}; if (text === null) delete next[name]; else next[name] = text; return next;});

  // ---------------------------------------------------------------- the code area
  const root = detail.entryUri.slice(0, detail.entryUri.lastIndexOf("/"));
  const codeShown = state.side === "code";
  const [codeLine, setCodeLine] = useState<{line: number; request: string} | undefined>(undefined);
  const goLine = (_file: string | null | undefined, line: number) => {update({side: "code"}); setCodeLine({line, request: `${Date.now()}`});};
  const marks: CodeMark[] = run?.failure && run.failure.line && run.failure.file ? [{uri: fileUri(root, run.failure.file), line: run.failure.line, message: run.failure.error.split("\n")[0], title: t("Run #") + run.number}] : [];
  const [dragging, setDragging] = useState<{height?: number; side?: number} | null>(null);
  const side = dragging?.side ?? state.sideSize ?? .32, botHeight = dragging?.height ?? state.strategyHeight ?? null;
  const mainBox = useRef<HTMLDivElement>(null);
  const dragHeight = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const box = mainBox.current!.getBoundingClientRect();
    const target = event.currentTarget; target.setPointerCapture(event.pointerId);
    let height: number | null = null;
    const move = (next: PointerEvent) => {height = Math.round(Math.max(120, Math.min(box.height - 230, box.bottom - next.clientY))); setDragging({height});};
    const up = () => {target.removeEventListener("pointermove", move); target.removeEventListener("pointerup", up); if (height !== null) update({strategyHeight: height}); setDragging(null);};
    target.addEventListener("pointermove", move); target.addEventListener("pointerup", up);
  };
  const dragSide = (event: React.PointerEvent<HTMLDivElement>) => {
    const box = (event.currentTarget.parentElement as HTMLElement).getBoundingClientRect();
    const target = event.currentTarget; target.setPointerCapture(event.pointerId);
    let size: number | null = null;
    const move = (next: PointerEvent) => {size = Math.min(.6, Math.max(.22, (box.right - next.clientX) / box.width)); setDragging({side: size});};
    const up = () => {target.removeEventListener("pointermove", move); target.removeEventListener("pointerup", up); if (size !== null) update({sideSize: size}); setDragging(null);};
    target.addEventListener("pointermove", move); target.addEventListener("pointerup", up);
  };

  // ---------------------------------------------------------------- the bottom
  const tab = state.strategyTab;
  const [kinds, setKinds] = useState<string[]>([]);
  const bottomTabs: {id: NonNullable<ShellState["strategyTab"]>; label: string; count?: number}[] = [{id: "rebalances", label: t("Rebalances")}, {id: "trades", label: t("Trades")}, {id: "holdings", label: t("Holdings")}, {id: "contributions", label: t("Contributors")}];
  const runsTab = tab === "runs";
  const shownTab = bottomTabs.some(item => item.id === tab) ? tab : "rebalances";
  const [maxed, setMaxed] = useState<string | null>(null);
  const bar = barMs(source?.series.timeframe ?? data.timeframe ?? "1h");
  const sourceInstrument = source?.instrument as {base?: string; displaySymbol?: string; symbol?: string} | undefined;
  const instrumentShort = sourceInstrument?.base ?? sourceInstrument?.displaySymbol ?? sourceInstrument?.symbol ?? "";
  const liveSymbols = (data.symbols ?? []).map(item => item.instrument as {displaySymbol?: string; symbol?: string; base?: string});
  const liveName = liveSymbols.length === 1 ? liveSymbols[0].base ?? liveSymbols[0].displaySymbol ?? liveSymbols[0].symbol ?? "" : t("Equity");
  const liveKind = `${data.timeframe ?? ""}`.replace(/^(\d+)([a-z]+)$/i, "$1 $2");
  const liveBenchmark = liveSymbols.length === 1 ? t("{name} hold", { name: liveName }) : t("the market");
  const instrumentKind = `${work?.instrument?.type ?? ""} · ${(source?.series.timeframe ?? data.timeframe ?? "").replace(/^(\d+)([a-z]+)$/i, "$1 $2")}`.replace(/^ · /, "");
  const chartTitle = single ? <><h3>{instrumentShort}</h3><span className="fx-t3 qx-s11">{instrumentKind}</span></> : <><h3>{t("Equity and price")}</h3>{work?.multi && <span className="qx-cnt">{t("{rebalancesTotal} rebalances", { rebalancesTotal: work.multi.rebalancesTotal })}</span>}</>;
  const chartTools = <>{work !== null && run !== null && run.failure === null && <ChecksStrip work={work} go={validation.onCheck} />}
    <span className="tx-spacer" /><MaxButton id="chart" maxed={maxed} onMax={setMaxed} /></>;
  const comparePicker = <TxCompare run={comparedRuns.length ? comparedRuns.map(item => `#${item.number}`).join(", ") : null} items={[
    {id: "none", label: t("No comparison"), checked: comparedRuns.length === 0, onSelect: () => setChosen([])},
    ...list.filter(item => usable(item) && item.id !== selected?.id).map(item => ({id: item.id, label: `#${item.number}`, detail: when(item.createdAt), checked: comparedRuns.some(other => other.id === item.id), disabled: comparedRuns.length >= 3 && !comparedRuns.some(other => other.id === item.id),
      onSelect: () => setChosen(comparedRuns.some(other => other.id === item.id) ? comparedRuns.filter(other => other.id !== item.id).map(other => other.id) : [...comparedRuns.map(other => other.id), item.id])}))]} />;
  const [restored, setRestored] = useState<{id: string; note: string} | null>(null);
  const useVersion = async (id: string) => {
    if (saving.current) { clearTimeout(saving.current.timer); saving.current = null; }
    const next = await api.apply(id, detail.version);
    onDetail(next);
    setParams({...next.settings.params});
    await loadSettings();
    const note = restoreNotice(next);
    setRestored({id, note});
  };
  const menuItems: TxMenuItem[] = [
    ...(selected && run?.id === selected.id && run.codeDigest && selected.state === "completed" && !selected.sweep ? [
      {id: "rerun", label: t("Run with original settings"), onSelect: () => void sapi.start(detail.id, selected.id).then(next => {update({runId: next.id}); void loadRuns();}, fail)},
      {id: "apply", label: t("Use this version"), onSelect: () => void useVersion(selected.id).catch(fail)}] : []),
    {id: "common", label: t("Open common code"), icon: "folder", onSelect: () => void openCommonCode(ctx, api).catch(fail)}, ...menu];
  const mainFigure = work?.figures?.equity;
  const failedLine = run?.failure && run.failure.line && run.failure.file ? run.failure : null;
  const codeStatus = <>{run?.environment?.python && <span>{t("Python {python}", { python: run.environment.python })}</span>}{run?.environment?.nautilus && <span>{t("Nautilus {nautilus}", { nautilus: run.environment.nautilus })}</span>}{failedLine ? <span className="attn" role="alert">{t("Line {line}: {error}", { line: failedLine.line ?? "", error: failedLine.error.split("\n")[0].split(":")[0] })}</span> : run ? <span>{t("Code of #{number}", { number: run.number })}</span> : null}
    <span className="tx-spacer" /><span>{t("Ctrl+Enter runs")}</span></>;
  const firstLines: FirstLine[] = [
    {label: t("Names"), value: universeText(data, null) ?? t("Set data"), missing: universeText(data, null) === null, onClick: () => setDataOpen(true)},
    {label: t("Bars"), value: liveKind || t("Set data"), missing: !liveKind, onClick: () => setDataOpen(true)},
    {label: t("Period"), value: data.since && data.until ? <>{periodText(data.since, data.until)}{data.heldOutFrom && <span className="fx-t3">{t(", then held out")}</span>}</> : t("Set data"), missing: !(data.since && data.until), onClick: () => setDataOpen(true)},
    {label: t("Code"), value: blocked ?? detail.entry, missing: blocked !== null, onClick: () => update({side: "code"})},
  ];
  return <div className="qx-page fx-page" onKeyDownCapture={event => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && blocked === null && !(selected && isActive(selected))) {event.preventDefault(); event.stopPropagation(); validation.run(() => void start().catch(fail));}
  }}>
    <div className="tx-panelhead fx-head">
      {title}<span className="fx-kind">{t("Strategy")}</span>
      <DataSettingsChip ctx={ctx} api={api} kind="strategy" alsoBuilt={settings?.alsoBuilt ?? null} data={data} defaults={defaults.value} defaultsError={defaults.error} boundary={defaults.boundary} open={dataOpen} onOpenChange={setDataOpen}
        onSave={async next => onDetail(await api.settings(detail.id, {data: next}))} />
      <span className="tx-spacer" />
      {active !== null ? <TxRunProgress run={`#${active.number}`} stage={progress.stage} count={progress.count} value={progress.value} elapsed={elapsed((now - active.createdAt) / 1000)}
        onStop={() => void sapi.cancel(active.id).then(() => loadRuns(), fail)} />
        : <TxRunButton label={validation.label} kinds={validation.kinds} onRun={() => validation.run(() => void start().catch(fail))} onKind={validation.onKind} disabled={blocked !== null} title={blocked ?? t("Run backtest (Ctrl+Enter)")} />}
      <TxSegmented label={t("Work or Results")} value={pageView} options={[{value: "work", label: t("Work")}, {value: "results", label: t("Results")}]} onChange={next => update({strategyView: next === "results" ? "results" : undefined})} />
      <button type="button" className="tx-codetoggle" aria-pressed={codeShown} onClick={() => update({side: codeShown ? undefined : "code"})}><TxIcon name="code" /><span>{codeShown ? t("Hide code") : t("Show code")}</span></button>
      <TxMenu label={t("Strategy actions")} align="end" items={menuItems} trigger={<TxIconButton label={t("Strategy actions")} icon="more" />} />
    </div>
    {restored?.note && restored.id === selected?.id && <div className="qx-note2" role="status">{restored.note}</div>}
    {error && <div className="qx-page-error" role="alert"><span>{error}</span><TxButton size="sm" variant="quiet" onClick={() => setError(null)}>{t("Dismiss")}</TxButton></div>}
    <div className="fx-desk">
      <div className="fx-view">
        {pageView === "results"
            ? <div className="fx-main">{readError !== null && resultsWanted ? <TxFailure title={t("Could not read this run’s results")} message={readError} />
              : report === null ? runs !== null && selected === null ? <NoResultsYet lines={firstLines} toWork={() => update({strategyView: undefined})} /> : <div className="fx-pad"><TxSkeleton width="40%" /></div>
            : held === null && ownless ? <p className="fx-empty">{report.run.kind === "walk" ? t("Walk-forward #{number} has no Results of its own; its windows are on Work.", { number: report.run.number }) : t("Sweep #{number} has no Results of its own until one combination is picked on Work.", { number: report.run.number })}</p>
            : <ResultsView api={sapi} run={report.run} pending={held !== null} parameters={detail.parameters} picking={report.picking} multi={report.multi} compare={usable(selected) ? comparePicker : undefined} onWork={() => update({strategyView: undefined})} picker={<TxRunPicker run={`#${report.run.number}`} items={list.filter(item => usable(item) || item.state === "failed").map(item => ({id: item.id, label: `#${item.number}`, detail: when(item.createdAt), checked: item.id === selected?.id, onSelect: () => update({runId: item.id})}))} />} view={report.results} section={state.strategyResults} onSection={id => update({strategyResults: id})}
              onStar={() => void sapi.annotate(report.run.id, {starred: !report.run.starred}).then(() => loadRuns(), fail)}
              onNotebook={() => void route<{notebookId: string}>(ctx, "POST", at`/runs/${report.run.id}/notebook`).then(opened => onOpenObject(opened.notebookId), fail)} onOrder={order => void findTrade(report.run.id, {since: order.time - bar, until: order.time + bar}, item => item.instrument === order.instrument).then(found => toTrade(found, "log"), fail)}
              onTrade={id => void findTrade(report.run.id, {id}, () => true).then(found => toTrade(found), fail)} onCapacity={() => validation.onKind("costs")} onDrawdown={span => update({strategyView: undefined, strategyDrawdown: span, strategyTrade: undefined, strategyTab: work?.kind === "multi" ? "rebalances" as const : undefined})}
              onUse={() => void useVersion(report.run.id).catch(fail)} />}</div>
          : <>
            <SettingsColumn signalRun={(detail.settings as {signalRun?: {param: string; model: string; number: number; kind?: string}}).signalRun ?? null} entry={detail.entry} since={data.since} view={dataChosen ? settings : null} parameters={detail.parameters} errors={detail.errors} params={params} library={library} base={base} changed={changed} inputs={inputs}
              riskFreeMean={work?.riskFree.meanPct} onParam={editParam} onRun={edit} onRevert={revert} onLine={(file, line) => goLine(file, line)}
              ranges={ranges} rangeErrors={validation.plan.errors} combinations={validation.plan.total} onRange={setRange} heldMissing={validation.heldMissing} walk={walkSettings} onWalk={patch => setWalkSettings(current => ({...current, ...patch}))}
              onSwitch={(name, versionId) => void sapi.switchInput(detail.id, name, versionId).then(found => {setInputs(found.items); toast({title: found.notComparable ? t("Switched; results won't compare with earlier runs") : t("Switched back")});}, fail)}
              onOpenRun={runId => update({runId})} />
            <div className="fx-main qx-wmain" ref={mainBox}>
              <div className="qx-lhead" role="tablist">
                <button type="button" role="tab" className="qx-ltab" aria-selected={!runsTab} onClick={() => update({strategyTab: undefined})}>{t.c("run record", "Run")}{selected && <b>#{selected.number}</b>}</button>
                <button type="button" role="tab" className="qx-ltab" aria-selected={runsTab} onClick={() => update({strategyTab: "runs"})}>{t("Runs")}<span className="qx-cnt">{list.length}</span></button>
                {!runsTab && selected && run && <>
                  <span className="qx-sep" />
                  <span className="qx-x5">{when(selected.createdAt)}</span>
                  <span className="fx-t3 qx-per" title={run.dataTo ? t("Data to {dataTo}", { dataTo: dataToText(run.dataTo) }) : undefined}>{dataToText(run.settings.data.since)} – {dataToText(run.settings.data.until)}</span>
                  {run.seconds ? <span className="fx-t3 qx-x5">{t("ran {seconds}", { seconds: elapsed(run.seconds) })}</span> : null}
                  {work !== null && <DataNotes notes={work.dataNotes ?? []} />}
                  {selected.kind !== "backtest" && <TxTag tone="muted">{selected.kind === "sweep" ? t("Sweep") : selected.kind === "walk" ? t("Walk-forward") : t("Held-out")}</TxTag>}{selected.state === "failed" && <TxTag tone="fix">{t("Failed")}</TxTag>}{(selected.state === "cancelled" || selected.state === "interrupted") && <TxTag tone="muted">{t("Stopped")}</TxTag>}
                  {usable(selected) && comparePicker}
                  <span className="tx-spacer" />
                  {selected.state === "completed" && !ownless && <TxButton size="sm" variant="quiet" onClick={() => update({strategyView: "results"})}>{t("Results")}<TxIcon name="chevron" /></TxButton>}
                </>}
              </div>
              {runsTab ? <div className="qx-runsfull"><RunsView runs={list} parameters={detail.parameters} selected={selected?.id ?? null} compared={usable(selected) ? compared?.id ?? null : null} ticked={ticked}
                onSelect={id => update({runId: id})} onOpen={id => update({runId: id, strategyTab: undefined})} onTick={(id, on) => setTicked(current => {const next = new Set(current); if (on) next.add(id); else next.delete(id); return next;})} /></div>
              : selected === null ? runs === null ? <div className="fx-pad"><TxSkeleton width="40%" /></div> : <FirstRun lines={firstLines} />
              : <>
                {run !== null && run.failure !== null ? <FailedBlock run={run} onLine={goLine} />
                  : run !== null && (run.kind === "sweep" || run.kind === "walk") ? (run.notes.length > 0 ? <div className="qx-notes" role="status">{run.notes.map((note, index) => <span key={index}>{note}</span>)}</div> : null)
                  : work !== null ? <KeyNumbersRow work={work} go={goSection} />
                  : run !== null && isActive(selected) && run.state === "running" && run.kind === "backtest" ? <LiveRun run={run.progress} name={liveName} kind={liveKind} benchmark={liveBenchmark} />
                  : run !== null && isActive(selected) ? <div className="fx-empty" role="status"><TxIcon name="refresh" />{progress.stage}{progress.count ? ` · ${progress.count}` : ""}</div>
                  : run !== null && run.state === "completed" ? <p className="fx-empty">{run.notes[0] ?? t("This run has no report")}</p>
                  : run === null ? <div className="fx-pad"><TxSkeleton width="40%" /></div>
                  : <p className="fx-empty" role="status">{run.message ?? (run.state === "cancelled" ? t("Stopped before the backtest began") : t("Interrupted before the backtest began"))}</p>}
                {run !== null && (run.kind === "sweep" || run.kind === "walk") && (run.failure === null || run.kind === "walk")
                  ? <ValidationPane ctx={ctx} runId={run.id} state={run.state} kind={run.kind} sourceNumber={list.find(item => item.id === run.of)?.number ?? null} onError={fail}
                    onUsed={() => {toast({title: t("Values written to the settings")}); void api.read(detail.id).then(onDetail, fail);}}
                    onRunValues={(sweepId, index) => void sapi_use_and_run(sweepId, index)}
                    onWiden={(axis, end) => {
                      // The range the sweep ran, one more step past the end the best value sat on; the sweep is then one Run away.
                      const values = axis.values.map(Number).sort((a, b) => a - b);
                      const step = values.length > 1 ? (end === "largest" ? values[values.length - 1] - values[values.length - 2] : values[1] - values[0]) : Math.abs(values[0]) / 2 || 1;
                      const next = end === "largest" ? values[values.length - 1] + step : values[0] - step;
                      const wider = (end === "largest" ? [...values, next] : [next, ...values]).map(value => String(Math.round(value * 1e6) / 1e6)).join(", ");
                      if (!axis.name.startsWith("@")) setRange(axis.name, wider);
                    }} />
                : work !== null && <>
                  <div className="qx-upper" data-max={maxed ?? undefined} style={botHeight !== null ? {flex: `1 1 auto`} : undefined}>
                    {work.heldOut ? <div className="qx-blk qx-chartblk"><HeldView number={run?.number ?? 0} of={work.heldOut.of ?? 0} figure={work.heldOut.figure} rows={work.heldOut.rows.map(([name, inSample, heldOut]) => ({name, inSample, heldOut}))} /></div> :
                    <div className="qx-blk qx-chartblk">
                      {selected.state === "completed"
                        ? <div className="qx-mainchart"><RunPrice key={selected.id} runId={selected.id} compareTo={vs} trade={trade} portfolio head={{className: "qx-bh", title: chartTitle, tools: chartTools}} /></div>
                        : <><div className="qx-bh">{chartTitle}{chartTools}</div>
                          <div className="qx-mainchart"><MultiChart figure={mainFigure} picking={work?.usedToPick} /></div></>}
                    </div>}
                    {run !== null && run.failure !== null && <div className="qx-blk qx-ckcol"><FailureLocals run={run} onNotebook={() => void route<{notebookId: string}>(ctx, "POST", at`/runs/${run.id}/notebook`).then(opened => onOpenObject(opened.notebookId), fail)} /></div>}
                  </div>
                  <div className="fx-hgut" role="separator" aria-label={t("Resize")} onPointerDown={dragHeight} onDoubleClick={() => update({strategyHeight: undefined})} />
                  <div className="qx-lower" data-max={maxed ?? undefined} style={botHeight !== null ? {flex: `0 0 ${botHeight}px`} : undefined}>
                    <div className="qx-lowleft">{single
                      ? <TradesTable key={selected.id + ":" + selected.state} api={sapi} runId={selected.id} currency={work.currency} picked={trade?.id ?? state.strategyTrade ?? null} onPick={pick} during={state.strategyDrawdown ?? null} from={work.heldOut?.from ?? null} onClearDuring={() => update({strategyDrawdown: undefined})} extra={<MaxButton id="trades" maxed={maxed} onMax={setMaxed} />} />
                      : <><div className="fx-btabs" role="tablist">{bottomTabs.map(item => <button key={item.id} type="button" role="tab" className="fx-tab" aria-selected={shownTab === item.id} onClick={() => update({strategyTab: item.id})}>
                        {item.label}{item.count !== undefined && <span className="fx-cnt">{item.count}</span>}</button>)}</div>
                        <div className="fx-bsc">{shownTab === "rebalances" ? <Holdings.Rebalances api={sapi} runId={selected.id} during={state.strategyDrawdown ?? null} onClearDuring={() => update({strategyDrawdown: undefined})} />
                          : shownTab === "trades" ? <TradesTable api={sapi} runId={selected.id} currency={work.currency} picked={trade?.id ?? null} onPick={pick} during={null} from={null} onClearDuring={() => update({strategyDrawdown: undefined})} />
                          : shownTab === "holdings" ? <Holdings.Positions work={work} />
                          : shownTab === "contributions" && work.multi ? <Holdings.Contributions rows={work.multi.byInstrument} currency={work.currency} /> : null}</div></>}</div>
                    <div className="qx-botlog"><LogList api={sapi} runId={selected.id} from={work.heldOut?.from ?? null} trade={trade?.id ?? state.strategyTrade ?? null} kinds={kinds} onKinds={setKinds} onTrade={id => update({strategyTrade: id})} extra={<MaxButton id="log" maxed={maxed} onMax={setMaxed} />} /></div>
                  </div>
                </>}
              </>}
            </div></>}
      </div>
      {codeShown && <div className="fx-vsplit" role="separator" aria-label={t("Resize the code")} onPointerDown={dragSide} />}
      {codeShown && <section className="fx-code" aria-label={t("Code")} style={{width: `calc(${side * 100}% + ${state.strategyTree ? 150 : 0}px)`}}>
        <CodeArea status={codeStatus} ctx={ctx} projectId={detail.developer?.projectId ?? null} rootUri={root} treeTitle={detail.name} entryUri={detail.entryUri} marks={marks} line={codeLine?.line} request={codeLine?.request}
          tree={state.strategyTree === true} onTree={open => update({strategyTree: open})} onHide={() => update({side: undefined})}
          onOpenDeveloper={() => void ctx.platform.panels.openTarget({module: "developer", panelType: "main", objectId: `file:${detail.entryUri}`}).catch(fail)} onChanged={change => {if (!change.dirty) void api.read(detail.id).then(onDetail, fail);}} />
      </section>}
    </div>
    {validation.confirms}
  </div>;
}

/** A patch put into the effective settings as the column shows them while it saves. */
function mergeEffective(effective: RunSettings, patch: Record<string, any>): RunSettings {
  const next: Record<string, any> = {...effective};
  for (const [key, value] of Object.entries(patch)) next[key] = (key === "exchange" || key === "costs") && value ? {...(next[key] ?? {}), ...value} : value;
  return next as RunSettings;
}
/** A run's settings as the layer the object stores: only what differs from the defaults. */
function stored_of(run: RunSettings, view: RunSettingsView | null): Record<string, any> {
  if (view === null) return run as unknown as Record<string, any>;
  const out: Record<string, any> = {};
  for (const key of ["capital", "maxPosition", "fill", "shorting", "halted", "benchmark", "riskFree", "heldOutFrom", "limitThroughTicks", "accounts"] as const) {
    if (JSON.stringify((run as any)[key]) !== JSON.stringify((view.defaults as any)[key]) && (run as any)[key] !== undefined && (run as any)[key] !== null) out[key] = (run as any)[key];
  }
  for (const group of ["exchange", "costs"] as const) {
    const part = Object.fromEntries(Object.entries((run as any)[group] ?? {}).filter(([name, value]) => name !== "maxLeverage" && name !== "feeSource" && value !== null && JSON.stringify(value) !== JSON.stringify((view.defaults as any)[group]?.[name])));
    if (Object.keys(part).length) out[group] = part;
  }
  return out;
}
export { plain };
