"""Synchronous model handles whose replies are delivered by the process's reader."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from uuid import uuid4


class ModelError(RuntimeError):
    def __init__(self, error):
        super().__init__(error['message'])
        self.error = error


class ModelCancelled(RuntimeError):
    pass


@dataclass
class _Pending:
    version_id: str
    decision_at: str
    deadline: float
    ready: threading.Event = field(default_factory=threading.Event)
    value: object = None
    error: BaseException | None = None


class Client:
    """Bind names to versions; ``send`` is synchronous and never reads stdin."""

    def __init__(self, bindings, runId, generation, *, send, timeout=30):
        if timeout <= 0:
            raise ValueError('The model timeout must be positive.')
        self.versions = {name: binding['versionId'] for name, binding in bindings.items()}
        self.run_id, self.generation = runId, generation
        self.send, self.timeout = send, timeout
        self._serial = {version: threading.Lock() for version in set(self.versions.values())}
        self._lock = threading.Lock()
        self._pending = {}
        self._failures = {}
        self._closed = None

    def predict(self, name, payload, decisionAt):
        version_id = self.versions[name]
        decision_at = str(decisionAt)
        if not decision_at.isdecimal():
            raise ValueError('decisionAt must be a nonnegative integer nanosecond timestamp.')
        deadline = time.monotonic() + self.timeout
        serial = self._serial[version_id]
        if not serial.acquire(timeout=self.timeout):
            raise TimeoutError('The model session is still processing its previous request.')
        request_id = uuid4().hex
        pending = _Pending(version_id, decision_at, deadline)
        try:
            request = {'type': 'model_request', 'runId': self.run_id, 'generation': self.generation,
                       'versionId': version_id, 'requestId': request_id, 'decisionAt': decision_at, 'name': name, 'payload': payload}
            json.dumps(request, allow_nan=False)
            with self._lock:
                if self._closed is not None:
                    raise ModelCancelled(self._closed)
                if version_id in self._failures:
                    raise ModelError(self._failures[version_id])
                self._pending[request_id] = pending
            self.send(request)
            if not pending.ready.wait(max(0, deadline - time.monotonic())):
                raise TimeoutError(f'Model {name!r} did not answer within {self.timeout} seconds.')
            if pending.error is not None:
                raise pending.error
            return pending.value
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
            serial.release()

    def deliver(self, message):
        """Return False for unrelated, stale, duplicate or expired replies."""
        if not isinstance(message, dict) or message.get('runId') != self.run_id or message.get('generation') != self.generation:
            return False
        version_id = message.get('versionId')
        if version_id not in self._serial:
            return False
        with self._lock:
            if self._closed is not None:
                return False
            if message.get('type') == 'model_failed':
                self._failures[version_id] = message['error']
                for pending in self._pending.values():
                    if pending.version_id == version_id and not pending.ready.is_set() and time.monotonic() < pending.deadline:
                        pending.error = ModelError(message['error'])
                        pending.ready.set()
                return True
            if message.get('type') != 'model_response':
                return False
            pending = self._pending.get(message.get('requestId'))
            if (pending is None or pending.ready.is_set() or pending.version_id != version_id
                    or pending.decision_at != message.get('decisionAt') or time.monotonic() >= pending.deadline):
                return False
            if message['ok']:
                pending.value = message['value']
            else:
                pending.error = ModelError(message['error'])
            pending.ready.set()
            return True

    def close(self, reason='The model session was cancelled.'):
        with self._lock:
            self._closed = reason
            for pending in self._pending.values():
                if not pending.ready.is_set():
                    pending.error = ModelCancelled(reason)
                    pending.ready.set()
