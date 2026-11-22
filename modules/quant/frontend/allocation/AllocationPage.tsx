import { intlLocale } from "@talyx/sdk";
/* An allocation's page: one row on top (name, data, Run, Work or Results), the settings column, and in the middle either Work (key numbers, this period beside every period's stack, checks,
   runs) or Results (tabs). The state of every run comes from the backend; this period is a trial of the newest period whenever the settings differ from the run shown. */
import { t } from "../i18n";
import { restoreNotice } from "../shared/restored";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { TxButton, TxEmpty, TxIcon, TxIconButton, TxMenu, TxRunButton, TxRunProgress, TxSegmented, TxTag, TxRunPicker, TxRegionHead, TxRunStrip, TxCompare, TxSkeleton, useTxToast, type ModuleContext, type TxMenuItem } from "@talyx/sdk";
import { DataSettingsChip } from "../DataSettings";
import { dataReady, periodText, useEvent, useThrottled, type Api, type DataDefaults, type ObjectDetail, type ParamValue } from "../objects";
import type { ShellState } from "../QuantShell";
import { dayText, elapsed, mdy, when } from "../factor/format";
import { allocationApi, isActive, usable, type AllocationRun, type Change, type Prep, type Published, type Row, type RunView, type Tab, type Trial, type Work as WorkData } from "./api";
import { HeldView } from "./HeldView";
import { Work, StripActions, changePlain, changeWriter } from "./Work";
import { HistoryChart, PeriodTable, ReviewBox, TradesTable, trialStatus } from "./ThisPeriod";
import { Results } from "./Results";
import { differsFrom } from "./differs";
import { PoolSheet, Settings, listValues } from "./Settings";
import { PublishSheet } from "./PublishSheet";
import { SweepDialog, SweepGrid, sweepSummary } from "./SweepGrid";
import { CodeArea } from "../shared/CodeArea";
import { DataNotes } from "../shared/DataNotes";
import { FirstRun, NoResultsYet, type FirstLine } from "../shared/FirstRun";
import { HeldOutConfirm } from "../strategy/validation/HeldOutConfirm";
import { nf, pct } from "../shared/format";
import { LongRunConfirm, isLong, sweepSeconds } from "../shared/LongRun";
import "../factor/factor.css";
import "./allocation.css";

/** Settings the newest period alone cannot show: they only matter over the whole run. */
/** The assets whose prices at `date` are their proxy's: the run's notes read "IEF before Jul 2002 from VFITX". */
export const proxiedAt = (notes: string[], date: string) => new Set(notes.flatMap(note => {
  const found = /^(\S+) before (\w{3} \d{4}) from /.exec(note);
  return found && date < new Date(Date.parse(`1 ${found[2]} UTC`)).toISOString().slice(0, 10) ? [found[1]] : [];
}));
const RUN_ONLY = new Set(["frequency", "costs", "cost_multiple", "cost_aware", "pool", "capital"]);
const progressText = (run: AllocationRun | null) => run?.progress?.of ? {stage: t("Optimizing period"), count: `${run.progress.done}/${run.progress.of}`, value: run.progress.done / run.progress.of}
  : {stage: run?.state === "queued" ? t("Waiting in the queue") : t("Fetching prices"), count: undefined, value: null};

/** When the newest period is worked out fresh rather than read from the shown run: no run yet, settings changed since it (a trial), a held weight, a failed run, or a
    published period (waiting for review or decided); the version's period is compared with this method's weights for that same period, never with the run's last one. */
export const trialWanted = (s: {pool: number; hasRun: boolean; edits: number; pinned: boolean; failed: boolean; reviewPending: boolean}) =>
  s.pool > 0 && (!s.hasRun || s.edits > 0 || s.pinned || s.failed || s.reviewPending);
/** The trial is there only for the review: nothing was changed, so it is the target of the reviewed period, not a trial against the run. */
export const reviewOnly = (s: {hasRun: boolean; edits: number; pinned: boolean; failed: boolean; reviewPending: boolean}) =>
  s.reviewPending && s.hasRun && s.edits === 0 && !s.pinned && !s.failed;

/** What differs from a run outside the code settings: its assets (by ticker) and its dates (start, held-out start, and the end when one is set). */
export function outsideChanges(settings: {data: Record<string, any>; pool: {ticker: string}[]}, pool: {ticker: string}[] | null, data: Record<string, any>): string[] {
  const out: string[] = [];
  if (pool !== null && pool.map(row => row.ticker).join(",") !== (settings.pool ?? []).map(row => row.ticker).join(",")) out.push("assets");
  const was = settings.data ?? {};
  const day = (value: unknown) => value ? String(value).slice(0, 10) : "";
  if (day(was.since) !== day(data.since) || day(was.heldOutFrom) !== day(data.heldOutFrom) || (!data.untilLatest && day(was.until) !== day(data.until))) out.push("dates");
  return out;
}

