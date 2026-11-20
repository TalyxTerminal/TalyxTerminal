"""SDK split ratio roundoff never invents fractional execution shares."""
from copy import deepcopy
from decimal import Decimal

import pytest

from modules.quant.native.stock_execution import (
    conditions,
    original_volume,
    raw_config,
)
from tests.fixtures.strategy.instruments import stock_item


def test_dd_binary_one_third_volume_has_one_integer_value_at_every_execution_boundary():
    item = stock_item()
    row = item['stock']['sessions'][0]
    row.update(rawVolume='3376200.000000000337620000000', adjustedVolume='1125400', rawRestorationFactor='0.3333333333333333')
    before = deepcopy(item)
    assert str(original_volume(item, row)) == '3376200'
    rules = conditions(item, raw=True)['rules']['sessions'][0]
    assert rules['executionBar']['volume'] == '3376200' and rules['inputVolumeScale'] == '3'
    configured = raw_config({'instruments': [item]}, None, [])
    assert configured['instruments'][0]['stock']['sessions'][0]['rawVolume'] == '3376200'
    assert item == before


@pytest.mark.parametrize(('volume', 'factor'), [('10.5', '0.3333333333333333'), ('10.00001', '0.3333333333333333'),
    ('10.00000001', '1'), ('10.00000001', '1.0'), ('NaN', '0.3333333333333333'), ('-1', '0.3333333333333333')])
def test_real_fractional_or_unexplained_source_volumes_still_fail(volume, factor):
    item = stock_item()
    row = item['stock']['sessions'][0]
    row.update(rawVolume=volume, rawRestorationFactor=factor)
    with pytest.raises(ValueError, match='share volume'):
        original_volume(item, row)


def test_unverified_old_warmup_volume_is_never_a_matching_session_but_live_and_replay_stay_strict():
    item = stock_item()
    first, second = item['stock']['sessions'][:2]
    first.update(rawVolume='5015700.052438384897745149449', adjustedVolume='4782470', rawRestorationFactor='0.9535')
    config = {'instruments': [item]}
    current = raw_config(config, None, [], execution_from_ns=second['closeAt']*1_000_000)['instruments'][0]
    assert first in current['stock']['sessions']
    assert [row['date'] for row in conditions(current, raw=True)['rules']['sessions']] == ['2024-01-03', '2024-01-04']
    with pytest.raises(ValueError, match='fractional'):
        raw_config(config, None, [], execution_from_ns=first['closeAt']*1_000_000)
    checkpoint = {'lastBars': {item['id']+'-1-DAY-LAST-EXTERNAL': str(first['closeAt']*1_000_000)}}
    with pytest.raises(ValueError, match='fractional'):
        raw_config(config, checkpoint, [], execution_from_ns=second['closeAt']*1_000_000)


@pytest.mark.parametrize('offset', ['0E+29', '0E-28'])
def test_decimal_zero_exponents_are_normalized_only_at_native_wire_boundary(offset):
    item = stock_item()
    item['stock']['sessions'][0]['priceMap']['offset'] = offset
    assert conditions(item)['rules']['sessions'][0]['priceOrigin'] == format(Decimal(offset), 'f')
    native = conditions(item, raw=True)['rules']['sessions'][0]
    assert 'E' not in native['inputPriceOffset']
    assert Decimal(native['inputPriceOffset']) == 0
    assert item['stock']['sessions'][0]['priceMap']['offset'] == offset
