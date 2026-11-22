import { translator } from "../i18n";
/* Research workbench pieces: one row on top (name, data, what runs, one run button, views, code, menu), the run strip,
   key numbers, checks, the two-layer parameter column with linked inputs, the runs table, tags, state words,
   per-period strips, the heatmap and in-place failures. Generic: nothing here knows a module. Styles: research.css.ts. */
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { TxButton, TxCheckbox, TxMenu, TxPopover, type TxMenuItem } from "./components";
import { TxIcon, type IconName } from "./icons";

const t = translator("platform");

const on = (value: boolean | undefined) => (value ? "" : undefined);

export function TxSpacer() {
  return <span className="tx-spacer" />;
}

export function TxObjectTitle({ name, kind, tags, onClick }: { name: string; kind: string; tags?: ReactNode; onClick?: () => void }) {
  return <button type="button" className="tx-objtitle" onClick={onClick}><b>{name}</b><span className="tx-objkind">{kind}</span>{tags}</button>;
}

/** One part of the data chip. Priority 2 parts hide first when the head is narrow, then priority 3; priority 1 truncates. */
export interface TxDataPart { text: string; strong?: boolean; lock?: boolean; priority?: 1 | 2 | 3; title?: string }

// ---------------------------------------------------------------- run button and progress
/** One kind of run in the run button's menu. A kind that cannot run gives the reason, shown under its name. */
export interface TxRunKind { id: string; label: string; icon?: IconName; hint?: string; disabled?: boolean; reason?: string; separated?: boolean; current?: boolean }

/** One run button whose label follows the chosen kind; the arrow opens the kinds. A locked button uses something once (a held-out period). */
export function TxRunButton({ label, kinds, onRun, onKind, locked, disabled, title }: {
  label: string; kinds: TxRunKind[]; onRun: () => void; onKind: (id: string) => void; locked?: boolean; disabled?: boolean; title?: string;
}) {
  const items: TxMenuItem[] = kinds.map(kind => ({
    id: kind.id, label: kind.label, icon: kind.icon, hint: kind.hint, disabled: kind.disabled, detail: kind.disabled ? kind.reason : undefined,
    separated: kind.separated, checked: kind.current, onSelect: () => onKind(kind.id),
  }));
  return <span className="tx-runbutton" data-locked={on(locked)} data-disabled={on(disabled)}>
    <button type="button" onClick={onRun} title={title ?? t("{label} (Ctrl+Enter)", { label })}>{locked ? <TxIcon name="lock" /> : <TxIcon name="play" data-fill="" />}{label}</button>
    <TxMenu label={t("Kind of run")} align="end" items={items} trigger={<button type="button" aria-label={t("Kind of run")}><TxIcon name="chevron" /></button>} />
  </span>;
}

export interface TxRunStep { label: string; state: "done" | "now" | "next"; time?: string }

