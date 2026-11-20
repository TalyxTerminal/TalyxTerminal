"""A callback submits all its intentions only after every model call succeeds."""
from __future__ import annotations

import io
import json
import queue
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from modules.quant.native.model_client import ModelCancelled, ModelError
from modules.quant.native.model_runtime import TRADING_METHODS, Runtime

INVOCATION = {'models': [{'name': 'signal', 'versionId': 'ver-1'}], 'runId': 'run-1', 'generation': 1}


class Strategy:
    def __init__(self):
        self.clock = SimpleNamespace(timestamp_ns=lambda: 1234567890123456789)
        self.executed = []

    def submit_order(self, order):
        self.executed.append(('submit', order))

    def submit_order_list(self, orders):
        self.executed.append(('list', orders))

    def close_position(self, position):
        self.executed.append(('close', position))
        self.submit_order('internal-close-' + position)

    def close_all_positions(self, instrument):
        self.executed.append(('close_all', instrument))
        self.close_position('internal-all-' + instrument)

    def cancel_order(self, order):
        self.executed.append(('cancel', order))

    def cancel_all_orders(self, instrument):
        self.executed.append(('cancel_all', instrument))

    def modify_order(self, order):
        self.executed.append(('modify', order))

    def modify_orders(self, updates):
        self.executed.append(('modify_many', updates))

    def cancel_orders(self, orders):
        self.executed.append(('cancel_many', orders))


def answer(frame, **fields):
    return {**frame, 'type': 'model_response', **fields}


def test_nested_callback_defers_all_six_methods_and_native_close_expands_once():
    frames = []

    def send(frame):
        frames.append(frame)
        runtime.deliver(answer(frame, ok=True, value=frame['payload']))

    runtime = Runtime(INVOCATION, send=send)
    strategy = Strategy()
    runtime.bind_clock(strategy)
    originals = {name: getattr(strategy, name) for name in TRADING_METHODS}
    with runtime.callback(strategy):
        strategy.submit_order('first')
        with runtime.callback(strategy):
            assert runtime.model('signal').predict({'arbitrary': [False, None]}) == {'arbitrary': [False, None]}
            strategy.submit_order_list(['second'])
            strategy.close_position('third')
        strategy.close_all_positions('fourth')
        strategy.cancel_order('fifth')
        strategy.cancel_all_orders('sixth')
        assert strategy.executed == []
    assert strategy.executed == [('submit', 'first'), ('list', ['second']), ('close', 'third'), ('submit', 'internal-close-third'),
                                 ('close_all', 'fourth'), ('close', 'internal-all-fourth'), ('submit', 'internal-close-internal-all-fourth'),
                                 ('cancel', 'fifth'), ('cancel_all', 'sixth')]
    assert all(getattr(strategy, name) == original for name, original in originals.items())
    assert frames[0]['decisionAt'] == '1234567890123456789'
    runtime.close()


def test_caught_model_error_poisoning_discards_every_intent_and_raises_original():
    error = {'type': 'ValueError', 'message': 'original predictor failure', 'traceback': 'source traceback'}

    def send(frame):
        runtime.deliver(answer(frame, ok=False, error=error))

    runtime = Runtime(INVOCATION, send=send)
    strategy = Strategy()
    runtime.bind_clock(strategy)
    caught = None
    with pytest.raises(ModelError) as final:
        with runtime.callback(strategy):
            strategy.submit_order('before')
            try:
                runtime.model('signal').predict(None)
            except ModelError as failure:
                caught = failure
            strategy.cancel_all_orders('after')
    assert final.value is caught and final.value.error == error
    assert strategy.executed == []
    runtime.close()


def test_caught_nested_callback_error_discards_outer_intentions():
    runtime = Runtime(INVOCATION, send=lambda _frame: None)
    strategy = Strategy()
    runtime.bind_clock(strategy)
    original = ValueError('nested callback failed')
    with pytest.raises(ValueError) as caught:
        with runtime.callback(strategy):
            strategy.submit_order('before')
            try:
                with runtime.callback(strategy):
                    strategy.cancel_order('nested')
                    raise original
            except ValueError:
                pass
            strategy.submit_order('after')
    assert caught.value is original and strategy.executed == []
    runtime.close()


