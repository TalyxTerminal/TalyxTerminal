"""One frozen model, loaded once, serving serial JSON predictions."""
from __future__ import annotations

import contextlib
import hashlib
import importlib
import json
import re
import sys
import traceback
from pathlib import Path

REQUEST_ID = re.compile(r'[A-Za-z0-9_-]{1,64}')


def failure(error):
    return {'type': type(error).__name__, 'message': str(error),
            'traceback': ''.join(traceback.format_exception(error))}


def entry(value):
    module, separator, function = value.partition(':')
    if not separator or not module or not function:
        raise ValueError('A model entry must be module:function.')
    return getattr(importlib.import_module(module), function)


def load(config):
    model = config['model']
    artifacts = {}
    for artifact in model['artifacts']:
        path = Path(artifact['path'])
        if not path.is_absolute() or artifact['name'] in artifacts:
            raise ValueError('Model artifacts need unique names and absolute paths.')
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != artifact['sha256']:
            raise ValueError('The frozen model artifact changed: ' + artifact['name'])
        artifacts[artifact['name']] = str(path)
    sys.path.insert(0, model['source'])
    if model['common'] is not None:
        sys.path.insert(0, model['common'])
    return entry(model['loader'])(artifacts), entry(model['predictor'])


def envelope(config, kind, request_id=None, decision_at=None, **fields):
    return {'type': kind, 'runId': config['runId'], 'generation': config['generation'], 'versionId': config['versionId'],
            'requestId': request_id, 'decisionAt': decision_at, **fields}


def respond(config, state, predictor, request):
    request_id, decision_at = request['requestId'], request['decisionAt']
    try:
        if request['type'] != 'model_request' or any(request[key] != config[key] for key in ('runId', 'generation', 'versionId')):
            raise ValueError('The model request belongs to another session.')
        if not isinstance(request_id, str) or REQUEST_ID.fullmatch(request_id) is None:
            raise ValueError('Invalid model requestId.')
        if not isinstance(decision_at, str) or not decision_at.isdecimal():
            raise ValueError('Invalid model decisionAt nanosecond timestamp.')
        value = predictor(state, request['payload'])
        json.dumps(value, allow_nan=False)
        return envelope(config, 'model_response', request_id, decision_at, ok=True, value=value)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        return envelope(config, 'model_response', request_id, decision_at, ok=False, error=failure(error))


def serve(config):
    protocol = sys.stdout

    def emit(value):
        protocol.write(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n')
        protocol.flush()

    with contextlib.redirect_stdout(sys.stderr):
        try:
            state, predictor = load(config)
        except (ImportError, OSError, TypeError, ValueError) as error:
            emit(envelope(config, 'model_failed', ok=False, error=failure(error)))
            return 1
        emit(envelope(config, 'model_ready', ok=True))
        for line in sys.stdin:
            emit(respond(config, state, predictor, json.loads(line)))
        return 0


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        stream.reconfigure(encoding='utf-8')
    config = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    return serve(config)


if __name__ == '__main__':
    raise SystemExit(main())
