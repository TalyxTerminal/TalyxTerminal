"""A feed's closed bars as a file for the request.security children of the Pine workers.

The file holds float64 columns one after another (time, closeTime, open, high, low, close,
volume; a missing volume is NaN), ``count`` rows each. Chart writes it on the Host column by
column, letting the event loop run between them: an intrabar feed holds up to 200,000 bars, and as
a JSON message they would hold the loop for a third of a second. The worker's feed plugin reads it,
in the child that needs it or in the worker itself (intrabars of raw price fields, ``intrabars.py``).
"""
from __future__ import annotations

import asyncio
import functools
import math
from array import array
from pathlib import Path

FIELDS = ("time", "closeTime", "open", "high", "low", "close", "volume")


async def write(path: Path, columns: dict, count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        for name in FIELDS:
            values = columns[name][:count]
            if name == "volume":
                values = [math.nan if value is None else value for value in values]
            stream.write(array("d", values).tobytes())
            await asyncio.sleep(0)


@functools.lru_cache(maxsize=4)
def read(path: str | Path, count: int) -> tuple[list, ...]:
    """The rows as the feed plugin keeps them: ``["c", time, closeTime, open, high, low, close, volume]``.
    A history file never changes, and the instances of a worker that read intrabars in-process
    (``intrabars.py``) read the same one on every run, so the last few are kept; the rows are shared."""
    import numpy as np

    columns = np.fromfile(path, dtype=np.float64).reshape(len(FIELDS), count)
    times, closes = columns[0].astype(np.int64).tolist(), columns[1].astype(np.int64).tolist()
    opens, highs, lows, last, volumes = columns[2:].tolist()
    return tuple(["c", *row[:6], None if row[6] != row[6] else row[6]]
                 for row in zip(times, closes, opens, highs, lows, last, volumes))
