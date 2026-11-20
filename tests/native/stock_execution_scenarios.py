"""Identical stock orders and quotes through Backtest and live Sandbox."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'modules/quant'))

from native.stock_execution import instrument_info
from nautilus_trader.model import (
    ClientOrderId,
    Currency,
    Equity,
    InstrumentId,
    OrderSide,
    Price,
    Quantity,
    QuoteTick,
    StrategyId,
    Symbol,
    TriggerType,
    Venue,
)
from nautilus_trader.trading import Strategy, StrategyConfig

BASE = 1_704_153_600_000
IID = InstrumentId.from_str('STOCK.SIM')
SPEC = json.loads(sys.argv[1])


def stock_item():
    sessions = []
    for day, text in ((0, '2024-01-02'), (1, '2024-01-03')):
        sessions.append({'sessionDate': text, 'openAt': BASE + day * 86_400_000,
            'closeAt': BASE + day * 86_400_000 + 3_600_000, 'rawLower': '90', 'rawUpper': '110',
            'lower': '90', 'upper': '110', 'rawPriceIncrement': '.01', 'priceIncrement': '.01',
            'priceMap': {'scale': '1', 'offset': '0', 'maxError': '0'},
            'rawVolume': '1000', 'adjustedVolume': '1000', 'rawOHLC': ['100'] * 4, 'adjustedOHLC': ['100'] * 4})
    if SPEC.get('noLimits'):
        for session in sessions:
            for key in ('rawLower', 'rawUpper', 'lower', 'upper'):
                session[key] = None
    return {'id': str(IID), 'type': 'equity', 'symbol': 'STOCK', 'currency': 'USD', 'pricePrecision': 2,
        'stockCommission': SPEC.get('commission', {'rate': '.0003', 'perShare': '0', 'minimum': '5', 'maximumRate': None}),
        'stock': {'orderSize': {'minimum': SPEC.get('minimum', 100), 'increment': SPEC.get('increment', 100), 'oddLotSellAll': True},
            'settlement': {'sellAfterTradingDays': SPEC.get('resale', 1)}, 'sessions': sessions,
            'taxes': SPEC.get('taxes', [{'effectiveFrom': '2023-08-28', 'effectiveUntil': None, 'buyRate': '0', 'sellRate': '.0005'}])}}


item = stock_item()
if SPEC.get('rebuild'):
    item['rebuildSeed'] = {'at': BASE * 1_000_000, 'price': '100'}
info = instrument_info(item, state=SPEC.get('state'), rebuild_orders=SPEC.get('rebuild', []))
if SPEC.get('rebuild'):
    parsed = json.loads(info['execution_conditions'])
    parsed['rebuildSeed'] = {'at': BASE * 1_000_000, 'price': '100'}
    info['execution_conditions'] = json.dumps(parsed)
instrument = Equity(IID, Symbol('STOCK'), Currency.from_str('USD'), 2, Price.from_str('.01'), 0, 0,
                    lot_size=Quantity.from_str('1'), info=info)


def quotes():
    result = []
    for index, row in enumerate(SPEC['quotes']):
        day, price, size = row
        stamp = (BASE + day * 86_400_000 + (index + 1) * 60_000) * 1_000_000
        result.append(QuoteTick(IID, Price.from_str(price), Price.from_str(price), Quantity.from_str(size), Quantity.from_str(size), stamp, stamp))
    return result


class Orders(Strategy):
    def __init__(self):
        super().__init__(StrategyConfig(strategy_id=StrategyId('STOCK-001')))
        self.index, self.sent = 0, {}
        self.fills, self.events = [], []

    def on_start(self):
        self.subscribe_quotes(IID)

    def on_quote(self, quote):
        for action in SPEC['actions']:
            if action['at'] != self.index:
                continue
            if action['kind'] == 'modify':
                self.modify_order(self.sent[action['id']].client_order_id, quantity=Quantity.from_str(action['quantity']))
                continue
            args = {'instrument_id': IID, 'order_side': OrderSide.from_str(action['side']),
                    'quantity': Quantity.from_str(action['quantity']), 'client_order_id': ClientOrderId(action['id']),
                    'tags': action.get('tags')}
            if 'price' in action:
                args['price'] = Price.from_str(action['price'])
            if 'trigger' in action:
                args['trigger_price'] = Price.from_str(action['trigger'])
                args['trigger_type'] = TriggerType.BID_ASK
            if action.get('reduceOnly'):
                args['reduce_only'] = True
            order = getattr(self.order_factory, action['kind'])(**args)
            self.sent[action['id']] = order
            self.submit_order(order)
        self.index += 1

    def on_order_filled(self, event):
        self.fills.append({'id': str(event.client_order_id), 'side': str(event.order_side),
            'quantity': str(event.last_qty), 'price': str(event.last_px), 'fee': str(event.commission.as_decimal()), 'at': event.ts_event})

    def on_order_event(self, event):
        self.events.append({'id': str(event.client_order_id), 'kind': type(event).__name__, 'reason': str(getattr(event, 'reason', ''))})

    on_order_modify_rejected = on_order_event


def snapshot(cache, strategy):
    account = cache.account_for_venue(Venue('SIM'))
    return {'balance': str(account.balance_total(Currency.from_str('USD')).as_decimal()), 'fills': strategy.fills,
        'events': strategy.events, 'orders': [{'id': str(order.client_order_id), 'status': str(order.status),
        'quantity': str(order.quantity), 'filled': str(order.filled_qty)} for order in cache.orders()],
        'state': json.loads(cache.get('trading-conditions/state/' + str(IID)))}


def backtest():
    from nautilus_trader.backtest import BacktestEngine, BacktestEngineConfig
    from nautilus_trader.common import LoggerConfig
    from nautilus_trader.execution import FixedFeeModel
    from nautilus_trader.model import AccountType, Money, OmsType
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggerConfig(bypass_logging=True, print_config=False), bypass_logging=True))
    try:
        engine.add_venue(Venue('SIM'), OmsType.NETTING, AccountType.CASH, [Money.from_str(SPEC.get('funds', '100000') + ' USD')],
                         liquidity_consumption=True, allow_cash_borrowing=False, reject_stop_orders=False, fee_model=FixedFeeModel(Money.from_str('0 USD')))
        engine.add_instrument(instrument)
        strategy = Orders()
        engine.add_strategy(strategy)
        engine.add_data(quotes())
        engine.run()
        return snapshot(engine.cache, strategy)
    finally:
        engine.dispose()


def sandbox():
    import asyncio

    from nautilus_trader.adapters.sandbox import (
        SandboxExecutionClientConfig,
        SandboxExecutionClientFactory,
    )
    from nautilus_trader.common import Environment, LoggerConfig
    from nautilus_trader.config import DataClientConfig
    from nautilus_trader.execution import FixedFeeModel
    from nautilus_trader.live import LiveNode
    from nautilus_trader.live.clients import DataClient, DataClientFactory
    from nautilus_trader.model import AccountType, Money, OmsType, TraderId
    box = {}

    class Feed(DataClient):
        def __init__(self, *, name, config, cache, clock):
            super().__init__(name=name, config=config, cache=cache, clock=clock, venue=Venue('SIM'))
            self.subscribed = asyncio.Event()
            self.task = None

        async def _connect(self):
            self._handle_instrument(instrument)
            self.task = self.create_task(self.stream())

        async def _disconnect(self):
            self.task.cancel()

        async def _subscribe_quotes(self, command):
            self.subscribed.set()

        async def _unsubscribe_quotes(self, command):
            pass

        async def stream(self):
            await self.subscribed.wait()
            for value in quotes():
                self._handle_data(value)
                await asyncio.sleep(.1)
            await asyncio.sleep(.2)
            box['handle'].stop()

    class Factory(DataClientFactory):
        @staticmethod
        def create(*, name, config, cache, clock):
            return Feed(name=name, config=config, cache=cache, clock=clock)

    spec = SandboxExecutionClientConfig(venue=Venue('SIM'), starting_balances=[Money.from_str(SPEC.get('funds', '100000') + ' USD')],
        account_type=AccountType.CASH, oms_type=OmsType.NETTING, liquidity_consumption=True, reject_stop_orders=False, fee_model=FixedFeeModel(Money.from_str('0 USD')))
    builder = LiveNode.builder('Stock conditions', TraderId('STOCK-001'), Environment.SANDBOX).with_reconciliation(False)
    builder = builder.with_logging(LoggerConfig(bypass_logging=True, print_config=False)).with_delay_post_stop_secs(0).with_delay_shutdown_secs(0)
    node = builder.add_data_client('SIM', Factory(), DataClientConfig()).add_simulated_exec_client('SIM', SandboxExecutionClientFactory(), spec).build()
    try:
        strategy = Orders()
        node.add_strategy(strategy)
        box['handle'] = node.handle()
        node.run()
        return snapshot(node.cache, strategy)
    finally:
        node.dispose()


print(json.dumps(backtest() if SPEC['mode'] == 'backtest' else sandbox()))
