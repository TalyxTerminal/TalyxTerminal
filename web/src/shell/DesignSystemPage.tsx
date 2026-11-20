/** The sample page: every shared component in every state, rendered from the real SDK, then the same parts
    assembled into module-like screens with made-up data. It is the visual standard. Hover, pressed and focus
    are pinned with `data-force`, so all states can be compared side by side. Each section has a stable id
    for anchors and section screenshots. The assembled screens are specimens only; they are not in the SDK. */
import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  TxAlert, TxButton, TxCheckbox, TxColorInput, TxCompare, TxDetails, TxDialog, TxDisclosure, TxEmpty, TxField,
  TxFieldLayout, TxIcon, TxIconButton, TxInput, TxKeyNumbers, TxMenu, TxObjectTitle, TxPanel,
  TxPopover, TxProgress, TxRoot, TxRow, TxRunButton, TxRunPicker, TxRunStrip, TxSearch, TxSegmented, TxSelect, TxSheet,
  TxSkeleton, TxSpacer, TxStateWord, TxStatus, TxSwitch, TxTable, TxTabs, TxTag, TxTd, TxTextarea, TxTh, TxToastProvider,
  TxToolbar, TxToolbarDivider, TxTooltip, TxTooltipProvider, TxChecks, TxSettingsGroup, txDelta, txIconNames, tokenValue, useTxToast,
  type IconName, type TxDensity, type TxMenuItem, type TxOverlaySize,
} from "@talyx/sdk";
import { setUpColor, useUpColor } from "../design/upColor";
import "./designSystem.css";

const none = () => undefined;

const sections: [string, string][] = [
  ["tokens", "Values"], ["buttons", "Buttons"], ["inputs", "Inputs"], ["selection", "Selection"], ["menus", "Menus and tips"],
  ["overlays", "Dialogs and sheets"], ["tables", "Tables"], ["feedback", "Status and feedback"], ["icons", "Icons"],
  ["density", "Density"], ["variants", "Tool variants"], ["in-modules", "In modules"],
];

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return <section className="ds-section" id={id} data-section={id}><h2>{title}</h2>{children}</section>;
}

function Block({ title, children, className }: { title?: string; children: ReactNode; className?: string }) {
  return <div className={`ds-block${className ? ` ${className}` : ""}`}>{title && <h3>{title}</h3>}{children}</div>;
}

/** Rows of variants against columns of states. */
function Matrix({ columns, rows, first = "minmax(96px, 120px)" }: { columns: string[]; rows: [string, ReactNode[]][]; first?: string }) {
  return <div className="ds-matrix" style={{ gridTemplateColumns: `${first} repeat(${columns.length}, minmax(0, 1fr))` }}>
    <span />{columns.map(column => <span key={column} className="ds-mhead">{column}</span>)}
    {rows.map(([label, cells]) => <Fragment key={label}><span className="ds-mlabel">{label}</span>
      {cells.map((cell, index) => <div key={index} className="ds-mcell">{cell}</div>)}</Fragment>)}
  </div>;
}

/** Fixed-seed ranges (each step's open, close, high and low), used as plot backdrops. */
function useRanges(count: number, seed: number, drift = .47) {
  return useMemo(() => {
    let s = seed, price = 64000;
    const random = () => { s = s * 16807 % 2147483647; return (s - 1) / 2147483646; };
    return Array.from({ length: count }, () => {
      const open = price, close = open + (random() - drift) * 420;
      const high = Math.max(open, close) + random() * 210, low = Math.min(open, close) - random() * 210;
      price = close; return { open, close, high, low, volume: 40 + random() * 160 };
    });
  }, [count, seed, drift]);
}

function RangeBars({ width, height, count, seed, averages = false }: { width: number; height: number; count: number; seed: number; averages?: boolean }) {
  const data = useRanges(count, seed);
  const max = Math.max(...data.map(d => d.high)), min = Math.min(...data.map(d => d.low));
  const y = (value: number) => 16 + (max - value) / (max - min) * (height - 32);
  const step = width / count, bar = step * .62;
  const average = (span: number) => data.map((_, i) => {
    const part = data.slice(Math.max(0, i - span + 1), i + 1);
    return `${i * step + step / 2},${y(part.reduce((sum, d) => sum + d.close, 0) / part.length)}`;
  }).join(" ");
  return <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-label="Range bars">
    {[1, 2, 3, 4, 5].map(g => <line key={g} x1={0} x2={width} y1={height * g / 6} y2={height * g / 6} className="ds-grid-line" />)}
    {data.map((d, i) => {
      const x = i * step + step / 2, direction = d.close >= d.open ? "up" : "down";
      return <Fragment key={i}>
        <line x1={x} x2={x} y1={y(d.high)} y2={y(d.low)} className={`ds-wick-${direction}`} />
        <rect x={x - bar / 2} y={y(Math.max(d.open, d.close))} width={bar} height={Math.max(1, Math.abs(y(d.open) - y(d.close)))} className={`ds-bar-${direction}`} />
      </Fragment>;
    })}
    {averages && <><polyline points={average(20)} className="ds-ma ds-ma-1" /><polyline points={average(50)} className="ds-ma ds-ma-2" /></>}
  </svg>;
}

/* ── Values ─────────────────────────────────────────────── */

function Tokens() {
  const upColour = useUpColor();
  const groups: [string, [string, string][]][] = [
    ["Surfaces", [["bg", "Background"], ["panel", "Panel"], ["card", "Card"], ["raised", "Raised"]]],
    ["Text", [["text", "Text"], ["text-2", "Text 2"], ["text-3", "Text 3"], ["text-4", "Text 4"]]],
    ["Attention", [["ember", "Ember"], ["ember-text", "Ember text"], ["watch", "Watch"]]],
    ["Direction", [["up", "Up"], ["down", "Down"]]],
    ["Data series", [1, 2, 3, 4, 5].map(n => [`series-${n}`, `Series ${n}`] as [string, string])],
    ["Chart canvas", [["chart-grid", "Grid"], ["chart-crosshair", "Crosshair"], ["chart-reference", "Reference"], ["chart-legend", "Legend fill"]]],
  ];
  return <div className="ds-tokens">
    <div className="ds-swatchgroups">{groups.map(([title, items]) => <Block key={title} title={title}><div className="ds-swatches">
      {items.map(([key, label]) => <div className="ds-swatch" key={key}>
        <div className="ds-swatch-fill" style={{ background: `var(--tx-${key})` }} />
        <div className="ds-swatch-name">{label}</div><div className="ds-swatch-value">{tokenValue(`--tx-${key}`)}</div>
      </div>)}</div></Block>)}
    </div>
    <div className="ds-tokenrow">
      <Block title="Type" className="ds-type">
        <div className="ds-type-row"><span className="ds-type-size">19</span><span style={{ fontSize: "var(--tx-size-title)", fontWeight: 600 }}>Short-term reversal</span></div>
        <div className="ds-type-row"><span className="ds-type-size">15</span><span style={{ fontSize: "var(--tx-size-lg)", fontWeight: 600 }}>Publish to library</span></div>
        <div className="ds-type-row"><span className="ds-type-size">13</span><span>Holding five days beats one in every group but the last.</span></div>
        <div className="ds-type-row"><span className="ds-type-size">12</span><span style={{ fontSize: "var(--tx-size-sm)", color: "var(--tx-text-2)" }}>Leave out names listed under 60 days</span></div>
        <div className="ds-type-row"><span className="ds-type-size">11</span><span style={{ fontSize: "var(--tx-size-xs)", color: "var(--tx-text-3)" }}>Rank IC, 5D</span></div>
        <div className="ds-type-row"><span className="ds-type-size">18</span><span className="tx-num" style={{ fontSize: "var(--tx-size-num)" }}>64,182.40 <span className="tx-up">+2.31%</span> <span className="tx-down">−0.84%</span></span></div>
      </Block>
      <Block title="Shape and space" className="ds-shape">
        <div className="ds-radii">{([["xs", "4"], ["sm", "6"], ["", "10"], ["lg", "14"], ["pill", "pill"]] as const).map(([key, label]) =>
          <div key={label}><i style={{ borderRadius: `var(--tx-radius${key ? `-${key}` : ""})` }} /><span>{label}</span></div>)}</div>
        <div className="ds-spaces">{[4, 8, 12, 16, 24, 32].map(size => <div key={size}><i style={{ width: size }} /><span className="tx-num">{size}</span></div>)}</div>
      </Block>
      <Block title="Up color" className="ds-up-color">
        <TxSegmented label="Up color" value={upColour} onChange={setUpColor} options={[{ value: "green-up", label: "Green up" }, { value: "red-up", label: "Red up" }]} />
        <div className="tx-num ds-up-sample"><span className="tx-up">+1,204.50</span><span className="tx-down">−318.20</span></div>
      </Block>
    </div>
  </div>;
}

