/* Walk-forward: the out-of-sample equity stitched from the windows, and what each window picked and did. */
import {TxButton, TxIcon, TxKeyNumbers, TxRegionHead} from "@talyx/sdk";
import {Fig} from "./Fig";
import {METRIC_LABEL, axisLabel, cls, monthYear, nf, sgnPc, valueText} from "./format";
import type {SweepDetail, Walk, WalkWindow} from "./types";

/** What the Sharpe-picked-on cell says: the number, or that no combination traded in the picking months. */
export const pickedText = (window: WalkWindow) => window.kept ? "none traded" : nf(window.sharpePicked, 2);
/** The values the window traded: its own pick, or "#2's values" when it kept those of the window before. */
export const valuesText = (window: WalkWindow, axes: SweepDetail["axes"]) => window.kept ? `#${window.kept}'s values` : axes.map(axis => valueText(window.values[axis.name])).join(" · ");

/** A window's out-of-sample Sharpe: 0 when it placed no trade (it made nothing; leaving it out let one traded window stand for all), else its own. */
export const tradedSharpe = (window: WalkWindow) => window.sharpeTraded ?? (window.trades === 0 ? 0 : null);

export function walkNumbers(walk: Walk) {
  const traded = walk.windows.filter(item => tradedSharpe(item) != null), picks = walk.windows.filter(item => item.sharpePicked != null);
  const mean = (values: number[]) => values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
  const stitched = walk.stitched;
  const total = stitched.length > 1 ? stitched[stitched.length - 1][1] / stitched[0][1] - 1 : null;
  return {
    total, sharpeOut: mean(traded.map(item => tradedSharpe(item) as number)), sharpeIn: mean(picks.map(item => item.sharpePicked as number)),
    from: stitched[0]?.[0], to: stitched[stitched.length - 1]?.[0], maxDrawdown: stitched.length < 2 ? null : stitched.reduce((state, [, value]) => { const peak = Math.max(state.peak, value); return {peak, low: Math.min(state.low, value / peak - 1)}; }, {peak: 0, low: 0}).low,  // nothing traded yet: no number, not 0.0%
  };
}

/** The window a stopped walk-forward, or one a window of which failed, goes on from: the first that did not run. Null when it cannot go on. */
export function resumeFrom(detail: SweepDetail) {
  const windows = detail.walk?.windows ?? [];
  const failed = windows.some(item => item.failed);
  if (!failed && detail.state !== "cancelled" && detail.state !== "interrupted") return null;
  const ran = windows.findIndex((item, at) => item.n !== at + 1 || item.trades === null);
  return ran === -1 ? windows.length + 1 : ran + 1;
}

export function WalkView({detail, onResume}: {detail: SweepDetail; sourceNumber?: number | null; onResume?(): void}) {
  const walk = detail.walk;
  const from = resumeFrom(detail);
  const resume = from !== null && onResume ? <div className="sv-resume"><TxButton size="sm" onClick={onResume}><TxIcon name="play" />Resume from window {from}</TxButton></div> : null;
  if (!walk || walk.windows.length === 0) return <>{resume}<p className="sv-why" role="status">This walk-forward has no windows yet</p></>;
  const n = walkNumbers(walk);
  const items = [
    {label: "Return, out of sample", value: sgnPc(n.total, 1), tone: n.total == null ? undefined : n.total < 0 ? "down" as const : "up" as const, delta: n.from ? `${monthYear(n.from)} – ${monthYear(n.to!)}` : undefined},
    {label: "Sharpe, out of sample", value: nf(n.sharpeOut, 2)},
    {label: "Sharpe, picked on", value: nf(n.sharpeIn, 2), delta: "mean of the windows"},
    {label: "Efficiency", value: walk.efficiency == null ? "" : nf(n.sharpeOut != null && n.sharpeIn != null && n.sharpeIn > 0 ? n.sharpeOut / n.sharpeIn : walk.efficiency, 2), delta: "needs 0.50"},  // from the windows, as the check judges it
    {label: "Windows", value: String(walk.windows.length), delta: walk.changed ? `values changed ${walk.changed}×` : undefined},
    {label: "Max drawdown", value: sgnPc(n.maxDrawdown, 1), delta: "out of sample"},
  ];
  return <div className="sv-walk">
    <TxKeyNumbers items={items} />
    {resume}
    <TxRegionHead title="Out of sample, stitched" sub={`each window traded with the values picked on the ${walk.pickOn} months before it`} />
    <Fig fig={detail.figures.walk} label="Walk-forward equity" />
    <TxRegionHead title="Windows" sub={String(walk.windows.length)} />
    <div className="sv-tablebox"><table className="sv-table"><thead><tr><th>Window</th><th className="l">Picked on</th><th className="l">Traded</th>{detail.axes.map(axis => <th key={axis.name}>{axisLabel(axis)}</th>)}
      <th>{METRIC_LABEL.sharpe}, picked on</th><th>{METRIC_LABEL.sharpe}, traded</th><th>Return, traded</th><th>Trades</th></tr></thead>
      <tbody>{walk.windows.map(window => <tr key={window.n}><td>{window.n}</td><td className="l dim">{monthYear(window.pickedOn[0])} – {monthYear(window.pickedOn[1])}</td><td className="l">{monthYear(window.traded[0])} – {monthYear(window.traded[1])}</td>
        {window.kept ? <td colSpan={detail.axes.length} className="dim">{valuesText(window, detail.axes)}</td> : detail.axes.map(axis => <td key={axis.name}>{valueText(window.values[axis.name])}</td>)}
        <td className="dim">{pickedText(window)}</td>{window.failed ? <td colSpan={3} className="l sv-failed">Failed: {window.failed}</td> : <><td className={cls(tradedSharpe(window))}>{nf(tradedSharpe(window), 2)}</td>
        <td className={cls(window.returnTraded)}>{sgnPc(window.returnTraded, 1)}</td><td className="dim">{window.trades ?? ""}</td></>}</tr>)}</tbody></table></div>
  </div>;
}
