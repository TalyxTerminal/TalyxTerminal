"""Crisis periods: a run is measured in the stress periods its span covers, and only those are shown (recent years included)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[2] / "modules/quant"))
from native import allocation_results  # noqa: E402
from native.strategy_report import CRISES, Report  # noqa: E402


def covered_rows(crypto, first, last):
    index = pd.date_range(first, last, freq="D", tz="UTC")
    returns = pd.Series(np.random.default_rng(1).normal(0, 0.01, len(index)), index=index)
    report = Report.__new__(Report)
    report.crypto = crypto
    return report.crisis((1 + returns).cumprod(), returns, returns)["periods"]


def test_a_run_lists_exactly_the_periods_inside_its_span():
    for crypto, kind in ((True, "crypto"), (False, "stocks")):
        for first, last in (("2024-11-07", "2026-11-07"), ("2019-01-01", "2026-11-07"), ("2025-05-01", "2025-09-30")):
            rows = covered_rows(crypto, first, last)
            inside = {name for name, start, end in CRISES[kind] if pd.Timestamp(end) >= pd.Timestamp(first) and pd.Timestamp(start) <= pd.Timestamp(last)}
            assert {row["name"] for row in rows if row["covered"]} == inside
            assert all(row["return"] is not None for row in rows if row["covered"])


def test_the_lists_reach_recent_years():
    for periods in (*CRISES.values(), allocation_results.CRISES):
        assert max(pd.Timestamp(end) for _name, _start, end in periods) >= pd.Timestamp("2025-04-01")
    # A two-year crypto backtest ending now meets at least one period.
    assert any(row["covered"] for row in covered_rows(True, "2024-11-07", "2026-11-07"))


def test_each_period_runs_forward_from_a_high_to_a_low():
    for periods in (*CRISES.values(), allocation_results.CRISES):
        for _name, start, end in periods:
            assert pd.Timestamp(start) < pd.Timestamp(end)
