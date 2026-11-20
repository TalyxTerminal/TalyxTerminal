"""A strategy run's report keeps every order: the rebalances, the turnover, the order counts and the log are built from all of them.

The report kept only the first 20,000 orders, so an S&P 500 run of 24,887 orders showed no rebalance, turnover or unfilled count after August
2024. Here a synthetic engine with more orders than that, built into the report's tables directly (no backtest)."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "modules" / "quant"))
import talyx_quant as q  # noqa: E402
from native import strategy_report as R  # noqa: E402

DAY = 86_400_000
NAMES = [f"S{number:03d}.SIM" for number in range(250)]
DAYS = pd.bdate_range("2024-01-01", periods=100, tz="UTC")
ORDERS = 25_000  # more than the 20,000 the report kept


class Order:
    def __init__(self, number, at_ms, status="FILLED", tags=("entry:rebalance",), events=()):
        self.value = {"client_order_id": f"O-{number}", "ts_init": at_ms * 1_000_000, "instrument_id": NAMES[number % len(NAMES)], "side": "BUY", "type": "MARKET",
                      "quantity": "1", "filled_qty": "1" if status == "FILLED" else "0", "avg_px": "10" if status == "FILLED" else None, "status": status,
                      "tags": list(tags), "time_in_force": "GTC"}
        self._events = events

    def to_dict(self):
        return self.value

    def events(self):
        return list(self._events)


class Event:
    def __init__(self, kind, reason=None):
        self.detail = {"type": kind, "reason": reason}

    def to_dict(self):
        return self.detail


def report(orders):
    filled = [order.value for order in orders if order.value["status"] == "FILLED"]
    fills = pd.DataFrame({"client_order_id": [value["client_order_id"] for value in filled], "instrument_id": [value["instrument_id"] for value in filled],
                          "order_side": "BUY", "last_qty": 1.0, "last_px": 10.0, "ts_event": [value["ts_init"] + 1_000_000 for value in filled], "commission": "0 USD"})
    engine = SimpleNamespace(cache=SimpleNamespace(orders=lambda: orders), generate_fills_report=lambda: fills)
    ledger = SimpleNamespace(unfilled={}, at_end=set(), moments={}, entries=[])
    config = {"instruments": [{"id": name, "type": "equity", "currency": "USD"} for name in NAMES]}
    return R.Report(engine, None, config, {"accounts": {"reportCurrency": "USD"}}, None, {}, ledger, None, 252, {name: name.split(".")[0] for name in NAMES})


def test_reader_cannot_see_a_work_report_or_incomplete_table_during_publication(tmp_path, monkeypatch):
    original = pd.DataFrame.to_parquet
    def interrupted(frame, path, **kwargs):
        path.touch()
        assert not (tmp_path / "strategy" / "work.json").exists()
        assert not (tmp_path / "strategy" / "trades.parquet").exists()
        return original(frame, path, **kwargs)
    monkeypatch.setattr(pd.DataFrame, "to_parquet", interrupted)
    R.write(tmp_path, {"stats": {"trades": 1}}, {}, [{"id": 1}], {"orders": [{"id": 2}]})
    assert pd.read_parquet(tmp_path / "strategy" / "trades.parquet")["id"].tolist() == [1]


def test_failed_recollection_preserves_the_published_light_report(tmp_path, monkeypatch):
    R.write_light(tmp_path, {"count": 1}, [{"id": 1}])
    def failed(frame, path, **kwargs):
        path.touch()
        raise OSError("disk full")
    monkeypatch.setattr(pd.DataFrame, "to_parquet", failed)
    with pytest.raises(OSError, match="disk full"):
        R.write(tmp_path, {"count": 2}, {}, [{"id": 2}])
    folder = tmp_path / "strategy"
    assert (folder / "work.json").read_text() == '{"count":1}'
    assert pd.read_parquet(folder / "trades.parquet")["id"].tolist() == [1]
    assert not (folder / "results.json").exists()


def test_every_order_is_in_the_rebalances_turnover_and_counts():
    per_day = ORDERS // len(DAYS)
    stamps = [int(day.timestamp() * 1000) + 15 * 3_600_000 for day in DAYS]  # each day's orders at 15:00 UTC
    orders = [Order(number, stamps[number // per_day]) for number in range(ORDERS)]
    built = report(orders)
    q._rebalances[:] = [{"t": at - 60_000, "targets": {name: 1 / len(NAMES) for name in NAMES}} for at in stamps]
    try:
        rows = built.order_rows()
        assert len(rows) == ORDERS
        held = built.holdings_by_day(DAYS)
        closes = pd.DataFrame(10.0, index=DAYS, columns=NAMES)
        equity = pd.Series(1e6, index=DAYS)
        rebalances = built.rebalances(rows, held, closes, equity, [])
        assert len(rebalances) == len(DAYS) and sum(item["count"] for item in rebalances) == ORDERS and rebalances[-1]["count"] == per_day
        assert len(rebalances[-1]["orders"]) == per_day  # every order of a rebalance, not the first 500
        turnover = built.multi(held, closes, equity, [], built.fill_rows(), rows)["turnover"]
        assert len(turnover) == len(DAYS) and turnover[-1][0] == int(DAYS[-1].timestamp() * 1000)
        assert built.orders_section() == {"counts": {"Filled": ORDERS}, "total": ORDERS}
    finally:
        q._rebalances.clear()


def test_a_plain_cancel_is_quiet_and_an_order_open_at_the_end_says_so():
    at = int(DAYS[0].timestamp() * 1000)
    orders = [Order(0, at, "CANCELED", events=[Event("OrderCanceled")]), Order(1, at, "ACCEPTED"), Order(2, at, "REJECTED", events=[Event("OrderRejected", "no price")]),
              Order(3, at, "REJECTED", tags=("exit:liquidation",), events=[Event("OrderRejected", "would trigger")])]
    built = report(orders)
    rows = built.order_rows()
    assert [row["status"] for row in rows] == ["Canceled", R.AT_END, "Rejected", "Rejected"]
    assert rows[0]["reason"] is None and rows[1]["reason"] == "Still working when the run ended."
    lines = built.log_lines([])
    assert [(line["kind"], line["text"].split(": ")[-1]) for line in lines] == [("Not filled", "Still working when the run ended."), ("Not filled", "no price"),
                                                                                (R.LIQUIDATION_STOP, "would trigger")]
    # One count of rejected orders for the key numbers and the Orders section: the strategy's own, TALYX's liquidation stops apart.
    assert R.order_counts(rows) == {"Rejected": 1, "Canceled": 1, R.AT_END: 1, R.LIQUIDATION_STOP: 1}


def test_bars_held_are_binned_and_ticked_in_whole_bars():
    edges, axis = R.whole_bins([4, 5, 5, 4])
    assert list(edges) == [3.5, 4.5, 5.5] and axis == {"tick0": 0, "dtick": 1}
    edges, axis = R.whole_bins(list(range(1, 400)))
    assert np.allclose(np.diff(edges), np.diff(edges)[0]) and float(np.diff(edges)[0]).is_integer() and len(edges) - 1 <= 30 and axis["dtick"] == 50
