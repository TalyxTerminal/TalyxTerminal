import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { ChecksColumn, KeyNumbersRow } from "./Work";
import { SettingsColumn } from "./Settings";
import { FailedBlock } from "./FailedBlock";
import { CHECKS, run, view, work } from "./fixtures";

vi.mock("@talyx/sdk", async importOriginal => {
  const original = await importOriginal<Record<string, unknown>>();
  return {...original, ModuleView: () => <i />};
});
vi.mock("../chart/Plot", async importOriginal => {
  const original = await importOriginal<Record<string, unknown>>();
  return {...original, Plot: () => <i />};
});
const base = {entry: "strategy.py", parameters: [{name: "lookback", label: "Lookback (bars)", type: "int", default: 48, file: "strategy.py", line: 6}], errors: [], params: {}, library: null, base: null, changed: new Set<string>(), inputs: [],
  riskFreeMean: 4.12, onParam: vi.fn(), onRun: vi.fn(), onRevert: vi.fn(), onLine: vi.fn(), onSwitch: vi.fn(), onOpenRun: vi.fn()};
const titles = (markup: string) => [...markup.matchAll(/ title="([^"]*)"/g)].map(item => item[1].replace(/&amp;/g, "&").replace(/&#x27;/g, "'").replace(/&quot;/g, '"'));

/** What a hover may say on a strategy's page: a definition (how a number is worked out) or where the code declared a setting. Nothing that explains how to use the page. */
const DEFINITIONS: RegExp[] = [
  /^Total [+−]?[\d.,]+%/, /^annualized over (365 days|252 trading days)$/, /^A year: CAGR minus .+ CAGR$/, /^(Fees|Spread|Impact|Funding|Borrow) \d+% of gross/,
  /^Leveraged: margin = notional ÷ leverage\. Standard: margin = a fixed share of notional$/, /^The members of this run's universe, equally weighted each day$/, /^What this strategy trades, bought at the first open and held$/, /^Any Market symbol's daily closes, adjusted for splits and dividends$/,
  /^q\.param\(".+"\)/,
  // a group's one-line summary of its values, and the failure bar's button (the SDK's own words)
  /^\d[\d,]* [A-Z]+$/, /^[\d.]+×, (leveraged|standard)$/, /^from Market/, /^next (open|close)$/, /^(Buy and hold|Members, equal weight|[A-Z]+:[\w.^/:]+|[Nn]one)$/, /^Reg T$/, /^Open in the code$/, /^(Sharpe|Held-out|Walk|Neighbouring|Costs|Shuffled|Best five|Deflated|Probability|The five|out-of-sample|mean Sharpe|95th|Sharpe on)/i,
];
const unlisted = (markup: string) => titles(markup).filter(title => title !== "" && !DEFINITIONS.some(rule => rule.test(title)));

describe("The page's words", () => {
  it("no_title_outside_definitions: every hover on the settings column, the key numbers, the checks and a failure is a definition", () => {
    const columns = ["perpetual", "stock"].map(kind => renderToStaticMarkup(createElement(SettingsColumn, {...base, view: view(kind as never, kind === "stock" ? {benchmark: {kind: "symbol", symbol: "US:SPY"}} : {})} as never))).join("");
    const keys = renderToStaticMarkup(createElement(KeyNumbersRow, {work: work(), go: vi.fn()}));
    const checks = renderToStaticMarkup(createElement(ChecksColumn, {work: work({checks: CHECKS}), go: vi.fn()}));
    const failure = renderToStaticMarkup(createElement(FailedBlock, {run: run({state: "failed", failure: {file: "strategy.py", line: 7, error: "boom", stage: "run", simulatedAt: 1, instrument: null, cause: "", tradesBefore: null, equityBefore: null, locals: [], withoutPrice: null, inEngine: false, partial: false}}), onLine: vi.fn()}));
    expect(unlisted(columns + keys + checks + failure)).toEqual([]);
    expect(titles(columns + keys).length).toBeGreaterThan(3);
  });
  it("no greyed-out controls and no explanatory paragraph: what is not done does not appear", () => {
    const markup = renderToStaticMarkup(createElement(SettingsColumn, {...base, view: view("perpetual")} as never)) + renderToStaticMarkup(createElement(KeyNumbersRow, {work: work(), go: vi.fn()}));
    expect(markup).not.toMatch(/disabled=""/);
    for (const word of ["this round", "not supported", "coming", "Not available"]) expect(markup).not.toContain(word);
  });
  it("the page source writes no explanation under a heading: its only paragraphs are states and reasons", () => {
    const source = ["StrategyPage.tsx", "Work.tsx", "Results.tsx", "Settings.tsx"].map(name => readFileSync(new URL(`./${name}`, import.meta.url), "utf8")).join("\n");
    expect(source).not.toMatch(/Publish/);
    expect(source).not.toMatch(/placeholder="[^"]*(explain|how to)/i);
  });
});

describe("changed since the last run", () => {
  it("a run still preparing its data (no run settings yet) does not make every run setting read as changed", () => {
    const page = readFileSync(new URL("./StrategyPage.tsx", import.meta.url), "utf8");
    expect(page).toMatch(/\["queued", "preparing"\]\.includes\(base\.state\) \? differing\(settings\.effective, base\.settings\.run\) : new Set/);
  });
});

describe("a rebalance", () => {
  it("lists what is held first (largest weight, then rank 1 down), and a name not held reads Not held, not Long and Filled", () => {
    const page = readFileSync(new URL("./Holdings.tsx", import.meta.url), "utf8");
    expect(page).toContain("names.sort((a, b) => weight(b) - weight(a) || (row?.ranks?.[a] ?? Infinity) - (row?.ranks?.[b] ?? Infinity))");
    expect(page).toContain('"Not held"');
    expect(page).toContain('(target || held ? t("Filled") : "")');
  });
});
