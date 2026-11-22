/* What the work area shows for a sweep or a walk-forward run: read from the backend while it goes, the view and the combination looked at kept here. */
import { t } from "../../i18n";
import {useCallback, useEffect, useRef, useState} from "react";
import type {ModuleContext} from "@talyx/sdk";
import {validationApi} from "./api";
import {SweepView} from "./SweepView";
import {WalkView} from "./WalkView";
import type {Metric, SweepDetail, SweepAxis} from "./types";

const ACTIVE = new Set(["queued", "preparing", "running", "cancel_requested"]);

export function ValidationPane({ctx, runId, state, kind, sourceNumber, onError, onRunValues, onWiden, onUsed}: {
  ctx: ModuleContext; runId: string; state: string; kind: "sweep" | "walk"; sourceNumber?: number | null; onError(reason: unknown): void;
  /** Use the values of a combination, then run the strategy with them. */
  onRunValues(runId: string, index: number): void; onWiden?(axis: SweepAxis, end: "smallest" | "largest"): void; onUsed?(): void;
}) {
  const api = validationApi(ctx);
  const [detail, setDetail] = useState<SweepDetail | null>(null);
  const view = useRef<{x?: string; y?: string; metric?: Metric}>({});
  const read = useCallback(() => api.sweep(runId, view.current).then(setDetail, onError), [runId]);
  useEffect(() => {view.current = {}; setDetail(null); void read();}, [runId]);
  useEffect(() => {
    if (!ACTIVE.has(state)) {void read(); return;}
    const timer = setInterval(() => void read(), 2000);
    return () => clearInterval(timer);
  }, [runId, state]);
  if (detail === null) return <p className="sv-why" role="status">{(kind === "walk" ? t("Reading the walk-forward…") : t("Reading the sweep…"))}</p>;
  if (kind === "walk") return <WalkView detail={detail} sourceNumber={sourceNumber} onResume={() => void api.resume(runId).then(() => read(), onError)} />;
  return <SweepView detail={detail}
    onCell={index => void api.select(runId, index).then(setDetail, onError)}
    onUse={index => void api.use(runId, index).then(() => onUsed?.(), onError)}
    onRunCell={index => onRunValues(runId, index)} onResume={() => void api.resume(runId).then(() => read(), onError)} onWiden={onWiden} />;
}
