"""The allocation engine in the research process: the skfolio estimator a page's settings describe, one period's fit (the trial and
every period of the backtest are this same function), the walk-forward loop with drift, costs and fallbacks, and the numbers around it.

Every Portfolio built here is compounded and weight-drifting. A period never sees returns from its own rebalance day on: the fit uses the
`lookback` days before the rebalance close, the weights are held from the next day's close.

Settings that cannot work (a tracking limit without a benchmark, a line naming what the pool does not have, an option the estimate does not take) raise
`SettingsError` naming the fields, before any period is fitted; only a solver that fails on a period's data takes the fallback chain. `settings["annualization"]`
(observations a year, from Market's calendar) is set by the run: the limits written a year are turned into the period's units with it.
"""
from __future__ import annotations

import math
import time

import numpy as np
import pandas as pd
from native import allocation_options as O
from native import metrics as M
from native.allocation_holdings import book_period, cost_bps
from native.allocation_holdings import groups_of as groups_of

NODE_LIMIT = 250
EPS = 1e-6
FALLBACK_CHAIN = ("relaxed", "equal")
PER_YEAR = {"weekly": 52, "monthly": 12, "quarterly": 4}
VOLATILITY_CAPS = ("standard_deviation", "semi_deviation")
VARIANCE_CAPS = ("variance", "semi_variance")


class SettingsError(ValueError):
    """A setting that cannot work; `fields` are the settings to mark."""

    def __init__(self, message, fields):
        super().__init__(message)
        self.fields = list(fields)


def _enum(name, value):
    from skfolio import RiskMeasure
    from skfolio.optimization import ObjectiveFunction
    return {"ObjectiveFunction": ObjectiveFunction, "RiskMeasure": RiskMeasure}[name][value]


def needs_benchmark(settings):
    return settings.get("objective") == "benchmark_tracking" or settings.get("max_tracking") not in (None, "")


def prior(settings):
    import skfolio.moments as moments
    from skfolio.moments import EmpiricalMu, EquilibriumMu, ShrunkMu
    from skfolio.prior import EmpiricalPrior
    label, name, kwargs = O.COVARIANCES[settings.get("covariance", "empirical")]
    extra = settings.get("covariance_parameters") or {}
    try:
        if isinstance(extra, str):
            extra = O.parse_engine_parameters(extra, getattr(moments, name), set(kwargs))
        covariance = getattr(moments, name)(**{**kwargs, **extra})
    except (ValueError, TypeError) as error:
        raise SettingsError(f"Estimator options: {error}", ["covariance_parameters"]) from None
    mu = {"historical": EmpiricalMu, "shrunk": ShrunkMu, "equilibrium": EquilibriumMu}[settings.get("expected_returns", "historical")]()
    return EmpiricalPrior(covariance_estimator=covariance, mu_estimator=mu)


def _group_matrix(settings, assets):
    """skfolio `groups` ({name: [group of each asset]}) and the group names for the By-group limits."""
    groups = groups_of(settings)
    return {asset: [groups.get(asset, "Other")] for asset in assets}


def constraints(settings, assets, previous=None):
    """The constructor arguments the limits give, only those the objective's class takes (checked by the caller). The turnover cap is skfolio's: each asset's
    weight moves at most this much from what is held; the first period buys in from cash and has nothing to move from, so it is not capped. The tracking
    error is written a year and skfolio's is a period's: divided by the root of the observations a year."""
    p = {}
    short = float(settings.get("short_cap", 0)) / 100
    long_cap = float(settings.get("long_cap", 100)) / 100
    net = float(settings.get("net", 100)) / 100
    cap = settings.get("max_weight")
    upper = float(cap) / 100 if cap not in (None, "") else None
    if short > 0:
        p.update(max_long=long_cap, max_short=short, budget=net, min_weights=-short if settings.get("min_weight") in (None, "") else float(settings["min_weight"]) / 100)
    else:
        p["min_weights"] = float(settings["min_weight"]) / 100 if settings.get("min_weight") not in (None, "") else 0.0
        p["budget"] = 1.0
    if upper is not None:
        p["max_weights"] = upper
    if settings.get("cardinality") not in (None, ""):
        p["cardinality"] = int(settings["cardinality"])
    if settings.get("max_turnover") not in (None, "") and previous is not None:
        p["max_turnover"] = float(settings["max_turnover"]) / 100
    if settings.get("max_tracking") not in (None, ""):
        p["max_tracking_error"] = float(settings["max_tracking"]) / 100 / math.sqrt(float(settings["annualization"]))
    if settings.get("threshold") not in (None, ""):
        p["threshold_long"] = float(settings["threshold"]) / 100
    rows = [row for row in settings.get("group_limits", []) if row.get("max") not in (None, "") or row.get("min") not in (None, "") or row.get("count") not in (None, "")]
    lines = []
    for row in rows:
        if row.get("max") not in (None, ""):
            lines.append(f"{row['group']} <= {float(row['max']) / 100}")
        if row.get("min") not in (None, ""):
            lines.append(f"{row['group']} >= {float(row['min']) / 100}")
    try:
        lines += O.parse_linear(settings.get("linear", ""), assets, {row["group"] for row in settings.get("group_limits", [])} | set(groups_of(settings).values()))
    except ValueError as error:
        raise SettingsError(str(error), ["linear"]) from None
    if lines:
        p["groups"] = _group_matrix(settings, assets)
        p["linear_constraints"] = lines
    counts = {row["group"]: int(row["count"]) for row in rows if row.get("count") not in (None, "")}
    if counts:
        p["group_cardinalities"] = counts
    return p


