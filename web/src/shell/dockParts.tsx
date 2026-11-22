import { t } from "./i18n";
import { displayedPanelTitle } from "./workspaceSummary";
import { TxContextMenu, TxIcon, type TxMenuItem } from "@talyx/sdk";
import { createContext, type CSSProperties, type FunctionComponent, type ReactNode, useContext, useEffect, useState } from "react";
import type { DockviewApi, DockviewTheme, IDockviewHeaderActionsProps, IDockviewPanel, IDockviewPanelHeaderProps, IDockviewPanelProps } from "dockview-react";
import type { ShellPanel } from "./workspaceChanges";
import { ModuleIcon } from "./ModuleIcon";
import { DockEmptyState } from "./ShellMenus";
import type { ShellModuleChoice } from "./shellChrome";

/* What the dock draws: a panel's tab, the group's actions, the panel's surface, and the empty workspace. */

interface DockPanelParams {
  panel: ShellPanel;
}

export interface DockPreviewContextValue {
  renderModule?: (panel: ShellPanel) => ReactNode;
  onAddModule: (anchor?: HTMLElement | null) => void;
  onRequestRemovePanel: (id: string) => boolean | Promise<boolean>;
  activePanelId: string | null;
  panels: ShellPanel[];
  modules: ShellModuleChoice[];
  onAddModuleKind?: (module: ShellModuleChoice) => void;
}

export const DockPreviewContext = createContext<DockPreviewContextValue | null>(null);

/** Whether the panel a module draws is on screen and the current one. */
export const ModulePresentationContext = createContext<{ visible: boolean; active: boolean }>({ visible: true, active: false });

/* Groups meet on a 1px line (the separator); the sashes only light up while hovered or dragged. Dockview's own drop
   overlays are never shown: the frame draws the one place a drop lands (see the drag below). */
export const talyxDockTheme: DockviewTheme = {
  name: "talyx",
  className: "talyx-dock-theme",
  colorScheme: "dark",
  gap: 0,
  dndOverlayMounting: "absolute",
  dndPanelOverlay: "group",
  dndTabIndicator: "line",
  dndOverlayBorder: "none",
  tabGroupIndicator: "none",
  tabAnimation: "default",
};

// Tabs have no context menu. One function for the life of the page: a new one on each render makes dockview apply its options again and lay out the whole dock.
export const noTabMenu = () => [];


/** Redraws a tab when panels join, leave or move, its title changes, or a group is maximized or restored. */
function useDockRevision(api: IDockviewPanelHeaderProps["api"], containerApi: DockviewApi) {
  const [, redraw] = useState(0);
  useEffect(() => {
    const bump = () => redraw(value => value + 1);
    const disposables = [containerApi.onDidAddPanel(bump), containerApi.onDidRemovePanel(bump), containerApi.onDidMovePanel(bump),
      containerApi.onDidMaximizedGroupChange(bump), api.onDidTitleChange(bump)];
    return () => disposables.forEach(item => item.dispose());
  }, [api, containerApi]);
}

function toggleMaximized(panel: { isMaximized(): boolean; maximize(): void; exitMaximized(): void }) {
  if (panel.isMaximized()) panel.exitMaximized(); else panel.maximize();
}

async function closePanels(context: DockPreviewContextValue | null, panels: readonly IDockviewPanel[]) {
  for (const panel of panels) {
    if (!context || !(await context.onRequestRemovePanel(panel.id))) return;
    panel.api.close();
  }
}

/** A panel's tab: its module's icon and its name; close shows on hover and on the current tab. Double-click maximizes
    and restores; right-click has close, close others and maximize. */
