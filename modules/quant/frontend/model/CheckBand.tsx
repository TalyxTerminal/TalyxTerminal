/* The check before training and the time axis it draws: warm-up, period, held-out period and each window's training, validation and test with the
   gaps between them; windows that are thin or peek are marked. Narrow shows only what did not pass; wide and full show every check. */
import type { ReactNode } from "react";
import { TxChecks, TxIcon } from "@talyx/sdk";
import type { Axis, Check, CheckAnswer } from "./api";
import type { Width } from "./Prep";

const my = (stamp: number) => new Date(stamp).toLocaleString("en-US", {month: "short", year: "numeric", timeZone: "UTC"});

export function Timeline({axis, width = "narrow"}: {axis: Axis; width?: Width}) {
  const span = Math.max(axis.end - axis.start, 1);
  const at = (stamp: number) => `${Math.max(0, Math.min(100, ((stamp - axis.start) / span) * 100))}%`;
  const wide = (from: number, to: number) => `${Math.max(0.3, Math.min(100, ((Math.min(to, axis.end) - from) / span) * 100))}%`;
  const seg = (kind: string, from: number, to: number, title: string) => <i key={`${kind}-${from}`} data-kind={kind} title={title} style={{left: at(from), width: wide(from, to)}} />;
  const track = (children: ReactNode) => <div className="mx-tl2track">{axis.heldOut && <i className="mx-tl2ho" data-kind="" style={{left: at(axis.heldOut.from), width: wide(axis.heldOut.from, axis.heldOut.to), background: "rgba(255,255,255,.035)", top: -2, bottom: -2}} />}{children}</div>;
  const years: number[] = [];
  for (let year = new Date(axis.start).getUTCFullYear(); year <= new Date(axis.end).getUTCFullYear(); year += 1) if (Date.UTC(year, 0, 1) >= axis.start) years.push(year);
  const flagged = axis.windows.filter(item => item.flag).map(item => item.k - 1), last = axis.windows.length - 1;
  const pick = width === "narrow" ? [...new Set([0, last])] : [...new Set([0, 1, ...flagged, last])].filter(index => index >= 0 && index <= last).sort((x, y) => x - y);
  const rows: ReactNode[] = [];
  let previous = -1;
  pick.forEach(index => {
    const item = axis.windows[index];
    if (index - previous > 1) rows.push(<div key={`gap${index}`} className="mx-tl2row mx-tl2more"><span>⋯</span><span>{index - previous - 1} more, {axis.every}</span></div>);
    rows.push(<div key={item.k} className="mx-tl2row" data-flag={item.flag ? "" : undefined}><span title={item.flag}>{width === "narrow" ? `W${item.k}` : `Window ${item.k}`}</span>
      {track(<>{seg(item.flag ? "bad" : "train", item.train.from, item.train.to, `Window ${item.k}: training ${my(item.train.from)} – ${my(item.train.to)}${item.flag ? ` · ${item.flag}` : ""}`)}
        {seg("valid", item.valid.from, item.valid.to, `Window ${item.k}: validation`)}{seg("test", item.test.from, item.test.to, `Window ${item.k}: test ${my(item.test.from)} – ${my(item.test.to)}`)}</>)}</div>);
    previous = index;
  });
  return <div className="mx-tl2" data-width={width} role="img" aria-label="Time axis of the windows">
    <div className="mx-tl2row mx-tl2axis"><span />{track(years.map(year => <b key={year} style={{left: at(Date.UTC(year, 0, 1))}}>{width === "full" ? year : width === "narrow" && year % 2 ? "" : `'${String(year).slice(2)}`}</b>))}</div>
    <div className="mx-tl2row"><span>Data</span>{track(<>{seg("warm", axis.warmUp.from, axis.warmUp.to, "Warm-up")}{seg("period", axis.period.from, axis.period.to, `${my(axis.period.from)} – ${my(axis.period.to)}`)}
      {axis.heldOut && seg("held", axis.heldOut.from, axis.heldOut.to, `Held out from ${my(axis.heldOut.from)}`)}</>)}</div>
    {axis.inputs.map(item => <div key={item.name} className="mx-tl2row" title={item.note}><span>{item.name}</span>{track(<>
      {item.missingTo && seg("missing", axis.period.from, item.missingTo, `${item.name}: ${item.note}`)}{seg(item.peek ? "peek" : "input", item.missingTo ?? axis.period.from, axis.period.to, item.note)}</>)}</div>)}
    {rows}
    <div className="mx-tllegend"><span>{my(axis.start)}</span><span>{axis.count} {axis.count === 1 ? "window" : "windows"} · {axis.every} · gap {axis.gap} bars</span><span>{my(axis.end)}</span></div>
  </div>;
}

export function CheckBand({check, checking, width, onGo}: {check: CheckAnswer | null; checking: boolean; width: Width; onGo(group: string): void}) {
  if (check === null) return <div className="mx-checks" data-state="wait"><div className="mx-ckhead"><b>Checks</b><span className="mx-t3">{checking ? "checking…" : "waiting for a universe, inputs and an algorithm"}</span></div></div>;
  if (!check.ok && check.failure) return <div className="mx-checks"><div className="mx-ckhead"><b>Checks</b></div><div className="mx-ck" data-state="block"><TxIcon name="alert" /><span>{check.failure.title}</span><span className="mx-t3">{check.failure.message}</span></div></div>;
  const open = (item: Check) => item.state !== "ok";
  const shown = width === "narrow" ? check.checks.filter(open) : check.checks;
  const hidden = check.checks.length - shown.length;
  const need = check.checks.filter(item => item.state === "block" || item.state === "fix").length, review = check.checks.filter(item => item.state === "watch").length;
  return <div className="mx-checks" data-width={width} data-state={need ? "fix" : review ? "watch" : "ok"}>
    <div className="mx-ckhead"><b>{width === "narrow" ? "Checks" : "Check before training"}</b>{checking && <span className="mx-t3">checking…</span>}{need > 0 && <span className="mx-ckfix">{need} to fix</span>}{review > 0 && <span className="mx-ckwatch">{review} to review</span>}
      {!need && !review && <span className="mx-t3">all pass</span>}{check.seconds ? <span className="tx-spacer" /> : null}{check.seconds ? <span className="mx-t3">{check.seconds.toFixed(1)} s</span> : null}
      {width !== "narrow" && <span className="mx-legend">{[["train", "Train"], ["valid", "Validation"], ["test", "Test"], ["warm", "Warm-up"], ["held", "Held out"]].map(([kind, text]) => <span key={kind}><i data-kind={kind} />{text}</span>)}</span>}</div>
    <div className="mx-bandbody" data-width={width}>
    {check.axis && <Timeline axis={check.axis} width={width} />}
    <TxChecks title="" groups={[{items: shown.map(item => ({name: item.name, value: item.value, need: item.state === "block" ? "Blocks sending" : width !== "narrow" ? item.need : undefined, state: item.state === "block" ? "fix" as const : item.state, title: item.detail, onClick: () => onGo(item.group)}))}]} />
    </div>
    {hidden > 0 && <div className="mx-t3 mx-more">{hidden} more pass</div>}
  </div>;
}
