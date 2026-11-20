import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import type { DockviewApi, SerializedDockview } from "dockview-react";
import { DOCK_MIN, groupRects, planDrop, type DragSource, type DropPlan, type DropTarget, type Rect, type Side } from "./dockLayout";

/* Moving things in the dock: dragging a panel or a group to where the one highlight shows, and the dividers. */

/** The outer strip of the workspace where a drop takes a whole edge, in pixels. */
const EDGE_STRIP = 12;

/** The arrangement without the panels' runtime parameters (whole module states), for planning drops. */
export function arrangement(api: DockviewApi): SerializedDockview {
  const layout = api.toJSON();
  return { ...layout, panels: {} };
}

/** Panel records as the frame installs them: sizes once declared by modules are dropped (dockLayout.ts, DOCK_MIN). */
export function withoutDeclaredMinimums(layout: SerializedDockview): SerializedDockview {
  return { ...layout, panels: Object.fromEntries(Object.entries(layout.panels).map(([id, entry]) => {
    const { minimumWidth: _width, minimumHeight: _height, ...rest } = entry as typeof entry & { minimumWidth?: number; minimumHeight?: number };
    return [id, rest];
  })) as SerializedDockview["panels"] };
}

/** Groups never stop the window from scaling the layout; only a divider drag is held at the minimum. */
export const FREE = { minimumWidth: 0, minimumHeight: 0 };

/** Which drop the pointer is over: the outer strip, a group's tab row (between which tabs), or a side third or the
    middle of a group's content. */
export function dropTargetAt(api: DockviewApi, host: HTMLElement, x: number, y: number): DropTarget | null {
  const bounds = host.getBoundingClientRect();
  if (x < bounds.left || x > bounds.right || y < bounds.top || y > bounds.bottom) return null;
  const edges: [Side, number][] = [["left", x - bounds.left], ["right", bounds.right - x], ["top", y - bounds.top], ["bottom", bounds.bottom - y]];
  const edge = edges.reduce((nearest, item) => item[1] < nearest[1] ? item : nearest);
  if (edge[1] < EDGE_STRIP) return { kind: "edge", side: edge[0] };
  for (const group of api.groups) {
    if (!group.api.isVisible) continue;
    const rect = group.element.getBoundingClientRect();
    if (x < rect.left || x > rect.right || y < rect.top || y > rect.bottom) continue;
    const header = group.element.querySelector<HTMLElement>(".dv-tabs-and-actions-container");
    const row = header?.getBoundingClientRect();
    if (row && row.height > 0 && y <= row.bottom) {
      const tabs = [...header!.querySelectorAll<HTMLElement>(".dv-tab")].map(tab => tab.getBoundingClientRect());
      return { kind: "tabs", groupId: group.id, index: tabs.filter(tab => tab.left + tab.width / 2 < x).length };
    }
    const top = row && row.height > 0 ? row.bottom : rect.top;
    const fx = (x - rect.left) / rect.width, fy = (y - top) / Math.max(1, rect.bottom - top);
    const sides: [Side, number][] = [["left", fx], ["right", 1 - fx], ["top", fy], ["bottom", 1 - fy]];
    const side = sides.reduce((nearest, item) => item[1] < nearest[1] ? item : nearest);
    return { kind: "group", groupId: group.id, zone: side[1] < 1 / 3 ? side[0] : "center" };
  }
  return null;
}

/** The plan for a drop; a split that would leave the new group or a shrunk one under the minimum stacks the panel
    in that group instead. */
export function planFor(layout: SerializedDockview, source: DragSource, target: DropTarget, width: number, height: number,
  minWidth: number = DOCK_MIN.width): DropPlan | null {
  const plan = planDrop(layout, source, target, width, height);
  if (!plan || target.kind === "tabs" || (target.kind === "group" && target.zone === "center")) return plan;
  const previous = groupRects(layout, width, height);
  const cramped = [...groupRects(plan.layout).entries()].some(([id, rect]) => {
    const was = previous.get(id);
    return (!was || rect.width < was.width - 0.5) && rect.width < minWidth
      || (!was || rect.height < was.height - 0.5) && rect.height < DOCK_MIN.height;
  });
  if (!cramped) return plan;
  return target.kind === "group" ? planDrop(layout, source, { kind: "group", groupId: target.groupId, zone: "center" }, width, height) : null;
}