/* ── Buttons ────────────────────────────────────────────── */

function Buttons() {
  const states = (make: (extra: Record<string, unknown>) => ReactNode) => [
    make({}), make({ "data-force": "hover" }), make({ "data-force": "active" }), make({ "data-force": "focus" }), make({ disabled: true }), make({ loading: true }),
  ];
  const columns = ["Normal", "Hover", "Pressed", "Focus", "Disabled", "Loading"];
  return <div className="tx-col ds-stack">
    <Matrix columns={columns} rows={[
      ["Primary", states(extra => <TxButton variant="primary" {...extra}><TxIcon name="play" />Run once</TxButton>)],
      ["Default", states(extra => <TxButton {...extra}>Duplicate</TxButton>)],
      ["Quiet", states(extra => <TxButton variant="quiet" {...extra}>Cancel</TxButton>)],
      ["Tool", states(extra => <TxButton variant="tool" {...extra}><TxIcon name="function" />Overlays</TxButton>)],
      ["Tool primary", states(extra => <TxButton variant="tool-primary" {...extra}><TxIcon name="plus" />Get data</TxButton>)],
      ["Small", states(extra => <TxButton size="sm" {...extra}><TxIcon name="refresh" />Refresh</TxButton>)],
      ["Small primary", states(extra => <TxButton size="sm" variant="primary" {...extra}>Publish v2</TxButton>)],
      ["Row", states(extra => <TxButton variant="row" {...extra}><TxIcon name="file" />research/carry.py</TxButton>)],
    ]} />
    <Matrix columns={["Normal", "Hover", "Pressed", "On", "Disabled", "Small"]} rows={[
      ["Icon", [<TxIconButton key="a" label="More" icon="more" />, <TxIconButton key="b" label="More" icon="more" data-force="hover" />, <TxIconButton key="c" label="More" icon="more" data-force="active" />, <TxIconButton key="d" label="Pin" icon="bookmark" aria-pressed />, <TxIconButton key="e" label="More" icon="more" disabled />, <TxIconButton key="f" label="Close" icon="close" size="sm" />]],
      ["Icon outline", [<TxIconButton key="a" variant="outline" label="Refresh" icon="refresh" />, <TxIconButton key="b" variant="outline" label="Refresh" icon="refresh" data-force="hover" />, <TxIconButton key="c" variant="outline" label="Refresh" icon="refresh" data-force="active" />, <TxIconButton key="d" variant="outline" label="Star" icon="star" aria-pressed />, <TxIconButton key="e" variant="outline" label="Refresh" icon="refresh" disabled />, <TxIconButton key="f" variant="outline" size="sm" label="Download" icon="download" />]],
      ["Icon tool", [<TxIconButton key="a" variant="tool" label="Trend line" icon="trend" />, <TxIconButton key="b" variant="tool" label="Trend line" icon="trend" data-force="hover" />, <TxIconButton key="c" variant="tool" label="Trend line" icon="trend" data-force="active" />, <TxIconButton key="d" variant="tool" label="Crosshair" icon="crosshair" aria-pressed />, <TxIconButton key="e" variant="tool" label="Magnet" icon="magnet" disabled />, <TxIconButton key="f" variant="tool" size="sm" label="Ruler" icon="ruler" />]],
    ]} />
    <Block title="List rows" className="ds-rows-demo">
      <div className="tx-col ds-listrows">
        <TxButton variant="row" aria-current="true"><span className="ds-row-title">Funding carry review</span><span className="ds-row-meta">2 min ago</span></TxButton>
        <TxButton variant="row"><span className="ds-row-title">Why did the 5-day group lag in March?</span><span className="ds-row-meta">Yesterday</span></TxButton>
        <TxButton variant="row"><TxIcon name="check" /><span className="ds-row-title">Read research/carry.py</span></TxButton>
      </div>
    </Block>
    <Block title="Long labels and groups">
      <div className="tx-row ds-wrap-demo">
        <TxButton variant="primary"><TxIcon name="lock" />Validate on held-out 2025 – 2026</TxButton>
        <TxButton>Import a saved list as a research list</TxButton>
        <TxButton variant="quiet"><TxIcon name="external" />Open in VS Code</TxButton>
        <TxButton size="sm"><TxIcon name="download" />Export 12,480 rows</TxButton>
      </div>
      <div className="tx-row ds-wrap-demo">
        <TxButton variant="quiet" size="sm">Cancel</TxButton><TxButton variant="primary" size="sm">Delete run</TxButton>
        <span className="ds-gap" />
        <TxButton size="sm">Notebook</TxButton><TxButton size="sm"><TxIcon name="bookmark" />Publish</TxButton><TxIconButton size="sm" label="More" icon="more" />
      </div>
    </Block>
  </div>;
}

/* ── Inputs ─────────────────────────────────────────────── */

function Inputs() {
  const [colour, setColour] = useState("#4F98EF");
  const [search, setSearch] = useState("BTC");
  const columns = ["Normal", "Hover", "Focus", "Error", "Disabled", "Read-only"];
  const row = (make: (extra: Record<string, unknown>) => ReactNode) => [
    make({}), make({ "data-force": "hover" }), make({ "data-force": "focus" }), make({ "aria-invalid": true }), make({ disabled: true }), make({ readOnly: true }),
  ];
  return <div className="tx-col ds-stack">
    <Matrix columns={columns} rows={[
      ["Text", row(extra => <TxInput aria-label="Run name" defaultValue="Mean reversion" {...extra} />)],
      ["Empty", row(extra => <TxInput aria-label="Name" placeholder="Name" {...extra} />)],
      ["Number, unit", row(extra => <TxInput aria-label="Holding" numeric suffix="days" defaultValue="5" {...extra} />)],
      ["Large number", row(extra => <TxInput aria-label="Capital" numeric suffix="USDT" defaultValue="12,480,000.00" {...extra} />)],
      ["Select", row(extra => <TxSelect aria-label="Source" defaultValue="backup" {...extra}><option value="primary">Primary feed</option><option value="backup">Backup feed, fifteen minutes late</option></TxSelect>)],
    ]} />
    <div className="ds-fieldgrid">
      <Block title="Stacked fields">
        <div className="ds-fields">
          <TxField label="Run name"><TxInput defaultValue="Funding carry, two groups across three sources, rebalanced weekly" /></TxField>
          <TxField label="Starting capital" hint="Taken from the settings at run time"><TxInput numeric suffix="USDT" defaultValue="10,000" /></TxField>
          <TxField label="Lookback" error="Must be at least 1"><TxInput numeric suffix="bars" defaultValue="0" /></TxField>
          <TxField label="Source"><TxSelect defaultValue="primary"><option value="primary">Primary feed</option><option value="backup">Backup feed</option></TxSelect></TxField>
          <TxField label="Notes"><TxTextarea rows={3} defaultValue={"Rebalance at the close.\nSkip names halted in the last five days."} /></TxField>
        </div>
      </Block>
      <Block title="Property rows">
        <div className="ds-inline-fields"><TxFieldLayout layout="inline">
          <TxField label="Holding"><TxInput numeric suffix="days" defaultValue="5" /></TxField>
          <TxField label="Groups"><TxSelect defaultValue="5"><option value="5">5, equal counts</option><option value="10">10, equal counts</option></TxSelect></TxField>
          <TxField label="Cost per change"><TxInput numeric suffix="bps" defaultValue="5" /></TxField>
          <TxField label="Borrow"><TxInput numeric suffix="% a year" defaultValue="0.3" /></TxField>
          <TxField label="Leave out names listed under 60 days, and those halted"><TxSwitch hideLabel label="Leave out" checked onCheckedChange={none} /></TxField>
          <TxField label="Line colour"><TxColorInput aria-label="Line colour" value={colour} onValueChange={setColour} /></TxField>
        </TxFieldLayout></div>
        <div className="ds-search-row"><TxSearch value={search} onValueChange={setSearch} placeholder="Code or name" aria-label="Search series" /></div>
      </Block>
    </div>
  </div>;
}

