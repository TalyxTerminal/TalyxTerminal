import { readFileSync } from "node:fs";
import { transformSync } from "esbuild";
import { chromium } from "playwright";
import { describe, expect, it } from "vitest";
import { change, followSaved, savedAt, saveWorkspace, undoFailedOpen, unsaved, type WorkspaceState } from "./workspaceChanges";

const panel = (id: string) => ({ id, kind: "module:chart", title: id, state: { linkageVersion: 1, panelType: "chart.chart", stateVersion: 1, state: null } });
const opened = (): WorkspaceState => ({
  workspaceId: "ctx-1", snapshotId: "ws-1", snapshotRevision: "r1", name: "Desk",
  panels: [panel("a")], activePanelId: "a", dockLayout: null,
  changes: 0, savedChanges: 0,
});

describe("unsaved is one rising count of the user's changes", () => {
  it("a user's change leaves the workspace unsaved; a derived one is kept but does not", () => {
    const looked = change(opened(), { activePanelId: "a", panels: [{ ...panel("a"), state: "scrolled" }] }, { derived: true });
    expect(unsaved(looked)).toBe(false);
    expect(looked.panels[0].state).toBe("scrolled");
    expect(unsaved(change(looked, { name: "Desk 2" }))).toBe(true);
  });

  it("a save settles only the changes it captured; one made while it ran stays unsaved", () => {
    const edited = change(opened(), { panels: [panel("a"), panel("b")] });
    const captured = edited.changes;
    const during = change(edited, { name: "Renamed while saving" });
    const settled = savedAt(during, captured, { snapshotId: "ws-1", revision: "r2" });
    expect(settled.snapshotRevision).toBe("r2");
    expect(unsaved(settled)).toBe(true);
    expect(unsaved(savedAt(edited, captured, { snapshotId: "ws-1", revision: "r2" }))).toBe(false);
  });

  it("a panel that failed to open takes back only its own change", () => {
    const before = opened();
    const withPanel = change(before, { panels: [panel("a"), panel("x")], activePanelId: "x" });
    const undone = undoFailedOpen(withPanel, before, "x");
    expect(undone.panels.map(item => item.id)).toEqual(["a"]);
    expect(undone.activePanelId).toBe("a");
    expect(unsaved(undone)).toBe(false);
    const renamedMeanwhile = change(withPanel, { name: "Other" });
    expect(unsaved(undoFailedOpen(renamedMeanwhile, before, "x"))).toBe(true);
  });
});

describe("the window keeps the saved state the platform wrote", () => {
  const holder = (initial: WorkspaceState) => {
    let state = initial;
    return { commit: (update: (latest: WorkspaceState) => WorkspaceState) => { state = update(state); }, get: () => state };
  };

  it("a module failing after the platform wrote leaves the workspace unsaved, and Save again goes over what was written", async () => {
    const window = holder(change(opened(), { name: "Desk 2" }));
    const writes: Array<{ snapshotId: string | null; revision: string | null }> = [];
    const write = (n: number) => async () => {
      writes.push({ snapshotId: window.get().snapshotId, revision: window.get().snapshotRevision });
      return { snapshotId: "ws-1", revision: `r${n}` };
    };
    await expect(saveWorkspace(window.commit, window.get().changes, write(2), async () => { throw new Error("conflict"); })).rejects.toThrow("conflict");
    expect(window.get().snapshotRevision).toBe("r2");
    expect(unsaved(window.get())).toBe(true);
    await saveWorkspace(window.commit, window.get().changes, write(3), async () => {});
    expect(writes).toEqual([{ snapshotId: "ws-1", revision: "r1" }, { snapshotId: "ws-1", revision: "r2" }]);
    expect(unsaved(window.get())).toBe(false);
  });

  it("follows a rename from another window without counting it as a change", () => {
    const renamed = followSaved(opened(), { kind: "saved", snapshotId: "ws-1", name: "Renamed", revision: "r9" });
    expect([renamed.name, renamed.snapshotRevision, unsaved(renamed)]).toEqual(["Renamed", "r9", false]);
    expect(followSaved(renamed, { kind: "saved", snapshotId: "ws-1", name: "Renamed", revision: "r9" })).toBe(renamed);
  });
});


it("recovers unsaved workspaces once and commits all their drafts together in browser storage", async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const origin = "http://127.0.0.1:5249";
    const source = readFileSync(new URL("./workspaceChanges.ts", import.meta.url), "utf8");
    const js = transformSync(source, { loader: "ts", format: "iife", globalName: "recovery" }).code;
    await page.route(origin + "/", route => route.fulfill({ contentType: "text/html", body: "<title>Recovery</title>" }));
    await page.goto(origin);
    await page.addScriptTag({ content: js });
    const states = [change(opened(), { name: "Uncommitted" }), { ...opened(), workspaceId: "other" }];
    const result = await page.evaluate(async states => {
      const { stashUnsaved, takeStash } = (window as Window & { recovery: typeof import("./workspaceChanges") }).recovery;
      await stashUnsaved(states);
      let refused = false;
      try {
        await stashUnsaved([{ ...states[0], name: "Partial write" },
          { ...states[1], changes: 1, panels: [{ id: "p", kind: "module:test", title: "Bad", state: () => undefined }] }]);
      } catch { refused = true; }
      return { refused, other: await takeStash("other"), first: await takeStash("ctx-1"), again: await takeStash("ctx-1") };
    }, states);
    expect(result).toEqual({ refused: true, other: null, first: states[0], again: null });
  } finally { await browser.close(); }
}, 30000);
