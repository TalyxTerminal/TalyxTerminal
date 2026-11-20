// The browser half of scripts/perf.py: drives the real app in headless Chromium (Playwright from web/node_modules) and says
// what it saw, one line per event on stdout ("@ {json}"). It is started by perf.py, not by hand.
//   node scripts/perf_browser.mjs startup|load '<json options>'
// startup: open the browser, say "ready", wait for "go" on stdin (perf.py has started the Host by then), open the saved
//          workspace from the home screen as a user does, and say when the home screen showed, the click, and the moment the
//          workspace was usable.
// load:    open the workspace, say "usable" with the charts it shows, wait for "go", then for the given seconds switch a chart's
//          timeframe and open other symbols in another chart (each timed from the click to the new picture), start the batch
//          download first when asked, and say every number it collected, with each chart bar update the page received.
import { createRequire } from "node:module";
import { createInterface } from "node:readline";

const require = createRequire(new URL("../web/package.json", import.meta.url));
const { chromium } = require("playwright");
const [mode, optionsJson] = process.argv.slice(2);
const options = JSON.parse(optionsJson);
const say = event => process.stdout.write("@ " + JSON.stringify(event) + "\n");
const sleep = ms => new Promise(done => setTimeout(done, ms));
const input = createInterface({ input: process.stdin })[Symbol.asyncIterator]();
const waitForGo = async () => { const line = await input.next(); if (line.done || line.value.trim() !== "go") throw new Error("perf.py did not send go"); };

// What "usable" means per module panel in the workspace: the panel shows what a user came for, not a placeholder.
// Modules not listed only need the page free of loading skeletons.
const PANEL_READY = {
  "module:market": () => Boolean(document.querySelector(".market-start, .market-sets tbody tr, .market-series tbody tr")),
};
// A chart's legend is its "name · timeframe · venue" line; its card (two levels up) has the prices once data is there.
// By its class, not its text: Market's running download rows ("AAPL +603 · Daily · from …") read the same way, and
// while "Update all" ran they came first, so the scenario timed the download row instead of chart 1.
const LEGEND = ".chart-legend-title";
const hasPrices = text => /O[\d,]+\.?\d*H[\d,]+\.?\d*L/.test(text.replace(/\s/g, ""));

const browser = await chromium.launch({
  channel: "chromium",   // the full Chromium in its new headless mode: what a person's Chrome is, unlike the headless shell
  args: ["--disable-background-timer-throttling", "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows"],
});
const context = await browser.newContext({ viewport: { width: options.width, height: options.height } });
const page = await context.newPage();
page.setDefaultTimeout(30000);
const pageErrors = [];
page.on("pageerror", error => pageErrors.push(String(error.message).split("\n")[0].slice(0, 200)));

async function workspaceFrame(timeout) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    for (const frame of page.frames()) {
      if (!frame.url().includes("workspaceFrame=1")) continue;
      const element = await frame.frameElement().catch(() => null);
      if (element && await element.isVisible() && await element.getAttribute("title") === "Workspace " + options.workspace) return frame;
    }
    await sleep(25);
  }
  throw new Error(`the window of workspace ${options.workspace} did not show within ${timeout / 1000} s`);
}

/** Null when the workspace is usable, else what it still waits for. */
async function missing(frame) {
  const failed = await frame.evaluate(({ expected, ready }) => {
    if (document.querySelector(".tx-skeleton")) return "a loading skeleton is still showing";
    for (const [kind, check] of Object.entries(ready)) if (expected.panels.includes(kind) && !new Function("return (" + check + ")()")()) return kind + " has not shown its content";
    return null;
  }, { expected: options.expected, ready: Object.fromEntries(Object.entries(PANEL_READY).map(([kind, check]) => [kind, check.toString()])) }).catch(() => "the window is reloading");
  if (failed) return failed;
  if (options.expected.charts) {
    const cards = await frame.locator(LEGEND).evaluateAll(items => items.map(item => item.parentElement.parentElement.innerText)).catch(() => []);
    if (cards.length < options.expected.charts) return `${cards.length} of ${options.expected.charts} charts show`;
    if (!cards.every(hasPrices)) return "a chart has no prices yet";
  }
  return null;
}

async function waitUsable(frame, timeout) {
  const end = Date.now() + timeout;
  let why = "";
  while (Date.now() < end) {
    why = await missing(frame);
    if (!why) return;
    await sleep(100);
  }
  throw new Error("the workspace was not usable after " + timeout / 1000 + " s: " + why);
}

