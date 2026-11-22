import { intlLocale, translator } from "@talyx/sdk";
import { platformRuntime } from "../kernel/runtime";
import type { ShellPanel } from "./workspaceChanges";
import type { SavedWorkspace } from "./savedWorkspaces";
import { t } from "./i18n";

/* How a saved workspace is described wherever it is listed (Home, all workspaces): its modules and when it was saved. */

export type LayoutTile = NonNullable<SavedWorkspace["summary"]>["tiles"][number];

/** A module's name as people read it: its manifest's, translated with its own dictionary. */
export function moduleName(id: string) {
  const name = platformRuntime().kernel.definitions.get(id)?.name;
  return name ? translator(id)(name) : id;
}

/** Only the module's declared titles follow the language. A document or custom panel keeps its own name. */
export function displayedPanelTitle(panel: ShellPanel): string {
  const owner = panel.kind.slice("module:".length);
  const definition = platformRuntime().kernel.definitions.get(owner);
  if (definition && (panel.title === definition.name || Object.values(definition.manifest.panels ?? {}).some(spec => spec.title === panel.title))) {
    return translator(owner)(panel.title);
  }
  return panel.title;
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
  const full = date.toLocaleString(intlLocale(), { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
  const hours = Math.round(age / hour);
  const label = age < hour ? t("{minutes} min ago", { minutes: Math.max(1, Math.round(age / minute)) })
    : age < day ? (hours === 1 ? t("1 hour ago") : t("{hours} hours ago", { hours }))
      : age < 2 * day ? t("Yesterday")
        : age < 7 * day ? t("{days} days ago", { days: Math.floor(age / day) })
          : date.toLocaleDateString(intlLocale(), { month: "short", day: "numeric", ...(date.getFullYear() === new Date().getFullYear() ? {} : { year: "numeric" }) });
  return { label, full };
}