def risk_cap(settings):
    """(skfolio's `max_<measure>` name, the cap in the period's units) or None. A volatility is written a year (variance too, as its volatility); CVaR is a period's
    loss and the drawdown measures are over the lookback, as skfolio has them."""
    cap = settings.get("risk_cap")
    if cap in (None, ""):
        return None
    measure = settings.get("risk_cap_measure", "cvar")
    value = float(cap) / 100
    if measure in VOLATILITY_CAPS:
        value /= math.sqrt(float(settings["annualization"]))
    elif measure in VARIANCE_CAPS:
        value = value ** 2 / float(settings["annualization"])
    return f"max_{measure}", value


def estimator(settings, assets=None, previous=None, benchmark=None):
    """The unfitted skfolio estimator the settings describe; limits the class does not accept are left out (the page does not offer them). A setting that cannot
    work raises SettingsError."""
    objective = settings.get("objective", "min_risk")
    cls = O.objective_class(objective)
    keys = set(O.params_of(cls))
    assets = assets or [row["ticker"] for row in settings.get("pool", [])]
    if benchmark is None and settings.get("objective") == "benchmark_tracking":
        raise SettingsError("Benchmark tracking follows the benchmark; set one", ["benchmark"])
    if benchmark is None and settings.get("max_tracking") not in (None, "") and "max_tracking_error" in keys:
        raise SettingsError("The tracking error is against the benchmark; set one", ["benchmark"])
    params = {}
    if "prior_estimator" in keys:
        params["prior_estimator"] = prior(settings)
    measure = settings.get("risk_measure", "variance")
    if "risk_measure" in keys:
        params["risk_measure"] = _enum("RiskMeasure", measure.upper())
    for key, value in O.OBJECTIVES[objective][2].items():
        params[key] = _enum("ObjectiveFunction", value) if key == "objective_function" else value
    if objective == "min_risk" and settings.get("min_return") not in (None, "") and "min_return" in keys:
        # the least risk that still expects this return: written a year in percent, skfolio's is a period's
        try:
            params["min_return"] = float(settings["min_return"]) / 100 / float(settings["annualization"])
        except ValueError:
            raise SettingsError(f"Return at least: {settings['min_return']} is not a number", ["min_return"]) from None
    limits = constraints(settings, assets, previous)
    for key, value in limits.items():
        if key in keys:
            params[key] = value
    if settings.get("risk_free_rate") and "risk_free_rate" in keys:
        params["risk_free_rate"] = settings["risk_free_rate"]
    if previous is not None and "previous_weights" in keys:
        params["previous_weights"] = np.asarray(previous, float)
    if settings.get("cost_aware", True) and objective in O.COST_OBJECTIVES and "transaction_costs" in keys and previous is not None:
        # skfolio subtracts the costs from the expected return of every observation; a trade is paid once a holding, so it is spread over the observations of one
        hold = float(settings["annualization"]) / PER_YEAR[settings.get("frequency", "monthly")]
        params["transaction_costs"] = np.array([cost_bps(settings, a) / 1e4 / hold for a in assets])
    if settings.get("l1") not in (None, "", 0) and "l1_coef" in keys:
        params["l1_coef"] = float(settings["l1"])
    if settings.get("l2") not in (None, "", 0) and "l2_coef" in keys:
        params["l2_coef"] = float(settings["l2"])
    confidence = float(settings.get("confidence", 95)) / 100
    if settings.get("robust_returns") and "mu_uncertainty_set_estimator" in keys:
        from skfolio.uncertainty_set import EmpiricalMuUncertaintySet
        params["mu_uncertainty_set_estimator"] = EmpiricalMuUncertaintySet(confidence_level=confidence)
    if settings.get("robust_covariance") and "covariance_uncertainty_set_estimator" in keys:
        from skfolio.uncertainty_set import EmpiricalCovarianceUncertaintySet
        params["covariance_uncertainty_set_estimator"] = EmpiricalCovarianceUncertaintySet(confidence_level=confidence)
    capped = risk_cap(settings)
    if capped and capped[0] in keys:
        params[capped[0]] = capped[1]
    if objective == "risk_budget":
        table = {row["group"]: float(row["share"]) for row in settings.get("budget", [])}
        gs = groups_of(settings)
        counts = {g: sum(1 for a in assets if gs.get(a) == g) for g in table}
        params["risk_budget"] = np.array([table.get(gs.get(a), 0) / max(counts.get(gs.get(a), 1), 1) for a in assets])
    if objective in ("hrp", "herc", "schur", "nco"):
        from skfolio import cluster
        from skfolio import distance as D
        params["distance_estimator"] = getattr(D, O.DISTANCES[settings.get("distance", "pearson")][1])()
        if "hierarchical_clustering_estimator" in keys:
            params["hierarchical_clustering_estimator"] = cluster.HierarchicalClustering(linkage_method=getattr(cluster.LinkageMethod, settings.get("linkage", "ward").upper()))
        if objective == "nco":  # the clusters' inner and outer fits estimate risk with the chosen covariance (the class has no prior of its own)
            from skfolio.optimization import MeanRisk
            params["inner_estimator"] = MeanRisk(prior_estimator=prior(settings))
            params["outer_estimator"] = MeanRisk(prior_estimator=prior(settings))
    if "solver" in keys:
        linear = measure in O.LINEAR_MEASURES
        needs_integer = "cardinality" in params or "threshold_long" in params or "group_cardinalities" in params
        if needs_integer and not linear:  # a node limit, not a clock: the same settings give the same answer under any load
            params["solver"] = "SCIP"
            params["solver_params"] = {"scip_params": {"limits/totalnodes": NODE_LIMIT}}
        elif linear and needs_integer:
            params["solver"] = "HIGHS"
            params["solver_params"] = {"mip_max_nodes": NODE_LIMIT}
        if "save_problem" in keys:
            params["save_problem"] = True
    extra = settings.get("engine_parameters") or settings.get("objective_parameters") or {}
    try:
        if isinstance(extra, str):
            extra = O.parse_engine_parameters(extra, cls, set(params))
        params.update(extra)
        return cls(**params)
    except (ValueError, TypeError) as error:
        raise SettingsError(f"Objective options: {error}", ["objective_parameters"]) from None