/** What is running, in the head: run, short count, bar, time, Stop. The stage shows when the head is wide; the steps open on click. */
export function TxRunProgress({ run, stage, count, value, elapsed, steps, onStop }: {
  run: string; stage: string; count?: string; value?: number | null; elapsed?: string; steps?: TxRunStep[]; onStop?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const pill = <button type="button" className="tx-runprogress" title={stage}>
    <span className="tx-spin" /><b>{run}</b><span className="tx-runstage">{stage}</span>{count && <span className="tx-runcount">{count}</span>}
    {value != null && <span className="tx-runbar"><i style={{ width: `${Math.round(value * 100)}%` }} /></span>}
    {elapsed && <span className="tx-runtime">{elapsed}</span>}
    {onStop && <span role="button" tabIndex={0} className="tx-runstop" onClick={event => { event.stopPropagation(); onStop(); }}
      onKeyDown={event => { if (event.key === "Enter") { event.stopPropagation(); onStop(); } }}><TxIcon name="stop" />{t("Stop")}</span>}
  </button>;
  if (!steps) return pill;
  return <TxPopover open={open} onOpenChange={setOpen} trigger={pill} label={t("Steps")}>
    <div className="tx-runsteps">{steps.map((step, index) => <div key={index} data-state={step.state}>
      {step.state === "done" ? <TxIcon name="check" /> : step.state === "now" ? <span className="tx-spin" /> : <TxIcon name="circle" />}
      <span>{step.label}</span><span className="tx-steptime">{step.time ?? ""}</span>
    </div>)}</div>
  </TxPopover>;
}

/** A small running indicator inside a table cell. */
export function TxLive({ label, value }: { label: string; value?: number | null }) {
  return <span className="tx-live"><span className="tx-spin" />{label}{value != null && <span className="tx-runbar"><i style={{ width: `${Math.round(value * 100)}%` }} /></span>}</span>;
}
// ---------------------------------------------------------------- run strip: which run is shown, compared with what
/** The shown run as a menu of runs. */
export function TxRunPicker({ run, items }: { run: string; items: TxMenuItem[] }) {
  return <TxMenu label={t("Show another run")} items={items} trigger={<button type="button" className="tx-runpick">{run}<TxIcon name="chevron" /></button>} />;
}

/** The compared run as a menu; `run` null offers Compare. Its line's look is in the chart, by its name at the line's end. */
export function TxCompare({ run, items }: { run: string | null; items: TxMenuItem[] }) {
  return <TxMenu label={t("Compare with another run")} items={items} trigger={<button type="button" className="tx-compare">
    {run ? <>{t("vs")} <b>{run}</b></> : t("Compare")}<TxIcon name="chevron" />
  </button>} />;
}

export function TxRunStrip({ picker, when, meta, tags, notes, onNotes, compare, actions }: {
  picker: ReactNode; when: string; meta?: string[]; tags?: ReactNode; notes?: number; onNotes?: () => void; compare?: ReactNode; actions?: ReactNode;
}) {
  return <div className="tx-runstrip">{picker}<span className="tx-runwhen">{when}</span>
    {meta && <span className="tx-runmeta" title={meta.join(", ")}>{meta.map((item, index) => <span key={index}>{item}</span>)}</span>}{tags}
    <span className="tx-spacer" />
    {notes ? <button type="button" className="tx-notes" onClick={onNotes}>{t("Notes")} {notes}</button> : null}
    {compare}{actions}
  </div>;
}

// ---------------------------------------------------------------- key numbers
/** One headline number: label, value with its unit, the change against the compared run. A number with onClick opens the series behind it. */
export interface TxKeyNumber { label: string; value?: ReactNode; unit?: string; delta?: ReactNode; tone?: "up" | "down"; title?: string; onClick?: () => void; pending?: string | true }

/** A delta against a compared run: the change carries its own sign and the value's decimals. */
export function txDelta(change: string, run: string): ReactNode {
  return <>{change} <em>{t("vs")} {run}</em></>;
}

export function TxKeyNumbers({ items, columns }: { items: TxKeyNumber[]; columns?: number }) {
  return <div className="tx-keynumbers" style={{ "--tx-count": columns ?? items.length } as CSSProperties}>
    {items.map((item, index) => {
      const pending = item.pending !== undefined;
      const body = <><div className="tx-knl">{item.label}</div>
        <div className="tx-knv">{pending ? "—" : <><span className={item.tone === "up" ? "tx-up" : item.tone === "down" ? "tx-down" : undefined}>{item.value}</span>{item.unit && <small>{item.unit}</small>}</>}</div>
        <div className="tx-knd">{pending ? (item.pending === true ? t("when done") : item.pending) : item.delta}</div></>;
      return item.onClick && !pending
        ? <button key={index} type="button" title={item.title} onClick={item.onClick}>{body}</button>
        : <div key={index} title={item.title} data-pending={on(pending)}>{body}</div>;
    })}
  </div>;
}

// ---------------------------------------------------------------- checks
/** ok passes quietly; watch is to review (amber); fix is to fix now (orange); notrun offers to run it. */
export interface TxCheck { name: string; value?: ReactNode; need?: string; state: "ok" | "watch" | "fix" | "notrun"; title?: string; onClick?: () => void; runLabel?: string }

/**
 * The checks of a run. `layout="table"` is the dense form: each group a table with Check, Value, Needs and Result, 23 px rows,
 * the groups stacked; where the region around it is narrow (a container under 1000 px) the groups sit side by side under the charts.
 */
export function TxChecks({ title = t("Checks"), scope, groups, layout = "rows" }: { title?: string; scope?: string; groups: { title?: string; items: TxCheck[] }[]; layout?: "rows" | "table" }) {
  const all = groups.flatMap(group => group.items);
  const fix = all.filter(item => item.state === "fix").length, watch = all.filter(item => item.state === "watch").length, notrun = all.filter(item => item.state === "notrun").length;
  const head = <header><h3>{title}</h3>{fix > 0 && <span className="tx-ckfix">{t("{count} to fix", { count: fix })}</span>}{watch > 0 && <span className="tx-ckwatch">{t("{count} to review", { count: watch })}</span>}
    {!fix && !watch && <span className="tx-ckscope" style={{ marginLeft: 0 }}>{notrun === all.length ? t("not run yet") : notrun ? t("{value} passed, {notrun} not run", { value: all.length - notrun, notrun }) : layout === "table" ? t("all pass") : t("all passed")}</span>}{scope && <span className="tx-ckscope">{scope}</span>}</header>;
  if (layout === "table") return <section className="tx-checklist" data-layout="table">{head}
    <div className="tx-cktables">{groups.map((group, index) => <table key={index} className="tx-cktable">
      <colgroup><col style={{ width: "31%" }} /><col style={{ width: "24%" }} /><col style={{ width: "22%" }} /><col style={{ width: "23%" }} /></colgroup>
      <thead><tr><th data-align="left">{t("Check")}</th><th>{t("Value")}</th><th>{t("Needs")}</th><th data-align="left">{t("Result")}</th></tr></thead>
      <tbody>{group.items.map(item => <tr key={item.name} data-state={item.state} data-go={on(Boolean(item.onClick))} title={item.title} onClick={item.onClick}>
        <td data-align="left" className="tx-ckname">{item.name}</td><td className="tx-ckvalue">{item.state === "notrun" ? "" : item.value}</td><td className="tx-ckneed">{item.need ?? ""}</td>
        <td data-align="left" className="tx-ckresult">{item.state === "ok" ? <span><TxIcon name="check" />{t("Pass")}</span> : item.state === "notrun" ? <span>{t("Not run")}</span> : <span><i />{t("Not passed")}</span>}</td>
      </tr>)}</tbody></table>)}</div>
  </section>;
  return <section className="tx-checklist">
    {head}
    {groups.map((group, index) => <div key={index}>{group.title && <div className="tx-ckgroup">{group.title}</div>}
      {group.items.map(item => <button key={item.name} type="button" className="tx-check-row" data-state={item.state} title={item.title} onClick={item.onClick}>
        <span>{item.state === "ok" ? <TxIcon name="check" /> : <i />}</span>
        <span className="tx-ckname">{item.name}</span>
        <span className="tx-ckvalue">{item.state === "notrun" ? <span>{item.runLabel ?? t("Run")}</span> : item.value}</span>
        <span className="tx-ckneed">{item.need ?? ""}</span>
        <span><TxIcon name="chevron" /></span>
      </button>)}
    </div>)}
  </section>;
}
// ---------------------------------------------------------------- parameter column: linked inputs, then the code's settings, then the platform's
export function TxParams({ children, footer }: { children: ReactNode; footer?: ReactNode }) {
  return <aside className="tx-params"><div className="tx-params-scroll">{children}</div>{footer && <footer>{footer}</footer>}</aside>;
}

/** One layer of settings: declared in the code, or given by the platform for every run. Counts values changed since the shown run. */
export function TxParamLayer({ title, source, note, changed, onReset }: { title: string; source?: string; note?: string; changed?: number; onReset?: () => void }) {
  return <div className="tx-params-layer"><h3>{title}</h3>{source && <span className="tx-layersource">{source}</span>}{note && <span className="tx-layernote">{note}</span>}
    {changed ? <span className="tx-layerstate"><span className="tx-layerchanged">{changed} {t("changed")}</span><button type="button" className="tx-layerreset" onClick={onReset}>{t("Reset")}</button></span> : null}</div>;
}

/** A folding group of settings; folded, it shows a one-line summary. */
export function TxParamGroup({ title, summary, changed, defaultOpen = true, children }: { title: string; summary?: string; changed?: boolean; defaultOpen?: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return <div className="tx-params-group" data-closed={on(!open)}>
    <h4 onClick={() => setOpen(!open)}><TxIcon name="chevron" /><span>{title}</span>{changed && <span className="tx-groupdot" />}{summary && <span className="tx-groupsummary" title={summary}>{summary}</span>}</h4>
    {children}
  </div>;
}

/** One setting. `hint` is its meaning on hover. A value changed but not run has an orange edge. */
export function TxParamRow({ label, hint, changed, wide, onRevert, children }: { label?: string; hint?: string; changed?: boolean; wide?: boolean; onRevert?: () => void; children: ReactNode }) {
  return <div className="tx-params-row" data-changed={on(changed)} data-wide={on(wide)} data-revert={on(changed && !!onRevert)}>
    {!wide && <span className="tx-paramlabel" title={hint}>{label}</span>}{children}
    {changed && onRevert && <TxButton size="sm" variant="quiet" title={t("Restore last run value")} aria-label={t("Restore {value} to last run value", { value: label ?? t("row") })} onClick={onRevert}><TxIcon name="undo" /></TxButton>}
  </div>;
}

/** A read-only value in the column (a fee read from another module, the held-out start). */
export function TxParamValue({ children, icon, text }: { children: ReactNode; icon?: IconName; text?: boolean }) {
  return <span className="tx-paramvalue" data-text={on(text)}>{icon && <TxIcon name={icon} />}{children}</span>;
}

/** What the object reads from elsewhere (a published version or a research run), with a newer version and the switch. */
export interface TxLinkedInput { name: string; kind: string; version: string; pinned?: string; warn?: string; newer?: string; where?: string; onOpen?: () => void; onSwitch?: () => void }

export function TxLinkedInputs({ title = t("Inputs used"), note, items }: { title?: string; note?: string; items: TxLinkedInput[] }) {
  return <><TxParamLayer title={title} note={note} /><div className="tx-linked">{items.map(item => <div key={item.name} className="tx-linked-item">
    <span className="tx-linkedname"><button type="button" title={t("Open where it came from")} onClick={item.onOpen}>{item.name}</button><TxTag tone="version">{item.version}</TxTag></span>
    <span className="tx-linkedsource" title={item.where}>{item.kind}{item.pinned ? t(", pinned {pinned}", { pinned: item.pinned }) : ""}</span>{item.warn && <span className="tx-linkedwarn" role="alert">{item.warn}</span>}
    <span className="tx-linkedaction">{item.newer && <TxButton size="sm" onClick={item.onSwitch}>{t("Switch to")} {item.newer}</TxButton>}</span>
  </div>)}</div></>;
}
// ---------------------------------------------------------------- runs table
/** hideBelow: the column hides while the table's box is narrower than this many pixels. */
export interface TxRunColumn { key: string; label: string; align?: "left" | "right"; kind?: "id" | "time" | "text" | "change" | "number"; sortable?: boolean; sorted?: boolean; hideBelow?: number }
export interface TxRunRow { id: string; group?: string; selected?: boolean; compared?: boolean; checked?: boolean; queued?: boolean; cells: Record<string, ReactNode> }
export interface TxRunGroup { id: string; title: string; summary?: string; closed?: boolean }

/** Every run of an object. Tick to compare (grey edge); the shown run has an orange edge; batches and sweeps fold into a group row. */
export function TxRunTable({ columns, rows, groups = [], onRow, onOpen, onCheck, onSort }: {
  columns: TxRunColumn[]; rows: TxRunRow[]; groups?: TxRunGroup[]; onRow?: (id: string) => void; onOpen?: (id: string) => void; onCheck?: (id: string, checked: boolean) => void; onSort?: (key: string) => void;
}) {
  const [closed, setClosed] = useState<Record<string, boolean>>(() => Object.fromEntries(groups.map(group => [group.id, Boolean(group.closed)])));
  const ref = useRef<HTMLTableElement>(null), [room, setRoom] = useState(Infinity), hides = columns.some(column => column.hideBelow);
  useEffect(() => {
    const box = ref.current?.parentElement;
    if (!hides || !box) return;
    const observer = new ResizeObserver(() => setRoom(box.clientWidth));
    observer.observe(box);
    return () => observer.disconnect();
  }, [hides]);
  const shown = columns.filter(column => !column.hideBelow || room >= column.hideBelow);
  const align = (column: TxRunColumn) => column.align ?? (column.kind === "change" || column.kind === "text" ? "left" : undefined);
  const row = (item: TxRunRow) => <tr key={item.id} data-selected={on(item.selected)} data-compared={on(item.compared)} data-queued={on(item.queued)} onClick={() => onRow?.(item.id)} onDoubleClick={() => onOpen?.(item.id)}>
    <td data-align="left" onClick={event => event.stopPropagation()}><TxCheckbox checked={Boolean(item.checked)} onCheckedChange={value => onCheck?.(item.id, value)} name={`Compare ${item.id}`} /></td>
    {shown.map(column => <td key={column.key} data-align={align(column)} data-kind={column.kind}>{item.cells[column.key]}</td>)}
  </tr>;
  return <table ref={ref} className="tx-runtable">
    <thead><tr><th data-align="left" style={{ width: 28 }} />{shown.map(column => <th key={column.key} data-align={align(column)} data-sortable={on(column.sortable)} data-sorted={on(column.sorted)}
      onClick={column.sortable ? () => onSort?.(column.key) : undefined}>{column.label}{column.sorted ? " ↓" : ""}</th>)}</tr></thead>
    <tbody>
      {groups.map(group => [
        <tr key={`g-${group.id}`} data-group="" data-closed={on(closed[group.id])} onClick={() => setClosed({ ...closed, [group.id]: !closed[group.id] })}>
          <td colSpan={shown.length + 1}><TxIcon name="chevron" /><b>{group.title}</b>{group.summary}</td></tr>,
        ...(closed[group.id] ? [] : rows.filter(item => item.group === group.id).map(row)),
      ])}
      {rows.filter(item => !item.group || !groups.some(group => group.id === item.group)).map(row)}
    </tbody>
  </table>;
}

// ---------------------------------------------------------------- tags, state words, periods
/** Tags mean the same everywhere: fix (failed, future data), watch, muted (stopped, queued), version. */
export function TxTag({ tone, icon, title, children, onClick }: { tone?: "fix" | "watch" | "muted" | "version"; icon?: IconName; title?: string; children: ReactNode; onClick?: () => void }) {
  const content = <>{icon && <TxIcon name={icon} />}{children}</>;
  if (onClick) return <button type="button" className="tx-tag" data-tone={tone} title={title} onClick={onClick}>{content}</button>;
  return <span className="tx-tag" data-tone={tone} title={title}>{content}</span>;
}

/** A state with a dot: fix for what needs handling now, watch for what to keep an eye on, ok, muted for not producing. */
export function TxStateWord({ tone, title, children }: { tone: "fix" | "watch" | "ok" | "muted"; title?: string; children: ReactNode }) {
  return <span className="tx-stateword" data-tone={tone} title={title}><i />{children}</span>;
}

export type TxPeriodState = "ok" | "late" | "miss" | "none" | "pending";

/** One cell a period: on time, late, missing or failed, none, pending. Large cells can be picked. */
export function TxPeriods({ items, large, selected, onSelect, titles }: { items: TxPeriodState[]; large?: boolean; selected?: number; onSelect?: (index: number) => void; titles?: string[] }) {
  return <span className="tx-periods" data-large={on(large)}>{items.map((state, index) =>
    <i key={index} data-state={state === "ok" ? undefined : state} data-selected={on(selected === index)} title={titles?.[index]} onClick={large ? () => onSelect?.(index) : undefined} />)}</span>;
}

// ---------------------------------------------------------------- heatmap
export interface TxHeatCell { value?: number; title?: string; state?: "failed" | "notrun" }

/**
 * Two settings against one measure, shown in place of the main chart for a sweep or a batch. By default one neutral hue from the
 * lowest value to the highest; `scale="updown"` colours by direction (up and down colours, the user's setting) around zero, for
 * measures whose sign is the point (a return, a profit).
 */
export function TxHeatmap({ title, axes, metric, x, y, cells, selected, best, scale = "neutral", format = value => value.toFixed(2), onCell }: {
  title: string; axes?: string; metric?: ReactNode; x: ReactNode[]; y: ReactNode[]; cells: (TxHeatCell | null)[][];
  selected?: [number, number]; best?: [number, number]; scale?: "neutral" | "updown"; format?: (value: number) => string; onCell?: (row: number, column: number) => void;
}) {
  const values = cells.flat().filter((cell): cell is TxHeatCell => Boolean(cell && cell.value !== undefined && !cell.state)).map(cell => cell.value as number);
  const low = values.length ? Math.min(...values) : 0, high = values.length ? Math.max(...values) : 0;
  const span = Math.max(1e-9, ...values.map(Math.abs));
  const fill = scale === "updown"
    ? (value: number) => `color-mix(in srgb, var(${value >= 0 ? "--tx-up" : "--tx-down"}) ${Math.round(8 + Math.min(1, Math.abs(value) / span) * 50)}%, transparent)`
    : (value: number) => `color-mix(in srgb, var(--tx-heat) ${Math.round(12 + (high > low ? (value - low) / (high - low) : 1) * 58)}%, var(--tx-panel))`;
  const same = (pair: [number, number] | undefined, r: number, c: number) => Boolean(pair && pair[0] === r && pair[1] === c);
  return <div className="tx-heatmap" data-scale={scale}>
    <header><h3>{title}</h3>{axes && <span className="tx-heataxes">{axes}</span>}{metric}
      {values.length > 0 && <span className="tx-heatscale"><span>{format(scale === "updown" ? -span : low)}</span><i /><span>{format(scale === "updown" ? span : high)}</span></span>}</header>
    <table><thead><tr><th />{x.map((value, index) => <th key={index} scope="col">{value}</th>)}</tr></thead>
      <tbody>{y.map((label, r) => <tr key={r}><th scope="row">{label}</th>{x.map((_, c) => {
        const cell = cells[r]?.[c];
        return <td key={c} data-state={cell?.state} data-selected={on(same(selected, r, c))} data-best={on(same(best, r, c))} title={cell?.title}
          style={cell && cell.value !== undefined && !cell.state ? { background: fill(cell.value) } : undefined} onClick={() => onCell?.(r, c)}>
          {cell?.state === "failed" ? t("failed") : cell?.state === "notrun" || !cell || cell.value === undefined ? "—" : format(cell.value)}</td>;
      })}</tr>)}</tbody></table>
  </div>;
}
// ---------------------------------------------------------------- failures, region heads
/** A failure in place of the result. Code errors link to their line and keep finished results; data problems are amber with the way forward. */
export function TxFailure({ title, message, code, location, context, tone = "fix", actions, trace }: {
  title: string; message?: string; code?: boolean; location?: { label: string; onOpen: () => void }; context?: string; tone?: "fix" | "watch";
  actions?: ReactNode; trace?: { text: string; mine?: boolean }[];
}) {
  const [showTrace, setShowTrace] = useState(false);
  // The title and the message share a line and the buttons sit at its right end; the line the failure is at and where it stopped come under them.
  return <div className="tx-failure" data-tone={tone === "watch" ? "watch" : undefined}>
    <div><TxIcon name="alert" /><div style={{ flex: 1, minWidth: 0 }}><h3 style={{ display: "inline" }}>{title}</h3>{message && <span className="tx-failmessage" data-code={on(code)} style={{ marginLeft: 10, marginTop: 0 }}>{message}</span>}
      {(location || context) && <div className="tx-failcontext">{location && <button type="button" onClick={location.onOpen} title={t("Open in the code")}>{location.label}</button>}{context}</div>}</div>
      {(actions || trace) && <div className="tx-failactions" style={{ padding: 0, flex: "none" }}>{actions}{trace && <TxButton variant="quiet" size="sm" onClick={() => setShowTrace(!showTrace)}>{t("Traceback")}</TxButton>}</div>}</div>
    {trace && showTrace && <pre>{trace.map((line, index) => <span key={index}>{line.mine ? <b>{line.text}</b> : line.text}{"\n"}</span>)}</pre>}
  </div>;
}

/** The title row of a region in the result area (a chart, a table, a log), with its own controls. */
export function TxRegionHead({ title, sub, children }: { title: string; sub?: string; children?: ReactNode }) {
  return <div className="tx-regionhead"><h3>{title}</h3>{sub && <span className="tx-regionsub" title={sub}>{sub}</span>}<span className="tx-spacer" /><span className="tx-regionkeys" data-tx-keys="" />{children}</div>;
}

