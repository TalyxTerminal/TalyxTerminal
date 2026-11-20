"""Stock constraints agree on Backtest and Sandbox, including partial fees."""
import json
import os
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from tests.native.test_nautilus_fees import interpreter

pytestmark = pytest.mark.skipif(not os.environ.get('TALYX_NATIVE_SMOKE'), reason='Set TALYX_NATIVE_SMOKE=1')
SCENARIOS = Path(__file__).with_name('stock_execution_scenarios.py')


def scenario(mode, quotes, actions, **extra):
    result = subprocess.run([str(interpreter()), '-B', str(SCENARIOS), json.dumps({'mode': mode, 'quotes': quotes, 'actions': actions, **extra})],
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr[-4000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


def action(at, side, quantity, identity, *, kind='market', **extra):
    return {'at': at, 'kind': kind, 'side': side, 'quantity': str(quantity), 'id': identity, **extra}


def test_partial_fills_charge_one_order_minimum_and_direction_tax_identically():
    quotes = [[day, '100.00', str(size)] for day in [0, 1] for size in [50, 100, 150, 200]]
    actions = [action(0, 'BUY', 200, 'buy', kind='limit', price='100.00'), action(4, 'SELL', 200, 'sell', kind='limit', price='100.00')]
    results = [scenario(mode, quotes, actions) for mode in ['backtest', 'sandbox']]
    normalized = [[{key: value for key, value in fill.items() if key != 'at'} for fill in result['fills']] for result in results]
    assert normalized[0] == normalized[1]
    assert results[0]['state'] == results[1]['state']
    for result in results:
        assert Decimal(result['balance']) == 99978
        assert [Decimal(fill['fee']) for fill in result['fills'] if fill['id'] == 'buy'] == [5, 0, 1]
        assert result['state']['quantity'] == '0'
        assert Decimal(result['state']['bills']['sell']['taxPaid']) == 10


@pytest.mark.parametrize('mode', ['backtest', 'sandbox'])
def test_t1_blocks_same_day_market_and_unlocks_on_next_verified_session(mode):
    result = scenario(mode, [[0, '100.00', '1000'], [0, '100.00', '1001'], [1, '100.00', '1002']],
        [action(0, 'BUY', 100, 'buy'), action(1, 'SELL', 100, 'same-day'), action(2, 'SELL', 100, 'next-day')])
    assert [fill['id'] for fill in result['fills']] == ['buy', 'next-day']
    assert next(order for order in result['orders'] if order['id'] == 'same-day')['status'] in {'REJECTED', 'CANCELED'}
    assert Decimal(result['balance']) == 99985 and result['state']['frozen'] == '0'


@pytest.mark.parametrize('mode', ['backtest', 'sandbox'])
@pytest.mark.parametrize('minimum,increment,seed,invalid,valid', [(100, 100, 100, 150, 200), (200, 1, 200, 199, 201)])
def test_minimum_step_and_a_user_rebuild_tag_cannot_bypass_them(mode, minimum, increment, seed, invalid, valid):
    result = scenario(mode, [[0, '100.00', '1000']] * 3,
        [action(0, 'BUY', seed, 'seed'), action(1, 'BUY', invalid, 'invalid', tags=['rebuild']), action(2, 'BUY', valid, 'valid')],
        minimum=minimum, increment=increment)
    assert not any(fill['id'] == 'invalid' for fill in result['fills'])
    assert any(event['id'] == 'invalid' and event['kind'] == 'OrderRejected' for event in result['events'])
    assert {fill['id'] for fill in result['fills']} == {'seed', 'valid'}


@pytest.mark.parametrize('mode', ['backtest', 'sandbox'])
def test_amend_cannot_change_a_valid_order_to_an_invalid_step(mode):
    result = scenario(mode, [[0, '100.00', '1000']] * 3,
        [action(0, 'BUY', 100, 'seed'), action(1, 'BUY', 200, 'resting', kind='limit', price='99.00'),
         {'at': 2, 'kind': 'modify', 'id': 'resting', 'quantity': '150'}])
    assert any(event['kind'] == 'OrderModifyRejected' for event in result['events'])
    assert next(order for order in result['orders'] if order['id'] == 'resting')['quantity'] == '200'


@pytest.mark.parametrize('mode', ['backtest', 'sandbox'])
def test_commission_enters_the_risk_balance_check(mode):
    result = scenario(mode, [[0, '100.00', '1000']], [action(0, 'BUY', 100, 'all-in')], funds='10000')
    assert result['fills'] == [] and Decimal(result['balance']) == 10000
    assert any(event['kind'] == 'OrderDenied' for event in result['events'])


@pytest.mark.parametrize('mode', ['backtest', 'sandbox'])
def test_rebuild_keeps_frozen_shares_and_odd_lot_all_sale_then_unlocks(mode):
    state = {'sessionDate': '2024-01-02', 'lastMarketAt': 1704153600000000000, 'quantity': '150', 'frozen': '100', 'bills': {}}
    result = scenario(mode, [[0, '100.00', '1000'], [0, '100.00', '1001'], [1, '100.00', '1002']],
        [action(0, 'BUY', 150, 'internal'), action(1, 'SELL', 150, 'sell-all', kind='limit', price='100.00')],
        funds='16000', state=state, rebuild=[{'orderId': 'internal', 'side': 'BUY', 'quantity': '150'}])
    assert Decimal(result['fills'][0]['fee']) == 0
    sells = [fill for fill in result['fills'] if fill['id'] == 'sell-all']
    assert [Decimal(fill['quantity']) for fill in sells] == [50, 100]
    assert sum(Decimal(fill['fee']) for fill in sells) == Decimal('12.5')
    assert Decimal(result['balance']) == Decimal('15987.5') and result['state']['quantity'] == '0'


@pytest.mark.parametrize('mode', ['backtest', 'sandbox'])
@pytest.mark.parametrize('kind,trigger', [('market', None), ('limit', None), ('stop_market', '105.00'), ('stop_limit', '105.00'), ('market_if_touched', '115.00'), ('limit_if_touched', '110.00')])
def test_upper_limit_never_buys_and_conditional_orders_survive_until_fillable(mode, kind, trigger):
    args = {'price': '110.00'} if kind in {'limit', 'stop_limit', 'limit_if_touched'} else {}
    if trigger:
        args['trigger'] = trigger
    result = scenario(mode, [[0, '110.00', '1000'], [1, '109.00', '1001']], [action(0, 'BUY', 100, 'buy', kind=kind, **args)])
    assert any(order['id'] == 'buy' for order in result['orders'])
    assert not any(Decimal(fill['price']) >= 110 for fill in result['fills'])
    if kind in {'stop_market', 'market_if_touched'}:
        assert result['fills'] and sum(Decimal(fill['quantity']) for fill in result['fills']) == 100
    elif kind == 'market':
        assert result['fills'] == []


@pytest.mark.parametrize('mode', ['backtest', 'sandbox'])
def test_restored_partial_order_keeps_paid_minimum_and_direction_tax_accrual(mode):
    state = {'sessionDate': '2024-01-02', 'lastMarketAt': 1704153600000000000, 'quantity': '100', 'frozen': '0',
        'bills': {'protection': {'quantity': '50', 'sizeQuantity': '50', 'notional': '5000', 'commissionPaid': '5', 'taxAccrued': '2.5', 'taxPaid': '2.5'}}}
    result = scenario(mode, [[0, '100.00', '1000'], [0, '100.00', '1001']],
        [action(0, 'BUY', 100, 'internal'), action(1, 'SELL', 100, 'protection', kind='limit', price='100.00')],
        funds='11000', state=state, rebuild=[{'orderId': 'internal', 'side': 'BUY', 'quantity': '100'}])
    assert sum(Decimal(fill['fee']) for fill in result['fills']) == 5
    assert Decimal(result['balance']) == 10995
    assert Decimal(result['state']['bills']['protection']['commissionPaid']) == 5
    assert Decimal(result['state']['bills']['protection']['taxPaid']) == Decimal('7.5')


@pytest.mark.parametrize('mode', ['backtest', 'sandbox'])
@pytest.mark.parametrize('kind,trigger', [('market', None), ('limit', None), ('stop_market', '95.00'), ('stop_limit', '95.00'), ('market_if_touched', '85.00'), ('limit_if_touched', '90.00')])
def test_lower_limit_never_sells_and_protection_survives_until_fillable(mode, kind, trigger):
    state = {'sessionDate': '2024-01-02', 'lastMarketAt': 1704153600000000000, 'quantity': '100', 'frozen': '0', 'bills': {}}
    args = {'price': '90.00'} if kind in {'limit', 'stop_limit', 'limit_if_touched'} else {}
    if trigger:
        args['trigger'] = trigger
    result = scenario(mode, [[0, '100.00', '1000'], [0, '90.00', '1001'], [1, '91.00', '1002']],
        [action(0, 'BUY', 100, 'internal'), action(1, 'SELL', 100, 'protect', kind=kind, **args)],
        state=state, rebuild=[{'orderId': 'internal', 'side': 'BUY', 'quantity': '100'}])
    sells = [fill for fill in result['fills'] if fill['id'] == 'protect']
    assert any(order['id'] == 'protect' for order in result['orders'])
    assert not any(Decimal(fill['price']) <= 90 for fill in sells)
    if kind in {'stop_market', 'market_if_touched'}:
        assert sum(Decimal(fill['quantity']) for fill in sells) == 100
    elif kind == 'market':
        assert sells == []


@pytest.mark.parametrize('mode', ['backtest', 'sandbox'])
def test_same_day_protection_preserves_frozen_shares_until_next_session(mode):
    result = scenario(mode, [[0, '100.00', '1000'], [0, '95.00', '1001'], [1, '96.00', '1002']],
        [action(0, 'BUY', 100, 'buy'), action(1, 'SELL', 100, 'protect', kind='stop_market', trigger='98.00', reduceOnly=True)])
    sells = [fill for fill in result['fills'] if fill['id'] == 'protect']
    assert [Decimal(fill['price']) for fill in sells] == [96]
    assert result['state']['quantity'] == result['state']['frozen'] == '0'


@pytest.mark.parametrize('mode', ['backtest', 'sandbox'])
def test_per_share_commission_cap_and_same_session_resale(mode):
    result = scenario(mode, [[0, '.50', '1000'], [0, '.50', '1001']],
        [action(0, 'BUY', 100, 'buy'), action(1, 'SELL', 100, 'sell')], resale=0, noLimits=True, taxes=[],
        commission={'rate': '0', 'perShare': '.005', 'minimum': '1', 'maximumRate': '.01'})
    assert [Decimal(fill['fee']) for fill in result['fills']] == [Decimal('.5'), Decimal('.5')]
    assert Decimal(result['balance']) == 99999
