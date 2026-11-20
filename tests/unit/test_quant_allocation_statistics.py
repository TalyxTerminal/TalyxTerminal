"""An allocation's whole-run numbers follow Quant's one definition of each: the expected numbers are
worked by hand on short series here, never read from the code under test."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "modules" / "quant"))
from native import allocation_results as R  # noqa: E402
from native import allocation_work as W  # noqa: E402

DAYS = pd.bdate_range("2020-01-01", periods=120)


def alternating(first=None):
    """+1%, -0.5%, ... for 120 days; `first` replaces the first day's return."""
    values = [0.01 if k % 2 == 0 else -0.005 for k in range(120)]
    if first is not None:
        values[0] = first
    return pd.Series(values, index=DAYS)


def test_sharpe_is_over_the_rate_and_annualized_by_the_calendar():
    daily = alternating()
    rate = 0.0001
    # mean 0.0025, each day 0.0075 from it: the sample deviation is 0.0075 * sqrt(120 / 119)
    expected = (0.0025 - rate) / (0.0075 * math.sqrt(120 / 119)) * math.sqrt(365)
    found = W.metrics(daily, annual=365, risk_free=rate)
    assert abs(found["sharpe"] - expected) < 1e-9
    assert abs(found["vol"] - 0.0075 * math.sqrt(120 / 119) * math.sqrt(365)) < 1e-12
    # a rate a day for each return counts the same as one rate
    assert abs(W.metrics(daily, annual=365, risk_free=pd.Series(rate, index=DAYS))["sharpe"] - expected) < 1e-9


def test_without_a_rate_the_sharpe_is_empty_not_over_zero():
    found = W.metrics(alternating(), annual=252, risk_free=None)
    assert found["sharpe"] is None and found["cagr"] is not None


def test_cagr_is_by_observations_a_year_not_calendar_days():
    daily = alternating()
    growth = (1.01 * 0.995) ** 60
    assert abs(W.metrics(daily, annual=252)["cagr"] - (growth ** (252 / 120) - 1)) < 1e-12
    assert abs(W.metrics(daily, annual=52)["cagr"] - (growth ** (52 / 120) - 1)) < 1e-12


def test_drawdown_counts_from_the_starting_capital():
    daily = alternating(first=-0.05)
    # the first day falls 5% from the capital; after it the path makes new highs only once it is back above 1
    path = np.cumprod(1 + daily.to_numpy())
    expected = min(-0.05, float((path / np.maximum.accumulate(np.concatenate([[1.0], path]))[1:] - 1).min()))
    assert abs(W.metrics(daily, annual=252)["maxDrawdown"] - expected) < 1e-12
    assert W.metrics(daily, annual=252)["maxDrawdown"] <= -0.05


def test_turnover_leaves_out_the_buy_from_cash():
    daily = pd.Series(0.0, index=pd.bdate_range("2020-01-01", periods=504))
    periods = [{"turnover": 1.0, "cost": 0.0005, "drifted": None}, {"turnover": 0.1, "cost": 0.0001, "drifted": pd.Series([0.5, 0.5])},
               {"turnover": 0.2, "cost": 0.0002, "drifted": pd.Series([0.5, 0.5])}, {"turnover": 0.1, "cost": 0.0001, "drifted": pd.Series([0.5, 0.5])}]
    found = W.metrics(daily, periods=periods, annual=252)
    assert abs(found["turnover"] - 0.4 / 2) < 1e-12           # two years: 0.1 + 0.2 + 0.1, the first period's 100% not counted
    assert abs(found["costDrag"] - 0.0009 / 2) < 1e-12        # every period's cost is paid, the buy-in's too


def test_gap_to_a_benchmark_is_the_gap_of_the_two_cagrs():
    daily, other = alternating(), pd.Series(0.001, index=DAYS)
    mine = ((1.01 * 0.995) ** 60) ** (252 / 120) - 1
    theirs = 1.001 ** 252 - 1
    assert abs(W.metrics(daily, equal_daily=other, annual=252)["vsEqualWeight"] - (mine - theirs)) < 1e-12


