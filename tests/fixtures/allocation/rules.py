"""Runs in the research environment: python rules.py; prints one JSON line with every case of tests/native/test_allocation_rules.py.

Each expected number is built here independently of `allocation_work`: straight from skfolio or sklearn, or by hand with numpy."""
import json
import math
import traceback

import numpy as np
import pandas as pd
from native import allocation_options as O
from native import allocation_work as W

from tests.fixtures.allocation import returns as R

r = R.make()
window = r.iloc[-252:]
A = 252
out = {}


def case(name):
    def wrap(fn):
        try:
            out[name] = fn()
        except Exception as error:  # noqa: BLE001 - the test reads the failure as the case's value
            out[name] = {"error": f"{type(error).__name__}: {error}", "trace": traceback.format_exc()[-800:]}
        return fn
    return wrap


def settings(**more):
    return R.settings(annualization=A, **more)


def refused(fn):
    try:
        fn()
    except ValueError as error:
        return {"message": str(error), "fields": list(getattr(error, "fields", []))}
    return None


@case("every_covariance_fits")
def _():
    return {key: W.fit_period(window, settings(covariance=key))["status"] for key in O.COVARIANCES}


@case("ew_is_skfolios_ew")
def _():
    from skfolio.moments import EWCovariance
    from skfolio.optimization import MeanRisk
    from skfolio.prior import EmpiricalPrior
    found = {}
    for half_life, text in ((40, ""), (10, "half_life=10")):
        mine = W.fit_period(window, settings(covariance="ew", covariance_parameters=text))
        theirs = MeanRisk(prior_estimator=EmpiricalPrior(covariance_estimator=EWCovariance(half_life=half_life)), min_weights=0.0, budget=1.0).fit(window).weights_
        found[str(half_life)] = {"status": mine["status"], "diff": float(np.abs(mine["weights"].to_numpy() - theirs).max()),
                                 "from_equal": float(np.abs(mine["weights"].to_numpy() - 1 / 6).max())}
    return found


@case("tracking_error_binds")
def _():
    bench = 0.6 * r["A3"] + 0.4 * r["A1"]
    b = bench.loc[window.index].to_numpy()
    free = W.fit_period(window, settings(), benchmark=bench)["weights"].to_numpy()
    te = lambda w: float(np.sqrt(((window.to_numpy() @ w - b) ** 2).sum() / (len(b) - 1)) * math.sqrt(A))  # noqa: E731 - RMSE a period, a year's scale
    cap = te(free) / 2
    held = W.fit_period(window, settings(max_tracking=cap * 100, max_weight=60, group_limits=[{"group": "Bonds", "max": 70}]), benchmark=bench)
    w = held["weights"].to_numpy()
    return {"free": te(free), "cap": cap, "te": te(w), "status": held["status"], "max": float(w.max()), "bonds": float(w[:2].sum())}


@case("risk_shares_follow_the_measure")
def _():
    rp = W.fit_period(window, settings(objective="risk_parity", risk_measure="cdar"))
    return {"status": rp["status"], "shares": [float(x) for x in rp["riskShare"]], "sum": float(rp["riskShare"].sum())}


@case("risk_shares_follow_the_covariance")
def _():
    from sklearn.covariance import LedoitWolf
    s = settings(objective="min_risk", covariance="ledoit_wolf")
    fit = W.fit_period(window, s)
    w = fit["weights"].to_numpy()
    cov = LedoitWolf().fit(window.to_numpy()).covariance_
    hand = w * (cov @ w) / (w @ cov @ w)
    rp = W.fit_period(window, settings(objective="risk_parity", covariance="ledoit_wolf"))
    return {"diff": float(np.abs(fit["riskShare"].to_numpy() - hand).max()), "rp": [float(x) for x in rp["riskShare"]]}


