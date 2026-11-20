import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { txIconNames, type ModuleDefinition } from "@talyx/sdk";

import { platformRuntime } from "../kernel/runtime";
import { workspacesUsing } from "./communityData";
import { ModuleIcon, declaredModuleIcon } from "./ModuleIcon";
import { Linked } from "./communityParts";

const repository = resolve(__dirname, "../../..");

const definition = (id: string, panelIcon: string | null): ModuleDefinition => ({
  id, name: id, version: "1.0.0", description: id, source: "installed", enabled: true, state: "ready", problem: null, pid: 1234, digest: id, assetBase: "",
  manifest: { manifestVersion: 5, id, name: id, version: "1.0.0", entries: {}, panels: { main: { title: id, ...(panelIcon ? { icon: panelIcon } : {}) } } },
  contract: { contractVersion: "1", module: id, actions: {}, streams: {}, events: {}, extensionPoints: {}, settings: {}, $defs: {} },
});

describe("module intros", () => {
  it("link their URLs, without the sentence's closing punctuation", () => {
    const markup = renderToStaticMarkup(createElement(Linked, { text: "Charts by X (Apache-2.0), https://www.tradingview.com/. See http://a.b/c?d=1." }));
    expect(markup).toBe('Charts by X (Apache-2.0), <a class="cm-link" href="https://www.tradingview.com/" target="_blank" rel="noreferrer">https://www.tradingview.com/</a>. '
      + 'See <a class="cm-link" href="http://a.b/c?d=1" target="_blank" rel="noreferrer">http://a.b/c?d=1</a>.');
  });
});

describe("module icons", () => {
  it("are the monochrome icon the module's panel declares", () => {
    platformRuntime().kernel.setDefinitions([definition("plain", "chart"), definition("undeclared", null)]);
    expect(declaredModuleIcon("plain")).toBe("chart");
    expect(declaredModuleIcon("module:undeclared")).toBe("blocks");
    expect(renderToStaticMarkup(createElement(ModuleIcon, { module: "plain" }))).toMatch(/^<svg class="tx-icon"/);
    platformRuntime().kernel.setDefinitions([]);
  });

  it("declared by each official module are in the component library", () => {
    const modules = join(repository, "modules");
    const declared = readdirSync(modules, { withFileTypes: true }).filter(entry => existsSync(join(modules, entry.name, "talyx.module.json"))).flatMap(entry => {
      const manifest = JSON.parse(readFileSync(join(modules, entry.name, "talyx.module.json"), "utf8")) as { manifestVersion: number; panels?: Record<string, { icon?: string }> };
      return manifest.manifestVersion === 5 ? Object.values(manifest.panels ?? {}).map(panel => [entry.name, panel.icon] as const) : [];
    });
    expect(declared.length).toBeGreaterThan(0);
    for (const [id, icon] of declared) expect(txIconNames, `${id} declares ${icon}`).toContain(icon);
  });
});

describe("Installed", () => {
  const tile = (module: string, x = 0, w = 1) => ({ module, x, y: 0, w, h: 1 });

  it("counts the saved workspaces whose layout has a panel of the module", () => {
    const saved = [
      { summary: { tiles: [tile("chart", 0, .5), tile("notes", .5, .5)] } },
      { summary: { tiles: [tile("notes"), tile("notes")] } },
      { summary: { tiles: [tile("chart")] } },
      { summary: null },
      {},
    ];
    expect(workspacesUsing(saved, "notes")).toBe(2);
    expect(workspacesUsing(saved, "chart")).toBe(2);
    expect(workspacesUsing(saved, "agent")).toBe(0);
  });
});

describe("Community and settings sources", () => {
  it("have no mockup data or address switches left", () => {
    const shell = join(repository, "web/src/shell");
    const files = readdirSync(shell);
    expect(files).not.toContain("mockupBatch2.ts");
    expect(files).not.toContain("communitySample.ts");
    for (const name of files.filter(file => /^(Community|community|ManagerScreen|PlatformControls|NotificationSettings)/.test(file) && !file.endsWith(".test.ts"))) {
      const source = readFileSync(join(shell, name), "utf8");
      expect(source, name).not.toMatch(/mockupBatch2|communitySample|from "\.\/mockup"|location\.search|searchParams\.get\("(community|focus|open|settings|noworkspace|mock|fake)"\)/);
    }
  });
});
