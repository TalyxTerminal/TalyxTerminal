"""A library item is published with the figures it is followed on afterwards: one function, two stretches of time. The expected numbers are worked by hand here, never read from the code under test."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from modules.quant.backend import library_after, library_figures, library_summary

DAY = 86_400_000


def test_an_allocation_after_publication_earns_on_drifted_weights_and_its_volatility_is_the_runs_definition():
    # Two assets, 50/50 set on the first bar and never rebalanced again (one month): the holding drifts with the returns, as the backtest's does.
    labels = [DAY * (n + 1) for n in range(30)]
    a = np.where(np.arange(30) % 2 == 0, 0.02, -0.01)
    b = np.where(np.arange(30) % 3 == 0, -0.015, 0.005)
    prices = pd.DataFrame({"A": 100 * np.cumprod(np.concatenate([[1.0], 1 + a[1:]])), "B": 50 * np.cumprod(np.concatenate([[1.0], 1 + b[1:]]))}, index=labels)
    values = pd.DataFrame({"datetime": [labels[0], labels[0]], "instrument": ["A", "B"], "value": [0.5, 0.5]})
    held, path = np.array([0.5, 0.5]), []
    for k in range(1, 30):
        growth = float(held @ np.array([a[k], b[k]]))
        path.append(growth)
        held = held * (1 + np.array([a[k], b[k]])) / (1 + growth)
    expected = float(np.std([0.0, *path], ddof=1) * math.sqrt(252))
    figures, _ = library_after.allocation(values, prices, scheduled=prices.index, published_through=0, published={"value": 0.1, "riskShares": {"A": 0.5, "B": 0.5}}, annualization=252,
                                          shares={"A": 0.5, "B": 0.5}, every="month")
    assert abs(figures["value"] - expected) < 1e-12
    # without drift (weights pulled back to 50/50 each day) the number differs: the test tells the two apart
    flat = float(np.std(0.5 * a[1:] + 0.5 * b[1:], ddof=1) * math.sqrt(252))
    assert abs(flat - expected) > 1e-6


def test_an_allocations_sharpe_since_publication_is_over_the_rate_and_empty_without_one():
    labels = [DAY * (n + 1) for n in range(40)]
    a = np.where(np.arange(40) % 2 == 0, 0.01, -0.004)
    prices = pd.DataFrame({"A": 100 * np.cumprod(np.concatenate([[1.0], 1 + a[1:]]))}, index=labels)
    values = pd.DataFrame({"datetime": [labels[0]], "instrument": ["A"], "value": [1.0]})
    common = {"scheduled": labels[10:], "published_through": labels[9], "published": {"value": 0.1, "riskShares": {"A": 1.0}}, "annualization": 252, "shares": {"A": 1.0}, "every": "month"}
    since = a[10:]
    rate = 0.0001
    expected = (since.mean() - rate) / since.std(ddof=1) * math.sqrt(252)
    figures, _ = library_after.allocation(values, prices, risk_free=rate, **common)
    assert abs(figures["sharpe"] - expected) < 1e-9
    assert library_after.allocation(values, prices, risk_free=None, **common)[0]["sharpe"] is None


def test_a_signal_is_scored_on_the_horizon_it_was_run_with():
    # 4 names, holding 2, delay 1: the value at bar t is ranked against close[t + 3] / close[t + 1] - 1.
    labels = [DAY * (n + 1) for n in range(8)]
    closes = pd.DataFrame({"A": [10, 10, 11, 12, 13, 14, 15, 16], "B": [10, 10, 10, 9, 8, 8, 8, 8], "C": [10, 10, 10, 10, 10, 10, 10, 10], "D": [10, 10, 12, 15, 15, 15, 15, 15]},
                          index=labels, dtype=float)
    values = pd.DataFrame([(labels[0], name, value) for name, value in zip("ABCD", [4.0, 1.0, 2.0, 3.0], strict=True)], columns=["datetime", "instrument", "value"])
    # forward return from bar 1 to bar 3: A 12/10, B 9/10, C 10/10, D 15/10 -> ranks A 3, B 1, C 2, D 4 against values' ranks A 4, B 1, C 2, D 3
    x, y = np.array([4, 1, 2, 3], float), np.array([3, 1, 2, 4], float)
    expected = float(((x - x.mean()) * (y - y.mean())).sum() / math.sqrt(((x - x.mean()) ** 2).sum() * ((y - y.mean()) ** 2).sum()))
    published = library_after.published(values, closes, holding=2, delay=1, annualization=252)
    assert abs(published["value"] - expected) < 1e-12 and published["holding"] == 2 and published["delay"] == 1


def test_standard_error_is_given_with_t():
    labels = [DAY * (n + 1) for n in range(40)]
    rng = np.random.default_rng(3)
    closes = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.02, (40, 6)), axis=0)), index=labels, columns=list("ABCDEF"))
    values = pd.DataFrame([(label, name, float(rng.normal())) for label in labels for name in "ABCDEF"], columns=["datetime", "instrument", "value"])
    published = library_after.published(values, closes, holding=1, delay=1, annualization=252)
    assert published["t"] is not None and abs(published["se"] - published["value"] / published["t"]) < 1e-15


def test_figures_after_publication_collect_until_sixty_trading_days_whatever_the_bar():
    hourly = {"kind": "signal", "settled": 59 * 24, "settledDays": 59}
    assert library_summary.collecting(hourly) and not library_summary.collecting({**hourly, "settled": 60 * 24, "settledDays": 60})
    assert not library_summary.collecting({"kind": "allocation", "settled": 3})


def test_settled_days_are_the_days_with_a_settled_period():
    hour = 3_600_000
    labels = [n * hour for n in range(24 * 4)]   # four days of hourly bars
    rng = np.random.default_rng(5)
    closes = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.01, (len(labels), 5)), axis=0)), index=labels, columns=list("ABCDE"))
    values = pd.DataFrame([(label, name, float(rng.normal())) for label in labels for name in "ABCDE"], columns=["datetime", "instrument", "value"])
    figures, _ = library_after.measure(values, closes, scheduled=closes.index, holding=1, delay=1, annualization=24 * 365)
    assert figures["settled"] == 24 * 4 - 2 and figures["settledDays"] == 4


def test_the_long_short_record_compounds_and_the_ic_record_sums():
    points = [{"date": DAY, "ic": 0.1, "ls": 0.1, "settled": True, "after": False}, {"date": 2 * DAY, "ic": 0.1, "ls": 0.1, "settled": True, "after": True},
              {"date": 3 * DAY, "ic": 0.1, "ls": 0.1, "settled": True, "after": True}]
    version = {"timeframe": "1d", "published": {"value": 0.05, "ls": 0.252}, "publishedAt": DAY, "info": {"annualization": 252}, "version": 1}
    ls = library_figures.cumulative(version, points, "ls", "all")
    settled = next(trace for trace in ls["data"] if trace["name"] == "settled")
    assert abs(settled["y"][-1] - (1.1 ** 3 - 1)) < 1e-12
    ic = library_figures.cumulative(version, points, "ic", "all")
    assert abs(next(trace for trace in ic["data"] if trace["name"] == "settled")["y"][-1] - 0.3) < 1e-12
