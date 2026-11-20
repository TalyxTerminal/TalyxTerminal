import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium, type Browser, type BrowserContext } from "playwright";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createServer, type ViteDevServer } from "vite";

const fixturePath = "/__close_flow__";
const fixture = `<div id="root"></div><script type="module">
  import React from "react";
  import { createRoot } from "react-dom/client";
  import { WorkspaceCloseFlow } from "/src/shell/WorkspaceCloseFlow.tsx";
  window.exits = [];
  window.talyxDesktop = {
    onExitRequested: callback => { window.requestExit = () => callback(exitRequest); return () => undefined; },
    cancelExit: async id => { window.exits.push("cancel " + id); },
    confirmExit: async id => { window.exits.push("confirm " + id); },
  };
  const exitRequest = { requestId: "exit", preview: { workspaces: [
    { workspace: { workspaceId: "B", name: "Restored B", snapshotId: null }, activities: [{ id: "r1", module: "probe", title: "Backfill 3 series", origin: "ai" }] },
    { workspace: { workspaceId: "C", name: "Quiet C", snapshotId: null }, activities: [] },
  ] } };
  window.order = [];
  window.closeFlow = React.createRef();
  createRoot(document.getElementById("root")).render(React.createElement(WorkspaceCloseFlow, {
    ref: window.closeFlow, onClosing: async id => { window.order.push("frame gone " + id); },
    onClosed: async () => { window.order.push("closed"); }, onWorkspaceAction: async () => false, onError: reason => { throw reason; },
  }));
</script>`;

describe("Exiting TALYX", () => {
  let server: ViteDevServer;
  let browser: Browser;
  let context: BrowserContext;
  let origin: string;
  // Its own dependency cache: web/node_modules/.vite is rewritten by every other Vite server (the dev server, other
  // checkouts, other test files), which breaks this page's imports in the middle of loading.
  const cacheDir = mkdtempSync(join(tmpdir(), "talyx-vite-close-"));

  beforeAll(async () => {
    server = await createServer({
      root: fileURLToPath(new URL("../../", import.meta.url)),
      configFile: fileURLToPath(new URL("../../vite.config.ts", import.meta.url)),
      logLevel: "silent",
      // Scan only modules requested by this fixture, not the full application.
      optimizeDeps: { entries: [] },
      cacheDir,
      plugins: [{
        name: "close-navigation-runtime",
        enforce: "pre",
        resolveId(source, importer) {
          if (source === "../kernel/runtime" && importer?.includes("WorkspaceCloseFlow.tsx")) return "\0close-runtime";
        },
        load(id) {
          if (id === "\0close-runtime") return `export const platformRuntime = () => ({ kernel: { definitions: new Map([["probe", { name: "Probe module" }]]) },
            platform: {
              "workspace.list_open": async () => [{ workspaceId: "A", name: "Quiet A", snapshotId: null }],
              "activity.list": async () => ({ activities: [] }),
              "workspace.delete_preview": async () => [],
              "workspace.close": async ({ workspaceId }) => { window.order.push("host closes " + workspaceId); },
            } });`;
        },
        // Ahead of Vite's own HTML fallback, which would serve the application.
        configureServer(devServer) {
          devServer.middlewares.use(fixturePath, async (request, response) => {
            response.setHeader("Content-Type", "text/html");
            response.end(await devServer.transformIndexHtml(request.originalUrl ?? fixturePath, fixture));
          });
        },
      }],
      server: { host: "127.0.0.1", port: 18579, strictPort: true },
    });
    await server.listen();
    origin = "http://127.0.0.1:18579";
    browser = await chromium.launch({ headless: true });
    context = await browser.newContext();
  }, 60_000);

  afterAll(async () => {
    await context?.close();
    await browser?.close();
    await server?.close();
    rmSync(cacheDir, { recursive: true, force: true });
  }, 60_000);

  it("lists what stops in each workspace as its module reported it, by the module's name; cancel and exit answer the desktop app", async () => {
    const page = await context.newPage();
    page.setDefaultTimeout(10_000);
    // The first visit waits for Vite to prepare the shell's dependencies.
    await page.goto(`${origin}${fixturePath}`, { waitUntil: "commit" });
    await page.waitForFunction(() => typeof (window as unknown as { requestExit?: unknown }).requestExit === "function", null, { timeout: 120_000 });
    await page.evaluate(() => (window as unknown as { requestExit(): void }).requestExit());
    const dialog = page.getByRole("dialog", { name: "Exit TALYX?", exact: true });
    await expect(dialog.innerText()).resolves.toMatch(/Restored B[\s\S]*Backfill 3 series[\s\S]*Probe module/);
    expect(await dialog.innerText()).not.toContain("Quiet C");
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await dialog.waitFor({ state: "hidden", timeout: 3_000 });
    await page.evaluate(() => (window as unknown as { requestExit(): void }).requestExit());
    await dialog.getByRole("button", { name: "Exit", exact: true }).click();
    await dialog.waitFor({ state: "hidden", timeout: 3_000 });
    expect(await page.evaluate(() => (window as unknown as { exits: string[] }).exits)).toEqual(["cancel exit", "confirm exit"]);
  }, 180_000);

  it("takes the workspace's frame down before the Host closes the workspace, so nothing calls for it once it is closed", async () => {
    const page = await context.newPage();
    page.setDefaultTimeout(10_000);
    await page.goto(`${origin}${fixturePath}`, { waitUntil: "commit" });
    await page.waitForFunction(() => Boolean((window as unknown as { closeFlow?: { current: unknown } }).closeFlow?.current), null, { timeout: 120_000 });
    await page.evaluate(() => (window as unknown as { closeFlow: { current: { close(id: string): Promise<void> } } }).closeFlow.current.close("A"));
    expect(await page.evaluate(() => (window as unknown as { order: string[] }).order)).toEqual(["frame gone A", "host closes A", "closed"]);
  }, 180_000);
});