/* ── Selection ──────────────────────────────────────────── */

function Selection() {
  const [timeframe, setTimeframe] = useState("1h"), [view, setView] = useState("work"), [tab, setTab] = useState("queued"), [tab2, setTab2] = useState("summary");
  const [archived, setArchived] = useState(true), [live, setLive] = useState(true);
  const [files, setFiles] = useState([
    { value: "carry.py", label: "carry.py", title: "research/carry.py" },
    { value: "signals.py", label: "signals.py", title: "research/signals.py", dirty: true },
    { value: "README.md", label: "README.md", title: "README.md" },
    { value: "universe.toml", label: "universe.toml", title: "config/universe.toml", dirty: true },
  ]), [file, setFile] = useState("signals.py");
  return <div className="ds-selection">
    <Block title="Checkbox">
      <Matrix first="minmax(80px, 96px)" columns={["Normal", "Hover", "Disabled"]} rows={[
        ["Off", [<TxCheckbox key="a" label="Failed only" checked={false} onCheckedChange={none} />, <label key="b" className="tx-check"><button type="button" className="tx-check-box" data-state="unchecked" data-force="hover" aria-label="Failed only" /><span>Failed only</span></label>, <TxCheckbox key="c" label="Failed only" checked={false} disabled onCheckedChange={none} />]],
        ["On", [<TxCheckbox key="a" label="Include archived" checked={archived} onCheckedChange={setArchived} />, <TxCheckbox key="b" label="Include the last group" checked onCheckedChange={none} />, <TxCheckbox key="c" label="Locked" checked disabled onCheckedChange={none} />]],
        ["Mixed", [<TxCheckbox key="a" label="All sources" checked="indeterminate" onCheckedChange={none} />, <TxCheckbox key="b" label="Some groups" checked="indeterminate" onCheckedChange={none} />, <TxCheckbox key="c" label="Mixed" checked="indeterminate" disabled onCheckedChange={none} />]],
      ]} />
      <div className="ds-long"><TxCheckbox label="Leave out names listed under 60 days and any name halted in the last five sessions" checked onCheckedChange={none} /></div>
    </Block>
    <Block title="Switch">
      <div className="tx-col">
        <TxSwitch label="Live data" checked={live} onCheckedChange={setLive} />
        <TxSwitch label="Alert sounds" checked={false} onCheckedChange={none} />
        <TxSwitch label="New values each period" checked disabled onCheckedChange={none} />
        <TxSwitch label="Simulated only" checked={false} disabled onCheckedChange={none} />
      </div>
    </Block>
    <Block title="Segmented">
      <div className="tx-col">
        <TxSegmented label="View" value={view} onChange={setView} options={[{ value: "work", label: "Work" }, { value: "results", label: "Results" }]} />
        <TxSegmented label="Timeframe" value={timeframe} onChange={setTimeframe} options={["1m", "5m", "15m", "1h", "4h", "1D"].map(value => ({ value, label: value }))} />
        <TxSegmented label="Small" size="sm" value="t" onChange={none} options={[{ value: "t", label: "t-stat" }, { value: "net", label: "L–S net" }, { value: "x", label: "Disabled", disabled: true }]} />
        <TxSegmented label="Tool" variant="tool" value={timeframe} onChange={setTimeframe} options={["1m", "5m", "15m", "1h", "4h", "1D", "1W"].map(value => ({ value, label: value }))} />
      </div>
    </Block>
    <Block title="Tabs" className="ds-tabs-block">
      <TxTabs value={tab} onChange={setTab} options={[{ value: "items", label: "Items", count: 6 }, { value: "queued", label: "Queued", count: 38 }, { value: "done", label: "Done", count: 1204, marked: "New results" }, { value: "history", label: "History" }, { value: "closed", label: "Closed", disabled: true }]} />
      <TxTabs value={tab2} onChange={setTab2} options={[{ value: "summary", label: "Summary" }, { value: "weights", label: "Weights and changes" }, { value: "risk", label: "Risk" }, { value: "returns", label: "Returns" }, { value: "style", label: "Style and attribution" }, { value: "robust", label: "Robustness" }, { value: "settings", label: "Settings" }]} />
      <TxTabs closable label="Open files" value={file} onChange={setFile} options={files}
        onClose={value => { const rest = files.filter(item => item.value !== value); setFiles(rest); if (value === file) setFile(rest[0]?.value ?? ""); }} />
    </Block>
  </div>;
}

/* ── Menus ──────────────────────────────────────────────── */

const runActions: TxMenuItem[] = [
  { id: "new", label: "New run", icon: "plus", shortcut: "Ctrl N", onSelect: none },
  { id: "duplicate", label: "Duplicate", icon: "copy", shortcut: "Ctrl D", onSelect: none },
  { id: "notebook", label: "Open in notebook", icon: "book", onSelect: none },
  { id: "export", label: "Export results", icon: "download", hint: "4.2 MB", onSelect: none },
  { id: "compare", label: "Compare with…", icon: "columns", disabled: true, onSelect: none },
  { id: "archive", label: "Archive", icon: "archive", separated: true, onSelect: none },
  { id: "delete", label: "Delete run", icon: "trash", tone: "danger", onSelect: none },
];

const overlayItems: TxMenuItem[] = [
  { id: "ma", group: "Trend", label: "Moving average 20", checked: true, onSelect: none },
  { id: "ema", label: "Exponential average 50", checked: true, onSelect: none },
  { id: "bb", label: "Bollinger bands", onSelect: none },
  { id: "vwap", label: "VWAP, session", onSelect: none },
  { id: "rsi", group: "Momentum", label: "RSI 14", checked: true, onSelect: none },
  { id: "macd", label: "MACD 12 26 9", onSelect: none },
  { id: "vol", group: "Volume", label: "Volume", checked: true, onSelect: none },
  { id: "script", label: "New overlay script…", icon: "code", separated: true, onSelect: none },
];

const kindItems: TxMenuItem[] = [
  { id: "bt", label: "Run once", icon: "play", shortcut: "Ctrl ↵", checked: true, onSelect: none },
  { id: "sweep", label: "Sweep 12", icon: "grid", detail: "holding × groups, 12 runs", onSelect: none },
  { id: "wf", label: "Walk-forward 6", icon: "history", detail: "six windows, two years each", onSelect: none },
  { id: "held", label: "Validate on held-out", icon: "lock", disabled: true, detail: "Keep a run first: held-out is used once", onSelect: none },
  { id: "late", label: "Data to Oct 30 is three days late", tone: "warn", icon: "alert", separated: true, onSelect: none },
];

