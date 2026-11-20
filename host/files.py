"""The platform's one way to write a file that must never be seen half written."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import orjson


def default_data_dir() -> Path:
    """``TALYX_DATA_DIR``, else the daily data root."""
    return Path(os.environ.get("TALYX_DATA_DIR") or Path(__file__).resolve().parents[1] / "data" / "store").resolve()


def write_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` in one step: a reader sees the old file or the new one, never a part. Each write
    has its own temporary file; writers of one path that may run at once still take turns (``asyncio.Lock``), so
    the last state is the one that lands."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def write_json(path: Path, value) -> None:
    """``value`` as JSON, written in one step."""
    write_atomic(path, orjson.dumps(value))


async def finish_before_cancel(operation):
    """Finish this commit or rollback before giving cancellation back to its caller."""
    work = asyncio.create_task(operation)
    cancelled = False
    while not work.done():
        try:
            await asyncio.shield(work)
        except asyncio.CancelledError:
            cancelled = True
    result = work.result()
    if cancelled:
        raise asyncio.CancelledError
    return result
