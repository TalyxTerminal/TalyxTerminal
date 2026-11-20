"""Native financial definitions reject fields or targets they cannot execute."""

import pytest

from modules.quant.native.strategy_options import latency_ns, native_ns, validate


def test_unknown_economic_configuration_is_rejected_instead_of_ignored():
    definition = configuration()
    definition["venues"][0]["fundingModel"] = "custom"
    with pytest.raises(ValueError, match="unsupported fields: fundingModel"):
        validate(definition)


def test_object_strategy_keeps_its_frozen_next_rebalance_start_policy():
    definition = configuration()
    definition['strategy'] = {'kind': 'object', 'module': 'strategy', 'nextRebalance': True}
    validate(definition)
    definition['strategy']['nextRebalance'] = 'true'
    with pytest.raises(ValueError, match='must be boolean'):
        validate(definition)


def test_native_zero_and_submillisecond_text_remain_exact():
    assert native_ns(0, "Time") == 0
    assert native_ns("1700000000000000123", "Time") == 1700000000000000123
    for value in (True, False, 1700000000000000123.0):
        with pytest.raises(ValueError, match="exact integer"):
            native_ns(value, "Time")
    with pytest.raises(ValueError, match="bounds"):
        native_ns(str(2**64), "Time")


def test_latency_text_keeps_precision_above_javascript_integer_limit():
    definition = configuration()
    definition["venues"][0]["latency"]["insert"] = "9007199254740993"
    validate(definition)
    assert latency_ns(definition["venues"][0]["latency"]["insert"], "Delay") == 9007199254740993


@pytest.mark.parametrize("value", [None, "", True, 1.5, "1.5", -1, str(2**64)])
def test_invalid_latency_is_rejected_before_native_execution(value):
    definition = configuration()
    definition["venues"][0]["latency"]["cancel"] = value
    with pytest.raises(ValueError, match="Native cancel latency"):
        validate(definition)

def configuration():
    return {'engine': 'nautilus', 'task': 'event', 'dataType': 'bars', 'priceBasis': 'raw',
        'strategy': {'kind': 'object', 'module': 'strategy'},
        'instruments': [{'id': 'BTC.SIM', 'type': 'perpetual', 'currency': 'USD', 'pricePrecision': 2, 'priceIncrement': '0.01'}],
        'venues': [{'name': 'SIM', 'balances': ['1000 USD'], 'latency': {'base': 0, 'insert': 0, 'update': 0, 'cancel': 0}}]}


@pytest.mark.parametrize('field', ['borrow_terms', 'funding_rates', 'execution_targets', 'parameterGrid', 'eventStreams', 'corporate_actions'])
def test_obsolete_json_research_inputs_are_rejected(field):
    definition = configuration()
    definition[field] = []
    with pytest.raises(ValueError, match='unsupported fields'):
        validate(definition)


@pytest.mark.parametrize(('field', 'value'), [('task', 'execution'), ('task', 'market_making'), ('task', 'rebalance'), ('dataType', 'quotes')])
def test_strategy_object_has_one_frozen_bar_workflow(field, value):
    definition = configuration()
    definition[field] = value
    with pytest.raises(ValueError, match='frozen Market bars'):
        validate(definition)


def test_frozen_reference_prices_are_distinct_supported_bases():
    definition = configuration()
    definition['referencePrices'] = ['mark', 'index']
    validate(definition)
    for invalid in ('mark', ['mark', 'mark'], ['premium']):
        definition['referencePrices'] = invalid
        with pytest.raises(ValueError, match='referencePrices'):
            validate(definition)
