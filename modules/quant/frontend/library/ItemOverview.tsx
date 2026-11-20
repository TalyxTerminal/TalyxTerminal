/* The Overview tab: the record after publication (cumulative chart, or an allocation's volatility and risk shares), published against after, the production strip, who uses which version and what feeds what (library.js overview). */
import { TxSegmented, TxTable, TxTd, TxTh } from "@talyx/sdk";
import { at, hm, md, mdy, nf, pc, pct, pp, sgn, tone } from "./format";
import type { After, Figure, ItemDetail, ItemVersion, Period, Published, UserRow } from "./api";
import { PeriodDetail, ProductionStrip, counts, periodUnit } from "./Production";
import { Plot } from "../chart/Plot";

const isFigure = (value: Figure | undefined): value is Exclude<Figure, {missing: string}> => value !== undefined && !("missing" in value);

/** The three charts the backend draws: the figure, or the reason this one is not there. */
export function ItemFigures({figures}: {figures: {cumulative?: Figure; volatility?: Figure; riskShares?: Figure}}) {
  const entries: [string, Figure | undefined, string, number][] = [["cumulative", figures.cumulative, "Cumulative record since publication", 230], ["volatility", figures.volatility, "Realised volatility", 190], ["riskShares", figures.riskShares, "Risk shares", 190]];
  return <>{entries.filter(([, figure]) => figure !== undefined).map(([key, figure, label, height]) => isFigure(figure)
    ? <div className="lb-chart" key={key} data-chart={key}><Plot figure={figure} ariaLabel={label} height={height} /></div>
    : <div className="lb-calm" key={key} title={(figure as {missing: string}).missing}>{(figure as {missing: string}).missing.split(":")[0]}</div>)}</>;
}

const Row = ({label, a, b, d, title}: {label: string; a?: React.ReactNode; b?: React.ReactNode; d?: React.ReactNode; title?: string}) =>
  <tr title={title}><TxTd>{label}</TxTd><TxTd align="right">{a}</TxTd><TxTd align="right">{b}</TxTd><TxTd align="right">{d}</TxTd></tr>;
const signed = (value: number | null | undefined, digits: number) => value == null ? "" : <span className={tone(value)}>{sgn(value, digits)}</span>;

