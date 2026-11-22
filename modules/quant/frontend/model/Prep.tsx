/* A model's preparation: the standard pipeline's settings in their groups (Data, Inputs, Processing, Label, Split, Algorithm), then the run settings and the batch.
   Generated from the pipeline's declarations; the groups with their own layout are the inputs table, the label, the algorithm and the batch. */
import { t } from "../i18n";
import { Fragment, useState, type ReactNode } from "react";
import { TxButton, TxCheckbox, TxIcon, TxInput, TxParamGroup, TxParamRow, TxPopover, TxSegmented, TxSelect, TxTable, TxTag, TxTd, TxTh } from "@talyx/sdk";
import type { Parameter, ParamValue } from "../objects";
import type { Algorithm, CheckAnswer, DeepEnvironment as Deep, ModelPrep, Offer, Vary } from "./api";
import { DeepEnvironment } from "./DeepEnvironment";
import { DayInput, dayShown } from "../shared/DayInput";
import { dayYear } from "./format";
import { CheckBand } from "./CheckBand";
import { FormulaBox } from "../factor/FormulaBox";
import type { FactorFields, FormulaError } from "../factor/api";
import { Plot, type PlotFigure } from "../chart/Plot";

export interface Inspected {name: string; input: string; features: string[]; stats: {label: string; before: string; after: string}[]; note: string; figures: Record<string, PlotFigure>}
export type Width = "narrow" | "wide" | "full";
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
/** A setting shows when the settings it depends on hold one of the values it names. */
export function visible(item: Parameter, values: Record<string, ParamValue>, all: Parameter[]): boolean {
  if (!item.showIf) return true;
  return Object.entries(item.showIf).every(([name, want]) => {
    const have = values[name] ?? all.find(other => other.name === name)?.default;
    return Array.isArray(want) ? want.some(entry => same(entry, have)) : same(want, have);
  });
}
const quick = (days: number, form: string) => {
  const core = `Ref($open, -${days + 1}) / Ref($open, -1) - 1`;
  return form === "rank" ? `CSRank(${core})` : form === "demean" ? `CSDemean(${core})` : core;
};
const DAYS = [1, 5, 10, 20], FORMS = [["raw", t.c("label", "Raw")], ["demean", t("Less the mean")], ["rank", t("Rank")]] as const;
const SOURCES: Record<string, string> = {library: t("Library factor"), value: "Library value", run: t("Factor run"), builtin: t("Built-in set"), market: t("Market series"), code: t("Own code")};

export interface PrepProps {
  prep: ModelPrep; values: Record<string, ParamValue>; base: Record<string, ParamValue> | null; baseNumber: number | null; width: Width; check: CheckAnswer | null; checking: boolean;
  vary: Vary[]; onVary(next: Vary[]): void; onChange(name: string, value: ParamValue): void; onRevert(name: string, value: ParamValue): void; baseRun: ModelPrep["run"] | null; onRun(next: {cost?: number; topN?: number}): void; onHeldOut(day: string | null): void;
  dataBasics?: ReactNode; deep?: Deep | null; onInstallDeep?(variant?: string): void; onCancelDeep?(): void;
  onReset(): void; onWidth(next: Width): void; onWriteCode(): void; onGo(group: string): void; fields: FactorFields | null; inspect(input: string, feature?: string): Promise<Inspected>;
}

/** "Early stop on" follows the network's own first choice (ADD: IC, ADARNN: loss) until the person has picked one; returns the new value, or undefined to leave it. */
export function nextEarlyStop(current: string | undefined, was: {earlyStopOn?: {id: string}[]} | undefined, now: {earlyStopOn?: {id: string}[]} | undefined): string | undefined {
  const next = now?.earlyStopOn?.[0]?.id;
  return next && (current ?? was?.earlyStopOn?.[0]?.id ?? "ic") === (was?.earlyStopOn?.[0]?.id ?? "ic") ? next : undefined;
}

type BaseRun = NonNullable<ModelPrep["baseRuns"]>[number];
/** The finished LSTM or GRU runs GATs can start from: the same inputs, look back, width and layers as the settings here. */
export function matchingBases(runs: BaseRun[] | undefined, values: Record<string, ParamValue>): BaseRun[] {
  const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
  return (runs ?? []).filter(run => same(run.inputs, values.inputs) && Number(run.lookback) === Number(values.nn_lookback ?? 20) && Number(run.width) === Number(values.nn_width ?? 64)
    && Number(run.layers) === Number(values.nn_layers ?? 2));
}

/** Alpha360 alone already is 5 fields by 60 days, so the network reads it as it is and the look back is not a choice. */
export function alpha360Only(inputs: unknown): boolean {
  const rows = (Array.isArray(inputs) ? inputs : []) as {source?: string; ref?: string}[];
  return rows.length > 0 && rows.every(row => row.source === "builtin" && row.ref === "alpha360");
}

