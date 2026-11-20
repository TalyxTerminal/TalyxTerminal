"""What a sweep's combinations and a walk-forward's windows rely on in the strategy environment: warm-up (orders start at `tradeFrom`), the light report
a combination writes, and the peak memory and timings every run records.

Not part of the daily suite: set TALYX_NATIVE_SMOKE=1 (see environments.py).
"""
from __future__ import annotations

import os

import pytest

from tests.fixtures.strategy import bars as B
from tests.fixtures.strategy import engine as E
from tests.fixtures.strategy.engine import prepare, read, run
from tests.native.test_strategy_report_native import CODE

pytestmark = pytest.mark.skipif(not os.environ.get("TALYX_NATIVE_SMOKE"), reason="Set TALYX_NATIVE_SMOKE=1 to run native engines")


def test_warm_up_orders_start_at_trade_from(tmp_path):
    frame = B.perp_bars(24 * 60)
    stamps = frame["ts_event"].astype("int64") // 1_000_000
    start = int(stamps.iloc[24 * 30])
    prepare(tmp_path, CODE, frame=frame, extra=None)
    import json
    invocation = json.loads((tmp_path / "invocation.json").read_text(encoding="utf-8"))
    invocation["backtest"]["tradeFrom"] = start
    (tmp_path / "invocation.json").write_text(json.dumps(invocation), encoding="utf-8")
    index = run(tmp_path)
    assert index["status"] == "completed", (index.get("error"), (tmp_path / "stderr.txt").read_text(encoding="utf-8")[-3000:])
    work = read(tmp_path, "work")
    assert E.rows(tmp_path, "trades") and all(trade["entryAt"] >= start for trade in E.rows(tmp_path, "trades") if trade.get("entryAt"))
    assert work["equity"][0][0] >= start - 2 * 86_400_000


def test_a_light_report_has_numbers_and_trades_but_no_figures(tmp_path):
    prepare(tmp_path, CODE, frame=B.perp_bars(24 * 40), extra={"report": "light"})
    index = run(tmp_path)
    assert index["status"] == "completed", (index.get("error"), (tmp_path / "stderr.txt").read_text(encoding="utf-8")[-3000:])
    strategy = tmp_path / "output" / "strategy"
    assert (strategy / "work.json").is_file() and (strategy / "trades.parquet").is_file() and not (strategy / "results.json").exists()
    assert index["strategyReport"]["light"] is True and index["metrics"]
    assert index.get("peakMemory", 0) > 0 and index.get("timings")


def rates(root):
    from tests.fixtures.strategy.engine import trades_table
    table = trades_table(root)
    notional = (table["qty"] * (table["entryPrice"] + table["exitPrice"])).sum()
    return table["fees"].sum() / notional, table["spread"].sum() / notional


def test_doubled_costs_double_the_rate_per_traded_notional(tmp_path):
    """The cost multiple scales the rates, not the totals (positions move with equity): fees and spread over the traded notional are 2x (to the rounding of the written trades)."""
    found = {}
    for name, factor in (("one", 1), ("two", 2)):
        root = tmp_path / name
        root.mkdir()
        venue = {**E.OKX, "fees": {"type": "maker_taker", "maker": 0.0002 * factor, "taker": 0.0005 * factor}}
        prepare(root, CODE, frame=B.perp_bars(24 * 60), venues=[venue], settings={"costs": {"halfSpreadBps": 2.0 * factor, "impact": 0.0, "funding": True, "borrowPct": 0.3}})
        assert run(root)["status"] == "completed"
        found[name] = rates(root)
    assert found["two"][0] == pytest.approx(2 * found["one"][0], rel=1e-4) and found["two"][1] == pytest.approx(2 * found["one"][1], rel=1e-4)
