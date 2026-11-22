import { t } from "./i18n";
import { type PointerEvent, type ReactNode, type WheelEvent, useLayoutEffect, useRef, useState } from "react";
import { TxIcon, TxMenu, TxTooltip, TxTooltipProvider, type TxMenuItem } from "@talyx/sdk";
import { PlatformControls } from "./PlatformControls";
import { UpdateNotice } from "./UpdateNotice";
import { useDesktop, useWindowMaximized, type ShellAttention, type ShellWorkspaceTab } from "./shellChrome";
import type { TalyxDesktop } from "../../../host/desktop/preload";
import logoMark from "./assets/talyx-logo-round.png";
import "./WorkspaceShell.css";
import "./ShellTopBar.css";

/* The top bar. In a workspace it is the window's title bar: the TALYX mark, the open workspaces, "+", the settings
   gear, and the window's own minimize, maximize and close. Home, Workspaces and Community have the same bar
   with the three places instead. Parts come from the component library; the capsules reuse the library's pill switch
   (its classes and values) because the workspaces also carry a state mark, which the library part does not. */

/** The TALYX mark. Hovering it opens the library menu with Home and Community; a module that couldn't start lights
    an orange dot on the mark and gets its own row, which lands on that module in Community's Installed. */
function ShellLogo({ attention, places, onHome, onCommunity, onFault }: {
  attention: ShellAttention[];
  /** Home and Community rows; the Home bar already has them in its navigation. */
  places: boolean;
  onHome: () => void;
  onCommunity: () => void;
  onFault: (item: ShellAttention) => void;
}) {
  const failing = attention.length > 0;
  const mark = <span className="talyx-logo" data-fault={failing || undefined}
    aria-label={failing ? t("TALYX, a module couldn't start") : "TALYX"} role={places || failing ? undefined : "img"}>
    <img src={logoMark} alt="" draggable={false} />
    {failing && <span className="talyx-logo-dot" aria-hidden="true" />}
  </span>;
  const faults: TxMenuItem[] = attention.slice(0, 3).map((item, index) => ({
    id: `fault-${item.id}`, label: t("{title} couldn't start", { title: item.title }), tone: "danger",
    separated: places && index === 0, onSelect: () => onFault(item),
  }));
  const items: TxMenuItem[] = [
    ...(places ? [{ id: "home", label: t("Home"), onSelect: onHome }, { id: "community", label: t("Community"), onSelect: onCommunity }] : []),
    ...faults,
  ];
  if (!items.length) return mark;
  return <TxMenu label="TALYX" openOnHover items={items}
    trigger={<button type="button" className="talyx-logo-trigger" aria-label={failing ? t("TALYX, a module couldn't start") : "TALYX"}>{mark}</button>} />;
}


/** Slides the workspaces sideways when they don't fit, without a native scroll (so no scroll bar or scroll hint shows);
    the clipped side fades. */
function useSlidingRow(currentId: string | null, count: number) {
  const clip = useRef<HTMLDivElement>(null), track = useRef<HTMLDivElement>(null);
  const [offset, setOffset] = useState(0);
  const [room, setRoom] = useState(0);
  useLayoutEffect(() => {
    const measure = () => setRoom(Math.max(0, (track.current?.scrollWidth ?? 0) - (clip.current?.clientWidth ?? 0)));
    measure();
    const observer = new ResizeObserver(measure);
    if (clip.current) observer.observe(clip.current);
    if (track.current) observer.observe(track.current);
    return () => observer.disconnect();
  }, [count]);
  // Keeps the current workspace in view.
  useLayoutEffect(() => {
    const button = track.current?.querySelector<HTMLElement>("[aria-pressed='true']"), width = clip.current?.clientWidth ?? 0;
    if (!button || !width) return;
    setOffset(value => Math.min(Math.max(value, button.offsetLeft + button.offsetWidth - width), button.offsetLeft));
  }, [currentId, room]);
  const shown = Math.min(Math.max(offset, 0), room);
  return {
    clip, track, offset: shown,
    fade: room > 0 ? `${shown > 0 ? "start" : ""} ${shown < room ? "end" : ""}`.trim() || undefined : undefined,
    onWheel: (event: WheelEvent<HTMLDivElement>) => { const delta = event.deltaX || event.deltaY; if (delta && room) setOffset(Math.min(Math.max(shown + delta, 0), room)); },
  };
}

/** One workspace in the row. The tooltip only says something the tab can't: the whole name when it is cut, or what
    its mark means. */
