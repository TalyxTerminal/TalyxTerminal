import type { SerializedDockview } from "dockview-react";

/* The workspace's arrangement as Dockview serializes it, read and rearranged as plain data. Dropping a panel is planned here first: the plan is the new arrangement, so the place it draws
   while the panel is dragged is computed from the same arrangement the drop installs. */

/** The smallest a group can be dragged to, the same for every module. It only keeps a divider drag from making a
    panel disappear; resizing the window scales everything by proportion and never stops at it. */
export const DOCK_MIN = { width: 120, height: 96 } as const;

export interface Rect { x: number; y: number; width: number; height: number }
export type Side = "left" | "right" | "top" | "bottom";
type Orientation = "HORIZONTAL" | "VERTICAL";
interface LeafData { id: string; views: string[]; activeView?: string; [key: string]: unknown }
type Leaf = { type: "leaf"; data: LeafData; size?: number; visible?: boolean };
type Branch = { type: "branch"; data: GridNode[]; size?: number; visible?: boolean };
type GridNode = Leaf | Branch;
/** The grid's root as the tree this file walks (Dockview's own type doesn't tell leaves from branches). */
const rootOf = (layout: SerializedDockview) => layout.grid.root as unknown as Branch;
const setRoot = (layout: SerializedDockview, root: Branch) => { layout.grid.root = root as unknown as SerializedDockview["grid"]["root"]; };

/** What is dragged: one panel, or a whole group (its tab row's empty space). */
export interface DragSource { groupId: string; panelId: string | null }

/** Where a drop goes: a side or the middle of a group, between two tabs, or along an outer edge of the workspace. */
export type DropTarget =
  | { kind: "group"; groupId: string; zone: Side | "center" }
  | { kind: "tabs"; groupId: string; index: number }
  | { kind: "edge"; side: Side };

export interface DropPlan {
  layout: SerializedDockview;
  /** The group the dragged panel ends up in, and where that group will be. */
  groupId: string;
  rect: Rect;
  target: DropTarget;
}

const orthogonal = (value: Orientation): Orientation => value === "HORIZONTAL" ? "VERTICAL" : "HORIZONTAL";
const axisOf = (side: Side): Orientation => side === "left" || side === "right" ? "HORIZONTAL" : "VERTICAL";
const before = (side: Side) => side === "left" || side === "top";
const leafData = (leaf: Leaf) => leaf.data;
const shown = (node: GridNode) => node.visible !== false;

/** Each group's place, from the serialized sizes scaled to the workspace's own size (so a layout saved at another
    window size reads the same way it is drawn). */
export function groupRects(layout: SerializedDockview, width = layout.grid.width, height = layout.grid.height): Map<string, Rect> {
  const rects = new Map<string, Rect>();
  const walk = (node: GridNode, orientation: Orientation, rect: Rect) => {
    if (node.type === "leaf") { rects.set(leafData(node).id, rect); return; }
    const children = node.data as GridNode[];
    const total = children.reduce((sum, child) => sum + (shown(child) ? child.size ?? 0 : 0), 0);
    const extent = orientation === "HORIZONTAL" ? rect.width : rect.height;
    let offset = 0;
    children.forEach(child => {
      const length = !shown(child) ? 0 : (total > 0 ? (child.size ?? 0) / total : 1 / children.length) * extent;
      walk(child, orthogonal(orientation), orientation === "HORIZONTAL"
        ? { x: rect.x + offset, y: rect.y, width: length, height: rect.height }
        : { x: rect.x, y: rect.y + offset, width: rect.width, height: length });
      offset += length;
    });
  };
  walk(rootOf(layout), layout.grid.orientation as Orientation, { x: 0, y: 0, width, height });
  return rects;
}

/* ── Rearranging ─────────────────────────────────────────────────────────────────────────────────────────────── */