export type DropHint = { rect: Rect; line: boolean };

/** Dragging a panel or a group: `land` installs the plan the highlight showed. */
export function usePanelDrag(hostRef: RefObject<HTMLDivElement | null>, land: (api: DockviewApi, plan: DropPlan, activeId: string | null) => void) {
  const dragCleanupRef = useRef<(() => void) | null>(null);
  const [hint, setHint] = useState<DropHint | null>(null);
  /* ── Dragging a panel ──────────────────────────────────────────────────────────────────────────────────────────
     Dockview starts the drag and draws the dragged tab; every drop overlay of its own is refused. The frame works out
     the drop under the pointer, plans it (dockLayout.ts), draws the planned place, and on release installs exactly
     that plan: one highlight, and the panel lands where it is. */
  const endDrag = useCallback(() => {
    dragCleanupRef.current?.();
    dragCleanupRef.current = null;
    setHint(null);
    const host = hostRef.current;
    host?.classList.remove("is-dragging-panel", "is-dragging-group");
    host?.querySelectorAll(".talyx-dock-drag-source").forEach(element => element.classList.remove("talyx-dock-drag-source"));
  }, [hostRef]);

  const beginDrag = useCallback((api: DockviewApi, source: DragSource, sourceWindow: Window) => {
    const host = hostRef.current;
    if (!host || dragCleanupRef.current) return;
    const group = api.groups.find(item => item.id === source.groupId);
    host.classList.add(source.panelId ? "is-dragging-panel" : "is-dragging-group");
    group?.element.classList.add("talyx-dock-drag-source");
    const layout = arrangement(api);
    const bounds = host.getBoundingClientRect();
    const width = Math.round(bounds.width), height = Math.round(bounds.height);
    let plan: DropPlan | null = null;
    let planKey = "";
    let frame: number | null = null;
    let pointer: { x: number; y: number } | null = null;
    const update = () => {
      frame = null;
      if (!pointer) return;
      const target = dropTargetAt(api, host, pointer.x, pointer.y);
      const key = JSON.stringify(target);
      if (key === planKey) return;
      planKey = key;
      plan = target ? planFor(layout, source, target, width, height) : null;
      if (!plan) { setHint(null); return; }
      if (plan.target.kind === "tabs") {
        // Between two tabs: a thin line where the tab will go.
        const targetGroup = api.groups.find(item => item.id === plan!.groupId)!;
        const header = targetGroup.element.querySelector<HTMLElement>(".dv-tabs-and-actions-container")!.getBoundingClientRect();
        const tabs = [...targetGroup.element.querySelectorAll<HTMLElement>(".dv-tab")].map(tab => tab.getBoundingClientRect());
        const index = plan.target.index;
        const lineX = tabs.length === 0 ? header.left : index < tabs.length ? tabs[index].left : tabs[tabs.length - 1].right;
        const hostBounds = host.getBoundingClientRect();
        setHint({ line: true, rect: { x: lineX - hostBounds.left, y: header.top - hostBounds.top, width: 0, height: header.height } });
      } else {
        setHint({ line: false, rect: plan.rect });
      }
    };
    const move = (event: PointerEvent | DragEvent) => {
      pointer = { x: event.clientX, y: event.clientY };
      if (frame == null) frame = sourceWindow.requestAnimationFrame(update);
    };
    const finish = (event: Event) => {
      const commit = event.type === "pointerup" || event.type === "drop";
      if (commit && pointer) {
        if (frame != null) { sourceWindow.cancelAnimationFrame(frame); frame = null; }
        update();
      }
      const chosen = commit ? plan : null;
      endDrag();
      if (!chosen) return;
      land(api, chosen, source.panelId ?? group?.activePanel?.id ?? null);
    };
    const cancel = (event: KeyboardEvent) => { if (event.key === "Escape") { plan = null; endDrag(); } };
    sourceWindow.addEventListener("pointermove", move, true);
    sourceWindow.addEventListener("dragover", move, true);
    sourceWindow.addEventListener("pointerup", finish, true);
    sourceWindow.addEventListener("drop", finish, true);
    sourceWindow.addEventListener("dragend", finish, true);
    sourceWindow.addEventListener("pointercancel", finish, true);
    sourceWindow.addEventListener("blur", finish);
    sourceWindow.addEventListener("keydown", cancel, true);
    dragCleanupRef.current = () => {
      sourceWindow.removeEventListener("pointermove", move, true);
      sourceWindow.removeEventListener("dragover", move, true);
      sourceWindow.removeEventListener("pointerup", finish, true);
      sourceWindow.removeEventListener("drop", finish, true);
      sourceWindow.removeEventListener("dragend", finish, true);
      sourceWindow.removeEventListener("pointercancel", finish, true);
      sourceWindow.removeEventListener("blur", finish);
      sourceWindow.removeEventListener("keydown", cancel, true);
      if (frame != null) sourceWindow.cancelAnimationFrame(frame);
    };
  }, [endDrag, hostRef, land]);

  useEffect(() => () => endDrag(), [endDrag]);
  return { hint, beginDrag };
}

