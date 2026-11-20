import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import type { ModuleContext } from "@talyx/sdk";
import { factorApi, type FactorRun, type ResultsFigure } from "./api";
import * as Pages from "./Results";
import { Fig, IndustryTab, StyleTab, unplaced, wantsTab } from "./Results";

// A chart is noted (its figure's key, the height it was given) and stands in as an empty element, so the boxes around it can be read.
const plotted: {key: string | undefined; height: number | undefined}[] = [];
vi.mock("../chart/Plot", async importOriginal => {
  const original = await importOriginal<Record<string, unknown>>();
  return {...original, Plot: (props: {figure: {layout?: {meta?: {key?: string}}}; height?: number}) => {
    plotted.push({key: props.figure.layout?.meta?.key, height: props.height});
    return <i data-plot={props.figure.layout?.meta?.key} />;
  }};
});
const css = readFileSync(new URL("./factor.css", import.meta.url), "utf8");
const source = readFileSync(new URL("./Results.tsx", import.meta.url), "utf8");
const html = (node: ReturnType<typeof createElement>) => renderToStaticMarkup(node);
const drawn = (key: string, title = key) => ({key, title, figure: {data: [], layout: {meta: {key}}}}) as unknown as ResultsFigure;
/** The chart boxes of a markup, in order: the size the box asks for and which figure is in it. */
const boxes = (markup: string) => [...markup.matchAll(/<div class="fx-rch"(?: data-size="(\w+)")?[^>]*>(.*?)<\/div>/g)].map(item => ({size: item[1] ?? "", key: /data-plot="([^"]+)"/.exec(item[2])?.[1]}));

describe("Factor Results: what could not be made says why where it would be", () => {
  it("a figure that could not be drawn gives its reason in its place", () => {
    const fig = {key: "factor.results.sectors", title: "Inside each sector", unavailable: "Market gives no industry for these names"} as ResultsFigure;
    const markup = html(createElement(Fig, {fig, label: "Sectors"}));
    expect(markup).toContain("Inside each sector could not be drawn");
    expect(markup).toContain("Market gives no industry for these names");
  });
  it("a figure the tab does not carry gives the tab's own reason; there is no made-up sentence of the page's own", () => {
    expect(html(createElement(Fig, {fig: undefined, why: "Too few days", label: "x"}))).toContain("Too few days");
    // The backend writes a reason for everything it could not make (test_every_table_and_figure_is_there_or_says_why).
    expect(html(createElement(Fig, {fig: undefined, label: "x"}))).toBe("");
  });
  it("every chart and table of a tab with nothing to show writes its reason, none stays silent", () => {
    const sectors = {key: "factor.results.sectors", title: "Inside each sector", unavailable: "no industry"};
    const size = {key: "factor.results.sizeAndPeriod", title: "By size and period", unavailable: "no size"};
    const decay = {key: "factor.results.decay", title: "IC by holding days", unavailable: "no rank IC"};
    const tab = {tab: "industry", holding: 5, unit: "day", periods: [1, 5], periodLabels: ["1D", "5D"], figures: {sectors, sizeAndPeriod: size, decay}, missing: {sectors: "no industry", sizeAndPeriod: "no size", decay: "no rank IC", method: "no method"}};
    const markup = html(createElement(IndustryTab, {tab: tab as never}));
    for (const reason of ["no industry", "no size", "no rank IC", "no method"]) expect(markup).toContain(reason);
    expect(markup.match(/fx-why/g)?.length).toBeGreaterThanOrEqual(4);
  });
  it("the style tab names why the styles and the cleaned IC are missing", () => {
    const like = {key: "factor.results.like", title: "Like this factor", unavailable: "No library factor shares days with this run"};
    const tab = {tab: "style", holding: 5, unit: "day", periods: [5], periodLabels: ["5D"], version: null, figures: {like}, missing: {like: "No library factor shares days with this run", styles: "too few names", residual: "too few names"}};
    const markup = html(createElement(StyleTab, {tab: tab as never}));
    expect(markup).toContain("No library factor shares days with this run");
    expect(markup).toContain("too few names");
  });
  it("a missing section the tab has no place for is still listed, so nothing is dropped silently", () => {
    const tab = {tab: "costs" as const, missing: {split: "placed", surprise: "Something new the backend could not build"}};
    expect(unplaced(tab)).toEqual([["surprise", "Something new the backend could not build"]]);
    expect(unplaced({tab: "event", missing: {event: "a", table: "b"}})).toEqual([]);
  });
});

describe("Factor Results: reading", () => {
  it("only the selected tab is read, and only once its build is done", () => {
    expect(wantsTab("returns", "done", false)).toBe(true);
    expect(wantsTab("ic", "failed", false)).toBe(true);
    for (const status of ["waiting", "running", undefined]) expect(wantsTab("costs", status, false)).toBe(false);
    expect(wantsTab("settings", "done", false)).toBe(false);
    expect(wantsTab("returns", undefined, true)).toBe(true);
  });
  it("compared runs are written as repeated vs parameters, never joined", async () => {
    const seen: string[] = [];
    const ctx = {routes: {fetch: async (path: string) => {seen.push(path); return {ok: true, json: async () => ({})};}}} as unknown as ModuleContext;
    await factorApi(ctx).results("run-1", {tab: "returns", vs: ["run-2", "run-3", "run-4"]});
    await factorApi(ctx).results("run-1", {tab: "ic", cell: 3});
    await factorApi(ctx).results("run-1");
    expect(seen[0]).toBe("/factor-runs/run-1/results?tab=returns&vs=run-2&vs=run-3&vs=run-4");
    expect(seen[1]).toBe("/factor-runs/run-1/results?tab=ic&cell=3");
    expect(seen[2]).toBe("/factor-runs/run-1/results");
  });
});


describe("Factor Results: the charts take the height of their box", () => {
  it("charts take their box height, not a fixed one", () => {
    plotted.length = 0;
    const markup = html(createElement(Fig, {fig: drawn("factor.results.groups"), label: "Groups", size: "l"} as never));
    expect(boxes(markup)).toEqual([{size: "l", key: "factor.results.groups"}]);
    expect(plotted.every(item => item.height === undefined)).toBe(true);  // no height given: the box decides
    expect(css).toMatch(/\.fx-rscroll\s*\{[^}]*container-type:\s*size/);
    expect(css).toMatch(/\.fx-rch\s*\{[^}]*height:\s*max\(240px,\s*38cqh\)/);
    expect(css).toMatch(/\.fx-rch\[data-size="s"\]\s*\{[^}]*height:\s*max\(190px,\s*30cqh\)/);
    expect(css).toMatch(/\.fx-rch\[data-size="l"\]\s*\{[^}]*height:\s*max\(300px,\s*48cqh\)/);
    expect(source).not.toMatch(/height=\{\d+\}/);  // no fixed pixel heights left on the figures
  });
  it("each section takes the size the draft gives it", () => {
    const tab = (name: string, keys: string[], more: object = {}) => ({tab: name, holding: 5, unit: "day", periods: [1, 5, 10], periodLabels: ["1D", "5D", "10D"], missing: {},
      figures: Object.fromEntries(keys.map(key => [key.split(".").pop()!, drawn(`factor.results.${key}`)])), ...more}) as never;
    const Returns = (Pages as Record<string, never>).ReturnsTab, Ic = (Pages as Record<string, never>).IcTab, Turnover = (Pages as Record<string, never>).TurnoverTab,
      Costs = (Pages as Record<string, never>).CostsTab, Event = (Pages as Record<string, never>).EventTab;
    const sizes = (component: never, value: never) => Object.fromEntries(boxes(html(createElement(component, {tab: value}))).map(item => [item.key, item.size]));
    expect(sizes(Returns, tab("returns", ["groups", "meanByGroup", "violin", "factorWeighted", "spread"], {figures: {groups: drawn("factor.results.groups"), meanByGroup: drawn("factor.results.meanByGroup"),
      violin: drawn("factor.results.violin"), factorWeighted: drawn("factor.results.factorWeighted"), spread: drawn("factor.results.spread")}}))).toEqual({
      "factor.results.groups": "l", "factor.results.meanByGroup": "", "factor.results.violin": "", "factor.results.factorWeighted": "s", "factor.results.spread": "s"});
    expect(sizes(Ic, tab("ic", [], {figures: {daily: drawn("factor.results.daily"), histogram: drawn("factor.results.histogram"), qq: drawn("factor.results.qq"), years: drawn("factor.results.years"),
      monthly: drawn("factor.results.monthly")}}))).toEqual({"factor.results.daily": "", "factor.results.histogram": "s", "factor.results.qq": "s", "factor.results.monthly": "rows"});
    // One year is the table's one row: the bars come from two years on.
    const year = (y: number) => ({year: y, rankIc: .01, t: 1, net: .01, sharpe: 1, turnover: .1});
    expect(sizes(Ic, tab("ic", [], {years: [year(2024), year(2025)], figures: {years: drawn("factor.results.years")}}))["factor.results.years"]).toBe("s");
    expect(sizes(Turnover, tab("turnover", [], {figures: {topBottom: drawn("factor.results.topBottom"), autocorrelation: drawn("factor.results.autocorrelation")}}))).toEqual({
      "factor.results.topBottom": "", "factor.results.autocorrelation": "s"});
    expect(sizes(Costs, tab("costs", [], {figures: {byCost: drawn("factor.results.byCost")}}))).toEqual({"factor.results.byCost": "s"});
    expect(sizes(Event, tab("event", [], {figures: {event: drawn("factor.results.event")}}))).toEqual({"factor.results.event": "l"});
    const industry = {tab: "industry", holding: 5, unit: "day", periods: [5], periodLabels: ["5D"], missing: {}, sectors: Array(11).fill({}), figures: {sectors: drawn("factor.results.sectors"), decay: drawn("factor.results.decay")},
      decay: {neutralize: "industry+size", before: Array(20).fill(.01), after: Array(20).fill(.02)}} as never;
    const found = sizes(IndustryTab as never, industry);
    expect(found["factor.results.sectors"]).toBe("rows");
    expect(found["factor.results.decay"]).toBe("");
  });
  it("the event study says what its returns are relative to, as the run's settings measured them", () => {
    const Event = (Pages as Record<string, never>).EventTab;
    const event = (basis: string) => html(createElement(Event, {tab: {tab: "event", holding: 5, unit: "day", periods: [5], periodLabels: ["5D"], missing: {}, figures: {}, basis} as never}));
    expect(event("all")).toContain("20 after, relative to all names");
    expect(event("group")).toContain("relative to the names of the same industry");
    expect(event("raw")).not.toContain("relative to");
  });
  it("a chart of rows is as tall as its rows", () => {
    const months = {...drawn("factor.results.monthly"), figure: {data: [{y: Array(12).fill(0)}], layout: {meta: {key: "factor.results.monthly"}}}} as never;
    const Ic = (Pages as Record<string, never>).IcTab;
    const ic = html(createElement(Ic, {tab: {tab: "ic", holding: 5, unit: "day", periods: [5], periodLabels: ["5D"], missing: {}, figures: {monthly: months}} as never}));
    expect(ic).toMatch(/data-size="rows" style="height:342px"/);  // 30 + 12 rows x 26 px
    const industry = {tab: "industry", holding: 5, unit: "day", periods: [5], periodLabels: ["5D"], missing: {}, sectors: Array(11).fill({}), figures: {sectors: drawn("factor.results.sectors")}, decay: null} as never;
    expect(html(createElement(IndustryTab as never, {tab: industry}))).toMatch(/data-size="rows" style="height:352px"/);  // 44 + 11 rows x 28 px
  });
});

describe("Factor Results: the Industry, Style and Costs pages", () => {
  it("size and period is a table", () => {
    plotted.length = 0;
    const rows = [{name: "Small third by size", kind: "size", rankIc: .0212, note: "close × shares outstanding"}, {name: "Large third by size", kind: "size", rankIc: -.004, note: ""},
      {name: "First half, 2017–2021", kind: "period", rankIc: .031, note: "Jan 2017 to Dec 2021"}, {name: "Second half, 2021–2026", kind: "period", rankIc: .012, note: ""}];
    const tab = {tab: "industry", holding: 5, unit: "day", periods: [5], periodLabels: ["5D"], missing: {}, figures: {}, sizeAndPeriod: rows, sectors: [], decay: null} as never;
    const markup = html(createElement(IndustryTab, {tab}));
    for (const text of ["Small third by size", "+0.0212", "Large third by size", "−0.0040", "First half, 2017–2021", "+0.0310"]) expect(markup, text).toContain(text);
    expect(/<table[^>]*>(?:(?!<\/table>).)*Small third by size/.test(markup)).toBe(true);
    expect(plotted.map(item => item.key)).not.toContain("factor.results.ic-by-size");
  });
  it("the style page puts the styles beside the cleaned IC, and what is like this factor under them", () => {
    const tab = {tab: "style", holding: 5, unit: "day", periods: [5], periodLabels: ["5D"], version: "Styles v1", missing: {}, figures: {like: drawn("factor.results.like")},
      styles: [{style: "Size", family: "price", rho: .1, measuredAs: "Log market value"}], like: [], residual: {rankIc: .02, before: .03, t: 2.1, styles: ["Size"]}} as never;
    const markup = html(createElement(StyleTab, {tab}));
    const two = /<div class="fx-rtwo"[^>]*>(.*?)<\/div><section class="fx-rsec">(?:(?!<section).)*Like this factor/s.exec(markup);
    expect(two, "two columns, then what is like it").not.toBeNull();
    expect(two![1]).toContain("Styles");
    expect(two![1]).toContain("Rank IC after taking out");
    expect(two![1]).not.toContain("Like this factor");
  });
  it("no row says Market does not give", () => {
    const tab = {tab: "style", holding: 5, unit: "day", periods: [5], periodLabels: ["5D"], version: "Styles v1", missing: {}, figures: {}, like: [], residual: null,
      styles: [{style: "Size", family: "price", rho: .1, measuredAs: "Log market value"}, {style: "Momentum", family: "price", rho: -.05, measuredAs: "12-1 month return"}]} as never;
    const markup = html(createElement(StyleTab, {tab}));
    expect(markup).not.toMatch(/Market (has|does|gives)/);
    for (const style of ["Value", "Profitability", "Growth", "Leverage", "Dividend"]) expect(markup).not.toContain(style);
    expect(source).not.toMatch(/Market (has not|does not give|has no)/);
  });
  it("the costs page says loses before costs when the break-even is negative", () => {
    const Costs = (Pages as Record<string, never>).CostsTab;
    const tab = (even: number) => ({tab: "costs", holding: 5, unit: "day", periods: [5], periodLabels: ["5D"], missing: {}, figures: {}, holdings: null,
      split: {gross: -.01, tradingLong: .01, tradingShort: .01, borrow: .003, net: -.033, breakEven: even, costBps: 5, borrowPct: .3, traded: 20}}) as never;
    expect(html(createElement(Costs, {tab: tab(-4.2)}))).toContain("loses before costs");
    expect(html(createElement(Costs, {tab: tab(14.8)}))).toContain("14.8 bps");
    expect(html(createElement(Costs, {tab: tab(14.8)}))).not.toContain("loses before costs");
  });
  it("no sentence explains the page", () => {
    for (const text of ["They are built on request, in the background", "Work's numbers are in; the Results keep building in the background"]) expect(source).not.toContain(text);
    expect(source).not.toMatch(/sub="[^"]*(each dot a window|under the line)/);
  });
});