/** A copy whose sizes are whole pixels of the given workspace size, every branch's children summing exactly to it. */
function normalized(layout: SerializedDockview, width: number, height: number): SerializedDockview {
  const copy = structuredClone(layout);
  const fit = (node: GridNode, orientation: Orientation, along: number, across: number) => {
    if (node.type === "leaf") return;
    const children = node.data as GridNode[];
    const total = children.reduce((sum, child) => sum + (shown(child) ? child.size ?? 0 : 0), 0);
    let used = 0;
    const visible = children.filter(shown);
    visible.forEach((child, index) => {
      const size = index === visible.length - 1 ? along - used
        : Math.round(total > 0 ? (child.size ?? 0) / total * along : along / visible.length);
      child.size = size;
      used += size;
      fit(child, orthogonal(orientation), across, size);
    });
  };
  const orientation = copy.grid.orientation as Orientation;
  copy.grid.width = width;
  copy.grid.height = height;
  rootOf(copy).size = orientation === "HORIZONTAL" ? height : width;
  fit(rootOf(copy), orientation, orientation === "HORIZONTAL" ? width : height, orientation === "HORIZONTAL" ? height : width);
  return copy;
}

interface Located { node: GridNode; parent: Branch | null; index: number; orientation: Orientation }

/** The node holding a group, its parent branch and the orientation its parent lays children out in. */
function locate(layout: SerializedDockview, groupId: string): Located | null {
  const search = (node: GridNode, parent: Branch | null, index: number, orientation: Orientation): Located | null => {
    if (node.type === "leaf") return leafData(node).id === groupId ? { node, parent, index, orientation } : null;
    const children = node.data as GridNode[];
    for (let i = 0; i < children.length; i += 1) {
      const found = search(children[i], node, i, orientation);
      if (found) return found;
    }
    return null;
  };
  const root = rootOf(layout);
  const children = root.data;
  const orientation = layout.grid.orientation as Orientation;
  for (let i = 0; i < children.length; i += 1) {
    const found = search(children[i], root, i, orientation);
    if (found) return found;
  }
  return null;
}

function containsGroup(node: GridNode, groupId: string): boolean {
  return node.type === "leaf" ? leafData(node).id === groupId : (node.data as GridNode[]).some(child => containsGroup(child, groupId));
}

/** Takes a group out of the tree. Its space goes to the neighbour that doesn't hold `keep` when there is one, so the
    place the panel is dropped on keeps its size; a branch left with one child is folded into its parent. */
function removeGroup(layout: SerializedDockview, groupId: string, keep: string | null): void {
  const found = locate(layout, groupId)!;
  const parent = found.parent!;
  const children = parent.data as GridNode[];
  const size = found.node.size ?? 0;
  children.splice(found.index, 1);
  const neighbours = [children[found.index - 1], children[found.index]].filter((node): node is GridNode => Boolean(node && shown(node)));
  const heir = neighbours.find(node => !keep || !containsGroup(node, keep)) ?? neighbours[0];
  if (heir) heir.size = (heir.size ?? 0) + size;
  fold(layout, parent);
}

function fold(layout: SerializedDockview, branch: Branch): void {
  const children = branch.data as GridNode[];
  if (children.length !== 1) return;
  const only = children[0];
  const root = rootOf(layout);
  if (branch === root) {
    if (only.type === "branch") {
      // The root's single child becomes the root, laid out the other way.
      setRoot(layout, { ...only, size: root.size });
      layout.grid.orientation = orthogonal(layout.grid.orientation as Orientation) as SerializedDockview["grid"]["orientation"];
    }
    return;
  }
  const parentFound = findParent(layout, branch)!;
  const siblings = parentFound.parent.data as GridNode[];
  if (only.type === "leaf") {
    siblings[parentFound.index] = { ...only, size: branch.size };
  } else {
    // A branch inside a one-child branch runs the same way as the grandparent: its children join the grandparent.
    siblings.splice(parentFound.index, 1, ...(only.data as GridNode[]));
  }
}

function findParent(layout: SerializedDockview, target: GridNode): { parent: Branch; index: number } | null {
  const search = (node: GridNode): { parent: Branch; index: number } | null => {
    if (node.type === "leaf") return null;
    const children = node.data as GridNode[];
    const index = children.indexOf(target);
    if (index >= 0) return { parent: node, index };
    for (const child of children) {
      const found = search(child);
      if (found) return found;
    }
    return null;
  };
  return search(rootOf(layout));
}

