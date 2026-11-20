"""How a published signal did after publication, counted only on settled periods.

A period is settled once its forward return is known: the value at bar t is scored against the return from t + delay to t + delay + holding,
so the newest delay + holding bars after publication are pending and do not count. The figures are the mean daily rank IC with its standard
error adjusted for the overlapping horizon (Newey–West), its t, the annual net return on the version's overlapping long–short book, and names a day; the
daily points before and after publication are kept for the item page's cumulative chart.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..native import factor_work as W
from ..native import metrics as M
from ..native.allocation_holdings import (
    benchmark_returns,
    book_period,
    cost_bps,
    new_period,
    trade_spans,
)
from ..native.allocation_prices import aligned_lookback

DAY = 86_400_000


def book_returns(prices, table, settings, sectors, annualization):
    if prices is None or prices.empty or table.empty:
        return pd.Series(dtype=float)
    closes = prices.sort_index().reindex(columns=table.columns)
    scores = table.reindex(closes.index).to_numpy(float)
    industry = [sectors.get(name) for name in closes.columns] if sectors else None
    panel = W.Panel(closes.index, closes.columns, {"close": closes.to_numpy(float)}, None, {"annualization": annualization}, industry=industry)
    groups = W.group_of(scores, settings, industry)
    returns = W.returns_of(panel, groups, W.group_count(groups, settings), settings, scores)
    return pd.Series(returns["net"], index=closes.index)


def measure(values, prices, *, scheduled, holding, delay, annualization, realised=None, ahead=None, settings=None, sectors=None):
    """`values`: rows with datetime, instrument, value; `prices`: closes, bar labels by instrument (the same instruments).
    `scheduled` contains only signal labels eligible for the tracking record."""
    if values is None or len(values) == 0 or (realised is None and (prices is None or prices.empty)):
        return None, []
    table = values.pivot_table(index="datetime", columns="instrument", values="value").sort_index()
    if realised is not None:
        # A model scored against its own label (what it was trained to predict, not the close-to-close return): the realised label of each period.
        # A period settles `ahead` bars later, and the Newey-West lag follows how many bars the label spans.
        forward = realised.pivot_table(index="datetime", columns="instrument", values="value").sort_index().reindex(index=table.index, columns=table.columns)
    else:
        prices = prices.sort_index()
        closes = prices.reindex(columns=table.columns)
        start = closes.shift(-delay)
        end = closes.shift(-(delay + holding))
        forward = (end / start - 1).reindex(table.index)
    ic = pd.Series(M.rank_correlation(table.to_numpy(float), forward.reindex(index=table.index, columns=table.columns).to_numpy(float)), index=table.index)
    # settled: the bar `settle` after it exists among the prices (for a realised label: and the label is there)
    settled = forward.notna().any(axis=1)
    settings = W.settings_of({**(settings or {}), "holding": holding, "delay": delay})
    names = table.notna().sum(axis=1)
    after = table.index.isin(scheduled)
    spread = book_returns(prices, table, settings, sectors, annualization)
    tracked = table.loc[after & settled.to_numpy()]
    tracked_spread = book_returns(prices, tracked, settings, sectors, annualization) if len(tracked) else pd.Series(dtype=float)
    history = spread.reindex(table.index)
    points = [{"date": int(label), "ic": M.finite(ic.loc[label]), "ls": M.finite(history.loc[label]),
               "trackedLs": M.finite(tracked_spread.get(label)), "settled": bool(settled.loc[label]), "after": bool(after[index])}
              for index, label in enumerate(table.index)]
    # Some holding-period profits land on a bar without a fresh score.
    for label in spread.dropna().index.union(tracked_spread.dropna().index).difference(table.index):
        points.append({"date": int(label), "ic": None, "ls": M.finite(spread.get(label)), "trackedLs": M.finite(tracked_spread.get(label)),
                       "after": False, "settled": False})
    points.sort(key=lambda point: point["date"])
    chosen = ic[after & settled.to_numpy()]
    pending = int((after & ~settled.to_numpy()).sum())
    if not len(chosen):
        return {"kind": "signal", "value": None, "se": None, "t": None, "ls": None, "names": None, "settled": 0, "settledDays": 0, "pending": pending}, points
    value = M.mean(chosen.to_numpy())
    t = M.newey_west_t(chosen.to_numpy(), max(holding - 1, 0))
    figures = {"kind": "signal", "value": value, "se": value / t if t else None, "t": t,
               "ls": M.annual_mean(tracked_spread.to_numpy(), annualization), "names": float(names.loc[chosen.index].median()),
               "settled": len(chosen), "settledDays": len({int(label) // DAY for label in chosen.index}), "pending": pending}
    return figures, points


def published(values, prices, *, holding, delay, annualization, realised=None, ahead=None, settings=None, sectors=None):
    """The figures a signal is published with: `measure` over the whole history the version holds, with the horizon it is followed on afterwards, so the
    figures before and after publication are one function over two stretches of time."""
    figures, _points = measure(values, prices, scheduled=values["datetime"].unique(), settings=settings, sectors=sectors, holding=holding, delay=delay, annualization=annualization, realised=realised, ahead=ahead)
    return {"metric": "rank_ic", **{key: (figures or {}).get(key) for key in ("value", "se", "t", "ls", "names")}, "holding": holding, "delay": delay}


VOL_WINDOW = 63
DRIFT_VOL = 1.5
DRIFT_SHARE = 0.10


PER_YEAR = {"week": 52, "month": 12, "quarter": 4, "year": 1}


def benchmark_of(meta):
    """What an allocation's return is measured against, as its run was: 60/40 when SPY and IEF are in its pool, else its pool in equal weights."""
    ids = {row.get("ticker"): row.get("instrumentId") for row in (meta.get("allocation") or {}).get("pool") or []}
    if (meta.get("published") or {}).get("vs6040") is not None and ids.get("SPY") and ids.get("IEF"):
        return {"name": "60/40", "key": "vs6040", "weights": {ids["SPY"]: 0.6, ids["IEF"]: 0.4}}
    return {"name": "equal weight", "key": "vsEqualWeight", "weights": None}


