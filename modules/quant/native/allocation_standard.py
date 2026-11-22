"""TALYX's standard allocation: the pool, the objective, how risk is estimated, the limits, when to rebalance and what trading costs, then
the walk-forward run. Each setting is declared once; the page shows them in these groups and an allocation only keeps their values. An
allocation in the standard flow always runs TALYX's current copy of this file. "Write as code" copies it into the allocation's project.

Blank text means no limit. Percentages are numbers in percent. `q.allocate` fits each rebalance day on the `lookback` days before it and
holds the weights from the next day's close.
"""
import talyx_quant as q

# The choice lists are literals (the page reads them without running this file); test_choices_match_skfolio keeps them equal to skfolio's.
pool = q.table("pool", [], columns=["ticker", "group", "currency", "from", "proxy", "kind", "runId"], label="Assets", group="Asset pool", help="Market instruments with their group")
align = q.choice("align", "common", [("common", "Common days"), ("weekly", "Weekly"), ("monthly", "Monthly")], label="Align", group="Asset pool")
currency = q.param("currency", "USD", label="Currency", group="Asset pool")

objective = q.choice("objective", "min_risk", [("equal_weight", "Equal weight"), ("inverse_volatility", "Inverse volatility"), ("min_risk", "Minimum risk"),
                     ("max_ratio", "Maximum ratio"), ("max_utility", "Maximum utility"), ("max_return", "Maximum return"), ("risk_parity", "Risk parity"), ("risk_budget", "Risk budget by group"),
                     ("max_diversification", "Maximum diversification"), ("hrp", "Hierarchical risk parity"),
                     ("herc", "Hierarchical equal risk contribution"), ("nco", "Nested clusters"), ("benchmark_tracking", "Benchmark tracking"),
                     ("schur", "Schur complementary")], label="Objective", group="Objective")
risk_measure = q.choice("risk_measure", "variance", [("variance", "Variance"), ("semi_variance", "Semi variance"), ("standard_deviation", "Standard deviation"),
                        ("semi_deviation", "Semi deviation"), ("mean_absolute_deviation", "Mean absolute deviation"), ("cvar", "CVaR"), ("evar", "EVaR"),
                        ("worst_realization", "Worst realization"), ("cdar", "CDaR"), ("max_drawdown", "Max drawdown"), ("average_drawdown", "Average drawdown"),
                        ("edar", "EDaR"), ("first_lower_partial_moment", "First lower partial moment"), ("ulcer_index", "Ulcer index"),
                        ("gini_mean_difference", "Gini mean difference")], label="Risk measure", group="Objective")
expected_returns = q.choice("expected_returns", "historical", [("historical", "Historical"), ("shrunk", "Shrunk"), ("equilibrium", "Equilibrium")],
                            label="Expected returns", group="Objective", show_if={"objective": ["max_ratio", "max_utility", "max_return", "min_risk"]})
budget = q.table("budget", [], columns=["group", "share"], label="Risk shares", group="Objective", show_if={"objective": "risk_budget"})
distance = q.choice("distance", "pearson", [("pearson", "Pearson"), ("kendall", "Kendall"), ("spearman", "Spearman"), ("covariance", "Covariance"),
                    ("mutual_information", "Mutual information")], label="Distance", group="Objective")
linkage = q.choice("linkage", "ward", [("ward", "Ward"), ("single", "Single"), ("complete", "Complete"), ("average", "Average")], label="Linkage", group="Objective")
robust_returns = q.param("robust_returns", False, label="Robust returns", group="Objective", show_if={"objective": ["max_ratio", "max_utility", "max_return"]})
min_return = q.param("min_return", "", label="Return at least", group="Objective", show_if={"objective": "min_risk"},
                     help="A year, in percent: the least risk that still expects this return; empty for none")
robust_covariance = q.param("robust_covariance", False, label="Robust covariance", group="Objective")
confidence = q.param("confidence", 95.0, min=50.0, max=99.9, label="Confidence", group="Objective")
l1 = q.param("l1", 0.0, min=0.0, label="L1", group="Objective")
l2 = q.param("l2", 0.0, min=0.0, label="L2", group="Objective")
objective_parameters = q.param("objective_parameters", "", label="Objective options", group="Objective")

covariance = q.choice("covariance", "empirical", [("empirical", "Sample"), ("ledoit_wolf", "Ledoit-Wolf shrinkage"), ("oas", "Oracle approximating shrinkage"),
                      ("shrunk", "Shrunk"), ("ew", "Exponentially weighted"), ("gerber", "Gerber"), ("denoise", "Denoised"), ("detone", "Detoned"),
                      ("graphical_lasso", "Graphical lasso"), ("geodesic", "Geodesic shrinkage")], label="Covariance", group="Risk estimate")