def check_settings(settings, assets, benchmark=None):
    """The settings checked before any period is fitted: the estimator is built as a period with a holding would build it. Raises SettingsError."""
    estimator(settings, list(assets), previous=np.full(len(assets), 1.0 / max(len(assets), 1)), benchmark=benchmark)


def pool_equal(assets):
    return pd.Series(1.0 / len(assets), index=assets)


def bound_flags(weights, settings):
    """The limit each asset sits at or breaks: `over cap`, `over group`, `under group` (a fallback that could not keep the limit), `cap`, `group` (at the edge,
    within 1e-6), `zero` (holds nothing: the floor of a long-only book, not a limit the user set)."""
    cap = settings.get("max_weight")
    upper = float(cap) / 100 if cap not in (None, "") else None
    gs = groups_of(settings)
    out = {}
    group_max = {row["group"]: float(row["max"]) / 100 for row in settings.get("group_limits", []) if row.get("max") not in (None, "")}
    group_min = {row["group"]: float(row["min"]) / 100 for row in settings.get("group_limits", []) if row.get("min") not in (None, "")}
    totals = weights.groupby(lambda a: gs.get(a, "Other")).sum()
    for asset, value in weights.items():
        group = gs.get(asset)
        total = totals.get(group, 0)
        if upper is not None and value > upper + EPS:
            out[asset] = "over cap"
        elif group in group_max and total > group_max[group] + EPS:
            out[asset] = "over group"
        elif group in group_min and total < group_min[group] - EPS:
            out[asset] = "under group"
        elif upper is not None and abs(value - upper) <= EPS:
            out[asset] = "cap"
        elif abs(value) <= EPS:
            out[asset] = "zero"
        elif group in group_max and abs(total - group_max[group]) <= EPS:
            out[asset] = "group"
    return out


def fitted_covariance(window, settings, model=None):
    """The covariance the settings estimate on `window`: the fitted model's own when it has one, else the settings' estimate fitted here."""
    found = getattr(model, "prior_estimator_", None)
    if found is not None:
        return np.asarray(found.return_distribution_.covariance, float)
    return np.asarray(prior(settings).fit(window).return_distribution_.covariance, float)


def risk_shares(weights, window, settings, covariance=None):
    """Each asset's share of the portfolio's risk (sums to 1), in the settings' own terms: the chosen risk measure (skfolio's `Portfolio.contribution`), or for
    the variance and the objectives without a measure, the chosen covariance estimate. The run, the trial, a published period and the review all use this."""
    w = weights.to_numpy(float)
    objective = settings.get("objective", "min_risk")
    measure = settings.get("risk_measure", "variance") if "risk_measure" in O.params_of(O.objective_class(objective)) else "variance"
    if measure in ("variance", "standard_deviation"):
        cov = fitted_covariance(window, settings) if covariance is None else covariance
        part = w * (cov @ w)
    else:
        from skfolio import Portfolio
        part = np.asarray(Portfolio(X=window, weights=w).contribution(measure=_enum("RiskMeasure", measure.upper()), spacing=1e-4), float)  # skfolio's default step is too coarse for a drawdown
    total = float(part.sum())
    if not total > 0:
        return pd.Series(np.zeros(len(w)), index=weights.index)
    return pd.Series(part / total, index=weights.index)


def check_feasible(settings, assets):
    """Limits that cannot add up to 100%: raises SettingsError("Limits can't add up to 100%: ...") naming the fields."""
    cap = settings.get("max_weight")
    short = float(settings.get("short_cap", 0))
    if cap not in (None, "") and short == 0 and float(cap) * len(assets) < 100 - 1e-9:
        raise SettingsError(f"Limits can't add up to 100%: {len(assets)} assets x {float(cap):g}% = {float(cap) * len(assets):g}%", ["max_weight"])
    gs = groups_of(settings)
    maxima = {row["group"]: float(row["max"]) for row in settings.get("group_limits", []) if row.get("max") not in (None, "")}
    if maxima and short == 0:
        members = {g: [a for a in assets if gs.get(a) == g] for g in set(gs.values())}
        each = {g: min(maxima.get(g, 100.0), (float(cap) if cap not in (None, "") else 100.0) * len(items)) for g, items in members.items() if items}
        room = sum(each.values())
        if room < 100 - 1e-9:  # name each group's room, so the one to raise can be found
            parts = ", ".join(f"{g} {value:g}%" for g, value in sorted(each.items(), key=lambda item: -item[1]))
            raise SettingsError(f"Limits can't add up to 100%: the group limits allow {room:g}% ({parts})", ["max_weight", "group_limits"])


