"""The allocation rules that would otherwise go wrong without a sound (checkup s5): each number expected here is built by tests/fixtures/allocation/rules.py
straight from skfolio, sklearn or by hand, never from the code under test. All cases run in one research process.

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
RULES = ROOT / "tests" / "fixtures" / "allocation" / "rules.py"


@pytest.fixture(scope="module")
def found():
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(QUANT), str(ROOT)])}
    done = subprocess.run([str(environment("research.lock", "3.12")), "-B", "-W", "ignore", str(RULES)], capture_output=True, text=True, env=env, timeout=900)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def ok(value):
    assert not (isinstance(value, dict) and "error" in value), value
    return value


def test_every_covariance_choice_fits_without_falling_back(found):
    assert ok(found["every_covariance_fits"]) == {key: "optimal" for key in found["every_covariance_fits"]}


def test_exponentially_weighted_is_skfolios_with_its_half_life(found):
    for half_life, row in ok(found["ew_is_skfolios_ew"]).items():
        assert row["status"] == "optimal" and row["diff"] < 1e-6 and row["from_equal"] > 0.01, (half_life, row)


def test_tracking_error_cap_is_a_year_and_holds_with_the_other_limits(found):
    row = ok(found["tracking_error_binds"])
    assert row["status"] == "optimal" and row["te"] <= row["cap"] * 1.001 and row["te"] < row["free"]
    assert row["max"] <= 0.60 + 1e-6 and row["bonds"] <= 0.70 + 1e-6


def test_risk_shares_are_the_chosen_measures(found):
    row = ok(found["risk_shares_follow_the_measure"])
    assert row["status"] == "optimal" and abs(row["sum"] - 1) < 1e-9
    assert max(abs(share - 1 / 6) for share in row["shares"]) < 0.02, row["shares"]  # CDaR risk parity: each sixth of the CDaR


def test_risk_shares_are_the_chosen_covariances(found):
    row = ok(found["risk_shares_follow_the_covariance"])
    assert row["diff"] < 1e-9
    assert max(abs(share - 1 / 6) for share in row["rp"]) < 1e-3


def test_costs_in_the_objective_are_one_trade_a_holding_and_only_where_they_act(found):
    row = ok(found["costs_in_the_objective_once_a_holding"])
    for frequency in ("monthly", "weekly", "quarterly"):
        assert all(abs(given - row[frequency]["hand"]) < 1e-12 for given in row[frequency]["given"]), (frequency, row[frequency])
    assert row["min_risk"] == 0 and row["risk_parity"] == 0 and row["hrp"] == 0
    assert row["capabilities"] == {"min_risk": False, "max_ratio": True, "max_diversification": True, "risk_parity": False, "hrp": False, "benchmark_tracking": False}


def test_the_backtest_charges_each_trade_once(found):
    row = ok(found["backtest_charges_a_trade_once"])
    assert row["costs"] > 0 and row["max_error"] < 1e-12


def test_a_settings_error_is_named_and_never_falls_back(found):
    row = ok(found["settings_errors"])
    assert row["linear"] == {"message": "Line 1: SPX is not in the pool", "fields": ["linear"]}
    assert row["check_linear"] == row["linear"]
    assert row["tracking_no_benchmark"]["fields"] == ["max_tracking"] and row["check_tracking"]["fields"] == ["max_tracking"]
    assert row["benchmark_tracking_no_benchmark"]["fields"] == ["objective"]
    assert row["bad_option"]["fields"] == ["covariance_parameters"] and "alpha" in row["bad_option"]["message"]
    assert row["bad_engine"]["fields"] == ["objective_parameters"] and "nope" in row["bad_engine"]["message"]
    assert row["check_ok"] is None


def test_turnover_cap_leaves_the_buy_in_alone_and_caps_each_asset_after(found):
    row = ok(found["turnover_cap"])
    assert row["first_status"] == "optimal" and row["first_diff"] < 1e-6
    assert row["statuses"] == ["optimal"] and row["largest_move"] <= 0.02 + 1e-6


def test_the_fallback_keeps_the_shorts_and_a_broken_limit_is_marked(found):
    row = ok(found["fallback_keeps_shorts_and_names_breaches"])
    assert row["keeps"] == {"long_cap": 130, "short_cap": 30, "net": 100}
    assert row["flags"]["A1"] == "over group" and row["flags"]["A2"] == "over group"
    # a period not solved as set names the settings let go and where they are changed, never the engine's text
    assert row["notes"][0] == ["group limits let go on this window", "limits"]
    assert row["notes"][1] == ["turnover, tracking let go on this window", "limits"]
    assert row["notes"][2] == ["equal weight: A1 did not move over the lookback", "pool"] and row["notes"][3][1] == "objective"


def test_limits_that_bind_do_not_count_the_zero_floor_and_the_largest_is_named(found):
    row = ok(found["binding_ignores_the_zero_floor"])
    assert row["binding"] == ["0%", "pass"]
    assert row["effective"][0].endswith("20% A2, Feb 3, 2020")
    # a check points at the period and the asset it names, or at the setting a fallback came from
    assert row["at"]["effective"] == {"date": "2020-02-03", "asset": "A2"} and row["at"]["solver"] is None
    assert row["solver_at"] == {"date": "2020-03-02", "fix": "limits"}


def test_nested_clusters_take_the_distance_and_the_covariance(found):
    row = ok(found["nco_uses_distance_and_covariance"])
    assert row["cov"] > 1e-4 and row["distance"] == "SpearmanDistance"


def test_a_volatility_cap_is_a_year(found):
    row = ok(found["risk_cap_is_a_year"])
    assert abs(row["std"] - row["hand_std"]) < 1e-15 and abs(row["variance"] - row["hand_var"]) < 1e-15
    assert row["status"] == "optimal" and row["vol"] <= 0.09 * 1.001


def test_the_review_drift_reads_risk_with_the_settings_estimate(found):
    row = ok(found["review_drift_is_the_same_estimate"])
    assert row["same_gap"] < 1e-12 and abs(row["vol"] - row["hand_vol"]) < 1e-12 and abs(row["gap"] - row["hand_gap"]) < 1e-12


def test_a_return_floor_is_a_year_and_the_return_objectives_fit(found):
    """Return at least X% a year under minimum risk (skfolio's min_return a period); maximum return and maximum utility fit."""
    row = ok(found["return_floor_and_return_objectives"])
    assert row["status"] == "optimal" and row["expected"] >= row["floor"] - 1e-6 and row["floor_risk"] >= row["plain_risk"]
    assert row["max_return_status"] == "optimal" and row["utility_status"] == "optimal" and row["best"] == row["hand_best"]
    assert row["bad"] and row["bad"]["fields"] == ["min_return"]
