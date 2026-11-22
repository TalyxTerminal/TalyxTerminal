"""Preset order sizing ignores quantities the native instrument cannot represent."""
from __future__ import annotations

import math
import sys
from types import ModuleType, SimpleNamespace

import pandas as pd
import pytest

from modules.quant import talyx_quant
from modules.quant.native.strategy_presets import PRESETS


class Instrument:
    id = 'A'

    def __init__(self, increment):
        self.size_increment = SimpleNamespace(as_double=lambda: increment)
        self.calls = []
        self.increment = increment

    def make_qty(self, amount, *, round_down):
        assert round_down
        assert amount >= self.increment
        self.calls.append(amount)
        return math.floor(amount / self.increment) * self.increment


def preset(monkeypatch, kind, increment):
    q = ModuleType('talyx_quant')
    q.Strategy = type('Strategy', (), {})
    q.library_param = lambda _name, _default, **_kw: 'signal'
    q.param = lambda _name, default, **_kw: default
    q.choice = lambda _name, default, _options, **_kw: default
    q.tradable_quantity = talyx_quant.tradable_quantity
    model = ModuleType('nautilus_trader.model')
    model.OrderSide = SimpleNamespace(BUY='BUY', SELL='SELL')
    monkeypatch.setitem(sys.modules, 'talyx_quant', q)
    monkeypatch.setitem(sys.modules, 'nautilus_trader.model', model)
    scope = {}
    exec(PRESETS[kind]['source'], scope)
    strategy = scope[{'ranked': 'RankedPortfolio', 'rebalance': 'WeightRebalance', 'signal': 'SignalTrader'}[kind]]()
    instrument = Instrument(increment)
    strategy.cache = SimpleNamespace(instrument=lambda _identity: instrument)
    strategy.instrument_ids = ['A']
    strategy.instrument_id = 'A'
    strategy.by_name = {'A': 'A'}
    strategy.universe = lambda at: list(strategy.by_name.values())
    strategy.is_tradable = lambda *args: True
    strategy.last_close = lambda _identity: 1
    strategy.open_orders = lambda: []
    strategy.orders = []
    strategy.submit_order = strategy.orders.append
    strategy.order_factory = SimpleNamespace(market=lambda identity, side, amount: (identity, side, amount))
    return strategy, instrument


@pytest.mark.parametrize('kind', ['ranked', 'rebalance'])
@pytest.mark.parametrize('change', [0, .05, -.05, .1, -.1, .25])
def test_rebalance_filters_substep_changes_before_native_rounding(monkeypatch, kind, change):
    strategy, instrument = preset(monkeypatch, kind, .1)
    strategy.equity = lambda: 1
    strategy.position_size = lambda _identity: max(0, -change)
    weight = max(0, change)
    if kind == 'ranked':
        strategy.trade_to({'A': weight})
    else:
        strategy.rebalance(1, {'A': SimpleNamespace(value=weight, version=1)})
    if abs(change) < .1:
        assert not instrument.calls and not strategy.orders
    else:
        assert instrument.calls == [abs(change)]
        assert strategy.orders == [('A', 'BUY' if change > 0 else 'SELL', .1 if abs(change) == .1 else .2)]


@pytest.mark.parametrize('amount', [0, .5, 1, 2.5])
def test_signal_filters_substep_stakes_before_native_rounding(monkeypatch, amount):
    strategy, instrument = preset(monkeypatch, 'signal', 1)
    strategy.position_size = lambda _identity: 0
    strategy.stake = 1
    strategy.equity = lambda: amount
    data = SimpleNamespace(data=SimpleNamespace(name='signal', field='x', value=1, version=1, ts_init=2, ts_event=1))
    strategy.on_data(data)
    if amount < 1:
        assert not instrument.calls and not strategy.orders
        assert strategy.decisions[-1]['action'] == 'skipped: the stake is below one order step'
    else:
        assert len(instrument.calls) == 1
        assert strategy.orders == [('A', 'BUY', math.floor(amount))]


def test_missing_weight_keeps_the_entire_existing_portfolio(monkeypatch):
    strategy, instrument = preset(monkeypatch, 'rebalance', .1)
    strategy.by_name['B'] = 'B'
    strategy.rebalance(1, {'A': SimpleNamespace(value=1, version=1)})
    assert strategy.rebalances == [{'ts_available': 1, 'state': 'skipped', 'reason': 'No weight for B', 'version': 1}]
    assert not instrument.calls and not strategy.orders


