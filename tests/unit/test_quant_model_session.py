"""Frozen model sessions use one loader and preserve request identity over JSON lines."""
from __future__ import annotations

import hashlib
import json
import queue
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from modules.quant.native.model_client import Client, ModelCancelled, ModelError

WORKER = Path(__file__).parents[2] / 'modules' / 'quant' / 'native' / 'model_worker.py'
IDENTITY = {'runId': 'run-test', 'generation': 3, 'versionId': 'ver-test'}


def request(identity='request1', payload=None):
    return {'type': 'model_request', **IDENTITY, 'requestId': identity, 'decisionAt': '1234567890123456789', 'payload': payload}


def worker_config(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    artifact = tmp_path / 'model.json'
    artifact.write_text('{"saved": 7}', encoding='utf-8')
    (source / 'user_model.py').write_text('''import json
loads = 0
def load(artifacts):
    global loads
    loads += 1
    print("loader ordinary print")
    return {"loads": loads, "calls": 0, "saved": json.load(open(artifacts["state"]))}
def predict(state, payload):
    state["calls"] += 1
    print("predictor ordinary print")
    if payload == "error":
        raise ValueError("original model error")
    return {"loads": state["loads"], "calls": state["calls"], "saved": state["saved"], "payload": payload}
''', encoding='utf-8')
    config = {**IDENTITY, 'output': str(tmp_path),
              'model': {'source': str(source), 'common': None, 'loader': 'user_model:load', 'predictor': 'user_model:predict',
                        'artifacts': [{'name': 'state', 'path': str(artifact), 'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest()}]}}
    path = tmp_path / 'worker.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    return path, config


def start(path, *, stdin=subprocess.PIPE):
    return subprocess.Popen([sys.executable, '-B', str(WORKER), str(path)], stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding='utf-8')


def test_lines_load_once_preserve_arbitrary_json_and_original_error(tmp_path):
    path, _ = worker_config(tmp_path)
    frames = [request('first', [None, {'unicode': '模型', 'array': [True, 1, 1.5]}]), request('second', 'error'), request('third', None)]
    process = start(path)
    stdout, stderr = process.communicate(''.join(json.dumps(frame) + '\n' for frame in frames), timeout=5)
    assert process.returncode == 0
    messages = [json.loads(line) for line in stdout.splitlines()]
    assert messages[0]['type'] == 'model_ready'
    assert len(messages) == 4
    first, error, third = messages[1:]
    assert first['value'] == {'loads': 1, 'calls': 1, 'saved': {'saved': 7}, 'payload': frames[0]['payload']}
    assert third['value']['loads'] == 1 and third['value']['calls'] == 3 and third['value']['payload'] is None
    assert error['ok'] is False and error['error']['type'] == 'ValueError'
    assert error['error']['message'] == 'original model error' and 'user_model.py' in error['error']['traceback']
    for message, frame in zip(messages[1:], frames, strict=True):
        assert all(message[key] == frame[key] for key in (*IDENTITY, 'requestId', 'decisionAt'))
    assert stderr.count('loader ordinary print') == 1 and stderr.count('predictor ordinary print') == 3


def test_changed_artifact_fails_before_loading(tmp_path):
    path, config = worker_config(tmp_path)
    Path(config['model']['artifacts'][0]['path']).write_text('changed', encoding='utf-8')
    process = start(path)
    stdout, stderr = process.communicate('', timeout=5)
    message = json.loads(stdout)
    assert process.returncode == 1 and message['type'] == 'model_failed'
    assert message['error']['type'] == 'ValueError' and 'artifact changed' in message['error']['message']
    assert 'loader ordinary print' not in stderr


def test_unclassified_user_exception_ends_the_worker_with_its_original_traceback(tmp_path):
    path, config = worker_config(tmp_path)
    source = Path(config['model']['source']) / 'user_model.py'
    source.write_text(source.read_text(encoding='utf-8') + '''
class UnclassifiedFailure(Exception):
    pass
def predict(state, payload):
    raise UnclassifiedFailure('original unclassified failure')
''', encoding='utf-8')
    process = start(path)
    stdout, stderr = process.communicate(json.dumps(request('one', None)) + '\n', timeout=5)
    assert process.returncode != 0
    assert len(stdout.splitlines()) == 1  # Only model_ready; no invented response.
    assert 'UnclassifiedFailure: original unclassified failure' in stderr and 'user_model.py' in stderr


@pytest.mark.parametrize('stdin_pipe', [False, True])
def response(frame, **result):
    return {**frame, 'type': 'model_response', **result}


def test_client_matches_every_identity_field_and_preserves_model_errors():
    sent = queue.Queue()
    client = Client({'model': {'versionId': 'ver-test'}}, 'run-test', 3, send=sent.put)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(client.predict, 'model', [False, None], '123')
        frame = sent.get(timeout=2)
        for key, wrong in [('runId', 'other'), ('generation', 2), ('versionId', 'other'), ('requestId', 'other'), ('decisionAt', '124')]:
            assert not client.deliver(response(frame, ok=True, value='wrong', **{key: wrong}))
        assert client.deliver(response(frame, ok=True, value=None))
        assert pending.result(timeout=2) is None
        assert not client.deliver(response(frame, ok=True, value='duplicate'))
        pending = executor.submit(client.predict, 'model', {}, '124')
        frame = sent.get(timeout=2)
        original = {'type': 'ValueError', 'message': 'original', 'traceback': 'original traceback'}
        assert client.deliver(response(frame, ok=False, error=original))
        with pytest.raises(ModelError, match='original') as caught:
            pending.result(timeout=2)
        assert caught.value.error == original
    client.close()


def test_client_timeout_close_and_loader_failure_discard_late_replies():
    sent = queue.Queue()
    client = Client({'model': {'versionId': 'ver-test'}}, 'run-test', 3, send=sent.put, timeout=.03)
    with pytest.raises(TimeoutError, match='did not answer'):
        client.predict('model', 1, '123')
    old = sent.get_nowait()
    assert not client.deliver(response(old, ok=True, value='late'))
    client = Client({'model': {'versionId': 'ver-test'}}, 'run-test', 3, send=sent.put)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(client.predict, 'model', 1, '123')
        frame = sent.get(timeout=2)
        client.close('The run was cancelled.')
        with pytest.raises(ModelCancelled, match='run was cancelled'):
            pending.result(timeout=2)
        assert not client.deliver(response(frame, ok=True, value='late'))
    client = Client({'model': {'versionId': 'ver-test'}}, 'run-test', 3, send=sent.put)
    client.deliver({'type': 'model_failed', **IDENTITY, 'error': {'type': 'RuntimeError', 'message': 'load failed'}})
    with pytest.raises(ModelError, match='load failed'):
        client.predict('model', {}, '123')


def test_deadline_rejects_response_before_the_waiting_thread_has_exited(monkeypatch):
    from modules.quant.native import model_client
    clock = [0.0]
    monkeypatch.setattr(model_client.time, 'monotonic', lambda: clock[0])

    def send(frame):
        clock[0] = 31.0
        assert not client.deliver(response(frame, ok=True, value='expired'))

    client = Client({'model': {'versionId': 'ver-test'}}, 'run-test', 3, send=send)
    with pytest.raises(TimeoutError, match='did not answer'):
        client.predict('model', {}, '123')
