"""Cross-listing index stocks spend one simulated cash balance."""
import json
import os
import subprocess
from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest

from tests.fixtures.quant_index import frozen_index
from tests.fixtures.strategy.engine import sessions_file
from tests.fixtures.strategy.instruments import stock_item
from tests.native.test_nautilus_fees import interpreter

pytestmark = pytest.mark.skipif(not os.environ.get('TALYX_NATIVE_SMOKE'), reason='Set TALYX_NATIVE_SMOKE=1')
QUANT = Path('modules/quant').resolve()


@pytest.mark.parametrize('indexed', [False, True])
def test_cross_listing_stocks_cannot_spend_the_capital_twice(tmp_path, indexed):
    source, output = tmp_path/'source', tmp_path/'output'
    source.mkdir()
    output.mkdir()
    (source/'strategy.py').write_text('''
import talyx_quant as q
from nautilus_trader.model import OrderSide, Quantity
class BuyBoth(q.Strategy):
    def __init__(self):
        super().__init__()
        self.seen, self.sent = set(), False
        self.denials = []
        self.samples = []
    def on_order_denied(self, event):
        self.denials.append(event.reason)
    def reports(self, engine, frame):
        import pandas as pd
        return {'Denials': pd.DataFrame({'reason': self.denials}), 'Universe': pd.DataFrame(self.samples)}
    def on_start(self):
        for kind in self.bar_types:
            self.subscribe_bars(kind)
    def on_bar(self, bar):
        if not self.samples:
            assert self.equity() == 10000
        moment = bar.ts_init // 1_000_000
        if not self.samples or self.samples[-1]['at'] != moment:
            selected = self.universe()
            count = len(selected)
            selected.clear()
            first = self.samples[0]['at'] if self.samples else moment
            past = len(self.universe(first))
            self.samples.append({'at': moment, 'count': count, 'again': len(self.universe()), 'past': past})
        self.seen.add(str(bar.bar_type.instrument_id))
        if len(self.seen) == 2 and not self.sent:
            self.sent = True
            for instrument in self.instrument_ids:
                self.submit_order(self.order_factory.market(instrument, OrderSide.BUY, Quantity.from_str('100')))
''',encoding='utf8')
    instruments, rows, kinds = [], [], []
    for symbol in ['AAA-XNAS','BBB-XNYS']:
        item = deepcopy(stock_item())
        item.update(id=symbol+'.SIM_USD',symbol=symbol)
        instruments.append(item)
        kind = item['id']+'-1-DAY-LAST-EXTERNAL'
        kinds.append(kind)
        for session in item['stock']['sessions'][:2]:
            rows.append({'instrument':item['id'],'barType':kind,
                **dict(zip(('open','high','low','close'),session['adjustedOHLC'],strict=True)),
                'volume':session['adjustedVolume'],'ts_event':session['closeAt']*1_000_000,'ts_init':session['closeAt']*1_000_000})
    bars = tmp_path/'bars.parquet'
    pd.DataFrame(rows).to_parquet(bars,index=False)
    config = {'engine':'nautilus','task':'event','dataType':'bars','timezone':'UTC','priceBasis':'split_dividend',
        'instruments':instruments,'venues':[{'name':'SIM_USD','accountType':'CASH','omsType':'NETTING','balances':['10000 USD'],
            'fees':{'type':'instrument_conditions'},'leverage':1}], 'strategy':{'kind':'object','module':'strategy'}}
    invocation = {'runId':'shared-index-cash','title':'Index cash','workbench':'strategy','entry':'strategy.py','source':str(source),
        'output':str(output),'library':None,'materials':[{'role':'market_data','path':str(bars)}],'mode':'run','operation':'strategy.backtest',
        'options':{},'sourceRefs':[],'annualization':252,'barTypes':kinds,'params':{},'backtest':config,'recipeRef':None,
        'libraryValues':{'path':None,'items':[]},'models':[],'index':None}
    invocation['stockSessions'] = sessions_file(tmp_path, instruments)
    if indexed:
        closes = sorted({int(row['ts_init'])//1_000_000 for row in rows})
        a, b = [{'instrumentId': item['id']} for item in instruments]
        members = [{'assetId': item['instrumentId'], 'instrumentId': item['instrumentId'], 'symbol': item['instrumentId']} for item in [a, b]]
        invocation['index'] = {'selection': {}, 'since': 0, 'until': closes[-1], 'limitation': 'Free source', 'exclusions': [],
            'instruments': [a, b], 'nativeInstruments': {item['id']: item['id'] for item in instruments},
            'intervals': [{'since': 0, 'until': closes[0], 'members': members[:1]},
                          {'since': closes[0]+1, 'until': closes[-1], 'members': members}]}
        invocation['index'] = frozen_index(invocation['index'])
    path = tmp_path/'invocation.json'
    path.write_text(json.dumps(invocation),encoding='utf8')
    done = subprocess.run([str(interpreter()),'-B',str(QUANT/'native/object_worker.py'),str(path)],cwd=source,
        capture_output=True,text=True,encoding='utf8',timeout=60)
    result = json.loads((output/'result-index.json').read_text(encoding='utf8'))
    assert result['status'] == 'completed', (result.get('failure'),done.stderr[-2000:])
    def table(title):
        artifact = next(item for item in result['artifacts'] if item['title'] == title)
        return pd.read_parquet(output/artifact['name'])
    fills, orders = table('Fills'), table('Orders')
    assert len(fills) == 1 and len(orders) == 2
    denied = orders[orders['status'] == 'DENIED']
    assert len(denied) == 1
    reasons = ' '.join(table('Denials')['reason']).lower()
    assert any(word in reasons for word in ['cash','balance','funds'])
    universe = table('Universe')
    assert universe['count'].tolist() == ([1, 2] if indexed else [2, 2])
    assert universe['again'].tolist() == universe['count'].tolist()
    assert universe['past'].tolist() == ([1, 1] if indexed else [2, 2])
