// A window in a real browser (headless Chromium), calling over /ws/bridge as the page's bridge does: binary frames,
// an envelope line then the body, TextDecoder and JSON.parse. Calls go one after another and are timed in the page.
// node window_client.mjs <ws url> <capability> <request.json> <warm-up calls> <calls> <UI URL> -> {"durations": [ms, ...]}
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const { chromium } = createRequire(join(root, "web/package.json"))("playwright");
const [url, capability, requestFile, warm, count, uiUrl] = process.argv.slice(2);
const body = readFileSync(requestFile, "utf8");
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  await page.goto(new URL("/api/platform/health", uiUrl).href);
  const result = await page.evaluate(async ({ url, capability, body, warm, count }) => {
    const encoder = new TextEncoder(), decoder = new TextDecoder();
    const socket = new WebSocket(url);
    socket.binaryType = "arraybuffer";
    let waiting = null, failed = null, ids = 0;
    const welcomed = new Promise((resolve, reject) => { waiting = resolve; failed = reject; });
    socket.onerror = () => failed(new Error("The test window could not reach its bridge"));
    socket.onclose = event => failed(new Error("The test window bridge closed: " + event.code));
    socket.onmessage = event => {
      const text = decoder.decode(event.data), cut = text.indexOf("\n");
      const envelope = JSON.parse(text.slice(0, cut));
      const value = cut === text.length - 1 ? null : JSON.parse(text.slice(cut + 1));
      waiting(envelope.t === "result" || envelope.t === "welcome" ? value : { error: envelope });
    };
    await welcomed;
    const call = () => new Promise((resolve, reject) => {
      waiting = resolve; failed = reject;
      socket.send(encoder.encode(JSON.stringify({ t: "call", id: `w${++ids}`, cap: capability }) + "\n" + body));
    });
    let last = null;
    for (let i = 0; i < warm; i++) last = await call();
    if (last?.error) return { error: last.error };
    const durations = [];
    for (let i = 0; i < count; i++) {
      const started = performance.now();
      last = await call();
      durations.push(performance.now() - started);
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    socket.close();
    return { durations, rows: last?.columns?.time?.length ?? 0 };
  }, { url, capability, body, warm: Number(warm), count: Number(count) });
  console.log(JSON.stringify(result));
} finally {
  await browser.close();
}
