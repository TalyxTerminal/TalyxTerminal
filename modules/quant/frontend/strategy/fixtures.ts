/* Hand-written pieces of a run for the page's tests: the shapes the backend gives, filled in only as far as a test reads them. */
import type { Check, RunSettings, RunSettingsView, StrategyRun, StrategyWork } from "./api";

export const SETTINGS: RunSettings = {
  capital: 10_000, currency: "USDT", maxPosition: 1, exchange: {leverage: 1, maxLeverage: 100, marginModel: "leveraged", liquidation: true, maintenancePct: 0.5, stopTrigger: "last", positionMode: "oneway"},
  limitThroughTicks: 0, costs: {feePct: 0.05, makerPct: 0.02, feeSource: "from Market (okx)", halfSpreadBps: 1, impact: 0.3, volumeCapPct: null, funding: true}, fill: "nextOpen", shorting: "allowed",
  benchmark: {kind: "hold"}, riskFree: {basis: "zero"}, heldOutFrom: null,
};
export const view = (kind: "perpetual" | "stock" | "spot", more: Partial<RunSettings> = {}): RunSettingsView => ({
  objectId: "o1", kind, effective: {...SETTINGS, ...more}, defaults: SETTINGS, stored: {},
  applicable: {exchange: kind === "perpetual" ? ["leverage", "marginModel", "liquidation", "maintenancePct", "stopTrigger", "positionMode"] : kind === "stock" ? ["margin"] : [], margin: kind === "stock" ? "Reg T, 2× overnight" : null,
    benchmark: kind === "stock" ? ["symbol", "equal", "none"] : ["symbol", "hold", "none"], shorting: kind === "stock"},
});
export const STATS = {totalReturn: 0.1452, cagr: 0.07, maxDrawdown: -0.1929, sharpe: 0.42, overBenchmark: -1.29, costsAYear: 0.1007, trades: 231, sortino: 0.8, volatility: 0.2, calmar: 0.36, winRate: 0.34, profitFactor: 1.08};
export const CHECKS: Check[] = [
  {id: "heldout", name: "Held-out Sharpe", value: "not run", need: "≥ 50% of in-sample", state: "notrun", title: "Sharpe on the held-out end over the Sharpe before it", go: "heldout"},  // i18n-ignore
  {id: "shuffle", name: "Shuffled trade order", value: "1.4×", need: "≤ 2× actual", state: "pass", title: "95th-percentile drawdown of the trades in random order, over the actual drawdown", go: "equity"},  // i18n-ignore
  {id: "top", name: "Best five", value: "62%", need: "≤ 50% of net", state: "fail", title: "the five best trades' (or names') share of the net profit", go: "trades"},  // i18n-ignore
];
export const work = (more: Partial<StrategyWork> = {}): StrategyWork => ({
  kind: "single", currency: "USDT", capital: 10_000, annualization: 365, riskFree: {basis: "zero", label: "zero"}, benchmarkLabel: "BTC/USDT:USDT hold", stats: STATS,  // i18n-ignore
  span: [1_672_531_200_000, 1_735_689_600_000], tradesTotal: 231, logTotal: 12, checks: CHECKS, instrument: null, instrumentCount: 0,
  costs: {fees: 2012, spread: 402, impact: 37, funding: 0, borrow: 0, total: 2451, gross: 3904, net: 1452, incomplete: []}, notes: [], ...more,
});
export const run = (more: Partial<StrategyRun> = {}): StrategyRun => ({
  id: "run-1", objectId: "o1", number: 11, kind: "backtest", state: "completed", createdAt: 1_735_689_600_000, endedAt: 1_735_689_630_000, seconds: 30, settings: {data: {}, run: SETTINGS, params: {}},
  changes: [], inputs: [], stats: STATS, progress: null, failure: null, starred: false, note: "", of: null, heldOut: false, codeDigest: "abc", dataTo: "2024-12-31", notes: [], tags: [], message: null, ...more,
});
