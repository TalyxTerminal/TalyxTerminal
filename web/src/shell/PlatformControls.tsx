import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import {
  ModuleView, TxButton, TxDetails, TxEmpty, TxIconButton, TxRoot, TxRow, TxSegmented, TxSettingsGroup, TxSettingsNote, TxSettingsPage,
  TxStatus, TxDialog, TxToastProvider, TxTooltip, TxTooltipProvider,
  type Json,
} from "@talyx/sdk";
import { platformRuntime } from "../kernel/runtime";
import { DeclaredSettings, declaredSettings } from "./DeclaredSettings";
import { workspaceFrameId, workspaceWindowCommand } from "./workspaceWindows";
import { NotificationsSection } from "./NotificationSettings";
import type { TalyxDesktop } from "../../../host/desktop/preload";
import type { Platform } from "@talyx/sdk";
import "./PlatformUi.css";
type Recorded = Platform.CallRecord;
type PlatformTypes = Platform.ContractTypes;

/** Opens settings from the module-facing `kernel.shell.settings(section)` call. */
function useSettingsRequest(onRequest: (section: string | null) => void) {
  useEffect(() => {
    const show = (event: Event) => {
      const detail = (event as CustomEvent<unknown>).detail;
      onRequest(typeof detail === "string" ? detail : null);
    };
    window.addEventListener("talyx:settings", show);
    return () => window.removeEventListener("talyx:settings", show);
  }, [onRequest]);
}

function useKernelChange() {
  const kernel = platformRuntime().kernel;
  const [, redraw] = useState(0);
  useEffect(() => kernel.change.watch(redraw), [kernel]);
  return kernel;
}

