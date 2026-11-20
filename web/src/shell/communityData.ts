import { useCallback, useEffect, useState } from "react";
import type { Json } from "@talyx/sdk";
import type { Platform } from "@talyx/sdk";
import { platformRuntime } from "../kernel/runtime";
type Listed = Platform.ModuleState;
type SavedInfo = Platform.SavedInfo;
type Windows = Platform.Windows;

/* Community's data: the modules on this computer (platform.modules.list, with each one's declarations from
   platform.modules.definitions), how many saved workspaces use each, and the platform's module management. */

export type ModuleState = "running" | "starting" | "failed" | "disabled";

export interface ModuleAction { name: string; title: string }

export interface ModuleEntry {
  id: string;
  name: string;
  summary: string;
  version: string;
  official: boolean;
  readable: boolean;
  enabled: boolean;
  state: ModuleState;
  /** Why it isn't running, as the platform or its frontend said it. */
  reason: string | null;
  panels: string[];
  /** Its public actions, which AI can call (each in its declaration's words). */
  actions: ModuleAction[];
  /** Where its settings open: its declared settings, else its first settings section. */
  settings: string | null;
  /** The modules it needs, by name. */
  requires: string[];
  /** Enabled modules that need it, by name: it can't be disabled or uninstalled while they are there. */
  dependents: string[];
}

/** Saved workspaces with a panel of this module, from their layout summaries. Records without one aren't counted. */
export function workspacesUsing(workspaces: readonly { summary?: SavedInfo["summary"] }[], moduleId: string): number {
  return workspaces.filter(item => item.summary?.tiles.some(tile => tile.module === moduleId)).length;
}

function stateOf(row: Listed): { state: ModuleState; reason: string | null } {
  const runtime = platformRuntime();
  if (!row.enabled) return { state: "disabled", reason: null };
  if (row.state === "failed") return { state: "failed", reason: row.problem ?? "It couldn't start." };
  if (row.state === "stopped") return { state: "failed", reason: row.problem ?? "It isn't running." };
  if (row.state === "starting") return { state: "starting", reason: null };
  const fault = runtime.faults.get(row.id);
  if (fault) return { state: "failed", reason: fault };
  if (runtime.kernel.definitions.get(row.id)?.manifest.entries.frontend && !runtime.kernel.modules.get(row.id)?.ready) return { state: "starting", reason: null };
  return { state: "running", reason: null };
}

export function readModules(rows: readonly Listed[]): ModuleEntry[] {
  const kernel = platformRuntime().kernel;
  const nameOf = (id: string) => kernel.definitions.get(id)?.name ?? id;
  return rows.filter(row => row.id !== "platform").map(row => {
    const definition = kernel.definitions.get(row.id);
    const manifest = definition?.manifest, contract = definition?.contract;
    const section = manifest?.provides?.["platform.settings_section"]?.[0];
    return {
      id: row.id,
      name: row.name,
      summary: row.description,
      version: row.version,
      readable: Boolean(definition),
      official: row.source === "official",
      enabled: row.enabled,
      ...stateOf(row),
      panels: Object.values(manifest?.panels ?? {}).map(panel => panel.title),
      actions: Object.entries(contract?.actions ?? {}).map(([local, spec]) =>
        ({ name: `${row.id}.${local}`, title: spec.description.split("\n")[0] })),
      settings: Object.keys(contract?.settings ?? {}).length ? `settings:${row.id}` : section ? `${row.id}.${section.id}` : null,
      requires: Object.entries(manifest?.requires ?? {}).filter(([, value]) => !value.optional).map(([id]) => nameOf(id)),
      dependents: [...kernel.definitions.values()]
        .filter(item => item.id !== row.id && item.enabled && item.manifest.requires?.[row.id] && !item.manifest.requires[row.id].optional)
        .map(item => item.name),
    };
  });
}

/** The modules on this computer, read again whenever the window's module list changes (platform.modules.changes). */
export function useModules() {
  const kernel = platformRuntime().kernel;
  const [rows, setRows] = useState<Listed[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [changes, setChanges] = useState(kernel.change.get());
  const reload = useCallback(() => setRevision(value => value + 1), []);
  useEffect(() => kernel.change.watch(() => setChanges(kernel.change.get())), [kernel]);
  useEffect(() => {
    let live = true;
    platformRuntime().platform["modules.list"]({})
      .then(value => { if (live) { setRows(value); setError(null); } })
      .catch(reason => { if (live) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => { live = false; };
  }, [revision, changes]);
  return { modules: rows ? readModules(rows) : null, error, reload };
}

/** The saved workspaces, with their layout summaries. */
export function useSavedWorkspaces(revision: unknown) {
  const [items, setItems] = useState<SavedInfo[]>([]);
  useEffect(() => {
    let live = true;
    platformRuntime().platform["workspace.list"]({})
      .then(value => { if (live) setItems(value.saved); })
      .catch(() => { if (live) setItems([]); });
    return () => { live = false; };
  }, [revision]);
  return items;
}

/* ── Managing a module: the window's frontends follow (platform.modules.changes) ─────── */

const managed = async <T,>(change: Promise<T>): Promise<T> => {
  try { return await change; }
  finally { await platformRuntime().refresh(); }
};

/** Installs a module from a folder holding talyx.module.json, or a zip file of one, and starts it. */
export const installModule = (path: string) => managed(platformRuntime().platform["modules.install"]({ path }));
export const uninstallModule = (id: string) => managed(platformRuntime().platform["modules.uninstall"]({ id }));
export const enableModule = (id: string) => managed(platformRuntime().platform["modules.enable"]({ id }));
export const disableModule = (id: string) => managed(platformRuntime().platform["modules.disable"]({ id }));
/** Reads the module's code again (its contract too) and restarts only it. */
export const reloadModule = (id: string) => managed(platformRuntime().platform["modules.reload"]({ id }));
/** Starts a module again; the platform answers with why when it still can't. */
export const retryModule = (id: string) => platformRuntime().retry(id);

/** Panels of this module open in any window now, as each window reports them (platform.workspace.windows). */
export async function openPanelsOf(moduleId: string): Promise<number> {
  const windows = await new Promise<Windows>((resolve, reject) => {
    const subscription = platformRuntime().bridge.subscribe("platform.workspace.windows", {}, (value: Json) => {
      resolve(value as unknown as Windows);
      void subscription.then(opened => opened.close());
    });
    subscription.catch(reject);
  });
  return windows.workspaces.reduce((total, workspace) => total + (workspace.moduleInstances[moduleId] ?? 0), 0);
}