function HighlightedMenu({ items, row }: { items: TxMenuItem[]; row: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { ref.current?.querySelectorAll(".tx-menu-item")[row]?.setAttribute("data-highlighted", ""); }, [row]);
  return <div ref={ref} className="ds-menu-holder"><TxMenu preview label="Menu specimen" trigger={null} items={items} /></div>;
}

function Menus() {
  const [popover, setPopover] = useState(false);
  return <div className="tx-col ds-stack">
    <div className="ds-menus">
      <Block title="Actions"><HighlightedMenu items={runActions} row={1} /></Block>
      <Block title="Choices"><HighlightedMenu items={overlayItems} row={4} /></Block>
      <Block title="With details"><HighlightedMenu items={kindItems} row={1} /></Block>
      <Block title="Tips">
        <div className="tx-col ds-tips">
          <span className="tx-tip">Copy run ID</span>
          <span className="tx-tip">Rank IC: rank correlation between today's values and the next five days' returns</span>
          <div className="tx-row">
            <TxTooltip label="Hover shows this tip"><TxIconButton variant="outline" label="Hover me" icon="info" /></TxTooltip>
            <TxMenu label="Run actions" items={runActions} trigger={<TxButton>Open menu<TxIcon name="chevron-down" /></TxButton>} />
            <TxPopover open={popover} onOpenChange={setPopover} label="Data limits" trigger={<TxButton variant="quiet">Popover</TxButton>}>
              <div className="ds-popover-body"><b>Data limits</b><span>History starts Jan 2, 2015 for BTC; earlier dates fall back to the common start.</span></div>
            </TxPopover>
          </div>
        </div>
      </Block>
    </div>
  </div>;
}

/* ── Dialogs and sheets ─────────────────────────────────── */

function Overlays() {
  const [dialog, setDialog] = useState(false), [size, setSize] = useState<TxOverlaySize>("compact"), [sheet, setSheet] = useState(false);
  return <div className="tx-col ds-stack">
    <div className="ds-stage ds-overlay-stage">
      <div className="ds-stage-backdrop"><RangeBars width={1200} height={420} count={110} seed={21} /></div>
      <div className="ds-dialog-pin">
        <TxDialog preview open onOpenChange={none} title="Use the held-out period?" footer={<><TxButton size="sm" variant="quiet">Cancel</TxButton><TxButton size="sm" variant="primary"><TxIcon name="lock" />Validate on held-out</TxButton></>}>
          The held-out period is used once. After this run, results from 2025 on stop being an honest test for this factor.
          <dl className="ds-kv"><dt>Run</dt><dd className="tx-num">#22</dd><dt>Held out</dt><dd>Jan 2025 – Oct 2026</dd><dt>Checks</dt><dd>in-sample only</dd></dl>
        </TxDialog>
      </div>
      <TxSheet preview open onOpenChange={none} title="Publish to library" label="publish" footer={<><TxButton size="sm" variant="quiet">Cancel</TxButton><span className="tx-spacer" /><TxButton size="sm" variant="primary"><TxIcon name="bookmark" />Publish v2</TxButton></>}>
        <div className="ds-sheet-rows">
          <div><span>Name</span><b>Multi-asset risk parity</b></div>
          <div><span>Version</span><b><span className="tx-num">v2</span> from <span className="tx-num">#8</span></b></div>
          <div><span>Publishes</span><b>Target weights each month</b></div>
          <div><span>New values</span><TxSwitch hideLabel label="New values each period" checked onCheckedChange={none} /></div>
        </div>
        <h4 className="ds-sheet-h">Checks to review</h4>
        <div className="tx-col ds-sheet-checks"><TxStateWord tone="watch">Solver fell back 2 of 108 periods</TxStateWord><TxStateWord tone="watch">History cut short: BTC starts Jan 2015</TxStateWord></div>
        <h4 className="ds-sheet-h">Against v1</h4>
        <TxTable>
          <thead><tr><TxTh>Measure</TxTh><TxTh numeric>v1</TxTh><TxTh numeric>This</TxTh><TxTh numeric>Change</TxTh></tr></thead>
          <tbody>{[["CAGR", "+5.37%", "+5.81%", "+0.44", "up"], ["Volatility", "7.73%", "7.83%", "+0.10", ""], ["Sharpe", "0.72", "0.76", "+0.04", "up"], ["Max drawdown", "−23.3%", "−22.8%", "+0.5", "up"]].map(([name, a, b, change, tone]) =>
            <tr key={name}><TxTd>{name}</TxTd><TxTd numeric className="tx-secondary">{a}</TxTd><TxTd numeric>{b}</TxTd><TxTd numeric className={tone ? `tx-${tone}` : "tx-secondary"}>{change}</TxTd></tr>)}</tbody>
        </TxTable>
      </TxSheet>
    </div>
    <div className="tx-row">
      {(["compact", "standard", "wide"] as const).map(value => <TxButton key={value} size="sm" onClick={() => { setSize(value); setDialog(true); }}>Open {value} dialog</TxButton>)}
      <TxButton size="sm" onClick={() => setSheet(true)}>Open sheet</TxButton>
    </div>
    <TxDialog open={dialog} size={size} onOpenChange={setDialog} title="Delete this run?" footer={<><TxButton size="sm" variant="quiet" onClick={() => setDialog(false)}>Cancel</TxButton><TxButton size="sm" variant="primary" onClick={() => setDialog(false)}>Delete run</TxButton></>}>
      Results and logs for Funding carry #14 are removed. The published v2 keeps its own copy.
    </TxDialog>
    <TxSheet open={sheet} onOpenChange={setSheet} title="Run settings" label="run settings">
      <TxFieldLayout layout="inline"><div className="tx-col">
        <TxField label="Holding"><TxInput numeric suffix="days" defaultValue="5" /></TxField>
        <TxField label="Groups"><TxInput numeric defaultValue="5" /></TxField>
        <TxField label="Cost per change"><TxInput numeric suffix="bps" defaultValue="5" /></TxField>
      </div></TxFieldLayout>
    </TxSheet>
  </div>;
}

/* ── Tables ─────────────────────────────────────────────── */

const pairs = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT", "AVAX/USDT", "LINK/USDT", "ADA/USDT", "TON/USDT", "DOT/USDT", "MATIC/USDT", "LTC/USDT", "ARB/USDT", "OP/USDT", "APT/USDT", "INJ/USDT", "NEAR/USDT", "ATOM/USDT", "FIL/USDT", "UNI/USDT"];

function seeded(seed: number) {
  let s = seed;
  return () => { s = s * 16807 % 2147483647; return (s - 1) / 2147483646; };
}

const money = (value: number, digits = 2) => value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
const signed = (value: number, digits = 2, unit = "") => `${value > 0 ? "+" : value < 0 ? "−" : ""}${money(Math.abs(value), digits)}${unit}`;

