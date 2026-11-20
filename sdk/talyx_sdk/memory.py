"""Full garbage collections when the process is quiet, not in the middle of a call.

A process that holds a lot (large in-memory directories, the Host's registries) spends tens of milliseconds in a full
collection, and Python starts one whenever enough objects have piled up, which is in the middle of whatever call is
running. So: what exists once the process is ready is frozen (never scanned again), the automatic full collection is
put off, and a full collection runs once a minute when no call has been running for a moment, or at the latest every
few minutes. Only a young collection runs before freezing: a full one over everything the process imported holds its
loop for up to 200 ms and finds a few dozen objects.
Young-generation collections stay as they are (they take about a millisecond).
"""

from __future__ import annotations

import asyncio
import gc
import time
from collections.abc import Callable

QUIET_SECONDS = 1.0
EVERY_SECONDS = 60.0
LATEST_SECONDS = 300.0
CHECK_SECONDS = 5.0


def settle(busy: Callable[[], bool]) -> asyncio.Task:
    """Call once the process is ready, on its loop; ``busy`` says whether a call is running now."""
    global _threshold
    gc.collect(1)
    gc.freeze()
    _threshold = _threshold or gc.get_threshold()
    gc.set_threshold(_threshold[0], _threshold[1], 1_000_000)
    return asyncio.get_running_loop().create_task(_collect_when_quiet(busy))


def restore(collector: asyncio.Task | None) -> None:
    """Back to Python's own collections (a Host stopping inside a longer-lived process, such as a test run)."""
    if collector is not None:
        collector.cancel()
    if _threshold:
        gc.set_threshold(*_threshold)
    gc.unfreeze()


_threshold: tuple[int, int, int] | None = None


async def _collect_when_quiet(busy: Callable[[], bool]) -> None:
    last = time.monotonic()
    quiet_since = None
    while True:
        await asyncio.sleep(CHECK_SECONDS if quiet_since is None else QUIET_SECONDS)
        now = time.monotonic()
        if now - last < EVERY_SECONDS:
            continue
        if busy():
            quiet_since = None
            if now - last < LATEST_SECONDS:
                continue
        elif quiet_since is None:
            quiet_since = now
            continue
        gc.collect()
        last, quiet_since = time.monotonic(), None
