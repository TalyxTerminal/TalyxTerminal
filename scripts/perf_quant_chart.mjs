/** Click a finished strategy's Run tab in the real workspace and time the embedded Chart through paint. */
import { createRequire } from "node:module";

const require = createRequire(new URL("../web/package.json", import.meta.url));
const { chromium } = require("playwright");
const [url, objectName] = process.argv.slice(2);
if (!url || !objectName) throw new Error("Usage: node scripts/perf_quant_chart.mjs <trial workspace URL> <open strategy name>");
const browser = await chromium.launch();
try {
  const page = await browser.newPage({viewport: {width: 1440, height: 900}});
  await page.goto(url);
  const frame = page.frameLocator('iframe[src*="workspaceFrame"]');
  const strategy = frame.locator(".fx-page").filter({has: frame.getByText(objectName, {exact: true})});
  const runs = strategy.getByRole("tab", {name: /^Runs/});
  await runs.click();
  const run = strategy.getByRole("tab", {name: /^Run(\s|#|$)/});
  // Capture the input event in the browser so Playwright's actionability checks do not enter the measurement.
  await run.evaluate(button => {
    button.addEventListener("click", () => { window.__quantChartClick = performance.now(); }, {capture: true, once: true});
  });
  await run.click();
  const drawing = strategy.locator(".chart-research-canvas canvas").first();
  await drawing.waitFor({state: "visible", timeout: 120_000});
  const ms = await drawing.evaluate(async () => {
    await new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)));
    return performance.now() - window.__quantChartClick;
  });
  console.log(`${objectName}: click to painted chart ${(ms / 1000).toFixed(3)} s`);
} finally {
  await browser.close();
}

