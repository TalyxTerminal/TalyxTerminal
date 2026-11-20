"""One all-in buy that the cash cannot pay the commission for; run by test_nautilus_fees.py in the strategy environment.

Usage: python nautilus_fee_scenarios.py <spot|equity|sandbox> <with-fee-info|no-fee-info>
Prints one JSON object: run error, order events and statuses, and the account's balances.
"""
import json
import sys
import threading
import time
from decimal import Decimal

from nautilus_trader.backtest import BacktestEngine, BacktestEngineConfig
from nautilus_trader.common import LoggerConfig
from nautilus_trader.execution import MakerTakerFeeModel
from nautilus_trader.model import (
    AccountType,
    Bar,
    BarType,
    Currency,
    CurrencyPair,
    Equity,
    InstrumentId,
    Money,
    OmsType,
    OrderSide,
    Price,
    Quantity,
    StrategyId,
    Symbol,
    Venue,
)
from nautilus_trader.trading import Strategy, StrategyConfig

KIND, INFO_MODE = sys.argv[1], sys.argv[2]
FEE = Decimal("0.001")
INFO = {"taker_fee": str(FEE)} if INFO_MODE == "with-fee-info" else None
TERMINAL = ("OrderFilled", "OrderDenied", "OrderRejected", "OrderCanceled")


def instrument(iid):
    if KIND == "equity":
        return Equity(iid, Symbol("AAA"), Currency.from_str("USD"), 2, Price.from_str("0.01"), 0, 0, lot_size=Quantity.from_str("1"), info=INFO)
    return CurrencyPair(iid, Symbol("BTCUSDT"), Currency.from_str("BTC"), Currency.from_str("USDT"), 2, 3,
                        Price.from_str("0.01"), Quantity.from_str("0.001"), 0, 0, info=INFO)


class AllIn(Strategy):
    def __init__(self, spec):
        iid, bar_type, cash_currency, price = spec
        super().__init__(StrategyConfig(strategy_id=StrategyId("ALLIN-001")))
        self.iid, self.bar_type, self.cash_currency, self.price = iid, bar_type, cash_currency, price
        self.sent, self.events, self.terminal_at, self.snapshot = False, [], None, None

    def on_start(self):
        if self.bar_type is None:
            self.subscribe_quotes(self.iid)
        else:
            self.subscribe_bars(self.bar_type)

    def trade(self):
        if self.sent:
            return
        self.sent = True
        cash = Decimal(str(self.cache.account_for_venue(self.iid.venue).balance_free(Currency.from_str(self.cash_currency)).as_double()))
        instrument = self.cache.instrument(self.iid)
        step = Decimal(str(instrument.size_increment))
        quantity = (cash / Decimal(str(self.price)) // step) * step
        self.submit_order(self.order_factory.market(self.iid, OrderSide.BUY, Quantity.from_str(str(quantity))))
        self.requested = float(quantity)

    def on_bar(self, bar):
        self.trade()

    def on_quote(self, quote):
        self.trade()

    def on_order_event(self, event):
        self.events.append(type(event).__name__)
        if type(event).__name__ in TERMINAL:
            self.terminal_at = time.time()
            self.snapshot = snapshot(self.cache, self.iid.venue)


def snapshot(cache, venue):
    account = cache.account_for_venue(venue)
    return {"balances": {str(c): str(m) for c, m in account.balances_total().items()},
            "orders": [{"status": str(o.status), "quantity": str(o.quantity), "filled": str(o.filled_qty)} for o in cache.orders()]}


def backtest():
    venue = Venue("SIM")
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggerConfig(bypass_logging=True, print_config=False), bypass_logging=True))
    fee_model = MakerTakerFeeModel(FEE, FEE)
    if KIND == "equity":
        iid, price, cash = InstrumentId.from_str("AAA.SIM"), 100.0, "USD"
        engine.add_venue(venue, OmsType.NETTING, AccountType.CASH, [Money.from_str("100000 USD")], base_currency=Currency.from_str("USD"),
                         fee_model=fee_model, allow_cash_borrowing=False)
    else:
        iid, price, cash = InstrumentId.from_str("BTCUSDT.SIM"), 30000.0, "USDT"
        engine.add_venue(venue, OmsType.NETTING, AccountType.CASH, [Money.from_str("100000 USDT")], fee_model=fee_model, allow_cash_borrowing=False)
    engine.add_instrument(instrument(iid))
    bar_type = BarType.from_str(f"{iid}-1-MINUTE-LAST-EXTERNAL")
    size_precision = 0 if KIND == "equity" else 3
    bars = []
    for i in range(1, 6):
        step_price = price * (1 + 0.001 * i)  # the fill happens above the close the order was sized on
        bars.append(Bar(bar_type, Price(step_price, 2), Price(step_price, 2), Price(step_price, 2), Price(step_price, 2),
                        Quantity(1000, size_precision), i * 60_000_000_000, i * 60_000_000_000))
    engine.add_data(bars)
    strategy = AllIn((iid, bar_type, cash, bars[0].close.as_double()))
    engine.add_strategy(strategy)
    error = None
    try:
        engine.run()
    except RuntimeError as exc:
        error = str(exc)[:300]
    return {"error": error, "events": strategy.events, "requested": strategy.requested, **snapshot(engine.cache, venue)}