/** Puts `leaf` on one side of the group `targetId`, taking half of that group's room on that axis. */
function splitGroup(layout: SerializedDockview, targetId: string, side: Side, leaf: Leaf, extent: Rect): void {
  const found = locate(layout, targetId)!;
  const axis = axisOf(side);
  const parent = found.parent!;
  const children = parent.data as GridNode[];
  if (found.orientation === axis) {
    const total = found.node.size ?? 0;
    const half = Math.round(total / 2);
    found.node.size = total - half;
    children.splice(before(side) ? found.index : found.index + 1, 0, { ...leaf, size: half });
  } else {
    const length = axis === "HORIZONTAL" ? extent.width : extent.height;
    const half = Math.round(length / 2);
    const target = { ...found.node, size: length - half };
    const added = { ...leaf, size: half };
    children[found.index] = { type: "branch", data: before(side) ? [added, target] : [target, added], size: found.node.size } as Branch;
  }
}

/** Puts `leaf` along a whole outer edge of the workspace, taking an equal share of that axis; the rest keeps its
    proportions. */
function addAtEdge(layout: SerializedDockview, side: Side, leaf: Leaf, width: number, height: number): void {
  const axis = axisOf(side);
  const root = rootOf(layout);
  const length = axis === "HORIZONTAL" ? width : height;
  if (layout.grid.orientation === axis) {
    const children = root.data as GridNode[];
    const count = children.filter(shown).length;
    const share = Math.round(length / (count + 1));
    const rest = length - share;
    let used = 0;
    const visible = children.filter(shown);
    visible.forEach((child, index) => {
      const size = index === visible.length - 1 ? rest - used : Math.round((child.size ?? 0) / length * rest);
      child.size = size;
      used += size;
    });
    children.splice(before(side) ? 0 : children.length, 0, { ...leaf, size: share });
  } else {
    const half = Math.round(length / 2);
    const previous = { ...root, size: length - half };
    const added = { ...leaf, size: half };
    setRoot(layout, { type: "branch", data: before(side) ? [added, previous] : [previous, added], size: axis === "HORIZONTAL" ? height : width });
    layout.grid.orientation = axis as SerializedDockview["grid"]["orientation"];
  }
}

let groupSequence = 0;
const newGroupId = () => `talyx-group-${Date.now().toString(36)}-${(groupSequence += 1)}`;

function leafFor(groupId: string, views: string[], activeView: string, template?: LeafData): Leaf {
  return { type: "leaf", data: { ...(template ?? {}), id: groupId, views, activeView }, size: 0 };
}

/** The arrangement after dropping `source` on `target`, and where the dropped panel's group will be; null when the
    drop changes nothing. Sizes are planned in pixels of the given workspace size. */
export function planDrop(current: SerializedDockview, source: DragSource, target: DropTarget, width: number, height: number): DropPlan | null {
  const layout = normalized(current, width, height);
  const rects = groupRects(layout, width, height);
  const sourceFound = locate(layout, source.groupId);
  if (!sourceFound) return null;
  const sourceLeaf = sourceFound.node as Leaf;
  const sourceData = leafData(sourceLeaf);
  const moving = source.panelId ? [source.panelId] : [...sourceData.views];
  const active = source.panelId ?? sourceData.activeView ?? moving[0];
  const wholeGroup = moving.length === sourceData.views.length;
  const targetId = target.kind === "edge" ? null : target.groupId;
  if (target.kind !== "edge" && !locate(layout, target.groupId)) return null;

  if (target.kind === "tabs" || (target.kind === "group" && target.zone === "center")) {
    const into = targetId!;
    if (into === source.groupId) {
      if (target.kind !== "tabs" || moving.length !== 1) return null;
      const views = sourceData.views.filter(view => view !== active);
      const from = sourceData.views.indexOf(active);
      const index = Math.min(target.index > from ? target.index - 1 : target.index, views.length);
      views.splice(index, 0, active);
      if (views.join("\u0000") === sourceData.views.join("\u0000")) return null;
      sourceData.views = views;
      sourceData.activeView = active;
      return { layout, groupId: into, rect: rects.get(into)!, target };
    }
    if (wholeGroup) removeGroup(layout, source.groupId, into);
    else take(sourceData, moving);
    const intoData = leafData(locate(layout, into)!.node as Leaf);
    const index = target.kind === "tabs" ? Math.min(target.index, intoData.views.length) : intoData.views.length;
    intoData.views.splice(index, 0, ...moving);
    intoData.activeView = active;
    layout.activeGroup = into;
    return { layout, groupId: into, rect: groupRects(layout, width, height).get(into)!, target };
  }

  // A split or an edge: the dragged panels get a group of their own.
  if (target.kind === "group" && target.groupId === source.groupId && wholeGroup) return null;
  const groupId = wholeGroup ? source.groupId : newGroupId();
  const leaf = leafFor(groupId, moving, active, wholeGroup ? sourceData : undefined);
  if (wholeGroup) removeGroup(layout, source.groupId, targetId);
  else take(sourceData, moving);
  if (target.kind === "group") {
    // The target's room after the source left it: the same as before unless the source's space had nowhere else to go.
    const room = groupRects(layout, width, height).get(target.groupId)!;
    splitGroup(layout, target.groupId, target.zone as Side, leaf, room);
  } else {
    addAtEdge(layout, target.side, leaf, width, height);
  }
  layout.activeGroup = groupId;
  const planned = normalized(layout, width, height);
  return { layout: planned, groupId, rect: groupRects(planned, width, height).get(groupId)!, target };
}

