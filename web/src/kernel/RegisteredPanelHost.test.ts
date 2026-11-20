import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium, type Browser, type BrowserContext } from "playwright";
import { afterAll, beforeAll, expect, it } from "vitest";
import { createServer, type ViteDevServer } from "vite";

let server: ViteDevServer, browser: Browser, context: BrowserContext, origin: string;
// Its own dependency cache: web/node_modules/.vite is rewritten by every other Vite server (the dev server, other
// checkouts, other test files), which breaks this page's imports in the middle of loading.
const cacheDir = mkdtempSync(join(tmpdir(), "talyx-vite-panel-"));
const fixture = `<div id="root"></div><script type="module">
import React from 'react';
import {createRoot} from 'react-dom/client';
import {FrontendKernel} from '/src/kernel/registry.ts';
import {RegisteredPanelHost} from '/src/kernel/RegisteredPanelHost.tsx';
const kernel = new FrontendKernel();
window.__panelRuntime = {kernel, faults: new Map()};
window.mounts = 0;
const item = {id:'probe',name:'Probe',version:'1.0.0',description:'Fixture',source:'official',enabled:true,state:'ready',problem:null,
 digest:'probe',assetBase:'',
 manifest:{manifestVersion:5,id:'probe',name:'Probe',version:'1.0.0',entries:{},panels:{main:{title:'Probe'}}},
 contract:{contractVersion:'1',module:'probe',actions:{},streams:{},events:{},extensionPoints:{},settings:{},$defs:{}}};
kernel.setDefinitions([item]);
await kernel.activate(item, ctx => ctx.panels.provide('main', (element,panel) => {
 window.mounts++;
 const root = createRoot(element);
 function Content() {
  React.useEffect(() => { panel.platform.panels.setStatus(panel.panelRef,{state:'idle',text:'',navigation:null}); }, []);
  return React.createElement('p',null,'Panel ready');
 }
 root.render(React.createElement(Content));
 return {dispose(){root.unmount();}};
}));
createRoot(document.getElementById('root')).render(React.createElement(React.StrictMode,null,
 React.createElement(RegisteredPanelHost,{id:'one',owner:'probe',state:{panelType:'probe.main',state:null},
 active:true,visible:true,onActivate(){},registerState(){}})));
</script>`;

beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    configFile: fileURLToPath(new URL("../../vite.config.ts", import.meta.url)),
    logLevel: "silent",
    // This server renders the fixture below, not the application's index.html.
    optimizeDeps: { entries: [] },
    cacheDir,
    plugins: [{ name: "panel-lifecycle-fixture", enforce: "pre",
      resolveId(source, importer) {
        if (source === "./runtime" && importer?.includes("RegisteredPanelHost.tsx")) return "\0panel-runtime";
      },
      load(id) { if (id === "\0panel-runtime") return "export const platformRuntime=()=>window.__panelRuntime;"; },
      configureServer(dev) {
        dev.middlewares.use("/__panel_lifecycle__", async (request, response) => {
          response.setHeader("Content-Type", "text/html");
          response.end(await dev.transformIndexHtml(request.originalUrl!, fixture));
        });
      },
    }],
    server: { host: "127.0.0.1", port: 18583, strictPort: true },
  });
  await server.listen(); origin = "http://127.0.0.1:18583";
  browser = await chromium.launch({ headless: true });
  context = await browser.newContext();
}, 60_000);

afterAll(async () => {
  await context?.close(); await browser?.close(); await server?.close();
  rmSync(cacheDir, { recursive: true, force: true });
}, 60_000);

it("does not create a retired child React root during StrictMode restoration", async () => {
  const page = await context.newPage(), errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(origin + "/__panel_lifecycle__");
  await page.getByText("Panel ready", { exact: true }).waitFor({ timeout: 60_000 });
  expect(await page.evaluate(() => (window as unknown as { mounts: number }).mounts)).toBe(1);
  expect(errors).toEqual([]);
  await page.close();
}, 90_000);
