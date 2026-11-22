import { displayIndicator } from "../indicatorText";
import { t } from "../i18n";
/* The editor tab (TradingView's script editor): one of the user's indicators in Developer's project editor, a picker of
   them and New indicator on top, Add to chart. Developer owns the text, saving and Python editing; Chart marks where its
   own run of the script failed (loading it in the catalog, or an instance on the active chart). A built-in's code opens
   read-only (Chart's indicator documents in Developer's editor), with Make a copy into the user's indicators. */
import { useEffect, useMemo, useRef, useState } from "react";
import { ModuleView, TxButton, TxDialog, TxEmpty, TxIcon, TxInput, TxMenu, type Json, type ModuleContext } from "@talyx/sdk";
import { createClient } from "../../../developer/public/ts/types";
import { message, refreshCatalog, setUi, store, useStore } from "../store";
import { addIndicator } from "../panel/IndicatorsDialog";

interface Change { uri: string; documentId: string; version: number; dirty: boolean }
interface Mark { uri: string; line: number; severity: "error"; message: string; source: string }

/** A user indicator is a `.py` file or a folder with `main.py`; its catalog id is the file or folder name. */
const fileOf = (rootUri: string, id: string) => `${rootUri.replace(/\/+$/, "")}/${id.endsWith(".py") ? id : `${id}/main.py`}`;

const template = (title: string) => `"""@pyne"""
from pynecore.lib import close, plot, script


@script.indicator(${JSON.stringify(title)}, overlay=True)
def main():
    plot(close, "Close")
`;

/** Chart's read-only indicator documents (backend/authoring.py): a built-in's code is `builtin/<id>`. */
const builtinSource = (id: string) => ({ kind: "contribution" as const, providerId: "chart:indicator-docs", sourceKey: `builtin/${id}` });

/** A built-in's copy: `suffix` after its title (the string that opens `@script.indicator(`) and its `shorttitle=`; every
    built-in declares both as plain strings there. */