/** The desktop API is installed on the top window; workspace frames use it from there. */
function useDesktop(): TalyxDesktop | undefined {
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

/** Something a module reports that the user should see: a broken module or a status item at warn or error.
    Modules that couldn't start light the logo and are handled in Community, Installed. */
type Attention = { key: string; title: string; text: string; error: boolean; action?: { run: () => Promise<unknown>; label: string } };

export function attentionItems(): Attention[] {
  const runtime = platformRuntime(), kernel = runtime.kernel;
  const names = new Map([...kernel.definitions.values()].map(item => [item.id, item.name]));
  const broken: Attention[] = [...kernel.definitions.values(), ...runtime.unreadable]
    .filter(item => item.id !== "platform" && item.enabled && (item.state === "failed" || runtime.faults.has(item.id) && !kernel.modules.get(item.id)?.ready))
    .map(item => ({ key: `module:${item.id}`, title: item.name, text: item.problem ?? runtime.faults.get(item.id) ?? "This module couldn't start.",
      error: true, action: { label: "Retry", run: () => runtime.retry(item.id) } }));
  const platformFaults: Attention[] = [...runtime.faults.entries()]
    .filter(([owner]) => !names.has(owner))
    .map(([owner, text]) => ({ key: `fault:${owner}`, title: "TALYX", text, error: true }));
  const reported: Attention[] = kernel.contributions<PlatformTypes["point:status_item"]>("platform.status_item").flatMap(item => {
    const key = `${item.moduleId}.${item.id}`;
    const value = kernel.statusValues.get(key);
    if (!value || value.visible === false || (value.level !== "warn" && value.level !== "error")) return [];
    const { title, action } = item.descriptor;
    // A status item's tooltip starts with what its action does ("Retry cleanup", "Data sources").
    return [{ key, title, text: value.text || "Needs attention", error: value.level === "error",
      action: action ? { run: () => kernel.call("platform", action, {}), label: value.tooltip?.split("\n")[0] || "Retry" } : undefined }];
  });
  return [...broken, ...platformFaults, ...reported];
}

type NavItem = { key: string; title: string };
type NavGroup = { key: string; label: string | null; items: NavItem[] };

/** Real problems the platform or a module reports, with the module's own fix. Shown only while there are any. */
function AttentionGroup({ items }: { items: Attention[] }) {
  const [pending, setPending] = useState<string | null>(null);
  const [failed, setFailed] = useState<{ key: string; message: string } | null>(null);
  if (!items.length) return null;
  return <TxSettingsGroup title="Needs attention" className="talyx-settings-attention" aria-label="Needs attention">
    {items.map(item => <TxRow key={item.key} tone={item.error ? "failed" : "default"} title={item.title}
      detail={failed?.key === item.key ? <span role="alert">{failed.message}</span> : item.text}>
      {item.action && <TxButton size="sm" disabled={pending !== null} aria-busy={pending === item.key || undefined}
        onClick={async () => {
          setPending(item.key); setFailed(null);
          try { await item.action!.run(); }
          catch (reason) { setFailed({ key: item.key, message: reason instanceof Error ? reason.message : String(reason) }); }
          finally { setPending(null); }
        }}>{item.action.label}</TxButton>}
    </TxRow>)}
  </TxSettingsGroup>;
}

/** General: what needs attention, then the platform's declared settings (up color, sign-in startup,
    keep-awake), in the global layer or one workspace's. */
function GeneralSection({ attention, workspaceId }: { attention: Attention[]; workspaceId: string }) {
  return <TxSettingsPage>
    {workspaceId === "global" && <AttentionGroup items={attention} />}
    <DeclaredSettings moduleId="platform" workspaceId={workspaceId} />
  </TxSettingsPage>;
}

/** One recorded call (platform.ai.calls): who called, what, when it started and ended, and how it went. */
type CallRecord = Omit<Recorded, "caller"> & { caller: { origin?: string; module?: string | null; workspaceId?: string | null; ai?: { client?: string } | null } };

const callStatus = (call: CallRecord): { tone: "busy" | "done" | "failed"; label: string } =>
  call.endedAt === null ? { tone: "busy", label: "Running" } : call.ok ? { tone: "done", label: "Done" } : { tone: "failed", label: "Failed" };

/** An ISO time, as the call log records it. */
function when(iso: string): string {
  const date = new Date(iso), now = new Date();
  const ago = (now.getTime() - date.getTime()) / 1000;
  if (ago < 60) return "just now";
  if (ago < 3600) return `${Math.floor(ago / 60)} min ago`;
  const time = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return date.toDateString() === now.toDateString() ? time : `${date.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
}

/** An action, in its declaration's words: the first line of its description. */
function actionTitle(action: string): string {
  const [owner, ...rest] = action.split(".");
  const description = platformRuntime().kernel.definitions.get(owner)?.contract.actions[rest.join(".")]?.description;
  return description?.split("\n")[0].replace(/\.$/, "") || action;
}

/** External AI: what AI apps connected over MCP did, newest first, by app. The platform keeps a
    record of every call and stops none; whether to ask first is the AI app's own permission setting. */
function ExternalAiSection() {
  const runtime = platformRuntime();
  const [calls, setCalls] = useState<CallRecord[] | null>(null);
  const [names, setNames] = useState<Map<string, string>>(new Map());
  const [error, setError] = useState<string | null>(null);
  const [opened, setOpened] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const all = await runtime.platform["ai.calls"]({ limit: 200 }) as CallRecord[];
      // A module's AI calls carry that module; an external app's carry none.
      setCalls(all.filter(call => call.caller.origin === "ai" && !call.caller.module));
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [runtime]);

  useEffect(() => {
    void refresh();
    void workspaceWindowCommand({ kind: "list" })
      .then(open => setNames(new Map(open.map(item => [item.workspaceId, item.name ?? "Untitled"]))))
      .catch(() => setNames(new Map()));
    // Calls are recorded as they happen; there is no push for them, so the list is re-read while open.
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const apps = new Map<string, CallRecord[]>();
  for (const call of calls ?? []) {
    const app = call.caller.ai?.client ?? "AI app";
    apps.set(app, [...apps.get(app) ?? [], call]);
  }
  return <TxSettingsPage>
    {error && <TxSettingsGroup title="AI apps"><TxSettingsNote tone="error">Couldn't read the call record. {error}</TxSettingsNote></TxSettingsGroup>}
    {!error && !calls && <TxSettingsGroup title="AI apps"><TxSettingsNote>Loading…</TxSettingsNote></TxSettingsGroup>}
    {calls && calls.length === 0 && <TxSettingsGroup title="AI apps"><TxEmpty title="No AI app has called TALYX yet" /></TxSettingsGroup>}
    {[...apps].map(([app, items]) => <TxSettingsGroup key={app} title={app}>
      <div className="talyx-calls">
        {items.map(call => {
          const status = callStatus(call);
          return <Fragment key={call.callId}>
            <button type="button" className="talyx-call" aria-expanded={opened === call.callId}
              onClick={() => setOpened(current => current === call.callId ? null : call.callId)}>
              <span className="talyx-call-action">{actionTitle(call.capability)}</span>
              <span className="talyx-call-time">{names.get(call.caller.workspaceId ?? "") ?? "Closed workspace"}</span>
              <TxStatus tone={status.tone} label={status.label} />
              <span className="talyx-call-time">{when(call.startedAt)}</span>
            </button>
            {opened === call.callId && <div className="talyx-call-body">
              {call.error ? <span className="tx-error" role="alert">{call.error}</span> : <TxDetails label="Result">{call.summary}</TxDetails>}
            </div>}
          </Fragment>;
        })}
      </div>
    </TxSettingsGroup>)}
  </TxSettingsPage>;
}

/** A module's management page: `key` is moduleId.sectionId, `view` the module's view drawn as the page. */
type Section = { moduleId: string; key: string; title: string; view: string; workspace: boolean };

/** Sections owned and drawn by the platform. Modules moved to Community, Installed. */
const platformItems: Record<string, NavItem> = {
  general: { key: "general", title: "General" },
  notifications: { key: "notifications", title: "Notifications" },
  external_ai: { key: "external_ai", title: "External AI" },
};

type Scope = "global" | "workspace";

/** The platform owns one group; each module with declared settings or a management page owns one more. The
    workspace side lists only declared settings: management pages (sources, connections, keys) are global. */
function navigation(sections: Section[], names: Map<string, string>, scope: Scope): { groups: NavGroup[]; titles: Map<string, string> } {
  const titles = new Map<string, string>();
  const groups: NavGroup[] = [{ key: "platform", label: "Platform", items: scope === "global"
    ? [platformItems.general, platformItems.notifications, platformItems.external_ai] : [platformItems.general] }];
  const owned = new Map<string, Section[]>();
  for (const moduleId of [...names.keys()].sort((a, b) => names.get(a)!.localeCompare(names.get(b)!))) {
    if (moduleId !== "platform" && declaredSettings(moduleId).length) owned.set(moduleId, []);
  }
  for (const section of sections) owned.set(section.moduleId, [...owned.get(section.moduleId) ?? [], section]);
  for (const [moduleId, entries] of owned) {
    const items = [
      ...declaredSettings(moduleId).length ? [{ key: `settings:${moduleId}`, title: "General" }] : [],
      ...entries.filter(section => scope === "global" || section.workspace).map(section => ({ key: section.key, title: section.title })),
    ];
    groups.push({ key: `module:${moduleId}`, label: names.get(moduleId) ?? moduleId, items });
    titles.set(`settings:${moduleId}`, names.get(moduleId) ?? moduleId);
  }
  for (const section of sections) if (!titles.has(section.key)) titles.set(section.key, section.title);
  for (const entry of Object.values(platformItems)) titles.set(entry.key, entry.title);
  return { groups: groups.filter(group => group.items.length), titles };
}

const slug = (text: string) => text.toLowerCase().replace(/[^a-z0-9]+/g, "-");

/** The workspace this window shows, if any: a workspace frame's own, or the one the top window has in front.
    `undefined` while it is still being read. */
function useCurrentWorkspace(open: boolean): { id: string; name: string } | null | undefined {
  const [workspace, setWorkspace] = useState<{ id: string; name: string } | null | undefined>(undefined);
  useEffect(() => {
    if (!open) return;
    let live = true;
    const read = () => {
      const id = workspaceFrameId() ?? (window.top === window ? new URL(location.href).searchParams.get("workspaceId") : null);
      if (!id) { setWorkspace(null); return; }
      void workspaceWindowCommand({ kind: "list" }).then(records => {
        const name = records.find(record => record.workspaceId === id)?.name ?? "Untitled";
        if (live) setWorkspace(current => current?.id === id && current.name === name ? current : { id, name });
      }).catch(() => { if (live) setWorkspace(null); });
    };
    read();
    // The workspace frame may still be loading when the window opens with the page.
    const timer = window.setInterval(read, 1500);
    return () => { live = false; window.clearInterval(timer); };
  }, [open]);
  return workspace;
}

function SettingsDialog({ open, onOpenChange, selection, onSelectionChange }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  selection: string | null;
  onSelectionChange: (selection: string) => void;
}) {
  const kernel = useKernelChange();
  const desktop = useDesktop();
  const [requested, setRequested] = useState<string | null>(selection);
  const [exitError, setExitError] = useState<string | null>(null);
  const [scope, setScope] = useState<Scope>("global");
  const workspace = useCurrentWorkspace(open);
  const attention = attentionItems();
  const sections = kernel.contributions<PlatformTypes["point:settings_section"]>("platform.settings_section").map((item): Section =>
    ({ moduleId: item.moduleId, key: `${item.moduleId}.${item.id}`, title: item.descriptor.title, view: `${item.moduleId}.${item.descriptor.view}`,
      workspace: item.descriptor.workspace === true }));
  const names = new Map([...kernel.definitions.values()].map(item => [item.id, item.name]));
  const { groups, titles } = navigation(sections, names, scope);
  const selected = requested ?? "general";
  // A module id or name ("my-module") opens that module's first section, as Installed links to it.
  const settingsOf = selected.startsWith("settings:") ? selected.slice("settings:".length)
    : [...names.keys()].find(id => id !== "platform" && (id === selected || slug(id) === selected || slug(names.get(id) ?? "") === selected) && declaredSettings(id).length);
  const visibleSections = sections.filter(section => scope === "global" || section.workspace);
  const current = settingsOf ? undefined : visibleSections.find(item => item.key === selected)
    ?? visibleSections.find(item => item.moduleId === selected || slug(item.moduleId) === selected || slug(names.get(item.moduleId) ?? "") === selected);
  const ctx = current ? kernel.modules.get(current.moduleId)?.ctx : undefined;
  const activeKey = current?.key ?? (settingsOf ? `settings:${settingsOf}`
    : scope === "global" && selected in platformItems ? selected : "general");
  const heading = titles.get(activeKey)!;
  const layer = scope === "global" ? "global" : workspace?.id;

  useEffect(() => { if (open) setRequested(selection); }, [open, selection]);
  useEffect(() => { if (open) setScope("global"); }, [open]);
  // The window opens with the page in focus (arrow keys scroll it), not with a ring on the first control.
  const pane = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!open) return;
    const timer = window.setTimeout(() => pane.current?.focus({ preventScroll: true }));
    return () => window.clearTimeout(timer);
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const page = layer === undefined ? null
    : current ? ctx && <ModuleView key={layer} id={current.view} props={current.workspace ? {scope} : {} as Json} context={ctx} />
    : settingsOf ? <TxSettingsPage><DeclaredSettings key={layer} moduleId={settingsOf} workspaceId={layer} /></TxSettingsPage>
    : activeKey === "external_ai" ? <ExternalAiSection />
    : activeKey === "notifications" ? <NotificationsSection />
    : <GeneralSection key={layer} attention={attention} workspaceId={layer} />;

  return <TxDialog open={open} onOpenChange={onOpenChange} title="Settings" size="wide" className="talyx-settings" footer={<></>}>
    <div className="talyx-settings-layout">
      <nav className="talyx-settings-nav" aria-label="Settings sections">
        <div className="talyx-settings-nav-title" aria-hidden="true">Settings</div>
        <div className="talyx-settings-scope">
          <TxSegmented label="Scope" size="sm" value={scope} onChange={setScope} options={[
            { value: "global", label: "Global" },
            { value: "workspace", label: "This workspace" },
          ]} />
        </div>
        {scope === "global" || workspace ? groups.map(group => <div key={group.key} className="talyx-settings-group">
          {group.label && <div className="talyx-settings-group-label">{group.label}</div>}
          {group.items.map(item => <TxButton key={item.key} variant="row" className="talyx-settings-link"
            aria-current={item.key === activeKey ? "page" : undefined} onClick={() => { setRequested(item.key); onSelectionChange(item.key); }}>
            <span>{item.title}</span>
          </TxButton>)}
        </div>) : null}
        {desktop && <div className="talyx-settings-nav-foot">
          {exitError && <span className="tx-error" role="alert">{exitError}</span>}
          <TxButton variant="row" className="talyx-settings-link" onClick={() => {
            setExitError(null);
            // Exit takes the tray's path, so running work is listed before anything stops.
            desktop.requestExit().catch(reason => setExitError(`TALYX could not exit: ${reason instanceof Error ? reason.message : String(reason)}`));
          }}><span>Exit TALYX</span></TxButton>
        </div>}
      </nav>
      <div className="talyx-settings-main">
        {/* Keyed by section so each page opens at its top. */}
        <section key={`${scope}:${activeKey}`} ref={pane} tabIndex={-1} className="talyx-settings-pane" data-settings-section={activeKey}
          data-settings-scope={scope} aria-label={heading}>
          <div className="talyx-settings-page">
            {scope === "global" || workspace
              ? <><header className="talyx-settings-head"><h3 className="talyx-settings-heading">{heading}</h3></header>{page}</>
              : workspace === null && <div className="talyx-settings-blank"><TxEmpty title="No workspace is open" /></div>}
          </div>
        </section>
      </div>
      <TxIconButton className="talyx-settings-close" label="Close settings" icon="close" onClick={() => onOpenChange(false)} />
    </div>
  </TxDialog>;
}

export function PlatformControls() {
  const [open, setOpen] = useState(false);
  const [selection, setSelection] = useState<string | null>(null);
  useSettingsRequest(section => { setSelection(section); setOpen(true); });
  // The provider lives inside the component so a caller cannot mount the
  // controls without notification support.
  return <TxToastProvider>
    <TxTooltipProvider>
      <TxRoot className="talyx-platform-controls" data-platform-controls="">
        <TxTooltip label="Settings">
          <TxIconButton label="Settings" icon="settings" onClick={() => { setSelection(null); setOpen(true); }} />
        </TxTooltip>
      </TxRoot>
      <SettingsDialog open={open} onOpenChange={setOpen} selection={selection} onSelectionChange={setSelection} />
    </TxTooltipProvider>
  </TxToastProvider>;
}

/** Opens settings programmatically, the same path the toolbar button uses. */
export function openPlatformSettings(section?: string): void {
  window.dispatchEvent(new CustomEvent("talyx:settings", { detail: section ?? null }));
}

