/** Persistent Playwright browser for disposable local application trials. */
import { createRequire } from "node:module";
import { createInterface } from "node:readline";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const require = createRequire(new URL("../web/package.json", import.meta.url));
const { chromium } = require("playwright");
const url = process.argv[2];
if (!url) throw new Error("Usage: node scripts/dev_browser.mjs <url>");
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
const errors = [];
let trace = null;
page.on("pageerror", error => errors.push(error.message));
await page.goto(url);
console.log(JSON.stringify({ ready: true, url: page.url() }));
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const input = createInterface({ input: process.stdin });
try {
  for await (const line of input) {
    try {
      const request = JSON.parse(line);
      if (request.op === "close") break;
      if (request.op === "trace_start") {
        if (trace) throw new Error("A performance trace is already running");
        const client = await context.newCDPSession(page);
        const events = [];
        client.on("Tracing.dataCollected", batch => events.push(...batch.value));
        await client.send("Tracing.start", {
          categories: "devtools.timeline,blink.user_timing,disabled-by-default-devtools.timeline.frame",
          transferMode: "ReportEvents",
        });
        trace = { client, events };
        console.log(JSON.stringify({ tracing: true }));
        continue;
      }
      if (request.op === "trace_stop") {
        if (!trace) throw new Error("No performance trace is running");
        if (typeof request.path !== "string" || !request.path) throw new Error("trace_stop requires path");
        const { client, events } = trace;
        const finished = new Promise(resolve => client.once("Tracing.tracingComplete", resolve));
        await client.send("Tracing.end");
        await finished;
        await client.detach();
        trace = null;
        const path = resolve(request.path);
        await mkdir(dirname(path), { recursive: true });
        await writeFile(path, JSON.stringify({ traceEvents: events }));
        console.log(JSON.stringify({ tracePath: path, events: events.length }));
        continue;
      }
      const result = await new AsyncFunction("page", "context", "browser", "errors", request.script)(page, context, browser, errors);
      console.log(JSON.stringify({ result: result ?? null }));
    } catch (error) {
      console.log(JSON.stringify({ error: error.message, stack: error.stack }));
    }
  }
} finally {
  input.close();
  process.stdin.destroy();
  await browser.close();
}