const retitled = (source: string, suffix: string) => source
  .replace(/(@script\.indicator\(\s*(['"]).*?)\2/, `$1${suffix}$2`)
  .replace(/(shorttitle\s*=\s*(['"]).*?)\2/, `$1${suffix}$2`);

export function EditorTab({ ctx, panelId }: { ctx: ModuleContext; panelId: string }) {
  const developer = useMemo(() => createClient(ctx), [ctx]);
  const folder = useStore(state => state.indicatorsFolder);
  const catalog = useStore(state => state.catalog);
  const chosen = useStore(state => state.ui[panelId].script);
  const active = useStore(state => state.layouts[panelId].active);
  const chart = useStore(state => state.charts[active]);
  const [project, setProject] = useState<{ projectId: string; rootUri: string } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [naming, setNaming] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const changes = useRef(new Map<string, Change>());

  useEffect(() => {
    if (!folder) return;
    developer["projects.open"]({ directory: folder, name: t("Indicators") })
      .then(opened => setProject({ projectId: opened.projectId, rootUri: opened.rootUri }), error => setProblem(message(error)));
  }, [developer, folder]);
  // One `on` for the life of the tab (a new object each render would remount the view). A save shows in the catalog.
  const listeners = useMemo(() => ({ changed: (value: Json) => {
    const change = value as unknown as Change, before = changes.current.get(change.uri);
    changes.current.set(change.uri, change);
    if (before?.dirty && !change.dirty) refreshCatalog(ctx).catch(error => setProblem(message(error)));
  } }), [ctx]);

  const scripts = (catalog ?? []).filter(entry => entry.source === "user").sort((a, b) => a.title.localeCompare(b.title));
  const builtin = chosen?.source === "builtin" ? (catalog ?? []).find(entry => entry.source === "builtin" && entry.id === chosen.id) ?? null : null;
  const script = builtin ? undefined : scripts.find(entry => entry.id === chosen?.id) ?? scripts[0];
  const builtinProps = useMemo(() => builtin && { source: builtinSource(builtin.id), readOnly: true }, [builtin?.id]);
  const file = project && script ? fileOf(project.rootUri, script.id) : null;

  // Where Chart's run of this script failed: loading it, and its instances on the active chart.
  const failures = script ? [
    ...(script.error ? [{ message: script.error, line: script.line ?? null, source: t("Load") }] : []),
    ...(chart?.chart?.document.indicators ?? []).filter(item => item.definition.source === "user" && item.definition.id === script.id)
      .flatMap(item => chart!.indicatorErrors[item.id] ? [{ message: chart!.indicatorErrors[item.id].message, line: chart!.indicatorErrors[item.id].line ?? null, source: t("Chart") }] : []),
  ] : [];
  const markers: Mark[] = file ? failures.flatMap(failure => failure.line ? [{ uri: file, line: failure.line, severity: "error" as const, message: failure.message, source: failure.source }] : []) : [];
  const unplaced = failures.find(failure => !failure.line);

  const create = async () => {
    const title = naming!.trim(), id = `${title.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "")}.py`;
    setBusy(true);
    try {
      await developer["documents.create"]({ projectId: project!.projectId, relativePath: id, content: template(title) });
      await refreshCatalog(ctx);
      setUi(panelId, { script: { source: "user", id } });
      setNaming(null);
    } catch (error) { setProblem(message(error)); }
    setBusy(false);
  };
  // TradingView's Make a copy: the built-in's code as a new indicator of the user's, opened to edit.
  const makeCopy = async () => {
    setBusy(true); setProblem(null);
    try {
      const { content } = await developer["documents.read_source"]({ full: true, source: builtinSource(builtin!.id) });
      const ids = new Set(scripts.map(entry => entry.id)), titles = new Set(scripts.map(entry => entry.title));
      let id = `${builtin!.id}_copy.py`, suffix = " copy";
      for (let n = 2; ids.has(id) || titles.has(builtin!.title + suffix); n++) { id = `${builtin!.id}_copy_${n}.py`; suffix = ` copy ${n}`; }
      await developer["documents.create"]({ projectId: project!.projectId, relativePath: id, content: retitled(content, suffix) });
      await refreshCatalog(ctx);
      setUi(panelId, { script: { source: "user", id } });
    } catch (error) { setProblem(message(error)); }
    setBusy(false);
  };
  // TradingView's Add to chart saves first: the chart runs what is on disk.
  const addToChart = async () => {
    setBusy(true); setProblem(null);
    try {
      const change = changes.current.get(file!);
      if (change?.dirty) {
        const saved = await developer["documents.save"]({ documentId: change.documentId, expectedVersion: change.version });
        if (saved.status !== "saved" && saved.status !== "clean") throw new Error(saved.message ?? t("Not saved ({status})", { status: saved.status }));
      }
      await refreshCatalog(ctx);
      const entry = store.getState().catalog!.find(item => item.source === "user" && item.id === script!.id)!;
      if (!entry.error) await addIndicator(ctx, active, entry);
    } catch (error) { setProblem(message(error)); }
    setBusy(false);
  };

  const picker = <TxMenu label={t("Indicators")} items={[
    ...scripts.map(entry => ({ id: entry.id, label: entry.title, checked: entry.id === script?.id, onSelect: () => setUi(panelId, { script: { source: "user", id: entry.id } }) })),
    { id: "new", label: t("New indicator…"), icon: "plus" as const, separated: scripts.length > 0, onSelect: () => setNaming("") },
  ]} trigger={<TxButton variant="tool" size="sm" className="chart-bottom-title">{builtin ? displayIndicator(builtin).title : script?.title ?? t("Indicators")}<TxIcon name="chevron-down" /></TxButton>} />;

  return <div className="chart-bottom-body">
    <div className="chart-bottom-toolbar" role="toolbar" aria-label={t("Editor")}>
      {picker}
      {(problem || unplaced) && <span className="chart-alert-error" role="alert">{problem ?? unplaced!.message}</span>}
      <span className="chart-tb-spacer" />
      {builtin && <TxButton size="sm" loading={busy} disabled={!project} onClick={() => void makeCopy()}>{t("Make a copy")}</TxButton>}
      {script && <TxButton size="sm" loading={busy} disabled={!file} onClick={() => void addToChart()}>{t("Add to chart")}</TxButton>}
    </div>
    {builtinProps
      ? <div className="chart-bottom-view"><ModuleView id="developer.editor" context={ctx} props={builtinProps as unknown as Json} /></div>
      : project && file
      ? <div className="chart-bottom-view"><ModuleView id="developer.project_editor" context={ctx} on={listeners}
          props={{ rootUri: project.rootUri, projectId: project.projectId, treeTitle: t("My indicators"), file, tree: false, markers } as unknown as Json} /></div>
      : catalog !== null && !scripts.length && <TxEmpty title={t("No indicators of your own yet")} action={<TxButton size="sm" onClick={() => setNaming("")}><TxIcon name="plus" />{t("New indicator")}</TxButton>} />}
    {naming !== null && <TxDialog open onOpenChange={value => { if (!value) setNaming(null); }} title={t("New indicator")}
      footer={<><span className="chart-tb-spacer" /><TxButton size="sm" onClick={() => setNaming(null)}>{t("Cancel")}</TxButton>
        <TxButton size="sm" variant="primary" loading={busy} disabled={!naming.trim() || !project} onClick={() => void create()}>{t("Create")}</TxButton></>}>
      <TxInput aria-label={t("Indicator name")} placeholder={t("Indicator name")} autoFocus value={naming} onChange={event => setNaming(event.target.value)}
        onKeyDown={event => { if (event.key === "Enter" && naming.trim() && project) void create(); }} />
    </TxDialog>}
  </div>;
}
