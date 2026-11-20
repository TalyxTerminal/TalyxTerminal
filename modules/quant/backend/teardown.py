"""Teardown shared by materials and model sessions: finish every step, then deliver errors or cancellation."""
from __future__ import annotations

import asyncio
import inspect


def failures(*errors):
    """Flatten and deduplicate real failures; cancellation alone is control flow."""
    seen = set()

    def leaves(error):
        if isinstance(error, BaseExceptionGroup):
            for child in error.exceptions:
                yield from leaves(child)
        elif isinstance(error, BaseException) and not isinstance(error, asyncio.CancelledError) and id(error) not in seen:
            seen.add(id(error))
            yield error

    for error in errors:
        yield from leaves(error)


async def cleanup(*steps, original=None):
    """Run all steps despite caller cancellation, retaining the original failure.

    Steps are awaitables or deferred synchronous/asynchronous callables. They
    run in order; already-created tasks can run concurrently. Every result is
    observed, including failures after a previous step failed. At the end real
    errors take precedence over cancellation; cancellation propagates only if
    there are no real errors. Call a finally block with original=sys.exception().
    """
    async def complete():
        results, errors = [], []
        for step in steps:
            try:
                value = step() if callable(step) else step
                results.append(await value if inspect.isawaitable(value) else value)
            except BaseException as error:  # The task/resource teardown boundary must observe every result.
                results.append(None)
                errors.append(error)
        return results, errors

    task = asyncio.create_task(complete(), name='quant-cleanup')
    cancelled = original is not None and not list(failures(original))
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    results, errors = task.result()
    errors = list(failures(original, *errors))
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise BaseExceptionGroup('The operation and its cleanup failed', errors)
    if cancelled:
        raise asyncio.CancelledError
    return results
