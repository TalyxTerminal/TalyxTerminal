import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { SettingsColumn, differing } from "./Settings";
import { run, view } from "./fixtures";

const base = {entry: "strategy.py", parameters: [], errors: [], params: {}, library: null, base: null, changed: new Set<string>(), inputs: [], riskFreeMean: null, onParam: vi.fn(), onRun: vi.fn(), onRevert: vi.fn(), onLine: vi.fn(), onSwitch: vi.fn(), onOpenRun: vi.fn()};
const html = (props: object) => renderToStaticMarkup(createElement(SettingsColumn, {...base, ...props} as never));
const param = (name: string, type: string, extra: object = {}) => ({name, label: name, type, default: 5, file: "strategy.py", line: 6, ...extra});

describe("A strategy's settings column", () => {
  it("code_params_come_from_declarations: one row for each setting the code declares, named as in the code", () => {
    const markup = html({view: view("perpetual"), parameters: [param("lookback", "int", {label: "Lookback (bars)"}), param("shorts", "bool", {default: true})], params: {lookback: 48}});
    expect(markup).toContain("Code settings");
    expect(markup).toContain(">Lookback<");  // the declared label, its trailing "(bars)" is the unit in the box
    expect(markup).toContain(">bars<");
    expect(markup).toContain(">shorts<");
    expect(markup).toContain('q.param(&quot;lookback&quot;), strategy.py line 6');
    expect(html({view: view("perpetual")})).toContain("None declared");
  });
  it("exchange_conditions_by_kind: a stock has no stop trigger or position mode, and its margin is one read-only line", () => {
    const stock = html({view: view("stock")});
    expect(stock).toContain("Reg T, 2× overnight");
    expect(stock).not.toContain("Stop trigger");
    expect(stock).not.toContain("Position mode");
    expect(stock).not.toContain("Liquidate below maintenance");
    const perpetual = html({view: view("perpetual")});
    for (const word of ["Stop trigger", "Position mode", "Liquidate below maintenance", "Leverage", "Leveraged", "Standard", "Last price", "Mark price", "Index price", "One-way", "Hedge"]) expect(perpetual).toContain(word);
    expect(perpetual).toContain("max 100×");
    expect(html({view: view("spot")})).not.toContain(">Exchange<");
  });
  it("costs: crypto has taker and maker fees, funding and the volume cap; stock has commission and borrow", () => {
    const crypto = html({view: view("perpetual")});
    for (const word of ["Taker fee", "Maker fee", "Half spread", "Impact", "Volume cap", "Funding", "from Market (okx)"]) expect(crypto).toContain(word);
    const stock = html({view: view("stock", {costs: {commissionBps: 1, halfSpreadBps: 2, impact: .3, volumeCapPct: null, borrowPct: 0.3, feeSource: "from Market"}})});
    for (const word of ["Commission", "Borrow", "Volume cap", "Half spread"]) expect(stock).toContain(word);
    expect(stock).not.toContain("Taker fee");
  });
  it("execution: fill timing, shorting by kind, the limit-order rule and halted names for stocks", () => {
    const stock = html({view: view("stock")});
    for (const word of ["Fill at", "Next bar open", "Next bar close", "Allowed", "Long only", "Fill on touch", "Through 1 tick", "Through N ticks", "Halted names", "Keep position", "Close at next price"]) expect(stock).toContain(word);
    expect(html({view: view("perpetual")})).toContain("No borrow");
    expect(html({view: view("perpetual", {limitThroughTicks: 3})})).toContain("ticks");
    expect(html({view: view("perpetual")})).not.toContain('aria-label="Ticks"');
  });
  it("measure against: the benchmark kinds of what is traded, the risk-free choices, and the average of the shown run", () => {
    const stock = html({view: view("stock", {benchmark: {kind: "beta"}}), riskFreeMean: 4.12});
    for (const word of ["Cash + β × S&amp;P 500", "S&amp;P 500 equal weight", "None", "T-bill (^IRX)", "Zero", "Fixed…", "avg 4.12%"]) expect(stock).toContain(word);
    expect(html({view: view("perpetual")})).toContain("BTC buy and hold");
    expect(html({view: view("perpetual", {riskFree: {basis: "fixed", pct: 3}})})).toContain("% a year");
  });
  it("several venues become a small table of balances and the currency it is reported in", () => {
    const markup = html({view: view("perpetual", {accounts: [{venue: "OKX", balances: ["10000 USDT"]}, {venue: "XNAS", balances: ["100000 USD"]}], reportCurrency: "USDT"})});
    expect(markup).toContain("Capital by venue");
    expect(markup).toContain("OKX balances");
    expect(markup).toContain("Reported in");
    expect(markup).toContain("2 venues");
  });
  it("changed_marked: settings that differ from the shown run are marked and counted with a way back", () => {
    const changed = differing({capital: 20_000, exchange: {leverage: 5, stopTrigger: "last"}}, {capital: 10_000, exchange: {leverage: 1, stopTrigger: "last"}});
    expect([...changed].sort()).toEqual(["capital", "exchange.leverage"]);
    const markup = html({view: view("perpetual"), changed: new Set(["capital", "exchange.leverage", "param:lookback"]), base: run({number: 11})});
    expect(markup).toContain("3 changed since #11");
    expect(markup).toContain("Revert");
    expect(html({view: view("perpetual"), changed: new Set(), base: run()})).not.toContain("changed since");
  });
  it("held_out_set_in_run_settings_only: the run settings have the date row; the data settings only show it", () => {
    expect(html({view: view("perpetual")})).toContain('aria-label="Held out from"');
    const data = readFileSync(new URL("../DataSettings.tsx", import.meta.url), "utf8");
    expect(data).not.toContain('aria-label="Held out from"');
  });
  it("marks a model called directly when the backtest starts before its training cutoff", () => {
    const card = {name: "Momentum LGBM", kind: "model" as const, line: 6, file: "strategy.py", pinned: {versionId: "v3", version: 3, trainedTo: "2024-06-30"}, fromRun: null};
    expect(html({view: view("perpetual"), since: "2023-01-01", inputs: [card]})).toContain("Backtest starts before this");
    expect(html({view: view("perpetual"), since: "2024-07-01", inputs: [card]})).not.toContain("Backtest starts before this");
    expect(html({view: view("perpetual"), since: "2023-01-01", inputs: [{...card, reads: "values" as const, pickedOn: "2025-04-30"}]})).not.toContain("Backtest starts before this");
  });
  it("Inputs used lists what the code reads with its version and offers the newer one", () => {
    const markup = html({view: view("perpetual"), inputs: [{name: "Momentum LGBM", kind: "model", line: 6, file: "strategy.py", pinned: {versionId: "v3", version: 3, trainedTo: "2024-06-30"}, newer: {versionId: "v4", version: 4}, fromRun: "run-1"},
      {name: "Reversal 5d", kind: "factor", line: 9, file: "strategy.py", pinned: {versionId: "f2", version: 2}, fromRun: null, retired: true}]});
    for (const word of ["Inputs used", "Momentum LGBM", "v3", "trained to Jun 30, 2024", "Switch to v4", "v2 retired", "strategy.py:6"]) expect(markup).toContain(word);
  });
});

