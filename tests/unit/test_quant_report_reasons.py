"""a statistic the strategy report cannot compute is left out, not zero, and its row says why."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "modules" / "quant"))
from native import strategy_report as R  # noqa: E402


class Statistic:
    name = "Sharpe Ratio (252 days)"

    def __init__(self, value=None, error=None):
        self.value, self.error = value, error

    def calculate_from_returns(self, returns):
        if self.error is not None:
            raise self.error
        return self.value


def test_a_statistic_that_cannot_be_computed_says_why():
    assert R.stat_of(Statistic(error=ZeroDivisionError("the returns do not vary")), {1: 0.0}) == \
        {"value": None, "why": "ZeroDivisionError: the returns do not vary"}


def test_a_computed_statistic_has_only_its_value():
    assert R.stat_of(Statistic(value=1.25), {1: 0.01}) == {"value": 1.25}