async function startup() {
  say({ event: "ready" });
  await waitForGo();
  const marks = { pageStart: Date.now() };
  await page.goto(options.base + "/", { waitUntil: "load", timeout: 120000 });
  const workspaceButton = page.getByRole("button", { name: "Workspace", exact: true });
  await workspaceButton.waitFor({ timeout: 120000 });
  marks.homeShown = Date.now();
  await workspaceButton.click();
  await page.getByRole("button", { name: "Open " + options.workspace, exact: true }).click({ timeout: 120000 });
  marks.openClicked = Date.now();
  const frame = await workspaceFrame(120000);
  marks.windowShown = Date.now();
  await waitUsable(frame, 120000);
  marks.usable = Date.now();
  say({ event: "result", marks, pageErrors });
}

/** Shows the saved workspace. With nothing open before (options.restored false) a user's way: the home screen's Open. When the Host
 *  restored open workspaces, the window already shows the last of them: the home window's own "open this saved workspace" command
 *  (what its Open button sends) then brings this one up, opening it first when needed. */
async function openSaved() {
  await page.goto(options.base + "/", { waitUntil: "load", timeout: 120000 });
  if (!options.restored) {
    const workspaceButton = page.getByRole("button", { name: "Workspace", exact: true });
    await workspaceButton.waitFor({ timeout: 120000 });
    await workspaceButton.click();
    await page.getByRole("button", { name: "Open " + options.workspace, exact: true }).click({ timeout: 120000 });
  } else {
    const end = Date.now() + 120000;
    while (!page.frames().some(frame => frame.url().includes("workspaceFrame=1")) && Date.now() < end) await sleep(100);
    await page.evaluate(({ snapshotId, name }) => new Promise((resolve, reject) => {
      const requestId = crypto.randomUUID();
      const receive = event => {
        if (event.source !== window || event.data?.channel !== "talyx-workspace-window" || event.data.command || event.data.requestId !== requestId) return;
        window.removeEventListener("message", receive);
        if (event.data.error) reject(new Error(event.data.error)); else resolve();
      };
      window.addEventListener("message", receive);
      window.postMessage({ channel: "talyx-workspace-window", requestId, command: { kind: "open", snapshotId, name, presentation: null } }, location.origin);
    }), { snapshotId: options.snapshotId, name: options.workspace });
  }
  const frame = await workspaceFrame(120000);
  await waitUsable(frame, 120000);
  return frame;
}

// Installed in every frame: the time of each chart bar update the page receives (and of the frame that drew it), and the
// time between animation frames.
const PROBE = () => {
  if (window.__perf) return;
  const state = window.__perf = { bars: [], dts: [] };
  const descriptor = Object.getOwnPropertyDescriptor(MessageEvent.prototype, "data");
  const decoder = new TextDecoder();
  Object.defineProperty(MessageEvent.prototype, "data", {
    configurable: true,
    get() {
      const value = descriptor.get.call(this);
      if (this.target instanceof WebSocket && !this.__perfSeen) {
        this.__perfSeen = true;
        const text = typeof value === "string" ? value : decoder.decode(value);
        if (text.includes('"replay":') && text.includes('"bar":{')) {
          const entry = { at: Date.now(), frame: null, bars: [...text.matchAll(/"replay":(true|false),"bar":(\{[^}]*\})/g)].map(found => ({ ...JSON.parse(found[2]), replay: found[1] === "true" })) };
          state.bars.push(entry);
          requestAnimationFrame(() => { entry.frame = Date.now(); });
        }
      }
      return value;
    },
  });
  let last = performance.now();
  const tick = now => { state.dts.push(now - last); last = now; requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
};