def rate_per_bar(spec, labels, annualization):
    """The risk-free rate an observation on each bar label (ms) from `{basis: zero}` or ^IRX closes (`strategy_inputs.risk_free`): the newest close not later
    than the bar, over `annualization`. 0.0 for zero, None when Market has no rate."""
    if spec["basis"] == "zero":
        return 0.0
    if spec.get("unavailable") or not spec.get("rows"):
        return None
    closes = pd.Series([float(row[1]) for row in spec["rows"]], index=[int(row[0]) for row in spec["rows"]]).sort_index()
    closes = closes[~closes.index.duplicated(keep="last")]
    labels = sorted(int(label) for label in labels)
    return closes.reindex(closes.index.union(labels)).ffill().reindex(labels) / 100 / annualization


def allocation(values, prices, *, scheduled, published_through, published, annualization, shares, every, risk_free=0.0, benchmark=None, settings=None):
    """How an allocation's risk went after publication, by the run's own definitions (`native/metrics.py`): the volatility of its drifted return over the last 63
    bars against the volatility it was published with (the run's), and each asset's risk share now (the producer's, from the same fit and risk measure as the
    run's periods) against the run's newest period's. Drift is volatility above 1.5 times the published, or a share 10 points off. Beside them: the turnover a
    year of its rebalances since, its Sharpe since over `risk_free` (an observation's, by bar label; None when Market has no rate), and its return since against
    its benchmark's."""
    if values is None or len(values) == 0 or prices is None or prices.empty:
        return None, []
    weights = values.pivot_table(index="datetime", columns="instrument", values="value").sort_index()
    closes = prices.sort_index().reindex(columns=weights.columns)
    returns = closes.pct_change(fill_method=None)
    # The first price is the base of the first return, outside the native fit window.
    if returns.index[0] < weights.index[0]:
        returns = returns.iloc[1:]
    else:
        returns.iloc[0] = 0.0  # opening the book needs no return before the first observed price
    settings = settings or {}
    labels = list(weights.index.intersection(returns.index))
    trades = [label for i, label in enumerate(labels) if new_period(label, labels[i - 1] if i else None, every)]
    costs = np.array([cost_bps(settings, column) / 1e4 for column in returns.columns])
    path = pd.Series(0.0, index=returns.index)
    held = None
    changes = []
    for start, end in trade_spans(returns, trades):
        target = weights.loc[returns.index[start]].fillna(0)
        if held is not None and returns.index[start] in scheduled:
            changes.append(0.5 * float(np.abs(target - held).sum()))
        _, _, held, _ = book_period(path, returns, start, end, target, held, costs)
    path = path.loc[weights.index[0]:]
    realized = path.rolling(VOL_WINDOW, min_periods=20).apply(lambda window: np.nan if (found := M.volatility(window, annualization)) is None else found, raw=True)
    after = realized.index.isin(scheduled)
    points = [{"date": int(label), "value": None if not np.isfinite(value) else round(float(value), 6), "settled": True, "after": bool(flag)}
              for label, value, flag in zip(realized.index, realized.to_numpy(), after, strict=True)]
    settled = int(after.sum())
    now = realized[after].dropna()
    published_vol = published["value"]
    figures = {"kind": "allocation", "value": float(now.iloc[-1]) if len(now) else None, "published": published_vol, "settled": settled, "pending": 0,
               "multiple": None, "shares": None, "worst": None, "drift": False}
    if figures["value"] is not None and published_vol:
        figures["multiple"] = figures["value"] / published_vol
    shares_now = {key: round(float(value), 6) for key, value in shares.items()}
    shares_then = published["riskShares"]
    if shares_now and shares_then:
        deltas = {asset: shares_now.get(asset, 0.0) - shares_then.get(asset, 0.0) for asset in shares_now.keys() | shares_then.keys()}
        asset = max(deltas, key=lambda key: abs(deltas[key]))
        figures["worst"] = {"asset": asset, "delta": deltas[asset]}
    figures["shares"] = {"published": shares_then, "now": shares_now}
    figures["drift"] = bool(settled and ((figures["multiple"] or 0) > DRIFT_VOL or (figures["worst"] is not None and abs(figures["worst"]["delta"]) > DRIFT_SHARE)))
    since = path[path.index.isin(scheduled)]
    rate = risk_free.reindex(since.index).to_numpy(float) if isinstance(risk_free, pd.Series) else risk_free
    figures["sharpe"] = None if rate is None else M.sharpe(since.to_numpy(float), annualization, rate)
    years = (weights.index[-1] - published_through) / DAY / 365.25
    turnover = None
    if changes:
        turnover = float(np.mean(changes)) * PER_YEAR[every] if every in PER_YEAR else (sum(changes) / years if years > 0 else None)
    figures.update(turnover=turnover, rebalances=len(changes))
    if benchmark is not None and len(since):
        other = benchmark_returns(returns, trades, {**settings, "lookback": aligned_lookback(settings)}, benchmark["weights"]).loc[since.index]
        mine, theirs = float((1 + since).prod() - 1), float((1 + other).prod() - 1)
        figures["benchmark"] = {"name": benchmark["name"], "published": (published or {}).get(benchmark["key"]), "since": mine - theirs}
    return figures, points
