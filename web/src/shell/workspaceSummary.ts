import { platformRuntime } from "../kernel/runtime";
import type { SavedWorkspace } from "./savedWorkspaces";

/* How a saved workspace is described wherever it is listed (Home, all workspaces): its modules and when it was saved. */

export type LayoutTile = NonNullable<SavedWorkspace["summary"]>["tiles"][number];

export function moduleName(id: string) {
  return platformRuntime().kernel.definitions.get(id)?.name ?? id;
}

/** "Editor ×4 · Notes": the modules in the order they appear. */
export function moduleLine(tiles: readonly LayoutTile[]) {
  const counts = new Map<string, number>();
  for (const tile of tiles) counts.set(tile.module, (counts.get(tile.module) ?? 0) + 1);
  return [...counts].map(([id, count]) => count > 1 ? `${moduleName(id)} ×${count}` : moduleName(id)).join(" · ");
}

const minute = 60_000, hour = 60 * minute, day = 24 * hour;

export function savedWhen(iso: string) {
  const date = new Date(iso);
  const age = Date.now() - date.getTime();
  const full = date.toLocaleString("en-US", { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
  const label = age < hour ? `${Math.max(1, Math.round(age / minute))} min ago`
    : age < day ? `${Math.round(age / hour)} ${Math.round(age / hour) === 1 ? "hour" : "hours"} ago`
      : age < 2 * day ? "Yesterday"
        : age < 7 * day ? `${Math.floor(age / day)} days ago`
          : date.toLocaleDateString("en-US", { month: "short", day: "numeric", ...(date.getFullYear() === new Date().getFullYear() ? {} : { year: "numeric" }) });
  return { label, full };
}
