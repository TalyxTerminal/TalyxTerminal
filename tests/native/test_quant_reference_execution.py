"""MARK and INDEX ranges remain executable in strategy backtests."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.fixtures.strategy.instruments import PERP, PERP_VENUE
from tests.native.test_nautilus_fees import interpreter

pytestmark = pytest.mark.skipif(not os.environ.get("TALYX_NATIVE_SMOKE"), reason="Set TALYX_NATIVE_SMOKE=1")


@pytest.mark.parametrize('basis', ['mark', 'index'])
def test_backtest_reference_range_precedes_the_authored_close(basis):
    config = {'dataType': 'bars', 'instruments': [{**PERP, 'pricePrecision': 2, 'priceIncrement': '0.10'}],
              'venues': [PERP_VENUE], 'referencePrices': ['mark', 'index']}
    completed = subprocess.run([str(interpreter()), '-B', str(Path(__file__).with_name('reference_price_scenarios.py')), basis, json.dumps(config)],
        capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result['counts'] == [1, 1, 2]
    assert result['fills'][-1]['side'] == 'SELL' and result['fills'][-1]['price'] == '100.00'

