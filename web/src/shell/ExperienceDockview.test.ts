import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium, type Browser, type Page } from "playwright";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createServer, type ViteDevServer } from "vite";

/* The workspace frame in a real browser: the layout only scales with the
   workspace, an old arrangement saved under module minimums opens as saved, and a dropped panel lands where the
   highlight was drawn. */

const fixturePath = "/__dock_frame__";
const fixture = `<style>html,body{margin:0;overflow:hidden}#root{position:fixed;inset:0}</style><div id="root"></div>
<script type="module">
import React from "react";
import { createRoot } from "react-dom/client";
import { platformRuntime } from "/src/kernel/runtime.ts";
import { ExperienceDockview } from "/src/shell/ExperienceDockview.tsx";
const kernel = platformRuntime().kernel;
kernel.setDefinitions([["chart", "chart"], ["agent", "message"], ["trade", "bolt"]].map(([id, icon]) =>
  ({ id, name: id, enabled: true, state: "ready", source: "official", manifest: { id, name: id, panels: { main: { title: id, icon } } } })));
let root = null, reader = null, mounts = 0, props = null;
window.published = [];
window.durable = () => reader();
window.openPanel = panel => {
  props = { ...props, panels: [...props.panels, panel], activePanelId: panel.id };
  root.render(React.createElement(ExperienceDockview, props));
};
window.mount = (panels, layout) => {
  root?.unmount();
  window.published = [];
  root = createRoot(document.getElementById("root"));
  props = {
    key: ++mounts, panels, activePanelId: panels[0].id, initialLayout: layout,
    renderModule: panel => React.createElement("div", { "data-probe": panel.id, style: { height: "100%" } }, panel.id),
    onActivatePanel() {}, onRemovePanel() {}, onRequestRemovePanel: () => true,
    onLayoutChange: layout => window.published.push(layout),
    registerLayoutReader: value => { if (value) reader = value; },
    onAddModule() {},
  };
  root.render(React.createElement(ExperienceDockview, props));
};
window.ready = true;
</script>`;

type Box = { x: number; y: number; width: number; height: number };
type Node = { type: "leaf"; data: { id: string; views: string[]; activeView: string }; size: number }
  | { type: "branch"; data: Node[]; size: number };

const leaf = (id: string, views: string[], size: number): Node => ({ type: "leaf", data: { id, views, activeView: views[0] }, size });
const branch = (data: Node[], size: number): Node => ({ type: "branch", data, size });
const panel = (id: string, module = "chart") => ({ id, kind: `module:${module}`, title: id, state: null });

function layoutOf(root: Node, width: number, height: number, ids: string[], extra: Record<string, unknown> = {}) {
  return {
    grid: { root, width, height, orientation: "HORIZONTAL" },
    panels: Object.fromEntries(ids.map(id => [id, { id, contentComponent: "module", title: id, params: { panel: panel(id) }, ...extra }])),
    activeGroup: "g1",
  };
}

let server: ViteDevServer, browser: Browser, page: Page, origin: string;
// Its own dependency cache: web/node_modules/.vite is rewritten by every other Vite server (the dev server, other
// checkouts, other test files), which breaks this page's imports in the middle of loading.
const cacheDir = mkdtempSync(join(tmpdir(), "talyx-vite-dock-"));

beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    configFile: fileURLToPath(new URL("../../vite.config.ts", import.meta.url)),
    logLevel: "silent",
    optimizeDeps: { entries: [] },
    cacheDir,
    plugins: [{
      name: "dock-frame-fixture",
      enforce: "pre",
      configureServer(devServer) {
        devServer.middlewares.use(fixturePath, async (request, response) => {
          response.setHeader("Content-Type", "text/html");
          response.end(await devServer.transformIndexHtml(request.originalUrl ?? fixturePath, fixture));
        });
      },
    }],
    server: { host: "127.0.0.1", port: 18587, strictPort: true },
  });
  await server.listen();
  origin = "http://127.0.0.1:18587";
  browser = await chromium.launch({ headless: true });
  page = await browser.newPage({ viewport: { width: 1440, height: 760 } });
  page.setDefaultTimeout(15_000);
  // What the page ran into, so a page that never gets ready says why.
  const problems: string[] = [];
  page.on("pageerror", error => problems.push(String(error)));
  page.on("response", response => { if (response.status() >= 400) problems.push(`${response.status()} ${response.url()}`); });
  await page.goto(`${origin}${fixturePath}`, { waitUntil: "commit" });
  try {
    await page.waitForFunction(() => (window as unknown as { ready?: boolean }).ready === true, null, { timeout: 120_000 });
  } catch (error) {
    throw new Error(`The dock page did not get ready: ${problems.join("; ") || "no page errors"}`, { cause: error });
  }
}, 180_000);

