"""A stock that starts trading during the run (listed, spun off or renamed) trades from its first bar like any other."""
import json
import os
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from tests.fixtures.strategy.engine import sessions_file
from tests.native.test_nautilus_fees import interpreter

pytestmark = pytest.mark.skipif(not os.environ.get('TALYX_NATIVE_SMOKE'), reason='Set TALYX_NATIVE_SMOKE=1')
QUANT = Path('modules/quant').resolve()
DAY = 86_400_000
BASE = 1704204000000  # 2024-01-02 14:30 UTC


def stock(symbol, first_day, days, *, ohlc=('100', '101', '99', '100'), step='.01', precision=2):
    """A plain stock (no split, no limits) with one session a day from `first_day` to `days - 1`."""
    sessions = [{'sessionDate': pd.Timestamp(BASE + day * DAY, unit='ms').strftime('%Y-%m-%d'), 'openAt': BASE + day * DAY,
                 'closeAt': BASE + day * DAY + 23_400_000, 'rawOHLC': list(ohlc), 'adjustedOHLC': list(ohlc),
                 'rawVolume': '100000', 'adjustedVolume': '100000', 'rawLower': None, 'rawUpper': None, 'lower': None, 'upper': None,
                 'rawPriceIncrement': step, 'priceIncrement': step, 'priceMap': {'scale': '1', 'offset': '0', 'maxError': '0'}}
                for day in range(first_day, days)]
    commission = {'rate': '0', 'perShare': '0', 'minimum': '0', 'maximumRate': None}
    return {'id': symbol + '.SIM_USD', 'type': 'equity', 'symbol': symbol, 'currency': 'USD', 'pricePrecision': precision, 'sizePrecision': 0,
            'priceIncrement': step, 'sizeIncrement': '1', 'stockCommission': commission,
            'stock': {'orderSize': {'minimum': 1, 'increment': 1, 'oddLotSellAll': False}, 'settlement': {'sellAfterTradingDays': 0},
                      'commission': commission, 'sessions': sessions, 'taxes': [], 'corporateActions': [], 'materialVersion': 'test', 'limitations': []}}


def buy_on_first_bar(tmp_path, instruments):
    """Runs a strategy that buys 10 of each stock at its first bar; the native orders report."""
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
    rows, kinds = [], []
    for item in instruments:
        kind = item['id'] + '-1-DAY-LAST-EXTERNAL'
        kinds.append(kind)
        for session in item['stock']['sessions']:
            rows.append({'instrument': item['id'], 'barType': kind, **dict(zip(('open', 'high', 'low', 'close'), session['adjustedOHLC'], strict=True)),
                         'volume': session['adjustedVolume'], 'ts_event': session['closeAt'] * 1_000_000, 'ts_init': session['closeAt'] * 1_000_000})
    bars = tmp_path / 'bars.parquet'
    pd.DataFrame(rows).to_parquet(bars, index=False)
    config = {'engine': 'nautilus', 'task': 'event', 'dataType': 'bars', 'timezone': 'UTC', 'priceBasis': 'split_dividend', 'instruments': instruments,
              'venues': [{'name': 'SIM_USD', 'accountType': 'CASH', 'omsType': 'NETTING', 'balances': ['100000 USD'], 'fees': {'type': 'instrument_conditions'}, 'leverage': 1}],
              'strategy': {'kind': 'object', 'module': 'strategy'}}
    invocation = {'runId': 'late-listing', 'title': 'Late listing', 'workbench': 'strategy', 'entry': 'strategy.py', 'source': str(source), 'output': str(output),
                  'library': None, 'materials': [{'role': 'market_data', 'path': str(bars)}], 'mode': 'run', 'operation': 'strategy.backtest', 'options': {},
                  'sourceRefs': [], 'annualization': 252, 'barTypes': kinds, 'params': {}, 'backtest': config, 'recipeRef': None,
                  'libraryValues': {'path': None, 'items': []}, 'models': [], 'index': None}
    invocation['stockSessions'] = sessions_file(tmp_path, instruments)
    path = tmp_path / 'invocation.json'
    path.write_text(json.dumps(invocation), encoding='utf8')
    done = subprocess.run([str(interpreter()), '-B', str(QUANT / 'native/object_worker.py'), str(path)], cwd=source, capture_output=True, text=True,
                          encoding='utf8', timeout=120)
    result = json.loads((output / 'result-index.json').read_text(encoding='utf8'))
    assert result['status'] == 'completed', (result.get('failure'), done.stderr[-2000:])
    return pd.read_parquet(output / next(item for item in result['artifacts'] if item['title'] == 'Orders')['name'])


def test_a_stock_listed_months_into_the_run_fills_its_orders(tmp_path):
    # LATE's first session is 100 days in, past the two months of sessions the venue is given at the start.
    orders = buy_on_first_bar(tmp_path, [stock('EARLY', 0, 110), stock('LATE', 100, 110)])
    assert sorted(orders['status']) == ['FILLED', 'FILLED'], orders[['instrument_id', 'status']].to_string()


def test_a_price_step_that_is_not_a_power_of_ten_fills_on_its_multiples(tmp_path):
    # A Hong Kong step of 0.05 under adjusted prices with four places: the cost-moved price must land on a multiple of the step, or the venue
    # skips the fill and the order stays submitted for the rest of the run.
    orders = buy_on_first_bar(tmp_path, [stock('STEP', 0, 5, ohlc=('85.0067', '85.4832', '82.2431', '82.8625'), step='0.0500', precision=4)])
    assert list(orders['status']) == ['FILLED'], orders[['instrument_id', 'status']].to_string()
    assert float(orders['avg_px'].iloc[0]) / 0.05 == pytest.approx(round(float(orders['avg_px'].iloc[0]) / 0.05)), orders['avg_px'].to_string()
