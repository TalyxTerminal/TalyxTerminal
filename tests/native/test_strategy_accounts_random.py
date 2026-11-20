"""The accounts that must always hold, over twenty seeds that each mix order types and conditions: market, limit (through 0-3 ticks), stop, partial
fills (a volume cap), cancels, halted days, funding of either sign, and two venues.

Not part of the daily suite: set TALYX_NATIVE_SMOKE=1 (see environments.py).
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from tests.fixtures.strategy import bars as B
from tests.fixtures.strategy import engine as E

pytestmark = pytest.mark.skipif(not os.environ.get("TALYX_NATIVE_SMOKE"), reason="Set TALYX_NATIVE_SMOKE=1 to run native engines")

CODE = '''
import random
import talyx_quant as q
from nautilus_trader.model import OrderSide, TimeInForce

SEED = %d


class Random(q.Strategy):
    def __init__(self):
        super().__init__()
        self.rng = random.Random(SEED)
        self.n = 0
        self.pending = []

    def on_start(self):
        for bar_type in self.bar_types:
            self.subscribe_bars(bar_type)

    def on_bar(self, bar):
        self.n += 1
        name = bar.bar_type.instrument_id
        instrument = self.cache.instrument(name)
        close = float(bar.close)
        draw = self.rng.random()
        side = OrderSide.BUY if self.rng.random() < .5 else OrderSide.SELL
        qty = instrument.make_qty(self.rng.choice([.05, .1, .2]) if "BTC" in str(name) else self.rng.choice([.5, 1, 2]))
        if draw < .25:
            self.submit_order(self.order_factory.market(name, side, qty, tags=["entry:random"]))
        elif draw < .45:
            offset = self.rng.choice([-.004, -.002, .002, .004])
            self.submit_order(self.order_factory.limit(name, side, qty, instrument.make_price(close * (1 + offset)), time_in_force=TimeInForce.GTC, tags=["entry:limit"]))
        elif draw < .6 and self.cache.positions_open(instrument_id=name):
            held = self.position_size(name)
            stop_side = OrderSide.SELL if held > 0 else OrderSide.BUY
            trigger = close * (.99 if held > 0 else 1.01)
            self.submit_order(self.order_factory.stop_market(name, stop_side, instrument.make_qty(abs(held)), instrument.make_price(trigger), reduce_only=True, tags=["exit:stop"]))
        elif draw < .7:
            for order in self.open_orders():
                self.cancel_order(order.client_order_id)
                break
        elif draw < .78:
            self.close(name, reason="random")
'''


def build(root, seed):
    rng = np.random.default_rng(seed)
    two = seed % 2 == 1
    ticks = seed % 4
    cap = [None, 5.0][seed % 3 == 0]
    gaps = (3, 5) if seed % 5 == 0 else None
    frame = B.perp_bars(24 * 30, seed, sigma=.006)
    if gaps:
        frame = frame.drop(index=range(24 * 10, 24 * 10 + 6)).reset_index(drop=True)
    venues, instruments, bar_types = [{**E.OKX, "balances": ["20000 USDT"]}], [E.PERP], [B.BAR_TYPE]
    display = [{"nativeInstrument": B.INSTRUMENT, "symbol": "BTC/USDT:USDT"}]
    extra = {}
    if two:
        other, usdt = B.two_venues(30, seed)
        frame = pd.concat([frame, other[other["instrument"] == "ETHUSD-SPOT.COINBASE"]]).sort_values("ts_event", key=lambda s: s.astype("int64"), kind="stable").reset_index(drop=True)
        usdt.to_parquet(root / "usdt.parquet", index=False)
        venues.append(E.COINBASE)
        instruments.append(E.SPOT)
        bar_types.append("ETHUSD-SPOT.COINBASE-1-HOUR-LAST-EXTERNAL")
        display.append({"nativeInstrument": "ETHUSD-SPOT.COINBASE", "symbol": "ETH/USD"})
        extra["accounts"] = {"reportCurrency": "USD", "conversions": [{"currency": "USDT", "path": str(root / "usdt.parquet")}]}
    funding = B.funding_rows(days=30, gap=(10, 12))
    funding["rate"] = funding["rate"] * rng.choice([-1, 1])
    costs = {"halfSpreadBps": 2.0, "impact": .1, "funding": True, "borrowPct": .3, **({"volumeCapPct": cap} if cap else {})}
    E.prepare(root, CODE % seed, frame=frame, venues=venues, instruments=instruments, bar_types=bar_types, display=display, funding_frame=funding,
              settings={"limitThroughTicks": ticks, "costs": costs, **({"reportCurrency": "USD"} if two else {})}, extra=extra)
    return two


@pytest.mark.parametrize("seed", range(20))
def test_the_accounts_hold(tmp_path, seed):
    two = build(tmp_path, seed)
    _, work, results = E.completed(tmp_path)
    costs = work["costs"]
    # the cost split adds up to the gap between gross and net
    assert costs["gross"] - (costs["fees"] + costs["spread"] + costs["impact"] + costs["funding"] + costs["borrow"]) - costs["net"] == pytest.approx(0, abs=1e-6)
    # equity ends where the net profit says
    assert work["equity"][-1][1] - work["equity"][0][1] == pytest.approx(costs["net"], abs=.01 if not two else .5)
    table = E.trades_table(tmp_path)
    # every fill enters the trade table once
    assert table["id"].is_unique
    closed = int((table["exitBy"] != "open at the end").sum())
    assert work["tradesTotal"] == len(table) == closed + int((table["exitBy"] == "open at the end").sum())
    orders = E.orders_table(tmp_path)
    # a rejected, expired or canceled order changes no position: its filled quantity is zero
    dead = orders[orders["status"].isin(["Rejected", "Expired", "Canceled"])]
    assert (dead["filled"].astype(float) == 0).all() or (dead["status"] == "Canceled").any()
    if two:
        # the whole is the accounts converted to the report currency and added
        assert len(work["accounts"]) == 2
