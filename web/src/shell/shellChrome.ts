import { useEffect, useMemo, useState } from "react";
import { failure } from "@talyx/sdk";
import { platformRuntime } from "../kernel/runtime";
import { useWorkspaceActivities, type Activity } from "./workspaceActivityData";
import type { TalyxDesktop } from "../../../host/desktop/preload";

/** One open workspace as the top bar shows it: its name, whether its modules report something running, and how much
    of that AI asked for (what "Stop AI work" stops). */
export interface ShellWorkspaceTab {
  id: string;
  name: string;
  running: boolean;
  aiWork: number;
}

/** A module that couldn't start: it lights the dot on the TALYX mark. */
export interface ShellAttention { id: string; title: string }

/** A module the module picker offers: every installed module with a panel, official or not. */
export interface ShellModuleChoice { kind: string; name: string; failed: boolean }

/** Work AI asked for in a workspace: what "Stop AI work" stops. */
export const aiWorkCount = (items: readonly Activity[]): number => items.filter(item => item.origin === "ai").length;

/** The open workspaces, each marked from what its modules report. */
export function useWorkspaceTabs(open: readonly { id: string; name: string | null }[]): ShellWorkspaceTab[] {
  const activity = useWorkspaceActivities();
  return useMemo(() => open.map(record => {
    const activities = activity.find(entry => entry.workspace.workspaceId === record.id)?.activities ?? [];
    return { id: record.id, name: record.name ?? "Untitled", running: activities.length > 0, aiWork: aiWorkCount(activities) };
  }), [open, activity]);
}

/* ── What lights the dots ──────────────────────────────────────────────────────────────────────────────────────── */

export interface ShellModuleState { id: string; name: string; failed: boolean }
export interface ShellStatusItem { level: string; visible?: boolean }

/** The TALYX mark lights only for modules that couldn't start; the settings gear lights for an error a module
    reports (its status items, listed in Settings under "Needs attention"). */
export function shellAlerts(modules: readonly ShellModuleState[], status: readonly ShellStatusItem[]): { faults: ShellAttention[]; settings: boolean } {
  return {
    faults: modules.filter(item => item.failed).map(item => ({ id: item.id, title: item.name })),
    settings: status.some(item => item.visible !== false && item.level === "error"),
  };
}

export function readShellAlerts() {
  const runtime = platformRuntime(), kernel = runtime.kernel;
  const modules = [...kernel.definitions.values()].filter(item => item.id !== "platform").map(item => ({
    id: item.id, name: item.name, failed: item.enabled && (item.state === "failed" || runtime.faults.has(item.id) && !kernel.modules.get(item.id)?.ready),
  }));
  modules.push(...runtime.unreadable.map(item => ({ id: item.id, name: item.name, failed: true })));
  const status = kernel.contributions("platform.status_item").flatMap(item => {
    const value = kernel.statusValues.get(`${item.moduleId}.${item.id}`);
    return value ? [value] : [];
  });
  return shellAlerts(modules, status);
}

export function useShellAlerts() {
  const kernel = platformRuntime().kernel;
  const [, redraw] = useState(0);
  useEffect(() => kernel.change.watch(() => redraw(value => value + 1)), [kernel]);
  return readShellAlerts();
}

/** Every enabled module that offers a panel for opening (`launcher`); one that couldn't start is listed but can't be picked. */
export function shellModuleChoices(faults: readonly ShellAttention[]): ShellModuleChoice[] {
  return [...platformRuntime().kernel.definitions.values()]
    .filter(item => item.id !== "platform" && item.enabled && Object.values(item.manifest.panels ?? {}).some(panel => panel.launcher))
    .map(item => ({ kind: `module:${item.id}`, name: item.name, failed: faults.some(entry => entry.id === item.id) }))
    .sort((left, right) => left.name.localeCompare(right.name));
}

/* ── The desktop window ────────────────────────────────────────────────────────────────────────────────────────── */

/** The desktop API, installed on the top window; workspace frames use it from there. Absent in a browser. */
export function useDesktop(): TalyxDesktop | undefined {
  const host = window.top!;
  const [desktop, setDesktop] = useState(() => host.talyxDesktop);
  useEffect(() => {
    const ready = () => setDesktop(host.talyxDesktop);
    host.addEventListener("talyx-desktop-ready", ready);
    ready();
    return () => host.removeEventListener("talyx-desktop-ready", ready);
  }, [host]);
  return desktop;
}

export function useWindowMaximized(desktop: TalyxDesktop | undefined): boolean {
  const [maximized, setMaximized] = useState(false);
  useEffect(() => {
    if (!desktop) return;
    let alive = true;
    void desktop.isWindowMaximized().then(value => { if (alive) setMaximized(value); });
    const stop = desktop.onWindowStateChanged(state => setMaximized(state.maximized));
    return () => { alive = false; stop(); };
  }, [desktop]);
  return maximized;
}

/** Opens an open workspace's folder in File Explorer (the desktop app only). */
export async function openWorkspaceFolder(workspaceId: string): Promise<void> {
  const desktop = window.top!.talyxDesktop;
  if (!desktop) throw failure("unavailable", "Opening folders needs the TALYX desktop app");
  const { path } = await platformRuntime().platform["workspace.folder"]({ workspaceId });
  await desktop.openFolder(path);
}
