/* The routes this part of the strategy page calls (backend/object_routes.py); each returns the shape in types.ts. */
import type {ModuleContext} from "@talyx/sdk";
import {at, route} from "../../types";
import type {AxisValue, HeldStatus, Metric, SweepDetail} from "./types";

export interface SweepRequest {axes: {name: string; values: AxisValue[]}[]; metric?: Metric; of?: string}
export interface WalkRequest extends SweepRequest {pickOn: number; trade: number; pickBy?: "sharpe" | "cagr" | "calmar"}
export interface Estimate {seconds: number; perCase: number; atOnce: number; limit: string; combinations: number}

export function validationApi(ctx: ModuleContext) {
  return {
    sweep: (runId: string, view?: {x?: string; y?: string; metric?: Metric}) => route<SweepDetail>(ctx, "GET", at`/object-runs/${runId}/sweep`, view),
    select: (runId: string, index: number) => route<SweepDetail>(ctx, "POST", at`/object-runs/${runId}/sweep/select`, {index}),
    use: (runId: string, index: number, expectedVersion?: number) => route<unknown>(ctx, "POST", at`/object-runs/${runId}/sweep/use`, {index, expectedVersion}),
    resume: (runId: string) => route<unknown>(ctx, "POST", at`/object-runs/${runId}/sweep/resume`, {}),
    startSweep: (objectId: string, body: SweepRequest) => route<unknown>(ctx, "POST", at`/objects/${objectId}/sweeps`, body),
    estimateSweep: (objectId: string, body: SweepRequest) => route<Estimate>(ctx, "POST", at`/objects/${objectId}/sweeps/estimate`, body),
    startWalk: (objectId: string, body: WalkRequest) => route<unknown>(ctx, "POST", at`/objects/${objectId}/walk-forward`, body),
    estimateWalk: (objectId: string, body: WalkRequest) => route<Estimate>(ctx, "POST", at`/objects/${objectId}/walk-forward/estimate`, body),
    heldStatus: (runId: string) => route<HeldStatus>(ctx, "GET", at`/strategy-runs/${runId}/held-out`),
    heldOut: (runId: string) => route<unknown>(ctx, "POST", at`/strategy-runs/${runId}/held-out`, {}),
  };
}
export type ValidationApi = ReturnType<typeof validationApi>;

/** The runs a "Costs and capacity" press sends: the cost multiples and the capital steps, each one sweep that starts from the run. */
export const COST_STEPS = [0, 1, 2];
export function costRequests(of: string, capital: number): SweepRequest[] {
  return [{axes: [{name: "@costMultiple", values: COST_STEPS}], of}, {axes: [{name: "@capital", values: [capital, capital * 10, capital * 100]}], of}];
}
