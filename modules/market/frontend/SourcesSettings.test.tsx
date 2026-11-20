import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import type { Source } from "./panel/data";
import { groupsOf } from "./panel/Sources";

const settings = readFileSync(new URL("./SourcesSettings.tsx", import.meta.url), "utf8");
const panel = readFileSync(new URL("./panel/Sources.tsx", import.meta.url), "utf8");

const source = (id: string, enabled: boolean, status: Source["status"]) => ({ id, enabled, status }) as Source;

describe("Market's Sources in the settings window", () => {
  it("lists the sources in the panel's bands, in the panel's order, empty bands left out", () => {
    const groups = groupsOf([source("off", false, "ok"), source("used", true, "ok"), source("key", true, "needs-key"), source("down", true, "unreachable")]);
    expect(groups.map(group => [group.label, group.sources.map(item => item.id)])).toEqual([
      ["Needs attention", ["down"]], ["In use", ["used"]], ["Not set up", ["key"]], ["Off", ["off"]]]);
    expect(settings).toContain("groupsOf(sources)");
    expect(panel).toContain("groupsOf(sources)");
  });
  it("edits the same global key as the panel: the same fields, saved through the same configure request", () => {
    expect(settings).toContain("<SourceFields");
    expect(settings).toContain('panelCall(ctx, "configure", { source: id, settings })');
    expect(panel).toContain('panelCall(ctx, "configure", { source: id, settings })');
  });
});