/** TCTS's auxiliary labels: one formula in each line of the setting. None means the label's own return one and two bars further out. */
export function AuxLabels({value, onChange}: {value: string; onChange(next: string): void}) {
  const [blank, setBlank] = useState(false);
  const rows = value.split("\n");
  const write = (next: string[]) => {
    onChange(next.join("\n"));
    if (!next.some(row => row.trim())) setBlank(true);
  };
  const filled = rows.some(row => row.trim()) || blank;
  return <div className="mx-label" data-testid="aux-labels">
    {filled ? rows.map((row, index) => <div key={index} className="mx-auxrow">
      <TxInput aria-label={t("Auxiliary label {index}", { index: index + 1 })} value={row} placeholder={t("e.g. Ref($open, -8) / Ref($open, -1) - 1")} onChange={event => write(rows.map((other, at) => at === index ? event.target.value.replace(/\n/g, " ") : other))} />
      <TxButton size="sm" variant="quiet" aria-label={t("Remove auxiliary label {index}", { index: index + 1 })} onClick={() => { const left = rows.filter((_other, at) => at !== index); if (!left.some(row => row.trim())) setBlank(false); onChange(left.join("\n")); }}>{t("Remove")}</TxButton></div>)
      : <span className="mx-t3">{t("next 1 and 2 bars further out")}</span>}
    <TxButton size="sm" variant="quiet" onClick={() => { if (!filled) setBlank(true); else write([...rows, ""]); }}>{t("Add label")}</TxButton></div>;
}

