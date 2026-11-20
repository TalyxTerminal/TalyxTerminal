"""One reference OHLC path for backtests, Sandbox and retained protection replay.

LAST supplies executable prices. References trigger existing orders, then the
author receives the close; a new order cannot trigger on the same past range.
"""
from __future__ import annotations

FIELDS = ('open', 'high', 'low', 'close')


def updates(instrument, references, at):
    from nautilus_trader.model import IndexPriceUpdate, MarkPriceUpdate, Price
    result = []
    for basis, values in references.items():
        kind = {'mark': MarkPriceUpdate, 'index': IndexPriceUpdate}[basis]
        for field in FIELDS:
            # A reference observation can lie between two executable ticks.
            price = Price.from_str(str(values[field]))
            result.append(kind(instrument.id, price, at, at))
    return result


def rows(frame, bases):
    return {(row['instrument'], int(row['ts_event'])): {
        basis: {field: row[basis + '_' + field] for field in FIELDS} for basis in bases}
        for row in frame.to_dict('records')}


def guard(strategy, bases):
    from nautilus_trader.model import TriggerType
    allowed = {TriggerType.DEFAULT, TriggerType.NO_TRIGGER, TriggerType.LAST_PRICE,
               *({'mark': TriggerType.MARK_PRICE, 'index': TriggerType.INDEX_PRICE}[basis] for basis in bases)}
    original = strategy.submit_order
    original_list = strategy.submit_order_list

    def check(order):
        value = order.to_dict()
        trigger = TriggerType.from_str(value['trigger_type']) if 'trigger_type' in value else TriggerType.NO_TRIGGER
        if trigger not in allowed:
            raise ValueError(f'{order.client_order_id}: select the required referencePrices in data settings before submitting {trigger} protection.')

    def submit(order, *args, **kwargs):
        check(order)
        return original(order, *args, **kwargs)

    def submit_list(order_list, *args, **kwargs):
        for order in order_list:
            check(order)
        return original_list(order_list, *args, **kwargs)

    strategy.submit_order, strategy.submit_order_list = submit, submit_list


def protocol():
    from nautilus_trader.model import CustomData, DataType, register_custom_data_class
    from nautilus_trader.model.custom import customdataclass

    @customdataclass()
    class ReferenceClose:
        instrument: str = ''

    register_custom_data_class(ReferenceClose)
    data_type = DataType('ReferenceClose', None, None)
    return ReferenceClose, data_type, CustomData


_protocol = None


def closing(instrument, at):
    global _protocol
    if _protocol is None:
        _protocol = protocol()
    kind, data_type, custom = _protocol
    return custom(data_type, kind(ts_event=at, ts_init=at, instrument=instrument))


def expand(bars, observations, instruments, *, author_barrier=False):
    result = []
    for bar in bars:
        key = (str(bar.bar_type.instrument_id), bar.ts_event)
        result.append(bar)
        result.extend(updates(instruments[key[0]], observations[key], bar.ts_event))
        if author_barrier:
            result.append(closing(key[0], bar.ts_event))
    return result


def bind(engine, strategy, bases):
    """Defer the authored callback until all references of this close were matched."""
    from nautilus_trader.common import DataActor
    guard(strategy, bases)
    if not bases:
        return
    closing('', 0)
    _, data_type, _ = _protocol
    pending = {}
    call = strategy.on_bar

    def on_bar(bar):
        pending[(str(bar.bar_type.instrument_id), bar.ts_event)] = bar
    strategy.on_bar = on_bar

    class ReferenceActor(DataActor):
        def on_start(self):
            self.subscribe_data(data_type, None, None)

        def on_data(self, data):
            bar = pending.pop((data.data.instrument, data.ts_event))
            call(bar)

    engine.add_actor(ReferenceActor())
