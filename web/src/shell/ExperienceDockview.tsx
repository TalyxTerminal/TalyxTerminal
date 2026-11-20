import { TxTooltipProvider } from "@talyx/sdk";
import { type ReactNode, useCallback, useEffect, useRef } from "react";
import { type DockviewGroupPanel, DockviewReact, type DockviewApi, type DockviewReadyEvent, type SerializedDockview } from "dockview-react";
import "dockview-react/dist/styles/dockview.css";
import "./ExperienceDockview.css";

import type { ShellPanel } from "./workspaceChanges";
import type { ShellModuleChoice } from "./shellChrome";
import { sameArrangement, type DropPlan, type DropTarget } from "./dockLayout";
import { arrangement, FREE, planFor, useDividers, usePanelDrag, withoutDeclaredMinimums } from "./dockDrag";
import { DockEmptyWatermark, DockGroupAdd, DockGroupRestore, DockModuleTab, DockPreviewContext, dockComponents, noTabMenu, talyxDockTheme } from "./dockParts";

interface ExperienceDockviewProps {
  panels: ShellPanel[];
  activePanelId: string | null;
  initialLayout: SerializedDockview | null;
  renderModule?: (panel: ShellPanel) => ReactNode;
  onActivatePanel: (id: string) => void;
  onRemovePanel: (id: string) => void;
  onRequestRemovePanel: (id: string) => boolean | Promise<boolean>;
  onLayoutChange: (layout: SerializedDockview, source?: "panel-sync") => void;
  registerLayoutReader?: (reader: (() => SerializedDockview) | null) => void;
  onAddModule: (anchor?: HTMLElement | null) => void;
  /** What an empty workspace offers to start with. */
  modules?: ShellModuleChoice[];
  onAddModuleKind?: (module: ShellModuleChoice) => void;
}

/* The workspace's panels in a dock: groups meet on a 1px line; the layout only scales with the window; a drop lands
   where its one highlight was drawn (dockDrag.ts); the tabs and surfaces are dockParts.tsx. */

/** Narrowest half a newly opened panel splits a group into; a narrower split opens it as a tab there instead. */
const OPEN_MIN_WIDTH = 360;

function addInitialPanels(api: DockviewApi, panels: ShellPanel[]) {
  let previousId: string | undefined;
  panels.forEach((panel, index) => {
    const referencePanel = index === 2 ? panels[1]?.id : previousId;
    const direction = index === 2 ? "below" : "right";
    api.addPanel({
      id: panel.id,
      component: "module",
      title: panel.title,
      params: { panel },
      position: referencePanel ? { referencePanel, direction } : undefined,
    });
    previousId = panel.id;
  });
}

