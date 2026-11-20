"""Close-to-close returns on signed holdings, drifting until the next trade."""
from itertools import pairwise

import numpy as np
import pandas as pd


def hold_period(weights, returns):
    """Opening weights per bar, gross returns, and closing weights after the final bar."""
    held = np.asarray(weights, dtype=float).copy()
    rows = np.nan_to_num(np.asarray(returns, dtype=float))
    opening = np.empty_like(rows)
    gross = np.empty(len(rows))
    for i, row in enumerate(rows):
        opening[i] = held
        gross[i] = held @ row
        if 1 + gross[i] != 0:
            held = held * (1 + row) / (1 + gross[i])
    return opening, gross, held


def groups_of(settings):
    """asset -> group from the pool rows."""
    return {row["ticker"]: row.get("group") or "Other" for row in settings.get("pool", [])}


def cost_bps(settings, asset):
    """The bps a trade of `asset` costs: its group's row, else the Other row (the Host writes a row for every group of the pool, so the record says what is charged)."""
    group = groups_of(settings).get(asset, "Other")
    table = {row["group"]: float(row.get("bps", 0)) for row in settings.get("costs", [])}
    return table.get(group, table.get("Other", 0.0)) * float(settings.get("cost_multiple", 1.0))


def trade_cost(target, previous, rates):
    """One-way cost, as a share of equity, at this rebalance close."""
    before = 0.0 if previous is None else previous
    return float(np.abs(np.asarray(target) - before) @ np.asarray(rates))


def book_period(daily, returns, start, end, target, held, costs):
    """Trade at the start close after the old book earned that bar. Hold through the end close, inclusive."""
    span = slice(start + 1, end + 1)
    opening, gross, drifted = hold_period(target, returns.iloc[span])
    cost = trade_cost(target, held, costs)
    daily.iloc[start] -= (1 + daily.iloc[start]) * cost
    daily.iloc[span] = gross
    return opening, gross, drifted, cost


def new_period(label, previous, every):
    """Whether the bar `label` starts a new rebalance period against the bar `previous` (the first bar of a new month, week or year)."""
    if previous is None:
        return True
    then, now = pd.Timestamp(previous, unit="ms"), pd.Timestamp(label, unit="ms")
    if every == "week":
        return now.isocalendar()[:2] != then.isocalendar()[:2]
    if every == "year":
        return now.year != then.year
    if every == "quarter":
        return (now.year, (now.month - 1) // 3) != (then.year, (then.month - 1) // 3)
    if every == "month":
        return (now.year, now.month) != (then.year, then.month)
    return now != then


def trade_spans(returns, closes):
    """Each effective close through the next effective close (or the final observed bar)."""
    return pairwise([*returns.index.get_indexer(closes), len(returns) - 1])


def benchmark_returns(returns, closes, settings, weights=None):
    """A fixed mix, or equal weights among assets with data in the fit window, traded on the allocation's dates."""
    costs = [cost_bps(settings, name) / 1e4 for name in returns.columns]
    daily = pd.Series(0.0, index=returns.index)
    held = None
    for start, end in trade_spans(returns, closes):
        if weights is None:
            lookback = int(settings.get("lookback", 252))
            eligible = returns.iloc[max(0, start - lookback):start].notna().all()
            if not eligible.any():
                raise ValueError("No asset in the pool has a price in the lookback window")
            target = eligible / eligible.sum()
        else:
            target = pd.Series(weights).reindex(returns.columns, fill_value=0.0)
        _, _, held, _ = book_period(daily, returns, start, end, target, held, costs)
    return daily