/** Published against after, the figures of the publication on the left of what has settled since; after shows from sixty settled days. */
export function AfterTable({measure, kind, published, after, collecting, need, auto = false, weak = false}: {measure: string | null; kind: string; published: Published | null; after: After | null; collecting: boolean; need: number; auto?: boolean; weak?: boolean}) {
  const none = auto ? "none yet" : "none, auto-update off";
  const hidden = collecting || after === null || after.settled === 0 && after.value === null && kind !== "allocation";
  if (kind === "allocation") {
    const live = after?.value != null, benchmark = after?.benchmark;
    const since = (value: number | null | undefined, word: string) => value == null ? "" : <><span className={tone(value)}>{pp(value, 1)}</span> <span className="lb-t3">{word}</span></>;
    // As wide as the draft's (a row of its own up to 620 px): "−14.7 pt a year" and "+2.5 pt since" fit their columns.
    return <TxTable className="lb-pt"><colgroup><col /><col style={{width: 136}} /><col style={{width: 120}} /><col style={{width: 84}} /></colgroup>
    <thead><tr><TxTh /><TxTh align="right">Published</TxTh><TxTh align="right">{live ? "Now" : "After"}</TxTh><TxTh align="right">Change</TxTh></tr></thead>
    <tbody><Row label={live ? "Volatility, 63 days" : "Volatility"} a={published?.value == null ? "" : pct(published.value, 1)} b={after?.value == null ? "" : pct(after.value, 1)}
      d={after?.multiple == null ? "" : <span className={after.multiple > 1.5 ? "lb-watch" : "lb-t2"}>{`${nf(after.multiple, 2)}×`}</span>} title="Flag above 1.5× published" />
      {after?.worstShare && <Row label={`Risk share, ${after.worstShare.name}`} a={pct(after.worstShare.published, 1)} b={pct(after.worstShare.now, 1)}
        d={<span className={Math.abs(after.worstShare.delta) > 0.1 ? "lb-watch" : "lb-t2"}>{pp(after.worstShare.delta, 1)}</span>} title="The asset whose risk share moved most; flag above 10 pt" />}
      {live && published?.turnover != null && <Row label="Turnover a year" a={pct(published.turnover, 0)} b={after?.turnover == null ? "" : pct(after.turnover, 0)}
        d={after?.turnover == null ? "" : <span className="lb-t2">{pp(after.turnover - published.turnover, 1)}</span>} title={after?.turnover == null ? "No rebalance since publication yet" : undefined} />}
      {published?.sharpe != null && <Row label="Sharpe" a={nf(published.sharpe, 2)} b={live && after?.sharpe != null ? <span className={after.settled < 126 ? "lb-t3" : undefined} title={after.settled < 126 ? "Under 126 trading days a Sharpe says little" : undefined}>{nf(after.sharpe, 2)}</span> : ""} />}
      {live && benchmark && <Row label={`Return vs ${benchmark.name}`} a={since(benchmark.published, "a year")} b={since(benchmark.since, "since")} />}
      {!live && published?.maxDrawdown != null && <Row label="Max drawdown" a={<span className={tone(published.maxDrawdown)}>{pc(published.maxDrawdown, 1)}</span>} />}
      <tr><TxTd>Periods</TxTd><TxTd className="lb-t3">in-sample</TxTd><TxTd className="lb-t3" colSpan={2}>{after ? `${after.settled} trading days${after.rebalances != null ? ` · ${after.rebalances} rebalance${after.rebalances === 1 ? "" : "s"}` : ""}` : none}</TxTd></tr></tbody></TxTable>;
  }
  const delta = (now: number | null | undefined, then: number | null | undefined, digits: number) => hidden || now == null || then == null ? "" : sgn(now - then, digits);
  return <><TxTable className="lb-pt"><colgroup><col /><col style={{width: 78}} /><col style={{width: 82}} /><col style={{width: 88}} /></colgroup>
    <thead><tr><TxTh /><TxTh align="right">Published</TxTh><TxTh align="right">After</TxTh><TxTh align="right">Change</TxTh></tr></thead>
    <tbody>
      <Row label={measure ?? "Rank IC"} a={signed(published?.value, 4)} b={hidden || after?.value == null ? "" : weak ? <span className="lb-watch">{sgn(after.value, 4)}</span> : signed(after.value, 4)} d={hidden ? "" : <span className={tone((after?.value ?? 0) - (published?.value ?? 0))}>{delta(after?.value, published?.value, 4)}</span>} />
      {(published?.se != null || (!hidden && after?.se != null)) && <Row label="Standard error" a={published?.se == null ? "" : `± ${nf(published.se, 4)}`} b={hidden || after?.se == null ? "" : `± ${nf(after.se, 4)}`} title="Daily IC, adjusted for the overlapping horizon" />}
      <Row label="t" a={published?.t == null ? "" : nf(published.t, 2)} b={hidden || after?.t == null ? "" : nf(after.t, 2)} d={delta(after?.t, published?.t, 2)} />
      <Row label="Long–short, after costs, a year" a={published?.ls == null ? "" : <span className={tone(published.ls)}>{`${sgn(published.ls * 100, 1)}%`}</span>} b={hidden || after?.ls == null ? "" : <span className={tone(after.ls)}>{`${sgn(after.ls * 100, 1)}%`}</span>}
        d={hidden || after?.ls == null || published?.ls == null ? "" : pp(after.ls - published.ls, 1)} />
      <Row label="Names a day" a={published?.names == null ? "" : nf(published.names, 0)} b={hidden || after?.names == null ? "" : nf(after.names, 0)} d={delta(after?.names, published?.names, 0)} />
      {published?.heldOut != null && <Row label={`Held-out ${measure ?? "Rank IC"}`} a={signed(published.heldOut, 4)} title="Validated before publication" />}
      {/* While collecting, After stays empty until `need` trading days have settled: the row says how far it is (its title says from when). */}
      <tr><TxTd>Periods</TxTd><TxTd className="lb-t3">in-sample</TxTd><TxTd className="lb-t3" colSpan={2} title={collecting ? `After-publication figures show from ${need} trading days` : undefined}>
        {collecting && after !== null ? `${after.settledDays ?? 0}/${need} days · ${after.pending} pending` : after && (after.settled > 0 || after.pending > 0) ? `${after.settled} settled · ${after.pending} pending` : none}</TxTd></tr>
    </tbody></TxTable></>;
}

export function UsersBlock({detail, onOpenUser}: {detail: ItemDetail; onOpenUser(user: UserRow): void}) {
  const newest = detail.versions.find(version => version.state === "producing" || version.state === "fixed");
  const rows = detail.versions.flatMap(version => version.users.map(user => ({version, user})));
  if (rows.length === 0) return null;   // nobody uses it: nothing to list
  return <><div className="lb-sub"><b title="Each stays on its version until switched there">Who uses which version</b></div>
    {rows.map(({version, user}) => <div className="lb-usr" key={`${version.versionId}-${user.ownerId}`}>
      <span>{user.name}</span><span className="lb-t3">{user.kind.charAt(0).toUpperCase() + user.kind.slice(1)}{user.unattended ? " · unattended" : ""}</span><span className="lb-num lb-t2">{`v${version.version}`}</span>
      <span className="lb-note" data-attn={version.state === "stopped" ? "" : undefined}>{version.state === "stopped" ? `v${version.version} stopped${version.stoppedAt ? ` ${md(version.stoppedAt)}` : ""}${newest ? `; told v${newest.version} is available` : ""}`
        : newest && newest.version > version.version ? `v${newest.version} is newer; stays on v${version.version} until switched there` : user.how}</span>
      <button type="button" className="lb-link" onClick={() => onOpenUser(user)}>Open</button></div>)}</>;
}

