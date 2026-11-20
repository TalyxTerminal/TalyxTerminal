"""A stock that starts trading during the run (listed, spun off or renamed) trades from its first bar like any other."""
import json
import os
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from tests.native.test_nautilus_fees import interpreter

pytestmark = pytest.mark.skipif(not os.environ.get('TALYX_NATIVE_SMOKE'), reason='Set TALYX_NATIVE_SMOKE=1')
QUANT = Path('modules/quant').resolve()
DAY = 86_400_000
BASE = 1704204000000  # 2024-01-02 14:30 UTC


def stock(symbol, first_day, days):
    """A plain stock (no split, no limits) with one session a day from `first_day` to `days - 1`."""
    sessions = [{'sessionDate': pd.Timestamp(BASE + day * DAY, unit='ms').strftime('%Y-%m-%d'), 'openAt': BASE + day * DAY,
                 'closeAt': BASE + day * DAY + 23_400_000, 'rawOHLC': ['100', '101', '99', '100'], 'adjustedOHLC': ['100', '101', '99', '100'],
                 'rawVolume': '100000', 'adjustedVolume': '100000', 'rawLower': None, 'rawUpper': None, 'lower': None, 'upper': None,
                 'rawPriceIncrement': '.01', 'priceIncrement': '.01', 'priceMap': {'scale': '1', 'offset': '0', 'maxError': '0'}}
                for day in range(first_day, days)]
    commission = {'rate': '0', 'perShare': '0', 'minimum': '0', 'maximumRate': None}
    return {'id': symbol + '.SIM_USD', 'type': 'equity', 'symbol': symbol, 'currency': 'USD', 'pricePrecision': 2, 'sizePrecision': 0,
            'priceIncrement': '.01', 'sizeIncrement': '1', 'stockCommission': commission,
            'stock': {'orderSize': {'minimum': 1, 'increment': 1, 'oddLotSellAll': False}, 'settlement': {'sellAfterTradingDays': 0},
                      'commission': commission, 'sessions': sessions, 'taxes': [], 'corporateActions': [], 'materialVersion': 'test', 'limitations': []}}


def test_a_stock_listed_months_into_the_run_fills_its_orders(tmp_path):
    # LATE's first session is 100 days in, past the two months of sessions the venue is given at the start.
    source, output = tmp_path / 'source', tmp_path / 'output'
    source.mkdir()
    output.mkdir()
    (source / 'strategy.py').write_text('''
import talyx_quant as q
from nautilus_trader.model import OrderSide, Quantity
class BuyOnFirstBar(q.Strategy):
    def __init__(self):
        super().__init__()
        self.bought = set()
    def on_start(self):
        for kind in self.bar_types:
            self.subscribe_bars(kind)
    def on_bar(self, bar):
        instrument = bar.bar_type.instrument_id
        if instrument not in self.bought:
            self.bought.add(instrument)
            self.submit_order(self.order_factory.market(instrument, OrderSide.BUY, Quantity.from_str('10')))
''', encoding='utf8')
    instruments = [stock('EARLY', 0, 110), stock('LATE', 100, 110)]
    rows, kinds = [], []
    for item in instruments:
        kind = item['id'] + '-1-DAY-LAST-EXTERNAL'
        kinds.append(kind)
        for session in item['stock']['sessions']:
            rows.append({'instrument': item['id'], 'barType': kind, **dict(zip(('open', 'high', 'low', 'close'), session['adjustedOHLC'], strict=True)),
                         'volume': session['adjustedVolume'], 'ts_event': str(session['closeAt'] * 1_000_000), 'ts_init': str(session['closeAt'] * 1_000_000)})
    bars = tmp_path / 'bars.parquet'
    pd.DataFrame(rows).to_parquet(bars, index=False)
    config = {'engine': 'nautilus', 'task': 'event', 'dataType': 'bars', 'timezone': 'UTC', 'priceBasis': 'split_dividend', 'instruments': instruments,
              'venues': [{'name': 'SIM_USD', 'accountType': 'CASH', 'omsType': 'NETTING', 'balances': ['100000 USD'], 'fees': {'type': 'instrument_conditions'}, 'leverage': 1}],
              'strategy': {'kind': 'object', 'module': 'strategy'}}
    invocation = {'runId': 'late-listing', 'title': 'Late listing', 'workbench': 'strategy', 'entry': 'strategy.py', 'source': str(source), 'output': str(output),
                  'library': None, 'materials': [{'role': 'market_data', 'path': str(bars)}], 'mode': 'run', 'operation': 'strategy.backtest', 'options': {},
                  'sourceRefs': [], 'annualization': 252, 'barTypes': kinds, 'params': {}, 'backtest': config, 'recipeRef': None,
                  'libraryValues': {'path': None, 'items': []}, 'models': [], 'index': None}
    path = tmp_path / 'invocation.json'
    path.write_text(json.dumps(invocation), encoding='utf8')
    done = subprocess.run([str(interpreter()), '-B', str(QUANT / 'native/object_worker.py'), str(path)], cwd=source, capture_output=True, text=True,
                          encoding='utf8', timeout=120)
    result = json.loads((output / 'result-index.json').read_text(encoding='utf8'))
    assert result['status'] == 'completed', (result.get('failure'), done.stderr[-2000:])
    orders = pd.read_parquet(output / next(item for item in result['artifacts'] if item['title'] == 'Orders')['name'])
    assert sorted(orders['status']) == ['FILLED', 'FILLED'], orders[['instrument_id', 'status']].to_string()