def test_the_rate_of_each_day_is_the_newest_t_bill_close_not_after_it():
    index = pd.DatetimeIndex(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"])
    stamp = lambda text: int(pd.Timestamp(text, tz="UTC").timestamp() * 1000)  # noqa: E731
    spec = {"basis": "irx", "rows": [[stamp("2023-12-29"), 5.2], [stamp("2024-01-04"), 5.0]]}
    rate = W.risk_free_per_bar(spec, index, 252)
    assert np.allclose(rate.to_numpy(), [0.052 / 252, 0.052 / 252, 0.050 / 252, 0.050 / 252])
    assert W.risk_free_per_bar({"basis": "zero"}, index, 252) == 0.0
    assert W.risk_free_per_bar({"basis": "irx", "rows": [], "unavailable": "Market has no ^IRX closes in this period"}, index, 252) is None


def test_value_at_risk_is_labelled_by_the_pools_period():
    daily = pd.Series(np.linspace(-0.05, 0.05, 101))
    weekly = {row["label"]: row["value"] for row in R.risk_measures(daily, None, 52)}
    # the 5% quantile of -0.05 .. 0.05 in steps of 0.001 is -0.045; the mean at or below it is -0.0475
    assert abs(weekly["Weekly VaR 95%"] - -0.045) < 1e-12 and abs(weekly["Weekly CVaR 95%"] - -0.0475) < 1e-12
    assert "Monthly VaR 95%" in {row["label"] for row in R.risk_measures(daily, None, 12)}
    assert "Daily VaR 95%" in {row["label"] for row in R.risk_measures(daily, None, 365)}


def test_rolling_sharpe_takes_a_year_of_the_pools_own_calendar():
    days = pd.bdate_range("2020-01-01", periods=400)
    daily = pd.Series(np.where(np.arange(400) % 2 == 0, 0.01, -0.005), index=days)
    figure = R.rolling([("Allocation", daily, "main")], 365, 0.0)
    trace = figure["data"][0]
    assert len(trace["y"]) == 400 - 365 + 1
    last = daily.iloc[-365:].to_numpy()
    assert abs(trace["y"][-1] - round(last.mean() / last.std(ddof=1) * math.sqrt(365), 2)) < 1e-9


@pytest.mark.parametrize(("target", "benchmark", "timely"), [
    (.5, None, (0, 1, 2)), (.5, None, None), (.6, {"SPY": .6, "IEF": .4}, None),
    (.8, None, (0, 2, 4)), (.3, {"SPY": .6, "IEF": .4}, (1, 3, 5)),
])
def test_published_allocation_excess_matches_the_two_native_books(target, benchmark, timely):
    from modules.quant.backend import library_after

    dates = pd.to_datetime(["2025-01-30", "2025-01-31", "2025-02-03", "2025-02-04", "2025-02-05",
                            "2025-02-28", "2025-03-03", "2025-03-04"])
    prices = pd.DataFrame({"SPY": [100, 100, 100, 200, 100, 140, 170, 150], "IEF": [100] * 8}, index=dates)
    settings = {"frequency": "monthly", "lookback": 1, "pool": [{"ticker": "SPY", "group": "stock"}],
                "costs": [{"group": "stock", "bps": 30}, {"group": "Other", "bps": 10}]}
    def native(weight):
        def fit(window, previous):
            w = pd.Series([weight, 1 - weight], index=window.columns)
            return {"weights": w, "riskShare": w, "status": "optimal", "note": "", "bound": {}, "seconds": 0}
        return W.backtest(prices.pct_change().iloc[1:], settings, fitter=fit)["daily"]

    mine, other = native(target), native(.5 if benchmark is None else .6)
    assert mine.index[0] == dates[2] and mine.iloc[0] == pytest.approx(-(target * .003 + (1-target) * .001))
    picked = list(range(len(mine))) if timely is None else list(timely)
    expected = (1 + mine.iloc[picked]).prod() - (1 + other.iloc[picked]).prod()
    labels = mine.index.as_unit("ms").asi8
    prices.index = dates.as_unit("ms").asi8
    values = pd.DataFrame([(day, name, w) for day in labels for name, w in (("SPY", target), ("IEF", 1-target))],
                          columns=["datetime", "instrument", "value"])
    figures, _ = library_after.allocation(values, prices, scheduled=labels[picked], published_through=labels[0]-1,
        published={"value": .1, "riskShares": {}}, annualization=252, shares={}, every="month", settings=settings,
        benchmark={"name": "mix", "key": "vs", "weights": benchmark})
    assert figures["benchmark"]["since"] == pytest.approx(expected, abs=1e-12)
