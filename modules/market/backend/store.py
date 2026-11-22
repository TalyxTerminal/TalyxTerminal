"""Local series storage: raw rows in time-partitioned Parquet plus one pointer.

Each stored series is (instrument, capability, price basis, session scope, base
period[, imported dataset]). Its directory holds Parquet parts (one-minute bars by
month, daily and longer in one file, other intraday periods by year) and
``pointer.json``: which parts are current, which time spans were fetched ("have")
or confirmed empty by the source ("none"), which spans no layer of the source could give
when last asked ("unavailable": until when not to ask again, and why), the source of the
rows and the revision.
A write lands new part files first and then replaces the pointer atomically; there
are no receipts or per-read checksums (Parquet pages carry their own).
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from talyx_sdk.i18n import t

from vocabulary.identity import timeframe_nanoseconds

from . import bars
from .common import DAY_NS, NS, MarketError

ROW_BYTES = {"1m": 25, "1d": 40}


# Decoded parts kept in memory (about 60 MB of one-minute bars).
TABLE_CACHE_ROWS = 1_000_000


def _between(table: pa.Table, start: int | None, end: int | None) -> pa.Table:
    """Rows with start <= time < end."""
    if start is None and end is None:
        return table
    # Each immutable part is sorted and deduplicated when it is written.
    times = table.column("time").to_numpy()
    first = 0 if start is None else int(times.searchsorted(start))
    last = len(times) if end is None else int(times.searchsorted(end))
    return table.slice(first, max(0, last - first))


@dataclass(frozen=True, slots=True)
class SeriesKey:
    instrument_id: str
    capability: str
    price_basis: str
    session_scope: str
    timeframe: str | None
    import_id: str | None = None
    environment: str = "production"

    @property
    def name(self) -> str:
        text = f"{self.capability}.{self.price_basis}.{self.session_scope}.{self.timeframe or 'none'}"
        name = text + (f".import-{self.import_id}" if self.import_id else "")
        return name if self.environment == "production" else f"{name}@{self.environment}"

    @classmethod
    def parse(cls, instrument_id: str, name: str) -> SeriesKey:
        name, _, environment = name.partition("@")
        parts = name.split(".")
        import_id = parts[4][len("import-"):] if len(parts) > 4 else None
        return cls(instrument_id, parts[0], parts[1], parts[2], None if parts[3] == "none" else parts[3], import_id,
                   environment or "production")


def partition_unit(timeframe: str | None) -> str:
    step = None if timeframe is None else timeframe_nanoseconds(timeframe)
    if step is None or step >= DAY_NS:
        return "all"
    return "month" if step <= 60 * NS else "year"


def partition_of(unit: str, times: np.ndarray) -> np.ndarray:
    if unit == "all":
        return np.zeros(len(times), dtype=np.int64)
    as_dates = times.astype("datetime64[ns]")
    return (as_dates.astype("datetime64[M]") if unit == "month" else as_dates.astype("datetime64[Y]")).astype(np.int64)


def partition_label(unit: str, value: int) -> str:
    if unit == "all":
        return "all"
    return str(np.datetime64(value, "M" if unit == "month" else "Y"))


def partition_bounds(unit: str, label: str) -> tuple[int, int]:
    if unit == "all":
        return -(2**62), 2**62
    step = "M" if unit == "month" else "Y"
    start = np.datetime64(label, step)
    end = start + np.timedelta64(1, step)
    return int(start.astype("datetime64[ns]").astype(np.int64)), int(end.astype("datetime64[ns]").astype(np.int64))


class Spans:
    """Sorted, non-overlapping [start, end) spans with a state ("have" or "none")."""

    @staticmethod
    def add(spans: list[list], start: int, end: int, state: str) -> list[list]:
        if end <= start:
            return spans
        out = []
        for a, b, s in spans:
            if b <= start or a >= end:
                out.append([a, b, s])
                continue
            if a < start:
                out.append([a, start, s])
            if b > end:
                out.append([end, b, s])
        out.append([start, end, state])
        out.sort()
        merged = []
        for a, b, s in out:
            if merged and merged[-1][2] == s and merged[-1][1] >= a:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b, s])
        return merged

    @staticmethod
    def missing(spans: list[list], start: int, end: int) -> list[tuple[int, int]]:
        gaps, cursor = [], start
        for a, b, _s in spans:
            if b <= cursor:
                continue
            if a >= end:
                break
            if a > cursor:
                gaps.append((cursor, min(a, end)))
            cursor = max(cursor, b)
            if cursor >= end:
                break
        if cursor < end:
            gaps.append((cursor, end))
        return gaps


class SeriesStore:
    def __init__(self, root: Path):
        self.root = root / "series"
        self.root.mkdir(parents=True, exist_ok=True)
        self._pointers: dict[SeriesKey, dict] = {}
        self._locks: dict[SeriesKey, threading.RLock] = {}
        self._guard = threading.Lock()
        self.on_commit = None
        # Parts never change once written (a write makes a new file), so their decoded tables are kept by name.
        self._tables: OrderedDict[Path, pa.Table] = OrderedDict()
        self._table_rows = 0
        self._tables_lock = threading.Lock()
        # Parts a reader is reading (path -> readers): a write that replaces one leaves the file until the last of
        # them is done (``_doomed``), then deletes it. Taking a pointer's parts and swapping a pointer share ``_pins``.
        self._pins = threading.Lock()
        self._reading: dict[Path, int] = {}
        self._doomed: set[Path] = set()

    def directory(self, key: SeriesKey) -> Path:
        return self.root / key.instrument_id / key.name

    def lock(self, key: SeriesKey) -> threading.RLock:
        with self._guard:
            return self._locks.setdefault(key, threading.RLock())

    def pointer(self, key: SeriesKey) -> dict | None:
        cached = self._pointers.get(key)
        if cached is not None:
            return cached
        path = self.directory(key) / "pointer.json"
        with self.lock(key):
            cached = self._pointers.get(key)    # a write may have landed while this waited for the lock
            if cached is not None:
                return cached
            try:
                value = json.loads(_patiently(lambda: path.read_text(encoding="utf-8")))
            except FileNotFoundError:
                return None
            self._pointers[key] = value
        return value

    def _pinned(self, key: SeriesKey) -> tuple[dict | None, list[Path]]:
        """The current pointer and its part files, held for reading until ``_unpin``."""
        while True:
            pointer = self.pointer(key)
            with self._pins:
                if pointer is not None and self._pointers.get(key) is not pointer:
                    continue                    # replaced meanwhile: take the new one
                paths = [self.directory(key) / name for name in (pointer or {"parts": {}})["parts"].values()]
                for path in paths:
                    self._reading[path] = self._reading.get(path, 0) + 1
                return pointer, paths

    def _unpin(self, paths: list[Path]) -> None:
        done = []
        with self._pins:
            for path in paths:
                self._reading[path] -= 1
                if not self._reading[path]:
                    del self._reading[path]
                    if path in self._doomed:
                        self._doomed.discard(path)
                        done.append(path)
        for path in done:
            _patiently(lambda path=path: path.unlink(missing_ok=True))

    def keys(self, instrument_id: str | None = None, *, environment: str | None = "production") -> list[SeriesKey]:
        roots = [self.root / instrument_id] if instrument_id else [p for p in self.root.iterdir() if p.is_dir()]
        found = []
        for root in roots:
            if not root.is_dir():
                continue
            for child in root.iterdir():
                if (child / "pointer.json").is_file():
                    key = SeriesKey.parse(root.name, child.name)
                    if environment is None or key.environment == environment:
                        found.append(key)
        return found

    def read(self, key: SeriesKey, start: int | None = None, end: int | None = None) -> bars.Columns:
        pointer, pinned = self._pinned(key)
        try:
            if pointer is None or not pointer["parts"]:
                return {}
            unit = partition_unit(key.timeframe)
            directory = self.directory(key)
            tables = []
            for label in sorted(pointer["parts"]):
                lo, hi = partition_bounds(unit, label)
                if (end is not None and lo >= end) or (start is not None and hi <= start):
                    continue
                tables.append(_between(self._table(directory / pointer["parts"][label]), start, end))
        finally:
            self._unpin(pinned)
        if not tables:
            return {}
        table = pa.concat_tables(tables)
        return {name: table.column(name).to_numpy() for name in table.column_names}

    def last_rows(self, key: SeriesKey, before: int | None, limit: int) -> bars.Columns:
        """The newest ``limit`` rows strictly before ``before``, reading parts newest first."""
        pointer, pinned = self._pinned(key)
        try:
            if pointer is None or not pointer["parts"]:
                return {}
            unit = partition_unit(key.timeframe)
            collected, count = [], 0
            for label in sorted(pointer["parts"], reverse=True):
                lo, _hi = partition_bounds(unit, label)
                if before is not None and lo >= before:
                    continue
                table = _between(self._table(self.directory(key) / pointer["parts"][label]), None, before)
                if table.num_rows:
                    collected.append(table)
                    count += table.num_rows
                if count >= limit:
                    break
        finally:
            self._unpin(pinned)
        if not collected:
            return {}
        table = pa.concat_tables(collected[::-1]).sort_by("time")
        table = table.slice(max(table.num_rows - limit, 0))
        return {name: table.column(name).to_numpy() for name in table.column_names}

    def write(self, key: SeriesKey, columns: bars.Columns | None, *, fetched: list[tuple[int, int]] = (),
              none: list[tuple[int, int]] = (), unavailable: list[tuple[int, int, int, dict]] = (),
              source: str | None = None, revision: int | None = None, repaired: list[dict] = ()) -> dict:
        """Merge rows and spans into the stored series; returns the new pointer. ``repaired`` holds the source's
        original values of rows written with high and low widened; a row written again replaces its record.
        ``unavailable`` (start, end, retry at, problem) are spans the source could not give; a later answer for any
        part of them replaces that part."""
        with self.lock(key):
            directory = self.directory(key)
            directory.mkdir(parents=True, exist_ok=True)
            old = self.pointer(key) or {"revision": 0, "source": None, "spans": [], "parts": {}, "rows": 0,
                                        "bytes": 0, "first": None, "last": None}
            if source is not None and old["source"] is not None and old["source"] != source and old["parts"]:
                raise MarketError("invalid_input", t("A series holds one source's rows; switch sources by replacing it"))
            pointer = json.loads(json.dumps(old))
            if source is not None and old["source"] != source and not old["parts"]:
                pointer.update(spans=[], unavailable=[])
            changed = False
            stale = []
            if columns and bars.length(columns):
                unit = partition_unit(key.timeframe)
                labels = partition_of(unit, columns["time"])
                for value in np.unique(labels):
                    label = partition_label(unit, int(value))
                    part = bars.take(columns, labels == value)
                    if label in pointer["parts"]:
                        previous = self._read_part(directory / pointer["parts"][label])
                        part = bars.merge(previous, part)
                        stale.append(pointer["parts"][label])
                    else:
                        part = bars.sort_unique(part)
                    name = f"{label}.{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}.parquet"
                    self._write_part(directory / name, part)
                    pointer["parts"][label] = name
                changed = True
                written = set(columns["time"].tolist())
                kept = [item for item in pointer.get("repaired", []) if int(item["time"]) not in written]
                pointer["repaired"] = sorted(kept + list(repaired), key=lambda item: int(item["time"]))
            for start, end in fetched:
                pointer["spans"] = Spans.add(pointer["spans"], start, end, "have")
            for start, end in none:
                pointer["spans"] = Spans.add(pointer["spans"], start, end, "none")
            answered = [*fetched, *none, *((a, b) for a, b, _retry, _problem in unavailable)]
            if answered:
                pointer["unavailable"] = sorted(
                    [[a, b, retry, problem] for start, end, retry, problem in pointer.get("unavailable", [])
                     for a, b in _outside(start, end, answered)]
                    + [[a, b, retry, problem] for a, b, retry, problem in unavailable if b > a])
            if source is not None:
                pointer["source"] = source
            if changed:
                pointer["revision"] = max(pointer["revision"] + 1, revision or 0)
                self._summarize(directory, pointer)
            self._commit(key, directory, pointer, stale)
            if changed and self.on_commit:
                self.on_commit(key, pointer)
            return pointer

    def switch_source(self, key: SeriesKey, columns: bars.Columns, spans: list[list], source: str) -> bool:
        """Keep the current series unless every stored timestamp has a replacement."""
        with self.lock(key):
            held = self.read(key)["time"]
            if not np.isin(held, columns["time"], assume_unique=True).all():
                return False
            self.replace(key, columns, spans, source)
            return True

    def replace(self, key: SeriesKey, columns: bars.Columns, spans: list[list], source: str) -> dict:
        """Swap the whole series to another source's rows in one pointer replace."""
        with self.lock(key):
            directory = self.directory(key)
            directory.mkdir(parents=True, exist_ok=True)
            old = self.pointer(key) or {"revision": 0, "parts": {}}
            unit = partition_unit(key.timeframe)
            parts = {}
            if bars.length(columns):
                labels = partition_of(unit, columns["time"])
                stamp = datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')
                for value in np.unique(labels):
                    label = partition_label(unit, int(value))
                    name = f"{label}.{stamp}.parquet"
                    self._write_part(directory / name, bars.sort_unique(bars.take(columns, labels == value)))
                    parts[label] = name
            pointer = {"revision": old["revision"] + 1, "source": source, "spans": spans, "parts": parts}
            self._summarize(directory, pointer)
            self._commit(key, directory, pointer, list(old["parts"].values()))
            if self.on_commit:
                self.on_commit(key, pointer)
            return pointer

    def bump(self, key: SeriesKey, revision: int) -> None:
        """Raise the revision (adjustment factors changed) without touching rows."""
        with self.lock(key):
            pointer = self.pointer(key)
            if pointer is None or pointer["revision"] >= revision:
                return
            pointer = {**pointer, "revision": revision}
            self._commit(key, self.directory(key), pointer, [])
            if self.on_commit:
                self.on_commit(key, pointer)

    def delete(self, key: SeriesKey) -> None:
        with self.lock(key):
            self._pointers.pop(key, None)
            shutil.rmtree(self.directory(key), ignore_errors=True)

    def move(self, key: SeriesKey, target: SeriesKey) -> None:
        """Another instrument's copy of the same series becomes ``target`` (instruments merged)."""
        self.delete(target)
        with self.lock(key), self.lock(target):
            self._pointers.pop(key, None)
            destination = self.directory(target)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(self.directory(key), destination)
        source_root = self.root / key.instrument_id
        if source_root.is_dir() and not any(source_root.iterdir()):
            source_root.rmdir()

    def _summarize(self, directory: Path, pointer: dict) -> None:
        rows = size = 0
        first = last = None
        for name in pointer["parts"].values():
            metadata = pq.read_metadata(directory / name)
            rows += metadata.num_rows
            size += (directory / name).stat().st_size
            stats = metadata.row_group(0).column(0).statistics if metadata.num_row_groups else None
            if stats is not None and stats.has_min_max:
                first = stats.min if first is None else min(first, stats.min)
            if metadata.num_row_groups:
                tail = metadata.row_group(metadata.num_row_groups - 1).column(0).statistics
                if tail is not None and tail.has_min_max:
                    last = tail.max if last is None else max(last, tail.max)
        pointer.update(rows=rows, bytes=size, first=first, last=last)

    def _commit(self, key: SeriesKey, directory: Path, pointer: dict, stale: list[str]) -> None:
        temp = directory / "pointer.json.tmp"
        temp.write_text(json.dumps(pointer), encoding="utf-8")
        _patiently(lambda: os.replace(temp, directory / "pointer.json"))
        unused = []
        with self._pins:
            self._pointers[key] = pointer
            for name in stale:
                if name not in pointer["parts"].values():
                    path = directory / name
                    # A reader that took the old pointer reads it to the end; the last one deletes it.
                    (self._doomed.add if path in self._reading else unused.append)(path)
        for path in unused:
            _patiently(lambda path=path: path.unlink(missing_ok=True))

    def _table(self, path: Path) -> pa.Table:
        with self._tables_lock:
            table = self._tables.get(path)
            if table is not None:
                self._tables.move_to_end(path)
                return table
        with pq.ParquetFile(path) as part:
            table = part.read(use_threads=False)
        with self._tables_lock:
            if path not in self._tables:
                self._tables[path] = table
                self._table_rows += table.num_rows
            while self._table_rows > TABLE_CACHE_ROWS and len(self._tables) > 1:
                _, old = self._tables.popitem(last=False)
                self._table_rows -= old.num_rows
        return table

    @staticmethod
    def _read_part(path: Path) -> bars.Columns:
        table = pq.read_table(path)
        return {name: table.column(name).to_numpy() for name in table.column_names}

    @staticmethod
    def _write_part(path: Path, columns: bars.Columns) -> None:
        table = pa.table({name: pa.array(values) for name, values in columns.items()})
        try:
            pq.write_table(table, path, compression="zstd")
        except OSError as exc:
            if getattr(exc, "errno", None) == 28 or "No space" in str(exc) or "not enough space" in str(exc):
                raise MarketError("disk_full", t("The disk is full; Market stopped writing")) from exc
            raise