function Tables() {
  const [selected, setSelected] = useState("ETH/USDT");
  const rows = useMemo(() => {
    const random = seeded(7);
    return pairs.slice(0, 7).map((name, index) => ({ name, source: index % 3 ? "Primary feed" : "Backup feed", value: index === 0 ? 64182.4 : 0.5 + random() * 3400, change: (random() - .5) * 9, count: random() * 2e9 }));
  }, []);
  const many = useMemo(() => {
    const random = seeded(19);
    return Array.from({ length: 40 }, (_, index) => ({ time: `14:${String(59 - index).padStart(2, "0")}:${String(Math.floor(random() * 60)).padStart(2, "0")}`, name: pairs[index % pairs.length], direction: random() > .5 ? "Up" : "Down", value: 10 + random() * 3000, amount: random() * 40 }));
  }, []);
  return <div className="ds-tables">
    <Block title="Hover, selected, sorted, long and large values" className="ds-table-main">
      <div className="ds-tablebox">
        <TxTable>
          <thead><tr><TxTh>Name</TxTh><TxTh>Source</TxTh><TxTh numeric sort="descending">Value</TxTh><TxTh numeric sort="none">24h</TxTh><TxTh numeric sort="none">Count</TxTh></tr></thead>
          <tbody>{rows.map((row, index) => <tr key={row.name} data-selected={selected === row.name} data-force={index === 2 ? "hover" : undefined} onClick={() => setSelected(row.name)}>
            <TxTd><span className="tx-cell-name">{row.name}</span></TxTd><TxTd className="tx-muted">{index === 4 ? "Backup feed, fifteen minutes late, gaps filled forward" : row.source}</TxTd>
            <TxTd numeric>{money(row.value, row.value > 100 ? 2 : 4)}</TxTd>
            <TxTd numeric className={row.change >= 0 ? "tx-up" : "tx-down"}>{signed(row.change, 2, "%")}</TxTd>
            <TxTd numeric className="tx-secondary">{index === 0 ? "12,480,316,904" : money(row.count, 0)}</TxTd>
          </tr>)}</tbody>
        </TxTable>
      </div>
    </Block>
    <Block title="Many rows" className="ds-table-many">
      <div className="ds-tablebox ds-scrollbox">
        <TxTable stickyHead density="compact">
          <thead><tr><TxTh>Time</TxTh><TxTh>Name</TxTh><TxTh>Direction</TxTh><TxTh numeric>Value</TxTh><TxTh numeric>Amount</TxTh></tr></thead>
          <tbody>{many.map((row, index) => <tr key={index}>
            <TxTd numeric className="tx-muted" style={{ textAlign: "left" }}>{row.time}</TxTd><TxTd>{row.name}</TxTd>
            <TxTd className={row.direction === "Up" ? "tx-up" : "tx-down"}>{row.direction}</TxTd>
            <TxTd numeric>{money(row.value)}</TxTd><TxTd numeric>{money(row.amount, 3)}</TxTd>
          </tr>)}</tbody>
        </TxTable>
      </div>
    </Block>
    <Block title="Empty and loading" className="ds-table-empty">
      <div className="ds-tablebox">
        <TxTable><thead><tr><TxTh>Item</TxTh><TxTh numeric>Value</TxTh><TxTh numeric>Amount</TxTh></tr></thead></TxTable>
        <TxEmpty title="Nothing queued" text="Items queued from any panel show up here." />
      </div>
      <div className="ds-tablebox">
        <TxTable><thead><tr><TxTh>Item</TxTh><TxTh numeric>Value</TxTh><TxTh numeric>Amount</TxTh></tr></thead>
          <tbody>{[62, 48, 70].map((width, index) => <tr key={index}><TxTd><TxSkeleton width={`${width}%`} /></TxTd><TxTd><TxSkeleton width="70%" /></TxTd><TxTd><TxSkeleton width="50%" /></TxTd></tr>)}</tbody>
        </TxTable>
      </div>
    </Block>
  </div>;
}

/* ── Status and feedback ────────────────────────────────── */

function Feedback() {
  const toast = useTxToast();
  return <div className="ds-feedback">
    <Block title="Status words">
      <div className="tx-col"><TxStatus tone="off" label="Not producing" /><TxStatus tone="busy" /><TxStatus tone="done" /><TxStatus tone="review" /><TxStatus tone="failed" /></div>
    </Block>
    <Block title="Tags and states">
      <div className="tx-col">
        <div className="tx-row"><TxTag tone="version">v2</TxTag><TxTag icon="lock">Held-out</TxTag><TxTag>Walk-forward</TxTag><TxTag>Sweep</TxTag></div>
        <div className="tx-row"><TxTag tone="fix">Failed</TxTag><TxTag tone="fix">Future data</TxTag><TxTag tone="watch">Late</TxTag><TxTag tone="muted">Queued</TxTag><TxTag tone="muted">Stopped</TxTag></div>
        <div className="tx-row ds-states"><TxStateWord tone="fix">Missing</TxStateWord><TxStateWord tone="watch">Weakening</TxStateWord><TxStateWord tone="ok">On time</TxStateWord><TxStateWord tone="muted">Paused</TxStateWord></div>
        <div className="tx-row"><span className="tx-kbd">Ctrl</span><span className="tx-kbd">↵</span><span className="tx-muted">runs</span></div>
      </div>
    </Block>
    <Block title="Progress and loading">
      <div className="tx-col">
        <TxProgress value={64} label="Loading bars" />
        <TxProgress value={null} label="Fetching index members" />
        <div className="tx-col ds-skeletons"><TxSkeleton width="62%" /><TxSkeleton width="88%" /><TxSkeleton width="46%" /></div>
      </div>
    </Block>
    <Block title="Empty">
      <div className="ds-empty"><TxEmpty title="No runs yet" text="Run the factor and its results show up here." action={<TxButton size="sm" variant="primary"><TxIcon name="play" />Run once</TxButton>} /></div>
    </Block>
    <Block title="In place" className="ds-span2">
      <div className="tx-col">
        <TxAlert title="The test engine didn't start" actions={<><TxButton size="sm">Install engine</TxButton><TxDetails>ModuleNotFoundError: No module named 'nautilus_trader'</TxDetails></>}>
          The engine isn't installed, so the run stopped before the first bar. Finished runs are kept.
        </TxAlert>
        <TxAlert tone="watch" title="Data to Oct 30 is three days late" actions={<TxButton size="sm" variant="quiet">Fetch now</TxButton>}>
          Results stop at the last close that arrived. Fetch to bring them up to date.
        </TxAlert>
        <TxAlert tone="info" title="Checks use the in-sample period only">Held-out results show in their own run.</TxAlert>
      </div>
    </Block>
    <Block title="Notices and toasts" className="ds-span2">
      <div className="ds-toasts">
        <TxButton size="sm" onClick={() => toast({ title: "Run started", detail: "Short-term reversal, sweep 12", action: { label: "Show", onClick: none } })}>Show a toast</TxButton>
        <TxButton size="sm" onClick={() => toast({ tone: "failed", title: "Publish failed", detail: "The library is read-only while another window saves." })}>Show a failure</TxButton>
      </div>
    </Block>
    <Block title="Disclosure and rows" className="ds-span2">
      <div className="ds-rows">
        <TxDisclosure label="Limits by group" defaultOpen><div className="tx-muted">Equity 0–100%, Bonds 0–55%</div></TxDisclosure>
        <TxSettingsGroup title="Modules" action={<TxButton size="sm"><TxIcon name="plus" />Add</TxButton>}>
          <TxRow icon="chart" title="Plots" detail="Lines, bars, drawings"><TxSwitch hideLabel label="Plots" checked onCheckedChange={none} /></TxRow>
          <TxRow icon="flask" title="Lab" detail="Couldn't start: Python environment missing" tone="failed"><TxButton size="sm">Retry</TxButton></TxRow>
        </TxSettingsGroup>
      </div>
    </Block>
  </div>;
}

/* ── Icons ──────────────────────────────────────────────── */

function Icons() {
  return <div className="tx-col ds-stack">
    <div className="ds-icon-grid" aria-label="Icon set">
      {txIconNames.map(name => <div key={name} className="ds-icon-tile" data-icon={name}><TxIcon name={name} /><span>{name}</span></div>)}
    </div>
    <div className="ds-icon-sizes">{([20, 16, 14, 12] as const).map(size => <div key={size} className="tx-row">
      <span className="ds-type-size">{size}</span>{(["chart", "function", "trend", "crosshair", "bell", "play", "lock", "alert", "settings", "search"] as IconName[]).map(name => <TxIcon key={name} name={name} style={{ width: size, height: size }} />)}
    </div>)}</div>
  </div>;
}