def relaxed_settings(settings):
    """The first fallback: the objective and the risk estimate with the plain limits (long, short, net, each asset's bounds); the integer, turnover, tracking,
    group and risk-cap limits are what is let go."""
    keep = ("objective", "risk_measure", "covariance", "covariance_parameters", "expected_returns", "pool", "costs", "cost_multiple", "max_weight", "min_weight",
            "long_cap", "short_cap", "net", "distance", "linkage", "annualization", "frequency")
    return {key: settings[key] for key in keep if key in settings}


LET_GO = (("group_limits", "group limits"), ("cardinality", "at most n"), ("threshold", "if held"), ("max_turnover", "turnover"), ("max_tracking", "tracking"),
          ("risk_cap", "risk cap"), ("linear", "other limits"))


def let_go(settings):
    """The limits the first fallback lets go that this allocation sets, by their names under Limits (the page showed the engine's text, not the setting)."""
    def set_(key):
        value = settings.get(key)
        if key == "group_limits":
            return any(row.get("max") not in (None, "") or row.get("min") not in (None, "") or row.get("count") not in (None, "") for row in value or [])
        return value not in (None, "", [], 0)
    return [label for key, label in LET_GO if set_(key)]


def fallback_note(status, settings, first, flat):
    """A period not solved as set, in words: which setting kept it from solving and where it is changed (`fix`: limits, objective or pool)."""
    if status == "relaxed":
        names = let_go(settings)
        return f"{', '.join(names) if names else 'Limits'} let go on this window", "limits"
    if flat:
        return f"equal weight: {first}", "pool"
    return f"equal weight: the objective could not be fitted on this window ({first})", "objective"


def _solver_failures():
    """What a fit raises when the window's data defeats it (the settings were checked before): the solver failing, an asset that did not move, or skfolio's
    ValueError for a problem with no solution on these returns (every asset below the risk-free rate for a maximum ratio)."""
    import cvxpy
    from skfolio.exceptions import NonPositiveVarianceError, OptimizationError, SolverError
    return OptimizationError, SolverError, NonPositiveVarianceError, cvxpy.error.SolverError, RuntimeError, np.linalg.LinAlgError, ValueError


def pinned(weights, pin):
    asset, value = pin["asset"], float(pin["weight"])
    rest = weights.drop(asset)
    left_over = max(1.0 - value, 0.0)
    weights = (rest / rest.sum() * left_over if rest.sum() > 0 else rest).reindex(weights.index).fillna(0.0)
    weights[asset] = value
    return weights


def fit_period(window, settings, previous=None, benchmark=None, pin=None):
    """One period's weights from the window of returns before the rebalance: {"weights": Series, "status", "note", "seconds"}.

    A setting that cannot work raises SettingsError before anything is fitted. A solver that stops at the node limit keeps its best feasible answer ("search limit");
    one that fails on this window's data follows the fallback chain (the plain limits only, then equal weight) and says which step it came to."""
    everyone = list(window.columns)
    left = [name for name in everyone if window[name].isna().any()]   # an asset with no price at the end of the window has left: it gets 0 and the rest are solved without it
    if left:
        mask = np.array([name not in left for name in everyone])
        if previous is not None and len(previous) == len(everyone):
            previous = np.asarray(previous, float)[mask]
        if pin and pin["asset"] in left:
            pin = None
        window = window[[name for name in everyone if name not in left]]
        if window.shape[1] == 0:
            raise ValueError("No asset in the pool has a price in the lookback window")
    assets = list(window.columns)
    started = time.perf_counter()
    status, note, fix = "optimal", "", None
    y = {"y": benchmark.loc[window.index]} if benchmark is not None and needs_benchmark(settings) else {}
    model = estimator(settings, assets, previous=previous, benchmark=benchmark)
    integer = getattr(model, "solver", None) in ("SCIP", "HIGHS")
    covariance = None
    try:
        model.fit(window, **y)
        weights = pd.Series(np.asarray(model.weights_, float), index=assets)
        problem = getattr(model, "problem_", None)
        text = str(getattr(problem, "status", "") or "")
        if text == "optimal_inaccurate":
            status, note = ("search limit", "best feasible within the node limit") if integer else ("inaccurate", "solved to the solver's reduced accuracy")
        elif text and text not in ("optimal",) and "optimal" not in text:
            raise RuntimeError(text)
        if not np.isfinite(weights).all():
            raise RuntimeError("no finite weights")
        covariance = fitted_covariance(window, settings, model) if getattr(model, "prior_estimator_", None) is not None else None
    except _solver_failures() as error:
        first = why_not_fitted(window, error)
        weights = None
        try:
            model = estimator(relaxed_settings(settings), assets, previous=None, benchmark=benchmark)
            model.fit(window, **({"y": benchmark.loc[window.index]} if settings.get("objective") == "benchmark_tracking" else {}))
            weights = pd.Series(np.asarray(model.weights_, float), index=assets)
            if not np.isfinite(weights).all():
                weights = None
            status = "relaxed"
        except _solver_failures():
            weights = None
        if weights is None:
            weights = pool_equal(assets)
            status = "equal"
        note, fix = fallback_note(status, settings, first, flat_assets(window))
    if pin:
        weights = pinned(weights, pin)
    out = {"weights": weights, "status": status, "note": note, "fix": fix, "seconds": time.perf_counter() - started,
           "bound": bound_flags(weights, settings), "riskShare": risk_shares(weights, window, settings, covariance)}
    if left:
        out["weights"] = weights.reindex(everyone).fillna(0.0)
        out["riskShare"] = out["riskShare"].reindex(everyone).fillna(0.0)
        out["left"] = left
    return out