export function Prep(props: PrepProps) {
  const {prep, values, base, baseNumber, width, check} = props;
  const all = prep.groups.flatMap(group => group.settings);
  const changed = (name: string) => base !== null && !same(values[name] ?? all.find(item => item.name === name)?.default, base[name] ?? all.find(item => item.name === name)?.default);
  const changes = all.filter(item => changed(item.name)).length;
  const algorithm = prep.algorithms.find(item => item.id === values.algorithm);
  const label = (item: Parameter) => item.label || item.name;
  const set = (item: Parameter) => (value: ParamValue) => props.onChange(item.name, value);

  const control = (item: Parameter): ReactNode => {
    const value = (item.name in values ? values[item.name] : item.default) as ParamValue;
    if (item.name === "inputs") return <InputsTable inspect={props.inspect} rows={(value ?? []) as unknown as Offer[]} offers={prep.offers} check={check} onChange={rows => props.onChange("inputs", rows as unknown as ParamValue)} />;
    if (item.name === "label") return <LabelBox value={String(value)} error={check?.label?.error ?? null} reach={check?.label?.reach ?? null} fields={props.fields} onChange={set(item)} />;
    if (item.name === "weight_formula") {
      const problem = check?.checks?.find(entry => entry.name === "Sample weight" && entry.state === "fix");
      return <div className="mx-label"><FormulaBox value={String(value ?? "")} error={null} fields={props.fields} onChange={set(item)} onRun={() => undefined} label={t("Weight formula")} placeholder={t("Weight, e.g. $volume * $close")} />
        {problem && <div className="mx-labelnote" data-error="" role="alert">{problem.value}</div>}</div>;
    }
    if (item.name === "objective") {
      const options = (algorithm ?? prep.algorithms.find(entry => entry.id === "lightgbm"))?.objectives ?? [];
      return <TxSelect aria-label={t("Objective")} value={String(value)} onChange={event => set(item)(event.target.value)}>{options.map(option => <option key={option.id} value={option.id}>{t(option.label)}</option>)}</TxSelect>;
    }
    if (item.name === "objective_param") {
      const objective = algorithm?.objectives?.find(option => option.id === values.objective);
      const number = objective?.param;
      return number ? <TxInput numeric aria-label={number.name} suffix={number.name} value={String(value)} onChange={event => set(item)(Number(event.target.value))} /> : null;
    }
    if (item.name === "nn_base") {
      const options = matchingBases(prep.baseRuns, values);
      return <TxSelect aria-label={t("Start from")} value={String(value ?? "")} onChange={event => set(item)(event.target.value)}><option value="">{t("none")}</option>
        {options.map(option => <option key={option.id} value={option.id}>{t(option.label)}</option>)}</TxSelect>;
    }
    if (item.name === "label_aux") return <AuxLabels value={String(value ?? "")} onChange={set(item)} />;
    if (item.name === "nn_concepts") {
      const options = prep.concepts ?? [];
      return options.length ? <TxSelect aria-label={t("Concepts")} value={String(value ?? "")} onChange={event => set(item)(event.target.value)}><option value="">{t("none")}</option>
        {options.map(option => <option key={option.id} value={option.id}>{t(option.label)}</option>)}</TxSelect> : <span className="mx-t3">{t("none")}</span>;
    }
    if (item.name === "nn_lookback" && alpha360Only(values.inputs)) return <span className="mx-t3">{t("60 days (Alpha360)")}</span>;
    if (item.name === "nn_device" && props.deep?.installed) {
      const options = props.deep.devices;
      return options.length > 1 ? <TxSelect aria-label={t("Device")} value={String(value || options[0].id)} onChange={event => set(item)(event.target.value)}>{options.map(option => <option key={option.id} value={option.id}>{t(option.label)}</option>)}</TxSelect>
        : <span className="mx-t3">{options[0]?.label}</span>;
    }
    if (item.name === "algorithm") return <AlgorithmSelect algorithms={prep.algorithms} value={String(value)} onChange={set(item)} />;
    if (item.type === "bool") return <TxCheckbox label={label(item)} checked={Boolean(value)} onCheckedChange={set(item)} />;
    if (item.type === "int" || item.type === "float") return <TxInput numeric aria-label={label(item)} value={String(value)} onChange={event => {const next = Number(event.target.value); if (Number.isFinite(next)) set(item)(next);}} />;
    if (item.type === "choice") return <TxSelect aria-label={label(item)} value={JSON.stringify(value)} onChange={event => set(item)(JSON.parse(event.target.value))}>
      {(item.options ?? []).map(option => <option key={JSON.stringify(option.value)} value={JSON.stringify(option.value)}>{prep.ownCode ? option.label : t(option.label)}</option>)}</TxSelect>;
    // The first test day is a day in the app's writing (as Held out from), empty for after the first training and validation.
    if (item.name === "first_test") return <DayInput label={label(item)} value={value ? String(value) : null} empty onChange={day => set(item)(day ?? "")} />;
    return <TxInput aria-label={label(item)} value={String(value ?? "")} onChange={event => set(item)(event.target.value)} />;
  };
  const row = (item: Parameter) => {
    if (item.name === "weight" && algorithm && !(algorithm.objectives ?? []).find(option => option.id === values.objective)?.weights) return null;
    if (item.name === "objective" && !algorithm) return null;
    if (item.name === "weight_formula" && values.weight !== "formula") return null;
    const wide = item.name === "inputs" || item.name === "label" || item.name === "weight_formula" || item.type === "bool";
    const name = item.name === "objective_param" ? "" : label(item);
    const failed = item.name === "engine_params" ? check?.engineParams?.errors?.[0] : undefined;
    const shownLabel = item.type === "bool" && !wide ? "" : name;
    return <div key={item.name} className="mx-row" id={`mx-row-${item.name}`}><TxParamRow label={shownLabel} hint={item.help ?? undefined} changed={changed(item.name)} onRevert={() => props.onRevert(item.name, base?.[item.name] ?? item.default as ParamValue)} wide={wide}>{control(item)}</TxParamRow>
      {failed && <div className="mx-rowerror" role="alert">{failed.message}</div>}</div>;
  };
  const group = (name: string, body?: ReactNode) => {
    const items = prep.groups.find(entry => entry.name === name)?.settings.filter(item => visible(item, values, all)) ?? [];
    const settingsChanged = items.some(item => changed(item.name));
    return <TxParamGroup key={`${name}-${width}`} title={name === "Processing" ? t("Processing") : t(name)} summary={summaryOf(name)} changed={settingsChanged} defaultOpen={width !== "narrow"}>
      {body ?? items.map(row)}</TxParamGroup>;
  };
  /** Inputs and the label side by side, as the draft lays them out: a row for missing, extremes and scale, a column each. */
  const processingBody = () => {
    const find = (name: string) => all.find(item => item.name === name);
    const pairs: [string, string, string][] = [[t("Missing"), "inputs_missing", "label_missing"], [t("Extremes"), "inputs_extremes", "label_extremes"], [t("Scale"), "inputs_scale", "label_scale"]];
    const rest = ["take_out_industry", "take_out_size"].map(find).filter((item): item is Parameter => Boolean(item));
    return <>
      <div className="mx-proc"><span /><span className="mx-t3">{t("Inputs")}</span><span className="mx-t3">{t("Label")}</span>
        {pairs.map(([title, left, right]) => <Fragment key={title}><span className="mx-t3">{title}</span>{[left, right].map(name => { const item = find(name); return item ? <TxParamRow key={name} wide changed={changed(name)} onRevert={() => props.onRevert(name, base?.[name] ?? item.default as ParamValue)}>{control(item)}</TxParamRow> : <span key={name} />; })}</Fragment>)}</div>
      {rest.map(row)}</>;
  };
  const algorithmBody = () => {
    const items = prep.groups.find(entry => entry.name === "Algorithm")?.settings.filter(item => visible(item, values, all)) ?? [];
    const first = items.filter(item => item.name === "algorithm");
    const rest = items.filter(item => item.name !== "algorithm" && item.name !== "seed" && item.name !== "reproducible" && item.name !== "engine_params");
    const tail = items.filter(item => item.name === "engine_params" || item.name === "seed" || item.name === "reproducible");
    const needsEnvironment = Boolean(algorithm?.deep) && props.deep && (!props.deep.installed || props.deep.newCard);
    const hideDevice = (item: Parameter) => item.name !== "nn_device" || Boolean(props.deep?.installed);
    return <>{first.map(row)}{needsEnvironment && props.deep && <DeepEnvironment deep={props.deep} onInstall={variant => props.onInstallDeep?.(variant)} onCancel={() => props.onCancelDeep?.()} />}
      {rest.filter(hideDevice).map(row)}{algorithm && tail.map(row)}</>;
  };
  const run = prep.run;
  const heldOut = prep.data.heldOutFrom ?? "";
  const optionLabel = (name: string) => {
    const item = all.find(entry => entry.name === name);
    const value = values[name] ?? item?.default;
    return item?.options?.find(option => same(option.value, value))?.label ?? String(value ?? "");
  };
  // the model's own thresholds, written in its labels (the factor page's differ: 60 days, the lowest fifth)
  const minimum = Number(values.min_dollar ?? all.find(item => item.name === "min_dollar")?.default ?? 0);
  const dollars = minimum >= 1e6 ? `$${(minimum / 1e6).toLocaleString("en-US", {maximumFractionDigits: 1})}M` : `$${minimum.toLocaleString("en-US")}`;
  const summaryOf = (name: string): string => {
    if (name === "Data") return [[t("halted days"), values.leave_out_halted], [t("listed under 1 year"), values.leave_out_young], [t("under {amount} a day", { amount: dollars }), values.leave_out_illiquid]].filter(([, on]) => on).map(([text]) => text).join(", ") || t("nothing left out");
    if (name === "Inputs") { const rows = (values.inputs ?? []) as unknown as unknown[]; return rows.length ? (rows.length > 1 ? (check?.features ? t("{count} inputs · {features} features", { count: rows.length, features: check.features }) : t("{count} inputs", { count: rows.length })) : (check?.features ? t("{count} input · {features} features", { count: rows.length, features: check.features }) : t("{count} input", { count: rows.length }))) : t("none yet"); }
    if (name === "Processing") return `${optionLabel("inputs_missing").toLowerCase()}, ${optionLabel("inputs_extremes").toLowerCase()}, ${optionLabel("inputs_scale").toLowerCase()}`;
    if (name === "Label") return check?.label?.reach ? t("{bars} bars from {start}", { bars: check.label.reach.ahead - check.label.reach.start.bar, start: check.label.reach.start.bar === 1 ? t("the next open") : t("this close") }) : String(values.label ?? "").slice(0, 40);
    if (name === "Split") return t("{mode} {train} · validation {validation} · retrain every {interval}", { mode: optionLabel("window_mode").toLowerCase(), train: optionLabel("train_months").toLowerCase(), validation: optionLabel("valid_months"), interval: optionLabel("every_months") });
    if (name === "Algorithm") return algorithm?.label ?? t("not chosen");
    return "";
  };
  const G: Record<string, ReactNode> = {
    data: width !== "narrow" && props.dataBasics ? <TxParamGroup key={`Data-${width}`} title={t("Data")} summary={summaryOf("Data")} defaultOpen>{props.dataBasics}
      <TxParamRow label={t("Leave out")}><div className="mx-chips">{([["leave_out_halted", t("Halted days")], ["leave_out_young", t("Listed under 1 year")], ["leave_out_illiquid", t("Under {amount} a day", { amount: dollars })]] as const).map(([name, text]) =>
        <button key={name} type="button" aria-pressed={Boolean(values[name] ?? all.find(item => item.name === name)?.default)} onClick={() => props.onChange(name, !(values[name] ?? all.find(item => item.name === name)?.default))}>{text}</button>)}</div></TxParamRow></TxParamGroup>
      : <TxParamGroup key={`Data-${width}`} title={t("Data")} summary={summaryOf("Data")} defaultOpen={width !== "narrow"}>{prep.groups.find(entry => entry.name === "Data")?.settings.filter(item => visible(item, values, all)).map(row)}</TxParamGroup>,
    inputs: group("Inputs"), processing: group("Processing", processingBody()), label: group("Label", <LabelGroup items={prep.groups.find(entry => entry.name === "Label")?.settings.filter(item => visible(item, values, all)) ?? []}
      values={values} onChange={props.onChange} row={row} />),
    split: group("Split"), algorithm: group("Algorithm", algorithmBody()),
    run: <TxParamGroup key={`Run-${width}`} title={t("Run settings")} summary={(heldOut ? t("cost {cost} bps · top {topN} · held out from {heldOut}", { cost: run.cost, topN: run.topN, heldOut: dayShown(heldOut) }) : t("cost {cost} bps · top {topN}", { cost: run.cost, topN: run.topN }))} defaultOpen={width !== "narrow"}>
      <TxParamRow label={t("Held out from")}><DayInput label={t("Held out from")} value={heldOut || null} empty onChange={props.onHeldOut} /></TxParamRow>
      <TxParamRow label={t("Cost a trade")} changed={props.baseRun !== null && run.cost !== props.baseRun.cost} onRevert={() => props.baseRun && props.onRun({cost: props.baseRun.cost})}><TxInput numeric suffix={t("bps")} aria-label={t("Cost a trade")} value={String(run.cost)} onChange={event => {const next = Number(event.target.value); if (Number.isFinite(next)) props.onRun({cost: next});}} /></TxParamRow>
      <TxParamRow label={t("Top N")} changed={props.baseRun !== null && run.topN !== props.baseRun.topN} onRevert={() => props.baseRun && props.onRun({topN: props.baseRun.topN})}><TxInput numeric aria-label={t("Top N")} value={String(run.topN)} onChange={event => {const next = Number(event.target.value); if (Number.isInteger(next) && next > 0) props.onRun({topN: next});}} /></TxParamRow>
    </TxParamGroup>,
    batch: <TxParamGroup key={`Batch-${width}`} title={t("Batch")} summary={props.vary.length ? t("{vary} groups", { vary: props.vary.reduce((count, item) => count * item.values.length, 1) }) : t("one run")} defaultOpen={width !== "narrow" || props.vary.length > 0}><BatchGroup prep={prep} values={values} vary={props.vary} onVary={props.onVary} check={check} /></TxParamGroup>
  };
  const order = ["data", "inputs", "processing", "label", "split", "algorithm", "run", "batch"];
  const columns = width === "full" ? [["data", "inputs"], ["processing", "label", "split"], ["algorithm", "run", "batch"]] : width === "wide" ? [["data", "inputs", "processing", "label"], ["split", "algorithm", "run", "batch"]] : [order];
  return <aside className="mx-prep" data-width={width}>
    <div className="mx-phead">{baseNumber !== null ? <span>{t.rich("Based on {baseNumber}", { baseNumber: <b>#{baseNumber}</b> })}</span> : <b>{t("Next run")}</b>}
      {changes > 0 && <><span className="mx-chg">{t("{changes} changed", { changes })}</span><button type="button" className="mx-link" onClick={props.onReset}>{t("Reset")}</button></>}
      <span className="tx-spacer" />
      <button type="button" className="mx-icon" title={width === "full" ? t("Back to the experiments") : t("Fill the panel")} aria-label={width === "full" ? t("Back to the experiments") : t("Fill the panel")}
        onClick={() => props.onWidth(width === "full" ? "narrow" : "full")}><TxIcon name={width === "full" ? "restore" : "maximize"} /></button></div>
    <div className="mx-pscroll">
      <CheckBand check={check} checking={props.checking} width={width} onGo={props.onGo} />
      <div className="mx-layer"><b>{t("Code settings")}</b><span className="mx-t3" title={t("TALYX's standard model pipeline; improvements arrive by themselves")}>{t("standard pipeline")}</span><span className="tx-spacer" />
        <button type="button" className="mx-link" onClick={props.onWriteCode}>{t("Write as code")}</button></div>
      <div className="mx-groups" data-columns={columns.length}>{columns.map((ids, index) => <div key={index}>{ids.map(id => <Fragment key={id}>{G[id]}</Fragment>)}</div>)}</div>
    </div>
  </aside>;
}

export function AlgorithmSelect({algorithms, value, onChange}: {algorithms: Algorithm[]; value: string; onChange(next: string): void}) {
  const groups = [...new Set(algorithms.map(item => item.groupLabel))];
  return <TxSelect aria-label={t("Algorithm")} value={value} onChange={event => onChange(event.target.value)}>
    <option value="">{t("Choose")}</option>{groups.map(name => <optgroup key={name} label={name}>{algorithms.filter(item => item.groupLabel === name).map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</optgroup>)}</TxSelect>;
}

function LabelGroup({items, values, onChange, row}: {items: Parameter[]; values: Record<string, ParamValue>; onChange(name: string, value: ParamValue): void; row(item: Parameter): ReactNode}) {
  const formula = String(values.label ?? items.find(item => item.name === "label")?.default ?? "");
  const dayOf = DAYS.find(days => FORMS.some(([form]) => quick(days, form) === formula));
  const formOf = FORMS.find(([form]) => DAYS.some(days => quick(days, form) === formula))?.[0];
  const pick = (days: number | undefined, form: string | undefined) => onChange("label", quick(days ?? 5, form ?? "rank"));
  return <>
    {items.filter(item => item.name === "label").map(row)}
    <TxParamRow label={t("Bars ahead")}><TxSegmented label={t("Bars ahead")} value={dayOf === undefined ? "" : String(dayOf)} options={DAYS.map(days => ({value: String(days), label: String(days)}))} onChange={next => pick(Number(next), formOf)} /></TxParamRow>
    <TxParamRow label={t("Form")}><TxSegmented label={t("Form")} value={formOf ?? ""} options={FORMS.map(([value, text]) => ({value, label: text}))} onChange={next => pick(dayOf, next)} /></TxParamRow>
    {items.filter(item => item.name !== "label").map(row)}
  </>;
}

function LabelBox({value, error, reach, fields, onChange}: {value: string; error: FormulaError | null; reach: {ahead: number; start: {bar: number; at: string}} | null; fields: FactorFields | null; onChange(next: string): void}) {
  const text = error ? null : reach ? t("reads {bars} bars ahead · from {start}", { bars: reach.ahead, start: reach.start.bar === 1 && reach.start.at === "open" ? t("the next open") : t("this close") }) : null;
  return <div className="mx-label"><FormulaBox value={value} error={error} fields={fields} onChange={onChange} onRun={() => undefined} label={t("Label formula")} placeholder={t("Label, e.g. CSRank(Ref($open, -6) / Ref($open, -1) - 1)")} />
    {text && <div className="mx-labelnote">{text}</div>}</div>;
}

/** The inputs table: one row each with where it comes from, how much of the rows it covers and from when; a row opens its distribution on click (block 4). */
function InputsTable({rows, offers, check, onChange, inspect}: {inspect(input: string, feature?: string): Promise<Inspected>; rows: Offer[]; offers: Offer[]; check: CheckAnswer | null; onChange(next: Offer[]): void}) {
  const [open, setOpen] = useState<string | null>(null), [query, setQuery] = useState("");
  const [seen, setSeen] = useState<{id: string; feature?: string; data: Inspected | null; error?: string} | null>(null);
  const look = (id: string, feature?: string) => {
    setSeen({id, feature, data: null});
    inspect(id, feature).then(data => setSeen(current => current && current.id === id ? {id, feature, data} : current), reason => setSeen(current => current && current.id === id ? {id, feature, data: null, error: reason instanceof Error ? reason.message : String(reason)} : current));
  };
  const [picked, setPicked] = useState<Record<string, string>>({});
  /** One row an item (the versions of a library factor under one name, newest first), so a version is chosen in the row. */
  const rowsOf = (source: string): Offer[][] => {
    const groups = new Map<string, Offer[]>();
    for (const offer of list(source)) groups.set(`${offer.source}:${offer.name}`, [...(groups.get(`${offer.source}:${offer.name}`) ?? []), offer]);
    return [...groups.values()];
  };
  const covered = (row: Offer) => check?.inputs?.find(item => item.name === row.name || item.id === row.name);
  const add = (offer: Offer) => onChange([...rows, {source: offer.source, name: offer.name, ref: offer.ref} as Offer]);
  const list = (source: string) => offers.filter(offer => (source === "library" ? offer.source === "library" || offer.source === "value" : offer.source === source)
    && `${offer.name} ${offer.ref}`.toLowerCase().includes(query.toLowerCase()) && !offer.unavailable);
  return <div className="mx-inputs">
    {rows.length === 0 && <div className="mx-none">{t("No inputs yet")}</div>}
    {rows.map((row, index) => {
      const found = covered(row);
      const id = found?.id ?? null, shown = id !== null && seen?.id === id ? seen : null;
      const version = offers.find(offer => offer.ref === row.ref)?.version;
      return <div key={`${row.name}-${index}`} className="mx-inputbox"><div className="mx-input" data-flag={found?.flag} data-open={shown ? "" : undefined} role={id ? "button" : undefined} tabIndex={id ? 0 : undefined}
        onClick={() => {if (id) {if (shown) setSeen(null); else look(id);}}} onKeyDown={event => {if (id && (event.key === "Enter" || event.key === " ")) {event.preventDefault(); if (shown) setSeen(null); else look(id);}}}>
        <span className="mx-iname" title={row.name}>{row.name}</span><span className="mx-t3">{SOURCES[row.source] ?? row.source}{version ? ` · v${version}` : ""}</span>
        <span className="mx-t3" title={t("First day with a value")}>{found?.start ? dayYear(found.start) : ""}</span><span className="mx-t3 mx-num">{found ? `${Math.round(found.coverage * 100)}%` : ""}</span>
        <button type="button" className="mx-x" aria-label={t("Remove {name}", { name: row.name })} onClick={event => {event.stopPropagation(); onChange(rows.filter((_, other) => other !== index));}}><TxIcon name="close" /></button></div>
        {shown && <div className="mx-inspect">{shown.error ? <div className="mx-none">{shown.error}</div> : shown.data === null ? <div className="mx-none">{t("Looking at the training segments…")}</div> : <>
          {shown.data.features.length > 1 && <TxSelect aria-label={t("Feature")} value={shown.data.name} onChange={event => look(id!, event.target.value)}>{shown.data.features.map(name => <option key={name} value={name}>{name}</option>)}</TxSelect>}
          <div className="mx-insgrid"><div className="mx-insfig"><Plot figure={shown.data.figures["model.inspect.distribution"]} ariaLabel={t("Distribution of {name} before and after processing", { name: shown.data.name })} /></div>
            <div className="mx-insfig"><Plot figure={shown.data.figures["model.inspect.missing"]} ariaLabel={t("Share of {name} missing each week", { name: shown.data.name })} /></div></div>
          <div className="mx-insstats">{shown.data.stats.map(stat => `${stat.label === "Beyond 3 MAD" ? t("Beyond 3 MAD") : t(stat.label)} ${stat.before}${stat.after !== stat.before ? ` → ${stat.after}` : ""}`).join(" · ")}</div>
          <div className="mx-t3">{shown.data.note}</div></>}</div>}</div>;
    })}
    <div className="mx-adds">{["library", "run", "builtin", "market", "code"].map(source => <TxPopover key={source} open={open === source} onOpenChange={next => {setOpen(next ? source : null); setQuery("");}} label={SOURCES[source]}
      trigger={<TxButton size="sm" variant="quiet">{source === "library" ? t("Library factor") : source === "run" ? t("Factor run") : source === "builtin" ? t("Built-in set") : source === "market" ? t("Market series") : t("Own code")}</TxButton>}>
      <div className="mx-picker"><TxInput autoFocus placeholder={t("Search")} aria-label={t("Search")} value={query} onChange={event => setQuery(event.target.value)} />
        <div className="mx-plist">{rowsOf(source).slice(0, 80).map(group => {
          const offer = group.find(item => item.ref === picked[`${group[0].source}:${group[0].name}`]) ?? group[0];
          return <div key={`${offer.source}-${offer.name}`} className="mx-prow"><button type="button" onClick={() => add(offer)}>
            <span>{offer.name}</span><span className="mx-t3">{offer.version ? `v${offer.version}${group.length === 1 ? ` · ${offer.state === "fixed" ? t("Fixed") : t("Producing")}` : ""}` : offer.features ? t("{features} features", { features: offer.features }) : offer.rankIc !== undefined && offer.rankIc !== null ? (offer.published?.version ? t("Rank IC {rankIc} · v{version}", { rankIc: offer.rankIc.toFixed(4), version: offer.published.version }) : t("Rank IC {rankIc}", { rankIc: offer.rankIc.toFixed(4) })) : ""}</span>
            </button>
            {group.length > 1 && <TxSelect aria-label={t("Version of {name}", { name: offer.name })} value={offer.ref} onChange={event => setPicked({...picked, [`${offer.source}:${offer.name}`]: event.target.value})}>
              {group.map(item => <option key={item.ref} value={item.ref}>{item.state === "fixed" ? t("v{version} · Fixed", { version: item.version ?? "" }) : t("v{version} · Producing", { version: item.version ?? "" })}</option>)}</TxSelect>}</div>;})}
          {list(source).length === 0 && <div className="mx-none">{source === "code" ? t("No feature functions in this project; write one with @q.feature in a .py file.") : t("Nothing to add")}</div>}</div></div>
    </TxPopover>)}</div></div>;
}

/** A batch: any setting given several values; the groups it makes and what differs between them. */
function BatchGroup({prep, values, vary, onVary, check}: {prep: ModelPrep; values: Record<string, ParamValue>; vary: Vary[]; onVary(next: Vary[]): void; check: CheckAnswer | null}) {
  const [open, setOpen] = useState(false), [name, setName] = useState(""), [text, setText] = useState("");
  // Only what the chosen algorithm shows can be varied; a label that two of those share is told apart by its group.
  const all = prep.groups.flatMap(group => group.settings);
  const shown = prep.groups.flatMap(group => group.settings.map(item => ({...item, group: group.name})))
    .filter(item => ["int", "float", "choice", "bool"].includes(item.type) && item.name !== "algorithm" && item.name !== "objective_param" && visible(item, values, all));
  const settings = shown.map(item => shown.filter(other => (other.label || other.name) === (item.label || item.name)).length > 1 ? {...item, label: `${item.group} · ${item.label || item.name}`} : item);
  const parse = (): ParamValue[] => {
    const item = settings.find(entry => entry.name === name);
    if (!item) return [];
    const range = /^\s*(-?[\d.]+)\s+to\s+(-?[\d.]+)\s+by\s+([\d.]+)\s*$/.exec(text);  // "0.01 to 0.1 by 0.03"
    if (range && (item.type === "int" || item.type === "float")) {
      const [from, to, step] = [Number(range[1]), Number(range[2]), Number(range[3])], values: number[] = [];
      for (let at = from; step > 0 && at <= to + step * 1e-9 && values.length < 50; at += step) values.push(Number(at.toFixed(10)));
      return values;
    }
    return text.split(",").map(part => part.trim()).filter(Boolean).map(part => item.type === "choice" ? (item.options?.find(option => String(option.value) === part)?.value ?? part) : item.type === "bool" ? part === "true" : Number(part)) as ParamValue[];
  };
  const total = vary.reduce((count, item) => count * item.values.length, 1);
  const add = () => {const next = parse(); if (name && next.length) {onVary([...vary.filter(item => item.name !== name), {name, values: next}]); setOpen(false); setName(""); setText("");}};
  return <div className="mx-batch">
    {vary.map(item => <div key={item.name} className="mx-var"><span>{prep.groups.flatMap(group => group.settings).find(entry => entry.name === item.name)?.label ?? item.name}</span>
      <span className="mx-num">{item.values.join(", ")}</span><button type="button" className="mx-x" aria-label={t("Remove {name}", { name: item.name })} onClick={() => onVary(vary.filter(other => other.name !== item.name))}><TxIcon name="close" /></button></div>)}
    {vary.length > 0 && check?.batch && check.batch.groups.length > 1 && <TxTable className="mx-groups"><thead><tr><TxTh>{t("Group")}</TxTh>
      {vary.map(item => <TxTh key={item.name}>{prep.groups.flatMap(group => group.settings).find(entry => entry.name === item.name)?.label ?? item.name}</TxTh>)}<TxTh align="right">{t("Memory")}</TxTh><TxTh align="right">{t("Time")}</TxTh></tr></thead>
      <tbody>{check.batch.groups.map(group => <tr key={group.group}><TxTd className="mx-num">{group.group}</TxTd>{vary.map(item => <TxTd key={item.name} className="mx-num">{String(group.values[item.name] ?? values[item.name] ?? "")}</TxTd>)}
        <TxTd align="right" className="mx-num">{group.memory === null ? "" : t("{value} GB", { value: (group.memory / 1024 ** 3).toFixed(1) })}</TxTd><TxTd align="right" className="mx-num">{group.time === null ? "" : t("{minutes} min", { minutes: Math.max(1, Math.round(group.time / 60)) })}</TxTd></tr>)}</tbody></TxTable>}
    {vary.length > 0 && <div className="mx-est">{check?.batch && check.batch.total ? t.rich("{count} groups · about {minutes} min", { count: <b>{total}</b>, minutes: Math.round(check.batch.total / 60) }) : t.rich("{count} groups", { count: <b>{total}</b> })}</div>}
    <TxPopover open={open} onOpenChange={setOpen} label={t("Vary a setting")} trigger={<TxButton size="sm" variant="quiet">{t("Vary a setting")}</TxButton>}>
      <div className="mx-picker"><TxSelect aria-label={t("Setting")} value={name} onChange={event => setName(event.target.value)}><option value="">{t("Choose a setting")}</option>
        {settings.map(item => <option key={item.name} value={item.name}>{item.label || item.name}</option>)}</TxSelect>
        <TxInput aria-label={t("Values")} placeholder={name ? `${String(values[name] ?? "")}, …` : "0.03, 0.05, 0.1"} value={text} onChange={event => setText(event.target.value)} />
        {name && parse().length > 0 && <div className="mx-t3 mx-live">{t("{parseCount} values · {groups} groups with {setting}", { parseCount: parse().length, groups: vary.filter(item => item.name !== name).reduce((count, item) => count * item.values.length, parse().length), setting: settings.find(entry => entry.name === name)?.label || name })}</div>}
        <TxButton size="sm" variant="primary" onClick={add}>{t("Add")}</TxButton></div></TxPopover>
    <TxButton size="sm" variant="quiet" onClick={() => {const seed = Number(values.seed ?? 42); onVary([...vary.filter(item => item.name !== "seed"), {name: "seed", values: [seed, seed + 1, seed + 2]}]);}}>{t("Repeat with 3 seeds")}</TxButton>
  </div>;
}

export function tagOf(state: string) { return <TxTag tone={state === "fix" || state === "block" ? "fix" : "watch"}>{state}</TxTag>; }
