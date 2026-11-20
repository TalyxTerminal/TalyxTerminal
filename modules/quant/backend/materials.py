"""Native inputs: local files are read in place under their recorded digest; Market hands over files by reference."""
from __future__ import annotations

import asyncio
import hashlib
import sys
from pathlib import Path

from talyx_sdk import ContractError

from .teardown import cleanup


def plain(value):
    return value.model_dump(mode="json", by_alias=True) if hasattr(value, "model_dump") else value


def task_failure(task):
    if task is None:
        return
    if task['state'] == 'failed':
        error = task['error']
        raise ContractError(error['code'], error['message'], error.get('details'),
                            **({key: error[key] for key in ('execution', 'effects')} if 'execution' in error else {}))
    if task['state'] in {'cancelled', 'interrupted'}:
        raise ContractError(task['state'], f"Market export was {task['state']}.", {'terminationReason': task['state']}, execution='failed')


async def run_task(ctx, name, arguments, progress=None):
    """Follow a Market task, preserving its progress, failure and cancellation."""
    submitting = asyncio.create_task(ctx.call(name, arguments))
    task_id = None
    try:
        receipt = plain(await asyncio.shield(submitting))
        task_id = receipt["taskId"]
        last = None
        while True:
            task = plain(await ctx.call("platform.tasks.get", {"taskId": task_id}))
            if progress is not None and task.get("progress") is not None and task["progress"] != last:
                last = task["progress"]
                progress(last)
            task_failure(task)
            if task["state"] == "completed":
                return task["result"]
            await asyncio.sleep(.1)
    except BaseException:
        if task_id is None and submitting.done() and not submitting.cancelled():
            if submitting.exception() is None:
                task_id = plain(submitting.result())["taskId"]
        elif task_id is None:
            task_id = plain(await submitting)["taskId"]
        if task_id is not None:
            await cleanup(lambda: ctx.call("platform.tasks.cancel", {"taskId": task_id}), original=sys.exception())
        raise


async def release(ctx, references, owner):
    """Let go of Quant's copies of handed-over files this owner kept."""
    for reference in references:
        ctx.let_go(reference, owner)


async def acquire(ctx, reference, owner, held):
    """Keep a file another module handed over (a ``FileRef``) for ``owner``: Quant's own copy, with its path."""
    held.append(reference)          # let go even when keeping it is cancelled midway (letting go goes by digest and owner)
    kept = await ctx.keep(reference, owner)
    held[next(index for index, item in enumerate(held) if item is reference)] = kept
    return kept


class ExportFiles:
    """Long export files, each opened once: an instrument's rows come from its own row groups (Market writes one instrument per row group, and
    its statistics name it), not from a filtered scan of the file for every name. Reading 483 names that way, three reads each, took 15 s of a
    34 s `q.data`. A row group holding several names is read and filtered. Close it to let the files go."""

    def __init__(self):
        self.opened = {}

    def rows(self, path, instrument_id):
        import pyarrow.compute as pc
        import pyarrow.parquet as pq
        found = self.opened.get(path)
        if found is None:
            handle = open(path, "rb")  # noqa: SIM115 - kept open across reads, closed by close()
            parquet = pq.ParquetFile(handle)
            column = parquet.schema_arrow.get_field_index("instrumentId")
            groups, mixed = {}, []
            for group in range(parquet.metadata.num_row_groups):
                stats = parquet.metadata.row_group(group).column(column).statistics
                low, high = (stats.min, stats.max) if stats is not None and stats.has_min_max else (None, None)
                low, high = (value.decode() if isinstance(value, bytes) else value for value in (low, high))
                if low is not None and low == high:
                    groups.setdefault(low, []).append(group)
                else:
                    mixed.append(group)
            found = self.opened[path] = (handle, parquet, groups, mixed)
        _, parquet, groups, mixed = found
        wanted = sorted([*groups.get(instrument_id, []), *mixed])
        if not wanted:
            return parquet.schema_arrow.empty_table()
        table = parquet.read_row_groups(wanted)
        return table.filter(pc.equal(table["instrumentId"], instrument_id)) if mixed else table

    def close(self):
        for handle, *_ in self.opened.values():
            handle.close()
        self.opened.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def read_selection(selection, files=None):
    """Read one instrument from long export files; return Quant's millisecond columns. Many names read from the same files share `files`
    (an `ExportFiles`), which opens each file once."""
    import pandas as pd
    import pyarrow as pa
    import pyarrow.compute as pc

    if files is None:
        with ExportFiles() as own:
            return read_selection(selection, own)
    tables = [files.rows(ref["path"], selection["instrument"]["instrumentId"]) for ref in selection["files"]]
    if not tables:
        return pd.DataFrame(columns=["time", "closeAt", "knownAt", "closeNs", "knownNs", "open", "high", "low", "close", "volume"])
    table = pa.concat_tables(tables)
    time = pc.cast(table["time"], pa.int64())
    close = pc.cast(table["closeTime"], pa.int64())
    known = pc.cast(table["knownAt"], pa.int64())
    frame = table.drop(["time", "closeTime", "knownAt"]).to_pandas()
    frame["time"] = time.to_numpy() // 1_000_000
    frame["closeAt"], frame["knownAt"] = close.to_numpy() // 1_000_000, known.to_numpy() // 1_000_000
    frame["closeNs"], frame["knownNs"] = close.to_numpy(), known.to_numpy()
    return frame.sort_values("time").reset_index(drop=True)


class Materials:
    def __init__(self, ctx):
        self.ctx = ctx

    async def capture(self, selection, owner):
        if selection.get("market"):
            from .market_capture import capture
            return await capture(self.ctx, selection["market"], owner)
        source = Path(selection["path"]).expanduser().resolve(strict=True)
        expected = selection.get("expectedDigest")
        if source.is_dir():
            from .project_paths import source_identity
            from .work_records import digest
            files = await asyncio.to_thread(source_identity, source)
            if expected and digest(files) != expected:
                raise ContractError("conflict", "The selected input directory changed since it was chosen")
            return {"kind": "directory", "path": str(source), "digest": digest(files), "source": str(source),
                    "files": [{"path": path, "sha256": value} for path, value in sorted(files.items())]}
        file_digest = await asyncio.to_thread(lambda: hashlib.sha256(source.read_bytes()).hexdigest())
        if expected and file_digest != expected:
            raise ContractError("conflict", "The selected input changed since it was chosen")
        return {"kind": "file", "path": str(source), "digest": file_digest, "source": str(source),
                **({"componentSource": selection["componentSource"]} if selection.get("componentSource") else {})}
