"""The allocation engine in the research environment on synthetic returns (tests/fixtures/allocation): every objective fits, every limit holds,
the walk-forward numbers reconcile with a hand loop, the trial is the last period, and one whole run through the worker.

Not part of the daily suite: set TALYX_NATIVE_SMOKE=1 (see environments.py).
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.native.environments import QUANT, environment

pytestmark = pytest.mark.skipif(not os.environ.get("TALYX_NATIVE_SMOKE"), reason="Set TALYX_NATIVE_SMOKE=1 to run native engines")
ROOT = Path(__file__).resolve().parents[2]
DRIVE = ROOT / "tests" / "fixtures" / "allocation" / "drive.py"


def drive(case, *more):
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(QUANT), str(ROOT)])}
    done = subprocess.run([str(environment("research.lock", "3.12")), "-B", "-W", "ignore", str(DRIVE), case, *more], capture_output=True, text=True, env=env, timeout=900)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_equal_weight_matches_a_hand_loop_and_compounds():
    out = drive("equal_reconcile")
    assert out["diff"] < 1e-9 and out["compound_diff"] < 1e-12 and out["periods"] > 40


def test_trial_is_the_last_period():
    assert drive("trial_last")["same"] < 1e-9


def test_risk_parity_shares_equal_and_sum_to_one():
    out = drive("risk_parity")
    assert abs(out["sum"] - 1) < 1e-9 and max(out["shares"]) - min(out["shares"]) < 1e-3


def test_each_objective_fits():
    out = drive("each_objective")
    assert all(status == "optimal" and abs(total - 1) < 1e-6 for status, total in out.values()), out


def test_limits_hold():
    out = drive("limits")
    assert out["max"] <= 0.25 + 1e-6 and out["bonds"] <= 0.40 + 1e-6 and out["status"] == "optimal"
    assert out["neg"] >= -0.30 - 1e-6 and out["pos"] <= 1.30 + 1e-6 and abs(out["net"] - 1) < 1e-6
    assert out["count_cvar"] <= 3 and out["count_semi"] <= 3 and out["status3"] == "optimal" and out["status4"] in ("optimal", "search limit")
    assert out["linear"] <= 0.30 + 1e-6


def test_cost_aware_trades_less():
    out = drive("cost_aware")
    assert out["on"] <= out["off"]


def test_regularization_and_engine_parameters_reach_the_estimator():
    out = drive("robust_reg_engine")
    assert out["reg_differs"] > 1e-4 and out["engine"] == 0.02


def test_missing_not_zero():
    assert drive("missing_not_zero")["zeros"] == 0


def test_scip_limit_is_repeatable():
    out = drive("scip_repeat")
    assert out["same"] == 0.0


def test_a_whole_run_through_the_worker(tmp_path):
    out = drive("worker", str(tmp_path))
    assert out["answer"]["ok"] and abs(out["sum"] - 1) < 1e-9
    assert {"summary.json", "weights.json", "risk.json", "returns.json", "settings.json", "style.json", "robust.json"} <= set(out["tabs"])
    assert [item["role"] for item in out["fig_keys"]] == ["main", "benchmark", "benchmark"]
    assert [state for _, state, _ in out["checks"]][0] in ("pass", "fail")


READS = {  # meta.key -> a fragment every trace's hover must hold, from the draft's read-out table (build notes A, section 6 item 11)
    "allocation.results.vol_calibration": "%{y:.2f}%", "allocation.results.frontier": "Predicted volatility %{x:.2f}%",
    "allocation.work.periods": "%{y:.1f}%", "allocation.results.weights": "%{y:.1f}%", "allocation.results.risk_shares": "%{y:.1f}%",
    "allocation.results.growth": "%{y:.3f}", "allocation.results.drawdown": "%{y:+.1f}%", "allocation.results.turnover": "Turnover %{y:.1f}%",
    "allocation.results.corr": "Correlation %{z:+.2f}", "allocation.results.weight_risk": "%{y:.2f}%", "allocation.results.annual": "%{y:+.1f}%",
    "allocation.results.monthly": "%{z:+.2f}%", "allocation.results.rolling": "Sharpe %{y:.2f}", "allocation.results.distribution": "% of days",
    "allocation.results.contribution": "%{y:+.2f} pt",
}


def test_every_figure_reads_out_like_the_draft(tmp_path):
    reads = drive("worker", str(tmp_path))["reads"]
    assert set(READS) <= set(reads), set(READS) - set(reads)
    for key, fragment in READS.items():
        assert reads[key] and all(fragment in template for template in reads[key]), (key, reads[key])
        assert all("<b>" not in template and "<extra></extra>" in template for template in reads[key]), key
    assert "Cost %{customdata:.2f} bps" in reads["allocation.results.turnover"][0]


def test_stacked_weights_are_steps_coloured_by_group_and_turnover_skips_the_buy_in(tmp_path):
    out = drive("worker", str(tmp_path))
    for name, parts in out["stacks"].items():
        assert parts and all(role == "part" and shape == "hv" and width == 0 for role, _, _, shape, width in parts), name
    assert len({index for _, index, _, _, _ in out["stacks"]["byGroup"]}) == len(out["stacks"]["byGroup"])  # a colour a group, no shade
    assert all(shade is None for _, _, shade, _, _ in out["stacks"]["byGroup"])
    assert len({index for _, index, _, _, _ in out["stacks"]["weights"]}) < len(out["stacks"]["weights"])   # assets of one group share its colour
    assert any(shade is not None for _, _, shade, _, _ in out["stacks"]["weights"])                       # and are told apart by a shade
    assert out["work_stack"] and all(role == "part" for role, _, _ in out["work_stack"])
    bars = out["turnover"]
    assert bars["role"] == "part" and bars["index"] == 0 and bars["n"] <= bars["periods"] - 1 and bars["min"] > 0   # no buy-in bar, only periods that traded


def test_allocate_with_custom_estimator(tmp_path):
    out = drive("custom", str(tmp_path))
    assert out["unselected_zero"] and out["captured"] and out["checked"].startswith("estimator must be a skfolio optimization")


def test_values_a_new_period_is_the_backtests_period(tmp_path):
    """What a published allocation makes for its next bar is the weights the whole backtest has for that period: same window, same drifted holding, so a turnover
    limit and costs in the objective give the same answer."""
    out = drive("values", str(tmp_path))
    assert out["rows"] == 12 and out["diff"] < 1e-9 and out["share_diff"] < 1e-9  # the weights and the risk shares, both from the same window


def test_style_exposures_come_from_styles_py_over_the_stocks_only(monkeypatch):
    """The Style tab: only styles that can be computed, a contribution each and a Specific row; none for a pool without three stocks."""
    monkeypatch.syspath_prepend(QUANT)
    import numpy as np
    import pandas as pd
    from native import allocation_results as R
    rng = np.random.default_rng(1)
    index = pd.bdate_range("2019-01-01", periods=900)
    cols = ["A", "B", "C", "D", "BND"]
    returns = pd.DataFrame(rng.normal(0.0004, 0.01, (900, 5)), index=index, columns=cols)
    groups = {"A": "Stocks", "B": "Stocks", "C": "Stocks", "D": "Stocks", "BND": "Bonds"}
    periods = [{"date": d, "heldFrom": d + pd.Timedelta(days=1), "weights": pd.Series([.25, .25, .2, .1, .2], index=cols), "riskShare": pd.Series([.2] * 5, index=cols), "turnover": 0., "cost": 0.}
               for d in pd.date_range("2020-03-31", periods=20, freq="ME")]
    daily = (returns * 0.2).sum(axis=1)
    held = pd.DataFrame([p["weights"].to_numpy() for p in periods], index=[p["heldFrom"] for p in periods], columns=cols).reindex(index).ffill().shift(1).fillna(0.0)
    rows = R.style_exposures(returns, held, groups, None, 252, daily)
    names = [row["style"] for row in rows]
    assert names[-1] == "Specific" and "Momentum" in names and "Liquidity" not in names and "Size" not in names  # no volume: only the styles from prices
    assert all(row["contribution"] is not None for row in rows)
    assert abs(sum(row["contribution"] for row in rows) - daily.mean() * 252) < 1e-9  # the styles and Specific add up to the run's return a year
    assert R.style_exposures(returns, held, {"A": "Stocks", "B": "Bonds"}, None, 252, daily) == []


def test_frontier_says_how_many_points_did_not_solve(monkeypatch):
    """3 of 20 frontier points without a solution are left out of the line and counted under the chart."""
    monkeypatch.syspath_prepend(QUANT)
    import numpy as np
    import pandas as pd
    from native import allocation_results as R
    from native import allocation_work as W
    rng = np.random.default_rng(2)
    returns = pd.DataFrame(rng.normal(0.0004, 0.01, (400, 3)), index=pd.bdate_range("2020-01-01", periods=400), columns=["A", "B", "C"])
    weights = np.tile(np.array([.5, .3, .2]), (20, 1))
    weights[[3, 4, 19]] = np.nan

    class Estimator:
        weights_ = weights
        prior_estimator_ = type("Prior", (), {"return_distribution_": type("Distribution", (), {"covariance": np.cov(returns.to_numpy(), rowvar=False), "mu": returns.mean().to_numpy()})})
        get_params = staticmethod(lambda: {})

        def set_params(self, **_):
            return self

        def fit(self, _):
            return self
    monkeypatch.setattr(W, "estimator", lambda settings, assets: Estimator())
    figure = R.frontier_figure(pd.Series([.5, .3, .2], index=returns.columns), returns, {}, 252)
    assert [item["text"] for item in figure["layout"]["annotations"]] == ["3 of 20 points did not solve"]


def test_asset_stops_mid_run():
    out = drive("asset_stops")
    assert out["first_left"] is not None and out["texts"][0].startswith("A5 left: no price after ")
    assert out["weight"] == 0.0 and abs(out["total"] - 1) < 1e-6 and out["finite"]


def test_values_on_weekly_returns_are_the_backtests_period(tmp_path):
    """A published allocation aligned weekly makes the same period the backtest on weekly returns made (the lookback counts rows, in days' worth)."""
    out = drive("values_weekly", str(tmp_path))
    assert out["diff"] < 1e-9 and out["annual"] == 52


def test_custom_estimator_is_used_from_trial_to_the_next_published_period(tmp_path):
    result = drive("custom_lifecycle", str(tmp_path))
    target = {"AAPL": 1, "MSFT": 0, "AMZN": 0}
    for step in ("trial", "next"):
        assert result[step] == pytest.approx(target, abs=1e-6)
    for row in [result["wrapper"], *result["backtest"], result["cut"]]:
        assert row == pytest.approx([1, 0, 0], abs=1e-6)
