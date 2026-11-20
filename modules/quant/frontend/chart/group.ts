/* The charts of one section work as one: the section's head carries one key for all of them, with Linear | Log when a chart offers it. A section is the
   nearest element above a chart that has a region head (`.tx-regionhead`) of its own; pages do nothing to join.
   Charts on a time axis share the x range (wheel, drag, double click) and the cursor (crosshair, marks and read-out on the same day) with the others of
   their section, or of a wider area a page marks with `data-chart-link` (every chart of a Results tab, whatever section it is in). */

import type { ChartKey } from "./paint";

export interface ChartMember {
  /** On a time axis: shares range and cursor with the others. */
  linked: boolean;
  keys: ChartKey[];
  /** The names this chart writes at its lines' ends: the section's key leaves them out. */
  ends?: string[];
  /** Offers a log value axis (its lines are levels above zero). */
  scales: boolean;
  range(range: [unknown, unknown] | null): void;
  /** A range shown for the moment only (in UTC milliseconds), while another chart is being zoomed with the wheel; `range` follows when the wheel stops. */
  preview(range: [number, number]): void;
  cursor(x: unknown | null): void;
  /** The right margin its own plot needs (value axis and end labels), in pixels: the linked charts of a section take the widest, so their plots end together. */
  right?: number;
}

export class ChartGroup {
  members: ChartMember[] = [];
  log = false;
  /** The x range last given to the section (null: the whole time); a chart drawn later opens on it. */
  shown: [unknown, unknown] | null = null;
  private version = 0;
  private listeners = new Set<() => void>();
  private busy = false;
  join(member: ChartMember): () => void {
    this.members.push(member);
    this.changed();
    return () => { this.members = this.members.filter(item => item !== member); this.changed(); };
  }
  changed(): void { this.version++; this.listeners.forEach(listener => listener()); }
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  snapshot = () => this.version;
  /** The key of the whole section: every member's entries, once per name, in the members' order; a name written at a line's end in any chart is left out. */
  keys(): ChartKey[] {
    const out: ChartKey[] = [];
    const ends = new Set(this.members.flatMap(member => member.ends ?? []));
    for (const member of this.members) for (const key of member.keys) if (!ends.has(key.name) && !out.some(item => item.name === key.name)) out.push(key);
    return out;
  }
  scales(): boolean { return this.members.some(member => member.scales); }
  right(): number { return Math.max(0, ...this.members.filter(member => member.linked).map(member => member.right ?? 0)); }
  setLog(log: boolean): void { if (this.log !== log) { this.log = log; this.changed(); } }
  /** One member's x range (null: back to the whole) given to the other linked members. */
  range(from: ChartMember, range: [unknown, unknown] | null): void { if (from.linked) this.shown = range; this.each(from, member => member.range(range)); }
  /** One member's passing x range, while the wheel turns over it, given to the other linked members to show without laying them out. */
  preview(from: ChartMember, range: [number, number]): void { this.each(from, member => member.preview(range)); }
  /** One member's cursor (an x, or null when it left) shown on the other linked members. */
  cursor(from: ChartMember, x: unknown | null): void { this.each(from, member => member.cursor(x)); }
  private each(from: ChartMember, act: (member: ChartMember) => void) {
    if (this.busy || !from.linked) return;
    this.busy = true;
    try { for (const member of this.members) if (member !== from && member.linked) act(member); } finally { this.busy = false; }
  }
}

const groups = new WeakMap<Element, ChartGroup>();

/** The section a chart sits in, the place in its head for the key, and the area whose charts move with it (the section, or a wider one marked
    `data-chart-link`); null when no element above it has a region head. */
export function chartSection(node: Element): { scope: Element; slot: Element | null; link: Element } | null {
  for (let at = node.parentElement; at && at !== node.ownerDocument.body; at = at.parentElement) {
    const head = Array.from(at.children).find(child => child.classList.contains("tx-regionhead"));
    if (head) return { scope: at, slot: head.querySelector(":scope > [data-tx-keys]"), link: at.closest("[data-chart-link]") ?? at };
  }
  return null;
}

export function chartGroup(scope: Element): ChartGroup {
  let group = groups.get(scope);
  if (!group) { group = new ChartGroup(); groups.set(scope, group); }
  return group;
}

/** Whether a figure can be read on a log value axis: it has lines and every value on them is above zero (levels such as equity, not returns). */
export function offersLog(figure: { data?: unknown[]; layout?: Record<string, unknown> }): boolean {
  const meta = (figure.layout?.meta ?? {}) as { scales?: unknown };
  if (!Array.isArray(meta.scales) || !meta.scales.includes("log")) return false;
  const lines = (figure.data ?? []).filter(trace => ((trace as { type?: string }).type ?? "scatter") === "scatter") as { y?: unknown[] }[];
  return lines.length > 0 && lines.every(trace => Array.isArray(trace.y) && trace.y.every(value => value === null || (typeof value === "number" && value > 0)));
}
