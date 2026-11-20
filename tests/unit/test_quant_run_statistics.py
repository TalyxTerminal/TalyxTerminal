"""A run's statistics, one definition each, checked on short series worked out by hand.

The returns 10%, −10%, 10%, −8%, 5% from a capital of 100 (equity 100, 110, 99, 108.9, 100.188, 105.1974), A = 252, risk-free 0.1% a day:
excess returns 0.099, −0.101, 0.099, −0.081, 0.049, mean 0.013; deviations of the returns from their mean 0.014 are 0.086, −0.114, 0.086,
−0.094, 0.036, whose squares add to 0.03792, so the sample standard deviation is √(0.03792 / 4) = 0.0973653.
"""
import math

import numpy as np
import pytest

from modules.quant.native import metrics as M

RETURNS = [0.10, -0.10, 0.10, -0.08, 0.05]
EQUITY = [100.0, 110.0, 99.0, 108.9, 100.188, 105.1974]
A = 252
RF = 0.001
DEVIATION = math.sqrt(0.03792 / 4)


def test_returns_lead_with_the_starting_capital():
    assert M.returns_of(EQUITY) == pytest.approx(RETURNS)


def test_sharpe_is_mean_excess_over_its_sample_deviation():
    assert M.sharpe(RETURNS, A, RF) == pytest.approx(0.013 / DEVIATION * math.sqrt(252))  # 2.11953


def test_sortino_takes_the_shortfall_over_every_day():
    # shortfalls below the rate: −0.101 and −0.081, the other three days count as zero: √((0.010201 + 0.006561) / 5) = 0.0579000
    assert M.sortino(RETURNS, A, RF) == pytest.approx(0.013 / math.sqrt(0.016762 / 5) * math.sqrt(252))  # 3.56423
    assert M.sortino(RETURNS, A, RF) == pytest.approx(3.564229880, rel=1e-9)


def test_a_rate_that_is_unavailable_leaves_the_statistics_that_use_it_empty():
    assert M.sortino(RETURNS, A, None) is None
    stats = M.run_statistics(_long_equity(), 252, None, pnls=[1.0], trades=1)
    assert stats["sharpe"] is None and stats["sortino"] is None and stats["probabilisticSharpe"] is None
    assert stats["cagr"] is not None and stats["volatility"] is not None


def test_volatility_cagr_drawdown_calmar():
    assert M.volatility(RETURNS, A) == pytest.approx(DEVIATION * math.sqrt(252))  # 1.54563
    # growth 1.1 × 0.9 × 1.1 × 0.92 × 1.05 = 1.051974 over 5 returns
    assert M.cagr(RETURNS, A) == pytest.approx(1.051974 ** (252 / 5) - 1)
    # the high is 110 on the first day, the low 99 the next
    assert M.equity_drawdown(EQUITY) == pytest.approx(-0.1)
    # the capital itself is a high: a fall on the first day is a drawdown
    assert M.equity_drawdown([100.0, 95.0, 96.0]) == pytest.approx(-0.05)
    assert M.calmar(0.2, -0.1) == pytest.approx(2.0)
    assert M.calmar(0.2, 0.0) is None


def test_skew_and_kurtosis_use_central_moments_over_the_sample_deviation():
    centred = [0.086, -0.114, 0.086, -0.094, 0.036]
    found = M.moments(RETURNS)
    assert found["skew"] == pytest.approx(sum(x ** 3 for x in centred) / 5 / DEVIATION ** 3)  # −0.215241
    assert found["kurtosis"] == pytest.approx(sum(x ** 4 for x in centred) / 5 / DEVIATION ** 4)  # 0.796819, plain (not excess)
    assert found["perPeriod"] == pytest.approx(0.014 / DEVIATION)


def test_value_at_risk_is_historical():
    # sorted −0.10, −0.08, 0.05, 0.10, 0.10: the 5% quantile sits 0.2 of the way from −0.10 to −0.08
    var, cvar = M.value_at_risk(RETURNS)
    assert var == pytest.approx(-0.096)
    assert cvar == pytest.approx(-0.10)


def test_closed_trades_count_money_and_a_loss_is_below_zero():
    found = M.closed_trades([50.0, -20.0, 0.0, -10.0, -5.0, 30.0])
    assert found["profitFactor"] == pytest.approx(80 / 35)
    assert found["winRate"] == pytest.approx(2 / 6)
    assert found["longestLosing"] == 2  # the trade at zero ends the first run of losses
    assert found["averageWin"] == pytest.approx(40.0)
    assert found["averageLoss"] == pytest.approx(-35 / 3)
    assert M.closed_trades([10.0, 0.0])["profitFactor"] is None


def _long_equity(days=120, step=0.001):
    return [100.0 * (1 + step) ** k for k in range(days + 1)]


def test_run_statistics_turnover_and_costs_are_a_year_of_the_mean_equity():
    equity = _long_equity()
    mean_equity = sum(equity[1:]) / 120
    years = 120 / 240
    stats = M.run_statistics(equity, 240, 0.0, pnls=[5.0, -1.0], trades=3, traded=4_000.0, costs=12.0)
    assert stats["turnover"] == pytest.approx(4_000 / 2 / mean_equity / years)
    assert stats["costsAYear"] == pytest.approx(12 / mean_equity / years)
    assert stats["openAtEnd"] == 1 and stats["trades"] == 3
    assert stats["totalReturn"] == pytest.approx(1.001 ** 120 - 1)
    assert stats["cagr"] == pytest.approx(1.001 ** 240 - 1)


def test_run_statistics_benchmark_over_the_same_days():
    equity = _long_equity()
    benchmark = [0.0005] * 120
    stats = M.run_statistics(equity, 240, 0.0, pnls=[1.0], trades=1, benchmark=benchmark)
    assert stats["benchmarkCagr"] == pytest.approx(1.0005 ** 240 - 1)
    assert stats["overBenchmark"] == pytest.approx((1.001 ** 240 - 1) - (1.0005 ** 240 - 1))


def test_run_statistics_need_sixty_daily_returns_and_a_trade():
    rng = np.random.default_rng(3)
    equity = list(100 * np.cumprod(np.concatenate([[1.0], 1 + rng.normal(0.001, 0.01, 59)])))
    short = M.run_statistics(equity, 252, 0.0, pnls=[1.0], trades=1)
    assert short["days"] == 59
    assert all(short[key] is None for key in ("cagr", "volatility", "sharpe", "sortino", "calmar", "turnover", "costsAYear"))
    assert short["totalReturn"] is not None and short["maxDrawdown"] is not None
    equity.append(equity[-1] * 1.001)
    enough = M.run_statistics(equity, 252, 0.0, pnls=[1.0], trades=1)
    assert enough["sharpe"] == pytest.approx(M.sharpe(M.returns_of(equity), 252))
    idle = M.run_statistics(equity, 252, 0.0, pnls=[], trades=0)
    assert idle["sharpe"] is None and idle["cagr"] == enough["cagr"]


def test_drawdown_counts_capital_before_the_first_loss():
    assert M.max_drawdown([-.30, .10, .10]) == pytest.approx(-.30)
    from decimal import Decimal
    assert M.drawdown_path([Decimal(90)], Decimal(100))[0] == pytest.approx(-.10)
    assert M.equity_drawdown([100, 90, 90]) == pytest.approx(-.10)
