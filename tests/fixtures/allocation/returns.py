"""Synthetic daily returns for the allocation checks: 6 assets x 1,260 days, pairwise correlation 0.3, volatility 0.5%..3% rising, in 3 groups."""
import numpy as np
import pandas as pd

ASSETS = ["A1", "A2", "A3", "A4", "A5", "A6"]
GROUPS = {"A1": "Bonds", "A2": "Bonds", "A3": "Stocks", "A4": "Stocks", "A5": "Other", "A6": "Other"}


def make(days=1260, seed=7, late_start=None, stop_at=None):
    rng = np.random.default_rng(seed)
    vol = np.linspace(0.005, 0.03, 6)
    corr = np.full((6, 6), 0.3) + 0.7 * np.eye(6)
    cov = corr * np.outer(vol, vol)
    data = rng.multivariate_normal(np.full(6, 0.0003), cov, size=days)
    frame = pd.DataFrame(data, index=pd.bdate_range("2015-01-01", periods=days), columns=ASSETS)
    if late_start:
        frame.iloc[:late_start, 5] = np.nan
    if stop_at:
        frame.iloc[stop_at:, 4] = np.nan
    return frame


def settings(**more):
    base = {"pool": [{"ticker": a, "group": GROUPS[a]} for a in ASSETS], "objective": "min_risk", "risk_measure": "variance", "covariance": "empirical",
            "lookback": 252, "frequency": "monthly", "annualization": 252, "costs": [{"group": g, "bps": 0} for g in ("Bonds", "Stocks", "Other")], "cost_multiple": 1.0, "cost_aware": False,
            "benchmarkColumns": {}}
    base.update(more)
    return base