async function load() {
  let frame = await openSaved();
  if (!options.expected.charts) throw new Error("the workspace shows no chart: the load scenario switches and opens charts");
  await Promise.all(page.frames().map(each => each.evaluate(PROBE).catch(() => undefined)));
  const legends = frame.locator(LEGEND);
  const cards = () => legends.evaluateAll(items => items.map(item => item.parentElement.parentElement.innerText)).catch(() => []);
  const toolbar = frame.getByRole("toolbar", { name: "Chart" });
  const first = (await cards())[0].split("\n")[0];
  // The chart's watchlist (the one last chosen in this workspace) is part of the load: its quotes stream meanwhile.
  await frame.getByRole("toolbar", { name: "Side panels" }).getByRole("button", { name: "Watchlist" }).click();
  const watchlist = frame.getByRole("complementary", { name: "Watchlist" });
  await watchlist.waitFor();
  await sleep(2000);
  say({ event: "usable", charts: (await cards()).map(text => text.split("\n")[0]),
        watchlist: (await watchlist.getByRole("button").first().innerText()).trim() });
  await waitForGo();

  const t0 = Date.now();
  const end = t0 + options.seconds * 1000;
  if (options.batch !== "none") await startBatch(frame);
  const switches = [], opens = [];
  const current = (await cards())[0].split("\n")[0].split(" · ")[1];
  const timeframes = [current === "1D" ? "1W" : "1D", current];
  const last = (await cards()).length - 1;
  let n = 0;
  do {
    await legends.nth(0).click({ position: { x: 5, y: 5 } });
    await sleep(200);
    for (const want of timeframes) {
      const before = (await cards())[0];
      const started = Date.now();
      await toolbar.getByRole("button", { name: want, exact: true }).click();
      const done = await waitFor(async () => { const text = (await cards())[0]; return text.includes(" · " + want + " · ") && hasPrices(text) && prices(text) !== prices(before); });
      switches.push({ ms: Date.now() - started, ok: done, at: started, want, shown: done ? null : (await cards())[0].split("\n").slice(0, 5).join(" | ") });
      await sleep(700);
    }
    await legends.nth(last).click({ position: { x: 5, y: 5 } });
    await sleep(200);
    const before = (await cards())[last];
    // The toolbar's first button names the active chart's symbol: opening the one it already shows changes nothing.
    const showing = (await toolbar.getByRole("button").first().innerText()).trim();
    if (options.symbols[n % options.symbols.length] === showing) n++;
    await toolbar.getByRole("button").first().click();
    await sleep(400);
    const want = options.symbols[n++ % options.symbols.length];
    await frame.getByRole("combobox", { name: "Search instruments" }).fill(want);
    await sleep(1500);
    const started = Date.now();
    await page.keyboard.press("Enter");
    const done = await waitFor(async () => { const text = (await cards())[last]; return hasPrices(text) && text.split(" · ")[0] !== before.split(" · ")[0]; });
    opens.push({ ms: Date.now() - started, ok: done, at: started, want, shown: done ? null : (await cards())[last].split("\n").slice(0, 5).join(" | ") });
    await sleep(1500);
  } while (Date.now() < end - 8000);
  while (Date.now() < end) await sleep(500);
  const t1 = Date.now();

  const frames = [];
  for (const each of page.frames()) {
    const got = await each.evaluate(() => window.__perf ? { bars: window.__perf.bars, dts: window.__perf.dts } : null).catch(() => null);
    if (got && each === frame) frames.push(got);
  }
  say({ event: "result", t0, t1, first, switches, opens, bars: frames.flatMap(each => each.bars), frameTimes: frames.flatMap(each => each.dts), pageErrors });
}

const prices = text => text.split("\n").slice(1, 5).join();
async function waitFor(condition, limit = 5000) {
  const end = Date.now() + limit;
  while (Date.now() < end) { if (await condition()) return true; await sleep(10); }
  return false;
}

/** The batch download the Market panel offers: S&P 500 members, one year of one bar size; or its "Update all". */
async function startBatch(frame) {
  if (options.batch === "update all") {
    await frame.getByRole("button", { name: "Update all" }).click();
    await sleep(1000);
    return;
  }
  await frame.getByRole("button", { name: "Get data" }).click();
  await sleep(800);
  const dialog = frame.getByRole("dialog", { name: "Get data" });
  await dialog.getByRole("button", { name: /S&P 500 members/ }).click();
  await sleep(800);
  for (const [name, want] of [["Daily", false], ["Corporate actions", false], ["1 hour", options.batch === "1 hour"], ["1 minute", options.batch === "1 minute"]]) {
    const box = dialog.getByRole("checkbox", { name });
    if ((await box.isChecked()) !== want) await box.click();
  }
  await dialog.getByRole("button", { name: "1 year" }).click();
  await sleep(300);
  await dialog.getByRole("button", { name: /^Get \d/ }).click();
  await sleep(1000);
}

let code = 0;
try {
  if (mode === "startup") await startup();
  else if (mode === "load") await load();
  else throw new Error("mode: startup or load");
} catch (error) {
  say({ event: "error", message: String(error && error.message || error).split("\n")[0], pageErrors });
  code = 1;
} finally {
  await browser.close().catch(() => undefined);
}
process.exit(code);