function WorkspaceTab({ tab, current, onSelect, onMenu }: {
  tab: ShellWorkspaceTab;
  current: boolean;
  onSelect: (tab: ShellWorkspaceTab) => void;
  onMenu?: (anchor: HTMLElement) => void;
}) {
  const name = useRef<HTMLSpanElement>(null);
  const [cut, setCut] = useState(false);
  useLayoutEffect(() => { const span = name.current; setCut(Boolean(span && span.scrollWidth > span.clientWidth)); }, [tab.name, current]);
  const label = tab.running ? t("{name} · Running", { name: tab.name }) : tab.name;
  const button = <button type="button" className="talyx-ws-tab" aria-pressed={current} aria-haspopup={current && onMenu ? "menu" : undefined}
    aria-label={label}
    onClick={event => { if (current) onMenu?.(event.currentTarget); else onSelect(tab); }}
    onContextMenu={event => { if (!current || !onMenu) return; event.preventDefault(); onMenu(event.currentTarget); }}>
    <span ref={name} className="talyx-ws-name">{tab.name}</span>
    {tab.running && <span className="talyx-ws-mark" data-status="running" aria-hidden="true" />}
    {current && onMenu && <TxIcon name="chevron-down" className="talyx-ws-caret" />}
  </button>;
  return tab.running || cut ? <TxTooltip label={label}>{button}</TxTooltip> : button;
}

/** The open workspaces as the library's pill switch, "+" its last item. The current one opens its menu; a name is
    followed by a spinner while something runs there. */
function WorkspaceRow({ tabs, currentId, onSelect, onMenu, add }: {
  tabs: ShellWorkspaceTab[];
  currentId: string | null;
  onSelect: (tab: ShellWorkspaceTab) => void;
  onMenu?: (anchor: HTMLElement) => void;
  add?: ReactNode;
}) {
  const row = useSlidingRow(currentId, tabs.length);
  return <div className="tx-seg talyx-capsule talyx-ws-row" role="group" aria-label={t("Open workspaces")} data-size="md" data-variant="pill">
    <div ref={row.clip} className="talyx-ws-clip" data-fade={row.fade} onWheel={row.onWheel}>
      <div ref={row.track} className="talyx-ws-track" style={{ transform: `translateX(${-row.offset}px)` }}>
    {tabs.map(tab => <WorkspaceTab key={tab.id} tab={tab} current={tab.id === currentId} onSelect={onSelect} onMenu={onMenu} />)}
      </div>
    </div>
    {add}
  </div>;
}

/** Minimize, maximize (restore), close. The desktop window has no system title bar; these are the shell's own. Close
    is the system's close: the window goes to the tray. */
function WindowControls({ desktop, maximized }: { desktop: TalyxDesktop; maximized: boolean }) {
  return <div className="talyx-window-controls" role="group" aria-label={t("Window")}>
    <button type="button" className="talyx-window-btn" aria-label={t("Minimize")} title={t("Minimize")} onClick={() => void desktop.minimizeWindow()}>
      <svg viewBox="0 0 10 10" aria-hidden="true"><path d="M0 5.5h10" /></svg>
    </button>
    <button type="button" className="talyx-window-btn" aria-label={maximized ? t("Restore") : t("Maximize")} title={maximized ? t("Restore") : t("Maximize")}
      onClick={() => void desktop.toggleMaximizeWindow()}>
      {maximized
        ? <svg viewBox="0 0 10 10" aria-hidden="true"><rect x=".5" y="2.5" width="7" height="7" rx="1" /><path d="M2.5 2.5V1.5a1 1 0 0 1 1-1h5a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1h-1" /></svg>
        : <svg viewBox="0 0 10 10" aria-hidden="true"><rect x=".5" y=".5" width="9" height="9" rx="1" /></svg>}
    </button>
    <button type="button" className="talyx-window-btn" data-close="" aria-label={t("Close")} title={t("Close")} onClick={() => void desktop.closeWindow()}>
      <svg viewBox="0 0 10 10" aria-hidden="true"><path d="M.5.5l9 9M9.5.5l-9 9" /></svg>
    </button>
  </div>;
}

/** Settings, with an orange dot while a module reports an error (listed in Settings under "Needs attention"). */
function SettingsGear({ alert }: { alert: boolean }) {
  return <span className="talyx-gear" data-alert={alert || undefined}>
    <PlatformControls />
    {alert && <span className="talyx-gear-dot" aria-hidden="true" />}
  </span>;
}

function BarEnd({ settingsAlert, desktop, maximized }: { settingsAlert: boolean; desktop: TalyxDesktop | undefined; maximized: boolean }) {
  return <div className="talyx-topbar-end">
    <UpdateNotice />
    <SettingsGear alert={settingsAlert} />
    {desktop && <>
      <span className="talyx-topbar-divider" aria-hidden="true" />
      <WindowControls desktop={desktop} maximized={maximized} />
    </>}
  </div>;
}

/** On the desktop the bar is the window's title bar: pressing its empty space moves the window (Windows snaps it at
    the screen's edges), a double press maximizes or restores, and its top edge resizes the window. */
