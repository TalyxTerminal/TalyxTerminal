/* The shapes the validation routes return (backend/object_sweeps.py `detail`, strategy_validation.py `status`, strategy_checks.py `rows`). */


export type AxisValue = number | string | boolean | {since: string; until: string};
export interface SweepAxis {name: string; label?: string; values: AxisValue[]}
export interface Edge {name: string; label?: string; value: AxisValue; end: "smallest" | "largest"; low: AxisValue; high: AxisValue}
export type CaseState = "queued" | "running" | "completed" | "failed" | "cancelled" | "interrupted";
export interface SweepCase {
  index: number; params: Record<string, AxisValue>; state: CaseState; runId: string | null; message?: string | null;
  metrics: Record<string, number | null> | null; neighbours: number | null; neighbourCount: number; edge: Edge[];
}
export type Metric = "sharpe" | "cagr" | "totalReturn" | "maxDrawdown" | "trades" | "profitFactor" | "winRate";
export type Figure = PlotFigure | {unavailable: string};
export interface WalkWindow {
  n: number; pickedOn: [number, number]; traded: [number, number]; index: number | null; values: Record<string, AxisValue>; sharpePicked: number | null; sharpeTraded: number | null;
  returnTraded: number | null; trades: number | null; runId: string; kept?: number;
  /** Why the window did not run; the walk stopped there and resumes from it. */
  failed?: string;
}
export interface Walk {pickOn: number; trade: number; pickBy: string; windows: WalkWindow[]; stitched: [number, number][]; efficiency?: number | null; changed?: number}
export interface SweepDetail {
  runId: string; objectId: string; state: string; old?: boolean; number: number | null; of?: string | null; message?: string | null;
  metric: Metric; axes: SweepAxis[]; cases: SweepCase[]; selected: number | null; viewed?: number | null; bestIndex: number | null;
  done: number; total: number; failed: number; concurrency: {atOnce: number | null; limit: "cpu" | "memory" | null};
  machine: {memory: number; budget: number; cores: number}; estimate: {seconds: number; perCase: number; atOnce: number; limit: string; combinations: number} | null;
  metrics: Metric[]; figures: {heat?: Figure; preview?: Figure; walk?: Figure; years?: Figure}; walk?: Walk;
}
export interface HeldStatus {runId: string; number: number | null; from: string; to: string; days: number; short: boolean; used: {runId: string; number: number | null; same: boolean} | null}
export type CheckGo = {kind: "validate" | "walk" | "sweep" | "costs"} | {kind: "open"; runId: string; resweep?: boolean} | {kind: "section"; id: string};
export interface CheckRow {id: string; name: string; value: string; need: string; state: "pass" | "fail" | "notrun"; title: string; go: CheckGo | null; detail?: string}

import type { PlotFigure } from "../../chart/Plot";