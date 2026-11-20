import { describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { FirstRun, NoResultsYet, viaWork } from "./FirstRun";

describe("the first run", () => {
  it("lists what the run will use and marks what it still needs", () => {
    const markup = renderToStaticMarkup(createElement(FirstRun, {lines: [{label: "Names", value: "S&P 500 members"}, {label: "Formula", value: "none yet", missing: true}]}));
    expect(markup).toContain("#1");
    expect(markup).toMatch(/<div class="qx-first-line"><dt>Names<\/dt><dd><span>S&amp;P 500 members<\/span><\/dd><\/div>/);
    expect(markup).toMatch(/<div class="qx-first-line" data-missing="">.*Formula/);
  });
  it("on Results each line goes back to Work, then opens its place there", () => {
    vi.useFakeTimers();
    const order: string[] = [];
    const [line] = viaWork([{label: "Names", value: "x", onClick: () => order.push("open")}], () => order.push("work"));
    line.onClick!();
    expect(order).toEqual(["work"]);
    vi.runAllTimers();
    expect(order).toEqual(["work", "open"]);
    vi.useRealTimers();
    expect(renderToStaticMarkup(createElement(NoResultsYet, {lines: [], toWork: () => undefined}))).toContain("The Results of #1 come here when it finishes.");
  });
});