describe("Settings for validation", () => {
  const p = (name: string, extra: object = {}) => ({name, label: name, type: "int", default: 48, file: "strategy.py", line: 6, min: 12, max: 240, ...extra});
  it("a box holding several values is a range: shown as typed, its error under it, the combinations over the rows", () => {
    const markup = html({view: view("perpetual"), parameters: [p("lookback")], params: {lookback: 48}, ranges: {lookback: "24, 48, 300"}, rangeErrors: {lookback: "lookback 300 is outside 12–240"}, combinations: 9, onRange: vi.fn()});
    expect(markup).toContain('value="24, 48, 300"'); expect(markup).toContain("lookback 300 is outside 12–240"); expect(markup).toContain("9 combinations ×");
  });
  it("the walk-forward group is there only while a range is", () => {
    const walk = {pickOn: 6, trade: 2, pickBy: "sharpe" as const};
    const shown = html({view: view("perpetual"), parameters: [p("lookback")], combinations: 3, onRange: vi.fn(), walk, onWalk: vi.fn()});
    expect(shown).toContain("Walk-forward"); expect(shown).toContain("Pick on"); expect(shown).toContain("Then trade"); expect(shown).toContain("Pick by");
    expect(html({view: view("perpetual"), parameters: [p("lookback")], combinations: 0, onRange: vi.fn(), walk, onWalk: vi.fn()})).not.toContain("Pick on");
  });
  it("held_out_needs_start: asked for with no start, the row is marked and says what to do", () => {
    const empty = {...view("perpetual"), effective: {...view("perpetual").effective, heldOutFrom: null}};
    const marked = html({view: empty, heldMissing: true});
    expect(marked).toContain("Set where the held-out part starts"); expect(marked).toContain("data-error");
    expect(html({view: empty})).not.toContain("Set where the held-out part starts");
  });
});
