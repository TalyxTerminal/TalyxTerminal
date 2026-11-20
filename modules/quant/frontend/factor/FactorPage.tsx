/* A factor's page: one row on top, the settings column, the formula box (or the code bar) over the selected run's Work, and under it
   the runs, the values of one day and the library factors most like it; for a factor in code, its code beside. Results come later. */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  ModuleView, TxButton, TxIcon, TxSkeleton, TxIconButton, TxDialog, TxMenu, TxRunButton, TxRunProgress, TxSegmented, useTxToast,
  type Json, type ModuleContext, type TxMenuItem, type TxRunKind, type TxRunStep,
} from "@talyx/sdk";
import { DataSettingsChip, useIndexMaterials, useResearchLists } from "../DataSettings";
import { FirstRun, NoResultsYet, Starters } from "../shared/FirstRun";
import { documentsClient } from "../developerClients";
import { openCommonCode, periodText, universeText, useEvent, useThrottled, type Api, type LibraryItemSummary, type ObjectDetail, type Preset } from "../objects";
import type { ShellState } from "../QuantShell";
import { factorApi, usable, type ResultsPageTab, type CrossSection, type FactorRunSettings, type FactorFields, type FactorGo, type FieldDecl, type FactorRun, type FetchRow, type FormulaError, type RunWork, type WorkView } from "./api";
import { restoreNotice } from "../shared/restored";
import { RunsView, SimilarView, ValuesBar, ValuesTable, compareLabel } from "./Bottom";
import { FormulaBox, highlight } from "./FormulaBox";
import { dayText, elapsed, mdy, monthText, my, when } from "./format";
import { ResultArea, heldOutUsedLine, runUniverse, stepLabel } from "./Result";
import { RESULT_TABS, ResultsSkeleton, ResultsView, type ResultsHandlers } from "./Results";
import { changedFields, runSettings, sweepAxes, sweepCount, type Draft } from "./draft";
import { SettingsColumn } from "./Settings";
import { at, route } from "../types";
import { PublishSheet } from "./PublishSheet";
import "./factor.css";

const RESULT_TAB_IDS = RESULT_TABS.map(item => item.value);
const ACTIVE = ["queued", "preparing", "running", "cancel_requested"];
const isActive = (run: FactorRun) => ACTIVE.includes(run.state);
const DAY = 86_400_000;

function draftOf(detail: ObjectDetail, text: Record<string, string> = {}): Draft {
  return {formula: detail.settings.formula ?? "", run: runSettings(detail.settings.run as never), params: {...detail.settings.params}, text: {...text}};
}

/** Now, once a second while something runs (for elapsed times). */
function useNow(on: boolean) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!on) return;
    // At once too: the clock stood still while nothing ran, and a run that just started would read a time before it began.
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [on]);
  return now;
}

/** What the head's progress says about a run: the stage, a short count and the share done. */
function progressOf(run: FactorRun): {stage: string; count?: string; value: number | null} {
  if (run.state === "queued" || run.stage === "queued") return {stage: run.progress?.queue ? `Waiting in the queue (${run.progress.queue})` : "Waiting in the queue", value: null};
  const steps = run.progress?.steps ?? [];
  const now = [...steps].reverse().find(step => step.state === "running") ?? steps[steps.length - 1];
  if (!now) return {stage: "Starting", value: null};
  const words: Record<string, string> = {code: "Saving the code", data: "Fetching prices", facts: "Fetching industry and shares", environment: "Starting the research process",
    compute: "Computing values", numbers: run.kind === "sweep" ? "Testing combinations" : "Groups, IC, checks", future: "Checking for future data"};
  const count = now.total ? `${now.done ?? 0} / ${now.total}` : undefined;
  return {stage: words[now.name] ?? now.name, count, value: now.total ? (now.done ?? 0) / now.total : null};
}

/** When a series starts: Market gives a day in milliseconds or a text such as "today's, every year". */
const fromText = (from: string | number) => typeof from === "number" || /^\d{10,}$/.test(String(from)) ? my(Number(from)) : String(from);

/** The series beyond prices and library values that this data gives, by kind, with when each starts. What it cannot give is not listed
    (the interface rule: what the data lacks is said once, where it is used: the formula's check names an unavailable field). */
function extraSeries(fields: FieldDecl[]) {
  const groups = new Map<string, {group: string; available: FieldDecl[]}>();
  for (const field of fields) {
    if (field.group === "Prices and volume" || field.group === "Library values" || field.unavailable) continue;
    const group = groups.get(field.group) ?? {group: field.group, available: []};
    groups.set(field.group, group);
    group.available.push(field);
  }
  return [...groups.values()];
}