def test_ranked_index_ignores_unpriced_member_and_accepts_the_remaining_section(monkeypatch):
    strategy, _ = preset(monkeypatch, 'ranked', .1)
    strategy.instrument_ids = ['A', 'B']
    strategy.by_name = {'A':'A', 'B':'B'}
    strategy.universe = lambda at: ['A']
    strategy.top_n = 1
    captured = []
    strategy.rebalance = lambda moment, section: captured.append((moment, section))
    value = SimpleNamespace(name='signal', field='x', instrument_id='A', value=1, version=1, ts_init=2, batch_end=True)
    strategy.on_data(SimpleNamespace(data=value))
    assert captured == [(2, {'A':value})] and not strategy.rebalances


def test_ranked_index_never_ranks_an_unpriced_member_even_if_a_signal_exists(monkeypatch):
    strategy, _ = preset(monkeypatch, 'ranked', .1)
    strategy.instrument_ids = ['A','B']
    strategy.universe = lambda at: ['A']
    strategy.top_n = 1
    captured = []
    strategy.rebalance = lambda moment, section: captured.append(set(section))
    for name in ['B','A']:
        strategy.on_data(SimpleNamespace(data=SimpleNamespace(name='signal', field='x', instrument_id=name,
            value=100 if name == 'B' else 1, version=1, ts_init=2, batch_end=name == 'A')))
    assert captured == [{'A'}]


def test_published_allocations_include_explicit_zero_weights(monkeypatch):
    class Portfolio:
        observations = pd.to_datetime(['2026-01-02'], utc=True)
        weights_dict = {'A': 1., 'B': 0.}
    skfolio = ModuleType('skfolio')
    skfolio.Portfolio = Portfolio
    skfolio.MultiPeriodPortfolio = type('MultiPeriodPortfolio', (), {})
    native = ModuleType('native')
    native.reports = SimpleNamespace()
    monkeypatch.setitem(sys.modules, 'skfolio', skfolio)
    monkeypatch.setitem(sys.modules, 'native', native)
    monkeypatch.setattr(talyx_quant._Run, 'mode', 'values')
    monkeypatch.setattr(talyx_quant._Run, 'publications', [])
    talyx_quant.report.allocation(Portfolio())
    values = talyx_quant._Run.publications[0]['values']
    assert dict(zip(values['instrument'], values['value'], strict=True)) == {'A': 1., 'B': 0.}


def test_a_stock_order_carries_whole_lots_from_its_execution_terms():
    import json
    stock = SimpleNamespace(id="STOCK.XNYS", info={"execution_conditions": json.dumps({"rules": {"minimum": "100", "increment": "100"}})}, size_increment=SimpleNamespace(as_double=lambda: 1.0),
                            make_qty=lambda amount, *, round_down: amount)
    assert talyx_quant.tradable_quantity(stock, 12566.4) == 12500
    assert talyx_quant.tradable_quantity(stock, 99.0) is None
    plain = SimpleNamespace(id="BTCUSDT-PERP.BINANCE", info={}, size_increment=SimpleNamespace(as_double=lambda: 0.001), make_qty=lambda amount, *, round_down: amount)
    assert talyx_quant.tradable_quantity(plain, 0.5) == 0.5 and talyx_quant.tradable_quantity(plain, 0.0004) is None


def test_orders_from_users_code_are_rounded_to_whole_lots_and_odd_shares_go_with_the_rest():
    """rebalance_to and order_target_weight send these: an A share buys whole lots of 100, sells whole lots, and once less than a lot would
    be left sells all it holds (odd shares go together), as JoinQuant and RiceQuant do; an order under one lot sends nothing."""
    import json
    talyx_quant._lot_rules.pop("600000.XSHG", None)
    stock = SimpleNamespace(id="600000.XSHG", info={"execution_conditions": json.dumps({"rules": {"minimum": "100", "increment": "100"}})},
                            make_qty=lambda amount, *, round_down=False: amount)
    assert talyx_quant._order_quantity(stock, 1234.0, 0.0) == 1200
    assert talyx_quant._order_quantity(stock, 99.0, 0.0) is None
    assert talyx_quant._order_quantity(stock, -730.0, 1250.0) == 700  # 550 left: whole lots taken
    assert talyx_quant._order_quantity(stock, -1200.0, 1250.0) == 1250  # 50 would be left: the odd shares go too
    assert talyx_quant._order_quantity(stock, -1250.0, 1250.0) == 1250
    plain = SimpleNamespace(id="BTCUSDT-PERP.BINANCE", info={}, make_qty=lambda amount, **_kw: SimpleNamespace(as_double=lambda: amount, value=amount))
    assert talyx_quant._order_quantity(plain, 0.5).value == 0.5
