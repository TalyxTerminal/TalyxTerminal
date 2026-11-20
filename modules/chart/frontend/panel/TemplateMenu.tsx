/* TradingView's template menu, for indicator, chart-settings and drawing templates: save what is on screen under a name,
   apply one, remove one. Templates are global (every workspace has them; the workspace stream brings changes). */
import { useState } from "react";
import { TxButton, TxDialog, TxInput, TxMenu, type ModuleContext, type TxMenuItem } from "@talyx/sdk";
import { ChartGlyph } from "../chart/icons";
import { post } from "../api";
import { message, reportChartError, useStore } from "../store";
import type { Template } from "../wire";

export function TemplateMenu({ ctx, chartId, kind, matches = () => true, content, apply, tool }: {
  ctx: ModuleContext; chartId: string; kind: Template["kind"];
  /** Drawing templates belong to one tool. */
  matches?: (template: Template) => boolean;
  content: () => Record<string, unknown>; apply: (content: Record<string, unknown>) => void;
  /** A toolbar's tool button with this label (the indicator templates); a dialog footer's "Template" button otherwise. */
  tool?: string;
}) {
  const all = useStore(state => state.templates);
  const templates = Object.values(all).filter(item => item.kind === kind && matches(item)).sort((a, b) => a.name.localeCompare(b.name));
  const [naming, setNaming] = useState<string | null>(null);
  const fail = (error: unknown) => reportChartError(chartId, message(error));
  const save = () => {
    const name = naming!.trim();
    // A name already used is saved over (TradingView asks; the user chose the name again).
    const same = templates.find(item => item.name === name);
    post(ctx, "/templates/save", { ...(same ? { templateId: same.id } : {}), kind, name, content: content() }).then(() => setNaming(null), fail);
  };
  const items: TxMenuItem[] = [
    { id: "save", label: "Save template…", icon: "plus", onSelect: () => setNaming("") },
    ...templates.map((template, index) => ({ id: template.id, label: template.name, separated: index === 0, onSelect: () => apply(template.content) })),
    ...templates.map((template, index) => ({ id: `remove-${template.id}`, label: `Remove “${template.name}”`, icon: "trash" as const, tone: "danger" as const,
      separated: index === 0, onSelect: () => void post(ctx, "/templates/delete", { templateId: template.id }).catch(fail) })),
  ];
  return <>
    <TxMenu label={tool ?? "Templates"} items={items} trigger={tool
      ? <TxButton variant="tool" size="sm" aria-label={tool}><ChartGlyph name="templates" /></TxButton>
      : <TxButton size="sm">Template</TxButton>} />
    {naming !== null && <TxDialog open onOpenChange={open => { if (!open) setNaming(null); }} title="Save template"
      footer={<><span className="chart-tb-spacer" /><TxButton size="sm" onClick={() => setNaming(null)}>Cancel</TxButton>
        <TxButton size="sm" variant="primary" disabled={!naming.trim()} onClick={save}>Save</TxButton></>}>
      <TxInput aria-label="Template name" placeholder="Template name" autoFocus value={naming} onChange={event => setNaming(event.target.value)}
        onKeyDown={event => { if (event.key === "Enter" && naming.trim()) save(); }} />
    </TxDialog>}
  </>;
}