def sandbox():
    import asyncio

    from nautilus_trader.adapters.sandbox import (
        SandboxExecutionClientConfig,
        SandboxExecutionClientFactory,
    )
    from nautilus_trader.common import Environment
    from nautilus_trader.config import DataClientConfig
    from nautilus_trader.live import LiveNode
    from nautilus_trader.live.clients import DataClient, DataClientFactory
    from nautilus_trader.model import QuoteTick, TraderId

    iid = InstrumentId.from_str("BTCUSDT.SBX")

    class Feed(DataClient):
        def __init__(self, *, name, config, cache, clock):
            super().__init__(name=name, config=config, cache=cache, clock=clock, venue=Venue("SBX"))
            self.quotes, self.task = False, None

        async def _connect(self):
            self._handle_instrument(instrument(iid))
            self.task = self.create_task(self.stream())

        async def _disconnect(self):
            self.task.cancel()

        async def _subscribe_quotes(self, command):
            self.quotes = True

        async def _unsubscribe_quotes(self, command):
            self.quotes = False

        async def stream(self):
            while True:
                now = self.clock.timestamp_ns()
                if self.quotes:
                    self._handle_data(QuoteTick(iid, Price.from_str("30000.00"), Price.from_str("30000.10"),
                                                Quantity.from_str("100.000"), Quantity.from_str("100.000"), now, now))
                await asyncio.sleep(0.1)

    class FeedFactory(DataClientFactory):
        @staticmethod
        def create(*, name, config, cache, clock):
            return Feed(name=name, config=config, cache=cache, clock=clock)

    builder = LiveNode.builder("fee-sandbox", TraderId("TRADER-001"), Environment.SANDBOX).with_reconciliation(False)
    builder = builder.with_logging(LoggerConfig(bypass_logging=True, print_config=False)).with_delay_post_stop_secs(0).with_delay_shutdown_secs(0)
    builder = builder.add_data_client("SBX", FeedFactory(), DataClientConfig())
    config = SandboxExecutionClientConfig(Venue("SBX"), [Money.from_str("990.0033 USDT")], oms_type=OmsType.NETTING, account_type=AccountType.CASH,
                                          fee_model=MakerTakerFeeModel(FEE, FEE))
    node = builder.add_simulated_exec_client("SBX", SandboxExecutionClientFactory(), config).build()
    strategy = AllIn((iid, None, "USDT", 30000.10))
    node.add_strategy(strategy)
    handle = node.handle()

    def stop_when_done():
        deadline = time.time() + 40
        while time.time() < deadline and (strategy.terminal_at is None or time.time() < strategy.terminal_at + 2):
            time.sleep(0.2)
        handle.stop()

    threading.Thread(target=stop_when_done, daemon=True).start()
    node.run()
    return {"error": None, "events": strategy.events, "requested": getattr(strategy, "requested", None), **(strategy.snapshot or {})}


print(json.dumps(sandbox() if KIND == "sandbox" else backtest()))
