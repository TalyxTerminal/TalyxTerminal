/* The settings column of a factor: Code settings (declared in its Python files) above the platform's Run settings. */
import { t } from "../i18n";
import { type ReactNode } from "react";
import { TxButton, TxCheckbox, TxIcon, TxInput, TxParamGroup, TxParamLayer, TxParamRow, TxParams, TxSelect } from "@talyx/sdk";
import type { LibraryItemSummary, Parameter, ParameterError, ParamValue } from "../objects";
import type { FactorRun, FactorRunSettings } from "./api";
import { listOf, paramValue, runNumber, shown, withRunNumber, type Draft } from "./draft";
import { dayText } from "./format";
import { DayInput } from "../shared/DayInput";

// Short enough for the settings column's dropdown at 1440 px ("Z-score each c…", "Below and abov…"); Results › Settings writes them in full.
const EXTREMES = [["mad3", t("Clip at 3 MAD")], ["pct1", t("Clip 1% / 99%")], ["none", t("None")]] as const;
const SCALES = [["zscore", t("Z-score daily")], ["rank", t("Rank daily")], ["none", t("None")]] as const;
const NEUTRAL = [["industry+size", t("Industry, size")], ["industry", t("Industry")], ["none", t("None")]] as const;
const SPLITS = [["equal", t("Equal counts")], ["cuts", t("Fixed cuts")], ["sign", t("By sign")]] as const;

/** A number field that also takes a list ("1, 3, 5, 10"): a list makes the run a sweep, shown as ×4 in place of the unit. */
function NumberField({id, value, text, unit, label, onText}: {id: string; value: number; text: string | undefined; unit: string; label: string; onText(text: string): void}) {
  const many = listOf(text);
  return <TxInput numeric aria-label={label} data-k={id} value={text ?? String(value)} suffix={many ? `×${many.length}` : unit}
    onChange={event => onText(event.target.value)} />;
}

function Row({id, label, hint, changed, children, onRevert}: {id: string; label: string; hint?: string; changed: boolean; onRevert?: () => void; children: ReactNode}) {
  return <div id={`fx-row-${id}`} className="fx-rowwrap"><TxParamRow label={label} hint={hint} changed={changed} onRevert={onRevert}>{children}</TxParamRow></div>;
}

function Check({label, checked, changed, onChange, onRevert}: {label: string; checked: boolean; changed: boolean; onRevert(): void; onChange(value: boolean): void}) {
  return <div className="fx-chk" data-changed={changed ? "" : undefined}><TxParamRow wide changed={changed} onRevert={onRevert}><TxCheckbox label={label} checked={checked} onCheckedChange={onChange} /></TxParamRow></div>;
}

