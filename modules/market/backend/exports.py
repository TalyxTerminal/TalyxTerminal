"""Exports: selected series and research rows written as long-format files in bounded memory.

One file per content (``ohlcv:<timeframe>:<adjustment>``, ``<capability>:<timeframe>``,
``research:<kind>``); Parquet files get one row group per instrument and are written
instrument by instrument. Bar files carry instrumentId, market, the code in force on
each row's date, time, closeTime and knownAt, then the values as float64. Two clocks, two
meanings: knownAt is when the market knew the bar's values (its close); the publish cutoff
is when the source gives them as final, which only decides when Market fetches the bar. Adjusted prices are computed at export. ``fill=missing``
first downloads what is missing through a fetch job. Files go to the caller as
artifacts Market holds for 24 hours, or into a folder the user chose.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pa_csv
import pyarrow.parquet as pq
from talyx_sdk.i18n import t

from vocabulary.instruments import MarketSeries

from . import bars
from .common import DAY_NS, MarketError, now_ns, time_range
from .routing import in_worker

MAX_GAPS = 2000
MAX_FAILURES = 200
EXPORT_CHUNK = 100


def content_of(series: MarketSeries) -> str:
    if series.capability == "ohlcv":
        return f"ohlcv:{series.timeframe}:{series.adjustment}"
    return f"{series.capability}:{series.timeframe}" if series.timeframe else series.capability


def code_column(codes: list[dict], times: np.ndarray, fallback: str) -> list[str]:
    """The code in force at each row's time."""
    if not codes:
        return [fallback] * len(times)
    starts = np.asarray([int(c["from"]) if c["from"] is not None else -(2**62) for c in codes], dtype=np.int64)
    index = np.searchsorted(starts, times, side="right") - 1
    names = [c["code"] for c in codes]
    return [names[max(i, 0)] for i in index.tolist()]


class Writer:
    """One output file, appended to instrument by instrument."""

    def __init__(self, path: Path, file_format: str):
        self.path, self.format = path, file_format
        self.writer = None
        self.handle = None
        self.csv = None
        self.rows = 0
        self.instruments = 0
        self.columns: list[str] = []
        self.first: int | None = None
        self.last: int | None = None
        self.sources: dict[str, dict] = {}

    def append(self, table: pa.Table) -> None:
        if table.num_rows == 0:
            return
        if self.format == "parquet":
            if self.writer is None:
                self.writer = pq.ParquetWriter(self.path, table.schema)
                self.columns = table.column_names
            self.writer.write_table(table, row_group_size=max(table.num_rows, 1))
        else:
            table = _csv_table(table)
            if self.csv is None:
                self.handle = self.path.open("wb")
                self.columns = table.column_names
                # Column names are plain words; text values (research rows hold commas) are quoted by Arrow.
                self.handle.write((",".join(self.columns) + "\n").encode())
                self.csv = pa_csv.CSVWriter(self.handle, table.schema, write_options=pa_csv.WriteOptions(include_header=False))
            self.csv.write_table(table)
        self.rows += table.num_rows
        self.instruments += 1

    def close(self) -> None:
        if self.writer is not None:
            self.writer.close()
        if self.csv is not None:
            self.csv.close()
        if self.handle is not None:
            self.handle.close()


def _csv_table(table: pa.Table) -> pa.Table:
    """Times as ISO text in UTC to the second (2024-12-31T21:00:00Z); written by Arrow, not row by row in Python
    (1.4 million rows took 30 s that way)."""
    def text(column: pa.ChunkedArray):
        if not pa.types.is_timestamp(column.type):
            return column
        seconds = pc.cast(column, pa.timestamp("s", tz="UTC"), safe=False).to_numpy().astype("datetime64[s]")
        return pa.array(np.char.add(np.datetime_as_string(seconds, unit="s"), "Z"), mask=pc.is_null(column).to_numpy())
    return pa.table({name: text(table.column(name)) for name in table.column_names})