export function ExperienceDockview({
  panels,
  activePanelId,
  initialLayout,
  renderModule,
  onActivatePanel,
  onRemovePanel,
  onRequestRemovePanel,
  onLayoutChange,
  registerLayoutReader,
  onAddModule,
  modules = [],
  onAddModuleKind,
}: ExperienceDockviewProps) {
  const apiRef = useRef<DockviewApi | null>(null);
  const disposablesRef = useRef<Array<{ dispose: () => void }>>([]);
  const layoutTimerRef = useRef<number | null>(null);
  const settleTimerRef = useRef<number | null>(null);
  /** The arrangement last published: the one the workspace keeps and saves. Resizing the window only scales it. */
  const durableLayoutRef = useRef<SerializedDockview | null>(initialLayout);
  const panelIdsFingerprintRef = useRef(panels.map((panel) => panel.id).join("\u0000"));
  const hostRef = useRef<HTMLDivElement>(null);
  const syncingRef = useRef(false);
  const hydratedRef = useRef(false);
  const panelsRef = useRef(panels);
  const activePanelIdRef = useRef(activePanelId);
  const initialLayoutRef = useRef(initialLayout);
  const onActivatePanelRef = useRef(onActivatePanel);
  const onRemovePanelRef = useRef(onRemovePanel);
  const onLayoutChangeRef = useRef(onLayoutChange);
  const registerLayoutReaderRef = useRef(registerLayoutReader);

  panelsRef.current = panels;
  activePanelIdRef.current = activePanelId;
  onActivatePanelRef.current = onActivatePanel;
  onRemovePanelRef.current = onRemovePanel;
  onLayoutChangeRef.current = onLayoutChange;
  registerLayoutReaderRef.current = registerLayoutReader;


  /** Publishes the live arrangement when it is a real rearrangement, not the same one at another window size. */
  const publishLayout = useCallback((api: DockviewApi, source?: "panel-sync") => {
    const snapshot = api.toJSON();
    const previous = durableLayoutRef.current;
    if (previous && sameArrangement(previous, snapshot)) return;
    durableLayoutRef.current = snapshot;
    onLayoutChangeRef.current(snapshot, source);
  }, []);

  const scheduleLayoutSnapshot = useCallback((api: DockviewApi) => {
    if (syncingRef.current || !hydratedRef.current) return;
    if (layoutTimerRef.current != null) window.clearTimeout(layoutTimerRef.current);
    layoutTimerRef.current = window.setTimeout(() => publishLayout(api), 120);
  }, [publishLayout]);

  const settleDrop = useCallback((target: HTMLElement) => {
    if (settleTimerRef.current != null) window.clearTimeout(settleTimerRef.current);
    target.classList.remove("talyx-dock-drop-settling");
    window.requestAnimationFrame(() => {
      target.classList.add("talyx-dock-drop-settling");
      settleTimerRef.current = window.setTimeout(() => target.classList.remove("talyx-dock-drop-settling"), 180);
    });
  }, []);

  /** Installs a planned arrangement, keeping every mounted panel. */
  const installArrangement = useCallback((api: DockviewApi, planned: SerializedDockview, activeId: string | null) => {
    const live = api.toJSON();
    syncingRef.current = true;
    try {
      api.fromJSON(withoutDeclaredMinimums({ ...planned, panels: live.panels }), { reuseExistingPanels: true });
      (activeId ? api.getPanel(activeId) : undefined)?.api.setActive();
    } finally {
      syncingRef.current = false;
    }
  }, []);

  /** A drop lands where the highlight was drawn. */
  const land = useCallback((api: DockviewApi, plan: DropPlan, activeId: string | null) => {
    installArrangement(api, plan.layout, activeId);
    publishLayout(api);
    const landed = api.groups.find(item => item.id === plan.groupId);
    if (landed) settleDrop(landed.element);
  }, [installArrangement, publishLayout, settleDrop]);
  const { hint, beginDrag } = usePanelDrag(hostRef, land);
  useDividers(hostRef, apiRef);

  const onReady = useCallback((event: DockviewReadyEvent) => {
    const api = event.api;
    apiRef.current = api;
    registerLayoutReaderRef.current?.(() => durableLayoutRef.current ?? api.toJSON());
    syncingRef.current = true;
    disposablesRef.current.push(api.onDidAddGroup(group => group.api.setConstraints(FREE)));

    const layout = initialLayoutRef.current;
    if (layout) {
      try {
        api.fromJSON(withoutDeclaredMinimums(layout), { reuseExistingPanels: false });
      } catch {
        api.clear();
        addInitialPanels(api, panelsRef.current);
      }
    } else {
      addInitialPanels(api, panelsRef.current);
    }
    for (const group of api.groups) group.api.setConstraints(FREE);

    const active = panelsRef.current.find((panel) => panel.id === activePanelIdRef.current);
    (active ? api.getPanel(active.id) : api.panels[0])?.api.setActive();
    syncingRef.current = false;
    hydratedRef.current = true;
    // The opened arrangement is the kept one, even when this window draws it at another size.
    durableLayoutRef.current = layout ?? api.toJSON();

    disposablesRef.current.push(
      api.onDidActivePanelChange(({ panel }) => {
        if (panel) onActivatePanelRef.current(panel.id);
      }),
      api.onDidRemovePanel((panel) => {
        if (!syncingRef.current) onRemovePanelRef.current(panel.id);
      }),
      api.onWillDragPanel(({ panel }) => beginDrag(api, { groupId: panel.group.id, panelId: panel.id }, panel.api.getWindow())),
      api.onWillDragGroup(({ group }) => beginDrag(api, { groupId: group.id, panelId: null }, group.api.getWindow())),
      api.onWillShowOverlay((dragEvent) => {
        // A pointer drag announces itself through its first overlay (over the tab row it starts from).
        dragEvent.preventDefault();
        const transfer = dragEvent.getData();
        if (transfer?.groupId) beginDrag(api, { groupId: transfer.groupId, panelId: transfer.panelId ?? null }, window);
      }),
      api.onDidLayoutChange(() => scheduleLayoutSnapshot(api)),
    );
  }, [beginDrag, scheduleLayoutSnapshot]);

  useEffect(() => {
    const api = apiRef.current;
    if (!api || !hydratedRef.current) return;
    const panelIdsFingerprint = panels.map((panel) => panel.id).join("\u0000");
    panelIdsFingerprintRef.current = panelIdsFingerprint;
    const desiredIds = new Set(panels.map((panel) => panel.id));
    syncingRef.current = true;
    try {
      api.panels.forEach((panel) => {
        if (!desiredIds.has(panel.id)) api.removePanel(panel);
      });
      const added: { id: string; group: DockviewGroupPanel }[] = [];
      panels.forEach((panel) => {
        const existing = api.getPanel(panel.id);
        if (existing) {
          if (existing.title !== panel.title) existing.api.setTitle(panel.title);
          // Dockview keeps an existing portal while its component is unchanged; hand it the current panel.
          existing.api.updateParameters({ panel });
          return;
        }
        const referencePanel = api.activePanel ?? api.panels[api.panels.length - 1];
        api.addPanel({
          id: panel.id,
          component: "module",
          title: panel.title,
          params: { panel },
          position: referencePanel ? { referencePanel, direction: "within" } : undefined,
        });
        const group = api.getPanel(panel.id)!.group;
        // A new panel takes the right half of the group it is added to, unless either half would be too narrow to
        // use (then it waits there as a tab).
        if (referencePanel) added.push({ id: panel.id, group });
      });
      const bounds = hostRef.current?.getBoundingClientRect();
      for (const item of added) {
        if (!bounds || bounds.width <= 0 || bounds.height <= 0) break;
        const width = Math.round(bounds.width), height = Math.round(bounds.height);
        const layout = arrangement(api);
        const source = { groupId: item.group.id, panelId: item.id };
        const target: DropTarget = { kind: "group", groupId: item.group.id, zone: "right" };
        const plan = planFor(layout, source, target, width, height, OPEN_MIN_WIDTH);
        if (plan && plan.target.kind === "group" && plan.target.zone === "right") installArrangement(api, plan.layout, item.id);
      }

      const nextActive = activePanelId ? api.getPanel(activePanelId) : undefined;
      nextActive?.api.setActive();
    } finally {
      syncingRef.current = false;
    }
    publishLayout(api, "panel-sync");
  }, [activePanelId, installArrangement, panels, publishLayout]);

  useEffect(() => () => {
    disposablesRef.current.forEach((disposable) => disposable.dispose());
    disposablesRef.current = [];
    if (layoutTimerRef.current != null) window.clearTimeout(layoutTimerRef.current);
    if (settleTimerRef.current != null) window.clearTimeout(settleTimerRef.current);
    registerLayoutReaderRef.current?.(null);
    apiRef.current = null;
  }, []);

  // Ctrl+Tab steps through the panels of the current group, Ctrl+Shift+Tab back.
  useEffect(() => {
    const step = (event: KeyboardEvent) => {
      if (!event.ctrlKey || event.key !== "Tab") return;
      const group = apiRef.current?.activeGroup;
      if (!group || group.panels.length < 2) return;
      event.preventDefault();
      const index = group.panels.indexOf(group.activePanel!);
      group.panels[(index + (event.shiftKey ? -1 : 1) + group.panels.length) % group.panels.length].api.setActive();
    };
    window.addEventListener("keydown", step, true);
    return () => window.removeEventListener("keydown", step, true);
  }, []);

  return (
    <DockPreviewContext.Provider value={{
      renderModule,
      onAddModule,
      onRequestRemovePanel,
      activePanelId,
      panels,
      modules,
      onAddModuleKind,
    }}>
      <div className="experience-dockview-host" ref={hostRef} data-single={panels.length === 1 || undefined}>
        {hint && <div className="talyx-dock-drop-hint" data-line={hint.line || undefined} aria-hidden="true"
          style={{ left: hint.rect.x, top: hint.rect.y, width: hint.rect.width, height: hint.rect.height }} />}
        <TxTooltipProvider>
        <DockviewReact
          components={dockComponents}
          defaultTabComponent={DockModuleTab}
          leftHeaderActionsComponent={DockGroupAdd}
          rightHeaderActionsComponent={DockGroupRestore}
          watermarkComponent={DockEmptyWatermark}
          onReady={onReady}
          theme={talyxDockTheme}
          defaultRenderer="always"
          singleTabMode="default"
          dndStrategy="pointer"
          keyboardNavigation
          scrollbars="native"
          getTabContextMenuItems={noTabMenu}
        />
        </TxTooltipProvider>
      </div>
    </DockPreviewContext.Provider>
  );
}