/* ── Density ────────────────────────────────────────────── */

function DensitySample({ density }: { density: TxDensity }) {
  const [tf, setTf] = useState("1h");
  return <div className="ds-density-col">
    <div className="ds-density-name">{density === "compact" ? "Compact" : density === "standard" ? "Standard" : "Comfortable"}<span>{density === "compact" ? "Toolbars on plots, long tables" : density === "standard" ? "Panels, settings, results" : "Conversations"}</span></div>
    <TxPanel density={density} title="Queued items" actions={<TxIconButton size="sm" label="More" icon="more" />}>
      <div className="tx-col ds-density-body">
        <div className="tx-row tx-nowrap"><TxSegmented label="Timeframe" value={tf} onChange={setTf} options={["5m", "1h", "1D"].map(value => ({ value, label: value }))} /><span className="tx-spacer" /><TxButton size="sm">Cancel all</TxButton></div>
        <TxField label="Price"><TxInput numeric suffix="USDT" defaultValue="64,120.50" /></TxField>
        <TxCheckbox label="This group only" checked onCheckedChange={none} />
        <TxTable>
          <thead><tr><TxTh>Name</TxTh><TxTh numeric>Price</TxTh><TxTh numeric>Change</TxTh></tr></thead>
          <tbody>{[["BTC/USDT", "64,182.4", "+412.08", "up"], ["ETH/USDT", "3,402.15", "−88.20", "down"], ["SOL/USDT", "148.72", "+12.66", "up"]].map(([s, p, v, tone]) =>
            <tr key={s}><TxTd>{s}</TxTd><TxTd numeric>{p}</TxTd><TxTd numeric className={`tx-${tone}`}>{v}</TxTd></tr>)}</tbody>
        </TxTable>
        <div className="tx-row"><TxButton variant="primary" size="sm">Apply</TxButton><TxButton variant="quiet" size="sm">Clear</TxButton></div>
      </div>
    </TxPanel>
  </div>;
}

function Density() {
  return <div className="ds-density">{(["compact", "standard", "comfortable"] as const).map(density => <DensitySample key={density} density={density} />)}</div>;
}

/* ── Tool variants ──────────────────────────────────────── */

function Variants() {
  const [tool, setTool] = useState<IconName>("trend"), [tf, setTf] = useState("15m");
  const tools: [IconName, string][] = [["pointer", "Pointer"], ["crosshair", "Crosshair"], ["trend", "Trend line"], ["ruler", "Measure"], ["text", "Text"], ["magnet", "Snap to values"]];
  return <div className="ds-variants">
    <Block title="Horizontal toolbar, compact">
      <div className="ds-frame">
        <TxToolbar label="Plot toolbar" density="compact">
          <TxButton variant="tool"><TxIcon name="search" />ETHUSDT</TxButton><TxToolbarDivider />
          <TxSegmented variant="tool" label="Timeframe" value={tf} onChange={setTf} options={["1m", "5m", "15m", "1h", "4h", "1D"].map(value => ({ value, label: value }))} /><TxToolbarDivider />
          <TxIconButton variant="tool" label="Bars" icon="chart" /><TxButton variant="tool"><TxIcon name="function" />Overlays</TxButton>
          <TxIconButton variant="tool" label="Notify me" icon="bell" /><TxSpacer />
          <TxIconButton variant="tool" label="Undo" icon="undo" /><TxIconButton variant="tool" label="Redo" icon="redo" disabled /><TxIconButton variant="tool" label="Snapshot" icon="camera" />
        </TxToolbar>
      </div>
    </Block>
    <Block title="Data toolbar: one tool primary, the rest tools">
      <div className="ds-frame">
        <TxToolbar label="Data toolbar" density="compact">
          <TxButton variant="tool-primary"><TxIcon name="plus" />Get data</TxButton><TxToolbarDivider />
          <TxButton variant="tool"><TxIcon name="refresh" />Update</TxButton><TxButton variant="tool"><TxIcon name="download" />Import</TxButton>
          <TxButton variant="tool"><TxIcon name="arrow-up-right" />Export</TxButton><TxButton variant="tool" disabled><TxIcon name="trash" />Delete</TxButton>
        </TxToolbar>
      </div>
    </Block>
    <Block title="Drawing rail">
      <div className="ds-frame ds-rail-demo">
        <TxToolbar label="Drawing tools" orientation="vertical" density="compact">
          {tools.map(([icon, label]) => <TxIconButton key={icon} variant="tool" label={label} icon={icon} aria-pressed={tool === icon} onClick={() => setTool(icon)} />)}
          <TxToolbarDivider /><TxIconButton variant="tool" label="Hide drawings" icon="eye" /><TxIconButton variant="tool" label="Remove drawings" icon="trash" />
        </TxToolbar>
        <div className="ds-rail-canvas"><RangeBars width={600} height={260} count={60} seed={5} /></div>
      </div>
    </Block>
    <Block title="Standard toolbar">
      <div className="ds-frame">
        <TxToolbar label="Workbench toolbar">
          <TxButton variant="primary" size="sm"><TxIcon name="play" />Run</TxButton><TxButton size="sm">Duplicate</TxButton><TxToolbarDivider />
          <TxIconButton variant="outline" size="sm" label="Refresh" icon="refresh" /><TxIconButton size="sm" label="Filter" icon="filter" /><TxSpacer />
          <span className="tx-toolbar-text">Saved 14:02</span><TxIconButton size="sm" label="More" icon="more" />
        </TxToolbar>
      </div>
    </Block>
  </div>;
}

/* ── In modules: a plot ─────────────────────────────────── */