def with_rate(settings, risk_free, position):
    """The settings a period is fitted with: a maximum ratio is the return above the risk-free rate of the rebalance day (an observation's) over the risk.
    Without a rate (Market has no ^IRX for the day) a maximum ratio cannot be fitted, and the run stops naming the setting."""
    if settings.get("objective") != "max_ratio":
        return settings
    rate = risk_free if risk_free is None or np.isscalar(risk_free) else risk_free.iloc[position]
    if rate is None or not np.isfinite(rate):
        raise SettingsError("Market has no 13-week T-bill (^IRX) for this period, so the ratio over it cannot be fitted; set the risk-free rate to zero", ["risk_free"])
    return {**settings, "risk_free_rate": float(rate)}


def rebalance_days(index, frequency="monthly"):
    """Positions of the last trading day of each month (or week, quarter) in the return index."""
    series = pd.Series(np.arange(len(index)), index=index)
    key = {"monthly": index.to_period("M"), "weekly": index.to_period("W"), "quarterly": index.to_period("Q")}[frequency]
    return series.groupby(key).last().to_numpy()


def backtest(returns, settings, *, benchmark=None, cancel=None, progress=None, on_period=None, fitter=None, risk_free=0.0):
    """The walk-forward run. At each rebalance close the weights are fitted on the `lookback` days up to it and held from the next day's
    close, drifting with returns between rebalances; the change to the drifted holding costs bps by group. Returns {"periods", "daily", "gross", "held"}: `held` is
    what is held through each day (the drifted weights the day's return is earned on). `warmup` (rows) holds the first rebalance back as a longer lookback would, so
    the cells of a sweep over the lookback start on the same day."""
    lookback = int(settings.get("lookback", 252))
    first = max(lookback, int(settings.get("warmup") or 0))
    positions = [p for p in rebalance_days(returns.index, settings.get("frequency", "monthly")) if p + 1 >= first and p + 1 < len(returns)]
    n = len(returns)
    assets = list(returns.columns)
    held = None      # the holding drifted through the day the trade is made (the close after the rebalance day): what the change to the new weights costs
    held_fit = None  # the same holding drifted only through the rebalance day: what the estimator may know (turnover limits, costs in the objective)
    daily = pd.Series(0.0, index=returns.index)
    gross = pd.Series(0.0, index=returns.index)
    holding = np.zeros((len(returns), len(assets)))
    periods = []
    for number, pos in enumerate(positions):
        if cancel and cancel():
            break
        window = returns.iloc[pos + 1 - lookback:pos + 1]
        previous = held_fit
        result = fitter(window, held_fit) if fitter else fit_period(window, with_rate(settings, risk_free, pos), previous=held_fit, benchmark=benchmark)
        weights = result["weights"]
        drifted_now = held
        drifted = held if held is not None else np.zeros(len(assets))
        change = np.abs(weights.to_numpy() - np.asarray(drifted))
        costs = np.array([cost_bps(settings, a) / 1e4 for a in assets])
        turnover = 0.5 * float(change.sum()) if held is not None else 1.0
        end = positions[number + 1] + 1 if number + 1 < len(positions) else n - 1
        start = pos + 1
        # weights are held from the close of day `start`; returns of days start+1..end accrue, drifting daily
        span = slice(start + 1, end + 1)
        opening, earned, w, cost = book_period(daily, returns, start, end, weights, held, costs)
        holding[span] = opening
        gross.iloc[span] = earned
        later = positions[number + 1] if number + 1 < len(positions) else None
        fit_next = opening[later - start] if later is not None else None
        held, held_fit = w, fit_next
        gone = [f"{name} left: no price after {returns[name].last_valid_index():%b} {returns[name].last_valid_index().day}" for name in result.get("left", [])]
        period = {"index": number, "date": returns.index[pos], "heldFrom": returns.index[start], "weights": weights, "turnover": turnover, "cost": cost,
                  "left": gone, "status": result["status"], "note": result["note"], "fix": result.get("fix"), "bound": result["bound"], "riskShare": result["riskShare"],
                  "seconds": result["seconds"], "previous": None if previous is None else pd.Series(previous, index=assets),
                  "drifted": None if drifted_now is None else pd.Series(drifted_now, index=assets)}
        periods.append(period)
        if on_period:
            on_period(period)
        if progress:
            progress(number + 1, len(positions))
    first_day = returns.index[positions[0] + 1] if positions else returns.index[0]
    daily = daily.loc[first_day:]
    return {"periods": periods, "daily": daily, "gross": gross.loc[first_day:], "held": pd.DataFrame(holding, index=returns.index, columns=assets).loc[first_day:]}


