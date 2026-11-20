/* A strategy's settings column: the code's own settings (declared in its files) above the platform's run settings, and what the code reads from the library. */
import type { ReactNode } from "react";
import { TxIcon, TxInput, TxLinkedInputs, TxParamGroup, TxParamLayer, TxParams, TxSelect, TxButton, TxCheckbox } from "@talyx/sdk";
import type { LibraryItemSummary, Parameter, ParameterError, ParamValue } from "../objects";
import { Check, NumberBox, Row } from "../shared/SettingRows";
import { mdy } from "../factor/format";
import type { InputCard, RunSettingsView, StrategyRun } from "./api";
import { RangeBox } from "./validation/RangeBox";
import { EVERY } from "./validation/ranges";
import { DayInput } from "../shared/DayInput";
import { paramLabel } from "./names";

/** What each benchmark kind is called, and the definitions the hover gives (the page writes nothing else about them). */
export const BENCHMARKS: Record<string, {label: (kind: string) => string; title?: string}> = {
  hold: {label: kind => kind === "stock" ? "Buy and hold" : "BTC buy and hold"}, none: {label: () => "None"},
  beta: {label: () => "Cash + β × S&P 500", title: "Cash plus β times the S&P 500; β is measured over this run"},
  equal: {label: () => "S&P 500 equal weight", title: "The members of this run's universe, equally weighted each day"},
  symbol: {label: () => "A symbol"},
};
const RISK_FREE = [["irx", "T-bill (^IRX)"], ["zero", "Zero"], ["fixed", "Fixed…"]] as const;
const MARGIN_TITLE = "Leveraged: margin = notional ÷ leverage. Standard: margin = a fixed share of notional";

/** The settings that differ between two settings objects, by dotted name ("exchange.leverage"). */
export function differing(now: unknown, before: unknown, prefix = ""): Set<string> {
  const found = new Set<string>();
  const walk = (a: unknown, b: unknown, path: string) => {
    if (a !== null && typeof a === "object" && !Array.isArray(a)) {
      for (const key of new Set([...Object.keys(a as object), ...Object.keys((b ?? {}) as object)])) walk((a as Record<string, unknown>)[key], ((b ?? {}) as Record<string, unknown>)[key], path ? `${path}.${key}` : key);
    } else if (a !== undefined && b !== undefined && JSON.stringify(a) !== JSON.stringify(b) && !(a === null && b === undefined)) found.add(path);  // a setting one side does not have is not a change (older runs, what a run adds)
  };
  walk(now, before, prefix);
  return found;
}

export { paramLabel };

function Held({value, onChange}: {value: string | null; onChange(day: string | null): void}) {
  return <span className="fx-held"><TxIcon name="lock" /><DayInput label="Held out from" value={value} empty onChange={onChange} /></span>;
}

