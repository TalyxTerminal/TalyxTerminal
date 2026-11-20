"""The speed line's reference load: 500 names of daily bars over ten years (2,520 days), every Monday the top 50 by 12-1 month momentum held at equal
weight. It reads no library, so it needs no signal; `rebalance_to` does the trading. `bars(names, days)` builds the synthetic prices vectorised."""
from __future__ import annotations

import numpy as np
import pandas as pd

from tests.fixtures.strategy import bars as B

CODE = '''
import talyx_quant as q


class Momentum(q.Strategy):
    def __init__(self):
        super().__init__()
        self.top = q.param("top", 50, min=5, max=200, label="Names held")
        self.closes = {}
        self.day = 0

    def on_start(self):
        for bar_type in self.bar_types:
            self.subscribe_bars(bar_type)

    def on_bar(self, bar):
        key = str(bar.bar_type.instrument_id)
        history = self.closes.setdefault(key, [])
        history.append(float(bar.close))
        if key != str(self.instrument_ids[0]):
            return
        self.day += 1
        if self.day % 5 != 1 or self.day < 253:
            return
        scores = {name: values[-22] / values[-253] - 1 for name, values in self.closes.items() if len(values) >= 253}
        best = sorted(scores, key=scores.get, reverse=True)[: self.top]
        if best:
            self.rebalance_to({name: 1.0 / len(best) for name in best})
'''


def bars(names=500, days=2520, seed=17, start="2014-01-02"):
    """(frame, ids): one DataFrame of daily bars for all names, sorted by time, built column-wise (no per-name Python loop over rows)."""
    rng = np.random.default_rng(seed)
    ids = B.stock_ids(names)
    dates = pd.bdate_range(start, periods=days, tz="UTC")
    close = (50 + 10 * rng.random(names)) * np.exp(np.cumsum(rng.normal(.0004, .015, (days, names)), axis=0))
    begin = close * np.exp(rng.normal(0, .003, (days, names)))
    frames = [B.frame_of(identifier, identifier + "-1-DAY-LAST-EXTERNAL", dates + pd.Timedelta(hours=21), begin[:, number], close[:, number],
                         rng.uniform(1e5, 5e5, days).round(), span_ns=0, price_digits=2, size_digits=0) for number, identifier in enumerate(ids)]
    frame = pd.concat(frames).sort_values("ts_event", key=lambda values: values.astype("int64"), kind="stable").reset_index(drop=True)
    return frame, ids