async def _write_worker(func, *args):
    # A cancelled export must wait for its writer before closing or removing the staged file.
    task = asyncio.create_task(in_worker(func, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


class Exports:
    def __init__(self, market):
        self.market = market
        self.staging = market.root / "exports"

    async def export(self, value: dict, *, workspace_id: str | None = None, progress=None) -> dict:
        destination = value.get("destination")
        parent = await self.market.folder(destination["folder"], workspace_id) if destination else self.staging
        parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".market-export-", dir=parent) as staging:
            result = await self._write(value, Path(staging), workspace_id, progress)
            if destination:
                for file in result["files"]:
                    staged = Path(file["path"])
                    file["path"] = str(staged.replace(parent / staged.name))
            return result

    async def _write(self, value, folder, workspace_id, progress):
        destination = value.get("destination")
        file_format = value.get("format", "parquet")
        start = int(value["start"]) if value.get("start") else None
        end = int(value["end"]) if value.get("end") else None
        environment = value.get("environment", "production")
        override = value.get("adjustment")
        series_items, research_items, materials = [], [], []
        for selection in value["selections"]:
            if "instrument" in selection:
                instrument = self.market.instrument_record(selection["instrument"])
                series = MarketSeries.from_wire(selection["series"])
                if series.capability == "ohlcv" and override and series.adjustment != override \
                        and instrument.get("adjustmentEligible", False):
                    series = MarketSeries.from_wire(_with_adjustment(series, override))
                series_items.append((instrument, series, selection.get("start", start), selection.get("end", end)))
            elif "research" in selection:
                research_items.append(selection)
            else:
                materials.append(selection["professionalMaterial"])
        failures: list[dict] = []
        if value.get("fill", "missing") == "missing" and series_items:
            windows = defaultdict(list)
            for instrument, series, lo, hi in series_items:
                windows[lo, hi].append((instrument, series))
            for (lo, hi), group in windows.items():
                failures.extend(await self.fill(group, lo, hi, environment, workspace_id, progress))
        failed_ids = {f["instrumentId"] for f in failures}
        if value.get("fill", "missing") == "missing":
            for instrument, series, lo, hi in series_items:
                if series.adjustment == "split_dividend" and instrument["instrumentId"] not in failed_ids:
                    plan = await in_worker(self.market.data.plan, instrument, series, lo, environment=environment)
                    await self.market.data.fill_reference_closes(plan, None, lo, hi)
        writers: dict[str, Writer] = {}
        fixed: list[tuple[str, Path, int]] = []
        gaps: list[dict] = []
        try:
            # Series go to a worker thread a chunk at a time: one hop per series made each wait for the Host's busy event
            # loop (595 daily series took 12 s in the Host against 4 s alone); between chunks a cancel still lands.
            todo = [item for item in series_items if item[0]["instrumentId"] not in failed_ids]
            for chunk_start in range(0, len(todo), EXPORT_CHUNK):
                chunk = todo[chunk_start:chunk_start + EXPORT_CHUNK]
                await _write_worker(self.write_series, chunk, writers, folder, file_format,
                                value.get("allowEmpty", False), failures, gaps, environment)
                if progress:
                    progress({"done": chunk_start + len(chunk), "total": len(todo), "waitingFor": None,
                              "waitingUntil": None, "message": t("exporting")})
            for selection in research_items:
                await self.research_file(selection, folder, file_format, writers, value)
            for selection in materials:
                fixed.append(await _write_worker(self.material_file, selection, folder))
        finally:
            for writer in writers.values():
                writer.close()
        files = []
        for content, writer in writers.items():
            if writer.rows == 0:
                continue
            artifact = None
            path = str(writer.path) if destination else None
            if not destination:
                artifact = await self.market.handoff.publish(writer.path, "market-data", writer.path.name)
            files.append({"content": content, "artifact": artifact, "rows": writer.rows,
                          "instruments": writer.instruments, "columns": writer.columns,
                          "range": time_range(writer.first, writer.last + 1) if writer.first is not None else None,
                          "sources": list(writer.sources.values()), "path": path})
        for content, path, rows in fixed:
            artifact = None if destination else await self.market.handoff.publish(path, "market-data", path.name)
            material = self.market.professional.store.read(*content[len("professional:"):].split("@"))[0]
            files.append({"content": content, "artifact": artifact, "rows": rows, "instruments": 0, "columns": [],
                          "range": None, "sources": [{"sourceId": material["sourceId"], "name": material["name"]}],
                          "path": str(path) if destination else None})
        if not files and not value.get("allowEmpty"):
            raise MarketError("no_data", t("Nothing to export: no selected series has stored rows"),
                              details={"failures": failures[:20]})
        return {"files": files, "gaps": gaps[:MAX_GAPS], "failures": failures[:MAX_FAILURES]}

    def write_series(self, chunk, writers: dict, folder: Path, file_format: str, allow_empty: bool,
                     failures: list[dict], gaps: list[dict], environment: str) -> None:
        """Some series into their content's file (one file per content), in a worker thread."""
        for instrument, series, start, end in chunk:
            content = content_of(series)
            writer = writers.get(content)
            if writer is None:
                name = content.replace(":", "_") + (".parquet" if file_format == "parquet" else ".csv")
                writer = writers[content] = Writer(folder / name, file_format)
            table, source, series_gaps = self.table(instrument, series, start, end, environment)
            gaps.extend(series_gaps)
            if table is None:
                if not allow_empty:
                    failures.append({"instrumentId": instrument["instrumentId"], "problem": {
                        "code": "no_data", "message": t("No stored rows for {symbol}", symbol=instrument['symbol']),
                        "sourceId": source, "status": None, "retryAt": None}})
                continue
            writer.append(table)
            times = table.column("time")
            first, last = int(times[0].value), int(times[-1].value)
            writer.first = first if writer.first is None else min(writer.first, first)
            writer.last = last if writer.last is None else max(writer.last, last)
            if source:
                writer.sources[source] = self.market.source_ref(source)

    def material_file(self, selection: dict, folder: Path) -> tuple[str, Path, int]:
        """One professional material version as a JSON file: its record and every row, unchanged."""
        material, rows = self.market.professional.store.read(selection["materialId"], selection["version"])
        path = folder / f"professional_{material['materialId']}_{material['version'][:12]}.json"
        path.write_text(json.dumps({"material": material, "rows": rows}, ensure_ascii=False), encoding="utf-8")
        return f"professional:{material['materialId']}@{material['version']}", path, len(rows)

    async def fill(self, items, start, end, environment, workspace_id, progress) -> list[dict]:
        """Download what is missing (a fetch job); instruments whose download failed are reported."""
        kinds = {}
        for _instrument, series in items:
            if series.capability == "ohlcv":
                kinds[(series.timeframe, series.priceBasis)] = {"kind": "bars", "timeframe": series.timeframe,
                                                                "priceBasis": series.priceBasis}
        failures = []
        for kind in kinds.values():
            targets = [{"instrument": instrument} for instrument, series in items
                       if series.capability == "ohlcv" and series.timeframe == kind["timeframe"]]
            request = {"targets": targets, "kinds": [kind], "start": str(start) if start else None,
                       "end": str(end) if end else None, "environment": environment}
            job_result = await self.market.jobs.fetch(request, workspace_id=workspace_id,
                                                      progress=progress)
            job = self.market.jobs.get(job_result["jobId"])
            for item in job.record["items"]:
                if item["state"] == "failed":
                    failures.append({"instrumentId": item["instrument"]["instrumentId"], "problem": item["problem"]})
        return failures

    def table(self, instrument: dict, series: MarketSeries, start, end, environment="production") -> tuple[pa.Table | None, str | None, list]:
        data = self.market.data
        plan = data.plan(instrument, series, start, local_only=True, environment=environment)
        pointer = data.store.pointer(plan.key)
        source = pointer.get("source") if pointer else None
        if pointer is None or pointer.get("first") is None:
            return None, source, []
        lo = start if start is not None else int(pointer["first"])
        hi = end if end is not None else now_ns() + DAY_NS
        columns = data.local_rows(plan, lo, hi)
        gaps = [{"instrumentId": instrument["instrumentId"], "seriesId": series.seriesId, "gap": gap}
                for gap in data.gaps(plan, lo, min(hi, now_ns()))]
        if not columns or not bars.length(columns):
            return None, source, gaps
        times = columns["time"]
        count = len(times)
        close_times = columns.get("close_time", times)
        codes = self.market.identity.codes(instrument["instrumentId"]) if instrument["identityVersion"] == 2 else []
        arrays = {
            "instrumentId": pa.array([instrument["instrumentId"]] * count, pa.string()),
            "market": pa.array([instrument["venueId"]] * count, pa.string()),
            "code": pa.array(code_column(codes, times, instrument["symbol"]), pa.string()),
            "time": pa.array(times.astype("datetime64[ns]"), pa.timestamp("ns", tz="UTC")),
            "closeTime": pa.array(close_times.astype("datetime64[ns]"), pa.timestamp("ns", tz="UTC")),
            "knownAt": pa.array(close_times.astype("datetime64[ns]"), pa.timestamp("ns", tz="UTC")),
        }
        for name, values in columns.items():
            if name not in ("time", "close_time"):
                arrays[name] = pa.array(np.asarray(values, dtype=np.float64), pa.float64())
        return pa.table(arrays), source, gaps

    async def research_file(self, selection: dict, folder: Path, file_format: str, writers: dict, value: dict):
        kind = selection["research"]
        content = f"research:{kind}"
        name = content.replace(":", "_") + (".parquet" if file_format == "parquet" else ".csv")
        writer = writers.setdefault(content, Writer(folder / name, file_format))
        request = {"kind": kind, "limit": 5000, "fill": "await" if value.get("fill", "missing") == "missing" else "none"}
        if selection.get("index"):
            request["index"] = selection["index"]
        if selection.get("instruments"):
            request["instruments"] = selection["instruments"]
        for name_ in ("start", "end"):
            if value.get(name_):
                request[name_] = value[name_]
        cursor = None
        while True:
            page = await self.market.research.read({**request, "cursor": cursor})
            columns = page["columns"]
            if columns and len(next(iter(columns.values()))):
                table = pa.table({key: pa.array([None if v is None else str(v) for v in values], pa.string())
                                  for key, values in columns.items()})
                await _write_worker(writer.append, table)
            cursor = page["nextCursor"]
            if cursor is None:
                break


def _with_adjustment(series: MarketSeries, adjustment: str) -> dict:
    from vocabulary.instruments import MarketSeries as Series
    value = series.to_dict()
    value.pop("seriesId", None)
    value["adjustment"] = adjustment
    if adjustment == "raw":
        value["adjustmentAnchor"] = None
    fields = {k: v for k, v in value.items() if k != "identityVersion"}
    return Series(**fields).to_dict()
