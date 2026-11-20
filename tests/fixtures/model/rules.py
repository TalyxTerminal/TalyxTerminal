"""Runs in the research environment: python rules.py; prints one JSON line with every case of tests/native/test_model_rules.py.

The expected numbers are built here from the libraries themselves (xgboost, numpy least squares, Qlib's own definition of its report columns), never from
the code under test."""
import json
import traceback

import numpy as np
import pandas as pd

out = {}


def case(name):
    def wrap(fn):
        try:
            out[name] = fn()
        except Exception as error:  # noqa: BLE001 - the test reads the failure as the case's value
            out[name] = {"error": f"{type(error).__name__}: {error}", "trace": traceback.format_exc()[-800:]}
        return fn
    return wrap


rng = np.random.default_rng(3)


def part(rows, days, shift=0):
    x = rng.normal(0, 1, (rows, 4)).astype(np.float32)
    y = 0.3 * x[:, 0] + rng.normal(0, 1, rows)  # mostly noise: the validation loss turns early
    t = np.repeat(np.arange(days) + shift, rows // days)
    return {"x": x, "y": y, "t": t, "i": np.tile(np.arange(rows // days), days)}


TRAIN, VALID = part(2000, 100), part(600, 30, 200)
FEATURES = ["a", "b", "c", "d"]


@case("xgboost_uses_the_best_round")
def _():
    import xgboost as xgb
    from native import model_algorithms as A
    params = {"lr": 0.3, "depth": 6, "ff": 1.0, "bf": 1.0, "l1": 0.0, "l2": 1.0, "minleaf": 1.0, "rounds": 200, "stop": 10}
    fitted = A.fit("xgboost", {"train": TRAIN, "valid": VALID}, FEATURES, params, seed=7, threads=1)
    raw = {"eta": 0.3, "max_depth": 6, "colsample_bytree": 1.0, "subsample": 1.0, "alpha": 0.0, "lambda": 1.0, "min_child_weight": 1.0, "nthread": 1, "seed": 7,
           "tree_method": "hist", "verbosity": 0, "objective": "reg:squarederror"}
    full = xgb.train(raw, xgb.DMatrix(TRAIN["x"], label=TRAIN["y"]), num_boost_round=200, evals=[(xgb.DMatrix(VALID["x"], label=VALID["y"]), "valid")],
                     early_stopping_rounds=10, verbose_eval=False)
    rounds = full.best_iteration + 1
    best = xgb.train(raw, xgb.DMatrix(TRAIN["x"], label=TRAIN["y"]), num_boost_round=rounds)   # trained to the best round only, by xgboost itself
    test = rng.normal(0, 1, (300, 4)).astype(np.float32)
    mine = fitted.predict(test)
    parts = fitted.contributions(test)
    return {"best": fitted.best, "rounds": rounds, "diff": float(np.abs(mine - best.predict(xgb.DMatrix(test))).max()),
            "full_diff": float(np.abs(full.predict(xgb.DMatrix(test)) - best.predict(xgb.DMatrix(test))).max()),
            "contrib_sum": float(np.abs(parts.sum(axis=1) - mine).max())}


@case("linear_trains_on_the_validation_rows_too")
def _():
    from native import model_algorithms as A
    fitted = A.fit("linear", {"train": TRAIN, "valid": VALID}, FEATURES, {}, seed=7, threads=1)
    x = np.vstack([TRAIN["x"], VALID["x"]]).astype(float)
    y = np.concatenate([TRAIN["y"], VALID["y"]])
    design = np.hstack([x, np.ones((len(x), 1))])
    coef = np.linalg.lstsq(design, y, rcond=None)[0]
    test = rng.normal(0, 1, (50, 4)).astype(np.float32)
    return {"diff": float(np.abs(fitted.predict(test) - np.hstack([test, np.ones((50, 1))]) @ coef).max())}


@case("portfolio_report_with_and_without_cost")
def _():
    from native import model_run as R
    days = pd.bdate_range("2024-01-01", periods=300)
    frame = pd.DataFrame({"return": rng.normal(0.0008, 0.01, 300), "bench": rng.normal(0.0003, 0.009, 300), "cost": np.full(300, 0.0002),
                          "turnover": np.full(300, 0.1)}, index=days)
    risk, lines = R.portfolio_numbers(frame, 252)
    without = frame["return"] - frame["bench"]
    with_cost = without - frame["cost"]
    return {"without": risk["Excess return, without cost"]["annualized_return"], "with": risk["Excess return, with cost"]["annualized_return"],
            "hand_without": float(without.mean() * 252), "hand_with": float(with_cost.mean() * 252),
            "ir_with": risk["Excess return, with cost"]["information_ratio"], "hand_ir_with": float(with_cost.mean() / with_cost.std(ddof=1) * np.sqrt(252)),
            "main_last": float(lines["excess"].iloc[-1]), "hand_main_last": float((1 + with_cost).prod() - 1)}


@case("portfolio_benchmark_is_close_to_close")
def _():
    from native import model_run as R
    closes = pd.DataFrame({"A": [10, 11, 12.1, 12.1], "B": [20, 19, 19, 20.9]}, index=pd.bdate_range("2024-01-01", periods=4))
    bench = R.universe_benchmark(closes)
    return {"bench": [float(v) for v in bench.to_numpy()], "hand": [0.5 * (0.1 - 0.05), 0.5 * (0.1 + 0.0), 0.5 * (0.0 + 0.1)]}


print(json.dumps(out, default=str))
