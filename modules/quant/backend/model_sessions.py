"""One bounded, isolated model process per frozen version and strategy run."""
from __future__ import annotations

import asyncio
import json
import sys
import traceback
from pathlib import Path

from talyx_sdk import ContractError

from .materials import plain
from .project_paths import MODULE
from .research_process import environment
from .teardown import cleanup, failures

TIMEOUT = 30


class Session:
    def __init__(self, ctx, binding, run_id, generation, directory):
        self.ctx, self.binding = ctx, binding
        self.run_id, self.generation, self.directory = run_id, generation, Path(directory)
        self.lease = None
        self.serial = asyncio.Lock()
        self.closed = False
        self.closing = None
        self.environment_owner = None

    async def start(self):
        ready = False
        try:
            await self.load()
            ready = True
        finally:
            if not ready:
                await cleanup(self.close, original=sys.exception())

    async def load(self):
        self.directory.mkdir(parents=True)
        output = self.directory / 'output'
        output.mkdir()
        state = plain(self.ctx.environments.get(self.binding['environment']['preparationId']))
        if state['state'] != 'completed':
            raise ContractError('unavailable', 'The frozen model environment is no longer available.')
        self.environment_owner = f"qmodel:{self.run_id}:{self.generation}:{self.binding['versionId']}"
        self.ctx.environments.hold(self.binding['environment']['preparationId'], self.environment_owner)
        config = {'runId': self.run_id, 'generation': self.generation, 'versionId': self.binding['versionId'], 'model': self.binding['model'],
                  'output': str(output)}
        path = self.directory / 'model.json'
        path.write_text(json.dumps(config), encoding='utf-8')
        model = self.binding['model']
        launch = {'submission_key': f"qmodel-{self.run_id}-{self.generation}-{self.binding['versionId']}",
                  'argv': [state['interpreterPath'], '-B', str(MODULE / 'native' / 'model_worker.py'), str(path)], 'cwd': model['source'],
                  'env': environment(), 'stdout_path': str(output / 'stdout.log'), 'stderr_path': str(output / 'stderr.log'), 'lines': True}

        async def launched():
            self.lease = plain(await self.ctx.processes.start(launch))

        await cleanup(launched())
        async with asyncio.timeout(TIMEOUT):
            message = await self.receive()
            self.check(message)
            if message['type'] != 'model_ready':
                raise ContractError('model_failed', message['error']['message'], {'failure': message['error']})

    def check(self, message):
        if (message['runId'], message['generation'], message['versionId']) != (self.run_id, self.generation, self.binding['versionId']):
            raise ContractError('model_protocol', 'The model response belongs to a different run, generation or version.')

    async def receive(self):
        while True:
            event = plain(await self.ctx.processes.receive(self.lease, 1.0))
            if event['state'] == 'message':
                return event['message']
            if event['state'] == 'closed':
                raise ContractError('model_failed', 'The model process ended before answering.')

    async def predict(self, request):
        try:
            return await self._predict(request)
        except TimeoutError as error:
            failure = ContractError('model_timeout', 'The model did not answer within 30 seconds.')
            failure.__cause__ = error
            await cleanup(self.close, original=failure)

    async def _predict(self, request):
        self.check(request)
        async with asyncio.timeout(TIMEOUT):
            async with self.serial:
                if self.closed:
                    raise ContractError('cancelled', 'The model session has ended.')
                await self.ctx.processes.send(self.lease, request)
                while True:
                    response = await self.receive()
                    self.check(response)
                    if response['requestId'] == request['requestId']:
                        break
                if response['decisionAt'] != request['decisionAt']:
                    raise ContractError('model_protocol', 'The model response belongs to another decision.')
                return response

    async def close(self):
        self.closed = True
        if self.closing is None:
            async def release():
                lease, self.lease = self.lease, None
                gone = False

                async def cancel():
                    nonlocal gone
                    try:
                        await self.ctx.processes.cancel(lease)
                    except ContractError as error:
                        # The owning scope may already have ended the execution.
                        if error.code != 'target_gone':
                            raise
                        gone = True

                async def wait():
                    if not gone:
                        await self.ctx.processes.wait(lease)

                async def environment_release():
                    if self.environment_owner is not None:
                        owner, self.environment_owner = self.environment_owner, None
                        self.ctx.environments.release(self.binding['environment']['preparationId'], owner)

                steps = [] if lease is None else [cancel, wait, lambda: self.ctx.processes.close(lease)]
                await cleanup(*steps, environment_release)

            self.closing = asyncio.create_task(release())
        await cleanup(self.closing)


class Sessions:
    def __init__(self, ctx, bindings, run_id, generation, directory):
        self.ctx, self.bindings, self.run_id, self.generation = ctx, bindings, run_id, generation
        self.sessions = {item['versionId']: Session(ctx, item, run_id, generation, Path(directory) / item['versionId']) for item in bindings}

    async def start(self):
        started = False
        try:
            for session in self.sessions.values():
                await session.start()
            started = True
        finally:
            if not started:
                await cleanup(self.close, original=sys.exception())

    async def predict(self, request):
        binding = next((item for item in self.bindings if item['name'] == request['name']), None)
        if binding is None or binding['versionId'] != request['versionId']:
            raise ContractError('permission_denied', 'This model version is not frozen for this strategy run.')
        if int(request['decisionAt']) < binding['trainingCutoff'] * 1_000_000:
            raise ContractError('model_training_overlap', 'This decision predates the model training cutoff.')
        return await self.sessions[binding['versionId']].predict(request)

    async def close(self):
        await cleanup(*(asyncio.create_task(session.close()) for session in self.sessions.values()))


def error_response(request, error):
    detail = error.details if isinstance(error, ContractError) else None
    failure = detail['failure'] if detail is not None and 'failure' in detail else {
        'type': type(error).__name__, 'message': '; '.join(str(item) for item in failures(error)), 'traceback': ''.join(traceback.format_exception(error))}
    return {key: request[key] for key in ('runId', 'generation', 'versionId', 'requestId', 'decisionAt')} | {
        'type': 'model_response', 'ok': False, 'error': failure}


class Router:
    """Only live Host-established strategy runs may call their frozen models."""
    def __init__(self, ctx):
        self.ctx, self.active = ctx, {}

    async def open(self, bindings, run_id, generation, directory):
        sessions = Sessions(self.ctx, bindings, run_id, generation, directory)
        key = (run_id, generation)
        if key in self.active:
            raise ContractError('conflict', 'This model run is already active.')
        self.active[key] = sessions
        started = False
        try:
            await sessions.start()
            started = True
        finally:
            if not started:
                self.active.pop(key)
        return sessions

    async def predict(self, request, *, bound=None):
        key = (request['runId'], request['generation'])
        if bound is not None and (bound['modelRunId'], bound['generation']) != key:
            raise ContractError('permission_denied', 'This request belongs to a different strategy execution.')
        sessions = self.active.get(key)
        if sessions is None:
            raise ContractError('cancelled', 'This strategy model run has ended.')
        try:
            return await sessions.predict(request)
        except (ContractError, TimeoutError, OSError, ValueError, ExceptionGroup) as error:
            return error_response(request, error)

    async def close(self, run_id, generation):
        sessions = self.active.pop((run_id, generation), None)
        if sessions is not None:
            await sessions.close()