function PlotDemo() {
  const [tf, setTf] = useState("1h"), [tool, setTool] = useState<IconName>("crosshair");
  const data = useRanges(96, 33);
  const last = data[data.length - 1], prev = data[data.length - 2];
  const change = (last.close - prev.close) / prev.close * 100;
  const max = Math.max(...data.map(d => d.high)), min = Math.min(...data.map(d => d.low));
  const ticks = Array.from({ length: 6 }, (_, i) => max - (max - min) * (i + .5) / 6);
  const lastTop = (16 + (max - last.close) / (max - min) * (380 - 32)) / 380 * 100;
  const overlays = useRef<HTMLButtonElement>(null), canvas = useRef<HTMLDivElement>(null);
  const [menuLeft, setMenuLeft] = useState(0);
  useEffect(() => {
    const button = overlays.current?.getBoundingClientRect(), area = canvas.current?.getBoundingClientRect();
    if (button && area) setMenuLeft(button.left - area.left);
  }, []);
  return <div className="ds-demo ds-plot" id="in-plot">
    <TxToolbar label="Plot" density="compact">
      <TxButton variant="tool" className="ds-series"><TxIcon name="search" />BTCUSDT</TxButton>
      <TxToolbarDivider />
      <TxSegmented variant="tool" label="Timeframe" value={tf} onChange={setTf} options={["1m", "5m", "15m", "1h", "4h", "1D", "1W"].map(value => ({ value, label: value }))} />
      <TxToolbarDivider />
      <TxIconButton variant="tool" label="Chart type" icon="chart" />
      <TxButton ref={overlays} variant="tool" aria-expanded data-state="open"><TxIcon name="function" />Overlays</TxButton>
      <TxIconButton variant="tool" label="Compare" icon="plus" />
      <TxIconButton variant="tool" label="Notify me" icon="bell" />
      <TxIconButton variant="tool" label="Replay" icon="history" />
      <TxSpacer />
      <TxIconButton variant="tool" label="Undo" icon="undo" /><TxIconButton variant="tool" label="Redo" icon="redo" disabled />
      <TxToolbarDivider />
      <TxIconButton variant="tool" label="Snapshot" icon="camera" /><TxIconButton variant="tool" label="Chart settings" icon="settings" /><TxIconButton variant="tool" label="Full screen" icon="maximize" />
    </TxToolbar>
    <div className="ds-plot-body">
      <TxToolbar label="Drawing tools" orientation="vertical" density="compact">
        {([["pointer", "Pointer"], ["crosshair", "Crosshair"], ["trend", "Trend line"], ["ruler", "Measure"], ["text", "Text"], ["magnet", "Snap to values"]] as [IconName, string][]).map(([icon, label]) =>
          <TxIconButton key={icon} variant="tool" label={label} icon={icon} aria-pressed={tool === icon} onClick={() => setTool(icon)} />)}
        <TxToolbarDivider />
        <TxIconButton variant="tool" label="Hide drawings" icon="eye" />
        <TxIconButton variant="tool" label="Remove drawings" icon="trash" />
      </TxToolbar>
      <div className="ds-plot-canvas" data-tx-density="compact" ref={canvas}>
        <div className="ds-plot-legend">
          <div className="ds-legend-main"><b>BTCUSDT</b><span>1h</span><span>Primary feed</span>
            <span className="tx-num">O <em>{money(last.open, 1)}</em></span><span className="tx-num">H <em>{money(last.high, 1)}</em></span>
            <span className="tx-num">L <em>{money(last.low, 1)}</em></span><span className="tx-num">C <em className={change >= 0 ? "tx-up" : "tx-down"}>{money(last.close, 1)}</em></span>
            <span className={`tx-num ${change >= 0 ? "tx-up" : "tx-down"}`}>{signed(change, 2, "%")}</span></div>
          <div className="ds-legend-ind"><i className="ds-ma-1" />MA 20 <span className="tx-num">{money(last.close * .997, 1)}</span></div>
          <div className="ds-legend-ind"><i className="ds-ma-2" />EMA 50 <span className="tx-num">{money(last.close * .989, 1)}</span></div>
        </div>
        <div className="ds-plot-plot"><RangeBars width={1000} height={380} count={96} seed={33} averages /></div>
        <div className="ds-plot-axis">{ticks.map(t => <span key={t} className="tx-num">{money(t, 1)}</span>)}</div>
        <div className="ds-plot-last tx-num" data-tone={change >= 0 ? "up" : "down"} style={{ top: `${lastTop}%` }}>{money(last.close, 1)}</div>
        <div className="ds-plot-menu" style={{ left: menuLeft }}><TxMenu preview label="Overlays" trigger={null} items={overlayItems} /></div>
      </div>
    </div>
  </div>;
}

/* ── In modules: a conversation ─────────────────────────── */

function ToolCall({ name, target, status, time, open = false, children }: { name: string; target: string; status: "done" | "busy" | "failed"; time?: string; open?: boolean; children?: ReactNode }) {
  const [expanded, setExpanded] = useState(open);
  return <div className="ds-toolcall" data-open={expanded ? "" : undefined}>
    <button type="button" className="ds-toolcall-head" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
      <TxIcon name={status === "failed" ? "x-circle" : "wrench"} />
      <span className="ds-toolcall-name">{name}</span><span className="ds-toolcall-target">{target}</span>
      <span className="tx-spacer" />
      <TxStatus tone={status === "done" ? "done" : status === "busy" ? "busy" : "failed"} label={status === "done" ? time : status === "busy" ? "Running" : "Failed"} />
      <TxIcon name="chevron" className="ds-toolcall-chev" />
    </button>
    {expanded && children && <div className="ds-toolcall-body">{children}</div>}
  </div>;
}

function ConversationDemo() {
  const [draft, setDraft] = useState("");
  return <div className="ds-demo ds-talk" id="in-conversation" data-tx-density="comfortable">
    <div className="ds-talk-head"><b>Workspace panels</b><TxTag tone="muted">Research model</TxTag><span className="tx-spacer" /><TxIconButton label="History" icon="history" /><TxIconButton label="New conversation" icon="compose" /></div>
    <div className="ds-talk-scroll"><div className="ds-talk-thread">
      <div className="ds-msg-user">Read which panels are open in this workspace, then close Notes if nothing is pending there.</div>
      <div className="ds-msg-ai">I'll look at the workspace first.</div>
      <ToolCall name="Read workspace" target="platform.read_workspace" status="done" time="0.4 s" open>
        <div className="ds-toolcall-io"><span>Input</span><pre className="tx-num">{`{ "include": ["panels", "layout"] }`}</pre>
          <span>Result</span><pre className="tx-num">{`4 panels: Lab (Short-term reversal), Plot (BTCUSDT 1h),
Notes (6 pages, 38 unsaved edits), Editor`}</pre></div>
      </ToolCall>
      <ToolCall name="Read unsaved edits" target="notes.read_edits" status="done" time="1.1 s" />
      <div className="ds-msg-ai">Notes has <b>38 unsaved edits</b>, so closing it would set them aside. Lab, Plot and Editor are idle.</div>
      <div className="ds-approval">
        <div className="ds-approval-head"><TxIcon name="shield" /><b>Close the Notes panel?</b><span className="tx-spacer" /><TxTag tone="fix">Needs approval</TxTag></div>
        <div className="ds-approval-body">Unsaved edits stay in their drafts; this only closes the panel.</div>
        <div className="tx-row"><TxButton size="sm" variant="primary">Approve</TxButton><TxButton size="sm" variant="quiet">Deny</TxButton><span className="tx-spacer" /><span className="tx-muted ds-approval-op">platform.write_workspace</span></div>
      </div>
      <ToolCall name="Close panel" target="platform.write_workspace" status="busy" />
    </div></div>
    <div className="ds-composer">
      <TxTextarea rows={2} aria-label="Message" placeholder="Ask about this workspace" value={draft} onChange={event => setDraft(event.target.value)} />
      <div className="ds-composer-bar">
        <TxIconButton label="Attach a file" icon="paperclip" />
        <TxButton variant="tool" size="sm">Research model<TxIcon name="chevron-down" /></TxButton>
        <TxButton variant="tool" size="sm"><TxIcon name="shield" />Ask before changes<TxIcon name="chevron-down" /></TxButton>
        <span className="tx-spacer" />
        <TxButton variant="primary" size="sm" aria-label="Send"><TxIcon name="arrow-up" />Send</TxButton>
      </div>
    </div>
  </div>;
}

/* ── In modules: a long list ────────────────────────────── */

