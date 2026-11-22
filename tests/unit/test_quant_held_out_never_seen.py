"""A run that is not a held-out validation never sees the held-out period: changing every price from the held-out start on leaves its numbers,
its checks and its last day exactly as they were (factor, allocation); the cut points of a publication's future-data check lie before it."""
import json

import numpy as np
import pandas as pd
import pytest

from tests.unit.quant_library_fakes import INFO

DAY = 86_400_000


def factor_bars(path, *, shock):
    """Twelve names over 300 days; with `shock`, every price from day 240 on is replaced by noise of another scale."""
    rng = np.random.default_rng(7)
    columns = [f"S{index:02d}" for index in range(12)]
    close = 100 * np.exp(np.cumsum(rng.normal(0, .02, (300, len(columns))), axis=0))
    if shock:
        close[240:] = 50 * np.exp(np.cumsum(np.random.default_rng(99).normal(0, .2, (60, len(columns))), axis=0))
    rows = [(day * DAY, column, close[day, index], close[day, index], close[day, index], close[day, index], 1000.0, True, close[day, index], 1000.0)
            for day in range(300) for index, column in enumerate(columns)]
    pd.DataFrame(rows, columns=["label", "column", "open", "high", "low", "close", "volume", "isMember", "rawClose", "rawVolume"]).to_parquet(path, index=False)
    return columns


@pytest.mark.parametrize("kind", ["run", "sweep"])
def test_a_factor_run_reads_nothing_from_the_held_out_start_on(tmp_path, monkeypatch, kind):
    from modules.quant.native import factor_resident as engine

    def five_day_reversal(text, panel):  # the formula engine needs Qlib, which lives in the research environment; the stand-in looks back only
        close = panel.fields["close"]
        out = np.full_like(close, np.nan)
        out[5:] = -(close[5:] / close[:-5] - 1)
        return out, {}
    monkeypatch.setattr(engine, "formula_values", five_day_reversal)
    held = 240 * DAY
    answers = []
    for shock in (False, True):
        folder = tmp_path / str(shock)
        folder.mkdir()
        columns = factor_bars(folder / "bars.parquet", shock=shock)
        info = {**{key: value for key, value in INFO.items() if key != "instruments"},
                "instruments": [{"column": column, "instrumentId": f"x:{column}", "displaySymbol": column, "sourceId": "fake"} for column in columns]}
        (folder / "out").mkdir()
        job = {"output": str(folder / "out"), "kind": kind, "settings": {"holding": 5, "delay": 1, "neutralize": "none"}, "formula": "-($close / Ref($close, 5) - 1)", "heldOutFrom": held,
               "data": {"key": f"held-{kind}-{shock}", "barsPath": str(folder / "bars.parquet"), "info": info, "extrasPath": None, "libraryPath": None}}
        if kind == "sweep":
            job["sweep"] = [{"name": "holding", "values": [3, 5]}]
        answer = engine.handle({"op": "run", "job": job})
        reply = answer[0] if isinstance(answer, tuple) else answer
        assert reply["ok"], reply
        work = json.loads((folder / "out" / "work.json").read_text(encoding="utf-8"))
        work.pop("seconds", None)
        work.pop("figures", None)
        answers.append(work)
    if kind == "run":
        assert answers[0]["dataTo"] < held
    assert answers[0] == answers[1], "a price from the held-out start on changed what an ordinary run reports"


class Seen(Exception):
    pass


@pytest.mark.parametrize("kind, sees_held_out", [("backtest", False), ("heldout", True)])
def test_an_allocation_backtest_fits_and_scores_only_returns_before_the_held_out_start(tmp_path, monkeypatch, kind, sees_held_out):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "modules" / "quant"))
    from native import allocation_run as A
    days = pd.bdate_range("2020-01-01", periods=700)
    rng = np.random.default_rng(5)
    rows = [{"label": int(day.value // 10**6), "column": name, "close": value} for name, scale in (("SPY", .01), ("IEF", .004))
            for day, value in zip(days, 100 * np.cumprod(1 + rng.normal(0, scale, len(days))), strict=True)]
    pd.DataFrame(rows).to_parquet(tmp_path / "bars.parquet")
    held = str(days[600].date())
    seen = {}

    def backtest(returns, settings, **kwargs):
        seen["last"] = returns.index[-1]
        raise Seen
    monkeypatch.setattr(A.W, "backtest", backtest)
    monkeypatch.setattr(A.W, "check_feasible", lambda settings, pool: None)
    monkeypatch.setattr(A.W, "check_settings", lambda settings, pool, benchmark=None: None)   # builds the skfolio estimator, which this environment has not
    job = {"output": str(tmp_path / "out"), "kind": kind, "heldOutFrom": held, "data": {"barsPath": str(tmp_path / "bars.parquet"), "info": {"annualization": 252}}, "riskFree": {"basis": "zero"},
           "settings": {"pool": [{"ticker": "SPY", "group": "Stocks"}, {"ticker": "IEF", "group": "Bonds"}], "lookback": 126, "frequency": "monthly", "benchmarkColumns": {}}}
    with pytest.raises(Seen):
        A.run(job)
    assert (seen["last"] >= pd.Timestamp(held)) is sees_held_out


def test_a_new_strategy_only_holds_out_an_explicit_range(tmp_path, monkeypatch):
    # a strategy had no held-out stretch unless one was set, so sweeps and walk-forward ran through the stretch later used to validate.
    # The rule factors, models and allocations use: about the last fifth, from the start of a month (2016-01-01..2025-12-31: 2023-12-01).
    import asyncio

    from tests.unit.test_quant_factor_runs import services
    from tests.unit.test_quant_object_runs import DATA

    async def scenario():
        _, shell, _ = services(tmp_path, monkeypatch, [])
        created = await shell.store.create({"kind": "strategy", "name": "Trend"})
        found = await shell.store.update_settings(created["id"], {"data": {**DATA, "since": "2016-01-01", "until": "2025-12-31"}})
        assert "heldOutFrom" not in found["settings"]["data"]
        await shell.store.update_settings(created["id"], {"run":{"heldOutFrom":"2023-12-01"}})
        # once set it stays as it is when the data changes, and so does a cleared one
        found = await shell.store.update_settings(created["id"], {"data": {**DATA, "since": "2017-01-01", "until": "2025-12-31"}})
        assert found["settings"]["data"]["heldOutFrom"] == "2023-12-01"
        # a range that leaves it out gets the default of the new range
        found = await shell.store.update_settings(created["id"], {"data": {**DATA, "since": "2016-01-01", "until": "2020-12-31"}})
        assert "heldOutFrom" not in found["settings"]["data"]
        await shell.store.update_settings(created["id"], {"run": {"heldOutFrom": None}})
        found = await shell.store.update_settings(created["id"], {"data": {**DATA, "since": "2016-01-01", "until": "2025-12-31"}})
        assert found["settings"]["data"]["heldOutFrom"] is None
    asyncio.run(scenario())
