"""Model rules that need no research environment (checkup s4): the rank scale of the processing and of a ranked label, and the long–short earning the same
return the IC is measured against. Expected numbers are by hand."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "modules" / "quant"))
from native import model_data as D  # noqa: E402
from native import model_report as R  # noqa: E402

ROW = np.array([[0.1, 0.3, 0.2, np.nan, 0.5]])


def test_the_rank_processing_is_qlibs_csranknorm():
    """Qlib's CSRankNorm: the day's percentile rank, less one half, times 3.46."""
    found = D._rank(ROW)
    assert np.allclose(found[0, [0, 1, 2, 4]], [(0.25 - .5) * 3.46, (0.75 - .5) * 3.46, 0.0, (1.0 - .5) * 3.46]) and np.isnan(found[0, 3])


def test_a_ranked_label_is_the_formulas_csrank():
    """CSRank(...) in a label is the formula's CSRank, the pandas percentile rank of each day (0.25, 0.75, 0.5, -, 1 here), bit for bit."""
    settings = {"extremes": "none", "scale": "none", "missing": "drop"}
    found = D.process_label(ROW, settings, "rank")
    assert np.allclose(found[0, [0, 1, 2, 4]], [0.25, 0.75, 0.5, 1.0]) and np.isnan(found[0, 3])
    both = D.process_label(ROW, {**settings, "scale": "rank"}, "rank")   # ranked, then the rank processing: the same order on Qlib's scale
    assert np.allclose(both[0, [0, 1, 2, 4]], D._rank(ROW)[0, [0, 1, 2, 4]])


class Panel:
    """Ten names, the opens given; the closes move differently so a close-to-close long–short would not match."""

    def __init__(self, opens, closes):
        self.fields = {"open": opens, "close": closes}
        self.annualization = 252
        self.dates = np.arange(len(opens))
        self.columns = [f"S{i}" for i in range(opens.shape[1])]

    def derived(self, name):
        assert name == "returns"
        close = self.fields["close"]
        return np.vstack([np.full((1, close.shape[1]), np.nan), close[1:] / close[:-1] - 1])


def test_the_long_short_earns_the_return_the_ic_is_measured_on():
    """The label of day t is open[t+1+h] / open[t+1] - 1 (bought at the next open); the long–short of the signal of day t, held h = 1 bar, earns at bar t+2 the
    top group's mean label less the bottom group's."""
    rng = np.random.default_rng(4)
    opens = 50 * np.cumprod(1 + rng.normal(0, .02, (40, 10)), axis=0)
    closes = 50 * np.cumprod(1 + rng.normal(0, .02, (40, 10)), axis=0)
    pred = rng.normal(0, 1, (40, 10))
    pred[-3:] = np.nan
    panel = Panel(opens, closes)
    groups, returns = R.ls_of(panel, pred, 1, 0.0, 0.0)
    label = np.full_like(opens, np.nan)
    label[:-2] = opens[2:] / opens[1:-1] - 1
    for t in range(5, 30):
        top, bottom = groups[t] == R.GROUPS, groups[t] == 1
        assert abs(returns["gross"][t + 2] - (label[t, top].mean() - label[t, bottom].mean())) < 1e-12, t


class Check:
    """The parts of a finished report the checks read: the windows' best rounds and the two baselines."""

    def __init__(self, bests, *, best_ic=0.01, equal=0.02):
        self.windows = [{"best": best} for best in bests]
        self.per_window, self.days, self.cost = True, 5, 5.0
        self.base = {"bestInput": {"ic": best_ic}, "equalWeight": {"mean": equal}}


def checks_of(bests, **kwargs):
    numbers = {"t": 2.5, "ric": 0.03, "windows": len(bests), "positive": len(bests), "trainIc": 0.1, "like": {"name": "ROC5", "rho": 0.3},
               "breakeven": 20.0, "breakevenNote": None}
    return {row["id"]: row for row in R.Report.checks(Check(bests, **kwargs), numbers)}