/** Dividers: held at the minimum while dragged, evened out on a double-click. */
export function useDividers(hostRef: RefObject<HTMLDivElement | null>, apiRef: RefObject<DockviewApi | null>) {
  /* ── Dividers ─────────────────────────────────────────────────────────────────────────────────────────────────
     While a divider is dragged each group is held at the minimum (or its size, if already smaller), so nothing can be
     dragged out of sight; afterwards they are free again so the window scales the layout by proportion. Double-click
     shares the space between the two sides evenly. */
  const holdGroupsForDivider = useCallback((event: PointerEvent) => {
    const api = apiRef.current;
    if (!api || !(event.target instanceof Element) || !event.target.closest(".dv-sash")) return;
    for (const group of api.groups) {
      group.api.setConstraints({
        minimumWidth: Math.min(DOCK_MIN.width, Math.floor(group.api.width)),
        minimumHeight: Math.min(DOCK_MIN.height, Math.floor(group.api.height)),
      });
    }
    const release = () => {
      window.removeEventListener("pointerup", release, true);
      window.removeEventListener("pointercancel", release, true);
      for (const group of api.groups) group.api.setConstraints(FREE);
    };
    window.addEventListener("pointerup", release, true);
    window.addEventListener("pointercancel", release, true);
  }, [apiRef]);

  const evenOutDivider = useCallback((event: MouseEvent) => {
    const api = apiRef.current;
    const sash = event.target instanceof Element ? event.target.closest(".dv-sash") : null;
    const split = sash?.parentElement?.parentElement;
    if (!api || !sash || !split) return;
    const index = [...sash.parentElement!.children].indexOf(sash);
    const views = split.querySelector(":scope > .dv-view-container")?.children;
    const first = views?.[index] as HTMLElement | undefined, second = views?.[index + 1] as HTMLElement | undefined;
    const holder = api.groups.find(group => group.element === first?.querySelector(".dv-groupview"));
    if (!first || !second || !holder) return;
    // Any group in the first view sizes that view along the divider's axis; the second view takes the rest.
    if (split.classList.contains("dv-horizontal")) holder.api.setSize({ width: Math.round((first.offsetWidth + second.offsetWidth) / 2) });
    else holder.api.setSize({ height: Math.round((first.offsetHeight + second.offsetHeight) / 2) });
  }, [apiRef]);

  useEffect(() => {
    const host = hostRef.current;
    host?.addEventListener("pointerdown", holdGroupsForDivider, true);
    host?.addEventListener("dblclick", evenOutDivider, true);
    return () => {
      host?.removeEventListener("pointerdown", holdGroupsForDivider, true);
      host?.removeEventListener("dblclick", evenOutDivider, true);
    };
  }, [evenOutDivider, holdGroupsForDivider, hostRef]);
}
