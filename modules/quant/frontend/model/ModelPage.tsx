/* A model's page: the preparation on the left, the experiments and the selected run on the right (Work), or one run's Results. The preparation is the
   standard pipeline's settings; changing one saves it and, 400 ms after the last change, asks for the check before training (stale answers are dropped). */
import { t } from "../i18n";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { TxButton, TxDialog, TxEmpty, TxSkeleton, TxIcon, TxIconButton, TxMenu, TxRunButton, TxRunProgress, TxSegmented, TxSheet, useTxToast, type Json, type ModuleContext, type TxMenuItem, type TxRunKind } from "@talyx/sdk";
import { DataInline, DataSettingsChip, useIndexMaterials, useResearchLists } from "../DataSettings";
import { restoreNotice } from "../shared/restored";
import { CodeArea, fileUri, type CodeMark } from "../shared/CodeArea";
import { dataReady, universeText, useEvent, useThrottled, type Api, type ObjectDetail, type ParamValue } from "../objects";
import type { ShellState } from "../QuantShell";
import { LongRunConfirm, isLong } from "../shared/LongRun";
import { modelApi, type CheckAnswer, type DeepEnvironment, type HeldOutPlan, type ModelPrep, type ModelRun, type RunList, type Vary } from "./api";
import type { FactorFields } from "../factor/api";
import { ComparePane } from "./ComparePane";
import { Experiments } from "./Experiments";
import { FirstRun, NoResultsYet, type FirstLine } from "../shared/FirstRun";
import { Prep, nextEarlyStop, type Width } from "./Prep";
import { PublishSheet } from "./PublishSheet";
import { Selected, type Tab } from "./Selected";
import { Results, type ResultsTab } from "./results/Results";
import { ACTIVE, dayYear, headSpan, heldOutUses, latestOnly, minutes, pickSelected, prepWidth, runLabel, selHeight } from "./format";
import { at, route } from "../types";
import "./model.css";

const isActive = (run: ModelRun) => ACTIVE.includes(run.state);