export function DockModuleTab({ params, api, containerApi }: IDockviewPanelHeaderProps) {
  const context = useContext(DockPreviewContext);
  const { panel } = params as DockPanelParams;
  useDockRevision(api, containerApi);
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const title = api.title ?? displayedPanelTitle(panel);
  const live = containerApi.getPanel(panel.id);
  const maximized = api.isMaximized();
  const others = live ? live.group.panels.filter(item => item.id !== panel.id) : [];
  const items: TxMenuItem[] = [
    { id: "close", label: t("Close"), icon: "close", onSelect: () => { if (live) void closePanels(context, [live]); } },
    { id: "close-others", label: t("Close others"), disabled: others.length === 0, onSelect: () => { void closePanels(context, others); } },
    { id: "maximize", label: maximized ? t("Restore") : t("Maximize"), icon: maximized ? "restore" : "maximize", separated: true, onSelect: () => toggleMaximized(api) },
  ];
  return <>
    <div className="talyx-tab" title={title}
      onDoubleClick={() => toggleMaximized(api)}
      onContextMenu={event => { event.preventDefault(); setMenu({ x: event.clientX, y: event.clientY }); }}
      onMouseDown={event => { if (event.button === 1 && live) { event.preventDefault(); void closePanels(context, [live]); } }}>
      <ModuleIcon module={panel.kind} className="talyx-tab-icon" />
      <span className="talyx-tab-title">{title}</span>
      <button className="talyx-tab-close" type="button" aria-label={t("Close {title}", { title })} title={t("Close")}
        onPointerDown={event => event.stopPropagation()}
        onDoubleClick={event => event.stopPropagation()}
        onClick={event => { event.stopPropagation(); if (live) void closePanels(context, [live]); }}>
        <TxIcon name="close" />
      </button>
    </div>
    <TxContextMenu open={menu !== null} onOpenChange={open => { if (!open) setMenu(null); }} point={menu ?? { x: 0, y: 0 }} label={t("{title} panel", { title })} items={items} />
  </>;
}

/** Right after the tabs: add a module to this group. Shows while the tab row is hovered. */
export function DockGroupAdd({ activePanel }: IDockviewHeaderActionsProps) {
  const context = useContext(DockPreviewContext);
  return <button type="button" className="talyx-group-add" aria-label={t("Add module here")} title={t("Add module")}
    onClick={event => { activePanel?.api.setActive(); context?.onAddModule(event.currentTarget); }}><TxIcon name="plus" /></button>;
}

/** At the row's end while the group is maximized: restore. */
export function DockGroupRestore({ api, containerApi }: IDockviewHeaderActionsProps) {
  const [, redraw] = useState(0);
  useEffect(() => {
    const disposable = containerApi.onDidMaximizedGroupChange(() => redraw(value => value + 1));
    return () => disposable.dispose();
  }, [containerApi]);
  if (!api.isMaximized()) return null;
  return <button type="button" className="talyx-group-restore" aria-label={t("Restore")} title={t("Restore")} onClick={() => api.exitMaximized()}><TxIcon name="restore" /></button>;
}

export function dockModuleSurfaceStyle(isVisible: boolean): CSSProperties | undefined {
  // Dockview hides inactive tab contents with `visibility:hidden`. A drawing library may set
  // `visibility:visible` on its own canvases, which is allowed to override an
  // inherited hidden value and can paint an inactive panel over the active one.
  // Removing the inactive surface from layout is the only reliable descendant-safe
  // boundary; ResizeObserver repaints such a canvas when the panel becomes visible again.
  return isVisible ? undefined : { display: "none" };
}

function DockModuleSurface({ params, api }: IDockviewPanelProps) {
  const context = useContext(DockPreviewContext);
  const { panel } = params as DockPanelParams;
  const rendered = context?.renderModule?.(panel);
  const [isVisible, setIsVisible] = useState(api.isVisible);

  useEffect(() => {
    setIsVisible(api.isVisible);
    const disposable = api.onDidVisibilityChange((event) => {
      setIsVisible(event.isVisible);
    });
    return () => disposable.dispose();
  }, [api]);

  if (rendered) {
    return (
      <ModulePresentationContext.Provider value={{
        visible: isVisible,
        active: context?.activePanelId === panel.id,
      }}>
        <div
          aria-hidden={!isVisible}
          className="talyx-dock-module-live"
          data-dock-panel-visible={isVisible ? "true" : "false"}
          data-dock-panel-active={context?.activePanelId === panel.id ? "true" : "false"}
          onPointerDown={() => api.setActive()}
          onFocusCapture={() => api.setActive()}
          style={dockModuleSurfaceStyle(isVisible)}
        >
          {rendered}
        </div>
      </ModulePresentationContext.Provider>
    );
  }

  return (
    <section
      aria-hidden={!isVisible}
      className="talyx-dock-module-live"
      aria-label={displayedPanelTitle(panel)}
      data-dock-panel-visible={isVisible ? "true" : "false"}
      onPointerDown={() => api.setActive()}
      onFocusCapture={() => api.setActive()}
      style={dockModuleSurfaceStyle(isVisible)}
    />
  );
}

export function DockEmptyWatermark() {
  const context = useContext(DockPreviewContext);
  return <DockEmptyState modules={context?.modules ?? []} onPick={module => context?.onAddModuleKind?.(module)} />;
}

export const dockComponents: Record<string, FunctionComponent<IDockviewPanelProps>> = {
  module: DockModuleSurface,
};
