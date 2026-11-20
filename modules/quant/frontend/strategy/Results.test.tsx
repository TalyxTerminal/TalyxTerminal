import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { AccountSection, CostsSection, dataToText, CrisisSection, Fig, LongShort, OrdersSection, Periods, ResultsView, Rolling, SECTION_IDS, StyleSection, SettingsSection, Summary } from "./Results";
import { run } from "./fixtures";

vi.mock("../chart/Plot", async importOriginal => {
  const original = await importOriginal<Record<string, unknown>>();
  return {...original, Plot: (props: {figure: {layout?: {meta?: {key?: string}}}}) => <i data-plot={props.figure.layout?.meta?.key} />};
});
const html = (node: ReturnType<typeof createElement>) => renderToStaticMarkup(node);
const drawn = (key: string) => ({data: [], layout: {meta: {key}}}) as never;
const api = {orders: vi.fn()} as never;
const toc = SECTION_IDS.map(id => ({id, title: id[0].toUpperCase() + id.slice(1)}));

describe("Results is one page that scrolls", () => {
  it("toc_jumps_and_highlights: every section is on the page under its own anchor, the contents lists them and marks the first", () => {
    const view = {runId: "r", sections: {}, missing: {}, figuresFailed: {}, toc};
    const markup = html(createElement(ResultsView, {api, run: run(), view, section: undefined, onSection: vi.fn(), onStar: vi.fn(), onOrder: vi.fn(), onTrade: vi.fn(), onUse: vi.fn()}));
    for (const item of toc) expect(markup).toContain(`id="qx-sec-${item.id}"`);
    expect(markup).toContain('aria-current="true"');
    expect(markup.match(/aria-current="true"/g)?.length).toBe(1);
    expect(markup.indexOf("qx-sec-summary")).toBeLessThan(markup.indexOf("qx-sec-settings"));
    expect(markup).not.toContain('id="qx-sec-plots"');  // the code drew nothing: no section and no entry in the contents
  });
  it("no_publish_button: a strategy has no Publish; a run can be kept", () => {
    const view = {runId: "r", sections: {}, missing: {}, figuresFailed: {}, toc};
    const markup = html(createElement(ResultsView, {api, run: run({starred: true}), view, section: undefined, onSection: vi.fn(), onStar: vi.fn(), onOrder: vi.fn(), onTrade: vi.fn(), onUse: vi.fn()}));
    expect(markup).not.toContain("Publish");
    expect(markup).toContain("Kept");
    expect(html(createElement(ResultsView, {api, run: run(), view, section: undefined, onSection: vi.fn(), onStar: vi.fn(), onOrder: vi.fn(), onTrade: vi.fn(), onUse: vi.fn()}))).toContain("Keep");
  });
  it("a section the backend could not make says why in its place", () => {
    const view = {runId: "r", sections: {}, missing: {style: "Fewer than 20 days with both this run and the benchmark."}, figuresFailed: {}, toc: [{id: "style", title: "Style"}]};
    expect(html(createElement(ResultsView, {api, run: run(), view, section: undefined, onSection: vi.fn(), onStar: vi.fn(), onOrder: vi.fn(), onTrade: vi.fn(), onUse: vi.fn()}))).toContain("Fewer than 20 days");
    expect(html(createElement(Fig, {fig: {key: "strategy.rollingBeta", unavailable: "No benchmark"}, label: "Rolling beta"}))).toContain("could not be drawn: No benchmark");
    expect(html(createElement(Fig, {fig: undefined, label: "x"}))).toContain("was not built");
  });
});

