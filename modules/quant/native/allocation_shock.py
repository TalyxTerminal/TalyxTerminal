"""One shock on the allocation's scenarios table: an asset (or a group) falls X% and the others follow by the covariance (the conditional mean). Only numpy, so the Host can
answer "Add a shock" on a finished run from the covariance the run kept, and the worker writes the same rows."""
from __future__ import annotations

import numpy as np


def shock_row(names, weights, cov, groups, target, pct):
    """{scenario, kind, portfolio, worst} for `target` (an asset's column or a group's name) moving `pct` percent; None when nothing in the pool is that."""
    names = list(names)
    cov = np.asarray(cov, dtype=float)
    held = np.asarray([float(weights.get(name, 0.0)) for name in names])
    members = [name for name in names if name == target or groups.get(name) == target]
    if not members:
        return None
    at = [names.index(name) for name in members]
    rest = [k for k in range(len(names)) if k not in at]
    move = np.zeros(len(names))
    move[at] = pct / 100
    if rest:  # the others' conditional mean given the shocked ones' move: Sigma_og Sigma_gg^-1 m (a group moves its members together)
        move[rest] = cov[np.ix_(rest, at)] @ np.linalg.lstsq(cov[np.ix_(at, at)], move[at], rcond=None)[0]
    sign = "\u2212" if pct < 0 else "+"
    return {"scenario": f"{target} {sign}{abs(pct):g}%", "kind": "Shock", "portfolio": float(held @ move), "worst": names[int(np.argmin(move))]}