export function SettingsColumn({signalRun, entry, since, view, parameters, errors, params, library, base, changed, inputs, riskFreeMean, onParam, onRun, onRevert, onLine, onSwitch, onOpenRun, ranges, rangeErrors, combinations, onRange, walk, onWalk, heldMissing}: {
  signalRun?: {param: string; model: string; number: number; kind?: string} | null;
  entry: string; since?: string; view: RunSettingsView | null; parameters: Parameter[]; errors: ParameterError[]; params: Record<string, ParamValue>; library: LibraryItemSummary[] | null;
  /** The run the settings are compared with. */
  base: StrategyRun | null; changed: Set<string>; inputs: InputCard[]; riskFreeMean: number | null | undefined;
  onParam(name: string, value: ParamValue): void; onRun(patch: Record<string, unknown>): void; onRevert(): void; onLine(file: string | undefined, line: number): void;
  onSwitch(name: string, versionId: string): void; onOpenRun(runId: string): void;
  /** The boxes that hold several values (a range to sweep), the error of each, how many combinations they make, and the way to set or clear one. */
  ranges?: Record<string, string>; rangeErrors?: Record<string, string>; combinations?: number; onRange?(name: string, text: string | null): void;
  /** Walk-forward: how many months to pick values on, how many to trade them, and what picks them. Shown while a code setting holds a range. */
  /** Validate on held-out was asked for with no held-out start set: the row is marked and brought into view. */
  heldMissing?: boolean;
  walk?: {pickOn: number; trade: number; pickBy: "sharpe" | "cagr" | "calmar"}; onWalk?(patch: Partial<{pickOn: number; trade: number; pickBy: "sharpe" | "cagr" | "calmar"}>): void;
}) {
  const run = view?.effective;
  const kind = view?.kind ?? "perpetual";
  const shows = (field: string) => view?.applicable.exchange.includes(field) ?? false;
  const set = (group: "exchange" | "costs", key: string, value: unknown) => onRun({[group]: {[key]: value}});
  const mark = (...keys: string[]) => keys.some(key => changed.has(key));
  const restoreRun = (path: string) => {
    if (!base) return;
    const [group, key] = path.split(".");
    const prior = base.settings.run as Record<string, any>;
    if (!key || group === "riskFree" || group === "benchmark") onRun({[group]: prior[group]});
    else onRun({[group]: {[key]: prior[group][key]}});
  };
  const codeChanged = [...changed].filter(key => key.startsWith("param:")).length;
  const codeField = (item: Parameter): ReactNode => {
    const value = params[item.name] ?? item.default;
    if ((item.type === "int" || item.type === "float") && onRange) return <RangeBox label={paramLabel(item).label} value={Number(value)} unit={paramLabel(item).unit} range={ranges?.[item.name]} error={rangeErrors?.[item.name]}
      onValue={next => onParam(item.name, next)} onRange={text => onRange(item.name, text)} />;
    if (item.type === "int" || item.type === "float") return <NumberBox label={paramLabel(item).label} value={Number(value)} unit={paramLabel(item).unit} onValue={next => next !== null && onParam(item.name, next)} />;
    if (item.type === "bool") return <TxCheckbox label={item.label === item.name ? "On" : item.label} checked={Boolean(value)} onCheckedChange={next => onParam(item.name, next)} />;
    if (item.type === "choice") return <TxSelect aria-label={item.label} value={onRange && ranges?.[item.name] === EVERY ? EVERY : JSON.stringify(value)}
      onChange={event => {if (event.target.value === EVERY) {onRange?.(item.name, EVERY); return;} onRange?.(item.name, null); onParam(item.name, JSON.parse(event.target.value));}}>
      {(item.options ?? []).map(option => <option key={JSON.stringify(option.value)} value={JSON.stringify(option.value)}>{option.label}</option>)}
      {onRange && <option value={EVERY}>Each (sweep)</option>}</TxSelect>;
    if (item.type === "library" && signalRun?.param === item.name && !value) return <span className="fx-ro">{signalRun.model} #{signalRun.number} {signalRun.kind === "factor" ? "values" : "predictions"}</span>;
    if (item.type === "library" || item.type === "pick") {
      const items = (library ?? []).filter(entry => item.type === "library" || (item.kinds ?? ["factor"]).includes(entry.kind));
      const current = item.type === "library" ? String(value ?? "") : (value as unknown as {name?: string} | null)?.name ?? "";
      return <TxSelect aria-label={item.label} value={current} onChange={event => {
        const chosen = items.find(entry => entry.name === event.target.value);
        onParam(item.name, (item.type === "library" ? event.target.value : chosen ? {name: chosen.name, version: chosen.latest.version} : null) as ParamValue);
      }}><option value="">None</option>{items.map(entry => <option key={entry.itemId} value={entry.name}>{entry.name}</option>)}</TxSelect>;
    }
    return <TxInput aria-label={item.label} value={String(value ?? "")} onChange={event => onParam(item.name, event.target.value)} />;
  };
  const accounts = run?.accounts;
  const feeSource = run?.costs.feeSource;
  const limit = run?.limitThroughTicks ?? 0;
  const limitMode = limit === 0 ? "touch" : limit === 1 ? "one" : "many";
  return <TxParams footer={changed.size && base ? <><span className="fx-dot" data-tone="now" />{changed.size} changed since #{base.number}<span className="tx-spacer" />
    <TxButton variant="quiet" size="sm" onClick={onRevert}>Revert</TxButton></> : undefined}>
    <TxParamLayer title="Code settings" source={entry} changed={codeChanged} />
    {combinations !== undefined && combinations > 1 && <div className="sv-combos">{combinations} combinations ×</div>}
    {errors.map((error, index) => <button key={index} type="button" className="fx-paramerror" onClick={() => error.line && onLine(error.file, error.line)}><TxIcon name="alert" /><span>{error.message}</span></button>)}
    <div className="tx-params-group">{parameters.map(item => item.type === "bool" && onRange
      ? <Row key={item.name} id={`param:${item.name}`} label={paramLabel(item).label} changed={changed.has(`param:${item.name}`)} onRevert={() => {onRange?.(item.name, null); onParam(item.name, base?.settings.params[item.name] ?? item.default);}} hint={item.help ?? undefined}>
        <TxSelect aria-label={paramLabel(item).label} value={ranges?.[item.name] === EVERY ? "both" : Boolean(params[item.name] ?? item.default) ? "on" : "off"}
          onChange={event => {const next = event.target.value; if (next === "both") {onRange(item.name, EVERY); return;} onRange(item.name, null); onParam(item.name, next === "on");}}>
          <option value="on">On</option><option value="off">Off</option><option value="both">Both (sweep)</option></TxSelect></Row>
      : item.type === "bool"
      ? <Check key={item.name} label={item.label} checked={Boolean(params[item.name] ?? item.default)} changed={changed.has(`param:${item.name}`)} onRevert={() => {onRange?.(item.name, null); onParam(item.name, base?.settings.params[item.name] ?? item.default);}} onChange={next => onParam(item.name, next)} />
      : <Row key={item.name} id={`param:${item.name}`} label={paramLabel(item).label} changed={changed.has(`param:${item.name}`)} onRevert={() => {onRange?.(item.name, null); onParam(item.name, base?.settings.params[item.name] ?? item.default);}}
        hint={item.help ?? `q.param("${item.name}")${item.file ? `, ${item.file} line ${item.line}` : ""}`}>{codeField(item)}</Row>)}</div>
    {!parameters.length && !errors.length && <div className="fx-paramnone">None declared</div>}
    {walk && onWalk && combinations !== undefined && combinations > 1 && <>
      <TxParamLayer title="Walk-forward" />
      <div className="tx-params-group">
        <Row id="walk:pickOn" label="Pick on" changed={false}><NumberBox label="Pick on" value={walk.pickOn} unit="months" onValue={next => next !== null && next >= 1 && onWalk({pickOn: Math.round(next)})} /></Row>
        <Row id="walk:trade" label="Then trade" changed={false}><NumberBox label="Then trade" value={walk.trade} unit="months" onValue={next => next !== null && next >= 1 && onWalk({trade: Math.round(next)})} /></Row>
        <Row id="walk:pickBy" label="Pick by" changed={false}><TxSelect aria-label="Pick by" value={walk.pickBy} onChange={event => onWalk({pickBy: event.target.value as "sharpe" | "cagr" | "calmar"})}>
          <option value="sharpe">Sharpe</option><option value="cagr">CAGR</option><option value="calmar">Calmar</option></TxSelect></Row>
      </div><p className="sv-walknote">Re-picks from the ranges in Code settings, {combinations} combinations a window</p></>}
    {run && view && <>
      <TxParamLayer title="Run settings" changed={[...changed].filter(key => !key.startsWith("param:")).length} />
      <TxParamGroup defaultOpen={false} title="Account" summary={accounts ? `${accounts.length} venues` : `${run.capital.toLocaleString("en-US")} ${run.currency ?? ""}`.trim()} changed={mark("capital", "maxPosition", "accounts")}>
        {accounts
          ? <><div className="qx-accounts" role="table" aria-label="Capital by venue"><div role="row"><span>Venue</span><span>Balances</span></div>
            {accounts.map((account, index) => <div role="row" key={account.venue}><span>{account.venue}</span>
              <TxInput aria-label={`${account.venue} balances`} defaultValue={account.balances.join(", ")}
                onBlur={event => onRun({accounts: accounts.map((other, at) => at === index ? {...other, balances: event.target.value.split(",").map(item => item.trim()).filter(Boolean)} : other)})} /></div>)}</div>
            <div className="fx-dr"><span>Reported in</span><span className="fx-ro">{run.reportCurrency}</span></div></>
          : <Row id="capital" label="Capital" changed={changed.has("capital")} onRevert={() => restoreRun("capital")}><NumberBox label="Capital" value={run.capital} unit={run.currency} onValue={next => next !== null && onRun({capital: next})} /></Row>}
        <Row id="maxPosition" label="Max position" changed={changed.has("maxPosition")} onRevert={() => restoreRun("maxPosition")}>
          <NumberBox label="Max position" value={Math.round(run.maxPosition * 10000) / 100} unit="% of equity" onValue={next => next !== null && onRun({maxPosition: next / 100})} /></Row>
      </TxParamGroup>
      {(shows("leverage") || shows("margin")) && <TxParamGroup defaultOpen={false} title="Exchange" summary={kind === "stock" ? "Reg T" : `${run.exchange.leverage}×, ${run.exchange.marginModel}`}
        changed={mark("exchange.leverage", "exchange.marginModel", "exchange.liquidation", "exchange.maintenancePct", "exchange.stopTrigger", "exchange.positionMode")}>
        {shows("leverage") && <Row id="leverage" label="Leverage" changed={changed.has("exchange.leverage")} onRevert={() => restoreRun("exchange.leverage")}>
          <span className="qx-lev"><NumberBox label="Leverage" value={run.exchange.leverage} unit="×" onValue={next => next !== null && set("exchange", "leverage", next)} />
            {run.exchange.maxLeverage ? <span className="fx-t3">max {run.exchange.maxLeverage}×</span> : null}</span></Row>}
        {shows("margin") && <Row id="margin" label="Margin" changed={false}><span className="qx-ro">{view.applicable.margin}</span></Row>}
        {shows("marginModel") && <Row id="marginModel" label="Margin" hint={MARGIN_TITLE} changed={changed.has("exchange.marginModel")} onRevert={() => restoreRun("exchange.marginModel")}>
          <TxSelect aria-label="Margin" title={MARGIN_TITLE} value={run.exchange.marginModel} onChange={event => set("exchange", "marginModel", event.target.value)}>
            <option value="leveraged">Leveraged</option><option value="standard">Standard</option></TxSelect></Row>}
        {shows("liquidation") && <><Check label="Liquidate below maintenance" checked={run.exchange.liquidation} changed={changed.has("exchange.liquidation")} onRevert={() => restoreRun("exchange.liquidation")} onChange={next => set("exchange", "liquidation", next)} />
          {run.exchange.liquidation && <Row id="maintenance" label="Maintenance" changed={changed.has("exchange.maintenancePct")} onRevert={() => restoreRun("exchange.maintenancePct")}>
            <NumberBox label="Maintenance" value={run.exchange.maintenancePct} unit="%" onValue={next => next !== null && set("exchange", "maintenancePct", next)} /></Row>}</>}
        {shows("stopTrigger") && <Row id="stopTrigger" label="Stop trigger" changed={changed.has("exchange.stopTrigger")} onRevert={() => restoreRun("exchange.stopTrigger")}>
          <TxSelect aria-label="Stop trigger" value={run.exchange.stopTrigger} onChange={event => set("exchange", "stopTrigger", event.target.value)}>
            <option value="last">Last price</option><option value="mark">Mark price</option><option value="index">Index price</option></TxSelect></Row>}
        {shows("positionMode") && <Row id="positionMode" label="Position mode" changed={changed.has("exchange.positionMode")} onRevert={() => restoreRun("exchange.positionMode")}>
          <TxSelect aria-label="Position mode" value={run.exchange.positionMode} onChange={event => set("exchange", "positionMode", event.target.value)}><option value="oneway">One-way</option><option value="hedge">Hedge</option></TxSelect></Row>}
      </TxParamGroup>}
      <TxParamGroup defaultOpen={false} title="Costs" summary={feeSource} changed={mark("costs.feePct", "costs.makerPct", "costs.commissionBps", "costs.halfSpreadBps", "costs.impact", "costs.volumeCapPct", "costs.funding", "costs.borrowPct")}>
        {kind === "stock"
          ? <Row id="commission" label="Commission" changed={changed.has("costs.commissionBps")} onRevert={() => restoreRun("costs.commissionBps")}><NumberBox label="Commission" value={run.costs.commissionBps} unit="bps" allowEmpty onValue={next => set("costs", "commissionBps", next)} /></Row>
          : <><Row id="feePct" label="Taker fee" changed={changed.has("costs.feePct")} onRevert={() => restoreRun("costs.feePct")}><NumberBox label="Taker fee" value={run.costs.feePct} unit="%" allowEmpty onValue={next => set("costs", "feePct", next)} /></Row>
            <Row id="makerPct" label="Maker fee" changed={changed.has("costs.makerPct")} onRevert={() => restoreRun("costs.makerPct")}><NumberBox label="Maker fee" value={run.costs.makerPct} unit="%" allowEmpty onValue={next => set("costs", "makerPct", next)} /></Row></>}
        <Row id="halfSpread" label="Half spread" changed={changed.has("costs.halfSpreadBps")} onRevert={() => restoreRun("costs.halfSpreadBps")}><NumberBox label="Half spread" value={run.costs.halfSpreadBps} unit="bps" onValue={next => next !== null && set("costs", "halfSpreadBps", next)} /></Row>
        <Row id="impact" label="Impact" changed={changed.has("costs.impact")} onRevert={() => restoreRun("costs.impact")}><NumberBox label="Impact" value={run.costs.impact} onValue={next => next !== null && set("costs", "impact", next)} /></Row>
        <Row id="volumeCap" label="Volume cap" changed={changed.has("costs.volumeCapPct")} onRevert={() => restoreRun("costs.volumeCapPct")}><NumberBox label="Volume cap" value={run.costs.volumeCapPct} unit="% of volume" allowEmpty onValue={next => set("costs", "volumeCapPct", next)} /></Row>
        {kind === "stock"
          ? <Row id="borrow" label="Borrow" changed={changed.has("costs.borrowPct")} onRevert={() => restoreRun("costs.borrowPct")}><NumberBox label="Borrow" value={run.costs.borrowPct} unit="% a year" onValue={next => next !== null && set("costs", "borrowPct", next)} /></Row>
          : run.costs.funding !== undefined && <Check label="Funding" checked={run.costs.funding} changed={changed.has("costs.funding")} onRevert={() => restoreRun("costs.funding")} onChange={next => set("costs", "funding", next)} />}
      </TxParamGroup>
      <TxParamGroup defaultOpen={false} title="Execution" summary={run.fill === "nextOpen" ? "next open" : "next close"} changed={mark("fill", "shorting", "limitThroughTicks", "halted")}>
        <Row id="fill" label="Fill at" changed={changed.has("fill")} onRevert={() => restoreRun("fill")}><TxSelect aria-label="Fill at" value={run.fill} onChange={event => onRun({fill: event.target.value})}>
          <option value="nextOpen">Next bar open</option><option value="nextClose">Next bar close</option></TxSelect></Row>
        {kind === "stock"
          ? <Row id="shorting" label="Shorting" changed={changed.has("shorting")} onRevert={() => restoreRun("shorting")}><TxSelect aria-label="Shorting" value={run.shorting} onChange={event => onRun({shorting: event.target.value})}>
            <option value="allowed">Allowed</option><option value="off">Long only</option></TxSelect></Row>
          : <Row id="shorting" label="Shorting" changed={false}><span className="qx-ro">{kind === "spot" ? "Long only" : "No borrow"}</span></Row>}
        <Row id="limit" label="Limit orders" changed={changed.has("limitThroughTicks")} onRevert={() => restoreRun("limitThroughTicks")}>
          <TxSelect aria-label="Limit orders" value={limitMode} onChange={event => onRun({limitThroughTicks: event.target.value === "touch" ? 0 : event.target.value === "one" ? 1 : Math.max(2, limit)})}>
            <option value="touch">Fill on touch</option><option value="one">Through 1 tick</option><option value="many">Through N ticks</option></TxSelect></Row>
        {limitMode === "many" && <Row id="ticks" label="Ticks" changed={changed.has("limitThroughTicks")} onRevert={() => restoreRun("limitThroughTicks")}><NumberBox label="Ticks" value={limit} unit="ticks" onValue={next => next !== null && onRun({limitThroughTicks: Math.max(0, Math.round(next))})} /></Row>}
        {kind === "stock" && <Row id="halted" label="Halted names" changed={changed.has("halted")} onRevert={() => restoreRun("halted")}><TxSelect aria-label="Halted names" value={run.halted ?? "keep"} onChange={event => onRun({halted: event.target.value})}>
          <option value="keep">Keep position</option><option value="close">Close at next price</option></TxSelect></Row>}
      </TxParamGroup>
      <TxParamGroup defaultOpen={false} title="Measure against" summary={BENCHMARKS[run.benchmark.kind]?.label(kind)} changed={mark("benchmark.kind", "riskFree.basis", "riskFree.pct")}>
        <Row id="benchmark" label="Benchmark" changed={changed.has("benchmark.kind")} onRevert={() => restoreRun("benchmark.kind")}>
          <TxSelect aria-label="Benchmark" title={BENCHMARKS[run.benchmark.kind]?.title} value={run.benchmark.kind} onChange={event => onRun({benchmark: {kind: event.target.value}})}>
            {view.applicable.benchmark.map(item => <option key={item} value={item} title={BENCHMARKS[item]?.title}>{item === "hold" ? "Buy and hold" : BENCHMARKS[item]?.label(kind) ?? item}</option>)}</TxSelect></Row>
        <Row id="riskFree" label="Risk-free" changed={changed.has("riskFree.basis")} onRevert={() => restoreRun("riskFree.basis")}>
          <span className="qx-lev"><TxSelect aria-label="Risk-free" value={run.riskFree.basis} onChange={event => onRun({riskFree: event.target.value === "fixed" ? {basis: "fixed", pct: run.riskFree.pct ?? 0} : {basis: event.target.value}})}>
            {RISK_FREE.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</TxSelect>
            {riskFreeMean != null && <span className="fx-t3">avg {riskFreeMean.toFixed(2)}%</span>}</span></Row>
        {run.riskFree.basis === "fixed" && <Row id="riskFreePct" label="Rate" changed={changed.has("riskFree.pct")} onRevert={() => restoreRun("riskFree.pct")}>
          <NumberBox label="Risk-free rate" value={run.riskFree.pct ?? 0} unit="% a year" onValue={next => next !== null && onRun({riskFree: {basis: "fixed", pct: next}})} /></Row>}
      </TxParamGroup>
      <TxParamGroup defaultOpen={false} title="Held out" summary={run.heldOutFrom ? `from ${mdy(Date.parse(run.heldOutFrom + "T00:00:00Z"))}` : "none"}>
        <Row id="heldOut" label="From" changed={changed.has("heldOutFrom")} onRevert={() => restoreRun("heldOutFrom")}><span className="sv-rangebox" data-error={heldMissing && !run.heldOutFrom ? "" : undefined} ref={node => { if (heldMissing && !run.heldOutFrom) node?.scrollIntoView?.({block: "nearest"}); }}>
          <Held value={run.heldOutFrom} onChange={day => onRun({heldOutFrom: day})} />{heldMissing && !run.heldOutFrom && <span className="sv-rangeerror" role="alert">Set where the held-out part starts</span>}</span></Row>
      </TxParamGroup>
    </>}
    {inputs.length > 0 && <TxLinkedInputs items={inputs.map(item => ({
      name: item.kind === "run" ? `${item.name} #${item.pinned.version}` : item.name, version: item.kind === "run" ? (signalRun?.kind === "factor" ? "values" : "predictions") : item.retired ? `v${item.pinned.version ?? "?"} retired` : `v${item.pinned.version ?? "?"}`,
      kind: item.kind === "run" ? (signalRun?.kind === "factor" ? "Factor run, signal values" : "Model run, test-window predictions") : item.kind === "model" ? "Model" : "Factor", pinned: item.kind === "run" ? undefined : item.pickedOn ? `picked on data to ${mdy(Date.parse(item.pickedOn + "T00:00:00Z"))}` : item.pinned.trainedTo ? `trained to ${mdy(Date.parse(item.pinned.trainedTo + "T00:00:00Z"))}` : undefined,
      warn: item.pinned.trainedTo && !item.reads && since && since < item.pinned.trainedTo ? "Backtest starts before this" : undefined,
      where: item.file && item.line ? `${item.file}:${item.line}` : undefined, newer: item.newer ? `v${item.newer.version}` : undefined,
      onOpen: item.kind === "run" ? undefined : () => item.fromRun && onOpenRun(item.fromRun), onSwitch: () => item.newer && onSwitch(item.name, item.newer.versionId),
    }))} />}
  </TxParams>;
}
