"""Market's frozen stock evidence as generic native matching conditions."""
from __future__ import annotations

import copy
import json
from decimal import Decimal


def original_volume(item, row):
    """Restore the integer share grid only for sub-microshare split roundoff."""
    volume = Decimal(row['rawVolume'])
    if not volume.is_finite() or volume < 0:
        raise ValueError(f"{item['id']}: original share volume is invalid")
    whole = volume.to_integral_value()
    if volume != whole:
        factor = Decimal(row.get('rawRestorationFactor', '1'))
        if not factor.is_finite() or factor <= 0 or factor == 1 or abs(volume-whole) > Decimal('0.000001'):
            raise ValueError(f"{item['id']}: original share volume is fractional after source split restoration")
    return whole


def warmup_only(item, row):
    return row['closeAt'] * 1_000_000 < item.get('executionFromNs', 0)


def conditions(item, *, raw=False, state=None, rebuild_orders=()):
    stock = item['stock']
    sessions = []
    for row in stock['sessions']:
        execution_volume = None
        if raw:
            try:
                execution_volume = original_volume(item, row)
            except ValueError:
                if warmup_only(item, row):
                    continue
                raise
        taxes = [tax for tax in stock['taxes'] if tax['effectiveFrom'] <= row['sessionDate']
                 and (tax['effectiveUntil'] is None or row['sessionDate'] <= tax['effectiveUntil'])]
        if stock['taxes'] and len(taxes) != 1:
            raise ValueError(f"{item['id']}: no unique tax conditions for {row['sessionDate']}")
        tax = taxes[0] if taxes else {'buyRate': '0', 'sellRate': '0'}
        mapping = row['priceMap']
        scale = Decimal(mapping['scale'])
        sessions.append({'date': row['sessionDate'], 'openAt': row['openAt'] * 1_000_000,
            'closeAt': row['closeAt'] * 1_000_000,
            'lower': row['rawLower'] if raw else row['lower'], 'upper': row['rawUpper'] if raw else row['upper'],
            'priceIncrement': row['rawPriceIncrement'] if raw else row['priceIncrement'],
            'priceOrigin': '0' if raw else format(Decimal(mapping['offset']), 'f'),
            'priceTolerance': '0' if raw else format(Decimal(mapping['maxError']) + Decimal(1).scaleb(-int(item['pricePrecision'])), 'f'),
            'buyTaxRate': tax['buyRate'], 'sellTaxRate': tax['sellRate'],
            'inputPriceScale': format(Decimal(1) / scale, 'f') if raw else '1',
            'inputPriceOffset': format(-Decimal(mapping['offset']) / scale, 'f') if raw else '0',
            'inputPriceIncrement': row['rawPriceIncrement'] if raw else None,
            'inputVolumeScale': format(execution_volume / Decimal(row['adjustedVolume']), 'f') if raw and Decimal(row['adjustedVolume']) else '1',
            'executionBar': {**dict(zip(('open', 'high', 'low', 'close'), row['rawOHLC'], strict=True)), 'volume': format(execution_volume, 'f')} if raw else None})
    commission = item['stockCommission']
    rules = {'minimum': str(stock['orderSize']['minimum']), 'increment': str(stock['orderSize']['increment']),
        'oddLotSellAll': stock['orderSize']['oddLotSellAll'], 'sellAfterSessions': stock['settlement']['sellAfterTradingDays'],
        'commission': {key: commission[key] for key in ('rate', 'perShare', 'minimum', 'maximumRate')}, 'sessions': sessions}
    return {'rules': rules, 'state': state, 'rebuildOrders': list(rebuild_orders), 'rebuildSeed': item['rebuildSeed'] if rebuild_orders else None}


def instrument_info(item, *, raw=False, state=None, rebuild_orders=()):
    return {'execution_conditions': json.dumps(conditions(item, raw=raw, state=state, rebuild_orders=rebuild_orders), allow_nan=False)}