afterAll(async () => {
  await browser?.close();
  await server?.close();
  rmSync(cacheDir, { recursive: true, force: true });
}, 60_000);

const frames = (count = 3) => page.evaluate(n => new Promise<void>(resolve => {
  const step = (left: number) => left === 0 ? resolve() : requestAnimationFrame(() => step(left - 1));
  step(n);
}), count);

async function mount(panels: ReturnType<typeof panel>[], layout: unknown, width: number, height: number) {
  await page.setViewportSize({ width, height });
  await page.evaluate(([value, saved]) => (window as unknown as { mount(p: unknown, l: unknown): void }).mount(value, saved), [panels, layout] as const);
  await page.waitForSelector(".talyx-tab");
  await frames(4);
}

/** Each panel's group: its place, and the panels stacked in it. */
async function groups(): Promise<Record<string, Box & { views: string[] }>> {
  return page.evaluate(() => {
    const result: Record<string, { x: number; y: number; width: number; height: number; views: string[] }> = {};
    for (const group of document.querySelectorAll<HTMLElement>(".dv-groupview")) {
      const views = [...group.querySelectorAll<HTMLElement>(".talyx-tab")].map(tab => tab.title);
      const rect = group.getBoundingClientRect();
      for (const view of views) result[view] = { x: rect.x, y: rect.y, width: rect.width, height: rect.height, views };
    }
    return result;
  });
}

async function hint(): Promise<(Box & { line: boolean }) | null> {
  return page.evaluate(() => {
    const element = document.querySelector<HTMLElement>(".talyx-dock-drop-hint");
    if (!element) return null;
    const rect = element.getBoundingClientRect();
    return { x: rect.x, y: rect.y, width: rect.width, height: rect.height, line: element.hasAttribute("data-line") };
  });
}