function ChainBlock({detail, version, onOpenVersion}: {detail: ItemDetail; version: ItemVersion; onOpenVersion(versionId: string): void}) {
  if (detail.summary.kind === "model" && version.inputs.length > 0) return <><div className="lb-sub"><b>Inputs</b></div>
    <div className="lb-chain">{version.inputs.map(input => <span className="lb-nd" key={input.versionId} role="button" onClick={() => onOpenVersion(input.versionId)}><i data-state={input.state} />{input.name} <span className="lb-t3 lb-num">{`v${input.version}`}</span></span>)}
      <span className="lb-ar">→</span><span className="lb-nd" data-me="">{detail.summary.name} {`v${version.version}`}</span></div></>;
  if (detail.feeds.length === 0) return null;
  return <><div className="lb-sub"><b>Feeds</b></div><div className="lb-chain"><span className="lb-nd" data-me="">{detail.summary.name}</span>{detail.feeds.map(feed => <span key={feed.versionId} className="lb-chain-part"><span className="lb-ar">→</span>
    <span className="lb-nd" role="button" onClick={() => onOpenVersion(feed.versionId)}>{feed.name} <span className="lb-t3 lb-num">{`v${feed.version}`}</span></span>{feed.users[0] && <><span className="lb-ar">→</span><span className="lb-nd">{feed.users[0].name}</span></>}</span>)}</div></>;
}

export interface OverviewHandlers {
  onMetric(metric: "ic" | "ls"): void; onSpan(span: "since" | "all"): void; onPeriod(label: number): void; onValues(label: number): void; onLog(): void; onRetry: (() => void) | null;
  onOpenUser(user: UserRow): void; onOpenVersion(versionId: string): void;
}

