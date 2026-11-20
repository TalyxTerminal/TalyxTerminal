import { beforeEach, describe, expect, it } from "vitest";
import type { ModuleDefinition } from "@talyx/sdk";
import { platformRuntime } from "../kernel/runtime";
import { aiWorkCount, readShellAlerts, shellAlerts, shellModuleChoices } from "./shellChrome";
import type { Activity } from "./workspaceActivityData";

/* Status dots on the TALYX mark and settings gear, and third-party modules in the picker. */

const module = (id: string, source: "official" | "installed", state: ModuleDefinition["state"], panels: Record<string, { title: string; launcher?: boolean }> = { main: { title: id, launcher: true } }, enabled = true) =>
  ({ id, name: id.toUpperCase(), source, state, enabled, problem: null, manifest: { id, name: id.toUpperCase(), panels } }) as unknown as ModuleDefinition;

describe("the dots", () => {
  it("the mark lights for a module that couldn't start; a module's error status lights the settings gear instead", () => {
    const running = { id: "chart", name: "Chart", failed: false };
    expect(shellAlerts([running], [])).toEqual({ faults: [], settings: false });
    expect(shellAlerts([running, { id: "trade", name: "Trade", failed: true }], [])).toEqual({ faults: [{ id: "trade", title: "Trade" }], settings: false });
    expect(shellAlerts([running], [{ level: "error" }])).toEqual({ faults: [], settings: true });
    expect(shellAlerts([running], [{ level: "warn" }, { level: "error", visible: false }])).toEqual({ faults: [], settings: false });
  });

  it("reads modules from their definitions: a failed process, or a frontend that didn't load; a disabled one isn't a fault", () => {
    const kernel = platformRuntime().kernel;
    kernel.setDefinitions([module("platform", "official", "ready", {}), module("chart", "official", "ready"), module("lab", "installed", "failed"),
      module("off", "installed", "stopped", undefined, false)]);
    expect(readShellAlerts()).toEqual({ faults: [{ id: "lab", title: "LAB" }], settings: false });
    platformRuntime().faults.set("chart", "The frontend could not be loaded");
    expect(readShellAlerts().faults.map(item => item.id)).toEqual(["chart", "lab"]);
    platformRuntime().faults.clear();
  });
});

describe("adding a module", () => {
  beforeEach(() => {
    platformRuntime().kernel.setDefinitions([module("platform", "official", "ready", {}), module("chart", "official", "ready"),
      module("example-addon", "installed", "ready"), module("headless", "installed", "ready", {}),
      module("embedded", "installed", "ready", { card: { title: "Card" } }), module("broken-addon", "installed", "failed"),
      module("off", "installed", "stopped", undefined, false)]);
  });

  it("lists official and third-party modules that offer a panel to open; one that couldn't start is listed but can't be picked", () => {
    expect(shellModuleChoices(readShellAlerts().faults)).toEqual([
      { kind: "module:broken-addon", name: "BROKEN-ADDON", failed: true },
      { kind: "module:chart", name: "CHART", failed: false },
      { kind: "module:example-addon", name: "EXAMPLE-ADDON", failed: false },
    ]);
  });
});

describe("stop AI work", () => {
  it("counts what AI started", () => {
    const item = (id: string, origin: Activity["origin"]): Activity => ({ id, module: "m", title: id, origin });
    expect(aiWorkCount([item("a", "ai"), item("b", "ai"), item("c", "user"), item("d", "module"), item("e", "code")])).toBe(2);
  });
});
