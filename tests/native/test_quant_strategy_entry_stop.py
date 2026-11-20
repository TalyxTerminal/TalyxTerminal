"""a stop sent with the entry works inside the bar the position opens in.

An order sent on a bar reaches the venue after the next bar's prices have gone by (the entry is priced at that bar's open), so a stop sent
with the entry or when it fills only worked from the bar after: an entry bar that went through the stop left the position open (22 of 256
BTC preset trades, about 9.5% of capital counted too much). Not part of the daily suite: set TALYX_NATIVE_SMOKE=1.
"""
from __future__ import annotations

import os

import pytest

from tests.fixtures.strategy import bars as B
from tests.fixtures.strategy import engine as E
from tests.fixtures.strategy.engine import prepare

pytestmark = pytest.mark.skipif(not os.environ.get("TALYX_NATIVE_SMOKE"), reason="Set TALYX_NATIVE_SMOKE=1 to run native engines")

CODE = '''
import talyx_quant as q
from nautilus_trader.model import OrderSide


class EnterOnce(q.Strategy):
    def __init__(self):
        super().__init__()
        self.n = 0

    def on_start(self):
        self.instrument = self.cache.instrument(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar):
        self.n += 1
        if self.n == 2:
            self.submit_order(self.order_factory.market(self.instrument_id, SIDE, self.instrument.make_qty(1), tags=["entry:once"]))

    def on_order_filled(self, event):
        if self.position_size(self.instrument_id) and not self.open_orders():
            self.submit_order(self.order_factory.stop_market(self.instrument_id, EXIT, self.instrument.make_qty(1), self.instrument.make_price(STOP),
                                                             reduce_only=True, tags=["exit:stop"]))
'''


@pytest.mark.parametrize("side, rows, stop", [
    # Bar 3 (index 2) opens at 100, where the entry fills, and trades down to 90 before closing at 96: the long's stop at 95 is hit in that bar.
    ("BUY", [(100.0, 101.0, 99.0, 100.0)] * 2 + [(100.0, 101.0, 90.0, 96.0)] + [(96.0, 97.0, 95.5, 96.0)] * 6, 95.0),
    ("SELL", [(100.0, 101.0, 99.0, 100.0)] * 2 + [(100.0, 110.0, 99.0, 104.0)] + [(104.0, 104.5, 103.0, 104.0)] * 6, 105.0),
])
def test_a_stop_the_entry_bar_goes_through_closes_the_position_in_that_bar_at_the_stop(tmp_path, side, rows, stop):
    frame = B.hand_bars(rows)
    code = CODE.replace("SIDE", "OrderSide." + side).replace("EXIT", "OrderSide." + ("SELL" if side == "BUY" else "BUY")).replace("STOP", str(stop))
    prepare(tmp_path, code, frame=frame, funding=False, settings={"costs": {"halfSpreadBps": 0, "impact": 0, "funding": False}})
    _, work, _ = E.completed(tmp_path)
    assert len(E.rows(tmp_path, "trades")) == 1
    trade = E.rows(tmp_path, "trades")[0]
    entry_bar = (int(frame["ts_event"].iloc[2]) - B.HOUR_NS) // 1_000_000
    assert trade["entry"] == entry_bar and trade["exit"] < entry_bar + 3_600_000  # out within the bar it came in
    assert trade["entryPrice"] == pytest.approx(100.0) and trade["exitPrice"] == pytest.approx(stop) and trade["exitBy"] == "stop"


def test_an_entry_bar_that_stays_clear_of_the_stop_keeps_the_position(tmp_path):
    rows = [(100.0, 101.0, 99.0, 100.0)] * 2 + [(100.0, 101.0, 96.0, 99.0)] + [(99.0, 100.0, 98.0, 99.0)] * 6
    code = CODE.replace("SIDE", "OrderSide.BUY").replace("EXIT", "OrderSide.SELL").replace("STOP", "95.0")
    prepare(tmp_path, code, frame=B.hand_bars(rows), funding=False, settings={"costs": {"halfSpreadBps": 0, "impact": 0, "funding": False}})
    _, work, _ = E.completed(tmp_path)
    assert len(E.rows(tmp_path, "trades")) == 1 and E.rows(tmp_path, "trades")[0]["exitBy"] == "open at the end"


HOLD = '''
import talyx_quant as q
from nautilus_trader.model import OrderSide


class Hold(q.Strategy):
    def __init__(self):
        super().__init__()
        self.done = False

    def on_start(self):
        self.instrument = self.cache.instrument(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar):
        if not self.done:
            self.done = True
            self.submit_order(self.order_factory.market(self.instrument_id, OrderSide.BUY, self.instrument.make_qty(2), tags=["entry:hold"]))
'''


def test_the_native_positions_report_carries_the_funding_the_trades_table_takes_off(tmp_path):
    """the engine's own positions report has no funding in its realized P&L (TALYX charges it as a cash adjustment), so its column and
    the trades table's P&L did not agree. The report keeps the engine's columns and adds the funding and the P&L after it."""
    import pandas as pd
    rows = pd.DataFrame({"instrument": B.INSTRUMENT, "time": [int(pd.Timestamp("2024-01-02 08:00", tz="UTC").timestamp() * 1000) + i * 8 * 3_600_000 for i in range(3)],
                         "rate": [0.0001, -0.00005, 0.0002]})
    prepare(tmp_path, HOLD, funding_frame=rows, settings={"costs": {"halfSpreadBps": 0, "impact": 0, "funding": True}})
    _, work, _ = E.completed(tmp_path)
    trade = E.rows(tmp_path, "trades")[0]
    import json
    manifest = json.loads(next((tmp_path / "output").rglob("manifest.json")).read_text(encoding="utf-8"))
    path = next(item["name"] for item in manifest["files"] if item["role"] == "native_positions_report")
    native = pd.read_parquet(tmp_path / "output" / path)
    assert "funding" in native and "realized_pnl_after_funding" in native
    assert native["funding"].iloc[0] == pytest.approx(trade["funding"], abs=1e-6)