def checkpoint_state(cache, items):
    result = {}
    for item in items:
        if item['type'] != 'equity':
            continue
        value = cache.get('trading-conditions/state/' + item['id'])
        if value is None:
            raise ValueError(f"{item['id']}: native trading condition state was not acknowledged")
        result[item['id']] = json.loads(value)
    return result


def raw_config(config, checkpoint, rebuild_orders, *, execution_from_ns=0):
    """Real-price simulation uses a separate account from adjusted research."""
    result = copy.deepcopy(config)
    result['priceBasis'] = 'raw'
    for item in result['instruments']:
        if item['type'] != 'equity':
            continue
        item['executionRaw'] = True
        watermarks = [] if checkpoint is None else [int(value) for key, value in checkpoint['lastBars'].items() if key.rsplit('-', 4)[0] == item['id']]
        item['executionFromNs'] = min(watermarks) if watermarks else execution_from_ns
        for row in item['stock']['sessions']:
            try:
                row['rawVolume'] = str(original_volume(item, row))
            except ValueError:
                if not warmup_only(item, row):
                    raise
        item['executionState'] = None if checkpoint is None else checkpoint['tradingConditions'][item['id']]
        item['rebuildOrders'] = [order for order in rebuild_orders if order['instrumentId'] == item['id']]
        item['rebuildOrders'] = [{key: order[key] for key in ('orderId', 'side', 'quantity')} for order in item['rebuildOrders']]
        item['rebuildSeed'] = None if not item['rebuildOrders'] else {
            'at': max(int(value) for key, value in checkpoint['lastBars'].items() if key.rsplit('-', 4)[0] == item['id']),
            'price': str(checkpoint['marks'][item['id']])}
    return result


def execution_price(item, price, closed):
    """Only bookkeeping and sizing; native matching decides every fill."""
    rows = [row for row in item['stock']['sessions'] if closed == row['closeAt'] * 1_000_000]
    if len(rows) != 1:
        raise ValueError(f"{item['id']}: no execution coordinate for {closed}")
    row = rows[0]
    if Decimal(str(price)) == Decimal(row['adjustedOHLC'][3]):
        return Decimal(row['rawOHLC'][3])
    value = (Decimal(str(price)) - Decimal(row['priceMap']['offset'])) / Decimal(row['priceMap']['scale'])
    tick = Decimal(row['rawPriceIncrement'])
    return (value / tick).quantize(Decimal(1)) * tick


def bind_strategy(strategy, config):
    """Signals/indicators see adjusted bars, account sizing sees real prices."""
    items = {item['id']: item for item in config['instruments'] if 'executionRaw' in item and item['executionRaw']}
    if not items:
        return
    original = strategy.last_close

    def last_close(instrument_id):
        if str(instrument_id) not in items:
            return original(instrument_id)
        item = items[str(instrument_id)]
        bar_type = next(value for value in strategy.bar_types if value.instrument_id == instrument_id)
        bar = strategy.cache.bar(bar_type)
        return None if bar is None else float(execution_price(item, bar.close, bar.ts_event))
    strategy.last_close = last_close


def research_price(item, raw_price, closed):
    rows = [row for row in item['stock']['sessions'] if closed == row['closeAt'] * 1_000_000]
    if len(rows) != 1:
        raise ValueError(f"{item['id']}: no research coordinate for {closed}")
    row = rows[0]
    return Decimal(str(raw_price)) * Decimal(row['priceMap']['scale']) + Decimal(row['priceMap']['offset'])


def rebuild_plan(checkpoint):
    """Internal IDs are fixed before constructing the instrument's restore input."""
    import hashlib
    prefix = hashlib.sha256(checkpoint['id'].encode()).hexdigest()[:20]
    return [{'instrumentId': position['instrumentId'], 'orderId': f'QREBUILD-{prefix}-{index}',
             'side': 'BUY' if position['side'] == 'LONG' else 'SELL', 'quantity': position['quantity']}
            for index, position in enumerate(checkpoint['positions'])]