export function SettingsColumn({mode, parameters, errors, draft, changed, library, heldOutFrom, industries, neutralizeDefault, base, onRevert, onDraft, onHeldOut, onLine}: {
  mode: "formula" | "code"; parameters: Parameter[]; errors: ParameterError[]; draft: Draft; changed: Set<string>; library: LibraryItemSummary[] | null;
  heldOutFrom: string | null | undefined;
  /** Whether Market gives these names an industry (null until the backend says): without one the settings that need it do not appear,
   * unless one is already set (the run then stops naming it). Neutralize left out takes `neutralizeDefault`. */
  industries: boolean | null; neutralizeDefault: "industry+size" | "none" | null;
  /** The run the settings are compared with, and putting its settings back. */
  base: FactorRun | null; onRevert(): void;
  onDraft(next: Draft): void; onHeldOut(day: string | null): void; onLine(file: string | undefined, line: number): void;
}) {
  const run = draft.run;
  const set = (next: Partial<FactorRunSettings>) => onDraft({...draft, run: {...run, ...next}});
  /** A typed number: one value sets the setting at once; a list stays as typed until it runs as a sweep. */
  const typed = (key: string, text: string) => {
    const texts = {...draft.text, [key]: text};
    const single = Number(text.trim());
    if (!text.includes(",") && text.trim() !== "" && Number.isFinite(single)) {
      if (key.startsWith("param:")) onDraft({...draft, text: texts, params: {...draft.params, [key.slice(6)]: single}});
      else onDraft({...draft, text: texts, run: withRunNumber(run, key, single)});
    } else onDraft({...draft, text: texts});
  };

  const revert = (key: string) => {
    if (!base) return;
    const text = {...draft.text};
    delete text[key];
    if (key.startsWith("param:")) {
      const name = key.slice(6), params = {...draft.params};
      if (base.settings.params && name in base.settings.params) params[name] = base.settings.params[name];
      else delete params[name];
      onDraft({...draft, params, text});
    } else {
      const [group, field] = (key === "groups" ? "grouping.count" : key).split(".");
      const before = base.settings.run;
      const next = group === "grouping" ? {...run, grouping: {...run.grouping, [field]: before.grouping[field as keyof typeof before.grouping]}}
        : group === "leaveOut" ? {...run, leaveOut: {...run.leaveOut, [field]: before.leaveOut[field as keyof typeof before.leaveOut]}}
        : {...run, [group]: before[group as keyof FactorRunSettings]};
      onDraft({...draft, run: next, text});
    }
  };
  const visible = parameters.filter(item => shown(item, draft.params, parameters));
  const groups = new Map<string, Parameter[]>();
  for (const item of visible) groups.set(item.group ?? "", [...(groups.get(item.group ?? "") ?? []), item]);
  const field = (item: Parameter): ReactNode => {
    const value = paramValue(item, draft.params);
    const setParam = (next: ParamValue) => onDraft({...draft, params: {...draft.params, [item.name]: next}});
    if (item.type === "int" || item.type === "float") return <NumberField id={`param:${item.name}`} label={item.label} value={Number(value)} text={draft.text[`param:${item.name}`]} unit="" onText={text => typed(`param:${item.name}`, text)} />;
    if (item.type === "bool") return <TxCheckbox label={item.label === item.name ? t("On") : item.label} checked={Boolean(value)} onCheckedChange={setParam} />;
    if (item.type === "choice") return <TxSelect aria-label={item.label} value={JSON.stringify(value)} onChange={event => setParam(JSON.parse(event.target.value))}>
      {(item.options ?? []).map(option => <option key={JSON.stringify(option.value)} value={JSON.stringify(option.value)}>{option.label}</option>)}</TxSelect>;
    if (item.type === "library" || item.type === "pick") {
      const items = (library ?? []).filter(entry => item.type === "library" || (item.kinds ?? ["factor"]).includes(entry.kind));
      const current = item.type === "library" ? String(value ?? "") : (value as unknown as {name?: string} | null)?.name ?? "";
      return <TxSelect aria-label={item.label} value={current} onChange={event => {
        const chosen = items.find(entry => entry.name === event.target.value);
        setParam((item.type === "library" ? event.target.value : chosen ? {name: chosen.name, version: chosen.latest.version} : null) as ParamValue);
      }}><option value="">{t("None")}</option>{items.map(entry => <option key={entry.itemId} value={entry.name}>{entry.name}</option>)}</TxSelect>;
    }
    if (item.type === "table") {
      const rows = (Array.isArray(value) ? value : []) as unknown as Record<string, ParamValue>[];
      const columns = item.columns ?? Object.keys(rows[0] ?? {});
      return <table className="tx-params-mini"><thead><tr>{columns.map(column => <th key={column}>{column}</th>)}</tr></thead>
        <tbody>{rows.map((row, r) => <tr key={r}>{columns.map((column, c) => <td key={column}>{c === 0 && typeof row[column] === "string" ? String(row[column])
          : <TxInput numeric={typeof row[column] === "number"} aria-label={`${item.label} ${column} ${r + 1}`} value={String(row[column] ?? "")}
            onChange={event => {const text = event.target.value, number = Number(text); const next = rows.map((other, index) => index === r ? {...other, [column]: typeof row[column] === "number" && Number.isFinite(number) ? number : text} : other); setParam(next as unknown as ParamValue);}} />}</td>)}</tr>)}</tbody></table>;
    }
    return <TxInput aria-label={item.label} value={String(value ?? "")} onChange={event => setParam(event.target.value)} />;
  };
  const codeRow = (item: Parameter) => {
    const hint = [`q.param("${item.name}")${item.file ? t(", {file} line {line}", { file: item.file, line: item.line }) : ""}`, item.label !== item.name ? item.label : "", item.help ?? ""].filter(Boolean).join(": ");
    return <Row key={item.name} id={`param:${item.name}`} label={item.name} hint={hint} changed={changed.has(`param:${item.name}`)} onRevert={() => revert(`param:${item.name}`)}>{field(item)}</Row>;
  };
  const changes = (keys: string[]) => keys.filter(key => changed.has(key)).length;
  const leaves = [run.leaveOut.halted && t("halted"), run.leaveOut.newListings && t("new"), run.leaveOut.illiquid && t("illiquid")].filter(Boolean).join(", ");
  const neutral = run.neutralize ?? neutralizeDefault;
  const neutralRow = industries !== false || (neutral !== null && neutral !== "none");
  const prep = [run.extremes === "mad3" ? "3 MAD" : run.extremes === "pct1" ? "1% / 99%" : t("no clip"), run.scale === "zscore" ? "z-score" : run.scale === "rank" ? t("rank") : t("no scale"),
    neutralRow ? NEUTRAL.find(([value]) => value === neutral)?.[1].toLowerCase() : undefined].filter(Boolean).join(", ");
  return <TxParams footer={changed.size && base ? <><span className="fx-dot" data-tone="now" />{t("{size} changed since #{number}", { size: changed.size, number: base.number })}<span className="tx-spacer" /><TxButton variant="quiet" size="sm" onClick={onRevert}>{t("Revert")}</TxButton></> : undefined}>
    {mode === "code" && (parameters.length > 0 || errors.length > 0) && <>
      <TxParamLayer title={t("Code settings")} source={parameters[0]?.file ?? "factor.py"} changed={changes([...changed].filter(key => key.startsWith("param:")))} />
      {errors.map((error, index) => <button key={index} type="button" className="fx-paramerror" onClick={() => error.line && onLine(error.file, error.line)}><TxIcon name="alert" /><span>{error.message}</span></button>)}
      {[...groups.entries()].map(([group, items]) => group
        ? <TxParamGroup key={group} title={group} changed={items.some(item => changed.has(`param:${item.name}`))}>{items.map(codeRow)}</TxParamGroup>
        : <div key="" className="tx-params-group">{items.map(codeRow)}</div>)}
    </>}
    <TxParamLayer title={t("Run settings")} changed={changes([...changed].filter(key => !key.startsWith("param:")))} />
    <TxParamGroup defaultOpen={false} title={t("Universe")} summary={leaves || t("nothing left out")} changed={["leaveOut.halted", "leaveOut.newListings", "leaveOut.illiquid"].some(key => changed.has(key))}>
      <Check label={t("Leave out halted names")} checked={run.leaveOut.halted} changed={changed.has("leaveOut.halted")} onRevert={() => revert("leaveOut.halted")} onChange={halted => set({leaveOut: {...run.leaveOut, halted}})} />
      <Check label={t("Leave out names listed under 60 days")} checked={run.leaveOut.newListings} changed={changed.has("leaveOut.newListings")} onRevert={() => revert("leaveOut.newListings")} onChange={newListings => set({leaveOut: {...run.leaveOut, newListings}})} />
      <Check label={t("Leave out lowest 20% by dollar volume")} checked={run.leaveOut.illiquid} changed={changed.has("leaveOut.illiquid")} onRevert={() => revert("leaveOut.illiquid")} onChange={illiquid => set({leaveOut: {...run.leaveOut, illiquid}})} />
    </TxParamGroup>
    <TxParamGroup defaultOpen title={t("Test")} summary={t("{holding} days, {value}", { holding: run.holding, value: run.grouping.method === "equal" ? t("{count} equal", { count: runNumber(run, "groups") }) : run.grouping.method === "cuts" ? t("cut at {cuts}", { cuts: run.grouping.cuts }) : t("positive and negative") })} changed={["holding", "groups", "delay", "grouping.method", "grouping.cuts", "grouping.withinIndustry"].some(key => changed.has(key))}>
      <Row id="holding" label={t("Holding")} changed={changed.has("holding")} onRevert={() => revert("holding")}>
        <NumberField id="holding" label={t("Holding")} value={run.holding} text={draft.text.holding} unit={t("days")} onText={text => typed("holding", text)} /></Row>
      {run.grouping.method === "equal" && <Row id="groups" label={t("Groups")} changed={changed.has("groups")} onRevert={() => revert("groups")}>
        <NumberField id="groups" label={t("Groups")} value={runNumber(run, "groups")} text={draft.text.groups} unit={t("equal weight")} onText={text => typed("groups", text)} /></Row>}
      <Row id="split" label={t("Split")} changed={changed.has("grouping.method")} onRevert={() => revert("grouping.method")}>
        <TxSelect aria-label={t("Split")} value={run.grouping.method} onChange={event => set({grouping: {...run.grouping, method: event.target.value as FactorRunSettings["grouping"]["method"]}})}>
          {SPLITS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</TxSelect></Row>
      {run.grouping.method === "cuts" && <Row id="cuts" label={t("Cuts")} hint={t("Cut points on the preprocessed value")} changed={changed.has("grouping.cuts")} onRevert={() => revert("grouping.cuts")}>
        <TxInput aria-label={t("Cuts")} value={run.grouping.cuts} onChange={event => set({grouping: {...run.grouping, cuts: event.target.value}})} /></Row>}
      {(industries !== false || run.grouping.withinIndustry) && <Check label={t("Group within each industry")} checked={run.grouping.withinIndustry} changed={changed.has("grouping.withinIndustry")} onRevert={() => revert("grouping.withinIndustry")}
        onChange={withinIndustry => set({grouping: {...run.grouping, withinIndustry}})} />}
      <Row id="demeaned" label={t("Returns")} changed={changed.has("demeaned")} onRevert={() => revert("demeaned")}>
        <TxSelect aria-label={t("Returns")} value={run.demeaned ? "demeaned" : "raw"} onChange={event => set({demeaned: event.target.value === "demeaned"})}>
          <option value="demeaned">{t("Demeaned")}</option><option value="raw">{t("Raw")}</option></TxSelect></Row>
      {(industries !== false || run.groupAdjust) && <Check label={t("Group neutral")} checked={run.groupAdjust} changed={changed.has("groupAdjust")} onRevert={() => revert("groupAdjust")} onChange={groupAdjust => set({groupAdjust})} />}
      <Row id="delay" label={t("Delay")} hint={t("Values use data up to the close before the return starts")} changed={changed.has("delay")} onRevert={() => revert("delay")}>
        <TxSelect aria-label={t("Delay")} value={String(run.delay)} onChange={event => onDraft({...draft, text: {...draft.text, delay: event.target.value}, run: {...run, delay: Number(event.target.value)}})}>
          <option value="1">{t("1 day")}</option><option value="0">{t("0 days")}</option></TxSelect></Row>
    </TxParamGroup>
    <TxParamGroup defaultOpen title={t("Preprocessing")} summary={prep} changed={["extremes", "scale", "neutralize"].some(key => changed.has(key))}>
      <Row id="extremes" label={t("Extremes")} changed={changed.has("extremes")} onRevert={() => revert("extremes")}><TxSelect aria-label={t("Extremes")} value={run.extremes} onChange={event => set({extremes: event.target.value as FactorRunSettings["extremes"]})}>
        {EXTREMES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</TxSelect></Row>
      <Row id="scale" label={t("Scale")} changed={changed.has("scale")} onRevert={() => revert("scale")}><TxSelect aria-label={t("Scale")} value={run.scale} onChange={event => set({scale: event.target.value as FactorRunSettings["scale"]})}>
        {SCALES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</TxSelect></Row>
      {neutralRow && <Row id="neutralize" label={t("Neutralize")} hint={t("Residual after industry dummies and log market value, each day")} changed={changed.has("neutralize")} onRevert={() => revert("neutralize")}>
        <TxSelect aria-label={t("Neutralize")} value={neutral ?? ""} onChange={event => set({neutralize: event.target.value as NonNullable<FactorRunSettings["neutralize"]>})}>
          {neutral === null && <option value="" disabled />}
          {NEUTRAL.filter(([value]) => industries !== false || value === "none" || value === neutral).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</TxSelect></Row>}
    </TxParamGroup>
    <TxParamGroup defaultOpen title={t("Cost")} summary={t("{costBps} bps, borrow {borrowPct}%", { costBps: run.costBps, borrowPct: run.borrowPct })} changed={["costBps", "borrowPct"].some(key => changed.has(key))}>
      <Row id="costBps" label={t("A trade")} hint={t("Per trade, each leg")} changed={changed.has("costBps")} onRevert={() => revert("costBps")}>
        <NumberField id="costBps" label={t("A trade")} value={run.costBps} text={draft.text.costBps} unit={t("bps")} onText={text => typed("costBps", text)} /></Row>
      <Row id="borrowPct" label={t("Borrow")} hint={t("Borrow fee on the short leg, a year")} changed={changed.has("borrowPct")} onRevert={() => revert("borrowPct")}>
        <TxInput numeric aria-label={t("Borrow")} value={draft.text.borrowPct ?? String(run.borrowPct)} suffix={t("% a year")}
          onChange={event => {const text = event.target.value, value = Number(text); onDraft({...draft, text: {...draft.text, borrowPct: text}, run: Number.isFinite(value) && text.trim() ? {...run, borrowPct: value} : run});}} /></Row>
    </TxParamGroup>
    <TxParamGroup defaultOpen={false} title={t("Held out")} summary={heldOutFrom ? t("from {heldOutFrom}", { heldOutFrom: dayText(heldOutFrom) }) : t("none")}>
      <Row id="heldOut" label={t("From")} changed={false}>
        <span className="fx-held"><TxIcon name="lock" /><DayInput label={t("Held out from")} value={heldOutFrom ?? null} empty onChange={onHeldOut} /></span></Row>
    </TxParamGroup>
  </TxParams>;
}