export function ModelPage({ctx, api, detail, onDetail, state, update, title, menu}: {
  ctx: ModuleContext; api: Api; detail: ObjectDetail; onDetail(detail: ObjectDetail): void; state: ShellState; update(patch: Partial<ShellState>): void; title: ReactNode; menu: TxMenuItem[];
}) {
  const mapi = useMemo(() => modelApi(ctx), [ctx]);
  const toast = useTxToast();
  const [restored, setRestored] = useState<{id: string; note: string} | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dataOpen, setDataOpen] = useState(false);
  const fail = useCallback((reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason)), []);

  // ---------------------------------------------------------------- the preparation
  const [prep, setPrep] = useState<ModelPrep | null>(null);
  const [draft, setDraft] = useState<Record<string, ParamValue>>({});
  const [vary, setVary] = useState<Vary[]>([]);
  const loadPrep = useCallback(() => mapi.prep(detail.id).then(next => {setPrep(next); setDraft(current => Object.keys(current).length ? current : next.values);}, fail), [mapi, detail.id]);
  // A saved setting bumps the version and reloads the preparation in place: the panel stays up while it reloads (it can wait on Market), only another model clears it.
  const prepOf = useRef(detail.id);
  useEffect(() => {if (prepOf.current !== detail.id) {prepOf.current = detail.id; setPrep(null); setDraft({}); setVary([]);} void loadPrep();}, [detail.id, detail.version]);

  const useRun = async (id: string) => {
    if (timer.current) clearTimeout(timer.current);
    serial.current.next();
    const restored = await mapi.apply(id, detail.version);
    onDetail(restored);
    const next = await mapi.prep(detail.id);
    setPrep(next); setDraft(next.values); setVary([]);
    const note = restoreNotice(restored);
    setRestored({id, note});
    update({modelView: undefined} as Partial<ShellState>);
  };

  const [fields, setFields] = useState<FactorFields | null>(null);
  const dataKey = JSON.stringify(detail.settings.data);
  useEffect(() => {let live = true; void mapi.fields(detail.id).then(value => {if (live) setFields(value);}, reason => {if (live) fail(reason);}); return () => {live = false;};}, [mapi, detail.id, dataKey]);

  // ---------------------------------------------------------------- the runs
  const [list, setList] = useState<RunList | null>(null);
  const since = useRef<number | undefined>(undefined);
  const loading = useRef(false), again = useRef(false);
  const loadRuns = useCallback(async (full = false): Promise<void> => {
    if (loading.current) {again.current = true; return;}
    loading.current = true;
    try {
      const page = await mapi.runs(detail.id, full ? undefined : since.current);
      since.current = page.now - 2000;
      setList(current => {
        if (page.complete || current === null) return page;
        const byId = new Map(current.items.map(item => [item.id, item]));
        for (const item of page.items) byId.set(item.id, item);
        return {...page, items: [...byId.values()].sort((a, b) => b.number - a.number), batches: page.batches};
      });
    } catch (reason) {fail(reason);} finally {loading.current = false; if (again.current) {again.current = false; void loadRuns();}}
  }, [mapi, detail.id]);
  useEffect(() => {since.current = undefined; setList(null); void loadRuns(true);}, [detail.id]);
  // A running run reports several times a second: the list is read once a second at most. What the user does reads at once.
  const runsChanged = useThrottled(() => void loadRuns());
  useEvent(ctx, "quant.object_run_changed", payload => {if ((payload as {objectId?: string}).objectId === detail.id) runsChanged();}, () => void loadRuns(true));
  // "2 running, 1 queued" is the machine's queue, other objects' work and library updates too, and a slot is freed after its run's last event:
  // while it holds anything it is read again every few seconds, so the count does not stay at what it was when this page last heard of its own runs.
  const queueBusy = Boolean(list?.running && list.running.running + list.running.waiting > 0);
  useEffect(() => {if (!queueBusy) return; const timer = setInterval(() => void loadRuns(), 3000); return () => clearInterval(timer);}, [queueBusy, loadRuns]);
  const runs = list?.items ?? [];
  const selected = pickSelected(runs, state.runId);
  const active = runs.find(isActive) ?? null;
  const [compareId, setCompareId] = useState<string | null>(null);
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [view, setView] = useState<"table" | "heatmap">("table");
  const [comparing, setComparing] = useState<string[] | null>(null);
  const [filter, setFilter] = useState("all"), [query, setQuery] = useState(""), [sort, setSort] = useState("ric"), [byBatch, setByBatch] = useState(true);
  const baseRun = runs.find(run => run.id === compareId) ?? runs.find(run => run.status === "completed") ?? null;
  const [now, setNow] = useState(Date.now());
  useEffect(() => {if (!active) return; setNow(Date.now()); const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer);}, [active?.id]);
  void now;

  // ---------------------------------------------------------------- the check
  const [check, setCheck] = useState<CheckAnswer | null>(null);
  const [checking, setChecking] = useState(false);
  const serial = useRef(latestOnly()), newCard = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const checkNow = useCallback((params: Record<string, ParamValue>, groups: Vary[], save: boolean, data = detail.settings.data) => {
    if (timer.current) clearTimeout(timer.current);
    setChecking(true);
    timer.current = setTimeout(async () => {
      const mine = serial.current.next();
      try {
        if (save) onDetail(await api.settings(detail.id, {params}));
        if (!dataReady(data)) {setChecking(false); setCheck(null); return;}
        const answer = await mapi.check(detail.id, {vary: groups.filter(item => item.values.length)});
        if (serial.current.current(mine)) {
          setCheck(answer); setChecking(false);
          // choosing a card sets the batch size the card can hold, once, unless the person has changed it
          if (newCard.current && answer.batchDefault) {
            newCard.current = false;
            if (Number(params.nn_batch ?? 800) === 800) {const next = {...params, nn_batch: answer.batchDefault}; setDraft(next); onDetail(await api.settings(detail.id, {params: next})); checkNow(next, groups, false);}
          }
        }
      } catch (reason) {if (serial.current.current(mine)) {setChecking(false); setCheck(null); if (reason instanceof Error && !/Choose the names/.test(reason.message)) fail(reason);}}
    }, 400);
  }, [mapi, api, detail.id, dataKey]);
  // the deep learning environment: looked at when a neural network is chosen, and every second while it installs
  const [deep, setDeep] = useState<DeepEnvironment | null>(null);
  const chosen = prep?.algorithms.find(item => item.id === draft.algorithm);
  const deepState = deep?.installing?.state;
  useEffect(() => {
    if (!chosen?.deep) return;
    let live = true;
    const look = () => void mapi.deep().then(next => {if (live) setDeep(next);}, reason => {if (live) fail(reason);});
    look();
    const timer = deepState === "running" ? setInterval(look, 1000) : undefined;
    return () => {live = false; if (timer) clearInterval(timer);};
  }, [mapi, chosen?.deep, deepState]);
  const wasInstalling = useRef(false);
  useEffect(() => {
    if (deepState === "running") wasInstalling.current = true;
    else if (wasInstalling.current && deep?.installed) {wasInstalling.current = false; checkNow(draft, vary, false);}
  }, [deepState, deep?.installed]);
  const installDeep = (variant?: string) => void mapi.installDeep(variant).then(setDeep, fail);
  const cancelDeep = () => void mapi.cancelDeep().then(setDeep, fail);
  const first = useRef(true);
  useEffect(() => {if (prep && first.current) {first.current = false; checkNow(draft, vary, false);}}, [prep]);
  const change = (name: string, value: ParamValue) => {
    const next = {...draft, [name]: value};
    if (name === "nn_device") newCard.current = String(value).startsWith("cuda:");
    if (name === "algorithm" && prep) {
      // a neural network retrains every 6 months and a tree every 3, unless the person set it; the MLP's optimizer is SGD, the others' Adam
      const was = prep.algorithms.find(item => item.id === draft.algorithm), now = prep.algorithms.find(item => item.id === value);
      if (Boolean(now?.deep) !== Boolean(was?.deep)) {
        if (now?.deep && (draft.every_months ?? 3) === 3) next.every_months = 6;
        if (!now?.deep && draft.every_months === 6) next.every_months = 3;
      }
      if (now?.id === "mlp" && (draft.nn_optimizer ?? "adam") === "adam") next.nn_optimizer = "gd";
      if (was?.id === "mlp" && now?.id !== "mlp" && draft.nn_optimizer === "gd") next.nn_optimizer = "adam";
      const metric = nextEarlyStop(draft.nn_metric as string | undefined, was, now);
      if (metric) next.nn_metric = metric;
    }
    setDraft(next); checkNow(next, vary, true);
  };
  const changeVary = (next: Vary[]) => {setVary(next); checkNow(draft, next, false);};
  const saveRun = (next: {cost?: number; topN?: number}) => void api.settings(detail.id, {run: {...(prep?.run ?? {}), ...next}}).then(updated => {onDetail(updated); void loadPrep();}, fail);
  const saveHeldOut = (day: string | null) => void api.settings(detail.id, {data: {...(prep?.data ?? detail.settings.data), heldOutFrom: day}}).then(updated => {onDetail(updated); void loadPrep(); checkNow(draft, vary, false);}, fail);

  // ---------------------------------------------------------------- the panel and its dividers
  const [width, setWidth] = useState<Width | null>(null);
  const mode: Width = width ?? (list !== null && runs.length === 0 ? "wide" : "narrow");
  const [prepPx, setPrepPx] = useState(330), [selPx, setSelPx] = useState<number | null>(null);
  const desk = useRef<HTMLDivElement>(null), main = useRef<HTMLDivElement>(null);
  const dragPrep = (event: React.PointerEvent<HTMLDivElement>) => {
    const box = desk.current!.getBoundingClientRect(), target = event.currentTarget; target.setPointerCapture(event.pointerId);
    const move = (next: PointerEvent) => setPrepPx(prepWidth(next.clientX - box.left, box.width));
    const up = () => {target.removeEventListener("pointermove", move); target.removeEventListener("pointerup", up);};
    target.addEventListener("pointermove", move); target.addEventListener("pointerup", up);
  };
  const dragSel = (event: React.PointerEvent<HTMLDivElement>) => {
    const box = main.current!.getBoundingClientRect(), target = event.currentTarget; target.setPointerCapture(event.pointerId);
    const move = (next: PointerEvent) => setSelPx(selHeight(box.bottom - next.clientY, box.height));
    const up = () => {target.removeEventListener("pointermove", move); target.removeEventListener("pointerup", up);};
    target.addEventListener("pointermove", move); target.addEventListener("pointerup", up);
  };

  // ---------------------------------------------------------------- running
  const total = vary.reduce((count, item) => count * Math.max(1, item.values.length), 1);
  const blocked = check?.blocked ? (check.checks.find(item => item.state === "block")?.name ?? check.label?.error?.message ?? t("the check blocks sending")) : !prep || !draft.algorithm ? t("choose an algorithm first") : null;
  const [chosenKind, setKind] = useState<"train" | "batch" | "heldout" | "check">("train");
  const heldTarget = selected && selected.status === "completed" && !selected.heldOut ? selected : null;
  // Validate on held-out is offered only with a finished run not yet scored and a held-out period set; otherwise the button trains.
  const heldRun = prep?.data.heldOutFrom ? heldTarget : null;
  const kind = chosenKind === "heldout" && heldRun === null ? "train" : chosenKind;
  // A setting given several values sends a batch whichever was chosen, so the button says so.
  const shownKind = kind === "train" && total > 1 ? "batch" : kind;
  const prepFlex = mode === "narrow" ? {flex: `0 0 ${prepPx}px`} : mode === "wide" ? {flex: "0 0 58%"} : {flex: "1 1 auto"};
  const runKinds: TxRunKind[] = [
    // With several values only a batch can be sent, so the single run is not offered (8293ca6f's greyed item).
    ...(total > 1 ? [] : [{id: "train", label: t("Train"), hint: t("One run"), current: shownKind === "train"}]),
    {id: "batch", label: (total > 1 ? t("Train a batch · {total} groups", { total }) : t("Train a batch")), current: shownKind === "batch"},
    ...(heldRun !== null ? [{id: "heldout", label: t("Validate #{number} on held-out", { number: heldRun.number }), current: kind === "heldout"}] : []),
    {id: "check", label: t("Check only"), separated: true, current: kind === "check"},
  ];
  const [confirm, setConfirm] = useState<{run: ModelRun; plan: HeldOutPlan} | null>(null);
  const [long, setLong] = useState<{groups: number; seconds: number} | null>(null);
  // The run is made the moment Train is pressed and checks as its first step; until the answer comes the button takes no second press.
  const [sending, setSending] = useState(false);
  const send = () => {
    setSending(true);
    void api.settings(detail.id, {params: draft}).then(async updated => {
      onDetail(updated);
      const started = await mapi.train(detail.id, {vary: vary.filter(item => item.values.length)});
      void loadRuns();
      if (started.runs[0]) update({runId: started.runs[0].id});
    }).catch(fail).finally(() => setSending(false));
  };
  const go = () => {
    if (sending) return;
    if (kind === "check") {checkNow(draft, vary, true); return;}
    if (blocked !== null) {toast({title: t("Blocked: {blocked}", { blocked })}); return;}   // Train is never greyed: pressed while blocked, it says by what
    if (kind === "heldout" && heldTarget) {void mapi.heldOutPlan(heldTarget.id).then(plan => setConfirm({run: heldTarget, plan}), fail); return;}
    if (kind === "batch" && total < 2) {toast({title: t("Give any setting several values")}); document.getElementById("mx-row-batch")?.scrollIntoView(); return;}
    // Expected to take more than ten minutes (the check's estimate for the whole batch, or its longest run): it waits for the user's word.
    const longest = Math.max(0, ...(check?.batch?.groups ?? []).map(group => group.time ?? 0)), whole = Math.max(longest, check?.batch?.total ?? 0);
    if (isLong(whole)) {setLong({groups: check?.batch?.groups.length ?? 1, seconds: whole}); return;}
    send();
  };
  const stop = (run: ModelRun) => void mapi.stop(run.id).then(() => loadRuns(), fail);
  const batchOf = (run: ModelRun | null) => run?.batch ? runs.filter(item => item.batch === run.batch && isActive(item)) : [];
  const stopBatch = (run: ModelRun) => void mapi.stopBatch(run.batch!).then(() => loadRuns(), fail);
  const resume = (run: ModelRun, alone?: boolean, override?: Record<string, ParamValue>) => void mapi.resume(run.id, {alone, ...(override ? {override} : {})}).then(() => {
    if (override?.nn_batch) toast({title: (run.failure?.window && run.failure.window > 1 ? t("Window {window} starts over with batch size {nn_batch}; windows 1–{window2} are kept", { window: run.failure?.window ?? "", nn_batch: String(override.nn_batch), window2: run.failure.window - 1 }) : t("Window {window} starts over with batch size {nn_batch}", { window: run.failure?.window ?? "", nn_batch: String(override.nn_batch) }))});
    if (override?.nn_device === "cpu") toast({title: (run.failure?.window && run.failure.window > 1 ? t("Window {window} starts over on the CPU; windows 1–{window2} are kept", { window: run.failure?.window ?? "", window2: run.failure.window - 1 }) : t("Window {window} starts over on the CPU", { window: run.failure?.window ?? "" }))});
    void loadRuns();
  }, fail);
  const progress = active ? {stage: active.status === "queued" ? t("Waiting in the queue") : `${active.progress?.window ? (active.windowsTotal ? t("Window {window} of {total}", { window: active.progress.window, total: active.windowsTotal }) : t("Window {window}", { window: active.progress.window })) : t("Preparing")}${active.progress?.round ? t(" · round {round}", { round: active.progress.round }) : ""}`,
    count: active.progress?.left ? t("about {time} left", { time: minutes(active.progress.left) }) : undefined, value: active.progress?.fraction ?? null} : null;
    // ---------------------------------------------------------------- the code area: only a model written as code has a file
  const ownCode = detail.hasCode === true && prep?.mode === "code";
  // The code of a run that is not the newest (own code), or of any run (the standard pipeline's file as that run ran it): read-only, with its own head.
  const newest = Math.max(0, ...runs.map(item => item.number));
  const snapRun = selected && selected.status === "completed" && (prep?.mode === "standard" || (ownCode && selected.number !== newest)) ? selected : null;
  const codeShown = (ownCode || snapRun !== null) && state.side === "code";
  const [snap, setSnap] = useState<{id: string; files: {file: string; text: string}[]; pipeline: {version: string} | null} | null>(null);
  useEffect(() => {
    if (!codeShown || snapRun === null) return;
    let live = true;
    void mapi.code(snapRun.id).then(found => {if (live) setSnap({id: snapRun.id, ...found});}, fail);
    return () => {live = false;};
  }, [codeShown, snapRun?.id]);
  const [codeLine, setCodeLine] = useState<{line: number; request: string} | undefined>(undefined);
  const [sidePx, setSidePx] = useState<number | null>(null);
  const root = detail.entryUri.slice(0, detail.entryUri.lastIndexOf("/"));
  const failure = selected?.status === "failed" ? selected.failure : null;
  const marks: CodeMark[] = ownCode && failure?.file && failure.line ? [{uri: fileUri(root, failure.file), line: failure.line, message: (failure.message ?? failure.detail ?? "").split("\n")[0], title: t("Run #{number}", { number: selected!.number })}] : [];
  const goLine = (line: number) => {update({side: "code"}); setCodeLine({line, request: `${Date.now()}`});};
  const dragSide = (event: React.PointerEvent<HTMLDivElement>) => {
    const box = (event.currentTarget.parentElement as HTMLElement).getBoundingClientRect(), target = event.currentTarget; target.setPointerCapture(event.pointerId);
    const move = (next: PointerEvent) => setSidePx(Math.round(Math.min(box.width * .6, Math.max(300, box.right - next.clientX))));
    const up = () => {target.removeEventListener("pointermove", move); target.removeEventListener("pointerup", up);};
    target.addEventListener("pointermove", move); target.addEventListener("pointerup", up);
  };
  const [logOf, setLogOf] = useState<{run: ModelRun; text: string} | null>(null);
  const pageView: "work" | "results" = (state as {modelView?: string}).modelView === "results" ? "results" : "work";
  const showResults = (tab: Tab | ResultsTab) => update({modelView: "results", modelResults: tab} as Partial<ShellState>);

  // The head's data line, as the draft writes it: the universe, how often, the years runs train and test on, the held-out years behind a lock.
  const {items: lists} = useResearchLists(ctx, api);
  const indexes = useIndexMaterials(ctx);
  const data = detail.settings.data;
  const universe = universeText(data, lists, indexes.items);
  const span = headSpan(data);
  const dataTo = selected?.dataTo ?? null;
  const chip = <button type="button" className="tx-datachip" title={t("Data settings")} data-need={universe === null}>
    <TxIcon name="list" />{universe === null ? <b data-priority={1}>{t("No universe")}</b> : <b data-priority={1}>{universe}{span.often ? ` ${span.often}` : ""}</b>}
    {universe !== null && span.years && <><span className="tx-sep" /><span data-priority={2}>{span.years}</span></>}
    {universe !== null && span.held && <><span className="tx-sep" /><span data-priority={3} className="mx-held" title={span.held.title}><TxIcon name="lock" />{span.held.text}</span></>}
    <TxIcon name="chevron" style={{transform: "rotate(90deg)"}} />
  </button>;
  const pageMenu: TxMenuItem[] = [...menu];
  const firstLines: FirstLine[] = [
    {label: t("Names"), value: universe === null ? t("Set data") : `${universe}${span.often ? `, ${span.often}` : ""}`, missing: universe === null, onClick: () => setDataOpen(true)},
    {label: t("Years"), value: span.years ? <>{span.years}{span.held && <span className="mx-t3">{t(", then held out")}</span>}</> : t("Set data"), missing: !span.years, onClick: () => setDataOpen(true)},
    {label: t("Inputs"), value: Array.isArray(draft.inputs) && draft.inputs.length ? draft.inputs.length === 1 ? t("1 input") : t("{count} inputs", { count: draft.inputs.length }) : t("none yet"), missing: !(Array.isArray(draft.inputs) && draft.inputs.length)},
    {label: t("Algorithm"), value: chosen?.label ?? t("not chosen"), missing: !chosen},
    ...(blocked !== null && chosen ? [{label: t("Check"), value: blocked, missing: true}] : []),
  ];
  return <div className="qx-page mx-page" onKeyDownCapture={event => {if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {event.preventDefault(); event.stopPropagation(); go();}}}>
    <div className="tx-panelhead mx-head">{title}<span className="mx-kind">{t("Model")}</span>
      <DataSettingsChip ctx={ctx} api={api} kind="model" data={detail.settings.data} defaults={null} defaultsError={null} boundary={null} open={dataOpen} onOpenChange={setDataOpen}
        onSave={async next => {onDetail(await api.settings(detail.id, {data: next})); void loadPrep(); checkNow(draft, vary, false, next);}} trigger={chip}>
        <div className="mx-dpop">
          <div className="mx-dr"><span>{t("Held out")}</span><span>{span.held ? <><TxIcon name="lock" />{t("from {title}", { title: dayYear(Date.parse(data.heldOutFrom!)) })}</> : t("none")}</span></div>
          {dataTo !== null && <div className="mx-dr"><span>{t("Data to")}</span><span>{dayYear(dataTo)}</span></div>}
        </div>
      </DataSettingsChip>
      <span className="tx-spacer" />
      {active && progress ? <>{batchOf(active).length > 1 && <TxMenu label={t.c("action", "Stop")} align="end" trigger={<TxButton size="sm" variant="quiet">{t("Stop batch…")}</TxButton>}
          items={[{id: "run", label: t("Stop #{number}", { number: active.number }), onSelect: () => stop(active)},
            {id: "batch", label: t("Stop batch {batch} · {running} running, {queued} queued", { batch: active.batch ? (list?.batches.find(item => item.id === active.batch)?.title || "") : "", running: batchOf(active).filter(item => item.status === "running").length, queued: batchOf(active).filter(item => item.status === "queued").length }).replace(t("batch  ·"), t("batch ·")), onSelect: () => stopBatch(active)}]} />}
          <TxRunProgress run={`#${active.number}`} stage={progress.stage} count={progress.count} value={progress.value} onStop={() => stop(active)} /></>
        : <TxRunButton label={runLabel(shownKind, total, heldTarget?.number ?? null)}
          kinds={runKinds} onRun={go} onKind={id => setKind(id as typeof kind)} locked={kind === "heldout"} disabled={sending}
          title={blocked !== null ? t("Blocked: {blocked}", { blocked }) : t("Ctrl+Enter")} />}
      {(ownCode || snapRun !== null) && <button type="button" className="tx-codetoggle" aria-pressed={codeShown} onClick={() => update({side: codeShown ? undefined : "code"})}><TxIcon name="code" /><span>{codeShown ? t("Hide code") : t("Show code")}</span></button>}
      <TxSegmented label={t("Work or Results")} value={pageView} options={[{value: "work", label: t("Work")}, {value: "results", label: t("Results")}]} onChange={next => update({modelView: next === "results" ? "results" : undefined} as Partial<ShellState>)} />
      <TxMenu label={t("Actions")} align="end" items={pageMenu} trigger={<TxIconButton label={t("Actions")} icon="more" />} />
    </div>
    {restored?.note && restored.id === selected?.id && <div className="qx-note" role="status">{restored.note}</div>}
    {error && <div className="qx-page-error" role="alert"><span>{error}</span><TxButton size="sm" variant="quiet" onClick={() => setError(null)}>{t("Dismiss")}</TxButton></div>}
    {blocked !== null && check?.blocked && kind !== "check" && <div className="mx-blocked" role="status">{t("Blocked by {blocked}", { blocked })}</div>}
    {/* The preparation's place, the same while it loads (seconds when it waits on Market) as when it is there. */}
    <div className="mx-body"><div className="mx-desk" ref={desk} data-width={mode}>
      {pageView === "results" ? (selected ? <Results mapi={mapi} run={selected} runs={runs} tab={((state as {modelResults?: string}).modelResults as ResultsTab) ?? "windows"} vs={compareId === selected.id ? null : compareId}
        onTab={next => update({modelResults: next} as Partial<ShellState>)} onVs={setCompareId} onPublish={run => update({runId: run.id, modelPublish: true} as Partial<ShellState>)}
        onSelect={id => update({runId: id})} onStar={run => void mapi.annotate(run.id, {starred: !run.starred}).then(() => loadRuns(), fail)} error={fail} onHeldOut={() => void mapi.heldOutPlan(selected.id).then(plan => setConfirm({run: selected, plan}), fail)} onUseSettings={() => void useRun(selected.id).catch(fail)} /> : list !== null && runs.length === 0 ? <NoResultsYet lines={firstLines} toWork={() => update({modelView: undefined} as Partial<ShellState>)} /> : <TxEmpty title={t("No runs yet")} />) : <>
        {prep === null && <div className="mx-prepbox" style={prepFlex}><div className="mx-none"><TxSkeleton width="60%" /></div></div>}
        {prep && <div className="mx-prepbox" style={prepFlex}>
          <Prep dataBasics={<DataInline ctx={ctx} api={api} data={detail.settings.data} onSave={async next => {onDetail(await api.settings(detail.id, {data: next})); void loadPrep(); checkNow(draft, vary, false, next);}} />} deep={deep} onInstallDeep={installDeep} onCancelDeep={cancelDeep} fields={fields} inspect={(input, feature) => mapi.inspect(detail.id, {input, ...(feature ? {feature} : {})})} prep={prep} values={draft} base={baseRun ? baseRun.params : null} baseNumber={baseRun?.number ?? null} width={mode} check={check} checking={checking} vary={vary} onVary={changeVary}
            baseRun={baseRun?.run ?? null} onRevert={(name, value) => {const next = {...draft, [name]: value}; setDraft(next); checkNow(next, vary, true);}}
            onChange={change} onRun={saveRun} onHeldOut={saveHeldOut} onReset={() => baseRun && (setDraft(baseRun.params), checkNow(baseRun.params, vary, true))} onWidth={setWidth}
            onWriteCode={() => void mapi.writeCode(detail.id).then(() => api.read(detail.id).then(next => {onDetail(next); update({side: "code"}); void loadPrep();}), fail)} onGo={group => document.querySelector(`[data-group="${group}"]`)?.scrollIntoView()} /></div>}
        {mode === "narrow" && <div className="mx-vgut" role="separator" aria-label={t("Resize")} onPointerDown={dragPrep} />}
        {mode !== "full" && <div className="mx-main" ref={main}>
          {list !== null && runs.length === 0 && (comparing ?? []).length === 0 ? <FirstRun lines={firstLines} /> : <Experiments running={list?.running ?? null} perRun={check?.batch?.peak || Math.max(0, ...runs.map(item => item.memory ?? 0))} onConcurrency={count => void mapi.concurrency(count).then(() => loadRuns(true), fail)} view={view} onView={setView} list={list} runs={runs} selectedId={selected?.id ?? null} compareId={compareId} ticked={ticked} filter={filter} query={query} sort={sort} byBatch={byBatch}
            onFilter={setFilter} onQuery={setQuery} onSort={setSort} onByBatch={setByBatch} onSelect={id => update({runId: id})} onOpen={id => {update({runId: id}); showResults("windows");}}
            onTick={(id, on) => setTicked(current => {const next = new Set(current); if (on) next.add(id); else next.delete(id); return next;})}
            onStar={run => void mapi.annotate(run.id, {starred: !run.starred}).then(() => loadRuns(), fail)} onNext={run => void mapi.next(run.id).then(() => loadRuns(), fail)}
            onRemove={run => void mapi.remove(run.id).then(() => loadRuns(), fail)} onResume={resume} onCompare={() => setComparing([...ticked].slice(0, 4))}
            onCombine={() => void mapi.combine([...ticked]).then(created => {toast({title: t("Combining {value}", { value: [...ticked].map(id => `#${runs.find(run => run.id === id)?.number}`).join(", ") }), detail: t("mean of their daily ranks")}); setTicked(new Set()); update({runId: created.id}); void loadRuns();}, fail)} />}
          {(runs.length > 0 || (comparing !== null && comparing.length > 0)) && <><div className="mx-hgut" role="separator" aria-label={t("Resize")} onPointerDown={dragSel} />
          <div className="mx-selbox" style={selPx ? {flex: `0 0 ${selPx}px`} : undefined}>{comparing !== null && comparing.length > 0 ? <ComparePane mapi={mapi} runs={comparing.map(id => runs.find(run => run.id === id)).filter((run): run is ModelRun => run !== undefined)} onClose={() => setComparing(null)} error={fail} /> : selected ? <Selected runs={runs} onCompare={setCompareId} onEdit={run => void useRun(run.id).catch(fail)} mapi={mapi} run={selected} onLine={ownCode ? goLine : undefined} compare={baseRun && baseRun.id !== selected.id ? baseRun : null} onStop={stop} onResume={resume}
            onLog={run => void mapi.log(run.id).then(log => setLogOf({run, text: `${log.stdout}\n${log.stderr}`.trim()}), fail)} onGo={showResults}
            onNotebook={run => void route<{notebookId: string}>(ctx, "POST", at`/runs/${run.id}/notebook`).then(opened => update({objectId: opened.notebookId, runId: undefined}), fail)}
            onStrategy={run => void mapi.strategy(run.id).then(created => {toast({title: t("New strategy with {name} #{number}", { name: detail.name, number: run.number })}); update({objectId: created.id, runId: undefined});}, fail)}
            onPublish={run => update({runId: run.id, modelPublish: true} as Partial<ShellState>)} onHeldOut={run => void mapi.heldOutPlan(run.id).then(plan => setConfirm({run, plan}), fail)} error={fail} /> : null}</div></>}
        </div>}</>}
    </div>
    {codeShown && <div className="mx-vsplit" role="separator" aria-label={t("Resize the code")} onPointerDown={dragSide} />}
    {codeShown && <section className="mx-code" aria-label={t("Code")} style={{width: (sidePx ?? 520) + (!snapRun && state.modelTree ? 150 : 0)}}>
      {snapRun ? <><div className="mx-snaphead"><span>{snap?.pipeline ? t("Standard pipeline {version} as #{number} ran it, read-only", { version: snap.pipeline.version, number: snapRun.number }) : t("Code of #{number}, read-only", { number: snapRun.number })}</span><span className="tx-spacer" />
          {ownCode && <TxButton size="sm" onClick={() => void useRun(snapRun.id).catch(fail)}>{t("Restore this code")}</TxButton>}
          <TxIconButton size="sm" label={t("Hide code")} icon="close" onClick={() => update({side: undefined})} /></div>
        <pre className="mx-snap">{snap && snap.id === snapRun.id ? snap.files.map(item => item.text).join("\n\n") : ""}</pre></>
      : <CodeArea ctx={ctx} projectId={detail.developer?.projectId ?? null} rootUri={root} treeTitle={detail.name} entryUri={detail.entryUri} marks={marks} line={codeLine?.line} request={codeLine?.request}
        tree={state.modelTree === true} onTree={open => update({modelTree: open})} onHide={() => update({side: undefined})}
        onOpenDeveloper={() => void ctx.platform.panels.openTarget({module: "developer", panelType: "main", objectId: `file:${detail.entryUri}`}).catch(fail)} onChanged={change => {if (!change.dirty) void api.read(detail.id).then(onDetail, fail);}} />}
    </section>}
    </div>
    {(state as {modelPublish?: boolean}).modelPublish && selected && selected.status === "completed" && <PublishSheet mapi={mapi} run={selected} published={(selected.published ?? runs.find(item => item.published?.name)?.published)?.name ?? null} open onClose={() => update({modelPublish: undefined} as Partial<ShellState>)} onPublished={() => void loadRuns()} />}
    {long && <LongRunConfirm title={(long.groups > 1 ? t("Train {groups} runs?", { groups: long.groups }) : t("Train {groups} run?", { groups: long.groups }))} seconds={long.seconds} label={runLabel(shownKind, total, null)}
      onCancel={() => setLong(null)} onConfirm={() => {setLong(null); send();}} />}
    {confirm && <TxDialog open onOpenChange={() => setConfirm(null)} title={t("Validate #{number} on held-out?", { number: confirm.run.number })}
      footer={<><TxButton size="sm" onClick={() => setConfirm(null)}>{t("Cancel")}</TxButton><TxButton size="sm" variant="primary" data-autofocus="" onClick={() => void mapi.heldOut(confirm.run.id).then(() => {setConfirm(null); void loadRuns();}, fail)}>{t("Validate on held-out")}</TxButton></>}>
      <div className="mx-cfm"><p>{t("Scores · {from} – {to}, once", { from: dayYear(confirm.plan.from), to: dayYear(confirm.plan.to) })}</p><p>{(confirm.plan.windows === 1 ? t("Retrains · {windows} window on the same schedule", { windows: confirm.plan.windows }) : t("Retrains · {windows} windows on the same schedule", { windows: confirm.plan.windows }))}{confirm.plan.minutes ? confirm.plan.minutes < 1 ? t(", under a minute") : t(", about {minutes} min", { minutes: Math.round(confirm.plan.minutes) }) : ""}</p>
        <p>{t("Held out so far · {uses}", { uses: heldOutUses(confirm.plan.uses) })}</p>
        <p>{t("Afterwards #{number} is tagged Held-out. Picking between runs by their held-out score makes that score no longer clean.", { number: confirm.run.number })}</p></div></TxDialog>}
    {logOf && <TxSheet open onOpenChange={() => setLogOf(null)} title={t("Log of #{number}", { number: logOf.run.number })} label={t("Log")} footer={<><TxButton size="sm" onClick={() => {void navigator.clipboard?.writeText(logOf.text); toast({title: t("Copied the log")});}}>{t("Copy")}</TxButton><TxButton size="sm" variant="quiet" onClick={() => setLogOf(null)}>{t.c("action", "Close")}</TxButton></>}><pre className="mx-log">{logOf.text}</pre></TxSheet>}
  </div>;
}
void ({} as Json);
