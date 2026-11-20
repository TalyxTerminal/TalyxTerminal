import { afterEach, describe, expect, it, vi } from "vitest";
import { chromium } from "playwright";
import { talyxUiCss } from "../../../sdk/ui/src/ui/styles";
import {
  followUpColor,
  initUpColor,
  setUpColor,
  upColor,
} from "./upColor";

function stubDom() {
  const documentElement = { dataset: {} as Record<string, string> };
  vi.stubGlobal("document", { documentElement });
  return { documentElement };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("up colour preference", () => {
  it("defaults to green-up when nothing is set", () => {
    stubDom();
    expect(upColor()).toBe("green-up");
  });

  it("reads the value back off the document root and rejects unknown values", () => {
    const { documentElement } = stubDom();
    documentElement.dataset.upColor = "red-up";
    expect(upColor()).toBe("red-up");
    documentElement.dataset.upColor = "chartreuse-up";
    expect(upColor()).toBe("green-up");
  });

  it("writes the attribute used by the tokens", () => {
    const { documentElement } = stubDom();
    setUpColor("red-up");
    expect(documentElement.dataset.upColor).toBe("red-up");
    setUpColor("green-up");
    expect(documentElement.dataset.upColor).toBe("green-up");
  });

  it("changes a positive return's colour while a linked input warning keeps its state colour", async () => {
    const browser = await chromium.launch();
    try {
      const page = await browser.newPage();
      await page.setContent('<div class="tx-root"><b class="tx-up">+2%</b><div class="tx-linked-item"><span class="tx-linkedwarn">Missing input</span></div></div>');
      await page.addStyleTag({ content: talyxUiCss });
      const colours = () => page.locator(".tx-up, .tx-linkedwarn").evaluateAll(nodes => nodes.map(node => getComputedStyle(node).color));
      const green = await colours();
      await page.evaluate(() => { document.documentElement.dataset.upColor = "red-up"; });
      const red = await colours();
      expect(red[0]).not.toEqual(green[0]);
      expect(red[1]).toEqual(green[1]);
    } finally { await browser.close(); }
  }, 30000);

  it("starts at the default until the Host's value is read", () => {
    const { documentElement } = stubDom();
    initUpColor();
    expect(documentElement.dataset.upColor).toBe("green-up");
  });

  it("follows the platform setting as it applies in the window's workspace, and its changes", async () => {
    const { documentElement } = stubDom();
    let value = "red-up";
    const seen: { changed?: (item: unknown) => void } = {};
    const bridge = {
      call: vi.fn(async () => ({ values: { upColor: { value } } })),
      subscribe: vi.fn(async (_name: string, _params: unknown, callback: (item: unknown) => void) => { seen.changed = callback; }),
    };
    await followUpColor(bridge, "W1");
    expect(bridge.call).toHaveBeenCalledWith("platform.settings.read", { module: "platform", keys: ["upColor"], workspaceId: "W1" });
    expect(documentElement.dataset.upColor).toBe("red-up");
    value = "green-up";
    seen.changed!({ module: "platform", workspaceId: "W2", keys: ["upColor"] });          // another workspace's own value
    await Promise.resolve();
    expect(bridge.call).toHaveBeenCalledTimes(1);
    seen.changed!({ module: "platform", workspaceId: "global", keys: ["upColor"] });
    await vi.waitFor(() => expect(documentElement.dataset.upColor).toBe("green-up"));
  });
});
