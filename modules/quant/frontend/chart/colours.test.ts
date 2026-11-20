import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { expect, it } from "vitest";

const { chromium } = createRequire(new URL("../../../../web/package.json", import.meta.url))("playwright") as typeof import("playwright");

it("reverses price direction while keeping notebook and review status colours stable", async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    for (const path of ["../../../../docs/design/tokens.css", "../notebook.css", "../allocation/allocation.css"]) {
      await page.addStyleTag({ content: readFileSync(new URL(path, import.meta.url), "utf8") });
    }
    await page.locator("body").evaluate(body => {
      body.innerHTML = '<span class="al-up"></span><span class="al-down"></span><span class="qx-nb-kernel" data-state="ok"><i class="qx-nb-dot"></i></span><span class="al-reviewed"><i class="tx-icon"></i></span>';
    });
    const colours = (value: string) => page.evaluate(value => {
      document.documentElement.dataset.upColor = value;
      const style = (selector: string) => getComputedStyle(document.querySelector(selector)!);
      return { up: style(".al-up").color, down: style(".al-down").color,
        kernel: style(".qx-nb-dot").backgroundColor, reviewed: style(".al-reviewed i").color };
    }, value);
    const green = await colours("green-up");
    expect(green.up).not.toBe(green.down);
    expect(green.kernel).toBe(green.reviewed);
    expect(await colours("red-up")).toEqual({ ...green, up: green.down, down: green.up });
  } finally {
    await browser.close();
  }
}, 30_000);
