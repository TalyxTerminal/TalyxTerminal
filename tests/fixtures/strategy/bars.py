"""Synthetic bars for the strategy tests (fixed seeds): a perpetual on 1-hour bars with a 5-minute set under it, twenty daily stocks with one halted
for four days, an 8-hourly funding history with a hole, two venues, and a long 1-hour series. Prices are geometric random walks."""
from __future__ import annotations

import numpy as np
import pandas as pd

INSTRUMENT = "BTCUSDT-PERP.OKX"
BAR_TYPE = INSTRUMENT + "-1-HOUR-LAST-EXTERNAL"
HOUR_NS = 3_600_000_000_000
DAY_NS = 86_400_000_000_000
START = "2024-01-01"


def text(values, digits):
    return [format(round(float(value), digits), "f") for value in values]


def frame_of(instrument, bar_type, opened, start, close, volume, *, span_ns, price_digits=1, size_digits=3, spread=.002):
    """Bars as the engine takes them: decimal text, `ts_event` the bar's close time in nanoseconds."""
    ends = opened.as_unit("ns").asi8 + span_ns
    return pd.DataFrame({"open": text(start, price_digits), "high": text(np.maximum(start, close) * (1 + spread), price_digits),
                         "low": text(np.minimum(start, close) * (1 - spread), price_digits), "close": text(close, price_digits),
                         "volume": text(volume, size_digits), "instrument": instrument, "barType": bar_type, "ts_event": ends, "ts_init": ends})


def perp_bars(count=24 * 40, seed=3, instrument=INSTRUMENT, bar_type=BAR_TYPE, first=40000.0, sigma=.004, start=START):
    rng = np.random.default_rng(seed)
    opened = pd.date_range(start, periods=count, freq="h", tz="UTC")
    close = first * np.exp(np.cumsum(rng.normal(0, sigma, count)))
    begin = close * np.exp(rng.normal(0, .001, count))
    return frame_of(instrument, bar_type, opened, begin, close, rng.uniform(100, 500, count), span_ns=HOUR_NS)


def hand_bars(rows, *, instrument=INSTRUMENT, bar_type=BAR_TYPE, start=START, step="h", span_ns=HOUR_NS, price_digits=1):
    """Bars written by hand: rows of (open, high, low, close[, volume])."""
    opened = pd.date_range(start, periods=len(rows), freq=step, tz="UTC")
    ends = opened.as_unit("ns").asi8 + span_ns
    columns = list(zip(*[(*row, 1000)[:5] for row in rows]))
    names = ("open", "high", "low", "close", "volume")
    out = {name: text(column, 3 if name == "volume" else price_digits) for name, column in zip(names, columns)}
    return pd.DataFrame({**out, "instrument": instrument, "barType": bar_type, "ts_event": ends, "ts_init": ends})


def fine_bars(coarse, *, minutes=5, instrument=INSTRUMENT, seed=5):
    """The 5-minute bars under each 1-hour bar: they open where the hour opens, close where it closes, and reach exactly its high and low once each."""
    rng = np.random.default_rng(seed)
    parts = 60 // minutes
    rows = []
    for _, bar in coarse.iterrows():
        o, h, l, c = (float(bar[key]) for key in ("open", "high", "low", "close"))
        end = int(bar["ts_event"])
        path = np.linspace(o, c, parts + 1) * np.exp(np.concatenate([[0], rng.normal(0, .0005, parts - 1), [0]]))
        path = np.clip(path, l, h)
        up, down = rng.choice(parts, 2, replace=False)
        for i in range(parts):
            a, b = path[i], path[i + 1]
            high, low = max(a, b), min(a, b)
            if i == up:
                high = h
            if i == down:
                low = l
            rows.append((a, high, low, b, end - HOUR_NS + (i + 1) * minutes * 60_000_000_000))
    ends = [row[4] for row in rows]
    return pd.DataFrame({"open": text([r[0] for r in rows], 1), "high": text([r[1] for r in rows], 1), "low": text([r[2] for r in rows], 1), "close": text([r[3] for r in rows], 1),
                         "volume": "10.000", "instrument": instrument, "barType": f"{instrument}-{minutes}-MINUTE-LAST-EXTERNAL", "ts_event": ends, "ts_init": ends})


def stock_ids(count):
    return [f"S{index:02d}.XNAS" for index in range(count)]


def daily_universe(names=20, days=500, seed=11, halted=((7, 200, 204),), start="2022-01-03"):
    """Twenty stocks on daily bars; each `(name, first, last)` in `halted` has no bars on those day numbers (a halt). Returns (frame, ids)."""
    rng = np.random.default_rng(seed)
    ids = stock_ids(names)
    dates = pd.bdate_range(start, periods=days, tz="UTC")
    frames = []
    for number, identifier in enumerate(ids):
        close = (50 + 10 * number) * np.exp(np.cumsum(rng.normal(.0003, .012, days)))
        begin = close * np.exp(rng.normal(0, .003, days))
        frame = frame_of(identifier, identifier + "-1-DAY-LAST-EXTERNAL", dates + pd.Timedelta(hours=21), begin, close, rng.uniform(1e5, 5e5, days).round(), span_ns=0,
                         price_digits=2, size_digits=0)
        keep = np.ones(days, bool)
        for halted_name, first, last in halted:
            if halted_name == number:
                keep[first:last] = False
        frames.append(frame[keep])
    return pd.concat(frames).sort_values("ts_event", key=lambda values: values.astype("int64")).reset_index(drop=True), ids


def funding_rows(instrument=INSTRUMENT, days=40, gap=(10, 12), start=START):
    """Settlements every 8 hours, the rate alternating +0.01% and -0.005%, none on days `gap` (first, last) -- a hole in the history."""
    times = pd.date_range(start, periods=days * 3, freq="8h", tz="UTC")
    rate = np.where(np.arange(len(times)) % 2, 0.0001, -0.00005)
    keep = ~((times >= pd.Timestamp(start, tz="UTC") + pd.Timedelta(days=gap[0])) & (times < pd.Timestamp(start, tz="UTC") + pd.Timedelta(days=gap[1]))) if gap else np.ones(len(times), bool)
    return pd.DataFrame({"instrument": instrument, "time": times.as_unit("ms").asi8[keep], "rate": rate[keep]})


def long_series(count=20000, seed=9):
    return perp_bars(count, seed)


def two_venues(days=40, seed=21):
    """A perpetual quoted in USDT at OKX and a spot pair quoted in USD at COINBASE, plus the USDT/USD daily close walking between 0.999 and 1.001."""
    first = perp_bars(24 * days, seed)
    second = perp_bars(24 * days, seed + 1, instrument="ETHUSD-SPOT.COINBASE", bar_type="ETHUSD-SPOT.COINBASE-1-HOUR-LAST-EXTERNAL", first=2200.0)
    rng = np.random.default_rng(seed)
    dates = pd.date_range(START, periods=days + 1, freq="D", tz="UTC")
    usdt = pd.DataFrame({"time": dates.as_unit("ms").asi8, "close": 1 + np.clip(np.cumsum(rng.normal(0, .0003, len(dates))), -.001, .001)})
    return pd.concat([first, second]).sort_values("ts_event", key=lambda values: values.astype("int64"), kind="stable").reset_index(drop=True), usdt
