/* What a setting is called on screen: the label the code declares for a parameter, the form's label for a run or data setting, each with its unit
   as the draft writes it ("Taker fee, %"). A variable name (stop_atr, costs.feePct) is never shown. */
import type { Parameter } from "../objects";

/** A parameter's label as the code declares it; a short trailing "(bars)" is its unit ("Lookback (bars)" → Lookback, bars). */
export function paramLabel(item: Pick<Parameter, "name" | "label">): {label: string; unit?: string} {
  const found = /^(.*\S)\s*\(([^()=;,]{1,10})\)$/.exec(item.label ?? "");
  return found ? {label: found[1], unit: found[2]} : {label: item.label || words(item.name)};
}

const NAMES: Record<string, string> = {
  capital: "Capital", currency: "Currency", reportCurrency: "Report currency", maxPosition: "Max position, × equity", fill: "Fill at", accounts: "Capital by venue",
  "benchmark.kind": "Benchmark", "benchmark.symbol": "Benchmark symbol", heldOutFrom: "Held out from", limitThroughTicks: "Limit orders, ticks through",
  "exchange.leverage": "Leverage, ×", "exchange.maxLeverage": "Leverage cap, ×", "exchange.marginModel": "Margin", "exchange.liquidation": "Liquidate below maintenance",
  "exchange.maintenancePct": "Maintenance, %", "exchange.stopTrigger": "Stop trigger", "exchange.positionMode": "Position mode",
  "costs.feePct": "Taker fee, %", "costs.makerPct": "Maker fee, %", "costs.feeSource": "Fees", "costs.commissionBps": "Commission, bps", "costs.halfSpreadBps": "Half spread, bps",
  "costs.impact": "Impact, σ√q", "costs.volumeCapPct": "Volume cap, % ADV", "costs.funding": "Funding", "costs.borrowPct": "Borrow, % a year",
  shorting: "Shorting", halted: "Halted names", "riskFree.basis": "Risk-free", "riskFree.pct": "Risk-free rate, %",
  "data.since": "From", "data.until": "To", "data.timeframe": "Timeframe", "data.fillBars": "Fill bars", "data.heldOutFrom": "Held out from", code: "Code",
};

/** Words for a name nobody labelled yet: "volumeCapPct" → "Volume cap", "stop_atr" → "Stop atr" (still not the variable). */
function words(name: string): string {
  const last = name.split(".").pop() ?? name;
  const text = last.replace(/(Pct|Bps)$/, "").replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/_/g, " ").trim().toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** A setting's on-screen name: `param.x` or a declared parameter's name reads its label; run and data keys read the form's label. */
export function settingName(key: string, parameters: readonly Pick<Parameter, "name" | "label">[] = []): string {
  const bare = key.replace(/^(param|run)\./, "");
  const declared = parameters.find(item => item.name === bare);
  if (declared) { const {label, unit} = paramLabel(declared); return unit ? `${label}, ${unit}` : label; }
  return NAMES[bare] ?? words(bare);
}

/** The head line's few parameters as the draft writes them: "lookback 48, stop 2, trailing stop 3". */
export function paramsLine(params: Record<string, unknown>, parameters: readonly Pick<Parameter, "name" | "label">[] = [], count = 3): string {
  return Object.entries(params).slice(0, count).map(([name, value]) => {
    const declared = parameters.find(item => item.name === name);
    const label = (declared ? paramLabel(declared).label : words(name)).toLowerCase();
    return `${label} ${typeof value === "boolean" ? (value ? "on" : "off") : String(value)}`;
  }).join(", ");
}