describe("Results' sections", () => {
  it("the summary's numbers are in its table once: the run, the benchmark and every compared run, and alpha as a percent a year", () => {
    const rows = [["Total return", .145, 4.6], ["CAGR", .07, 1.36], ["Sharpe", .42, 1.2], ["Sortino", .88, 3.4], ["Calmar", .36, 5.2], ["Probabilistic Sharpe", .73, .99], ["Max drawdown", -.19, -.26], ["Volatility", .21, .48]]
      .map(([name, value, benchmark]) => ({name, run: value, benchmark}));
    const data = {annualization: 365, benchmarkLabel: "BTC hold", tiles: {totalReturn: .145, cagr: .07, sharpe: .42, maxDrawdown: -.19}, returnStatistics: rows, againstBenchmark: {alpha: -0.0936, beta: 0.09, correlation: .2, trackingError: .46, informationRatio: -1.7}, riskFree: {label: "zero"}, engine: [{group: "Engine's own returns (252 days a year, every calendar day, no risk-free rate)", name: "Average Win (Return)", value: .0139}, {group: "Engine's own returns (252 days a year, every calendar day, no risk-free rate)", name: "Sharpe Ratio (252 days)", value: 9.9}, {group: "Registered (365 days)", name: "Omega Ratio", value: 1.1}]};
    const compared = [{runId: "a", number: 2, label: "#2", stats: {cagr: .05, volatility: .2, sharpe: .3, sortino: .5, maxDrawdown: -.2, winRate: .3, profitFactor: 1.1} as never}, {runId: "b", number: 1, label: "#1", stats: null}];
    const markup = html(createElement(Summary, {data, run: run({number: 3}), compared}));
    for (const word of ["Return", "CAGR", "Sharpe", "Sortino", "Calmar", "Probabilistic Sharpe", "Max drawdown", "Volatility", "Return statistics", "Against BTC buy and hold", "annualized over 365 days", "risk-free zero", "own figures", "every calendar day"]) expect(markup).toContain(word);
    expect(markup).toContain("Alpha, a year"); expect(markup).toContain("−9.4%");
    expect(markup).not.toContain("tx-knv"); expect(markup).not.toContain("Sharpe Ratio (252 days)"); expect(markup).not.toContain("Omega");  // no tiles repeating the table, no second Sharpe
    expect(markup).toContain('<span class="tx-up">+14.5%');  // a return is in the up or down colour
    expect(markup).toContain("<td>−19.0%</td>");  // a drawdown is not
    expect(markup).toContain("<td>21.0%</td>"); expect(markup).not.toContain("+21.0%");  // volatility is a size: no sign
    expect(markup).toContain(">#3<"); expect(markup).toContain(">#2<"); expect(markup).toContain(">#1<");  // one column for each run drawn beside this one
    expect(markup).not.toContain("Risk-free:");
  });
  it("rolling sections say when the window is longer than the run", () => {
    expect(html(createElement(Rolling, {data: {longerThanRun: true}}))).toContain("Window longer than the run");
    expect(html(createElement(Rolling, {data: {figures: {sharpe: drawn("strategy.rollingSharpe")}}}))).toContain("strategy.rollingSharpe");
    const three = html(createElement(Rolling, {data: {figures: {}}, bench: "BTC/USDT:USDT hold"}));  // the draft's three side by side, each under its own title, in one section (no head of their own: they stay linked)
    expect(three).toContain("qx-grid3"); for (const head of [">Sharpe<", ">Beta to BTC<", ">Volatility<"]) expect(three).toContain(head);
    expect(three).not.toContain("tx-regionhead");
    const periods = html(createElement(Periods, {data: {benchmarkLabel: "BTC/USDT:USDT hold", table: [{year: 2025, months: [0.05, -0.02, null, null, null, null, null, null, null, null, null, null], total: 0.03, benchmark: -0.1}]}}));
    for (const head of [">Annual returns<", ">Monthly returns<", ">Jan<", ">Year<", ">BTC hold<"]) expect(periods).toContain(head);
    expect(periods).toContain("+5.0<"); expect(periods).toContain("−2.0<"); expect(periods).toMatch(/style="background:color-mix\(in srgb, var\(--tx-up\)/);  // a heat table, not a second chart
  });
  it("a month or a year of the monthly table opens its trades on Work", () => {
    const table = [{year: 2025, months: [0.05, -0.02, null, null, null, null, null, null, null, null, null, null], total: 0.03, benchmark: -0.1}];
    const markup = html(createElement(Periods, {data: {benchmarkLabel: "BTC/USDT:USDT hold", table}, onPeriod: vi.fn()}));
    expect(markup).toContain('title="Feb 2025 -2.00%: show the trades"'.replace("-", "−"));
    expect(markup).toContain('title="2025: show the trades"');
    expect(html(createElement(Periods, {data: {benchmarkLabel: "BTC/USDT:USDT hold", table}}))).not.toContain("qx-cellgo");  // nowhere to go, nothing to click
  });
  it("orders: All and only the states the run has, with their counts, and the columns of the draft", () => {
    const markup = html(createElement(OrdersSection, {api, runId: "r", counts: {Filled: 10, Rejected: 2}}));
    for (const word of ["All 12", "Filled 10", "Rejected 2", "Time in force", "Reason", "Linked", "of 12 orders"]) expect(markup).toContain(word);
    for (const word of ["Canceled", "Expired"]) expect(markup).not.toContain(word);
  });
  it("account: the balance chart, and the margin events or that there were none", () => {
    expect(html(createElement(AccountSection, {data: {figures: {balance: drawn("strategy.account")}, marginEvents: []}}))).toContain("No margin events");
    const markup = html(createElement(AccountSection, {data: {figures: {balance: drawn("strategy.account")}, marginEvents: [{time: 1_704_153_600_000, event: "Liquidated", detail: "Long 190 closed at 95.3"}]}}));
    expect(markup).toContain("Liquidated");
    expect(markup).toContain("Margin events");
  });
  it("costs: each part with its share of the gross, funding switched off says off, and the doubled-costs figures", () => {
    const data = {breakdown: [{name: "Fees", amount: -2012.5, shareOfGross: .5155, shareOfCapital: .2}, {name: "Funding", amount: 0, shareOfGross: 0, shareOfCapital: 0, off: true}],
      figures: {waterfall: drawn("strategy.costs")}, doubled: {cagrNow: .07, cagr: .02, sharpeNow: .42, sharpe: .1}};
    const markup = html(createElement(CostsSection, {data}));
    for (const word of ["52%", ">off<", "Costs doubled", "From gross to net"]) expect(markup).toContain(word);
    expect(markup).not.toContain("0.00");
  });
  it("crisis periods list the ones the run covers, as the draft; style has a Beta row and Specific; the code's own series and numbers are listed", () => {
    expect(html(createElement(CrisisSection, {data: {periods: [{name: "COVID crash", from: "2020-02-19", to: "2020-03-23", covered: false}]}}))).toContain("None inside this run&#x27;s period");
    const crisis = html(createElement(CrisisSection, {run: run({number: 11}), bench: "BTC/USDT:USDT hold", data: {periods: [{name: "COVID crash", from: "2020-02-19", to: "2020-03-23", covered: false},
      {name: "Jan 2025 pullback", from: "2024-12-17", to: "2025-01-13", covered: true, return: .254, benchmark: -.203, maxDrawdown: -.044}]}}));
    expect(crisis).not.toContain("COVID crash"); for (const word of [">#11<", ">BTC hold<", ">Max drawdown inside<", "Dec 17, 2024", "+25.4%", "−20.3%", "−4.4%"]) expect(crisis).toContain(word);
    const style = html(createElement(StyleSection, {data: {version: "Styles v1", exposures: [{style: "Beta to BTC hold", exposure: .09, contribution: .01}], specific: .02}}));
    for (const word of ["Styles v1", "Beta to BTC hold", "Specific"]) expect(style).toContain(word);
    expect(html(createElement(Summary, {data: {userStats: {"bars seen": 960}}}))).toContain("bars seen");
  });
  it("long and short are shown side by side, and settings offer the run's own settings when they differ", () => {
    const side = {trades: 3, pnl: 10, winRate: .5, profitFactor: 1.2, averageReturn: .01, averageDays: 2};
    expect(html(createElement(LongShort, {data: {long: side, short: side, all: side, figures: {exposure: drawn("strategy.exposure")}}}))).toContain("strategy.exposure");
    const settings = html(createElement(SettingsSection, {data: {layers: [{layer: "Run", setting: "capital", then: 50000, now: 90000}], differs: {"run.capital": {}}}, run: run({number: 4}), onUse: vi.fn()}));
    expect(settings).toContain("Use #4&#x27;s settings");
    const same = html(createElement(SettingsSection, {data: {layers: [], differs: {}}, run: run(), onUse: vi.fn()}));
    expect(same).toContain("Same as the form now"); expect(same).toMatch(/<button[^>]*disabled[^>]*>Use #/);
    expect(settings).toContain("1 differ from the form now");
    const values = html(createElement(SettingsSection, {data: {layers: [{layer: "Code", setting: "shorts", then: true, now: false}, {layer: "Run", setting: "capital", then: 100000, now: 100000}], differs: {}}, run: run(), onUse: vi.fn()}));
    for (const word of [">on<", ">off<", ">100,000<"]) expect(values).toContain(word);  // written as the draft, not true or 100000
  });
});

describe("the data-to day in the header", () => {
  it("reads a day or milliseconds, and says nothing for anything else", () => {
    expect(dataToText("2025-10-31")).toBe("Oct 31, 2025");
    expect(dataToText(Date.UTC(2025, 9, 31))).toBe("Oct 31, 2025");
    expect(dataToText(undefined)).toBe("");
    expect(dataToText("2026-11-07T12:29:00+00:00")).toBe("Nov 7, 2026");  // a run just started holds a time, not "undefined NaN, NaN"
    expect(dataToText({})).toBe("");
  });
});

describe("setting names", () => {
  it("no_variable_names_on_screen: every place that names a setting shows the declared label or the form's label with its unit", async () => {
    const {settingName, paramsLine} = await import("./names");
    const parameters = [{name: "stop_atr", label: "Stop (ATR)"}, {name: "trail_atr", label: "Trailing stop (ATR)"}, {name: "risk", label: "Risk per trade (%)"}, {name: "shorts", label: "Trade the short side"}];
    const keys = ["stop_atr", "param.trail_atr", "risk", "shorts", "costs.feePct", "costs.feeSource", "costs.makerPct", "run.costs.halfSpreadBps", "exchange.maintenancePct", "costs.volumeCapPct",
      "data.since", "benchmark.kind", "riskFree.basis", "maxPosition", "limitThroughTicks", "costs.someNewThing", "unlabelled_param"];
    for (const key of keys) {
      const name = settingName(key, parameters);
      expect(name, key).not.toMatch(/[._]|[a-z][A-Z]/);
    }
    expect(settingName("stop_atr", parameters)).toBe("Stop, ATR");
    expect(settingName("costs.feePct")).toBe("Taker fee, %");
    expect(paramsLine({stop_atr: 2, risk: 1, shorts: true}, parameters)).toBe("stop 2, risk per trade 1, trade the short side on");
    const values = html(createElement(SettingsSection, {data: {layers: [{layer: "Code", setting: "stop_atr", then: 2, now: 2}, {layer: "Run", setting: "costs.feePct", then: 0.05, now: 0.05}], differs: {}}, run: run(), parameters: parameters as never, onUse: vi.fn()}));
    expect(values).toContain("Stop, ATR");
    expect(values).toContain("Taker fee, %");
    expect(values).not.toMatch(/stop_atr|costs\.feePct/);
  });
});
