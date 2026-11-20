"""Prediction quality uses Qlib correlations and reports undefined samples honestly."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("qlib")
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[2] / "modules/quant"))
from native.model_quality import prediction_quality


def test_prediction_quality_uses_cross_sectional_correlation_and_sample_ir():
    index = pd.MultiIndex.from_product([pd.date_range("2024-01-01", periods=3), ["A", "B", "C"]], names=["datetime", "instrument"])
    pred = pd.DataFrame({"score": [1, 2, 3] * 3}, index=index)
    label = pd.DataFrame({"target": [1, 2, 3, 3, 2, 1, 1, 3, 2]}, index=index)
    daily, metrics = prediction_quality(pred, label)
    assert daily["IC"].tolist() == pytest.approx([1, -1, .5])
    assert metrics["IC"] == pytest.approx(1 / 6)
    assert metrics["ICIR"] == pytest.approx(daily.IC.mean() / daily.IC.std(ddof=1))
    assert metrics["Evaluated dates"] == 3
    assert metrics["Paired observations"] == 9


def test_single_asset_and_missing_targets_do_not_become_zero_skill():
    index = pd.MultiIndex.from_product([pd.date_range("2024-01-01", periods=2), ["A"]], names=["datetime", "instrument"])
    pred = pd.DataFrame({"score": [1, 2]}, index=index)
    label = pd.DataFrame({"target": [1, float("nan")]}, index=index)
    _, metrics = prediction_quality(pred, label)
    assert metrics["IC"] is None
    assert metrics["ICIR"] is None
    assert metrics["Evaluated dates"] == 0
    assert metrics["Prediction dates"] == 2
    assert metrics["Paired observations"] == 1
