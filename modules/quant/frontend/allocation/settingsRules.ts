import type { Parameter } from "../objects";
import type { Capability } from "./api";

/** What each objective's class must accept for a row to show (read from skfolio's parameters by the backend, see native/allocation_capabilities.json). */
const NEEDS: Record<string, (caps: Capability, values: Record<string, any>) => boolean> = {
  long_cap: c => c.short, short_cap: c => c.short, net: c => c.short, cardinality: c => c.count, threshold: c => c.count, max_turnover: c => c.turnover, max_tracking: c => c.tracking,
  risk_cap: c => c.riskCaps.length > 0, risk_cap_measure: c => c.riskCaps.length > 0, cost_aware: c => c.costAware, robust_returns: c => c.robustReturns,
  robust_covariance: (c, v) => c.robustCovariance && v.risk_measure === "variance", l1: c => c.regularization, l2: c => c.regularization, risk_measure: c => c.measure,
  distance: c => c.tree, linkage: (c, v) => c.tree && ["hrp", "herc", "schur"].includes(v.objective), expected_returns: c => c.returns, budget: c => c.budget,
};
/** A declared setting shows when its own `show_if` holds and the chosen objective's class accepts it. */
export const visible = (item: Parameter, values: Record<string, any>, caps: Capability | undefined): boolean => {
  if (item.showIf && !Object.entries(item.showIf).every(([key, want]) => values[key] === want)) return false;
  const rule = NEEDS[item.name];
  return !(rule && caps && !rule(caps, values));
};