function take(data: LeafData, moving: string[]): void {
  data.views = data.views.filter(view => !moving.includes(view));
  if (!data.activeView || moving.includes(data.activeView)) data.activeView = data.views[0];
}

/** Whether every group in the plan keeps at least the minimum size. */
export function planFits(plan: DropPlan): boolean {
  for (const rect of groupRects(plan.layout).values()) {
    if (rect.width < DOCK_MIN.width || rect.height < DOCK_MIN.height) return false;
  }
  return true;
}

/* ── Comparing arrangements ─────────────────────────────────────────────────────────────────────────────────── */

/** The arrangement with sizes as shares of their branch, for telling a real rearrangement from the window being
    resized (which only scales every size). Focus and panel titles are not part of it. */
export function arrangementOf(layout: SerializedDockview): unknown {
  const shape = (node: GridNode): unknown => {
    if (node.type === "leaf") { const data = leafData(node); return { group: data.id, views: data.views, visible: shown(node) }; }
    const children = node.data as GridNode[];
    const total = children.reduce((sum, child) => sum + (shown(child) ? child.size ?? 0 : 0), 0);
    return children.map(child => ({ share: total > 0 && shown(child) ? (child.size ?? 0) / total : 0, node: shape(child) }));
  };
  return { orientation: layout.grid.orientation, root: shape(rootOf(layout)) };
}

/** Same groups, same panels in the same order, and every share within half a percent. */
export function sameArrangement(left: SerializedDockview, right: SerializedDockview): boolean {
  const compare = (a: unknown, b: unknown): boolean => {
    if (typeof a === "number" && typeof b === "number") return Math.abs(a - b) <= 0.005;
    if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((item, index) => compare(item, b[index]));
    if (a && b && typeof a === "object" && typeof b === "object") {
      const keys = Object.keys(a as object);
      return keys.length === Object.keys(b as object).length && keys.every(key => compare((a as Record<string, unknown>)[key], (b as Record<string, unknown>)[key]));
    }
    return a === b;
  };
  return compare(arrangementOf(left), arrangementOf(right));
}

/* ── The summary saved with a workspace ─────────────────────────────────────────────────────────────────────── */

export interface LayoutTile { module: string; x: number; y: number; w: number; h: number }
export interface LayoutSummary { tiles: LayoutTile[] }

const MAX_TILES = 64;
const share = (value: number) => Math.round(value * 10_000) / 10_000;

/** Each panel's module and its place in the workspace as shares (0–1) of its width and height; panels stacked in one
    group share its place, the one in front last. */
export function layoutSummary(layout: SerializedDockview, moduleOf: (panelId: string) => string): LayoutSummary {
  const rects = groupRects(layout, 1, 1);
  const tiles: LayoutTile[] = [];
  const visit = (node: GridNode) => {
    if (node.type === "branch") { (node.data as GridNode[]).forEach(visit); return; }
    if (!shown(node)) return;
    const data = leafData(node);
    const rect = rects.get(data.id)!;
    if (share(rect.width) === 0 || share(rect.height) === 0) return;
    const ordered = [...data.views.filter(view => view !== data.activeView), ...data.views.filter(view => view === data.activeView)];
    for (const view of ordered) tiles.push({ module: moduleOf(view), x: share(rect.x), y: share(rect.y), w: share(rect.width), h: share(rect.height) });
  };
  visit(rootOf(layout));
  return { tiles: tiles.slice(0, MAX_TILES) };
}
