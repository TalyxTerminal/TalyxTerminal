"""What the allocation settings can offer, read from skfolio itself: the objectives, the risk measures, the covariance estimates, the
distances, and what each objective's class accepts (`get_params()`), plus the checked "Engine parameters" line.

Run in the research environment (skfolio is there); the Host never imports this file.
"""
from __future__ import annotations

import ast
import importlib
import re

# id -> (label, class name in skfolio.optimization, fixed constructor arguments). Stacking and distributionally robust CVaR are left out
# on purpose (use them from code with `q.allocate(settings, estimator=...)`).
OBJECTIVES = {
    "equal_weight": ("Equal weight", "EqualWeighted", {}),
    "inverse_volatility": ("Inverse volatility", "InverseVolatility", {}),
    "min_risk": ("Minimum risk", "MeanRisk", {"objective_function": "MINIMIZE_RISK"}),
    "max_ratio": ("Maximum ratio", "MeanRisk", {"objective_function": "MAXIMIZE_RATIO"}),
    "max_utility": ("Maximum utility", "MeanRisk", {"objective_function": "MAXIMIZE_UTILITY"}),
    "max_return": ("Maximum return", "MeanRisk", {"objective_function": "MAXIMIZE_RETURN"}),
    "risk_parity": ("Risk parity", "RiskBudgeting", {}),
    "risk_budget": ("Risk budget by group", "RiskBudgeting", {}),
    "max_diversification": ("Maximum diversification", "MaximumDiversification", {}),
    "hrp": ("Hierarchical risk parity", "HierarchicalRiskParity", {}),
    "herc": ("Hierarchical equal risk contribution", "HierarchicalEqualRiskContribution", {}),
    "nco": ("Nested clusters", "NestedClustersOptimization", {}),
    "benchmark_tracking": ("Benchmark tracking", "BenchmarkTracker", {}),
    "schur": ("Schur complementary", "SchurComplementary", {}),
}
EXCLUDED = ("StackingOptimization", "DistributionallyRobustCVaR", "Random")
# Objectives that need an expected return.
USES_RETURNS = ("max_ratio", "max_utility", "max_return")
# Objectives whose problem has an expected-return term: skfolio puts `transaction_costs` there and nowhere else, so only these count costs in the objective.
COST_OBJECTIVES = ("max_ratio", "max_utility", "max_return", "max_diversification")
LINEAR_MEASURES = ("cvar", "evar", "cdar", "edar", "mean_absolute_deviation", "worst_realization", "max_drawdown", "average_drawdown",
                   "first_lower_partial_moment", "gini_mean_difference")
COVARIANCES = {
    "empirical": ("Sample", "EmpiricalCovariance", {}),
    "ledoit_wolf": ("Ledoit-Wolf shrinkage", "LedoitWolf", {}),
    "oas": ("Oracle approximating shrinkage", "OAS", {}),
    "shrunk": ("Shrunk", "ShrunkCovariance", {}),
    "ew": ("Exponentially weighted", "EWCovariance", {}),
    "gerber": ("Gerber", "GerberCovariance", {}),
    "denoise": ("Denoised", "DenoiseCovariance", {}),
    "detone": ("Detoned", "DetoneCovariance", {}),
    "graphical_lasso": ("Graphical lasso", "GraphicalLassoCV", {}),
    "geodesic": ("Geodesic shrinkage", "GeodesicShrinkageCovariance", {}),
}
EXPECTED_RETURNS = {"historical": "Historical", "shrunk": "Shrunk", "equilibrium": "Equilibrium"}
DISTANCES = {"pearson": ("Pearson", "PearsonDistance"), "kendall": ("Kendall", "KendallDistance"), "spearman": ("Spearman", "SpearmanDistance"),
             "covariance": ("Covariance", "CovarianceDistance"), "mutual_information": ("Mutual information", "MutualInformation")}
LINKAGES = {"ward": "Ward", "single": "Single", "complete": "Complete", "average": "Average"}


def skfolio_class(name):
    return getattr(importlib.import_module("skfolio.optimization"), name)


