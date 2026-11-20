"""Non-standard bars stay in the Host: a 30,000-bar window projects in one pass
within 20 ms, and a live tick costs the bars it touches, not the window."""
import asyncio
import random
import time
from types import SimpleNamespace

import pytest

from modules.chart.backend.feeds import COLUMNS, ProjectedFeed

pytestmark = pytest.mark.timing

KINDS = {"heikin_ashi": None, "renko": 50.0, "kagi": 50.0, "point_figure": 50.0, "line_break": None, "range": None}


def raw_feed(count: int) -> SimpleNamespace:
    """A BTC-like 1-minute random walk, as a raw feed's columns and forming bar."""
    rng, price, columns = random.Random(7), 60_000.0, {name: [] for name in COLUMNS}
    for index in range(count):
        close = round(price + rng.gauss(0, 30), 1)
        high, low = round(max(price, close) + abs(rng.gauss(0, 15)), 1), round(min(price, close) - abs(rng.gauss(0, 15)), 1)
        for name, value in zip(COLUMNS, (index * 60_000, (index + 1) * 60_000, price, high, low, close, 5.0), strict=True):
            columns[name].append(value)
        price = close
    forming = {"time": count * 60_000, "closeTime": (count + 1) * 60_000, "open": price, "high": price + 40,
               "low": price - 40, "close": price, "volume": 1.0}
    return SimpleNamespace(columns=columns, forming=forming, price_format={"precision": 1, "minMove": 0.1},
                           listeners=[], ready=asyncio.Event())


def projected(base: SimpleNamespace, kind: str) -> ProjectedFeed:
    return ProjectedFeed(None, "key", base, kind, {"source": "close", "boxSize": KINDS[kind], "reversalBoxes": 3,
                                                   "lineCount": 3, "rangeTicks": 500})


def fastest(work, runs: int) -> float:
    best = float("inf")
    for _ in range(runs):
        started = time.perf_counter()
        work()
        best = min(best, time.perf_counter() - started)
    return best


@pytest.mark.parametrize("kind", KINDS)
def test_thirty_thousand_bars_project_in_one_pass_within_20_ms(kind):
    base = raw_feed(30_000)
    feed = projected(base, kind)
    seconds = fastest(lambda: feed.on_replaced(base), 7)
    assert len(feed.columns["time"]) == len(feed.projector.bars) > 0
    assert seconds <= 0.020, f"{kind}: {seconds * 1000:.1f} ms"


@pytest.mark.parametrize("kind", KINDS)
def test_a_live_tick_costs_the_same_on_a_long_window(kind):
    def per_tick(count: int) -> float:
        base = raw_feed(count)
        feed = projected(base, kind)
        feed.on_replaced(base)
        opened = base.forming["open"]

        def ticks():
            for step in range(200):
                base.forming = {**base.forming, "close": opened + step % 9 * 10 - 40}
                feed.on_forming(base)
        return fastest(ticks, 5) / 200
    short, long = per_tick(1_000), per_tick(30_000)
    assert long <= 3 * short + 2e-6, f"{kind}: {short * 1e6:.1f} us at 1,000 bars, {long * 1e6:.1f} us at 30,000"