def metrics(daily, benchmark_daily=None, equal_daily=None, periods=(), annual=None, risk_free=0.0):
    """Whole-run numbers by Quant's one definition of each (`native/metrics.py`), on the run's daily returns (drifted, after costs) led by the starting capital.
    `annual` is the observations a year of the pool's returns (Market's calendar, or 52 and 12 aligned weekly and monthly). `risk_free` is the rate an
    observation, one for every return (a Series on the same days) or a number; None when it is unavailable, and the Sharpe is then empty, never taken against
    zero. Turnover is each period's one-way trade a year; the period bought from cash (it has no drifted holding before it) is not a trade of the allocation."""
    r = daily.to_numpy(float)
    rate = risk_free.reindex(daily.index).to_numpy(float) if isinstance(risk_free, pd.Series) else risk_free
    equity = np.concatenate([[1.0], np.cumprod(1 + r)])
    years = len(r) / annual if len(r) else None
    grow = M.cagr(r, annual)
    out = {"total": M.finite(equity[-1] - 1) if len(r) else None, "cagr": grow, "vol": M.volatility(r, annual),
           "sharpe": None if rate is None else M.sharpe(r, annual, rate), "maxDrawdown": M.equity_drawdown(equity),
           "turnover": M.finite(sum(p["turnover"] for p in periods if p["drifted"] is not None) / years) if years else None,
           "costDrag": M.finite(sum(p["cost"] for p in periods) / years) if years else None}
    for name, other in (("vs6040", benchmark_daily), ("vsEqualWeight", equal_daily)):
        if other is not None and len(other):
            theirs = M.cagr(other.reindex(daily.index).fillna(0.0).to_numpy(float), annual)
            out[name] = M.finite(grow - theirs) if grow is not None and theirs is not None else None
    return out


def benchmark_daily(bars, weights, days, zones=None):
    """The benchmark's return on each of the pool's `days` but the first (the index of its returns, aligned as they are): each column's last close on or before the
    day, close to close, mixed by `weights` ({column: percent}; the columns need not be in the pool). None without weights. A benchmark with no price on the first
    day raises SettingsError: the tracking would have nothing to follow there."""
    if not weights:
        return None
    ordered = bars[bars["column"].isin(list(weights))].sort_values("label")
    dates = pd.to_datetime(ordered["label"], unit="ms", utc=True)
    session = dates.dt.tz_localize(None).dt.normalize()
    for name, zone in (zones or {}).items():
        selected = ordered["column"] == name
        session.loc[selected] = dates.loc[selected].dt.tz_convert(zone).dt.tz_localize(None).dt.normalize()
    closes = ordered.groupby([session, "column"])["close"].last().unstack("column")
    levels = closes.reindex(closes.index.union(days)).ffill().reindex(days)
    late = [name for name in weights if name not in levels.columns or pd.isna(levels[name].iloc[0])]
    if late:
        raise SettingsError(f"The benchmark has no price of {', '.join(late)} on {days[0]:%b} {days[0].day}, {days[0].year}, where the pool starts", ["benchmark"])
    returns = levels.pct_change(fill_method=None).iloc[1:]
    return sum(returns[name] * (weight / 100) for name, weight in weights.items())


def risk_free_per_bar(spec, index, annual):
    """The risk-free rate an observation on each day of `index` from the run's rate (`{basis: zero}`, `{basis: fixed, pct}` or `{basis: irx, rows: [[ms, percent a
    year]], unavailable}`): the fixed percent, or the newest ^IRX close not later than the day, over `annual`. 0.0 for zero; None when Market has no rate, so what
    uses it is empty."""
    if spec["basis"] == "zero":
        return 0.0
    if spec["basis"] == "fixed":
        return float(spec["pct"]) / 100 / annual
    if spec.get("unavailable") or not spec.get("rows"):
        return None
    days = pd.to_datetime([row[0] for row in spec["rows"]], unit="ms", utc=True).tz_localize(None).normalize()
    pct = pd.Series([float(row[1]) for row in spec["rows"]], index=days)
    pct = pct[~pct.index.duplicated(keep="last")].sort_index()
    return pct.reindex(pct.index.union(index)).ffill().reindex(index) / 100 / annual


def effective_assets(weights):
    w = np.asarray(weights, float)
    return float(1.0 / np.sum(w ** 2)) if np.sum(w ** 2) > 0 else 0.0


def flat_assets(window):
    """The assets whose returns did not move over the window."""
    return [str(name) for name in window.columns if float(np.nanstd(window[name].to_numpy(dtype=float))) == 0.0]


def why_not_fitted(window, error):
    """Why a period could not be fitted, in words: an asset whose returns did not move over the lookback (a strategy run out of the market holds cash,
    so its variance is zero and the optimizer refuses) is named as such; anything else is the optimizer's first line."""
    flat = flat_assets(window)
    if flat:
        return f"{', '.join(flat)} did not move over the lookback"
    text = str(error).strip()
    return text.splitlines()[0][:120] if text else type(error).__name__


