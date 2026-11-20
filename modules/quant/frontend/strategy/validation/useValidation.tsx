/* The page's validation actions in one place: the run button and its menu, what a check's button starts, and the two confirmations. */
import {useMemo, useState, type ReactNode} from "react";
import type {ModuleContext, TxRunKind} from "@talyx/sdk";
import type {Parameter} from "../../objects";
import {costRequests, validationApi, type Estimate, type SweepRequest, type WalkRequest} from "./api";
import {HeldOutConfirm} from "./HeldOutConfirm";
import {planRanges, runWord, type RangePlan} from "./ranges";
import {SweepConfirm, isSlow} from "./SweepConfirm";
import type {CheckGo, HeldStatus} from "./types";

export const WALK_DEFAULT = {pickOn: 12, trade: 3, pickBy: "sharpe" as "sharpe" | "cagr" | "calmar"};
export type WalkSettings = typeof WALK_DEFAULT;
export interface RunLike {id: string; number: number; settings: {data: {since?: string; until?: string}; run: {capital: number}}}

/** The calendar years a span covers, each from its first to its last day inside the span. */
export function calendarYears(since: string, until: string): {since: string; until: string}[] {
  const first = Number(since.slice(0, 4)), last = Number(until.slice(0, 4));
  const out = [];
  for (let year = first; year <= last; year++) out.push({since: year === first ? since : `${year}-01-01`, until: year === last ? until : `${year}-12-31`});
  return out;
}

export function useValidation({ctx, objectId, parameters, ranges, selected, usable, backtestId, walkSettings, sweepSource, update, reload, fail, showResults}: {
  ctx: ModuleContext; objectId: string; parameters: Parameter[]; ranges: Record<string, string>; selected: RunLike | null; usable: boolean; backtestId?: string | null; walkSettings?: WalkSettings; sweepSource?: string | null;
  update(patch: Record<string, unknown>): void; reload(): Promise<void>; fail(reason: unknown): void; showResults(section: string): void;
}): {plan: RangePlan; label: string; kinds: TxRunKind[]; heldMissing: boolean; run(start: () => void): void; onKind(id: string): void; onCheck(go: CheckGo | string): void; confirms: ReactNode} {
  const api = useMemo(() => validationApi(ctx), [ctx]);
  const plan = useMemo(() => planRanges(parameters, ranges), [parameters, ranges]);
  const [pending, setPending] = useState<{label: string; estimate: Estimate; go(): Promise<unknown>} | null>(null);
  const [held, setHeld] = useState<HeldStatus | null>(null);
  const [heldMissing, setHeldMissing] = useState(false);
  const opened = (row: unknown) => {const id = (row as {id?: string} | null)?.id; if (id) update({runId: id, strategyView: undefined, strategyTab: undefined});};
  const sent = (go: () => Promise<unknown>) => go().then(row => {opened(row); return reload();}, fail);
  const of = usable && selected ? selected.id : sweepSource ?? undefined;
  const withOf = <T extends SweepRequest>(body: T): T => (of ? {...body, of} : body);

  const sweep = async (body: SweepRequest, label = "Sweep") => {
    const estimate = await api.estimateSweep(objectId, body);
    const go = () => api.startSweep(objectId, body);
    if (isSlow(estimate)) setPending({label, estimate, go}); else await sent(go);
  };
  const walk = async () => {
    if (plan.total < 2) {fail(new Error("Give a code setting a range to pick from, like 24, 48, 96")); return;}
    const body: WalkRequest = withOf({axes: plan.axes, ...(walkSettings ?? WALK_DEFAULT)});
    const estimate = await api.estimateWalk(objectId, body);
    const go = () => api.startWalk(objectId, body);
    if (isSlow(estimate)) setPending({label: "Walk-forward", estimate, go}); else await sent(go);
  };
  const years = async () => {
    if (!selected) {fail(new Error("Run a backtest first")); return;}
    const {since, until} = selected.settings.data;
    if (!since || !until) {fail(new Error("This run has no period")); return;}
    await sweep(withOf({axes: [{name: "@period", values: calendarYears(since, until)}]}), "Each year separately");
  };
  const costs = async (only?: "cost") => {
    if (!selected || !of) {fail(new Error("Run a backtest first")); return;}
    const bodies = only ? [{axes: [{name: "@costMultiple", values: [0, 1, 2]}], of}] : costRequests(of, selected.settings.run.capital);
    for (const body of bodies) await api.startSweep(objectId, body);
    await reload();
  };
  const validate = async () => {
    const id = usable && selected ? selected.id : backtestId;
    if (!id) {fail(new Error("Run a backtest first")); return;}
    try {setHeld(await api.heldStatus(id)); setHeldMissing(false);}
    catch (reason) {
      if ((reason as {code?: string})?.code === "no_held_out_period") setHeldMissing(true); else throw reason;
    }
  };
  const swept = async () => {
    if (plan.total < 2) {fail(new Error("Give a code setting a range to sweep, like 24, 48, 96")); return;}
    await sweep(withOf({axes: plan.axes}), `Sweep`);
  };
  const guarded = (task: () => Promise<unknown>) => void task().catch(fail);

  const kinds: TxRunKind[] = [{id: "run", label: plan.total > 1 ? runWord(plan) : "Run backtest", current: true}, {id: "walk", label: "Walk-forward"},
    {id: "years", label: "Each year separately"}, {id: "costs", label: "Costs and capacity"}, {id: "held", label: "Validate on held-out", separated: true}];
  return {
    plan, label: runWord(plan), kinds, heldMissing,
    run: start => {
      if (plan.tooMany) {fail(new Error(plan.tooMany)); return;}
      if (Object.keys(plan.errors).length) {fail(new Error(Object.values(plan.errors)[0])); return;}
      if (plan.total > 1) guarded(swept); else start();
    },
    onKind: id => guarded(id === "walk" ? walk : id === "years" ? years : id === "costs" ? () => costs() : id === "held" ? validate : async () => undefined),
    onCheck: go => {
      if (typeof go === "string") {showResults(go === "equity" ? "equity" : go === "trades" ? "trades" : "summary"); return;}
      switch (go.kind) {
        case "validate": guarded(validate); break;
        case "walk": guarded(walk); break;
        case "sweep": guarded(swept); break;
        case "costs": guarded(() => costs("cost")); break;
        case "open": update({runId: go.runId, strategyView: undefined, strategyTab: undefined}); break;
        case "section": showResults(go.id); break;
      }
    },
    confirms: <>
      <SweepConfirm open={pending !== null} estimate={pending?.estimate ?? null} label={pending?.label} onCancel={() => setPending(null)}
        onConfirm={() => {const go = pending!.go; setPending(null); void sent(go);}} />
      <HeldOutConfirm open={held !== null} status={held} onCancel={() => setHeld(null)}
        onConfirm={() => {const id = held!.runId; setHeld(null); void sent(() => api.heldOut(id));}} />
    </>,
  };
}