export function AllocationPage({ctx, api, detail, onDetail, state, update, title, menu}: {ctx: ModuleContext; api: Api; detail: ObjectDetail; onDetail(detail: ObjectDetail): void;
  state: ShellState; update(patch: Partial<ShellState>): void; title: ReactNode; menu: TxMenuItem[]}) {
  const aapi = useMemo(() => allocationApi(ctx), [ctx]);
  const toast = useTxToast();
  const [error, setError] = useState<string | null>(null);
  const fail = (reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason));
  const data = detail.settings.data;
  // with no asset in the pool there is nothing to run, so there is no Run.
  const pooled = dataReady(data) && (data.symbols ?? []).length > 0;
  const view = state.allocationView === "results" ? "results" : "work";
  const settingsKey = JSON.stringify([detail.settings.data]);

  // ---- the preparation: declared settings, the pool, what each objective accepts
  const [prep, setPrep] = useState<Prep | null>(null);
  const loadPrep = () => aapi.prep(detail.id).then(setPrep, fail);
  useEffect(() => {void loadPrep();}, [detail.id, settingsKey]);
  const [values, setValues] = useState<Record<string, any>>({});
  useEffect(() => {if (prep) setValues(current => Object.keys(current).length && current.__id === detail.id ? current : {...prep.values, __id: detail.id});}, [prep?.objectId, detail.id]);
  const saving = useRef<ReturnType<typeof setTimeout> | null>(null);
  const edit = (name: string, value: ParamValue) => {
    saveValues({...values, [name]: value}, name);
  };
  const saveValues = (next: Record<string, any>, name?: string) => {
    setValues(next);
    // a name the system gave (its objective's) follows the objective; a name the user wrote stays.
    if ((name === "objective" || name === "risk_measure") && detail.name === nameOf(values) && nameOf(next) && nameOf(next) !== detail.name)
      void api.update(detail.id, {name: nameOf(next)}).then(() => api.read(detail.id)).then(onDetail, fail);
    if (saving.current) clearTimeout(saving.current);
    saving.current = setTimeout(() => {
      const {__id, ...params} = next;
      void api.settings(detail.id, {params}).then(onDetail, fail).then(loadPrep);
    }, 500);
  };

  // ---- runs
  const [runs, setRuns] = useState<AllocationRun[] | null>(null);
  const loadRuns = useCallback(() => aapi.runs(detail.id).then(page => setRuns(page.items), fail), [aapi, detail.id]);
  useEffect(() => {setRuns(null); void loadRuns();}, [detail.id]);
  // A running run reports several times a second: the list is read once a second at most. What the user does reads at once.
  const runsChanged = useThrottled(() => void loadRuns());
  useEvent(ctx, "quant.object_run_changed", payload => {if ((payload as {objectId?: string}).objectId === detail.id) runsChanged();}, () => void loadRuns());
  const list = runs ?? [];
  // ---- the code: the standard flow as the run ran it, read-only; a project of its own opens in the code area
  const [snap, setSnap] = useState<{id: string; text: string} | null>(null);
  const [tree, setTree] = useState(false);
  const selected = list.find(item => item.id === state.runId) ?? list[0] ?? null;
  const ownCode = detail.hasCode === true && prep?.mode === "code";
  const snapRun = selected && selected.state === "completed" && !ownCode ? selected : null;
  const codeShown = (ownCode || snapRun !== null) && state.side === "code";
  useEffect(() => {
    if (!codeShown || snapRun === null) return;
    let live = true;
    void aapi.code(snapRun.id).then(found => {if (live) setSnap({id: snapRun.id, text: found.files.map(item => item.text).join("\n\n")});}, fail);
    return () => {live = false;};
  }, [codeShown, snapRun?.id]);
  const active = list.find(isActive) ?? null;
  const [runView, setRunView] = useState<RunView | null>(null);
  useEffect(() => {
    let live = true;
    if (selected === null) {setRunView(null); return;}
    const read = () => aapi.run(selected.id).then(value => {if (live) setRunView(value);}, fail);
    void read();
    const timer = isActive(selected) ? setInterval(() => void read(), 1500) : undefined;
    return () => {live = false; if (timer) clearInterval(timer);};
  }, [selected?.id, selected?.state]);
  const [baseView, setBaseView] = useState<{id: string; work: WorkData | null} | null>(null);
  useEffect(() => {   // a held-out check shows the checks of the run it scored
    const of = selected?.runKind === "heldout" ? selected.of : null;
    if (!of) {setBaseView(null); return;}
    let live = true;
    void aapi.run(of).then(value => {if (live) setBaseView({id: of, work: value.work});}, reason => {if (live) fail(reason);});  // a failed read says so
    return () => {live = false;};
  }, [selected?.id]);
  const run = runView && runView.run.id === selected?.id ? runView.run : null;
  const work = runView && runView.run.id === selected?.id ? runView.work : null;
  // While a run is under way the period beside it is the last finished run's, as the draft keeps it.
  const keptWork = useRef<WorkData | null>(null);
  if (work) keptWork.current = work;
  const periodWork = work ?? (selected && isActive(selected) ? keptWork.current : null);
  const base = run?.settings.params ?? null;
  const changed = useMemo(() => differsFrom(Object.fromEntries(Object.entries(values).filter(([key]) => key !== "__id")), base), [values, base]);
  const trialKeys = [...changed].filter(key => !RUN_ONLY.has(key));
  const otherChanges = useMemo(() => run ? outsideChanges(run.settings, prep?.pool ?? null, data) : [], [run, prep, data]);
  const revert = () => {if (base) saveValues({...base, __id: detail.id});};
  const revertRow = (...names: string[]) => {
    if (!base) return;
    setLists(current => Object.fromEntries(Object.entries(current).filter(([name]) => !names.includes(name))));
    saveValues({...values, ...Object.fromEntries(names.map(name => [name, base[name]]))});
  };
  const [restored, setRestored] = useState<{id: string; note: string} | null>(null);
  const useVersion = async (id: string) => {
    if (saving.current) clearTimeout(saving.current);
    const next = await aapi.apply(id, detail.version);
    onDetail(next);
    const found = await aapi.prep(detail.id);
    setPrep(found);
    setValues({...found.values, __id: detail.id});
    setLists({});
    const note = restoreNotice(next);
    setRestored({id, note});
  };

  // ---- this period: the trial of the newest period (only the latest of overlapping requests is shown)
  const [trial, setTrial] = useState<{trial: Trial | null; error: string | null; busy: boolean}>({trial: null, error: null, busy: false});
  const [pin, setPin] = useState<{asset: string; weight: number} | null>(null);
  const [published, setPublished] = useState<Published>({});
  const loadPublished = () => aapi.published(detail.id).then(setPublished, () => setPublished({}));
  const trialCase = {pool: prep?.pool.length ?? 0, hasRun: selected !== null, edits: trialKeys.length, pinned: pin !== null, failed: run?.state === "failed",
    // the published period, reviewed or not: its v1 column is only ever set beside this method's weights for that same period
    reviewPending: published.versionId !== undefined && published.period !== undefined};
  const trialOn = prep !== null && trialWanted(trialCase);
  const forReview = reviewOnly(trialCase);
  useEffect(() => {
    if (!trialOn || !Object.keys(values).length) {setTrial({trial: null, error: null, busy: false}); return;}
    let live = true;
    setTrial(current => ({...current, busy: true}));
    const timer = setTimeout(() => {
      const {__id, ...params} = values;
      const changes = base ? Object.fromEntries(trialKeys.map(key => [key, params[key]])) : params;
      void aapi.trial(detail.id, {...changes}, pin ?? undefined, published.weights, trialCase.reviewPending ? published.period : undefined, base ? selected?.id : undefined).then(found => {if (live && !found.superseded) setTrial({trial: found, error: null, busy: false});},
        reason => {if (live) setTrial({trial: null, error: reason instanceof Error ? reason.message : String(reason), busy: false});});
    }, 450);
    return () => {live = false; clearTimeout(timer);};
  }, [trialOn, JSON.stringify(values), pin?.asset, pin?.weight, selected?.id, prep?.pool.length, JSON.stringify(published.weights ?? null), trialCase.reviewPending ? published.period : null]);

  // ---- an earlier period picked in the every-period chart
  const [picked, setPicked] = useState<{date: string; index: number} | null>(null);
  const [past, setPast] = useState<Row[] | null>(null);
  const [pickedTurnover, setPickedTurnover] = useState<number | null>(null);
  useEffect(() => {setPicked(null); setFocus(null);}, [selected?.id]);
  const pickDate = (date: string) => {
    if (!selected || !work) return;
    const stamp = date.slice(0, 10);
    const index = Math.max(0, (work.periodDates ?? []).findIndex(day => day >= stamp));
    void aapi.period(selected.id, index).then(row => {
      setPicked({date: row.heldFrom, index});
      setPickedTurnover(row.turnover);
      setPast(Object.keys(row.weights).map(asset => ({asset, group: run?.settings.pool.find(item => item.ticker === asset)?.group ?? null, weight: row.weights[asset], riskShare: row.riskShare[asset],
        published: null, bound: (row.bound[asset] as Row["bound"]) ?? null})));
    }, fail);
  };
  const [periodView, setPeriodView] = useState<"weights" | "trades">("weights");
  // Trades of the run's last rebalance, shown when the period in view has no holding before it to trade from (a review period after the run's last one).
  const [lastTrades, setLastTrades] = useState(false);
  useEffect(() => setLastTrades(false), [selected?.id, periodView]);
  const [by, setBy] = useState<"group" | "asset">("group");
  const [poolOpen, setPoolOpen] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);
  const [comparedId, setComparedId] = useState<string | null | undefined>(undefined);
  const previous = selected ? list.find(item => item.number < selected.number && usable(item)) ?? null : null;
  const compared = (comparedId === undefined ? previous : list.find(item => item.id === comparedId && usable(item))) ?? null;
  const comparePicker = <TxCompare run={compared ? `#${compared.number}` : null} items={[{id: "none", label: t("No comparison"), checked: compared === null, onSelect: () => setComparedId(null)},
    ...list.filter(item => usable(item) && item.id !== selected?.id).map(item => ({id: item.id, label: `#${item.number}`, detail: when(item.createdAt), checked: compared?.id === item.id, onSelect: () => setComparedId(item.id)}))]} />;
  useEffect(() => {void loadPublished();}, [detail.id]);
  const [publishOpen, setPublishOpen] = useState(false);
  const [sweepOpen, setSweepOpen] = useState(false);
  // A sweep expected to take more than ten minutes (its runs one after another, each as long as the newest backtest) waits for the user's word.
  const [long, setLong] = useState<{count: number; seconds: number; go(): void} | null>(null);
  const unlessLong = (count: number, go: () => void) => {
    const seconds = sweepSeconds(list.filter(item => !item.sweep && item.runKind === "backtest"), count);
    if (isLong(seconds)) setLong({count, seconds: seconds!, go}); else go();
  };
  const [shocksAdded, setShocksAdded] = useState(0);
  const poolRows = prep?.pool ?? [];
  const shockTargets = Array.from(new Set([...poolRows.map(item => item.ticker), ...poolRows.map(item => item.group)]));
  const addShock = (target: string, pct: number) => {
    const shown = `${target} ${pct > 0 ? "+" : "−"}${Math.abs(pct)}%`;
    if (!selected || !usable(selected)) {
      edit("shocks", [...(Array.isArray(values.shocks) ? values.shocks : []), {target, pct}] as unknown as ParamValue);
      toast({title: t("{shown} added", { shown }), detail: t("It shows in the table from the next run")});
      return;
    }
    void aapi.shock(selected.id, target, pct).then(() => api.read(detail.id)).then(onDetail).then(() => aapi.prep(detail.id)).then(found => {
      setPrep(found); setValues({...found.values, __id: detail.id}); setShocksAdded(count => count + 1); toast({title: t("{shown} added", { shown })});
    }, fail);
  };
  const [links, setLinks] = useState<Awaited<ReturnType<typeof aapi.links>> | null>(null);
  // Lists typed into number settings ("126, 252, 504"): the run button becomes "Sweep N" and starts them as a sweep.
  const [lists, setLists] = useState<Record<string, string>>({});
  useEffect(() => setLists({}), [detail.id]);
  const setList = (name: string, text: string | undefined) => setLists(current => {const {[name]: _gone, ...rest} = current; return text === undefined ? rest : {...rest, [name]: text};});
  const sweepAxes = Object.entries(lists).map(([name, text]) => ({name, values: listValues(text)})).filter(axis => axis.values.length >= 2);
  const sweepCount = sweepAxes.reduce((total, axis) => total * axis.values.length, 1);
  const [held, setHeld] = useState<Awaited<ReturnType<typeof aapi.heldStatus>> | null>(null);
  const [reason, setReason] = useState("");
  const withPublished = (rows: Row[] | null): Row[] | null => rows && published.weights ? rows.map(row => ({...row, published: published.weights![row.asset] ?? 0})) : rows;
  const [focus, setFocus] = useState<string | null>(null);
  const goTo = (where: string) => {
    if (where.startsWith("r:")) {update({allocationView: "results", allocationResults: where.slice(2)}); return;}
    if (where.startsWith("p:")) {   // a check opens the period it names, its asset selected
      const [, date, asset] = where.split(":");
      update({allocationView: undefined}); pickDate(date); setFocus(asset || null);
      window.setTimeout(() => document.querySelector(".al-periodsec")?.scrollIntoView({block: "start", behavior: "smooth"}), 50);
      return;
    }
    update({allocationView: undefined});
    const id = where.slice(4);
    setFlash(id); window.setTimeout(() => setFlash(null), 1400);
    window.setTimeout(() => document.getElementById(`al-g-${id}`)?.scrollIntoView({block: "start", behavior: "smooth"}), 50);
  };

  // ---- the head
  const [defaults, setDefaults] = useState<{value: DataDefaults | null; error: string | null}>({value: null, error: null});
  useEffect(() => {
    let live = true;
    if (!dataReady(data) || !(data.symbols ?? []).length) {setDefaults({value: null, error: null}); return;}
    void api.defaults(detail.id).then(value => {if (live) setDefaults({value, error: null});}, reason => {if (live) setDefaults({value: null, error: reason instanceof Error ? reason.message : String(reason)});});
    return () => {live = false;};
  }, [detail.id, settingsKey]);
  const [dataOpen, setDataOpen] = useState(false);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {if (active === null) return; setNow(Date.now()); const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer);}, [active?.id]);
  const start = async () => {
    setError(null);
    try {
      if (saving.current) {clearTimeout(saving.current); const {__id, ...params} = values; onDetail(await api.settings(detail.id, {params}));}
      if (sweepAxes.length > 0) {
        if (sweepAxes.length > 2) {setError(t("A sweep takes one or two settings with several values")); return;}
        const axes = sweepAxes;
        unlessLong(sweepCount, () => void aapi.sweep(detail.id, axes).then(found => {setLists({}); update({runId: found.runs[0].id, allocationView: undefined}); return loadRuns();}).catch(fail));
        return;
      }
      const row = await aapi.start(detail.id, {});
      update({runId: row.id, allocationView: undefined});
      await loadRuns();
    } catch (reason) {
      fail(reason);
    }
  };
  // The head's data chip, the draft's: the analysis period, the year held out, how fresh the prices are.
  const heldYear = data.heldOutFrom ? new Date(Date.parse(data.heldOutFrom + "T00:00:00Z")).getUTCFullYear() : null;
  const analysisEnd = data.heldOutFrom ? new Date(Date.parse(data.heldOutFrom + "T00:00:00Z") - 86_400_000).toISOString().slice(0, 10) : data.until;
  const period = data.since && analysisEnd ? periodText(data.since, analysisEnd) : null;
  const dataTo = selected?.data?.dataTo ? Date.parse(selected.data.dataTo + "T00:00:00Z") : null;
  const chip = <button type="button" className="tx-datachip" title={t("Data settings")}><TxIcon name="list" />
    <b data-priority={1}>{period ?? t("Set data")}</b>
    {heldYear !== null && <><span className="tx-sep" /><span data-priority={2} className="tx-lock"><TxIcon name="lock" />{t("{heldYear} on", { heldYear })}</span></>}
    {dataTo !== null && <><span className="tx-sep" /><span data-priority={3} className="fx-t3">{t("data to {dataTo}", { dataTo: mdy(dataTo).replace(/, \d{4}$/, "") })}</span></>}
    <TxIcon name="chevron" style={{transform: "rotate(90deg)"}} /></button>;
  const progress = progressText(active !== null && run?.id === active.id ? run : active);
  const write = useMemo(() => changeWriter((prep?.groups ?? []).flatMap(group => group.settings)), [prep]);
  const goTab = (tab: Tab) => update({allocationView: "results", allocationResults: tab});
  const tab = (state.allocationResults as Tab | undefined) ?? "summary";
  const lead = picked ? t("Target") : trial.trial && trialOn && !forReview ? t("Trial") : t("Target");
  const showing: Row[] | null = withPublished(picked ? past : trialOn && trial.trial ? trial.trial.rows : periodWork?.thisPeriod ?? null);
  // The period under review is the newest one, not the run's last: until its trial answers, nothing stands in for it (showed 2024's weights first).
  const reviewWaits = !picked && forReview && !trial.trial && !trial.error;
  const baseRows = !picked && trialOn && !forReview && trial.trial && work ? work.thisPeriod : null;
  // The trial's tag names its settings as "what changed" does ("each asset 10%", it read "max_weight 10, group_limits …").
  const edits = trialKeys.map(key => write({name: key, from: "", to: values[key]} as Change)).filter(item => item !== null).map(item => `${item!.label} ${item!.to}`);
  /** The name the system gives an allocation: its objective, a minimum risk by its measure ("Minimum variance"). */
  const nameOf = (v: Record<string, any>) => {
    const say = (key: string) => optionLabels[key]?.[String(v[key])] ?? "";
    return v.objective === "min_risk" && say("risk_measure") ? t("Minimum {measure}", { measure: say("risk_measure").toLowerCase() }) : say("objective");
  };
  const optionLabels = useMemo(() => Object.fromEntries((prep?.groups ?? []).flatMap(group => group.settings).filter(item => item.options)
    .map(item => [item.name, Object.fromEntries(item.options!.map(option => [String(option.value), option.label]))])), [prep]);

  const strip = selected && <TxRunStrip picker={<>{view === "results" && <TxButton variant="quiet" size="sm" onClick={() => update({allocationView: undefined})}><TxIcon name="chevron" style={{transform: "rotate(180deg)"}} />{t("Work")}</TxButton>}<TxRunPicker run={`#${selected.number}`} items={list.map(item => ({id: item.id, label: `#${item.number}`, detail: changePlain(item, write).slice(0, 60), checked: item.id === selected.id, onSelect: () => update({runId: item.id})}))} /></>}
    when={when(selected.createdAt)} meta={[run?.data?.dataFrom ? `${dayText(run.data.dataFrom)} – ${dayText(run.data.dataTo)}` : "", sweepSummary(list, selected) ?? (selected.runKind === "heldout" && work?.holdout ? t("held-out of #{number}, {start} – {end}, scored once", { number: list.find(item => item.id === selected.of)?.number ?? "", start: dayText(work.holdout.from), end: dayText(work.holdout.to) }) : "")].filter(Boolean)}
    tags={<>{selected.sweep && <TxTag>{t("Sweep")}</TxTag>}{selected.state === "failed" && <TxTag tone="fix">{t("Failed")}</TxTag>}{(selected.state === "cancelled" || selected.state === "interrupted") && <TxTag tone="muted">{t("Stopped")}</TxTag>}{selected.runKind === "heldout" && <TxTag icon="lock">{t("Held-out")}</TxTag>}{view === "work" && selected.runKind !== "heldout" && <DataNotes notes={work?.notes ?? []} />}</>}
    compare={comparePicker}
    actions={<StripActions inResults={view === "results"} starred={selected.starred} onStar={() => void aapi.annotate(selected.id, {starred: !selected.starred}).then(() => loadRuns(), fail)}
      onResults={() => update({allocationView: view === "work" ? "results" : undefined})} onPublish={usable(selected) && selected.runKind === "backtest" ? () => setPublishOpen(true) : undefined}
      onNotebook={view === "results" && usable(selected) ? () => void aapi.notebook(selected.id).then(opened => update({objectId: opened.notebookId, runId: undefined}), fail) : undefined} />} />;

  const reviewing = published.versionId !== undefined && published.period !== undefined && !published.review && !picked;
  const monthOf = (ms: number) => new Date(ms).toLocaleString(intlLocale(), {month: "long", year: "numeric", timeZone: "UTC"});
  const decide = (how: "accept" | "one_off") => {
    if (!published.versionId || published.period === undefined) return;
    const weights = how === "one_off" && trial.trial ? Object.fromEntries(trial.trial.rows.map(row => [row.asset, row.weight])) : undefined;
    void aapi.review(published.versionId, {period: published.period, how, weights, reason: how === "one_off" ? reason : undefined})
      .then(() => {setReason(""); setPin(null); void loadPublished(); toast({title: `${monthOf(published.period!)} ${how === "accept" ? t("accepted as computed") : t("marked as a one-off in the library")}`});}, fail);
  };
  const review = published.versionId !== undefined && published.period !== undefined ? <ReviewBox waiting={trial.busy || (edits.length > 0 || pin !== null) && !trial.trial} label={monthOf(published.period)} edits={reviewing ? [...edits, ...(pin ? [t("{asset} held at {weight}", { asset: pin.asset, weight: pct(pin.weight, 2) })] : [])] : []} reason={reason}
    decided={published.review ? (published.review.how === "accepted" ? t("{period}: v{version} accepted as computed", { period: monthOf(published.period), version: String(published.version) }) : t("{period}: one-off adjustment applied. {reason}", { period: monthOf(published.period), reason: published.review.reason ?? "" })) : null}
    computed={trial.trial && trialCase.reviewPending ? t("v{version} computed at the {date} close", { version: String(published.version), date: dayText(trial.trial.period).replace(/, \d{4}$/, "") }) : t("v{version}, held from {date}", { version: String(published.version), date: dayText(new Date(published.period).toISOString().slice(0, 10)).replace(/, \d{4}$/, "") })}
    since={trial.trial?.drift ? t("Since v{version}: volatility {volatility} against {target}; risk shares within {gap} pt", { version: String(published.version), volatility: pct(trial.trial.drift.publishedVol, 1), target: pct(trial.trial.drift.targetVol, 1), gap: nf(trial.trial.drift.riskShareGap * 100, 1) }) : null} onReason={setReason} onAccept={() => decide("accept")} onOneOff={() => decide("one_off")}
    onNewVersion={() => goTo("set:objective")} onDiscard={() => {revert(); setPin(null);}} /> : null;
  const groups = periodWork?.groups ?? Array.from(new Set((prep?.pool ?? []).map(item => item.group)));
  const costBps = Object.fromEntries((values.costs ?? []).map((row: {group: string; bps: number}) => [row.group, Number(row.bps) * Number(values.cost_multiple ?? 1)]));
  // What the data cannot give, from the run shown: prices, then each thing the run had to work around (missing closes, a shorter history, a proxy).
  const dataLimits: [string, string][] = prep ? [[t("Prices"), t("{adjustment} closes", { adjustment: data.adjustment === "raw" ? t("Raw") : t("Adjusted") })], ...(periodWork?.notes ?? []).map((note, index): [string, string] => [index === 0 && /missing closes/.test(note) ? t("Missing") : t("Note"), note])] : [];
  const lastRebalance = periodWork?.periodDates?.[periodWork.periodDates.length - 1] ?? null;
  const runTrades = periodWork && periodWork.thisPeriod.some(row => row.drifted != null) ? withPublished(periodWork.thisPeriod) : null;
  const showLast = !picked && periodView === "trades" && lastTrades && runTrades !== null;
  const periodNav = <span className="al-nav"><b>{picked ? dayText(picked.date) : showLast && lastRebalance ? dayText(lastRebalance) : trialOn && trial.trial ? dayText(trial.trial.period) : periodWork ? dayText(periodWork.to) : ""}</b></span>;
  // The period in view has no holding before it (the run's last rebalance is earlier, or there is no run): its trades are not known, so the run's last ones are offered.
  const tradesFrom = (rows: Row[]) => rows.some(row => row.drifted != null) ? <TradesTable rows={rows} groups={groups} lead={lead} capital={Number(values.capital) > 0 ? Number(values.capital) : 1_000_000} costBps={costBps} />
    : runTrades && lastRebalance && selected ? <TxEmpty title={t("{period} is after #{number}'s last rebalance, {lastRebalance}", { period: dayText(trialOn && trial.trial ? trial.trial.period : periodWork!.to), number: selected.number, lastRebalance: dayText(lastRebalance) })}
      action={<TxButton size="sm" onClick={() => setLastTrades(true)}>{t("Show {lastRebalance} trades", { lastRebalance: dayText(lastRebalance) })}</TxButton>} />
    : <TxEmpty title={t("Trades show after the first run")} />;
  const periodSection = showing ? <section className="al-periodsec">
    <div className="al-periodmain">
      <TxRegionHead title={picked || showLast ? t("Period") : t("This period")}>{periodNav}
        {!picked && <TxSegmented label={t("Show")} value={periodView} onChange={setPeriodView} options={[{value: "weights", label: t("Weights")}, {value: "trades", label: t("Trades")}]} />}
        {picked ? <span className="al-dim">{t("turnover {pickedTurnover}", { pickedTurnover: pct(pickedTurnover ?? 0, 1) })}</span> : showLast ? <span className="al-dim">{t("rebalance at the {lastRebalance} close", { lastRebalance: dayText(lastRebalance!) })}</span> : forReview ? (trial.trial ? <span className="al-dim">{t("rebalance at the {period} close", { period: dayText(trial.trial.period) })}</span> : null) : trialOn ? <><TxTag tone="watch" title={edits.length > 1 ? t("Recomputed the newest period only: {edits}", { edits: edits.join(", ") }) : t("Recomputed the newest period only")}>{t("Trial{value}", { value: edits.length === 1 ? `: ${edits[0]}` : edits.length ? t(": {count} settings", { count: edits.length }) : "" })}</TxTag>
          <span className="al-dim num">{trialStatus({busy: trial.busy, engineWarm: prep?.engineWarm, seconds: trial.trial?.wall}, Boolean(trial.trial))}</span></> : work ? <span className="al-dim">{t("rebalance at the {periodDates} close", { periodDates: dayText(work.periodDates?.[work.periodDates.length - 1] ?? work.to) })}</span> : null}
        {(picked || showLast) && <TxButton variant="quiet" size="sm" onClick={() => {setPicked(null); setFocus(null); setLastTrades(false);}}>{t("Back to this period")}</TxButton>}</TxRegionHead>
      <div className="qv-pad">{trial.error && trialOn && !picked ? <p className="fx-why" role="alert">{trial.error}</p>
        : reviewWaits ? <TxSkeleton width="60%" />
        : showLast ? <TradesTable rows={runTrades!} groups={groups} lead={t("Target")} capital={Number(values.capital) > 0 ? Number(values.capital) : 1_000_000} costBps={costBps} />
        : !picked && periodView === "trades" ? tradesFrom(showing)
        : <PeriodTable proxied={picked ? proxiedAt(work?.spliced ?? [], picked.date) : undefined} lead={lead} rows={showing} groups={groups} reference={showing.some(row => row.published != null)} referenceLabel={published.version ? `v${published.version}` : t("Published")} base={baseRows} baseLabel={selected ? `#${selected.number}` : ""}
          held={pin?.asset ?? null} onHold={picked ? undefined : asset => setPin(current => current?.asset === asset ? null : {asset, weight: Math.round((showing.find(row => row.asset === asset)?.weight ?? 0) * 1000) / 1000})}
          onHeldWeight={pin ? weight => setPin({asset: pin.asset, weight}) : undefined} focus={picked ? focus : null} />}
        {!picked && !showLast && trialOn && trial.trial && <div className="al-foot"><span>{t.rich("Effective assets {effectiveAssets}", { effectiveAssets: <b>{nf(trial.trial.effectiveAssets, 1)}</b> })}</span></div>}
        {!picked && !trialOn && periodWork && <div className="al-foot"><span>{t.rich("Periods {periods}", { periods: <b>{periodWork.periods}</b> })}</span><span>{t.rich("Effective assets {count}", { count: <b>{nf(1 / showing.reduce((s, r) => s + r.weight * r.weight, 0), 1)}</b> })}</span></div>}</div>
    </div>
    {periodWork && <HistoryChart by={by} onBy={setBy} figures={{group: periodWork.periodsGroupFigure, asset: periodWork.periodsFigure}} onSelect={pickDate} />}
  </section> : null;

  const firstLines: FirstLine[] | null = prep === null ? null : [
    {label: t("Assets"), value: prep.pool.length ? (prep.pool.length === 1 ? t("{count} asset in the pool", { count: prep.pool.length }) : t("{count} assets in the pool", { count: prep.pool.length })) : t("none yet"), missing: prep.pool.length === 0, onClick: () => setPoolOpen(true)},
    {label: t("Period"), value: period ? <>{period}{heldYear !== null && <span className="fx-t3">{t(", then held out")}</span>}</> : t("Set data"), missing: period === null, onClick: () => setDataOpen(true)},
    {label: t("Objective"), value: values.objective === "min_risk" && optionLabels.risk_measure?.[String(values.risk_measure)] ? t("Minimum {measure}", {measure: t(optionLabels.risk_measure[String(values.risk_measure)]).toLowerCase()}) : t(nameOf(values)) || "—", onClick: () => goTo("set:objective")},
    {label: t("Rebalance"), value: t(optionLabels.frequency?.[String(values.frequency)] ?? "—"), onClick: () => goTo("set:rebalance")},
  ];

  return <div className="qx-page fx-page">
    <div className="tx-panelhead fx-head">
      {title}<span className="fx-kind">{t("Allocation")}</span>
      <DataSettingsChip ctx={ctx} api={api} kind="allocation" alsoBuilt={null} data={data} defaults={defaults.value} defaultsError={defaults.error} boundary={null} open={dataOpen} onOpenChange={setDataOpen}
        onSave={async next => {onDetail(await api.settings(detail.id, {data: next}));}} trigger={chip} />
      <span className="tx-spacer" />
      {active !== null ? <TxRunProgress run={`#${active.number}`} stage={progress.stage} count={progress.count} value={progress.value} elapsed={elapsed((now - active.createdAt) / 1000)}
        onStop={() => void aapi.stop(active.id).then(() => loadRuns(), fail)} />
        : pooled && <TxRunButton label={sweepCount > 1 ? t("Sweep {sweepCount}", { sweepCount }) : t("Run backtest")} kinds={[{id: "run", label: t("Run backtest")}, {id: "sweep", label: t("Run a sweep…")}, {id: "heldout", label: t("Validate on held-out…")}]} onRun={() => void start()} onKind={id => {if (id === "sweep") setSweepOpen(true); if (id === "heldout") void aapi.heldStatus(detail.id, usable(selected) ? selected!.id : undefined).then(status => setHeld(status), fail);}} title={t("Run backtest (Ctrl+Enter)")} />}
      <TxSegmented label={t("Work or Results")} value={view} options={[{value: "work", label: t("Work")}, {value: "results", label: t("Results")}]} onChange={next => update({allocationView: next === "results" ? "results" : undefined})} />
      {(ownCode || snapRun !== null) && <button type="button" className="tx-codetoggle" aria-pressed={codeShown} onClick={() => update({side: codeShown ? undefined : "code"})}><TxIcon name="code" /><span>{codeShown ? t("Hide code") : t("Show code")}</span></button>}
      <TxMenu label={t("Allocation actions")} align="end" items={[...(selected?.state === "completed" ? [{id: "apply", label: t("Use this version"), onSelect: () => void useVersion(selected.id).catch(fail)}] : []), ...menu]} trigger={<TxIconButton label={t("Allocation actions")} icon="more" />} />
    </div>
    {restored?.note && restored.id === selected?.id && <div className="qx-note2" role="status">{restored.note}</div>}
    {error && <div className="qx-page-error" role="alert"><span>{error}</span><TxButton size="sm" variant="quiet" onClick={() => setError(null)}>{t("Dismiss")}</TxButton></div>}
    <div className="fx-desk"><div className="fx-view">
      {view === "results" ? (selected && usable(selected) ? <div className="fx-main"><Results facts={{mode: run?.settings.mode, heldFrom: run?.settings.data.heldOutFrom, dataTo: run?.settings.data.until, adjustment: run?.settings.data.adjustment, options: optionLabels}} shock={{targets: shockTargets, added: shocksAdded, onAdd: addShock}} api={aapi} run={selected} compared={compared} tab={tab} onTab={goTab} onPeriod={date => {update({allocationView: undefined}); pickDate(date);}} onSetting={group => goTo(`set:${group}`)} strip={strip} onValidate={run?.settings.data.heldOutFrom ? () => void aapi.heldStatus(detail.id, selected!.id).then(status => setHeld(status), fail) : undefined} /></div>
        : <div className="fx-main">{list.length === 0 && firstLines !== null ? <NoResultsYet lines={firstLines} toWork={() => update({allocationView: undefined})} /> : <p className="fx-empty">{t("No finished run yet")}</p>}</div>)
        : <>
          {prep ? <Settings dataLimits={dataLimits} prep={prep} values={values} changed={changed} otherChanges={otherChanges} runLabel={selected ? `#${selected.number}` : null} bad={run?.failure?.fields ?? []} flash={flash}
            lists={lists} onList={setList} commonStart={work?.from ? dayText(work.from) : null} heldOutFrom={data.heldOutFrom ? dayText(data.heldOutFrom) : null} heldOut={data.heldOutFrom ?? null}
            onHeldOut={day => void api.settings(detail.id, {data: {...data, heldOutFrom: day}}).then(onDetail, fail)} onValue={edit} onRevert={revert} onRevertRow={revertRow} onEditPool={() => setPoolOpen(true)} /> : <div className="fx-pad"><TxSkeleton width="40%" /></div>}
          <div className="fx-main qx-wmain">
            {selected === null && !trial.trial && !trial.busy && !trial.error ? firstLines === null ? <div className="fx-pad"><TxSkeleton width="40%" /></div> : <FirstRun lines={firstLines} /> : <>
              {strip}
              <Work lead={selected?.sweep && <SweepGrid runs={list} selected={selected.id} onSelect={id => update({runId: id})} onUse={id => void useVersion(id).catch(fail)} />} run={selected ? run ?? selected : null} metrics={work?.metrics ?? selected?.metrics ?? undefined} checks={work?.checks} runs={list} compared={compared} review={review} period={periodSection} notes={runView?.old && runView.run.id === selected?.id ? [runView.old] : []}
                held={selected?.runKind === "heldout" && work?.holdout ? <HeldView run={selected} work={work} base={list.find(item => item.id === selected.of) ?? null} baseWork={baseView?.id === selected.of ? baseView.work : null} onGo={goTo} /> : undefined}
                onRow={id => update({runId: id})} onStar={item => void aapi.annotate(item.id, {starred: !item.starred}).then(() => loadRuns(), fail)} onGo={goTo} write={write} /></>}
          </div></>}
      {codeShown && <section className="al-code" aria-label={t("Code")}>
        {snapRun !== null ? <><div className="al-snaphead"><span>{t("Standard flow as #{number} ran it, read-only", { number: snapRun.number })}</span><span className="tx-spacer" />
            <TxIconButton size="sm" label={t("Hide code")} icon="close" onClick={() => update({side: undefined})} /></div>
          <pre className="al-snap">{snap && snap.id === snapRun.id ? snap.text : ""}</pre></>
        : <CodeArea ctx={ctx} projectId={detail.developer?.projectId ?? null} rootUri={detail.entryUri.slice(0, detail.entryUri.lastIndexOf("/"))} treeTitle={detail.name} entryUri={detail.entryUri} tree={tree} onTree={setTree} onHide={() => update({side: undefined})}
          onOpenDeveloper={() => void ctx.platform.panels.openTarget({module: "developer", panelType: "main", objectId: `file:${detail.entryUri}`}).catch(fail)} onChanged={change => {if (!change.dirty) void api.read(detail.id).then(onDetail, fail);}} />}
      </section>}
    </div></div>
    {selected && <PublishSheet api={aapi} runId={selected.id} runName={detail.name} open={publishOpen} onClose={() => setPublishOpen(false)} onPublished={() => void loadPublished()} />}
    <SweepDialog open={sweepOpen} names={["lookback", "covariance", "risk_measure", "objective", "max_weight", "min_weight", "max_turnover",
      ...Array.from(new Set((values.pool ?? []).map((row: {group?: string}) => row.group).filter(Boolean) as string[])).map(group => `group_max:${group}`)]} onCancel={() => setSweepOpen(false)}
      onStart={axes => {setSweepOpen(false); unlessLong(axes.reduce((total, axis) => total * axis.values.length, 1),
        () => void aapi.sweep(detail.id, axes).then(found => {update({runId: found.runs[0].id, allocationView: undefined}); return loadRuns();}, fail));}} />
    {long !== null && <LongRunConfirm title={t("Run {count} backtests?", { count: long.count })} seconds={long.seconds} label={t("Run {count} backtests", { count: long.count })} onCancel={() => setLong(null)} onConfirm={() => {const {go} = long; setLong(null); go();}} />}
    <HeldOutConfirm open={held !== null} status={held} onCancel={() => setHeld(null)} onConfirm={() => {const status = held!; setHeld(null); void aapi.start(detail.id, {kind: "heldout", of: status.runId}).then(row => {update({runId: row.id, allocationView: undefined}); return loadRuns();}, fail);}} />
    {prep && <PoolSheet open={poolOpen} pool={prep.pool} rows={values.pool ?? []} align={String(values.align ?? "common")} currency={String(values.currency ?? "")} candidates={links}
      onClose={() => setPoolOpen(false)} onChange={rows => edit("pool", rows as unknown as ParamValue)} onSymbols={() => {setPoolOpen(false); setDataOpen(true);}}
      onAlign={value => edit("align", value)} onCurrency={value => edit("currency", value)} onOpenLinks={() => {void aapi.links(detail.id).then(setLinks, fail);}} />}
  </div>;
}
