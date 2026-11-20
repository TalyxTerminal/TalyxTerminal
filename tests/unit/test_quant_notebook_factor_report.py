"""q.report.factor in a notebook measures a factor the way the object page's Work does (it was Alphalens's own way, with no delay,
quantile cuts, no preprocessing and a plain t, so the same factor read differently in the two places)."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

DAY = 86_400_000


@pytest.fixture
def q(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "modules" / "quant"))
    from native.publish import Publisher

    from modules.quant import talyx_quant
    monkeypatch.setattr(talyx_quant._Run, "invocation", None)
    monkeypatch.setattr(talyx_quant._Run, "mode", "run")
    monkeypatch.setattr(talyx_quant._Run, "data", None)
    monkeypatch.setattr(talyx_quant._Run, "publications", [])
    monkeypatch.setattr(talyx_quant._Run, "publisher", Publisher({"runId": "notebook", "output": str(tmp_path / "report")}))
    return talyx_quant


def research(names=40, days=300, seed=4):
    rng = np.random.default_rng(seed)
    labels = (np.arange(days) + 19_000) * DAY
    frames = []
    for number in range(names):
        close = 50 * np.exp(np.cumsum(rng.normal(.0002, .015, days)))
        volume = rng.uniform(1e5, 1e6, days)
        frames.append(pd.DataFrame({"label": labels, "column": f"S{number}", "open": close, "high": close * 1.01, "low": close * .99, "close": close,
                                    "volume": volume, "rawClose": close, "rawVolume": volume, "knownAt": labels}))
    bars = pd.concat(frames, ignore_index=True)
    info = {"timeframe": "1d", "annualization": 252, "instruments": [{"column": f"S{number}", "instrumentId": f"x:S{number}", "displaySymbol": f"S{number}"} for number in range(names)]}
    return bars, info


def next_bar_return(close, rng):
    """A factor that knows the next bar's return: with no delay its rank IC over one bar is near 1; traded a bar later, as Work trades it, near 0."""
    ahead = close.shift(-1) / close - 1
    return ahead + rng.normal(0, 1e-4, ahead.shape)


def test_the_notebook_report_gives_the_work_numbers_for_the_same_factor_and_settings(q):
    from native import factor_work as W
    bars, info = research()
    source = q.ResearchData(bars, info)
    factor = next_bar_return(source.close, np.random.default_rng(1))
    settings = {"holding": 1, "neutralize": "none"}
    view = q.report.factor(factor, source, settings=settings)
    numbers = view.index["metrics"]
    panel = W.Panel.from_bars(bars, info)
    work = W.Evaluation(panel, factor.reindex(index=panel.dates, columns=panel.columns).to_numpy(copy=True), W.settings_of(settings), formula=False, with_similar=False).numbers
    for key in ("rankIc", "t", "longShortNet", "turnover", "names", "groups", "holding"):
        assert numbers[key] == pytest.approx(work[key], nan_ok=True), key
    # Traded a bar after it is known (delay 1), knowing the next bar's return is worth nothing; Alphalens's delay 0 read it as an IC near 1.
    assert abs(numbers["rankIc"]) < 0.1 and numbers["groups"] == 5


def test_a_prices_table_has_no_volumes_so_settings_that_need_them_are_refused_not_changed(q):
    bars, info = research(names=30, days=200)
    close = q.ResearchData(bars, info).close
    factor = close.pct_change(5, fill_method=None)
    with pytest.raises(ValueError, match="no volumes"):
        q.report.factor(factor, close)
    view = q.report.factor(factor, close, settings={"leaveOut": {"halted": False}, "neutralize": "none"}, groups=3)
    assert view.index["metrics"]["groups"] == 3 and view.index["metrics"]["names"] == 30