async function tabCenter(id: string) {
  const box = (await page.locator(`.talyx-tab[title="${id}"]`).boundingBox())!;
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

/** Drags a panel's tab to a point and reads the highlight just before releasing. */
async function drag(id: string, to: { x: number; y: number }) {
  const from = await tabCenter(id);
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  for (let step = 1; step <= 12; step += 1) {
    await page.mouse.move(from.x + (to.x - from.x) * step / 12, from.y + (to.y - from.y) * step / 12);
  }
  await frames(3);
  const shown = await hint();
  await page.mouse.up();
  await frames(4);
  return shown;
}

const near = (actual: Box, expected: Box, tolerance = 3) => {
  for (const key of ["x", "y", "width", "height"] as const) expect(Math.abs(actual[key] - expected[key]), `${key}: ${JSON.stringify(actual)} vs ${JSON.stringify(expected)}`).toBeLessThanOrEqual(tolerance);
};

describe("layout scales only by proportion", () => {
  it("keeps three columns, one split, through every workspace size, and returns to the same arrangement without a change", async () => {
    const ids = ["A", "B", "C", "D"];
    const saved = layoutOf(branch([leaf("g1", ["A"], 500), branch([leaf("g2", ["B"], 540), leaf("g3", ["C"], 360)], 600), leaf("g4", ["D"], 500)], 900), 1600, 900, ids);
    await mount(ids.map(id => panel(id)), saved, 1600, 900);
    const before = await groups();
    for (const [width, height] of [[800, 500], [2560, 1400], [1024, 640], [2560, 500], [800, 1400], [1440, 760], [1999, 1111], [801, 503], [1600, 900]]) {
      await page.setViewportSize({ width, height });
      await frames(4);
      const now = await groups();
      for (const id of ids) {
        expect(now[id].views, `${id} at ${width}×${height}`).toEqual([id]);
        expect(now[id].width).toBeGreaterThan(40);
        expect(now[id].height).toBeGreaterThan(40);
        expect(now[id].x + now[id].width).toBeLessThanOrEqual(width + 1);
        expect(now[id].y + now[id].height).toBeLessThanOrEqual(height + 1);
        // Every block keeps its share of the workspace.
        expect(Math.abs(now[id].width / width - before[id].width / 1600)).toBeLessThan(0.01);
        expect(Math.abs(now[id].height / height - before[id].height / 900)).toBeLessThan(0.01);
      }
    }
    const after = await groups();
    for (const id of ids) near(after[id], before[id], 2);
    expect(await page.evaluate(() => (window as unknown as { published: unknown[] }).published.length)).toBe(0);
    const durable = await page.evaluate(() => (window as unknown as { durable(): { grid: unknown } }).durable().grid);
    expect(durable).toEqual(saved.grid);
  }, 120_000);

  it("opens a workspace saved under the old Chart minimum (720×480) as three columns in a 1280×712 workspace", async () => {
    const ids = ["A", "B", "C"];
    const saved = layoutOf(branch([leaf("g1", ["A"], 800), leaf("g2", ["B"], 800), leaf("g3", ["C"], 800)], 1300), 2400, 1300, ids,
      { minimumWidth: 720, minimumHeight: 480 });
    await mount(ids.map(id => panel(id)), saved, 1280, 712);
    const now = await groups();
    for (const id of ids) {
      expect(now[id].views).toEqual([id]);
      expect(now[id].y).toBe(0);
      expect(now[id].height).toBeCloseTo(712, 0);
      expect(Math.abs(now[id].width - 1280 / 3)).toBeLessThanOrEqual(2);
    }
    expect(now.A.x).toBeLessThan(now.B.x);
    expect(now.B.x).toBeLessThan(now.C.x);
  }, 60_000);
});

describe("a dropped panel lands where the highlight was", () => {
  const ids = ["A", "B1", "B2", "C"];
  const threeColumns = () => layoutOf(branch([leaf("g1", ["A"], 480), leaf("g2", ["B1", "B2"], 480), leaf("g3", ["C"], 480)], 760), 1440, 760, ids);

  /** Points in a group's content, as shares of its box below the tab row. */
  const zones: [string, number, number][] = [["left", 0.08, 0.5], ["right", 0.92, 0.5], ["top", 0.5, 0.1], ["bottom", 0.5, 0.9], ["center", 0.5, 0.5], ["center", 0.4, 0.6]];

  for (const [target, source] of [["C", "B2"], ["A", "B2"], ["C", "A"], ["B1", "A"]] as const) {
    for (const [zone, fx, fy] of zones) {
      it(`${source} onto ${target}, ${zone} (${fx}, ${fy})`, async () => {
        await mount(ids.map(id => panel(id)), threeColumns(), 1440, 760);
        const box = (await groups())[target];
        const shown = await drag(source, { x: box.x + box.width * fx, y: box.y + 30 + (box.height - 30) * fy });
        expect(shown).not.toBeNull();
        expect(shown!.line).toBe(false);
        const landed = (await groups())[source];
        near(landed, shown!);
        if (zone === "center") expect(landed.views).toContain(target);
        else expect(landed.views).toEqual([source]);
      }, 60_000);
    }
  }

  for (const [side, at] of [["left", (_w: number, h: number) => ({ x: 4, y: h * 0.6 })], ["right", (w: number, h: number) => ({ x: w - 4, y: h * 0.3 })],
    ["top", (w: number) => ({ x: w * 0.7, y: 4 })], ["bottom", (w: number, h: number) => ({ x: w * 0.2, y: h - 4 })]] as const) {
    it(`B2 along the workspace's ${side} edge takes the whole edge`, async () => {
      await mount(ids.map(id => panel(id)), threeColumns(), 1440, 760);
      const shown = await drag("B2", at(1440, 760));
      expect(shown).not.toBeNull();
      const landed = (await groups()).B2;
      near(landed, shown!);
      expect(landed.views).toEqual(["B2"]);
      if (side === "left" || side === "right") expect(landed.height).toBeCloseTo(760, 0);
      else expect(landed.width).toBeCloseTo(1440, 0);
    }, 60_000);
  }

  for (const index of [0, 1]) {
    it(`B2 into C's tab row before tab ${index}: a line where the tab goes, and the tab lands there`, async () => {
      await mount(ids.map(id => panel(id)), threeColumns(), 1440, 760);
      const tab = (await page.locator('.talyx-tab[title="C"]').boundingBox())!;
      const shown = await drag("B2", { x: index === 0 ? tab.x + 4 : tab.x + tab.width - 4, y: tab.y + tab.height / 2 });
      expect(shown?.line).toBe(true);
      const after = await groups();
      expect(after.B2.views).toEqual(index === 0 ? ["B2", "C"] : ["C", "B2"]);
      const landed = (await page.locator('.talyx-tab[title="B2"]').boundingBox())!;
      expect(Math.abs(landed.x - shown!.x)).toBeLessThanOrEqual(4);
    }, 60_000);
  }

  it("a split that would be under the minimum lights the whole group and stacks the panel there", async () => {
    const narrow = layoutOf(branch([leaf("g1", ["A"], 620), leaf("g2", ["B1", "B2"], 620), leaf("g3", ["C"], 200)], 760), 1440, 760, ids);
    await mount(ids.map(id => panel(id)), narrow, 1440, 760);
    const box = (await groups()).C;
    const shown = await drag("B2", { x: box.x + box.width * 0.9, y: box.y + box.height / 2 });
    expect(shown).not.toBeNull();
    near(shown!, box);
    const landed = (await groups()).B2;
    expect(landed.views).toEqual(["C", "B2"]);
    near(landed, shown!);
  }, 60_000);

  it("draws none of Dockview's own overlays", async () => {
    await mount(ids.map(id => panel(id)), threeColumns(), 1440, 760);
    const box = (await groups()).C;
    const from = await tabCenter("B2");
    await page.mouse.move(from.x, from.y);
    await page.mouse.down();
    for (let step = 1; step <= 10; step += 1) await page.mouse.move(from.x + (box.x + box.width * 0.9 - from.x) * step / 10, from.y + (box.y + 200 - from.y) * step / 10);
    await frames(3);
    const visible = await page.evaluate(() => [...document.querySelectorAll<HTMLElement>(".dv-drop-target-anchor, .dv-drop-target-selection")]
      .some(element => element.getBoundingClientRect().width > 0 && getComputedStyle(element).visibility !== "hidden"));
    await page.mouse.up();
    expect(visible).toBe(false);
  }, 60_000);
});

describe("a panel opened from another one", () => {
  async function open(width: number) {
    const columns = layoutOf(branch([leaf("g1", ["A"], width / 2), leaf("g2", ["B"], width / 2)], 760), width, 760, ["A", "B"]);
    await mount([panel("B", "agent"), panel("A")], columns, width, 760);
    await page.evaluate(value => (window as unknown as { openPanel(p: unknown): void }).openPanel(value), panel("C", "trade"));
    await page.waitForSelector('.talyx-tab[title="C"]');
    await frames(4);
    return groups();
  }

  it("takes the right half of the opener's column when both halves stay usable", async () => {
    const placed = await open(1600);
    expect(placed.C.views).toEqual(["C"]);
    near(placed.C, { x: 1200, y: 0, width: 400, height: 760 });
    near(placed.B, { x: 800, y: 0, width: 400, height: 760 });
  }, 60_000);

  it("opens as a tab in the opener's column when halving it would squeeze both (trial 26)", async () => {
    const placed = await open(900);
    expect(placed.C.views).toEqual(["B", "C"]);
    near(placed.C, { x: 450, y: 0, width: 450, height: 760 });
    near(placed.A, { x: 0, y: 0, width: 450, height: 760 });
  }, 60_000);
});
