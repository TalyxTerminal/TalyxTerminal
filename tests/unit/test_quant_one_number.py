"""the same returns give the same number at every exit. A run's stored statistics (native report), a sweep's or walk-forward's
window over the whole run (Host), and the run list and home (stored numbers) agree; before, each computed its own (calendar days or
bars, with or without the rate, intraday or daily drawdown) and the checkup found them apart."""
import numpy as np
import pytest

from modules.quant.backend.object_history import metrics_of
from modules.quant.backend.object_sweeps import window_stats
from modules.quant.native import metrics as M

DAY = 86_400_000


def run_series(days=300, seed=11):
    rng = np.random.default_rng(seed)
    equity = list(100_000 * np.cumprod(np.concatenate([[1.0], 1 + rng.normal(.0006, .011, days)])))
    rates = list(rng.uniform(.00008, .00018, days))  # a day's T-bill rate, changing
    points = [[at * DAY, value] for at, value in enumerate(equity)]
    return equity, rates, points


def test_a_window_over_the_whole_run_gives_the_runs_own_numbers():
    equity, rates, points = run_series()
    stored = M.run_statistics(equity, 252, rates, pnls=[5.0, -2.0, 3.0], trades=3)
    window = window_stats(points, 0, 10 ** 18, 252, [[at * DAY, rate] for at, rate in enumerate(rates, start=1)])
    for key, other in (("sharpe", "sharpe"), ("cagr", "cagr"), ("maxDrawdown", "maxDrawdown"), ("calmar", "calmar"), ("totalReturn", "total")):
        assert window[other] == pytest.approx(stored[key], rel=1e-12), key


def test_the_list_and_home_show_the_stored_numbers():
    equity, rates, _ = run_series()
    stored = M.run_statistics(equity, 252, rates, pnls=[5.0, -2.0, 3.0], trades=3)
    shown = metrics_of({"metrics": {}, "units": [], "strategyReport": {"stats": stored}})
    assert shown == {key: value for key, value in stored.items() if value is not None}


def test_a_signals_years_are_one_set_of_numbers_and_the_every_year_check_shows_each_years_ic_and_net():
    """A factor's (and a model's) by-year rows come from metrics.signal_years: the year's mean rank IC and its long–short net a year (the mean net a bar
    times the bars in a year); "Every year" counts the whole years whose IC has the whole period's sign and carries each one's IC and net."""
    import pandas as pd

    from modules.quant.native import factor_work as W

    dates = pd.bdate_range("2019-03-01", "2021-12-31")
    rng = np.random.default_rng(3)
    ic = rng.normal(.02, .05, len(dates))
    ic[dates.year == 2020] -= .1   # 2020 turns against the whole period
    net = rng.normal(.0002, .004, len(dates))
    years = M.signal_years(dates, ic, net, 252, 5)
    assert [(row["year"], row["whole"]) for row in years] == [(2019, False), (2020, True), (2021, True)]
    in2021 = dates.year == 2021
    assert years[2]["rankIc"] == pytest.approx(ic[in2021].mean())
    assert years[2]["longShortNet"] == pytest.approx(net[in2021].mean() * 252)
    assert years[2]["t"] == pytest.approx(M.newey_west_t(ic[in2021], 4))

    numbers = {"t": 3.2, "mostLikeLibrary": None, "groupSize": 40, "rankIc": float(ic.mean()), "breakEven": 30.0}
    details = {"years": years, "groupMeans": [0.1, 0.2], "coverage": {"drop": 0.0, "day": None}, "flags": []}
    check = next(item for item in W.checks_of(numbers, details, {"holding": 5, "costBps": 5}, formula="x") if item["id"] == "every_year")
    assert check["value"] == "1 of 2" and check["state"] == "watch"
    assert check["years"] == [{"year": 2020, "rankIc": years[1]["rankIc"], "longShortNet": years[1]["longShortNet"]},
                              {"year": 2021, "rankIc": years[2]["rankIc"], "longShortNet": years[2]["longShortNet"]}]