def trial(returns, settings, previous=None, benchmark=None, pin=None, published=None, fitter=None):
    """One period, the same function as every period of the backtest, fitted at the close `returns` ends on (the caller cuts them there) from the holding
    `previous`: {"period", "rows", "seconds", ...}."""
    started = time.perf_counter()
    lookback = int(settings.get("lookback", 252))
    window = returns.iloc[-lookback:]
    result = fitter(window, previous) if fitter else fit_period(window, settings, previous=previous, benchmark=benchmark, pin=pin)
    if fitter and pin:
        weights = pinned(result["weights"], pin)
        result = {**result, "weights": weights, "riskShare": risk_shares(weights, window, settings)}
    weights = result["weights"]
    gs = groups_of(settings)
    rows = [{"asset": a, "group": gs.get(a), "weight": float(weights[a]), "riskShare": float(result["riskShare"][a]),
             "published": None if published is None else float(published.get(a, 0.0)), "bound": result["bound"].get(a)} for a in weights.index]
    out = {"period": str(returns.index[-1].date()), "rows": rows, "effectiveAssets": effective_assets(weights), "status": result["status"],
           "note": result["note"], "seconds": time.perf_counter() - started}
    if published:
        out["drift"] = drift_of(window, weights, published, settings)
    return out


def drift_of(window, weights, published, settings):
    """What the published weights hold now against this period's target, on this window's estimate of risk (the settings' covariance and risk measure, the
    same function as every period's risk shares): the volatility of each, a year, and the largest gap between their risk shares."""
    cov = fitted_covariance(window, settings)
    annual = float(settings["annualization"])
    new = weights.reindex(window.columns).fillna(0.0)
    old = pd.Series([float(published.get(a, 0.0)) for a in window.columns], index=window.columns)
    vol = lambda w: float(np.sqrt(max(float(w @ cov @ w), 0.0) * annual))  # noqa: E731
    share_old, share_new = risk_shares(old, window, settings, cov), risk_shares(new, window, settings, cov)
    return {"publishedVol": vol(old.to_numpy()), "targetVol": vol(new.to_numpy()), "riskShareGap": float(np.max(np.abs(share_old - share_new))) if len(new) else 0.0}


# --- the checks of an allocation (the thresholds are decided in the build notes, 7.6) --------------------------------------------
def checks(periods, metrics_, equal_metrics, settings, sensitivity=None, pool=None, cut_short=()):
    """[{id, name, value, need, state}]: state is pass, fail or unknown. Beats equal weight is first."""
    n = len(periods) or 1
    pool = pool or len(settings.get("pool", [])) or 1
    out = []
    sharpe, base = metrics_["sharpe"], (equal_metrics or {}).get("sharpe")
    known = sharpe is not None and base is not None
    out.append({"id": "beats_equal", "name": "Beats equal weight, net", "value": f"{sharpe:.2f} vs {base:.2f}" if known else None, "need": "≥ equal weight",
                "state": "unknown" if not known else "pass" if sharpe >= base else "fail"})
    binding = sum(1 for p in periods if any(kind != "zero" for kind in p["bound"].values())) / n   # holding nothing is the long-only floor, not a limit set
    out.append({"id": "binding", "name": "Limits that bind", "value": f"{binding:.0%}", "need": "≤ 50%", "state": "pass" if binding <= 0.5 else "fail"})
    eff = float(np.mean([effective_assets(p["weights"]) for p in periods])) if periods else 0.0
    top = max(periods, key=lambda p: float(p["weights"].max())) if periods else None
    largest = float(top["weights"].max()) if top is not None else 0.0
    cap = settings.get("max_weight")
    cap_limit = float(cap) / 100 if cap not in (None, "") else None
    need_assets = min(5, pool / 2)
    top_ok = largest <= 0.30 + 1e-9 or cap_limit is not None and cap_limit < 0.30 and largest <= cap_limit + 1e-6
    where = f" {top['weights'].idxmax()}, {top['heldFrom']:%b} {top['heldFrom'].day}, {top['heldFrom'].year}" if top is not None else ""
    out.append({"id": "effective", "name": "Effective assets, largest", "value": f"{eff:.1f}, {largest:.0%}{where}", "need": f"≥ {need_assets:g}, ≤ 30%",
                "state": "pass" if eff >= need_assets and top_ok else "fail",
                **({"at": {"date": f"{top['heldFrom']:%Y-%m-%d}", "asset": str(top["weights"].idxmax())}} if top is not None else {})})
    fell = [p for p in periods if p["status"] not in ("optimal", "search limit", "inaccurate")]
    out.append({"id": "solver", "name": "Solver failed or fell back", "value": f"{len(fell)} of {len(periods)} periods", "need": "none", "state": "pass" if not fell else "fail",
                **({"at": {"date": f"{fell[0]['heldFrom']:%Y-%m-%d}", "fix": fell[0].get("fix")}} if fell else {})})
    if sensitivity is None:
        out.append({"id": "sensitivity", "name": "Lookback or covariance, one step", "value": None, "need": "≤ 8 pt", "state": "unknown"})
    else:
        worst = max(sensitivity.values())
        out.append({"id": "sensitivity", "name": "Lookback or covariance, one step", "value": f"{worst * 100:.1f} pt", "need": "≤ 8 pt",
                    "state": "pass" if worst * 100 <= 8 + 1e-9 else "fail"})
    out.append({"id": "history", "name": "History cut short", "value": ", ".join(cut_short) if cut_short else f"0 of {pool} assets", "need": "none", "state": "fail" if cut_short else "pass"})
    return out


