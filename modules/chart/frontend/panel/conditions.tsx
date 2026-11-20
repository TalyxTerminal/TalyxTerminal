/* One condition, as alerts and screens write it (TradingView's alert condition row): a source, the operator, what it is
   compared with (another source or a value, or a channel's two bounds), or an amount within some bars. */
import { TxInput, TxSelect } from "@talyx/sdk";
import type { ChartEntry } from "../store";
import type { CatalogEntry, Condition, ConditionOp, Operand } from "../wire";

export const OPS: { op: ConditionOp; label: string; right: 0 | 1 | 2; amount?: boolean }[] = [
  { op: "crossing", label: "Crossing", right: 1 }, { op: "crossing_up", label: "Crossing up", right: 1 }, { op: "crossing_down", label: "Crossing down", right: 1 },
  { op: "greater", label: "Greater than", right: 1 }, { op: "less", label: "Less than", right: 1 },
  { op: "entering_channel", label: "Entering channel", right: 2 }, { op: "exiting_channel", label: "Exiting channel", right: 2 },
  { op: "inside_channel", label: "Inside channel", right: 2 }, { op: "outside_channel", label: "Outside channel", right: 2 },
  { op: "moving_up", label: "Moving up", right: 0, amount: true }, { op: "moving_down", label: "Moving down", right: 0, amount: true },
  { op: "moving_up_percent", label: "Moving up %", right: 0, amount: true }, { op: "moving_down_percent", label: "Moving down %", right: 0, amount: true },
  // An indicator's own alertcondition() fires by itself: nothing to compare it with.
  { op: "triggered", label: "Triggered", right: 0 },
];

/** The sources a condition can pick, keyed for a select. */
export type Sources = Map<string, { label: string; operand: Operand }>;

/** A chart's indicator outputs as sources; with `alertConditions`, their alertcondition() calls too. Indicators that read the
    visible range are left out: alerts and screens run without a chart. */
export function indicatorSources(entry: ChartEntry, catalog: CatalogEntry[] | null, alertConditions: boolean): Sources {
  const sources: Sources = new Map();
  for (const indicator of entry.chart!.document.indicators) {
    const definition = catalog?.find(item => item.source === indicator.definition.source && item.id === indicator.definition.id);
    if (definition?.visibleRange) continue;
    const name = indicator.name ?? definition?.shortTitle ?? indicator.definition.id;
    for (const meta of entry.feed.indicators.get(indicator.id)?.meta ?? []) if (meta.kind === "plot")
      sources.set(`ind:${indicator.id}:${meta.id}`, { label: `${name}: ${meta.title ?? meta.id}`, operand: { kind: "indicator", definition: indicator.definition, params: indicator.params, output: meta.id } });
    if (alertConditions) for (const condition of definition?.alertConditions ?? [])
      sources.set(`ac:${indicator.id}:${condition}`, { label: `${name}: ${condition}`, operand: { kind: "alertcondition", definition: indicator.definition, params: indicator.params, condition } });
  }
  return sources;
}

const keyOf = (operand: Operand | null | undefined, sources: Sources) =>
  !operand || operand.kind === "value" ? "value" : [...sources].find(([, item]) => JSON.stringify(item.operand) === JSON.stringify(operand))?.[0] ?? "value";

/** The left side offers alertconditions too; a side compared against offers values instead. */
function OperandPick({ label, sources, value, onChange, allowValue }: { label: string; sources: Sources; value: Operand | null; onChange(value: Operand): void; allowValue: boolean }) {
  if (allowValue) sources = new Map([...sources].filter(([, item]) => item.operand.kind !== "alertcondition"));
  return <div className="chart-style-row">
    <TxSelect aria-label={label} value={keyOf(value, sources)} onChange={event => onChange(event.target.value === "value" ? { kind: "value", value: value?.kind === "value" ? value.value : 0 } : sources.get(event.target.value)!.operand)}>
      {allowValue && <option value="value">Value</option>}
      {[...sources].map(([id, item]) => <option key={id} value={id}>{item.label}</option>)}
    </TxSelect>
    {value?.kind === "value" && <TxInput numeric type="number" step="any" aria-label={`${label} value`} value={String(value.value)}
      onChange={event => { const next = Number(event.target.value); if (event.target.value !== "" && Number.isFinite(next)) onChange({ kind: "value", value: next }); }} />}
  </div>;
}

/** `last`: the latest price, what a new value side starts from. */
export function ConditionFields({ condition, sources, last, onChange }: { condition: Condition; sources: Sources; last: number; onChange(condition: Condition): void }) {
  const shape = OPS.find(item => item.op === condition.op)!;
  const firing = condition.left.kind === "alertcondition";
  const set = (change: Partial<Condition>) => onChange({ ...condition, ...change });
  const setLeft = (left: Operand) => {
    if (left.kind === "alertcondition") onChange({ left, op: "triggered", right: null, right2: null, amount: null, bars: null });
    else if (firing) onChange({ left, op: "crossing", right: { kind: "value", value: last }, right2: null, amount: null, bars: null });
    else set({ left });
  };
  const setOp = (op: ConditionOp) => {
    const next = OPS.find(item => item.op === op)!;
    set({ op, right: next.right ? condition.right ?? { kind: "value", value: last } : null,
      right2: next.right === 2 ? condition.right2 ?? { kind: "value", value: last * 0.98 } : null,
      amount: next.amount ? condition.amount ?? 1 : null, bars: next.amount ? condition.bars ?? 1 : null });
  };
  return <div className="chart-condition">
    <OperandPick label="Source" sources={sources} value={condition.left} onChange={setLeft} allowValue={false} />
    <TxSelect aria-label="Operator" value={condition.op} onChange={event => setOp(event.target.value as ConditionOp)}>
      {OPS.filter(item => (item.op === "triggered") === firing).map(item => <option key={item.op} value={item.op}>{item.label}</option>)}
    </TxSelect>
    {shape.right >= 1 && <OperandPick label={shape.right === 2 ? "Upper bound" : "Against"} sources={sources} value={condition.right ?? null} onChange={right => set({ right })} allowValue />}
    {shape.right === 2 && <OperandPick label="Lower bound" sources={sources} value={condition.right2 ?? null} onChange={right2 => set({ right2 })} allowValue />}
    {shape.amount && <div className="chart-style-row">
      <TxInput numeric type="number" step="any" aria-label="Amount" value={String(condition.amount ?? 1)} onChange={event => set({ amount: Number(event.target.value) })} />
      <span className="chart-muted">within</span>
      <TxInput numeric type="number" min={1} aria-label="Bars" suffix="bars" value={String(condition.bars ?? 1)} onChange={event => set({ bars: Math.max(1, Math.round(Number(event.target.value))) })} />
    </div>}
  </div>;
}
