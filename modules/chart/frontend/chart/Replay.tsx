/* Bar replay (TradingView): Replay in the top bar arms the active chart; a click on it picks the bar replay starts
   from (a line follows the pointer, what comes after is shaded). While replaying, a bar under the chart plays, pauses
   and steps the bars on, sets the speed, picks another start and goes back to real time. */
import { useEffect, useRef, useState } from "react";
import { TxButton, TxIconButton, TxMenu, TxTooltip, type ModuleContext } from "@talyx/sdk";
import { post } from "../api";
import { message, reportChartError, setUi, useStore } from "../store";
import { ChartGlyph } from "./icons";
import type { ChartBinding } from "./binding";

/** Bars a second, TradingView's speeds. */
const SPEEDS = [0.5, 1, 2, 5, 10];

export function ReplayPicker({ ctx, panelId, chartId, binding }: { ctx: ModuleContext; panelId: string; chartId: string; binding: () => ChartBinding | null }) {
  const [x, setX] = useState<number | null>(null);
  const pick = (offsetX: number) => {
    const at = binding()?.view?.timeAtX(offsetX) ?? null;
    if (at === null) return;
    setUi(panelId, { replayPick: null });
    post(ctx, "/replay/start", { chartId, at }).catch(error => reportChartError(chartId, message(error)));
  };
  return <div className="chart-replay-pick" role="button" aria-label="Pick the bar replay starts from" tabIndex={-1}
    onMouseMove={event => setX(event.nativeEvent.offsetX)} onMouseLeave={() => setX(null)} onClick={event => pick(event.nativeEvent.offsetX)}
    onKeyDown={event => { if (event.key === "Escape") setUi(panelId, { replayPick: null }); }}>
    {x !== null && <><div className="chart-replay-line" style={{ left: x }} /><div className="chart-replay-shade" style={{ left: x }} /></>}
  </div>;
}

export function ReplayBar({ ctx, panelId, chartId }: { ctx: ModuleContext; panelId: string; chartId: string }) {
  const replay = useStore(state => state.charts[chartId]?.replay);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const fail = (error: unknown) => { setPlaying(false); reportChartError(chartId, message(error)); };
  const step = (bars: number) => post(ctx, "/replay/step", { chartId, bars });
  // One step at a time: the next waits for the previous one's answer, so a slow step never piles up requests.
  const busy = useRef(false);
  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(() => {
      if (busy.current) return;
      busy.current = true;
      step(1).then(() => { busy.current = false; }, error => { busy.current = false; fail(error); });
    }, 1000 / speed);
    return () => clearInterval(timer);
  // The step and fail helpers only close over the chart, which keys this bar.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, speed]);
  useEffect(() => { if (replay?.end) setPlaying(false); }, [replay?.end]);
  if (!replay) return null;
  return <div className="chart-replay-bar tx-glass" role="toolbar" aria-label="Replay">
    <TxTooltip label="Select bar"><TxButton variant="tool" size="sm" aria-label="Select bar" onClick={() => { setPlaying(false); setUi(panelId, { replayPick: chartId }); }}><ChartGlyph name="replay" /></TxButton></TxTooltip>
    <span className="chart-tb-divider" />
    <TxIconButton variant="tool" size="sm" icon={playing ? "pause" : "play"} label={playing ? "Pause" : "Play"} disabled={replay.end} onClick={() => setPlaying(value => !value)} />
    <TxTooltip label="Forward"><TxButton variant="tool" size="sm" aria-label="Forward" disabled={replay.end || playing} onClick={() => void step(1).catch(fail)}><ChartGlyph name="step-forward" /></TxButton></TxTooltip>
    <TxMenu label="Replay speed" items={SPEEDS.map(value => ({ id: String(value), label: `${value} bar${value === 1 ? "" : "s"} a second`, checked: value === speed, onSelect: () => setSpeed(value) }))}
      trigger={<TxButton variant="tool" size="sm" aria-label="Replay speed">{speed}×</TxButton>} />
    {replay.end && <span className="chart-muted">No more bars</span>}
    <span className="chart-tb-divider" />
    <TxButton variant="tool" size="sm" onClick={() => { setPlaying(false); post(ctx, "/replay/stop", { chartId }).catch(fail); }}><ChartGlyph name="return-live" />Jump to real time</TxButton>
  </div>;
}
