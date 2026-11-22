/* What a setting is called on screen: the label the code declares for a parameter, the form's label for a run or data setting, each with its unit
   as the draft writes it ("Taker fee, %"). A variable name (stop_atr, costs.feePct) is never shown. */
import { t } from "../i18n";
import type { Parameter } from "../objects";

/** A parameter's label as the code declares it; a short trailing "(bars)" is its unit ("Lookback (bars)" → Lookback, bars). */
export function paramLabel(item: Pick<Parameter, "name" | "label">): {label: string; unit?: string} {
  const found = /^(.*\S)\s*\(([^()=;,]{1,10})\)$/.exec(item.label ?? "");
  return found ? {label: found[1], unit: found[2]} : {label: item.label || words(item.name)};
}

const NAMES: Record<string, string> = {
  capital: t("Capital"), currency: t("Currency"), reportCurrency: t("Report currency"), maxPosition: t("Max position, × equity"), fill: t("Fill at"), accounts: t("Capital by venue"),
  "benchmark.kind": t("Benchmark"), "benchmark.symbol": t("Benchmark symbol"), heldOutFrom: t("Held out from"), limitThroughTicks: t("Limit orders, ticks through"),
  "exchange.leverage": t("Leverage, ×"), "exchange.maxLeverage": t("Leverage cap, ×"), "exchange.marginModel": t("Margin"), "exchange.liquidation": t("Liquidate below maintenance"),
  "exchange.maintenancePct": t("Maintenance, %"), "exchange.stopTrigger": t("Stop trigger"), "exchange.positionMode": t("Position mode"),
  "costs.feePct": t("Taker fee, %"), "costs.makerPct": t("Maker fee, %"), "costs.feeSource": t("Fees"), "costs.commissionBps": t("Commission, bps"), "costs.halfSpreadBps": t("Half spread, bps"),
  "costs.impact": t("Impact, σ√q"), "costs.volumeCapPct": t("Volume cap, % ADV"), "costs.funding": t("Funding"), "costs.borrowPct": t("Borrow, % a year"),
  shorting: t("Shorting"), halted: t("Halted names"), "riskFree.basis": t("Risk-free"), "riskFree.pct": t("Risk-free rate, %"),
  "data.since": t("From"), "data.until": "To", "data.timeframe": t("Timeframe"), "data.fillBars": t("Fill bars"), "data.heldOutFrom": t("Held out from"), code: t("Code"),
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
    return `${label} ${typeof value === "boolean" ? (value ? t("on") : t("off")) : String(value)}`;
  }).join(", ");
}