def risk_measures():
    """[(id, label)] from skfolio's RiskMeasure enum, the annualized duplicates left out (the pages annualize themselves)."""
    from skfolio import RiskMeasure
    return [(item.name.lower(), item.name.replace("_", " ").capitalize()) for item in RiskMeasure if not item.name.startswith("ANNUALIZED")]


def objective_class(objective):
    return skfolio_class(OBJECTIVES[objective][1])


def params_of(cls):
    return cls().get_params()


def capabilities(objective):
    """What this objective's class accepts, read from `get_params()`: the page shows a row only where it does."""
    cls = objective_class(objective)
    keys = set(params_of(cls))
    return {
        "short": "max_short" in keys,
        "costAware": "transaction_costs" in keys and objective in COST_OBJECTIVES,
        "robustReturns": "mu_uncertainty_set_estimator" in keys,
        "robustCovariance": "covariance_uncertainty_set_estimator" in keys,
        "regularization": "l1_coef" in keys,
        "riskCaps": sorted(key[4:] for key in keys if key.startswith("max_") and key[4:] in {item[0] for item in risk_measures()}),
        "count": "cardinality" in keys,
        "turnover": "max_turnover" in keys,
        "tracking": "max_tracking_error" in keys,
        "returns": objective in USES_RETURNS,
        "measure": "risk_measure" in keys,
        "tree": "hierarchical_clustering_estimator" in keys or objective == "nco",
        "budget": objective == "risk_budget",
    }


def parse_engine_parameters(text, cls, set_by_controls=()):
    """`key=value; key=value` -> {key: value}. Values are Python literals; a key must be one of the class's parameters and not already set by a
    control; a value's type must match the default's (a number for a number). Raises ValueError with the message the page shows."""
    out = {}
    names = set(params_of(cls)) if not isinstance(cls, (set, list, tuple)) else set(cls)
    defaults = params_of(cls) if not isinstance(cls, (set, list, tuple)) else {}
    for part in (text or "").split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"'{part}' is not key=value")
        key, raw = (item.strip() for item in part.split("=", 1))
        if key in set_by_controls:
            raise ValueError(f"{key} is set by a control above")
        if key not in names:
            close = [name for name in names if name.startswith(key[:4]) or key in name or name in key]
            hint = f"; did you mean {sorted(close)[0]}?" if close else ""
            raise ValueError(f"{key} is not a parameter of {getattr(cls, '__name__', 'this class')}{hint}")
        try:
            value = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            raise ValueError(f"{key}: {raw} is not a number, true/false, text or list") from None
        default = defaults.get(key)
        if isinstance(default, bool) and not isinstance(value, bool) or isinstance(default, (int, float)) and not isinstance(default, bool) \
                and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise ValueError(f"{key} needs {'true or false' if isinstance(default, bool) else 'a number'}")
        out[key] = value
    return out


def parse_linear(text, assets, groups):
    """Free linear constraints, one a line: `SPY + QQQ <= 0.4`, `Bonds >= 0.2`. Names are assets or groups. Returns skfolio's strings (one a
    line, comments with # allowed) after checking every line; raises ValueError("Line 2: TLT is not in the pool")."""
    names = set(assets) | set(groups)
    lines = []
    for number, line in enumerate((text or "").splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if not re.search(r"(<=|>=|==|=)", line):
            raise ValueError(f"Line {number}: needs <=, >= or =")
        left = re.split(r"(<=|>=|==|=)", line)
        for word in re.findall(r"[A-Za-z_][A-Za-z0-9_.\-^=]*", left[0] + " " + left[2]):
            if word not in names:
                raise ValueError(f"Line {number}: {word} is not in the pool")
        try:
            float(left[2].strip()) if not re.search(r"[A-Za-z]", left[2]) else None
        except ValueError:
            raise ValueError(f"Line {number}: the right side is not a number") from None
        lines.append(line.replace("==", "=").replace("=", "==") if left[1] in ("=", "==") else line)
    return lines