export function FactorPage({ctx, api, detail, onDetail, state, update, title, menu, onOpenObject, starters = []}: {
  ctx: ModuleContext; api: Api; detail: ObjectDetail; onDetail(detail: ObjectDetail): void; state: ShellState; update(patch: Partial<ShellState>): void;
  title: ReactNode; menu: TxMenuItem[]; onOpenObject(objectId: string): void; /** The price styles as formulas, to start a new factor from. */ starters?: Preset[];
}) {
  const fapi = useMemo(() => factorApi(ctx), [ctx]);
  const toast = useTxToast();
  const [restored, setRestored] = useState<{id: string; note: string} | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fail = (reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason));
  const mode = detail.settings.mode ?? (detail.hasCode ? "code" : "formula");
  const data = detail.settings.data;
  const wantsResults = state.factorView === "results";

  // ---------------------------------------------------------------- runs: the list, polled by change; the selected and compared ones
  const [runs, setRuns] = useState<FactorRun[] | null>(null);
  const since = useRef<number | undefined>(undefined);
  const loading = useRef<Promise<void> | null>(null), again = useRef(false);
  const loadRuns = useCallback((full = false): Promise<void> => {
    if (loading.current) {again.current = true; return loading.current;}
    const go = async () => {
      const page = await fapi.runs(detail.id, full ? undefined : since.current);
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
  }, [fapi, detail.id]);
  useEffect(() => {since.current = undefined; setRuns(null); void loadRuns(true);}, [detail.id]);
  // A running run reports several times a second: the list is read once a second at most. What the user does reads at once.
  const runsChanged = useThrottled(() => void loadRuns());
  useEvent(ctx, "quant.object_run_changed", payload => {if ((payload as {objectId?: string}).objectId === detail.id) runsChanged();}, () => void loadRuns(true));
  const list = runs ?? [];
  const selected = list.find(item => item.id === state.runId) ?? list[0] ?? null;
  const active = list.find(isActive) ?? null;
  const lastGood = (run: FactorRun | null) => run === null ? null : list.find(item => item.number < run.number && usable(item)) ?? null;
  const [compareId, setCompareId] = useState<string | null>(null);
  const compared = (compareId !== null ? list.find(item => item.id === compareId && usable(item)) : undefined) ?? lastGood(selected);
  /** The run whose values the tables under the result show: the selected one, or the last that has values. */
  const shownRun = usable(selected) ? selected : selected?.kind === "heldout" ? list.find(item => item.id === selected.of) ?? null : lastGood(selected) ?? list.find(usable) ?? null;
  /** The run the Results are of: a sweep's own (one combination at a time), else the shown run (a held-out validation reports the run it scored). */
  const resultsRun = selected?.kind === "sweep" && selected.state === "completed" ? selected : shownRun;
  // Results come with the first finished run; the Work/Results switch is there from the start, and Results says until then what is to come.
  const noResults = runs !== null && resultsRun === null;
  const pageView = wantsResults ? "results" : "work";

  // The selected run's Work, and the shown run's when it is another one.
  const [view, setView] = useState<WorkView | null>(null);
  const [shownView, setShownView] = useState<WorkView | null>(null);
  const vs = usable(selected) && compared ? compared.id : undefined;
  useEffect(() => {
    // Results read their own tabs: Work's numbers are read only while Work is on screen (and kept for when it comes back).
    let live = true;
    if (pageView !== "work") return;
    if (selected === null || selected.state !== "completed") {setView(null); return;}
    void fapi.work(selected.id, vs).then(value => {if (live) setView(value);}, fail);
    return () => {live = false;};
  }, [selected?.id, selected?.state, selected?.updatedAt, vs, pageView]);
  useEffect(() => {
    let live = true;
    if (pageView !== "work") return;
    if (shownRun === null || shownRun.id === selected?.id) {setShownView(null); return;}
    void fapi.work(shownRun.id).then(value => {if (live) setShownView(value);}, fail);
    return () => {live = false;};
  }, [shownRun?.id, selected?.id, pageView]);
  const shownWork = (shownRun !== null && shownRun.id === selected?.id ? view : shownView)?.work as RunWork | null | undefined;

  // Prices name by name, while a run fetches and after it stopped fetching.
  const [rows, setRows] = useState<FetchRow[]>([]);
  const fetching = selected !== null && (isActive(selected) || (selected.state === "failed" && selected.failure?.stage === "data"));
  // The rows change only while prices and facts are fetched: they are read again when those steps move, not on every update of the run.
  const fetchMoved = (selected?.progress?.steps ?? []).filter(step => step.name === "data" || step.name === "facts").map(step => `${step.name}:${step.state}:${step.done ?? ""}`).join("|");
  useEffect(() => {
    let live = true;
    if (!fetching || selected === null) {setRows([]); return;}
    void fapi.fetch(selected.id).then(value => {if (live) setRows(value.rows);}, () => undefined);
    return () => {live = false;};
  }, [selected?.id, selected?.state, fetchMoved, fetching]);

  // ---------------------------------------------------------------- the draft: formula and settings as edited, saved to the factor shortly after
  // Lists typed for a sweep ("5, 10") live in the panel state: the factor keeps one value per setting.
  const [draft, setDraftState] = useState<Draft>(() => draftOf(detail, state.factorText));
  useEffect(() => setDraftState(draftOf(detail, state.factorText)), [detail.id]);
  const saving = useRef<{timer: ReturnType<typeof setTimeout>; save: () => void} | null>(null);
  const setDraft = (next: Draft) => {
    setDraftState(next);
    if (JSON.stringify(next.text) !== JSON.stringify(draft.text)) update({factorText: next.text});
    if (saving.current) clearTimeout(saving.current.timer);
    const save = (live = true) => {
      saving.current = null;
      const params = Object.fromEntries(Object.entries(next.params).filter(([name]) => detail.parameters.some(item => item.name === name)));
      void api.settings(detail.id, {...(mode === "formula" ? {formula: next.formula} : {params}), run: next.run}).then(live ? onDetail : () => undefined, fail);
    };
    saving.current = {timer: setTimeout(save, 600), save: () => save(false)};
  };
  // Leaving the page saves what is still waiting rather than dropping it.
  useEffect(() => () => {if (saving.current) {clearTimeout(saving.current.timer); saving.current.save();}}, []);
  const [formulaError, setFormulaError] = useState<FormulaError | null>(null);
  useEffect(() => {
    if (mode !== "formula" || !draft.formula.trim()) {setFormulaError(null); return;}
    let live = true;
    // A check that could not be made says so; the last answer is not left standing as if it were this formula's.
    const timer = setTimeout(() => void fapi.check(draft.formula, detail.id).then(value => {if (live) setFormulaError(value.error);}, reason => {if (live) {setFormulaError(null); fail(reason);}}), 180);
    return () => {live = false; clearTimeout(timer);};
  }, [draft.formula, mode]);
  const base = selected?.kind === "heldout" ? list.find(item => item.id === selected.of) ?? null : selected && selected.kind === "run" ? selected : null;
  const {fields: changed, count: changes} = changedFields(draft, base, mode);

  // ---------------------------------------------------------------- the formula's fields, the library (for pick settings)
  const [fields, setFields] = useState<FactorFields | null>(null);
  // The fields and the industry settings follow the names: which symbols, and a research list's members as they change.
  const [listVersion, setListVersion] = useState(0);
  useEvent(ctx, "quant.research_lists_changed", payload => {if (data.list && (payload as {listId?: string}).listId === data.list) setListVersion(version => version + 1);});
  const dataKey = JSON.stringify([data.symbols?.map(item => item.instrument.instrumentId), data.list, listVersion, data.index, data.universe, data.since, data.until]);
  useEffect(() => {let live = true; void fapi.fields(detail.id).then(value => {if (live) setFields(value);}, () => undefined); return () => {live = false;};}, [detail.id, dataKey]);
  const [library, setLibrary] = useState<LibraryItemSummary[] | null>(null);
  const needsLibrary = detail.parameters.some(item => item.type === "library" || item.type === "pick");
  useEffect(() => {if (needsLibrary) void api.library().then(value => setLibrary(value.items), fail);}, [needsLibrary]);

  // ---------------------------------------------------------------- the code (a factor written in Python)
  const documents = useMemo(() => documentsClient(ctx), [ctx]);
  const [files, setFiles] = useState<string[]>([]);
  useEffect(() => {if (mode === "code") void fapi.files(detail.id).then(value => setFiles(value.files), () => undefined);}, [detail.id, mode, detail.version]);
  const [file, setFile] = useState(detail.entry);
  const [document, setDocument] = useState<{documentId: string; dirty: boolean; savedAt: number | null} | null>(null);
  const [position, setPosition] = useState<{line: number; requestId: string} | null>(null);
  const codeShown = mode === "code" && state.side === "code";
  // A run before the latest shows the code it was made with, read-only, not the file as it is now.
  const latestRun = list[0] ?? null;
  const oldCode = codeShown && selected !== null && latestRun !== null && selected.id !== latestRun.id && selected.mode === "code" ? selected : null;
  const [snap, setSnap] = useState<{id: string; files: {file: string; text: string}[]} | null>(null);
  useEffect(() => {
    if (oldCode === null) {setSnap(null); return;}
    let live = true;
    void fapi.work(oldCode.id).then(value => {if (live) setSnap(value.codeFiles ? {id: oldCode.id, files: value.codeFiles} : null);}, () => {if (live) setSnap(null);});
    return () => {live = false;};
  }, [oldCode?.id]);
  const snapText = oldCode !== null && snap?.id === oldCode.id ? snap.files.find(item => item.file === file)?.text ?? null : null;
  const applyVersion = async (id: string, cell?: number) => {
    const next = await fapi.apply(id, detail.version, cell);
    setRestored({id, note: restoreNotice(next)});
    onDetail(next); setDraftState(draftOf(next));
    update({runId: id, factorText: {}});
  };
  const restoreCode = () => {if (oldCode) void applyVersion(oldCode.id).catch(fail);};
  useEffect(() => {
    if (mode !== "code") {setDocument(null); return;}
    let live = true;
    const uri = new URL(file, detail.entryUri).href;
    void documents.open({source: {kind: "file", uri, projectId: detail.developer === null ? null : detail.developer.projectId}})
      .then(snapshot => {if (live) setDocument({documentId: snapshot.documentId, dirty: snapshot.dirty, savedAt: null});}).catch(fail);
    return () => {live = false;};
  }, [mode, file, detail.entryUri]);
  useEvent(ctx, "developer.document_changed", payload => {
    const change = payload as {documentId: string; kind: "opened" | "changed" | "saved"};
    if (document === null || change.documentId !== document.documentId) return;
    if (change.kind === "saved") {setDocument({...document, dirty: false, savedAt: Date.now()}); void api.read(detail.id).then(onDetail, fail);}
    else if (change.kind === "changed") setDocument({...document, dirty: true});
  });
  // A failed run of this code marks its line in the editor (Developer's editor view takes line marks from the module that embeds it).
  const failed = selected?.state === "failed" && selected.failure?.stage === "code" && selected.failure.line ? selected.failure : null;
  const failedHere = failed !== null && (failed.file ?? detail.entry) === file;
  const markers = useMemo(() => {
    if (!failed || !failedHere || !selected) return [];
    const message = failed.message.slice(0, 2000), [lead, ...rest] = message.split(":");
    const named = rest.length > 0 && /^[A-Za-z_][\w.]*$/.test(lead.trim());
    return [{line: failed.line!, severity: "error", message, source: `run #${selected.number}`,
      ...(named ? {title: lead.trim().slice(0, 200), detail: rest.join(":").trim() || message} : {detail: message})}];
  }, [failed, failedHere, selected?.number]);
  const editorProps = useMemo<Json>(() => document === null ? {} : {documentId: document.documentId, markers,
    ...(position === null ? {} : {position: {line: position.line}, positionRequestId: position.requestId})}, [document?.documentId, position, markers]);
  const goLine = (target: string | null | undefined, line: number) => {
    if (mode !== "code") return;
    setFile(target && files.includes(target) ? target : target ?? detail.entry);
    setPosition({line, requestId: String(Date.now())});
    update({side: "code"});
  };

  // ---------------------------------------------------------------- running
  const [chosenKind, setKind] = useState<"run" | "validate">("run");
  const [confirming, setConfirming] = useState(false);
  const count = sweepCount(draft);
  const heldOut = data.heldOutFrom ?? null;
  const validateTarget = selected?.kind === "heldout" ? list.find(item => item.id === selected.of) ?? null : usable(selected) ? selected : list.find(usable) ?? null;
  const heldPeriod = heldOut ? `${monthText(heldOut)} – ${data.until === "latest" ? "latest" : monthText(data.until)}` : "";
  const usedBefore = validateTarget?.heldOutUses ?? 0;  // across every factor: the backend counts them
  // Validate is offered only when there is a completed run not yet scored on the held-out period; otherwise the button runs.
  const canValidate = heldOut !== null && validateTarget !== null && !validateTarget.heldOut;
  const kind = chosenKind === "validate" && canValidate ? "validate" : "run";
  const blocked = mode === "formula" ? (formulaError !== null ? "Fix the formula first" : !draft.formula.trim() ? "Write a formula first" : null) : detail.errors.length ? "Fix the code settings first" : null;
  const runLabel = kind === "validate" ? "Validate on held-out" : count > 1 ? `Sweep ${count}` : "Run";
  const start = async (over: Partial<FactorRunSettings> = {}) => {
    setError(null);
    const params = mode === "code" ? Object.fromEntries(Object.entries(draft.params).filter(([name]) => detail.parameters.some(item => item.name === name))) : undefined;
    const axes = sweepAxes(draft);
    const input = {...(mode === "formula" ? {formula: draft.formula} : {}), settings: {...draft.run, ...over}, ...(params ? {params} : {}),
      ...(axes.length ? {kind: "sweep" as const, sweep: axes} : {})};
    if (saving.current) {clearTimeout(saving.current.timer); saving.current.save();}
    const started = await fapi.start(detail.id, input);
    update({runId: started.id});
    await loadRuns();
  };
  const run = () => {
    if (active) return;
    if (blocked !== null) {setError(blocked); return;}   // Run is never greyed: pressed before the formula or settings can run, it says what to fix
    if (kind === "validate") {setConfirming(true); return;}
    void start().catch(fail);
  };
  const validate = async () => {
    setConfirming(false);
    const started = await fapi.heldOut(validateTarget!.id);
    setKind("run");
    update({runId: started.id});
    await loadRuns();
  };
  const kinds: TxRunKind[] = [
    {id: "run", label: "Run", current: kind === "run" && count === 1},
    {id: "sweep", label: count > 1 ? `Sweep ${count}` : "Sweep", hint: "a list in any setting", current: kind === "run" && count > 1},
    // Without a held-out period, a completed run, or with that run already scored, there is nothing to validate: the kind is not offered (the period is set in Run settings, Held out).
    ...(canValidate ? [{id: "validate", label: "Validate on held-out", hint: `${heldPeriod}, once`, current: kind === "validate"}] : []),
  ];
  const pickKind = (id: string) => {
    if (id === "validate") {setKind("validate"); return;}
    setKind("run");
    if (id === "sweep" && count === 1) {
      setDraft({...draft, text: {...draft.text, holding: "1, 3, 5, 10"}});
      requestAnimationFrame(() => (window.document.querySelector("#fx-row-holding input") as HTMLInputElement | null)?.focus());
    }
    if (id === "run") setDraft({...draft, text: {}});
  };
  const now = useNow(active !== null);
  const steps: TxRunStep[] | undefined = active?.progress?.steps.map(step => ({label: stepLabel(step.name), state: step.state === "done" ? "done" : "now",
    time: step.seconds != null ? elapsed(step.seconds) : step.startedAt ? elapsed(Math.max(0, now - step.startedAt) / 1000) : undefined}));
  const progress = active ? progressOf(active) : null;
  /** A finished run whose Results are still building in the background: Work's numbers are final, the head says the rest is coming. */
  const building = list.find(item => item.resultsProgress != null && item.state === "completed") ?? null;

  // ---------------------------------------------------------------- selecting a run puts its formula and settings back
  const select = async (runId: string) => {
    const target = list.find(item => item.id === runId);
    update({runId});
    setCompareId(null);
    setKind("run");
    if (!target) return;
    const source = target.kind === "heldout" ? list.find(item => item.id === target.of) : target;
    if (!source || source.state !== "completed" || source.kind !== "run") return;
    if (mode === "formula") {
      await applyVersion(source.id);
    } else {
      const next = {...draft, run: runSettings(source.settings.run), params: {...draft.params, ...source.settings.params}, text: {}};
      setDraft(next);
    }
  };

  // ---------------------------------------------------------------- under the result: tab, day, order
  const tab = shownRun === null ? "runs" : state.factorTab ?? "runs";   // Values and Similar are there once a run is
  const resultsTab = (RESULT_TAB_IDS as readonly string[]).includes(state.factorResults ?? "") ? state.factorResults as ResultsPageTab : "returns";
  const [date, setDate] = useState<number | null>(null);
  const [order, setOrder] = useState<"top" | "bottom" | "missing">("top");
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<string | null>(null);
  const [cross, setCross] = useState<CrossSection | null>(null);
  useEffect(() => {
    let live = true;
    if (shownRun === null) {setCross(null); return;}
    // Read whichever tab is open, so the tab's title names the day the table is of.
    void fapi.cross(shownRun.id, date ?? undefined).then(value => {if (live) setCross(value);}, reason => {if (live) {setCross(null); if (tab === "values") fail(reason);}});
    return () => {live = false;};
  }, [tab, shownRun?.id, date]);
  const showValues = (patch: {date?: number | null; order?: "top" | "bottom" | "missing"; picked?: string | null}) => {
    if (patch.date !== undefined) setDate(patch.date);
    if (patch.order) setOrder(patch.order);
    if (patch.picked !== undefined) setPicked(patch.picked);
    update({factorTab: "values", factorFull: false, factorView: undefined});
  };
  const flags = shownWork?.flags ?? [];
  const goFlag = () => {
    const flag = flags[0];
    if (flag) showValues({date: flag.label, order: "top", picked: flag.text.split(" ")[0]});
  };
  const [ticked, setTicked] = useState<Set<string>>(new Set());

  const flash = (key: string) => {
    const row = window.document.getElementById(`fx-row-${key}`);
    if (!row) return;
    row.scrollIntoView({block: "center"});
    row.animate([{background: "var(--tx-ember-wash)"}, {background: "transparent"}], 1100);
    row.querySelector("input")?.focus();
  };
  const go = (target: FactorGo) => {
    if (target.to === "results") {update({factorView: "results", factorResults: target.id}); return;}
    if (target.to === "tab") {
      if (target.id === "values") showValues({order: (target.order as "missing" | undefined) ?? "top", ...(target.date ? {date: target.date} : {})});
      else update({factorTab: target.id as "similar", factorFull: false, factorView: undefined});
    } else if (target.to === "setting") {
      // The row is in Work's settings column: it needs to be on screen before it can flash.
      if (pageView !== "work") {update({factorView: undefined}); requestAnimationFrame(() => flash(target.id));} else flash(target.id);
    } else if (target.to === "code") goLine(null, target.line);
    else if (target.to === "flag") goFlag();
  };
  const [publishing, setPublishing] = useState<FactorRun | null>(null);
  const handlers: ResultsHandlers = {
    select: runId => void select(runId).catch(fail),
    go,
    // A holding picked (IC by holding days, the Costs tab's holding rows): set and run at once, on Work.
    holding: day => {
      if (!base || day === base.settings.run.holding) return;
      setDraft({...draft, run: {...draft.run, holding: day}, text: {...draft.text, holding: String(day)}});
      update({factorView: undefined});
      if (!blocked && !active) void start({holding: day}).catch(fail);
    },
    date: ms => showValues({date: ms, order: "top"}),
    retry: target => void fapi.retry(target.id).then(() => loadRuns(), fail),
    line: goLine,
    useSettings: (target, cell) => void fapi.apply(target.id, detail.version, cell).then(next => {
      const note = restoreNotice(next);
      setRestored({id: target.id, note});
      onDetail(next); setDraftState(draftOf(next));
      toast({title: "Settings of this combination set"});
    }, fail),
    create: (target, objectKind, cell) => void fapi.createFrom(target.id, objectKind, cell).then(created => {
      toast({title: `New ${objectKind} with ${detail.name} #${target.number}`, detail: `Its input is this run's values; data settings follow #${target.number}.`});
      onOpenObject(created.id);
    }, fail),
    compare: setCompareId,
    notebook: target => void route<{notebookId: string}>(ctx, "POST", at`/runs/${target.id}/notebook`).then(opened => onOpenObject(opened.notebookId), fail),
    publish: target => setPublishing(target),
    showDay: ms => showValues({date: ms, order: "top"}),
    star: target => void fapi.annotate(target.id, {starred: !target.starred}).then(() => loadRuns(), fail),
    build: (target, cellIndex) => void fapi.buildResults(target.id, cellIndex).then(() => loadRuns(), fail),
    reuse: target => {void applyVersion(target.id).then(() => update({factorView: undefined}), fail);},
  };
  const [sweepCell, setSweepCell] = useState<number | null>(null);
  useEffect(() => setSweepCell(null), [selected?.id]);
  const cell = sweepCell ?? selected?.sweep?.selected ?? 0;

  // ---------------------------------------------------------------- the head
  const {items: lists} = useResearchLists(ctx, api);
  const indexes = useIndexMaterials(ctx);
  const universe = universeText(data, lists, indexes.items) ?? "Set data";
  const until = heldOut ? new Date(Date.parse(heldOut + "T00:00:00Z") - DAY).toISOString().slice(0, 10) : data.until;
  const period = data.since && until ? periodText(data.since, until) : null;
  const dataTo = (selected?.data?.dataTo ?? shownRun?.data?.dataTo) ?? null;
  const [dataOpen, setDataOpen] = useState(false);
  // The series beyond prices this data gives, by kind, with their start.
  const extra = extraSeries(fields?.fields ?? []);
  const sourceLabel = (sourceId: string) => fields?.sources.find(source => source.sourceId === sourceId)?.label ?? sourceId;
  const chip = <button type="button" className="tx-datachip" title="Data settings">
    <TxIcon name="list" /><b data-priority={1}>{universe}</b>
    {period && <><span className="tx-sep" /><span data-priority={2}>{period}{heldOut && <span className="fx-t3">, then held out</span>}</span></>}
    {dataTo !== null && <><span className="tx-sep" /><span data-priority={3} className="fx-t3">data to {mdy(dataTo).replace(/, \d{4}$/, "")}</span></>}
    <TxIcon name="chevron" style={{transform: "rotate(90deg)"}} />
  </button>;
  const pageMenu: TxMenuItem[] = [
    ...(mode === "code" ? [{id: "developer", label: "Open in Developer", icon: "external" as const, onSelect: () => void ctx.platform.panels.openTarget({module: "developer", panelType: "main", objectId: `file:${detail.entryUri}`}).catch(fail)}] : []),
    {id: "common", label: "Open common code", icon: "folder", onSelect: () => void openCommonCode(ctx, api).catch(fail)},
    ...menu,
  ];
  // While a divider is dragged the size lives here; the panel state is written once, on release.
  const [dragging, setDragging] = useState<{factorHeight?: number; sideSize?: number} | null>(null);
  const resultHeight = dragging?.factorHeight ?? state.factorHeight ?? null, full = state.factorFull === true;
  const fmain = useRef<HTMLDivElement>(null), resultBox = useRef<HTMLDivElement>(null);
  const drag = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const top = resultBox.current!.getBoundingClientRect().top, max = fmain.current!.getBoundingClientRect().bottom - top - 140;
    const target = event.currentTarget; target.setPointerCapture(event.pointerId);
    let height: number | null = null;
    const move = (next: PointerEvent) => {height = Math.round(Math.max(350, Math.min(max, next.clientY - top))); setDragging({factorHeight: height});};
    const up = () => {target.removeEventListener("pointermove", move); target.removeEventListener("pointerup", up); if (height !== null) update({factorHeight: height}); setDragging(null);};
    target.addEventListener("pointermove", move); target.addEventListener("pointerup", up);
  };
  const side = dragging?.sideSize ?? state.sideSize ?? .4;
  const dragSide = (event: React.PointerEvent<HTMLDivElement>) => {
    const parent = event.currentTarget.parentElement as HTMLElement;
    const box = parent.getBoundingClientRect();
    const target = event.currentTarget; target.setPointerCapture(event.pointerId);
    let size: number | null = null;
    const move = (next: PointerEvent) => {size = Math.min(.6, Math.max(.22, (box.right - next.clientX) / box.width)); setDragging({sideSize: size});};
    const up = () => {target.removeEventListener("pointermove", move); target.removeEventListener("pointerup", up); if (size !== null) update({sideSize: size}); setDragging(null);};
    target.addEventListener("pointermove", move); target.addEventListener("pointerup", up);
  };
  const paramOverrides = detail.parameters.filter(item => (item.type === "int" || item.type === "float" || item.type === "bool" || item.type === "text") && item.name in draft.params
    && JSON.stringify(draft.params[item.name]) !== JSON.stringify(item.default));
  const codeFailure = selected?.state === "failed" && selected.failure?.stage === "code" && selected.failure.line ? selected.failure : null;
  const firstEmpty = list.length === 0 && mode === "formula" && !draft.formula.trim() ? detail.id : undefined;
  // Before the first run the result and the runs under it have nothing to show: the run to come stands in their place.
  const first = runs !== null && list.length === 0;
  const focusFormula = () => (window.document.querySelector(".fx-fcode textarea") as HTMLTextAreaElement | null)?.focus();
  const runLine = draft.run;
  const firstLines = [
    {label: "Names", value: universe, missing: universe === "Set data", onClick: () => setDataOpen(true)},
    {label: "Period", value: period ? <>{period}{heldOut && <span className="fx-t3">, then held out</span>}</> : "Set data", missing: !period, onClick: () => setDataOpen(true)},
    ...(mode === "formula" ? [{label: "Formula", value: draft.formula.trim() ? <code className="fx-firstformula">{highlight(draft.formula, formulaError)}</code> : "none yet",
      missing: !draft.formula.trim() || formulaError !== null, onClick: focusFormula}] : []),
    {label: "Test", value: `${runLine.holding}-day holding, ${runLine.grouping.method === "equal" ? `${runLine.grouping.count} equal groups` : runLine.grouping.method === "cuts" ? `cut at ${runLine.grouping.cuts}` : "by sign"}`,
      onClick: () => flash("holding")},
    {label: "Cost", value: `${runLine.costBps} bps a trade, borrow ${runLine.borrowPct}% a year`, onClick: () => flash("costBps")},
  ];

  return <div className="qx-page fx-page" onKeyDownCapture={event => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {event.preventDefault(); event.stopPropagation(); run();}
  }}>
    <div className="tx-panelhead fx-head">
      {title}<span className="fx-kind">Factor</span>
      <DataSettingsChip ctx={ctx} api={api} kind="factor" data={data} defaults={null} defaultsError={null} boundary={null} open={dataOpen} onOpenChange={setDataOpen}
        onSave={async next => onDetail(await api.settings(detail.id, {data: next}))} trigger={chip}>
        <div className="fx-dpop">
          <div className="fx-dr"><span>Held out</span><span className="fx-ro">{heldOut ? <><TxIcon name="lock" />from {dayText(heldOut)}</> : <span className="fx-t3">none</span>}</span></div>
          <div className="fx-dr"><span>Data to</span><span className="fx-ro">{dataTo !== null ? mdy(dataTo) : <span className="fx-t3">known after a run</span>}</span></div>
          {extra.length > 0 && <div className="fx-lim fx-extra"><h5>Extra series</h5>{extra.map(group => <div key={group.group} className="fx-xrow">
            <b>{group.group}</b>
            {group.available.map(field => <span key={field.name} title={[field.label, field.note].filter(Boolean).join(". ")}><code>{field.name}</code>{field.from ? <span className="fx-t3"> from {fromText(field.from)}</span> : null}</span>)}
          </div>)}</div>}
          {fields !== null && fields.limits.length > 0 && <div className="fx-lim"><h5>Data limits {fields.limits.length}{fields.sources.length > 0 && <span className="fx-t4">{fields.sources.map(item => item.label).join(", ")}</span>}</h5>
            {fields.limits.map((limit, index) => <div key={index} title={sourceLabel(limit.sourceId)}>{limit.text}</div>)}</div>}
        </div>
      </DataSettingsChip>
      <span className="tx-spacer" />
      {/* The selected run's own head carries its Results' progress (as in the draft); the page head only for another run's. */}
      {building && !(pageView === "work" && building.id === selected?.id) && <TxRunProgress run={`#${building.number}`} stage="Results in the background" count={`${Math.round((building.resultsProgress ?? 0) * 100)}%`} value={building.resultsProgress} />}
      {active && progress ? <TxRunProgress run={`#${active.number}`} stage={progress.stage} count={progress.count} value={progress.value} elapsed={elapsed(Math.max(0, now - active.createdAt) / 1000)}
          steps={steps} onStop={() => void fapi.cancel(active.id).then(() => loadRuns(), fail)} />
        : <>
          {changes > 0 && base && <span className="fx-chgl">{changes === 1 ? "1 change" : `${changes} changes`} since #{base.number}</span>}
          <TxRunButton label={runLabel} kinds={kinds} onRun={run} onKind={pickKind} locked={kind === "validate"}
            title={blocked ?? (kind === "validate" && validateTarget ? `Validate #${validateTarget.number} on ${heldPeriod} (Ctrl+Enter)` : `${runLabel} (Ctrl+Enter)`)} />
          <TxDialog open={confirming && validateTarget !== null} onOpenChange={setConfirming} title={`Validate #${validateTarget?.number} on held-out?`}
            footer={<><TxButton size="sm" onClick={() => setConfirming(false)}>Cancel</TxButton>
              <TxButton size="sm" variant="primary" data-autofocus="" onClick={() => void validate().catch(fail)}><TxIcon name="lock" />Validate #{validateTarget?.number}</TxButton></>}>
            <div className="fx-cfm">{heldOutUsedLine(usedBefore) && <p className="fx-attn" role="status">{heldOutUsedLine(usedBefore)}</p>}<p>Scores #{validateTarget?.number} once on {heldPeriod} and tags the run Held-out. After you have seen it, choices made on it are no longer out of sample; checks keep using in-sample data only.</p></div>
          </TxDialog>
        </>}
      <TxSegmented label="Work or Results" value={pageView} options={[{value: "work", label: "Work"}, {value: "results", label: "Results"}]}
        onChange={next => update({factorView: next === "results" ? "results" : undefined})} />
      {mode === "code" && <button type="button" className="tx-codetoggle" aria-pressed={codeShown} onClick={() => update({side: codeShown ? undefined : "code"})}>
        <TxIcon name="code" /><span>{codeShown ? "Hide code" : "Show code"}</span></button>}
      <TxMenu label="Actions" align="end" items={pageMenu} trigger={<TxIconButton label="Actions" icon="more" />} />
    </div>
    {restored?.note && restored.id === selected?.id && <div className="qx-note" role="status">{restored.note}</div>}
    {error && <div className="qx-page-error" role="alert"><span>{error}</span><TxButton size="sm" variant="quiet" onClick={() => setError(null)}>Dismiss</TxButton></div>}
    <div className="fx-desk" style={{["--fx-side" as string]: `${side * 100}%`}}>
      <div className="fx-view">
        {pageView === "results" ? <div className="fx-main">{runs === null ? <ResultsSkeleton /> : noResults ? first ? <NoResultsYet lines={firstLines} toWork={() => update({factorView: undefined})} /> : <p className="fx-empty">No finished run yet</p> : <ResultsView fapi={fapi} run={resultsRun} selected={selected} runs={list} tab={resultsTab} cell={resultsRun?.kind === "sweep" ? cell : undefined}
          onTab={next => update({factorResults: next})} vs={state.factorVs ?? []} onVs={ids => update({factorVs: ids})}
          universe={data => universeText(data, lists, indexes.items) ?? "Set data"} handlers={handlers} />}</div> : <>
        <SettingsColumn mode={mode} parameters={detail.parameters} errors={detail.errors} draft={draft} changed={changed} library={library} heldOutFrom={heldOut}
          industries={fields?.industries ?? null} neutralizeDefault={fields?.neutralizeDefault ?? null} base={base} onRevert={() => {if (base) void select(base.id).catch(fail);}}
          onDraft={setDraft} onHeldOut={day => void api.settings(detail.id, {data: {...data, heldOutFrom: day}}).then(onDetail, fail)} onLine={(target, line) => goLine(target, line)} />
        <div className="fx-main" ref={fmain}>
          {mode === "formula"
            ? <FormulaBox value={draft.formula} error={formulaError} fields={fields} focusKey={firstEmpty} onChange={formula => setDraft({...draft, formula})} onRun={run}
              onToCode={() => void fapi.toCode(detail.id, draft.formula).then(next => {onDetail(next); update({side: "code"});}, fail)} />
            : selected !== null && selected.mode === "formula" && selected.formula ? <div className="fx-fbox fx-codebar"><div className="fx-fline"><span className="fx-t3" data-priority={1}>Formula of #{selected.number}</span>
              <code className="fx-oldformula" title={selected.formula}>{selected.formula}</code><span className="tx-spacer" /><span className="fx-t3" data-priority={3}>made before the factor became code</span></div></div>
            : <div className="fx-fbox fx-codebar"><div className="fx-fline"><TxIcon name="code" className="fx-t3" /><span data-priority={1}>Code in Developer</span>
              <span className="fx-files">{(files.length ? files : [detail.entry]).map(name => <button key={name} type="button" className="fx-ftag" data-on={codeShown && name === file ? "" : undefined}
                onClick={() => {setFile(name); update({side: "code"});}}>{name}</button>)}</span>
              <span className="fx-t3" data-priority={3}>{document?.dirty ? "unsaved changes" : document?.savedAt ? `saved ${when(document.savedAt)}` : ""}</span><span className="tx-spacer" />
              {codeShown ? <span className="fx-t3" data-priority={2}>Ctrl+Enter runs</span> : <TxButton size="sm" variant="quiet" onClick={() => update({side: "code"})}>Show code</TxButton>}</div></div>}
          {first ? <div className="fx-firstwrap"><FirstRun lines={firstLines}>
            {mode === "formula" && <Starters title="Start from a price style" items={starters.filter(item => item.formula).map(item => ({id: item.id, name: item.label, title: item.description, body: highlight(item.formula!, null)}))}
              onPick={id => {const found = starters.find(item => item.id === id); if (found?.formula) {setDraft({...draft, formula: found.formula}); focusFormula();}}} />}
          </FirstRun></div> : <>
          <div className="fx-res" ref={resultBox} style={full ? {display: "none"} : resultHeight ? {flex: `0 0 ${resultHeight}px`} : undefined}>
            <div className="fx-rwrap">{runs === null ? <div className="fx-pad"><TxSkeleton width="40%" /></div> : <ResultArea run={selected} view={view} runs={list} rows={rows} compared={compared} universe={target => runUniverse(target, lists, indexes.items)} cell={cell} onCell={setSweepCell}
              editedHolding={base && draft.run.holding !== base.settings.run.holding ? draft.run.holding : null} valuesDate={tab === "values" && cross ? cross.date : null} handlers={handlers}
              fallback={shownRun !== null && shownWork ? {run: shownRun, work: shownWork} : null} />}</div>
          </div>
          {!full && <div className="fx-hgut" role="separator" aria-label="Resize" onPointerDown={drag} onDoubleClick={() => update({factorHeight: undefined})} />}
          <div className="fx-bot" data-grow={resultHeight || full ? "" : undefined}>
            <div className="fx-btabs" role="tablist">
              <button type="button" role="tab" className="fx-tab" aria-selected={tab === "runs"} onClick={() => update({factorTab: "runs"})}>Runs<span className="fx-cnt">{list.length}</span></button>
              {shownRun !== null && <><button type="button" role="tab" className="fx-tab" aria-selected={tab === "values"} onClick={() => update({factorTab: "values"})}>
                {cross ? `Values on ${mdy(cross.date)}` : "Values"}</button>
              <button type="button" role="tab" className="fx-tab" aria-selected={tab === "similar"} onClick={() => update({factorTab: "similar"})}>
                Similar in library<span className="fx-cnt">{shownWork?.similar?.filter(item => !item.style).length ?? 0}</span></button></>}
              <span className="tx-spacer" />
              {tab === "runs" && compareLabel(ticked.size) !== null && <TxButton size="sm" variant="quiet" className="fx-cmp"
                onClick={() => update({factorView: "results", factorResults: "returns", factorVs: [...ticked]})}>{compareLabel(ticked.size)}</TxButton>}
              <TxIconButton size="sm" label={full ? "Back to the result" : "Fill the work area"} icon={full ? "restore" : "maximize"} onClick={() => update({factorFull: !full})} />
            </div>
            {tab === "values" && <ValuesBar cross={cross} order={order} query={query} flagged={flags.length ? flags.length : null} onOrder={setOrder} onQuery={setQuery}
              onDate={value => setDate(value)} onFlag={goFlag} />}
            <div className="fx-bsc">
              {tab === "runs" ? <RunsView runs={list} mode={mode} selected={selected?.id ?? null} compared={usable(selected) ? compared?.id ?? null : null} ticked={ticked}
                futureOf={item => item.futureData}
                onSelect={runId => void select(runId).catch(fail)} onOpen={runId => {void select(runId).catch(fail); update({factorView: "results", factorResults: "returns", factorVs: undefined});}} onTick={(runId, on) => {const next = new Set(ticked); if (on && next.size < 3 && runId !== selected?.id) next.add(runId); else next.delete(runId); setTicked(next);}}
                onStar={item => void fapi.annotate(item.id, {starred: !item.starred}).then(() => loadRuns(), fail)} />
                : tab === "values" ? <div className="fx-pad fx-vtab"><ValuesTable cross={cross} order={order} query={query} picked={picked} onPick={setPicked} /></div>
                : <SimilarView items={shownWork?.similar ?? null} run={shownRun} holding={shownRun?.numbers?.holding ?? 5} />}
            </div>
          </div></>}
        </div></>}
      </div>
      {codeShown && <><div className="fx-vsplit" role="separator" aria-label="Resize the code" onPointerDown={dragSide} />
        <section className="fx-code" aria-label="Code">
          <div className="fx-codetabs">{(files.length ? files : [detail.entry]).map(name => <button key={name} type="button" className="fx-ftab" aria-selected={name === file} onClick={() => setFile(name)}>{name}</button>)}
            <span className="tx-spacer" /><TxIconButton size="sm" label="Hide code" icon="close" onClick={() => update({side: undefined})} /></div>
          {oldCode !== null && snapText !== null && <div className="fx-snaphead"><span>Code of #{oldCode.number}, read-only</span><span className="tx-spacer" />
            <TxButton size="sm" onClick={restoreCode}>Restore this code</TxButton></div>}
          <div className="fx-editor">{oldCode !== null && snapText !== null ? <pre className="fx-snap" aria-label={`Code of #${oldCode.number}`}>{snapText}</pre>
            : document === null ? <p className="fx-empty">Opening code…</p> : <ModuleView id="developer.editor" context={ctx} props={editorProps} />}</div>
          <div className="fx-sb"><span>Factor</span>
            {codeFailure ? <button type="button" className="fx-attn fx-sbline" role="alert" onClick={() => goLine(codeFailure.file, codeFailure.line!)}>Line {codeFailure.line}: {codeFailure.message.split(":")[0]}</button>
              : selected ? <span>Code of #{oldCode !== null && snapText !== null ? oldCode.number : (latestRun ?? selected).number}</span> : null}
            {paramOverrides.map(item => <span key={item.name} className="fx-attn">{item.name} = {String(draft.params[item.name])} from settings</span>)}
            <span className="tx-spacer" /><span>{document?.dirty ? "Unsaved" : ""}</span><span>Ctrl+Enter runs</span></div>
        </section></>}
    </div>
    {publishing && <PublishSheet ctx={ctx} runId={publishing.id} runName={detail.name} open onClose={() => setPublishing(null)} onPublished={() => void loadRuns()} />}
  </div>;
}

