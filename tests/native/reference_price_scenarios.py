"""Reference and executable price separation on the compiled backtest engine."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'modules/quant'))


def run(basis, config):
    import pandas as pd
    from native.reference_prices import bind
    from native.strategy_research import event_data, make_engine
    from nautilus_trader.model import BarType, InstrumentId, OrderSide, Price, Quantity, TriggerType
    from nautilus_trader.trading import Strategy

    instrument = config['instruments'][0]['id']
    kind = instrument + '-1-MINUTE-LAST-EXTERNAL'
    fills = []

    class Protected(Strategy):
        def __init__(self):
            super().__init__()
            self.entered = False

        def on_start(self):
            self.subscribe_bars(BarType.from_str(kind))

        def on_bar(self, bar):
            if not self.entered:
                self.entered = True
                self.submit_order(self.order_factory.market(InstrumentId.from_str(instrument), OrderSide.BUY, Quantity.from_str('0.010')))

        def on_order_filled(self, event):
            fills.append({'side': str(event.order_side), 'price': str(event.last_px), 'at': event.ts_event})
            if str(event.order_side) == 'BUY':
                self.submit_order(self.order_factory.stop_market(InstrumentId.from_str(instrument), OrderSide.SELL, Quantity.from_str('0.010'),
                    Price.from_str('95.00'), trigger_type=TriggerType.from_str(basis.upper() + '_PRICE'), reduce_only=True))

    engine = make_engine(config)
    try:
        strategy = Protected()
        engine.add_strategy(strategy)
        bind(engine, strategy, ['mark', 'index'])
        counts = []
        for index in range(3):
            at = (1_700_000_000_000 + (index + 1) * 60_000) * 1_000_000
            row = {'instrument': instrument, 'barType': kind, 'open': '100.0', 'high': '101.01', 'low': '99.0', 'close': '100.0',
                   'volume': '10.000', 'ts_event': at, 'ts_init': at}
            for key in ('mark', 'index'):
                cross = index == 0 or (index == 1 and key != basis) or (index == 2 and key == basis)
                low = '90.0' if cross else '95.04'  # Above the stop, even though it rounds to 95.0 at the trading tick.
                row.update({key + '_' + field: low if field == 'low' else '100.0' for field in ('open', 'high', 'low', 'close')})
            engine.add_data(event_data(config, pd.DataFrame([row])))
            engine.run(streaming=True)
            engine.clear_data()
            counts.append(len(fills))
        return {'counts': counts, 'fills': fills}
    finally:
        engine.dispose()


if __name__ == '__main__':
    print(json.dumps(run(sys.argv[1], json.loads(sys.argv[2]))))
