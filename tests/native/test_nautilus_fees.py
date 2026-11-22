"""A cash account that cannot pay an order's commission refuses or trims the order instead of breaking the account.

Not part of the daily suite: it runs the compiled Nautilus runtime. On this machine:

    $env:TALYX_NATIVE_SMOKE = "1"; .venv\\Scripts\\python.exe -B -m pytest tests/native/test_nautilus_fees.py -s

The environment is installed from `strategy.lock` with uv into %LOCALAPPDATA%\\TALYX\\native-smoke (reused while
the lock is unchanged); TALYX_NATIVE_PYTHON points at an interpreter that already has the wheel instead.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("TALYX_NATIVE_SMOKE"), reason="Set TALYX_NATIVE_SMOKE=1 to run native engines")

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "modules" / "quant" / "runtime"
SCENARIOS = Path(__file__).with_name("nautilus_fee_scenarios.py")


def interpreter():
    if os.environ.get("TALYX_NATIVE_PYTHON"):
        return Path(os.environ["TALYX_NATIVE_PYTHON"])
    lock = (RUNTIME / "strategy.lock").read_text(encoding="utf-8").replace("file:./modules/quant/runtime/", (RUNTIME.as_uri() + "/"))
    key = hashlib.sha256(("3.13.13" + lock).encode()).hexdigest()[:16]
    target = Path(os.environ["LOCALAPPDATA"]) / "TALYX" / "native-smoke" / f"fees-{key}"
    python = target / "Scripts" / "python.exe"
    if not (target / "ready").is_file():
        shutil.rmtree(target, ignore_errors=True)
        uv = shutil.which("uv")
        subprocess.run([uv, "venv", "--python", "3.13.13", str(target)], check=True)
        (target / "requirements.lock").write_text(lock, encoding="utf-8")
        subprocess.run([uv, "pip", "install", "--python", str(python), "-r", str(target / "requirements.lock")], check=True)
        (target / "ready").write_text("", encoding="utf-8")
    return python


def scenario(kind, info):
    completed = subprocess.run([str(interpreter()), "-I", str(SCENARIOS), kind, info], capture_output=True, text=True, timeout=180)
    assert completed.returncode == 0, completed.stderr[-3000:]
    return json.loads(completed.stdout.strip().splitlines()[-1])


def amounts(result):
    return {code: Decimal(text.split()[0]) for code, text in result["balances"].items()}


@pytest.mark.parametrize("kind", ["spot", "equity", "sandbox"])
def test_buy_that_the_estimated_fee_does_not_cover_is_denied(kind):
    result = scenario(kind, "with-fee-info")
    assert result["error"] is None
    assert "OrderDenied" in result["events"] and "OrderFilled" not in result["events"]
    assert all(Decimal(order["filled"]) == 0 for order in result["orders"])
    assert all(value >= 0 for value in amounts(result).values())


@pytest.mark.parametrize("kind", ["spot", "equity", "sandbox"])
def test_buy_the_account_cannot_fully_pay_is_filled_in_part_and_the_rest_canceled(kind):
    result = scenario(kind, "no-fee-info")
    assert result["error"] is None
    (order,) = result["orders"]
    assert order["status"] == "CANCELED"
    assert 0 < Decimal(order["filled"]) < Decimal(str(result["requested"]))
    assert all(value >= 0 for value in amounts(result).values())


PER_INSTRUMENT = '''
import json, sys
sys.path.insert(0, sys.argv[1])
from native.strategy_research import make_engine
from nautilus_trader.model import Bar, BarType, InstrumentId, OrderSide, Price, Quantity, StrategyId
from nautilus_trader.trading import Strategy, StrategyConfig
spot = {"type": "spot", "currency": "USDT", "pricePrecision": 2, "sizePrecision": 3, "priceIncrement": "0.01", "sizeIncrement": "0.001"}
config = {"venues": [{"name": "OKX", "accountType": "CASH", "omsType": "NETTING", "balances": ["100000 USDT"], "fees": {"type": "per_instrument"}}],
          "instruments": [{**spot, "id": "BTCUSDT.OKX", "symbol": "BTCUSDT", "baseCurrency": "BTC", "makerFee": "0.0008", "takerFee": "0.001"},
                          {**spot, "id": "ETHUSDT.OKX", "symbol": "ETHUSDT", "baseCurrency": "ETH", "makerFee": "0.0002", "takerFee": "0.004"}]}
engine = make_engine(config)
ids = [InstrumentId.from_str(item["id"]) for item in config["instruments"]]
types = [BarType.from_str(f"{iid}-1-MINUTE-LAST-EXTERNAL") for iid in ids]
engine.add_data([Bar(kind, Price(100.0, 2), Price(100.0, 2), Price(100.0, 2), Price(100.0, 2), Quantity(1000, 3), i * 60_000_000_000, i * 60_000_000_000)
                 for i in range(1, 4) for kind in types])
fills = []
class Both(Strategy):
    def __init__(self):
        super().__init__(StrategyConfig(strategy_id=StrategyId("S-001")))
    def on_start(self):
        for kind in types:
            self.subscribe_bars(kind)
    def on_bar(self, bar):
        if bar.ts_init == 120_000_000_000:
            self.submit_order(self.order_factory.market(bar.bar_type.instrument_id, OrderSide.BUY, Quantity.from_str("10.000")))
    def on_order_filled(self, event):
        fills.append([str(event.instrument_id), str(event.commission)])
engine.add_strategy(Both())
engine.run()
print(json.dumps(sorted(fills)))
'''


def test_symbols_on_one_venue_each_pay_their_own_fee():
    done = subprocess.run([str(interpreter()), "-I", "-c", PER_INSTRUMENT, str(ROOT / "modules" / "quant")], capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stderr[-3000:]
    assert json.loads(done.stdout.strip().splitlines()[-1]) == [["BTCUSDT.OKX", "1.00000000 USDT"], ["ETHUSDT.OKX", "4.00000000 USDT"]]
