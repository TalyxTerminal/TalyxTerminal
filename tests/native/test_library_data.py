"""A library value carried by Nautilus as custom data: a backtest hands it to the strategy when it became available, not when it is about.

Not part of the daily suite: it runs the compiled Nautilus runtime (TALYX_NATIVE_SMOKE=1, see test_nautilus_fees.py).
"""
from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests.native.test_nautilus_fees import interpreter

pytestmark = pytest.mark.skipif(not os.environ.get("TALYX_NATIVE_SMOKE"), reason="Set TALYX_NATIVE_SMOKE=1 to run native engines")

QUANT = Path(__file__).resolve().parents[2] / "modules" / "quant"
SCRIPT = textwrap.dedent('''
    import json, sys
    from decimal import Decimal
    sys.path.insert(0, sys.argv[1])
    from native.library_data import LibraryValue, as_data, data_type, library_value, register
    from nautilus_trader.backtest import BacktestEngine, BacktestEngineConfig
    from nautilus_trader.common import LoggerConfig
    from nautilus_trader.execution import MakerTakerFeeModel
    from nautilus_trader.model import (AccountType, Bar, BarType, Currency, CurrencyPair, InstrumentId, Money, OmsType, Price, Quantity, StrategyId, Symbol, Venue)
    from nautilus_trader.trading import Strategy, StrategyConfig
    register()
    iid, bar_type = InstrumentId.from_str("BTCUSDT.OKX"), BarType.from_str("BTCUSDT.OKX-1-MINUTE-LAST-EXTERNAL")
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggerConfig(bypass_logging=True, print_config=False), bypass_logging=True))
    engine.add_venue(Venue("OKX"), OmsType.NETTING, AccountType.CASH, [Money.from_str("1000 USDT")], fee_model=MakerTakerFeeModel(Decimal(0), Decimal(0)))
    engine.add_instrument(CurrencyPair(iid, Symbol("BTCUSDT"), Currency.from_str("BTC"), Currency.from_str("USDT"), 1, 3, Price.from_str("0.1"), Quantity.from_str("0.001"), 0, 0))
    seen = []
    class Watch(Strategy):
        def __init__(self):
            super().__init__(StrategyConfig(strategy_id=StrategyId("S-001")))
        def on_start(self):
            self.subscribe_data(data_type(), None, None)
            self.subscribe_bars(bar_type)
        def on_data(self, data):
            value = data.data
            seen.append(["value", data.ts_init, value.name, value.instrument_id, value.value, value.version, value.ts_event])
        def on_bar(self, bar):
            seen.append(["bar", bar.ts_init])
    engine.add_strategy(Watch())
    price = lambda text: Price.from_str(text)
    engine.add_data([Bar(bar_type, price("1.0"), price("1.0"), price("1.0"), price("1.0"), Quantity.from_str("1.000"), end, end) for end in (60_000_000_000, 120_000_000_000)])
    engine.add_data([as_data(library_value("momentum", "BTCUSDT.OKX", "score", 0.25, 3, 60_000_000_000, 90_000_000_000))])
    engine.run()
    try:
        library_value("momentum", "BTCUSDT.OKX", "score", 0.25, 3, 90, 60)
        refused = False
    except ValueError:
        refused = True
    print(json.dumps({"seen": seen, "refused": refused, "roundtrip": LibraryValue.from_dict(library_value("a", "b", "c", 1.5, 2, 1, 2).to_dict()).ts_init}))
''')


def test_a_value_reaches_the_strategy_between_the_bars_around_the_moment_it_became_available():
    done = subprocess.run([str(interpreter()), "-I", "-c", SCRIPT, str(QUANT)], capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stderr[-3000:]
    result = json.loads(done.stdout.strip().splitlines()[-1])
    assert result["seen"] == [["bar", 60_000_000_000], ["value", 90_000_000_000, "momentum", "BTCUSDT.OKX", 0.25, 3, 60_000_000_000], ["bar", 120_000_000_000]]
    assert result["refused"] and result["roundtrip"] == 2