def test_no_models_has_no_trading_wrappers_or_transport_setup():
    runtime = Runtime({'models': []})
    strategy = Strategy()
    runtime.bind_clock(strategy)
    with runtime.callback(strategy):
        assert set(vars(strategy)) == {'clock', 'executed'}
        strategy.submit_order('immediate')
        assert strategy.executed == [('submit', 'immediate')]
    assert runtime.client is None and not runtime.deliver({})
    runtime.close()


def test_close_wakes_model_waiter_and_drops_callback_intentions():
    sent = queue.Queue()
    runtime = Runtime(INVOCATION, send=sent.put)
    strategy = Strategy()
    runtime.bind_clock(strategy)

    def callback():
        with runtime.callback(strategy):
            strategy.submit_order('before')
            runtime.model('signal').predict('waiting')
            strategy.submit_order('after')

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(callback)
        frame = sent.get(timeout=2)
        runtime.close('The run was cancelled.')
        with pytest.raises(ModelCancelled, match='run was cancelled'):
            future.result(timeout=1)
        assert not runtime.deliver(answer(frame, ok=True, value='late'))
    assert strategy.executed == []


def test_backtest_reader_delivers_while_callback_blocks_and_prints_stay_off_protocol(monkeypatch):
    incoming, outgoing = queue.Queue(), queue.Queue()
    reads = []

    class Input:
        def readline(self):
            reads.append(True)
            return incoming.get(timeout=2) or ''

    class Output(io.StringIO):
        def write(self, text):
            outgoing.put(text)
            return super().write(text)

    protocol, logs = Output(), io.StringIO()
    monkeypatch.setattr(sys, 'stdin', Input())
    monkeypatch.setattr(sys, 'stdout', protocol)
    monkeypatch.setattr(sys, 'stderr', logs)
    runtime = Runtime(INVOCATION)
    assert not reads  # No blocking stdin read during native DLL initialization.
    failures = []

    def host():
        try:
            frame = json.loads(outgoing.get(timeout=2))
            incoming.put(json.dumps(answer({**frame, 'generation': 0}, ok=True, value='stale')) + '\n')
            incoming.put(json.dumps(answer(frame, ok=True, value='delivered')) + '\n')
        except (queue.Empty, ValueError) as error:
            failures.append(error)

    reader = threading.Thread(target=host)
    reader.start()
    try:
        print('ordinary authored output')
        strategy = Strategy()
        runtime.bind_clock(strategy)
        with runtime.callback(strategy):
            assert runtime.model('signal').predict(None) == 'delivered'
        reader.join(timeout=2)
        assert not failures and not reader.is_alive()
        assert len(protocol.getvalue().splitlines()) == 1
        assert 'ordinary authored output' in logs.getvalue()
        assert len(reads) == 2  # Stale response skipped; no idle read after the valid reply.
    finally:
        runtime.close()
        incoming.put(None)
    assert sys.stdout is protocol


def test_predict_outside_callback_and_unfrozen_name_are_rejected():
    runtime = Runtime(INVOCATION, send=lambda _frame: None)
    with pytest.raises(RuntimeError, match='inside a strategy callback'):
        runtime.model('signal').predict(None)
    with pytest.raises(ValueError, match='not frozen'):
        runtime.model('unfrozen')
    runtime.close()


def test_registered_clock_is_retained_when_native_callback_borrows_the_strategy():
    class BorrowedStrategy(Strategy):
        borrowed = False

        @property
        def clock(self):
            if self.borrowed:
                raise RuntimeError('Already mutably borrowed')
            return self.native_clock

        @clock.setter
        def clock(self, value):
            self.native_clock = value

    def send(message):
        runtime.deliver(answer(message, ok=True, value='ok'))

    runtime = Runtime(INVOCATION, send=send)
    strategy = BorrowedStrategy()
    runtime.bind_clock(strategy)
    strategy.borrowed = True
    with runtime.callback(strategy):
        assert runtime.model('signal').predict(None) == 'ok'
    runtime.close()
