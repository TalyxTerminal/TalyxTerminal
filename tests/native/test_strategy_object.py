"""A strategy object's backtest in the fixed strategy environment: parameter overrides reaching the author's code, and error mapping.

Not part of the daily suite; see test_engine_smoke.py for how to run the native checks.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tests.native.environments import environment

pytestmark = pytest.mark.skipif(not os.environ.get("TALYX_NATIVE_SMOKE"), reason="Set TALYX_NATIVE_SMOKE=1 to run native engines")

QUANT = Path(__file__).resolve().parents[2] / "modules" / "quant"
INSTRUMENT = "BTCUSDT-PERP.BINANCE"
BAR_TYPE = INSTRUMENT + "-1-HOUR-LAST-EXTERNAL"

# A strategy with parameters (the starter code declares none since the strategy page came in): a moving-average crossover.
CROSSOVER = '''"""Moving-average crossover: long when the fast average is above the slow one, flat otherwise.

Write only the trading logic. The data, venue, fees and account come from the strategy's data settings;
the numbers declared with q.param show up as editable fields next to the chart.
"""
from collections import deque

import talyx_quant as q
from nautilus_trader.model import OrderSide


class MovingAverageCrossover(q.Strategy):
    def __init__(self):
        super().__init__()
        self.fast = q.param("fast", 10, min=2, max=200, label="Fast window")
        self.slow = q.param("slow", 30, min=3, max=500, label="Slow window")
        self.stake = q.param("stake", 1000.0, min=1, label="Position size (quote currency)")
        if self.fast >= self.slow:
            raise ValueError("The fast window must be shorter than the slow window")
        self.closes = deque(maxlen=self.slow)

    def on_start(self):
        self.instrument = self.cache.instrument(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar):
        self.closes.append(float(bar.close))
        if len(self.closes) < self.slow:
            return
        closes = list(self.closes)
        fast = sum(closes[-self.fast:]) / self.fast
        slow = sum(closes) / self.slow
        flat = not self.cache.positions_open(instrument_id=self.instrument_id)
        if fast > slow and flat:
            quantity = self.instrument.make_qty(self.stake / float(bar.close))
            if quantity.as_double() > 0:
                self.submit_order(self.order_factory.market(self.instrument_id, OrderSide.BUY, quantity))
        elif fast < slow and not flat:
            self.close_all_positions(self.instrument_id)

    def on_stop(self):
        self.cancel_all_orders(self.instrument_id)
        self.close_all_positions(self.instrument_id)
'''


def prepare(root, params, code=CROSSOVER):
    (root / "source").mkdir()
    (root / "output").mkdir()
    (root / "source" / "strategy.py").write_text(code, encoding="utf-8")
    rng = np.random.default_rng(1)
    count = 24 * 60
    opened = pd.date_range("2024-01-01", periods=count, freq="h", tz="UTC")
    close = 40000 * np.exp(np.cumsum(rng.normal(0, 0.004, count)))
    start = np.r_[close[0], close[:-1]]
    text = lambda values, digits: [format(round(float(value), digits), "f") for value in values]
    ends = opened.as_unit("ns").asi8 + 3_600_000_000_000
    pd.DataFrame({"open": text(start, 1), "high": text(np.maximum(start, close) * 1.001, 1), "low": text(np.minimum(start, close) * .999, 1),
                  "close": text(close, 1), "volume": text(rng.uniform(10, 500, count), 3), "instrument": INSTRUMENT, "barType": BAR_TYPE,
                  "ts_event": ends, "ts_init": ends}).to_parquet(root / "bars.parquet", index=False)
    backtest = {"engine": "nautilus", "name": "object", "task": "event", "dataType": "bars", "timezone": "UTC", "priceBasis": "raw",
                "instruments": [{"id": INSTRUMENT, "type": "perpetual", "symbol": "BTCUSDT-PERP", "currency": "USDT", "baseCurrency": "BTC",
                                 "settlementCurrency": "USDT", "pricePrecision": 1, "sizePrecision": 6, "priceIncrement": "0.1", "sizeIncrement": "0.000001"}],
                "venues": [{"name": "BINANCE", "accountType": "MARGIN", "omsType": "NETTING", "balances": ["100000 USDT"], "baseCurrency": "USDT",
                            "fees": {"type": "maker_taker", "maker": 0.0002, "taker": 0.0005}, "leverage": 1}],
                "strategy": {"kind": "object", "module": "strategy"}}
    invocation = {"runId": "object-smoke", "title": "object", "workbench": "strategy", "entry": "strategy.py", "source": str(root / "source"),
                  "output": str(root / "output"), "library": None, "materials": [{"role": "market_data", "path": str(root / "bars.parquet")}],
                  "mode": "run", "operation": "strategy.backtest", "options": {}, "sourceRefs": [], "annualization": 365, "barTypes": [BAR_TYPE],
                  "params": params, "backtest": backtest, "recipeRef": None, "libraryValues": {"path": None}, "models": [], "index": None}
    (root / "invocation.json").write_text(json.dumps(invocation), encoding="utf-8")


def run(root):
    interpreter = environment("strategy.lock", "3.13.13")
    subprocess.run([str(interpreter), "-B", str(QUANT / "native" / "object_worker.py"), str(root / "invocation.json")],
                   cwd=root / "source", capture_output=True, text=True, timeout=600)
    return json.loads((root / "output" / "result-index.json").read_text(encoding="utf-8"))


def test_strategy_runs_with_the_panels_parameter_values(tmp_path):
    prepare(tmp_path, {"fast": 5, "slow": 20, "stake": 500.0})
    result = run(tmp_path)
    assert result["status"] == "completed", result.get("error")
    assert result["parameters"] == {"fast": 5, "slow": 20, "stake": 500.0}
    # The values reached the code, not only the record: each entry is the panel's stake, not the code's 1,000.
    fills = pd.read_parquet(tmp_path / "output" / next(item["name"] for item in result["artifacts"] if item["kind"] == "table" and item["title"] == "Fills"))
    buys = fills[fills["order_side"].astype(str) == "BUY"]
    notional = buys["last_qty"].astype(float) * buys["last_px"].astype(float)
    assert len(buys) and ((notional - 500).abs() < 500 * .01).all()
    statistics = result["units"][0]["nativeStatistics"]
    assert statistics["total_orders"] > 0 and statistics["accountPerformance"][0]["currency"] == "USDT"
    titles = {item["title"] for item in result["artifacts"] if item["kind"] == "table"}
    assert {"Account equity", "Fills", "Positions"} <= titles


def test_an_error_in_the_authors_callback_fails_the_run_at_the_authors_line(tmp_path):
    code = CROSSOVER.replace("        closes = list(self.closes)\n", "        closes = list(self.closes)\n        raise RuntimeError('boom')\n")
    prepare(tmp_path, {}, code)
    result = run(tmp_path)
    assert result["status"] == "failed" and result["error"] == "boom"
    frames = result["failure"]["frames"]
    frame = next(item for item in reversed(frames) if item["root"] == "source")
    assert (frame["file"], frame["function"]) == ("strategy.py", "on_bar")
    assert code.splitlines()[frame["line"] - 1].strip() == "raise RuntimeError('boom')"
    # No shown name carries this machine's directories.
    assert all(not Path(item["file"]).is_absolute() for item in frames)
