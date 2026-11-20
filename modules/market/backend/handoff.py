"""Handing files to callers as file references: written once under Market's data directory, kept 24 hours.

A file is named by its content digest, never rewritten, and deleted 24 hours after it was handed over. A caller that
keeps it longer hard-links it into its own directory first; Market deleting its copy then breaks nothing.
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from pathlib import Path

import numpy as np
import orjson
import pyarrow as pa
import pyarrow.parquet as pq

from .routing import in_worker

HOLD_SECONDS = 24 * 3600


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            value.update(chunk)
    return value.hexdigest()


def bars_table(columns: dict[str, np.ndarray]) -> pa.Table:
    arrays, names = [], []
    for name, values in columns.items():
        if name in ("time", "close_time"):
            arrays.append(pa.array(values.astype("datetime64[ns]"), type=pa.timestamp("ns", tz="UTC")))
        else:
            arrays.append(pa.array(values, type=pa.float64()))
        names.append("closeTime" if name == "close_time" else name)
    return pa.Table.from_arrays(arrays, names=names)


class Handoff:
    def __init__(self, market):
        self.market = market
        self.folder = market.root / "handoff"

    async def publish(self, path: Path, kind: str, name: str) -> dict:
        """Move a finished file into the hand-over folder; returns its ``FileRef``."""
        sha = await in_worker(digest, path)
        self.folder.mkdir(parents=True, exist_ok=True)
        target = self.folder / f"{sha[:32]}-{Path(name).name}"
        if target.exists():
            path.unlink()
        else:
            os.replace(path, target)
        os.utime(target)
        return {"path": str(target), "sha256": sha, "bytes": target.stat().st_size}

    async def parquet(self, columns: dict[str, np.ndarray], name: str) -> dict:
        return await self.table(bars_table(columns), name)

    async def research(self, columns: dict[str, list], name: str) -> dict:
        return await self.table(pa.table(columns), name)

    async def json(self, value, name):
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f".{uuid.uuid4().hex}.json"
        try:
            await in_worker(path.write_bytes, orjson.dumps(value))
            return await self.publish(path, "market-data", name + ".json")
        finally:
            path.unlink(missing_ok=True)

    async def table(self, table: pa.Table, name: str) -> dict:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f".{uuid.uuid4().hex}.parquet"
        rows = table.num_rows
        await in_worker(pq.write_table, table, path)
        reference = await self.publish(path, "market-data", f"{name}.parquet")
        return {**reference, "rows": rows}

    async def sweep(self) -> None:
        """Delete hand-over files older than 24 hours."""
        if not self.folder.exists():
            return
        cutoff = time.time() - HOLD_SECONDS
        for path in self.folder.iterdir():
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