def sensitivity(returns, settings, benchmark=None, detail=None, annual=None, fitter=None, base=None, risk_free=0.0):
    """The mean per-period change of weights (half the sum of absolute changes) when the lookback is halved or doubled, or the covariance estimate is the
    other of sample and Ledoit-Wolf (the two every estimate is judged against); the largest of them decides the check. Written as code (`fitter`, the user's own
    estimator) only the lookback moves: the estimate is the user's. An alternative that cannot be compared (too few periods in common) is named in `detail`."""
    first_run = base or backtest(returns, settings, benchmark=benchmark, fitter=fitter, risk_free=risk_free)
    base_sharpe = metrics(first_run["daily"], annual=annual, risk_free=risk_free)["sharpe"]
    lookback = int(settings.get("lookback", 252))
    alternatives = {"lookback x0.5": {**settings, "lookback": max(20, lookback // 2)}, "lookback x2": {**settings, "lookback": lookback * 2}}
    if fitter is None:
        other_estimate = "ledoit_wolf" if settings.get("covariance", "empirical") == "empirical" else "empirical"
        alternatives["covariance"] = {**settings, "covariance": other_estimate}
    out = {}
    first = {p["heldFrom"]: p["weights"] for p in first_run["periods"]}
    for name, alt in alternatives.items():
        label = f"Lookback {alt['lookback']} days" if name.startswith("lookback") else f"Covariance, {O.COVARIANCES[alt['covariance']][0].lower()}"
        changed = backtest(returns, alt, benchmark=benchmark, fitter=fitter, risk_free=risk_free)
        diffs = [0.5 * float(np.abs(p["weights"] - first[p["heldFrom"]]).sum()) for p in changed["periods"] if p["heldFrom"] in first]
        if detail is not None:
            detail[name] = {"label": label, "sharpe": base_sharpe, "changed": metrics(changed["daily"], annual=annual, risk_free=risk_free)["sharpe"] if changed["periods"] else None,
                            **({} if diffs else {"why": f"{label}: no period in common with this run"})}
        if diffs:
            out[name] = float(np.mean(diffs))
    return out or None


def period_rows(period, settings, published=None):
    """The rows of the this-period table from one backtest period: asset, group, weight, risk share, the limit that binds."""
    gs = groups_of(settings)
    before = period.get("drifted")
    return [{"asset": a, "group": gs.get(a), "weight": float(period["weights"][a]), "riskShare": float(period["riskShare"][a]),
             "published": None if published is None else float(published.get(a, 0.0)), "bound": period["bound"].get(a),
             "drifted": None if before is None else float(before[a])} for a in period["weights"].index]


def custom_needs_benchmark(estimator):
    """Whether the user's estimator (or the optimization a Pipeline ends in) is fitted against a benchmark: a benchmark tracker, or a tracking-error limit."""
    from skfolio.optimization import BenchmarkTracker
    last = estimator.steps[-1][1] if hasattr(estimator, "steps") else estimator
    return isinstance(last, BenchmarkTracker) or getattr(last, "max_tracking_error", None) is not None


def fit_custom(window, estimator, previous=None, settings=None, benchmark=None):
    """One period with an estimator the user's code built (any skfolio optimization, or a Pipeline that ends in one): a fresh clone is fitted on the window,
    against the run's benchmark when it tracks one (none raises SettingsError). A solver that fails on this window's data falls back to equal weight
    and says why; anything else (an estimator that needs what the run does not give) stops the run."""
    from sklearn.base import clone
    assets = list(window.columns)
    started = time.perf_counter()
    y = {}
    if custom_needs_benchmark(estimator):
        if benchmark is None:
            raise SettingsError("This estimator tracks a benchmark; set one", ["benchmark"])
        y = {"y": benchmark.loc[window.index]}
    model = clone(estimator)
    if previous is not None and hasattr(model, "set_params") and "previous_weights" in model.get_params(deep=False):
        model.set_params(previous_weights=np.asarray(previous, float))
    try:
        model.fit(window, **y)
    except _solver_failures() as error:
        first = why_not_fitted(window, error)
        weights, status = pool_equal(assets), "equal"
        note = f"equal weight: {first}" if flat_assets(window) else f"equal weight: the estimator could not be fitted on this window ({first})"
    else:
        last = model.steps[-1][1] if hasattr(model, "steps") else model
        names = list(getattr(last, "feature_names_in_", ()))
        if len(names) != len(last.weights_):  # a pre-selection step leaves the optimizer fewer assets: ask the steps before it which
            names = list(model[:-1].get_feature_names_out(assets)) if hasattr(model, "steps") and len(model.steps) > 1 else assets
        weights = pd.Series(np.asarray(last.weights_, float), index=names).reindex(assets).fillna(0.0)
        if not np.isfinite(weights).all():
            raise RuntimeError("no finite weights")
        status, note = "optimal", ""
    return {"weights": weights, "status": status, "note": note, "fix": "pool" if status == "equal" and flat_assets(window) else None, "seconds": time.perf_counter() - started, "bound": {},
            "riskShare": risk_shares(weights, window, settings or {})}
