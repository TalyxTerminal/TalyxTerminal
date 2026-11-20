"""Material cleanup preserves errors and releases every acquired input after cancellation."""
import asyncio

import pytest

from modules.quant.backend.teardown import cleanup


@pytest.mark.parametrize('original_kind', ['none', 'cancel', 'failure', 'cancel-group'])
@pytest.mark.parametrize('late_failures', [False, True])
@pytest.mark.parametrize('cancel_caller', [False, True])
def test_cleanup_contract_keeps_error_identity_runs_all_steps_and_delays_cancellation(original_kind, late_failures, cancel_caller):
    async def scenario():
        original = {'none': None, 'cancel': asyncio.CancelledError(), 'failure': RuntimeError('original'),
                    'cancel-group': BaseExceptionGroup('cancelled siblings', [asyncio.CancelledError()])}[original_kind]
        first, second = RuntimeError('first cleanup'), ValueError('second cleanup')
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def one():
            calls.append('one')
            entered.set()
            await release.wait()
            if late_failures:
                raise first

        async def two():
            calls.append('two')
            if late_failures:
                raise second

        task = asyncio.create_task(cleanup(one, two, lambda: calls.append('last'), original=original))
        await entered.wait()
        if cancel_caller:
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
        assert calls == ['one'] and not task.done()
        release.set()
        result = (await asyncio.gather(task, return_exceptions=True))[0]
        expected = ([original] if original_kind == 'failure' else []) + ([first, second] if late_failures else [])
        assert calls == ['one', 'two', 'last']
        if expected:
            actual = list(result.exceptions) if isinstance(result, BaseExceptionGroup) else [result]
            assert actual == expected  # The original exception objects and traceback are retained.
        elif cancel_caller or original_kind in {'cancel', 'cancel-group'}:
            assert isinstance(result, asyncio.CancelledError)
        else:
            assert result == [None, None, None]

    asyncio.run(scenario())