lookback = q.param("lookback", 252, min=20, max=2520, label="Lookback", group="Risk estimate", help="Trading days before each rebalance")
covariance_parameters = q.param("covariance_parameters", "", label="Estimator options", group="Risk estimate")

long_cap = q.param("long_cap", 100.0, min=0.0, label="Long", group="Limits")
short_cap = q.param("short_cap", 0.0, min=0.0, label="Short", group="Limits")
net = q.param("net", 100.0, label="Net", group="Limits")
max_weight = q.param("max_weight", "", label="Each asset, at most", group="Limits")
min_weight = q.param("min_weight", "", label="Each asset, at least", group="Limits")
group_limits = q.table("group_limits", [], columns=["group", "max", "min", "count"], label="By group", group="Limits")
cardinality = q.param("cardinality", "", label="At most", group="Limits")
threshold = q.param("threshold", "", label="If held, at least", group="Limits")
max_turnover = q.param("max_turnover", "", label="Turnover cap", group="Limits")
max_tracking = q.param("max_tracking", "", label="Tracking error", group="Limits")
risk_cap_measure = q.choice("risk_cap_measure", "cvar", [("variance", "Variance"), ("standard_deviation", "Standard deviation"), ("cvar", "CVaR"), ("cdar", "CDaR"),
                            ("max_drawdown", "Max drawdown"), ("semi_deviation", "Semi deviation")], label="Risk cap measure", group="Limits")
risk_cap = q.param("risk_cap", "", label="Risk cap", group="Limits")
linear = q.param("linear", "", label="Linear", group="Limits", help="One a line, such as SPY + QQQ <= 0.4")
cost_aware = q.param("cost_aware", True, label="Cost-aware", group="Limits")

cost_multiple = q.param("cost_multiple", 1.0, min=0.0, label="Costs", group="Costs by group", help="Scales every cost; sweep it to see what doubled costs leave")
capital = q.param("capital", 1000000.0, min=0.0, label="Capital", group="Run settings", help="What the trades list of a rebalance is sized on; the backtest itself works in weights")
risk_free = q.choice("risk_free", "tbill", [("tbill", "13-week T-bill"), ("fixed", "Fixed"), ("zero", "Zero")], label="Risk-free", group="Run settings",
                     help="What the Sharpe and a maximum ratio are over: the 13-week US T-bill (^IRX) of each day, a fixed rate a year, or zero. A pool of A shares starts at a fixed 4%, of crypto alone at zero")
risk_free_pct = q.param("risk_free_pct", 4.0, min=-5.0, max=50.0, label="Rate", group="Run settings", show_if={"risk_free": "fixed"}, help="Percent a year")
benchmark = q.param("benchmark", "SPY 60%, IEF 40%", label="Benchmark", group="Run settings",
                    help="What benchmark tracking and the tracking error follow: Market symbols with their weights, such as QQQ or SPY 60%, IEF 40%; they need not be in the pool")
frequency = q.choice("frequency", "monthly", [("weekly", "Weekly"), ("monthly", "Monthly"), ("quarterly", "Quarterly")], label="Every", group="Rebalance")
costs = q.table("costs", [{"group": "Stocks", "bps": 5.0}, {"group": "Funds", "bps": 5.0}, {"group": "Bonds", "bps": 5.0}, {"group": "Crypto", "bps": 10.0},
                         {"group": "Commodities", "bps": 10.0}, {"group": "Other", "bps": 10.0}], columns=["group", "bps"],
                label="Costs by group", group="Costs by group")
shocks = q.table("shocks", [{"target": "Stocks", "pct": -20.0}, {"target": "Bonds", "pct": -10.0}, {"target": "BTC", "pct": -50.0}], columns=["target", "pct"],
                 label="Shocks", group="Scenarios", help="An asset or group falls by this many percent; the others follow by covariance. A target the pool does not have is left out")

settings = {"pool": pool, "align": align, "currency": currency, "objective": objective, "risk_measure": risk_measure, "expected_returns": expected_returns,
            "budget": budget, "distance": distance, "linkage": linkage, "robust_returns": robust_returns, "robust_covariance": robust_covariance,
            "confidence": confidence, "l1": l1, "l2": l2, "objective_parameters": objective_parameters, "covariance": covariance, "lookback": lookback,
            "covariance_parameters": covariance_parameters, "long_cap": long_cap, "short_cap": short_cap, "net": net, "max_weight": max_weight,
            "min_weight": min_weight, "group_limits": group_limits, "cardinality": cardinality, "threshold": threshold, "max_turnover": max_turnover,
            "max_tracking": max_tracking, "risk_cap_measure": risk_cap_measure, "risk_cap": risk_cap, "linear": linear, "cost_aware": cost_aware,
            "frequency": frequency, "costs": costs, "cost_multiple": cost_multiple, "capital": capital, "risk_free": risk_free, "risk_free_pct": risk_free_pct,
            "benchmark": benchmark, "shocks": shocks}
result = q.allocate(settings)