function useTitleBar(desktop: TalyxDesktop | undefined) {
  const last = useRef({ time: 0, x: 0, y: 0 });
  const onPointerDown = (event: PointerEvent<HTMLElement>) => {
    if (!desktop || event.button !== 0 || !(event.target instanceof Element)) return;
    if (event.target.closest("button, a, input, [role='group'], [role='menu'], .talyx-platform-controls, .talyx-window-edge")) return;
    const now = { time: event.timeStamp, x: event.clientX, y: event.clientY };
    const double = now.time - last.current.time < 500 && Math.abs(now.x - last.current.x) < 6 && Math.abs(now.y - last.current.y) < 6;
    last.current = double ? { time: 0, x: 0, y: 0 } : now;
    event.preventDefault();
    void (double ? desktop.toggleMaximizeWindow() : desktop.dragWindow());
  };
  return onPointerDown;
}

/** The window's top edge and corners, above the bar; a press there resizes the window. Not while maximized. */
function WindowEdges({ desktop }: { desktop: TalyxDesktop }) {
  return <>{(["top-left", "top", "top-right"] as const).map(edge => <span key={edge} className="talyx-window-edge" data-edge={edge} aria-hidden="true"
    onPointerDown={event => { if (event.button === 0) { event.preventDefault(); void desktop.resizeWindow(edge); } }} />)}</>;
}

/** The workspace's top bar. */
export function ShellTopBar({ tabs, currentId, attention, settingsAlert, onHome, onCommunity, onFault, onSelectWorkspace, onWorkspaceMenu, addWorkspace }: {
  tabs: ShellWorkspaceTab[];
  currentId: string | null;
  attention: ShellAttention[];
  settingsAlert: boolean;
  onHome: () => void;
  onCommunity: () => void;
  onFault: (item: ShellAttention) => void;
  onSelectWorkspace: (tab: ShellWorkspaceTab) => void;
  /** Opens the current workspace's menu under its tab. */
  onWorkspaceMenu?: (anchor: HTMLElement) => void;
  /** The "+" after the workspaces: a new workspace or a saved one. */
  addWorkspace?: ReactNode;
}) {
  const desktop = useDesktop();
  const maximized = useWindowMaximized(desktop);
  const onPointerDown = useTitleBar(desktop);
  return <TxTooltipProvider>
    <header className="talyx-topbar" onPointerDown={onPointerDown}>
      {desktop && !maximized && <WindowEdges desktop={desktop} />}
      <ShellLogo attention={attention} places onHome={onHome} onCommunity={onCommunity} onFault={onFault} />
      <nav className="talyx-topbar-workspaces" aria-label={t("Workspaces")}>
        <WorkspaceRow tabs={tabs} currentId={currentId} onSelect={onSelectWorkspace} onMenu={onWorkspaceMenu} add={addWorkspace} />
      </nav>
      <div className="talyx-topbar-drag" aria-hidden="true" />
      <BarEnd settingsAlert={settingsAlert} desktop={desktop} maximized={maximized} />
    </header>
  </TxTooltipProvider>;
}

export type HomeBarTarget = "home" | "workspace" | "community";

/** Home, Workspaces and Community, and News as a place that isn't open yet. The same bar as a workspace's (height, mark, edges, right end), so going
    into a workspace doesn't move it; the places are the same glass capsule, centered. */
export function ShellHomeBar({ active, attention, settingsAlert, onNavigate, onFault }: {
  active: HomeBarTarget;
  attention: ShellAttention[];
  settingsAlert: boolean;
  onNavigate: (target: HomeBarTarget) => void;
  onFault: (item: ShellAttention) => void;
}) {
  const desktop = useDesktop();
  const maximized = useWindowMaximized(desktop);
  const onPointerDown = useTitleBar(desktop);
  return <TxTooltipProvider>
    <header className="talyx-topbar" data-place="home" onPointerDown={onPointerDown}>
      {desktop && !maximized && <WindowEdges desktop={desktop} />}
      <div className="talyx-brand">
        <ShellLogo attention={attention} places={false} onHome={() => onNavigate("home")} onCommunity={() => onNavigate("community")} onFault={onFault} />
        <span className="talyx-wordmark">TALYX</span>
      </div>
      <nav className="tx-seg talyx-capsule talyx-places" aria-label={t("Primary navigation")} data-size="md" data-variant="pill">
        {([["home", t("Home")], ["workspace", t("Workspace")], ["community", t("Community")]] as const).map(([target, label]) =>
          <button type="button" key={target}
            aria-pressed={active === target} aria-current={active === target ? "page" : undefined} onClick={() => onNavigate(target)}>{label}</button>)}
        <button type="button" disabled>{t("News")}</button>
      </nav>
      <BarEnd settingsAlert={settingsAlert} desktop={desktop} maximized={maximized} />
    </header>
  </TxTooltipProvider>;
}