def test_a_batch_whose_windows_stop_in_the_first_rounds_does_not_pass_learning():
    """Best by round 3 in half the windows or more: the model is one or two trees; fewer than half passes; no best round (linear) has no row."""
    assert checks_of([1, 2, 3, 40])["learned"]["state"] == "watch"
    assert checks_of([1, 40, 60, 80])["learned"]["state"] == "ok"
    assert checks_of([1, 2, 3, 3])["learned"]["value"] == "4 of 4 windows best by round 3"
    assert "learned" not in checks_of([None, None])


def test_beating_the_baselines_names_the_stronger_one():
    assert checks_of([40], best_ic=0.01, equal=0.02)["baselines"]["value"].endswith("(inputs averaged)")
    assert checks_of([40], best_ic=0.025, equal=0.02)["baselines"]["value"].endswith("(strongest input)")


class NoIndustry:
    """A pool Market gives no industry and no share counts for."""

    def __init__(self, bars=6, names=4):
        self.dates = np.arange(bars)
        self.columns = [f"S{i}" for i in range(names)]
        self.shape = (bars, names)

    def industry_series(self):
        return None

    def derived(self, name):
        assert name == "size"
        return np.full(self.shape, np.nan)


def test_taking_out_what_the_pool_does_not_have_is_refused_by_name():
    """Without industries or market values the residual would be the raw input or all zeros."""
    import pytest
    x = np.random.default_rng(1).normal(size=(6, 4, 2))
    for what, title in ((["industry"], "cannot take out industry"), (["size"], "cannot take out size"), (["industry", "size"], "cannot take out industry")):
        with pytest.raises(D.DataError) as found:
            D._take_out(x, NoIndustry(), what)
        assert found.value.failure["title"] == title


def test_low_coverage_says_where_the_values_are_missing():
    """A stretch of a week or more with values for under half the names is named by its dates; otherwise the share of names a day."""
    from native import model_check as C
    labels = [1_704_067_200_000 + day * 86_400_000 for day in range(30)]   # 2024-01-01 on
    daily = np.ones(30)
    daily[3:13] = 0.2
    daily[20] = 0.0   # one day alone is not a stretch
    assert C._missing_part(daily, labels) == "Missing for most names Jan 4, 2024 – Jan 13, 2024"
    assert C._missing_part(np.full(30, 0.6), labels) == "Values for 60% of the names on an average day"


