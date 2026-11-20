"""The Risk page's averages of predicted and realized volatility are read off the calibration chart's own two lines: different lines give
different averages, each the plain mean of its line."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "modules" / "quant"))
from native import allocation_results as R  # noqa: E402


def test_predicted_and_realized_averages_are_each_lines_own_mean():
    figure = {"data": [{"meta": {"key": "predicted"}, "y": [7.0, 8.0, None, 9.0]}, {"meta": {"key": "realized"}, "y": [5.0, 6.5, 7.0]}]}
    daily = pd.Series(np.random.default_rng(2).normal(0.0003, 0.008, 300))
    rows = {row["label"]: row["value"] for row in R.risk_measures(daily, None, 252, figure)}
    assert abs(rows["Predicted volatility, average"] - 0.08) < 1e-12 and abs(rows["Realized next month, average"] - 0.061666666666666665) < 1e-12