function ListDemo() {
  const [tab, setTab] = useState("items"), [scope, setScope] = useState("all"), [query, setQuery] = useState("");
  const items = useMemo(() => {
    const random = seeded(41);
    return Array.from({ length: 28 }, (_, index) => {
      const name = pairs[index % pairs.length];
      const up = random() > .4, start = index === 0 ? 61840.5 : .2 + random() * 3600, move = (random() - .48) * .12;
      const now = start * (1 + move), amount = (random() * 50 + .01) * (start > 1000 ? .05 : 10);
      const change = (now - start) * amount * (up ? 1 : -1), reserve = Math.abs(start * amount) / (index % 3 === 0 ? 10 : 5);
      return { name, source: index % 2 ? "Primary feed" : "Backup feed", up, amount, start, now, floor: start * (up ? .82 : 1.18), reserve, change, percent: change / reserve * 100 };
    });
  }, []);
  const shown = items.filter(row => (scope === "all" || (scope === "up") === row.up) && row.name.toLowerCase().includes(query.toLowerCase()));
  const total = shown.reduce((sum, row) => sum + row.change, 0);
  return <div className="ds-demo ds-ledger" id="in-list" data-tx-density="compact">
    <div className="ds-ledger-head">
      <TxTabs value={tab} onChange={setTab} options={[{ value: "items", label: "Items", count: 28 }, { value: "queued", label: "Queued", count: 38 }, { value: "done", label: "Done", count: 1204 }, { value: "history", label: "History" }]} />
      <div className="ds-ledger-tools">
        <TxSegmented size="sm" label="Direction" value={scope} onChange={setScope} options={[{ value: "all", label: "All" }, { value: "up", label: "Up" }, { value: "down", label: "Down" }]} />
        <TxSearch value={query} onValueChange={setQuery} placeholder="Name" aria-label="Filter rows" />
        <TxButton size="sm" variant="quiet">Clear all</TxButton>
      </div>
    </div>
    <div className="ds-ledger-scroll">
      <TxTable stickyHead>
        <thead><tr><TxTh>Name</TxTh><TxTh>Direction</TxTh><TxTh numeric>Amount</TxTh><TxTh numeric>Start</TxTh><TxTh numeric sort="none">Now</TxTh><TxTh numeric>Floor</TxTh><TxTh numeric>Reserve</TxTh><TxTh numeric sort="descending">Change</TxTh><TxTh numeric>Change %</TxTh><TxTh aria-label="Actions" /></tr></thead>
        <tbody>{shown.map((row, index) => {
          const digits = row.start > 100 ? 1 : row.start > 1 ? 3 : 5;
          return <tr key={index} data-selected={index === 3}>
            <TxTd><span className="tx-cell-name">{row.name}</span> <span className="tx-muted">{row.source}</span></TxTd>
            <TxTd className={row.up ? "tx-up" : "tx-down"}>{row.up ? "Up" : "Down"}</TxTd>
            <TxTd numeric>{money(row.amount, 3)}</TxTd><TxTd numeric className="tx-secondary">{money(row.start, digits)}</TxTd><TxTd numeric>{money(row.now, digits)}</TxTd>
            <TxTd numeric className="tx-muted">{money(row.floor, digits)}</TxTd><TxTd numeric className="tx-secondary">{money(row.reserve)}</TxTd>
            <TxTd numeric className={row.change >= 0 ? "tx-up" : "tx-down"}>{signed(row.change)}</TxTd>
            <TxTd numeric className={row.percent >= 0 ? "tx-up" : "tx-down"}>{signed(row.percent, 2, "%")}</TxTd>
            <TxTd align="right"><span className="ds-rowacts"><TxButton size="sm" variant="quiet">Remove</TxButton><TxIconButton size="sm" label="Edit floor" icon="edit" /></span></TxTd>
          </tr>;
        })}</tbody>
      </TxTable>
    </div>
    <div className="ds-ledger-foot"><span>{shown.length} rows</span><span className="tx-spacer" /><span>Change</span><b className={`tx-num ${total >= 0 ? "tx-up" : "tx-down"}`}>{signed(total)}</b><span>Reserved</span><b className="tx-num">{money(shown.reduce((sum, row) => sum + row.reserve, 0))}</b></div>
  </div>;
}

/* ── In modules: a result ───────────────────────────────── */

function ResultDemo() {
  const [view, setView] = useState("work");
  const runs: TxMenuItem[] = [{ id: "22", label: "#22", hint: "Oct 30", checked: true, onSelect: none }, { id: "21", label: "#21", hint: "Oct 30", onSelect: none }];
  return <div className="ds-demo ds-result" id="in-result">
    <div className="tx-panelhead">
      <TxObjectTitle name="Short-term reversal" kind="Factor" tags={<TxTag tone="version">v3</TxTag>} />
      <TxSpacer />
      <TxRunButton label="Sweep 12" kinds={[{ id: "bt", label: "Run once", icon: "play" }, { id: "sw", label: "Sweep 12", icon: "grid", current: true }]} onRun={none} onKind={none} />
      <TxSegmented label="Work or Results" value={view} onChange={setView} options={[{ value: "work", label: "Work" }, { value: "results", label: "Results" }]} />
      <TxIconButton label="Actions" icon="more" />
    </div>
    <TxRunStrip picker={<TxRunPicker run="#22" items={runs} />} when="Oct 30, 22:06" meta={["Jan 2016 – Dec 2024", "data to Oct 30"]} notes={4}
      compare={<TxCompare run="#21" items={runs} />} actions={<><TxIconButton size="sm" label="Star" icon="star" /><TxButton size="sm" variant="quiet"><TxIcon name="book" />Notebook</TxButton><TxButton size="sm"><TxIcon name="bookmark" />Publish</TxButton></>} />
    <TxKeyNumbers items={[
      { label: "Rank IC, 5D", value: "+0.0159", tone: "up", delta: txDelta("+0.0006", "#21") },
      { label: "t, overlap-corrected", value: "3.73", delta: txDelta("+0.30", "#21") },
      { label: "Long–short, net", value: "+7.93%", unit: "a year", tone: "up", delta: txDelta("+0.83 pt", "#21") },
      { label: "Turnover", value: "80%", unit: "a rebalance", delta: txDelta("0 pt", "#21") },
      { label: "Max drawdown", value: "−12.4%", tone: "down", delta: txDelta("−1.1 pt", "#21") },
      { label: "Names a day", value: "496", delta: txDelta("0", "#21") },
    ]} />
    <div className="ds-result-split">
      <div className="ds-result-plot"><RangeBars width={700} height={200} count={70} seed={8} /></div>
      <TxChecks scope="in-sample" groups={[{ items: [
        { name: "Significance", value: "3.73", need: "≥ 3.00", state: "ok" },
        { name: "Groups in order", value: "Q2 > Q3", need: "in order", state: "watch" },
        { name: "Future data", value: "yes", need: "no", state: "fix" },
        { name: "Held-out", need: "≥ 0.96", state: "notrun" },
      ] }]} />
    </div>
  </div>;
}

function InModules() {
  return <div className="ds-inmodules">
    <Block title="A plot: toolbar with the overlay menu open"><PlotDemo /></Block>
    <div className="ds-inmodules-row">
      <Block title="A conversation with tool calls"><ConversationDemo /></Block>
      <Block title="A result: panel head and key numbers"><ResultDemo /></Block>
    </div>
    <Block title="A long list: filters, many rows, totals"><ListDemo /></Block>
  </div>;
}

export default function DesignSystemPage() {
  return <TxRoot className="ds-page"><TxTooltipProvider><TxToastProvider>
    <nav className="ds-nav" aria-label="Sections">
      <div className="ds-brand">TALYX<span>Components</span></div>
      {sections.map(([id, title]) => <a key={id} href={`#${id}`}>{title}</a>)}
    </nav>
    <main className="ds-main">
      <Section id="tokens" title="Values"><Tokens /></Section>
      <Section id="buttons" title="Buttons"><Buttons /></Section>
      <Section id="inputs" title="Inputs"><Inputs /></Section>
      <Section id="selection" title="Selection"><Selection /></Section>
      <Section id="menus" title="Menus and tips"><Menus /></Section>
      <Section id="overlays" title="Dialogs and sheets"><Overlays /></Section>
      <Section id="tables" title="Tables"><Tables /></Section>
      <Section id="feedback" title="Status and feedback"><Feedback /></Section>
      <Section id="icons" title="Icons"><Icons /></Section>
      <Section id="density" title="Density"><Density /></Section>
      <Section id="variants" title="Tool variants"><Variants /></Section>
      <Section id="in-modules" title="In modules"><InModules /></Section>
    </main>
  </TxToastProvider></TxTooltipProvider></TxRoot>;
}