@case("costs_in_the_objective_once_a_holding")
def _():
    costs = [{"group": g, "bps": 20} for g in ("Bonds", "Stocks", "Other")]
    found = {}
    for frequency, per_year in (("monthly", 12), ("weekly", 52), ("quarterly", 4)):
        est = W.estimator(settings(objective="max_ratio", cost_aware=True, costs=costs, frequency=frequency), R.ASSETS, previous=np.full(6, 1 / 6))
        found[frequency] = {"given": [float(x) for x in est.transaction_costs], "hand": 20 / 1e4 / (A / per_year)}
    for objective in ("min_risk", "risk_parity", "hrp"):
        est = W.estimator(settings(objective=objective, cost_aware=True, costs=costs), R.ASSETS, previous=np.full(6, 1 / 6))
        found[objective] = float(np.abs(np.asarray(est.get_params().get("transaction_costs", 0.0), float)).sum())
    found["capabilities"] = {objective: O.capabilities(objective)["costAware"] for objective in ("min_risk", "max_ratio", "max_diversification", "risk_parity", "hrp", "benchmark_tracking")}
    return found


@case("backtest_charges_a_trade_once")
def _():
    costs = [{"group": g, "bps": 25} for g in ("Bonds", "Stocks", "Other")]
    run = W.backtest(r, settings(objective="inverse_volatility", costs=costs))
    # The trade takes place at the close, after the previous holdings earned that day's return.
    expected = run["gross"].copy()
    fees = []
    for period in run["periods"]:
        before = 0 if period["drifted"] is None else period["drifted"]
        fee = float((period["weights"] - before).abs().sum()) * 25 / 10_000
        fees.append(fee)
        day = period["heldFrom"]
        expected.loc[day] = (1 + expected.loc[day]) * (1 - fee) - 1
    return {"costs": sum(fees), "max_error": float((run["daily"] - expected).abs().max())}


@case("settings_errors")
def _():
    bench = 0.6 * r["A3"] + 0.4 * r["A1"]
    return {
        "linear": refused(lambda: W.fit_period(window, settings(linear="SPX <= 0.3"))),
        "tracking_no_benchmark": refused(lambda: W.fit_period(window, settings(max_tracking=5))),
        "benchmark_tracking_no_benchmark": refused(lambda: W.fit_period(window, settings(objective="benchmark_tracking"))),
        "bad_option": refused(lambda: W.fit_period(window, settings(covariance="ew", covariance_parameters="alpha=0.1"))),
        "bad_engine": refused(lambda: W.fit_period(window, settings(objective_parameters="risk_aversion=1; nope=2"))),
        "check_linear": refused(lambda: W.check_settings(settings(linear="SPX <= 0.3"), R.ASSETS)),
        "check_tracking": refused(lambda: W.check_settings(settings(max_tracking=5), R.ASSETS)),
        "check_ok": refused(lambda: W.check_settings(settings(max_tracking=5), R.ASSETS, benchmark=bench)),
    }


@case("turnover_cap")
def _():
    s = settings(objective="min_risk", max_turnover=2)
    first = W.fit_period(window, s)
    free = W.fit_period(window, settings(objective="min_risk"))
    run = W.backtest(r, s)
    moves = [float(np.abs(p["weights"].to_numpy() - p["previous"].to_numpy()).max()) for p in run["periods"][1:]]
    return {"first_status": first["status"], "first_diff": float(np.abs(first["weights"] - free["weights"]).max()),
            "statuses": sorted({p["status"] for p in run["periods"]}), "largest_move": max(moves)}


@case("fallback_keeps_shorts_and_names_breaches")
def _():
    class Broken(Exception):
        pass
    s = settings(objective="min_risk", long_cap=130, short_cap=30, net=100, max_weight=40, group_limits=[{"group": "Bonds", "max": 20}])
    flags = W.bound_flags(pd.Series([0.3, 0.3, 0.1, 0.1, 0.1, 0.1], index=R.ASSETS), s)
    relaxed = W.relaxed_settings(s)
    tracked = settings(max_tracking=3, max_turnover=5, group_limits=[{"group": "Bonds", "max": "", "min": ""}])
    return {"keeps": {key: relaxed.get(key) for key in ("long_cap", "short_cap", "net")}, "flags": flags,
            "notes": [W.fallback_note("relaxed", s, "engine text", []), W.fallback_note("relaxed", tracked, "engine text", []),
                      W.fallback_note("equal", s, "A1 did not move over the lookback", ["A1"]), W.fallback_note("equal", s, "engine text", [])]}


