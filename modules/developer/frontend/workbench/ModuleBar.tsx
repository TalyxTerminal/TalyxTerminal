/* A TALYX module's life in the running app, from its project: use it in TALYX, reload it after a change (only it
   restarts; its contract is generated again), disable or enable it, uninstall it, and debug its backend in VS Code. */
import { useEffect, useState } from "react";
import { TxButton, TxDialog, TxIconButton, TxMenu, TxStatus, TxToolbarDivider, type ModuleContext, type Platform, type TxMenuItem } from "@talyx/sdk";
import { documents } from "../documents/service";
import { debugInVsCode, type ProjectModule } from "./api";
import { message } from "./errors";

/** What the platform's modules.* actions say of a module; the fields this bar reads. */
type ModuleState = Platform.ModuleState;

const states: Record<string, [string, "done" | "busy" | "off" | "failed"]> = {
  ready: ["Running in TALYX", "done"], starting: ["Starting", "busy"], stopped: ["Stopped", "off"], failed: ["Couldn't start", "failed"],
};

export function ModuleBar({ ctx, projectId, module, onError, onProblem, onUninstalled }: { ctx: ModuleContext; projectId: string; module: ProjectModule; onError(message: string): void; onProblem(problem: string | null): void; onUninstalled?(): Promise<void> }) {
  const platform = <T,>(action: string, input: Record<string, string>) => ctx.actions.call(`platform.modules.${action}`, input) as Promise<unknown> as Promise<T>;
  const [state, setState] = useState<ModuleState | null | undefined>(undefined);
  const [busy, setBusy] = useState(false), [uninstalling, setUninstalling] = useState(false);
  useEffect(() => {
    let alive = true;
    const read = () => void platform<ModuleState[]>("list", {}).then(list => { if (alive) setState(list.find(item => item.id === module.id) ?? null); }, error => onError(message(error)));
    read();
    const subscription = ctx.streams.subscribe("platform.modules.changes", {}, read);
    return () => { alive = false; void subscription.then(value => value.close()); };
  }, [ctx, module.id]);
  const run = (work: () => Promise<unknown>) => { setBusy(true); void work().catch(error => onError(message(error))).finally(() => setBusy(false)); };
  /** What runs is what is on disk: the project's unsaved files are saved first. */
  const saveProject = async () => {
    for (const item of documents.dirty().filter(entry => entry.context.projectId === projectId)) await documents.save(item.documentId);
  };
  const use = () => run(async () => { await saveProject(); setState(await platform<ModuleState>("install", { path: module.directory })); });
  const reload = () => run(async () => { await saveProject(); setState(await platform<ModuleState>("reload", { id: module.id })); });
  const enable = () => run(async () => setState(await platform<ModuleState>("enable", { id: module.id })));
  const disable = () => run(async () => setState(await platform<ModuleState>("disable", { id: module.id })));
  const uninstall = () => run(async () => { await platform("uninstall", { id: module.id }); setUninstalling(false); setState(null); await onUninstalled?.(); });
  useEffect(() => onProblem(state?.state === "failed" ? state.problem ?? "It exited." : null), [state?.state, state?.problem]);
  if (state === undefined) return null;
  const [label, tone] = state === null ? ["Not in TALYX", "off" as const] : !state.enabled ? ["Disabled", "off" as const] : states[state.state] ?? [state.state, "off" as const];
  /** The next step stays a button; the rest waits in a menu so the toolbar fits a narrow panel. */
  const actions: TxMenuItem[] = [
    ...(state && state.enabled ? [{ id: "disable", label: "Disable", onSelect: disable }] : []),
    ...(state && state.source === "installed" ? [{ id: "uninstall", label: "Uninstall…", tone: "danger" as const, onSelect: () => setUninstalling(true) }] : []),
    { id: "debug", label: "Debug in VS Code", disabled: state?.pid == null, separated: !!state, onSelect: () => run(() => debugInVsCode(ctx, projectId)) },
  ];
  return <>
    <TxToolbarDivider />
    <span title={state?.problem ?? undefined}><TxStatus tone={tone} label={label} /></span>
    {state === null && <TxButton variant="primary" loading={busy} onClick={use}>Use in TALYX</TxButton>}
    {state && state.enabled && <TxButton variant="primary" loading={busy} onClick={reload}>Reload</TxButton>}
    {state && !state.enabled && <TxButton variant="primary" loading={busy} onClick={enable}>Enable</TxButton>}
    <TxMenu label="Module actions" align="end" trigger={<TxIconButton label="Module actions" icon="more" disabled={busy} />} items={actions} />
    <TxDialog open={uninstalling} onOpenChange={open => { if (!open && !busy) setUninstalling(false); }} title={`Uninstall ${module.name}`} footer={<>
      <TxButton disabled={busy} onClick={() => setUninstalling(false)}>Cancel</TxButton>
      <TxButton variant="primary" loading={busy} onClick={uninstall}>Uninstall</TxButton></>}>
      <p>{module.installed ? `This deletes ${module.directory}, the module's source with it.` : `TALYX removes its copy of ${module.name}; this project's folder stays.`}</p>
    </TxDialog>
  </>;
}