export function ItemOverview({detail, version, need, metric, span, period, handlers}: {detail: ItemDetail; version: ItemVersion; need: number; metric: "ic" | "ls"; span: "since" | "all"; period: number | null; handlers: OverviewHandlers}) {
  const kind = detail.summary.kind;
  const periods = detail.periods;
  if (version.state === "archived") return <div className="lb-pad"><div className="lb-iss" data-tone="calm"><div className="lb-iss-head"><b>{`v${version.version} archived ${version.archivedAt ? mdy(version.archivedAt) : ""}`}</b></div>
    <div className="lb-why"><span>Kept</span><span>this record, the frozen code and settings, the published figures</span><span>Dropped</span><span>the stored values</span>
      <span>Published</span><span>{`${mdy(version.publishedAt)}${version.origin?.runNumber ? ` from #${version.origin.runNumber}` : ""}; ${version.measure ?? ""} ${version.pub?.value == null ? "" : sgn(version.pub.value, 4)}${version.pub?.t == null ? "" : `, t ${nf(version.pub.t, 2)}`}`}</span></div></div></div>;
  if (kind === "data") return <div className="lb-pad"><div className="lb-sub"><b>What it is</b><span>put in from a notebook; it does not update</span></div>
    <div className="lb-iss" data-tone="calm"><div className="lb-why"><span>From</span><span>{`${detail.origin?.objectName ? `notebook ${detail.origin.objectName}, ` : ""}${mdy(version.publishedAt)}`}</span><span>Rows</span><span>{nf(version.rows, 0)}</span><span>Content</span><span>{version.settingsText}</span></div></div>
    <UsersBlock detail={detail} onOpenUser={handlers.onOpenUser} /></div>;
  const selected = Math.max(0, periods.findIndex(item => item.label === period));
  const shown: Period | undefined = period === null ? periods[periods.length - 1] : periods[selected];
  const tally = counts(periods);
  const figures = detail.figures ?? {};
  const signal = kind === "factor" || kind === "model";
  const drawn = Boolean(figures.cumulative && !("missing" in (figures.cumulative as object)));
  // Many assets: the chart draws the largest and says how many of all.
  const shareMeta = (figures.riskShares as {layout?: {meta?: {shown?: number; of?: number}}} | undefined)?.layout?.meta;
  const shareCount = shareMeta?.of && shareMeta.shown && shareMeta.of > shareMeta.shown ? `the ${shareMeta.shown} largest of ${shareMeta.of}` : null;
  const table = <><div className="lb-sub"><b>{kind === "allocation" ? "Published and now" : "Published and after"}</b><span>{`v${version.version}${version.origin?.runNumber ? ` from #${version.origin.runNumber}` : ""}, ${mdy(version.publishedAt)}`}</span></div>
    <AfterTable measure={version.measure} kind={kind} published={version.pub} after={version.after} collecting={version.collecting} need={need} auto={version.auto} weak={version.health === "weak"} /></>;
  return <div className="lb-pad">
    {kind === "allocation" ? <>
      {/* library.js: volatility since publication beside the risk shares, then published against now. */}
      <div className="lb-ov lb-ov2">
        <section><div className="lb-sub tx-regionhead"><b>Volatility since publication</b><span className="lb-sp" /><span className="tx-regionkeys" data-tx-keys="" /></div><ItemFigures figures={{volatility: figures.volatility}} /></section>
        <section><div className="lb-sub tx-regionhead"><b>Risk shares</b>{shareCount && <span>{shareCount}</span>}<span className="lb-sp" /><span className="tx-regionkeys" data-tx-keys="" /></div><ItemFigures figures={{riskShares: figures.riskShares}} /></section>
      </div>
      <div className="lb-palloc">{table}</div>
    </> : <div className={drawn ? "lb-ov" : undefined}>
      {/* library.js: the measure and span only beside a chart that draws; with auto-update off since publication the section is "Since publication".
          Nothing to draw yet (no settled period since publication): the title and its choices stay, the table takes the width, no line of text in a half. */}
      <section>{signal && <div className="lb-sub lb-wrap tx-regionhead"><b>{!version.auto && !version.after?.settled && !version.after?.pending ? "Since publication" : "Cumulative"}</b>
        <><TxSegmented label="Measure" value={metric} onChange={handlers.onMetric} options={[{value: "ic", label: version.measure ?? "Rank IC"}, {value: "ls", label: "Long–short"}]} />
        <TxSegmented label="Span" value={span} onChange={handlers.onSpan} options={[{value: "since", label: "Since publication"}, {value: "all", label: "Whole history"}]} /></><span className="lb-sp" />
        {/* The chart puts its own key here (settled, at the published level, pending), as every chart's key sits beside its section's title. */}
        <span className="tx-regionkeys" data-tx-keys="" /></div>}
        {drawn && <ItemFigures figures={figures} />}</section>
      <div>{table}</div>
    </div>}
    {periods.length > 0 ? <><div className="lb-sub"><b>Production</b><span>{`last ${Math.min(60, periods.length)} ${periodUnit(detail.summary.freq, Math.min(60, periods.length))}`}</span><span className="lb-sp" />
        <span>{version.state === "producing" && !version.auto ? "auto-update off" : version.state === "producing" ? (detail.nextDue ? `next ${detail.nextDue.why === "retry" ? "retry " : ""}${new Date(detail.nextDue.at).toISOString().slice(0, 10) === new Date().toISOString().slice(0, 10) ? hm(detail.nextDue.at) : at(detail.nextDue.at)}` : "") : `stopped${version.stoppedAt ? ` ${mdy(version.stoppedAt)}` : ""} by you`}</span></div>
      <div className="lb-prodrow"><div className="lb-stripcol"><ProductionStrip periods={periods} selected={period === null ? periods.length - 1 : selected} onSelect={index => handlers.onPeriod(periods[index].label)} />
        <div className="lb-scap"><span>{md(periods[0].label)}</span><span>{md(periods[periods.length - 1].label)}</span></div></div>
        <div className="lb-pnum"><span><b>{tally.ok}</b>on time</span><span><b>{tally.late}</b>late</span><span><b>{tally.failed}</b>failed</span><span><b>{tally.missing}</b>missing</span></div></div>
      {shown && <PeriodDetail period={shown} onValues={() => handlers.onValues(shown.label)} onLog={handlers.onLog} onRetry={handlers.onRetry} />}</>
      : <div className="lb-sub"><b>Production</b><span>{version.state === "stopped" ? `stopped${version.stoppedAt ? ` ${mdy(version.stoppedAt)}` : ""}; no periods since publication`
        : version.auto ? "first period on the next close" : "auto-update off; no periods since publication"}</span></div>}
    <div className="lb-ov lb-ov2"><div><UsersBlock detail={detail} onOpenUser={handlers.onOpenUser} /></div><div><ChainBlock detail={detail} version={version} onOpenVersion={handlers.onOpenVersion} /></div></div>
  </div>;
}