def test_a_split_with_no_window_says_what_start_or_training_gives_one():
    """Two years of daily bars cannot hold 3 years of training: the text gives a start that does and the longest training that fits."""
    import pandas as pd
    from native import model_check as C
    labels = [int(stamp.value // 1_000_000) for stamp in pd.bdate_range("2023-01-02", "2024-12-31", tz="UTC")]
    split = {"trainMonths": 36, "validMonths": 6, "everyMonths": 3, "mode": "rolling", "firstTest": ""}
    assert not D.layout(labels, split, 6, warmup=60)
    text = C._room_for_windows(labels, split, 6, 60)
    assert "or train 1 year." in text
    after = text.split("Start the data by ")[1].split(", ")
    start = pd.Timestamp(f"{after[0]}, {after[1][:4]}")   # "Mar 6, 2021"

    def from_(day):
        return [int(stamp.value // 1_000_000) for stamp in pd.bdate_range(day, "2024-12-31").tz_localize("UTC")]
    assert D.layout(from_(start), split, 6, warmup=60)                               # starting there gives a window
    assert not D.layout(from_(start + pd.DateOffset(months=1)), split, 6, warmup=60)  # and a month later does not: the date is not padded
    assert D.layout(labels, {**split, "trainMonths": 12}, 6, warmup=60)


def test_built_in_columns_keep_those_columns_and_name_the_ones_no_set_has():
    """Qlib's FilterCol as one setting: "Alpha158.KMID, ROC5" keeps two columns; a name no chosen set has is a data error naming it."""
    import pytest
    assert D.kept_columns("Alpha158.KMID, ROC5; ") == ["KMID", "ROC5"] and D.kept_columns("") == []
    names = ["KMID", "ROC5", "STD20"]
    D.qlib_set, real = (lambda name: ([f"$x{index}" for index in range(len(names))], names)), D.qlib_set
    try:
        D._check_kept([D.Input(id="i1", name="Alpha158", source="builtin", ref="alpha158", only=["KMID", "momentum"]),
                       D.Input(id="i2", name="Styles", source="builtin", ref="styles", only=["KMID", "momentum"])])
        with pytest.raises(D.DataError) as found:
            D._check_kept([D.Input(id="i1", name="Alpha158", source="builtin", ref="alpha158", only=["KMID", "ROC500"])])
        assert found.value.failure["message"] == "Built-in columns: ROC500 is not a column of Alpha158."
    finally:
        D.qlib_set = real


def test_a_years_sharpe_is_the_spreads_own_whatever_the_runs_rate():
    """Checkup and design.md: a long-short book pays for itself, so its Sharpe takes no rate off, a year's as the whole run's. By hand on +1%, -0.5%, ...
    (a model has no rate setting at all)."""
    from types import SimpleNamespace

    import pandas as pd
    dates = pd.bdate_range("2023-01-02", periods=40)
    net = np.where(np.arange(40) % 2 == 0, 0.01, -0.005)
    expected = net.mean() / net.std(ddof=1) * np.sqrt(252)
    report = object.__new__(R.Report)
    report.panel = SimpleNamespace(annualization=252, labels=np.arange(40), dates=dates)
    report.returns, report.A = {"net": net}, 252
    assert abs(report._year_sharpe(dates.year == 2023) - expected) < 1e-9


def panel(days, names):
    import pandas as pd
    from native import factor_work as W
    rng = np.random.default_rng(1)
    labels = pd.bdate_range("2020-01-01", periods=days).tz_localize("UTC").as_unit("ms").asi8
    close = np.exp(np.cumsum(rng.normal(0, .02, (days, names)), axis=0)) * 50
    fields = {"open": close.copy(), "high": close * 1.01, "low": close * .99, "close": close, "volume": rng.uniform(1e5, 1e6, (days, names))}
    fields.update(rawClose=fields["close"], rawVolume=fields["volume"])
    return W.Panel(labels, [f"S{i}" for i in range(names)], fields, np.ones((days, names), dtype=bool), {"annualization": 252, "timeframe": "1d"},
                   industry=[f"I{i % 4}" for i in range(names)])


def test_a_name_halted_past_five_bars_carries_the_move_over_the_halt_into_its_first_open_back():
    """on the model side: the label and the long–short read opens carried over a halt until the name trades again, as the factor's returns now
    do, so a held name earns the move over a long halt on its first bar back; after its last price (delisted) it has no return."""
    p = panel(40, 5)
    opens = p.fields["open"]
    opens[:, 3] = 10.0
    opens[1, 3], opens[2:9, 3], opens[9:, 3] = 11.0, np.nan, 12.1  # halted bars 2-8 (seven), back at 12.1 on bar 9
    opens[30:, 4] = np.nan  # name 4's last open is bar 29
    label = D.label_of(p, 1)  # bar t: open t+2 over open t+1
    assert label[7, 3] == pytest.approx(12.1 / 11 - 1)  # open 8 is the price held over the halt, 11; open 9 is 12.1
    assert label[1, 3] == pytest.approx(0.0)  # within the halt the held price does not move
    assert np.isnan(label[28:, 4]).all() and not np.isnan(label[27, 4])
    returns = R.OpenToOpen(p).derived("returns")
    assert returns[9, 3] == pytest.approx(12.1 / 11 - 1) and returns[5, 3] == pytest.approx(0.0)
    assert np.isnan(returns[30:, 4]).all()
