"""Model handles and one callback's deferred trading intentions."""
from __future__ import annotations

import contextlib
import contextvars
import json
import queue
import sys
import threading
from dataclasses import dataclass, field

from .model_client import Client, ModelCancelled

TRADING_METHODS = ('submit_order', 'submit_order_list', 'close_position', 'close_all_positions',
                   'modify_order', 'modify_orders', 'cancel_order', 'cancel_orders', 'cancel_all_orders')


@dataclass
class _Decision:
    at: str
    intents: list = field(default_factory=list)
    restores: list = field(default_factory=list)
    strategies: set = field(default_factory=set)
    failure: BaseException | None = None


class _Handle:
    def __init__(self, runtime, name):
        self.runtime, self.name = runtime, name

    def predict(self, payload):
        return self.runtime.predict(self.name, payload)


class Runtime:
    """Invocation fields: models, runId and generation. Model requests and replies are JSON lines.

    No send function means this is the backtest process: this
    object owns stdin's reader and reserves the original stdout for protocol.
    Nodes pass send and feed their existing reader's model replies to deliver.
    """

    def __init__(self, invocation, send=None):
        self.bindings = {item['name']: item for item in invocation['models']}
        self._active = contextvars.ContextVar('quant-model-decision', default=None)
        self._closed = None
        self._reader_error = None
        self._stdout_redirect = None
        self._lifecycle = threading.Lock()
        self._send_lock = threading.Lock()
        self.client = None
        self._clocks = {}
        self._read_requests = queue.SimpleQueue()
        if not self.bindings:
            return
        protocol = sys.stdout

        def write(message):
            line = json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            with self._send_lock:
                protocol.write(line + '\n')
                protocol.flush()
            if send is None:
                self._read_requests.put(True)

        self.client = Client(self.bindings, invocation['runId'], invocation['generation'], send=write if send is None else send)
        if send is None:
            self._stdout_redirect = contextlib.redirect_stdout(sys.stderr)
            self._stdout_redirect.__enter__()
            threading.Thread(target=self._read, args=(sys.stdin,), daemon=True, name='quant-model-responses').start()

    def _read(self, stream):
        try:
            # A Windows stdin read held while NumPy loads its DLL can deadlock
            # the import. Read only when a prediction has an outstanding reply;
            # between calls this thread waits on a queue, leaving authored
            # imports and the first native initialization free to run.
            while self._read_requests.get() is not None:
                while True:
                    line = stream.readline()
                    if not line:
                        self.close('The Host model response pipe ended.')
                        return
                    if self.deliver(json.loads(line)):
                        break
        except (OSError, ValueError) as error:
            self._reader_error = error
            self.close(str(error))

    def model(self, name):
        if name not in self.bindings:
            raise ValueError(f'Model {name!r} is not frozen for this run.')
        return _Handle(self, name)

    def bind_clock(self, strategy):
        # Capture the public clock after registration, outside native callbacks.
        # Live order callbacks may hold the strategy's mutable native borrow.
        self._clocks[id(strategy)] = strategy.clock

    def predict(self, name, payload):
        decision = self._active.get()
        if decision is None:
            raise RuntimeError('Model predict must run inside a strategy callback.')
        if decision.failure is not None:
            raise decision.failure
        try:
            return self.client.predict(name, payload, decision.at)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            failure = self._reader_error if self._reader_error is not None else error
            decision.failure = failure
            raise failure from None

    def deliver(self, message):
        return False if self.client is None else self.client.deliver(message)

    def _stage(self, decision, strategy):
        identity = id(strategy)
        if identity in decision.strategies:
            return
        decision.strategies.add(identity)
        for name in TRADING_METHODS:
            original = getattr(strategy, name)
            own = name in vars(strategy)

            def deferred(*args, _original=original, **kwargs):
                decision.intents.append((_original, args, kwargs))

            decision.restores.append((strategy, name, own, original))
            setattr(strategy, name, deferred)

    @contextlib.contextmanager
    def callback(self, strategy):
        if not self.bindings:
            yield
            return
        active = self._active.get()
        if active is not None:
            self._stage(active, strategy)
            try:
                yield
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                if active.failure is None:
                    active.failure = error
                raise
            return
        if self._closed is not None:
            raise ModelCancelled(self._closed)
        clock = self._clocks[id(strategy)]
        decision = _Decision(str(clock.timestamp_ns()))
        token = self._active.set(decision)
        try:
            self._stage(decision, strategy)
            try:
                yield
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                if decision.failure is None:
                    decision.failure = error
        finally:
            for target, name, own, original in reversed(decision.restores):
                if own:
                    setattr(target, name, original)
                else:
                    delattr(target, name)
            self._active.reset(token)
        if decision.failure is not None:
            raise decision.failure
        if self._closed is not None:
            raise ModelCancelled(self._closed)
        # Restore the real methods before committing. Native close methods can
        # submit orders internally; those calls must execute once, not restage.
        # Callbacks caused by a committed intent start a new decision boundary.
        for operation, args, kwargs in decision.intents:
            if self._closed is not None:
                raise ModelCancelled(self._closed)
            operation(*args, **kwargs)

    def close(self, reason='The model runtime was cancelled.'):
        with self._lifecycle:
            if self._closed is not None:
                return
            self._closed = reason
            self._read_requests.put(None)
            if self.client is not None:
                self.client.close(reason)
            if self._stdout_redirect is not None:
                self._stdout_redirect.__exit__(None, None, None)
                self._stdout_redirect = None
