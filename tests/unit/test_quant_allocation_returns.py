"""The pool's daily returns have one definition: the backtest worker and `q.allocate` (the frozen run a publication cuts and runs again) read the same
returns, also when closes are missing, so a cut-point re-run is bit-identical to the original backtest."""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "modules" / "quant"))

from native import allocation_run as A  # noqa: E402

DAY = 86_400_000


def bars_with_gaps():
    days = pd.bdate_range("2020-01-01", periods=60)
    rng = np.random.default_rng(3)
    columns = {"SPY": 100 * np.cumprod(1 + rng.normal(0, .01, 60)), "IEF": 100 * np.cumprod(1 + rng.normal(0, .004, 60)),
               "BIL": 100 * np.cumprod(1 + rng.normal(0, .001, 60))}
    frame = pd.DataFrame(columns, index=days)
    frame.loc[days[20], "SPY"] = np.nan   # a pool asset misses a close
    frame.loc[days[30], "BIL"] = np.nan   # a column outside the pool misses another
    frame.loc[days[:5], "IEF"] = np.nan   # one starts late
    rows = [{"label": int(day.value // 10**6), "column": name, "close": value} for name in frame for day, value in frame[name].items() if not np.isnan(value)]
    return pd.DataFrame(rows)


def test_returns_cross_gaps_of_the_pool_only():
    returns, info = A.daily_returns(bars_with_gaps(), ["SPY", "IEF"])
    assert list(returns.columns) == ["SPY", "IEF"]
    assert info["missing"] == {"SPY": 1}
    days = pd.bdate_range("2020-01-01", periods=60)
    assert days[30] in returns.index and days[20] not in returns.index
    closes = info["closes"]
    assert np.allclose(returns.iloc[10].to_numpy(), (closes.iloc[11] / closes.iloc[10] - 1).to_numpy())


def test_allocate_reads_the_same_returns_as_the_worker(monkeypatch, tmp_path):
    import talyx_quant as q
    bars = bars_with_gaps()
    path = tmp_path / "bars.parquet"
    bars.to_parquet(path)
    settings = {"pool": [{"ticker": "SPY", "group": "Stocks"}, {"ticker": "IEF", "group": "Bonds"}]}
    worker, _ = A.returns_of({"data": {"barsPath": str(path)}, "settings": settings})
    seen = {}
    monkeypatch.setattr(A.W, "backtest", lambda returns, settings, **kw: seen.update(returns=returns) or {"periods": []})
    monkeypatch.setattr(q, "data", lambda *a, **k: SimpleNamespace(bars=bars, annualization=252, info={}))
    monkeypatch.setattr(A.W, "check_settings", lambda *a: None)
    monkeypatch.setattr(q._Run, "mode", "run")
    monkeypatch.setattr(q._Run, "cutoff", None)
    A.ACTIVE.clear()
    A.allocate(settings)
    pd.testing.assert_frame_equal(seen["returns"], worker)
    assert seen["returns"].to_numpy().tobytes() == worker.to_numpy().tobytes()


def test_default_shocks_leave_out_a_target_the_pool_does_not_have():
    from native import allocation_results as R
    text = (Path(A.__file__).parent / "allocation_standard.py").read_text(encoding="utf-8")
    assert '{"target": "Stocks", "pct": -20.0}' in text and '{"target": "Bonds", "pct": -10.0}' in text and '{"target": "BTC", "pct": -50.0}' in text
    rng = np.random.default_rng(1)
    returns = pd.DataFrame(rng.normal(0, .01, (300, 2)), index=pd.bdate_range("2021-01-01", periods=300), columns=["SPY", "IEF"])
    rows = R.scenarios(pd.Series({"SPY": .6, "IEF": .4}), returns, {"groups": {"SPY": "Stocks", "IEF": "Bonds"}, "rows": [{"target": "Stocks", "pct": -20.0}, {"target": "Bonds", "pct": -10.0}, {"target": "BTC", "pct": -50.0}]}, np.cov(returns.to_numpy(), rowvar=False))
    shocks = [row["scenario"] for row in rows if row["kind"] == "Shock"]
    assert shocks == ["Stocks \u221220%", "Bonds \u221210%"]


def closes_frame(columns, days):
    rows = [{"label": int(day.value // 10**6), "column": name, "close": value} for name, series in columns.items() for day, value in zip(days, series) if not np.isnan(value)]
    return pd.DataFrame(rows)


def test_proxy_spliced_before_start():
    days = pd.bdate_range("2019-01-01", periods=800)
    rng = np.random.default_rng(5)
    proxy = 50 * np.cumprod(1 + rng.normal(0.0002, .004, 800))
    own = proxy * 2.0 * (1 + rng.normal(0, .0005, 800)).cumprod() ** 0   # same moves, another level
    own = own.copy()
    own[:300] = np.nan                       # lists on day 300
    other = 100 * np.cumprod(1 + rng.normal(0, .01, 800))
    bars = closes_frame({"IEF": own, "VFITX": proxy, "SPY": other}, days)
    plain, _ = A.daily_returns(bars, ["IEF", "SPY"])
    joined, info = A.daily_returns(bars, ["IEF", "SPY"], {"proxyColumns": {"IEF": "VFITX"}})
    assert plain.index[0] == days[301] and joined.index[0] == days[1]
    assert info["spliced"]["IEF"]["to"] == days[300] and info["spliced"]["IEF"]["correlation"] > .99
    assert info["cutShort"] == []
    assert np.isclose(joined["IEF"].iloc[5], proxy[6] / proxy[5] - 1)           # before the listing: the proxy's own return
    assert np.isclose(joined["IEF"].loc[days[400]], own[400] / own[399] - 1)    # after it: the asset's own
    assert A.spliced_text(info) == ["IEF before Jan 2020 from VFITX"] or A.spliced_text(info)[0].startswith("IEF before")


def test_proxy_low_correlation_refused():
    days = pd.bdate_range("2019-01-01", periods=800)
    rng = np.random.default_rng(6)
    own = 100 * np.cumprod(1 + rng.normal(0, .01, 800))
    own[:300] = np.nan
    proxy = 50 * np.cumprod(1 + rng.normal(0, .01, 800))
    bars = closes_frame({"IEF": own, "VFITX": proxy}, days)
    try:
        A.daily_returns(bars, ["IEF"], {"proxyColumns": {"IEF": "VFITX"}})
    except ValueError as error:
        assert "VFITX and IEF correlate" in str(error) and "pick another proxy" in str(error)
    else:
        raise AssertionError("a proxy that does not move with the asset must be refused")


def test_weekly_and_monthly_align():
    days = pd.bdate_range("2020-01-01", periods=520)
    rng = np.random.default_rng(7)
    bars = closes_frame({"A": 100 * np.cumprod(1 + rng.normal(0, .01, 520)), "B": 100 * np.cumprod(1 + rng.normal(0, .01, 520))}, days)
    weekly, info_w = A.daily_returns(bars, ["A", "B"], {"align": "weekly"})
    monthly, info_m = A.daily_returns(bars, ["A", "B"], {"align": "monthly"})
    assert info_w["annualization"] == 52 and info_m["annualization"] == 12
    assert all(day.dayofweek == 4 for day in weekly.index) and 95 < len(weekly) < 110
    assert 22 < len(monthly) < 26 and all((day + pd.offsets.MonthEnd(0)).month == day.month for day in monthly.index)
    first_two = bars[bars["column"] == "A"].set_index("label")["close"]
    friday = [d for d in days if d.dayofweek == 4]
    assert np.isclose(weekly["A"].iloc[1], first_two.loc[int(friday[2].value // 10**6)] / first_two.loc[int(friday[1].value // 10**6)] - 1)
    assert A.aligned_lookback({"align": "weekly", "lookback": 252}) == 50 and A.aligned_lookback({"lookback": 252}) == 252
    assert A.aligned_lookback({"align": "monthly", "lookback": 252}) == 12


def test_fx_converts():
    days = pd.bdate_range("2021-01-01", periods=200)
    rng = np.random.default_rng(8)
    local = 100 * np.cumprod(1 + rng.normal(0, .01, 200))
    rate = 1.2 * np.cumprod(1 + rng.normal(0, .003, 200))
    usd = 50 * np.cumprod(1 + rng.normal(0, .01, 200))
    bars = closes_frame({"SAP": local, "EURUSD": rate, "SPY": usd}, days)
    converted, _ = A.daily_returns(bars, ["SAP", "SPY"], {"fxColumns": {"SAP": {"column": "EURUSD"}}})
    expected = pd.Series(local * rate, index=days).pct_change().dropna()
    assert np.allclose(converted["SAP"].to_numpy(), expected.to_numpy())
    inverted, _ = A.daily_returns(bars, ["SAP", "SPY"], {"fxColumns": {"SAP": {"column": "EURUSD", "invert": True}}})
    assert np.allclose(inverted["SAP"].to_numpy(), pd.Series(local / rate, index=days).pct_change().dropna().to_numpy())


def test_asset_stops_keeps_its_days_and_everyone_elses():
    days = pd.bdate_range("2020-01-01", periods=300)
    rng = np.random.default_rng(9)
    a = 100 * np.cumprod(1 + rng.normal(0, .01, 300))
    b = 100 * np.cumprod(1 + rng.normal(0, .01, 300))
    b[200:] = np.nan                         # B stops trading on day 200
    returns, info = A.daily_returns(closes_frame({"A": a, "B": b}, days), ["A", "B"])
    assert info["ended"]["B"] == days[199] and len(returns) == 299
    assert returns["B"].iloc[:198].notna().all() and returns["B"].iloc[199:].isna().all()
    assert returns["A"].notna().all()


def test_too_few_periods_says_how_many():
    days = pd.bdate_range("2020-01-01", periods=300)
    frame = pd.DataFrame(np.zeros((299, 1)), index=days[1:], columns=["A"])
    text = A.too_few_periods(frame, {"lookback": 252}, 252, pd.Timestamp("2025-01-01"))
    assert text == "Lookback 252 days leaves 2 periods before Jan 1, 2025; at least 12 needed"
    assert A.too_few_periods(pd.DataFrame(np.zeros((1260, 1)), index=pd.bdate_range("2015-01-01", periods=1260), columns=["A"]), {"lookback": 252}, 252, pd.Timestamp("2020-01-01")) is None


def test_a_frozen_run_finds_its_proxy_and_rate_columns_from_the_pool_rows():
    days = pd.bdate_range("2019-01-01", periods=800)
    rng = np.random.default_rng(12)
    proxy = 50 * np.cumprod(1 + rng.normal(0.0002, .004, 800))
    own = proxy * 2.0
    own[:300] = np.nan
    local = 100 * np.cumprod(1 + rng.normal(0, .01, 800))
    rate = 1.1 * np.cumprod(1 + rng.normal(0, .002, 800))
    bars = closes_frame({"IEF": own, "VFITX": proxy, "SAP": local, "EURUSD=X": rate}, days)
    pool = [{"ticker": "IEF", "currency": "USD", "proxy": "VFITX"}, {"ticker": "SAP", "currency": "EUR", "proxy": ""}]
    derived, info = A.daily_returns(bars, ["IEF", "SAP"], {"pool": pool, "currency": "USD"})
    given, _ = A.daily_returns(bars, ["IEF", "SAP"], {"proxyColumns": {"IEF": "VFITX"}, "fxColumns": {"SAP": {"column": "EURUSD=X", "invert": False}}})
    assert np.array_equal(derived.to_numpy(), given.to_numpy()) and "IEF" in info["spliced"]