class DocumentStore:
    """Small JSON documents per instrument (corporate actions, factors).

    Market is the only writer, so a document read once is kept: a later read (a live bar's adjustment, on the
    event loop) touches no file. Callers treat what ``read`` returns as read-only and ``write`` a new value.
    """

    def __init__(self, root: Path, name: str):
        self.root = root / name
        self.root.mkdir(parents=True, exist_ok=True)
        self._documents: dict[str, dict | None] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    # One document's file steps share its lock: on Windows a file being replaced cannot be opened, and a file
    # open for reading cannot be replaced (both fail with PermissionError). A virus scanner or the search
    # indexer holds a new file the same way for a moment, so each step is tried a few times (_patiently).

    def _lock(self, instrument_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(instrument_id, threading.Lock())

    def read(self, instrument_id: str) -> dict | None:
        if instrument_id in self._documents:
            return self._documents[instrument_id]
        path = self.root / f"{instrument_id}.json"
        with self._lock(instrument_id):
            if instrument_id not in self._documents:
                try:
                    value = json.loads(_patiently(lambda: path.read_text(encoding="utf-8")))
                except FileNotFoundError:
                    value = None
                self._documents[instrument_id] = value
            return self._documents[instrument_id]

    def write(self, instrument_id: str, value: dict) -> None:
        with self._lock(instrument_id):
            temp = self.root / f"{instrument_id}.json.tmp"
            temp.write_text(json.dumps(value), encoding="utf-8")
            _patiently(lambda: os.replace(temp, self.root / f"{instrument_id}.json"))
            self._documents[instrument_id] = value

    def delete(self, instrument_id: str) -> None:
        with self._lock(instrument_id):
            (self.root / f"{instrument_id}.json").unlink(missing_ok=True)
            self._documents[instrument_id] = None

    def ids(self) -> list[str]:
        return [path.stem for path in self.root.glob("*.json")]


def _outside(start: int, end: int, spans) -> list[tuple[int, int]]:
    """The parts of [start, end) none of ``spans`` covers."""
    parts = [(start, end)]
    for a, b in spans:
        parts = [piece for lo, hi in parts for piece in ((lo, min(hi, a)), (max(lo, b), hi)) if piece[1] > piece[0]]
    return parts


def _patiently(step, tries: int = 8):
    """Run a file step again while Windows refuses it because another handle (a reader, a virus scanner,
    the search indexer) holds the file; about 1.4 s in all before the refusal stands."""
    for attempt in range(tries):
        try:
            return step()
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(0.05 * (attempt + 1))
