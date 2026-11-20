"""The model rules that would otherwise go wrong without a sound (checkup s4): each expected number is built by tests/fixtures/model/rules.py from the libraries
themselves or by hand, never from the code under test. All cases run in one research process.

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


@pytest.fixture(scope="module")
def found():
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(QUANT), str(ROOT)])}
    done = subprocess.run([str(environment("research.lock", "3.12")), "-B", "-W", "ignore", str(ROOT / "tests" / "fixtures" / "model" / "rules.py")], capture_output=True, text=True,
                          env=env, timeout=900)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def ok(value):
    assert not (isinstance(value, dict) and "error" in value), value
    return value


def test_xgboost_predicts_with_the_best_round_only(found):
    row = ok(found["xgboost_uses_the_best_round"])
    assert row["best"] == row["rounds"] and row["full_diff"] > 1e-3  # the case does stop early, and the extra trees would change the prediction
    assert row["diff"] < 1e-5 and row["contrib_sum"] < 1e-4         # prediction and contributions come from the best round's trees


def test_linear_models_train_on_the_validation_rows_they_never_stop_on(found):
    assert ok(found["linear_trains_on_the_validation_rows_too"])["diff"] < 1e-6


def test_qlib_portfolio_labels_cost_as_qlib_does_and_annualizes_by_the_calendar(found):
    row = ok(found["portfolio_report_with_and_without_cost"])
    assert abs(row["without"] - row["hand_without"]) < 1e-12 and abs(row["with"] - row["hand_with"]) < 1e-12 and row["with"] < row["without"]
    assert abs(row["ir_with"] - row["hand_ir_with"]) < 1e-9
    assert abs(row["main_last"] - row["hand_main_last"]) < 1e-12   # the main line is the excess after cost


def test_qlib_portfolio_benchmark_is_marked_at_the_close_like_the_portfolio(found):
    row = ok(found["portfolio_benchmark_is_close_to_close"])
    assert max(abs(a - b) for a, b in zip(row["bench"], row["hand"], strict=True)) < 1e-12