@case("binding_ignores_the_zero_floor")
def _():
    periods = [{"bound": {"A1": "zero"}, "weights": pd.Series([0.0, .2, .2, .2, .2, .2], index=R.ASSETS), "status": "optimal", "date": pd.Timestamp("2020-01-31"),
                "heldFrom": pd.Timestamp("2020-02-03")}] * 4
    checks = W.checks(periods, {"sharpe": 1.0}, {"sharpe": 0.5}, settings())
    fell = [{**periods[0], "status": "relaxed", "fix": "limits", "heldFrom": pd.Timestamp("2020-03-02")}]
    return {**{item["id"]: [item["value"], item["state"]] for item in checks}, "at": {item["id"]: item.get("at") for item in checks},
            "solver_at": next(item["at"] for item in W.checks(periods + fell, {"sharpe": 1.0}, {"sharpe": 0.5}, settings()) if item["id"] == "solver")}


@case("nco_uses_distance_and_covariance")
def _():
    base = W.fit_period(window, settings(objective="nco"))["weights"]
    other_cov = W.fit_period(window, settings(objective="nco", covariance="ledoit_wolf"))["weights"]
    W.fit_period(window, settings(objective="nco", distance="spearman"))   # fits with the distance it is given
    est = W.estimator(settings(objective="nco", distance="spearman"), R.ASSETS)
    return {"cov": float(np.abs(base - other_cov).max()), "distance": type(est.distance_estimator).__name__}


@case("risk_cap_is_a_year")
def _():
    est = W.estimator(settings(risk_cap=10, risk_cap_measure="standard_deviation"), R.ASSETS)
    var = W.estimator(settings(risk_cap=10, risk_cap_measure="variance"), R.ASSETS)
    fit = W.fit_period(window, settings(risk_cap=9, risk_cap_measure="standard_deviation", objective="max_ratio"))
    w = fit["weights"].to_numpy()
    vol = float(np.std(window.to_numpy() @ w, ddof=1) * math.sqrt(A))
    return {"std": est.max_standard_deviation, "variance": var.max_variance, "hand_std": 0.10 / math.sqrt(A), "hand_var": 0.10 ** 2 / A, "vol": vol, "status": fit["status"]}


@case("return_floor_and_return_objectives")
def _():
    from skfolio.optimization import MeanRisk, ObjectiveFunction
    plain = W.fit_period(window, settings(objective="min_risk"))["weights"].to_numpy()
    mu = window.mean().to_numpy()
    floor = float(plain @ mu * A * 100) + 1.0   # one point a year above what the least risk expects: the floor binds
    held = W.fit_period(window, settings(objective="min_risk", min_return=floor))
    w = held["weights"].to_numpy()
    by_hand = MeanRisk(objective_function=ObjectiveFunction.MAXIMIZE_RETURN).fit(window).weights_   # skfolio itself, no limits: all in the best mean
    mine = W.fit_period(window, settings(objective="max_return"))
    utility = W.fit_period(window, settings(objective="max_utility"))
    return {"floor": floor, "expected": float(w @ mu * A * 100), "status": held["status"], "plain_risk": float(np.std(window.to_numpy() @ plain, ddof=1)),
            "floor_risk": float(np.std(window.to_numpy() @ w, ddof=1)), "best": int(np.argmax(mu)), "hand_best": int(np.argmax(by_hand)),
            "max_return_status": mine["status"], "max_return_top": float(mine["weights"].max()), "utility_status": utility["status"],
            "bad": refused(lambda: W.fit_period(window, settings(objective="min_risk", min_return="ten")))}


@case("review_drift_is_the_same_estimate")
def _():
    from sklearn.covariance import LedoitWolf
    s = settings(covariance="ledoit_wolf")
    target = pd.Series([0.3, 0.3, 0.1, 0.1, 0.1, 0.1], index=R.ASSETS)
    published = {"A1": 0.2, "A2": 0.2, "A3": 0.2, "A4": 0.2, "A5": 0.1, "A6": 0.1}
    same = W.drift_of(window, target, target.to_dict(), s)
    moved = W.drift_of(window, target, published, s)
    cov = LedoitWolf().fit(window.to_numpy()).covariance_
    old, new = np.array(list(published.values())), target.to_numpy()
    share = lambda w: w * (cov @ w) / (w @ cov @ w)  # noqa: E731
    return {"same_gap": same["riskShareGap"], "vol": moved["publishedVol"], "hand_vol": float(np.sqrt(old @ cov @ old * A)),
            "gap": moved["riskShareGap"], "hand_gap": float(np.abs(share(old) - share(new)).max())}


print(json.dumps(out, default=str))
