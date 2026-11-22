"""The research library's store: a small SQLite catalog beside columnar value files.

The catalog holds items, versions with their state and cursor, one row per period a version produced (or failed to), the update log,
the last failed tries' logs, who uses which version, the figures after publication and one summary row per item that the list reads.
Values live in Parquet files, one per publication and one per update, registered in the catalog in the same transaction that moves the
cursor: a file the catalog does not list is never read, so a crash between writing a file and committing leaves nothing half done.

A version's `state` is what the rest of Quant reads: updating (producing), failed, expired (no new bars for several periods), delisted
(stopped by a person) or fixed (one-off data). Archiving a stopped version drops its values and keeps the record (`archived_at`).
`auto` says whether it produces a new value every period; `pending` is the newest due period still waiting for its bar. Each summary is
rebuilt in the transaction of every change to its item.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path
from uuid import uuid4

from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from . import library_summary
from .index_universe import member_spans
from .index_universe import summary as index_summary

STATES = ("updating", "failed", "expired", "delisted", "fixed")
STATE_FIELDS = ("state", "failure", "retry_after", "pending", "misses")  # what an update's outcome may not change on a version stopped while it ran
KINDS = ("factor", "model", "allocation", "data")
MODES = ("history", "scheduled", "catch_up")
RESULTS = ("ok", "late", "fail", "miss", "up")
COLUMNS = ("instrument", "field", "datetime", "value", "availableAt", "recordedAt", "mode")
FAILURE_LOGS = 5
ROW_GROUP = 50_000
EXCLUSIONS = "index-exclusions.json"  # beside a version's values: the exclusions of the index it was published from
SCHEMA = """
CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS versions (
  id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items(id), version INTEGER NOT NULL, state TEXT NOT NULL, auto INTEGER NOT NULL DEFAULT 1,
  object_id TEXT, run_id TEXT, workspace_id TEXT, meta TEXT NOT NULL, cursor INTEGER, misses INTEGER NOT NULL DEFAULT 0, miss_cursor INTEGER,
  failure TEXT, retry_after INTEGER, after TEXT, pending TEXT, stopped_at INTEGER, archived_at INTEGER, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  UNIQUE(item_id, version));
CREATE TABLE IF NOT EXISTS value_files (version_id TEXT NOT NULL REFERENCES versions(id), name TEXT NOT NULL, rows INTEGER NOT NULL,
  first_datetime INTEGER, last_datetime INTEGER, last_available INTEGER, created_at INTEGER NOT NULL, PRIMARY KEY(version_id, name));
CREATE TABLE IF NOT EXISTS periods (version_id TEXT NOT NULL REFERENCES versions(id), label INTEGER NOT NULL, due_at INTEGER, result TEXT NOT NULL,
  ready_at INTEGER, names INTEGER, distinct_values INTEGER, tries INTEGER NOT NULL DEFAULT 0, took REAL, why TEXT, PRIMARY KEY(version_id, label));
CREATE TABLE IF NOT EXISTS days (version_id TEXT NOT NULL REFERENCES versions(id), label INTEGER NOT NULL, names INTEGER NOT NULL, PRIMARY KEY(version_id, label));
CREATE TABLE IF NOT EXISTS updates (id INTEGER PRIMARY KEY AUTOINCREMENT, version_id TEXT NOT NULL, at INTEGER NOT NULL, outcome TEXT NOT NULL,
  rows INTEGER NOT NULL, first_datetime INTEGER, last_datetime INTEGER, mode TEXT, message TEXT);
CREATE INDEX IF NOT EXISTS updates_by ON updates(version_id, id);
CREATE TABLE IF NOT EXISTS failure_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, version_id TEXT NOT NULL, at INTEGER NOT NULL, label INTEGER,
  message TEXT NOT NULL, location TEXT, traceback TEXT, output TEXT);
CREATE TABLE IF NOT EXISTS update_attempts (version_id TEXT PRIMARY KEY REFERENCES versions(id), started_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS occupancy (version_id TEXT NOT NULL, owner_id TEXT NOT NULL, workspace_id TEXT, kind TEXT, name TEXT, how TEXT,
  created_at INTEGER NOT NULL, PRIMARY KEY(version_id, owner_id));
CREATE TABLE IF NOT EXISTS after_points (version_id TEXT PRIMARY KEY REFERENCES versions(id), content TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS summaries (item_id TEXT PRIMARY KEY, content TEXT NOT NULL, updated_at INTEGER NOT NULL);
"""


def now():
    return int(time.time() * 1000)


def item_kind(kind):
    """A model's predictions and its trained model are one item, a model."""
    return "model" if kind == "prediction" else kind


class LibraryStore:
    """Each call opens its own connection, so writes can run on worker threads and never wait on the Host's loop."""

    def __init__(self, root: Path, clock=now):
        self.root = Path(root)
        self.path = self.root / "library.sqlite3"
        self.values_root = self.root / "values"
        self.clock = clock
        self.values_root.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)
            db.execute("PRAGMA journal_mode=WAL")
        self._sweep_files()
        self.resummarize()   # a stored summary is derived: made again on opening, it has what the code now reads

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=60)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db

    def _sweep_files(self):
        """Value files the catalog does not list are what a crash left before its commit: they go."""
        with self.connect() as db:
            listed = {(row["version_id"], row["name"]) for row in db.execute("SELECT version_id, name FROM value_files")}
        for folder in self.values_root.iterdir():
            if not folder.is_dir():
                continue
            for path in folder.glob("*.parquet"):
                if (folder.name, path.stem) not in listed:
                    path.unlink(missing_ok=True)

    @staticmethod
    def version_row(row):
        value = dict(row)
        value["meta"] = json.loads(value["meta"])
        for key in ("failure", "after", "pending"):
            value[key] = json.loads(value[key]) if value[key] else None
        value["auto"] = bool(value["auto"])
        return value

    # --- reads -------------------------------------------------------------------------------------------------------
    @staticmethod
    def summary_row(row):
        summary = json.loads(row["content"])
        summary["stateLabel"] = t(library_summary.LABEL[summary["state"]])
        for period in summary["last20"]:
            if period["result"] in ("ok", "late") and period["why"]:
                period["why"] = t(period["why"])
        return summary

    def summaries(self, query=None):
        """What the list shows, one stored row per item; nothing is computed or read from the values here."""
        with self.connect() as db:
            rows = [self.summary_row(row) for row in db.execute("SELECT content FROM summaries")]
        if query:
            rows = [row for row in rows if query.casefold() in row["name"].casefold()]
        return library_summary.ordered(rows)

    def summary_of(self, item_id):
        with self.connect() as db:
            row = db.execute("SELECT content FROM summaries WHERE item_id=?", (item_id,)).fetchone()
        if row is None:
            raise ContractError("not_found", t("This library item does not exist."))
        return self.summary_row(row)

    def version(self, version_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM versions WHERE id=?", (version_id,)).fetchone()
            if row is None:
                raise ContractError("not_found", t("This library version does not exist."))
            return self.version_row(row)

    def version_number(self, version_id):
        """A version's number alone, without parsing its record."""
        with self.connect() as db:
            row = db.execute("SELECT version FROM versions WHERE id=?", (version_id,)).fetchone()
        if row is None:
            raise ContractError("not_found", t("This library version does not exist."))
        return row["version"]

    def versions(self, version_ids):
        with self.connect() as db:
            marks = ",".join("?" * len(version_ids))
            return {row["id"]: self.version_row(row) for row in db.execute(f"SELECT * FROM versions WHERE id IN ({marks})", list(version_ids))} if version_ids else {}

    def versions_of(self, db, item_id):
        return [self.version_row(row) for row in db.execute("SELECT * FROM versions WHERE item_id=? ORDER BY version DESC", (item_id,))]

    def all_versions(self):
        with self.connect() as db:
            return [self.version_row(row) for row in db.execute("SELECT * FROM versions ORDER BY item_id, version")]

    def version_states(self):
        """Every version's object, number and state, without its record (meta, up to a few MB for one published from an index): what
        the object list shows of them."""
        with self.connect() as db:
            return [{**dict(row), "auto": bool(row["auto"])} for row in db.execute(
                "SELECT id, version, state, auto, object_id, archived_at FROM versions ORDER BY item_id, version")]

    def summary_kind(self, item_id):
        with self.connect() as db:
            row = db.execute("SELECT kind FROM items WHERE id=?", (item_id,)).fetchone()
            return row["kind"] if row else None

    def summary_name(self, version_id):
        """A version as people read it: the item's name and the version number."""
        with self.connect() as db:
            row = db.execute("SELECT items.name, versions.version FROM versions JOIN items ON items.id=versions.item_id WHERE versions.id=?", (version_id,)).fetchone()
            return f"{row['name']} v{row['version']}" if row else version_id

    def item(self, item_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            if row is None:
                raise ContractError("not_found", t("This library item does not exist."))
            return dict(row)

    def version_summary(self, db, version):
        """One version as callers outside the library read it: its state, cursor, fields and who holds it."""
        item = db.execute("SELECT name, kind FROM items WHERE id=?", (version["item_id"],)).fetchone()
        files = db.execute("SELECT COALESCE(SUM(rows),0), MIN(first_datetime), MAX(last_datetime), MAX(last_available) FROM value_files WHERE version_id=?",
                           (version["id"],)).fetchone()
        occupied = [dict(row) for row in db.execute("SELECT owner_id, workspace_id, kind, name, how FROM occupancy WHERE version_id=?", (version["id"],))]
        meta = version["meta"]
        return {"versionId": version["id"], "itemId": version["item_id"], "name": item["name"], "kind": item["kind"], "version": version["version"],
                "state": version["state"], "auto": version["auto"], "objectId": version["object_id"], "runId": version["run_id"], "workspaceId": version["workspace_id"],
                "createdAt": version["created_at"], "updatedAt": version["updated_at"], "cursor": version["cursor"], "misses": version["misses"],
                "failure": version["failure"], "retryAfter": version["retry_after"], "stoppedAt": version["stopped_at"], "archivedAt": version["archived_at"],
                "rows": files[0], "firstDatetime": files[1], "lastDatetime": files[2], "lastAvailableAt": files[3], "fields": meta.get("fields", []),
                "timeframe": meta.get("timeframe"), "occupiedBy": occupied, "origin": meta.get("origin"), "model": meta.get("model")}

    def item_versions(self, item_id):
        with self.connect() as db:
            if db.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone() is None:
                raise ContractError("not_found", t("This library item does not exist."))
            return [self.version_summary(db, version) for version in self.versions_of(db, item_id)]

    def find_item(self, name, kind=None):
        kind = item_kind(kind) if kind else None
        with self.connect() as db:
            rows = db.execute("SELECT * FROM items WHERE name=?" + (" AND kind=?" if kind else ""), (name, kind) if kind else (name,)).fetchall()
            return [dict(row) for row in rows]

    def continuing(self):
        """Versions the production loop looks after: every producing version with auto-update on, whichever is newest (a newer version never stops
        an older one) and whether or not anything uses it."""
        with self.connect() as db:
            rows = db.execute("SELECT * FROM versions WHERE state IN ('updating','failed') AND auto=1 AND archived_at IS NULL").fetchall()
            return [self.version_row(row) for row in rows]

    def periods(self, version_id, limit=None, since=None):
        """A version's periods, oldest first."""
        clauses, values = ["version_id=?"], [version_id]
        if since is not None:
            clauses.append("label>?")
            values.append(since)
        query = "SELECT * FROM periods WHERE " + " AND ".join(clauses) + " ORDER BY label DESC" + (" LIMIT ?" if limit else "")
        with self.connect() as db:
            rows = [dict(row) for row in db.execute(query, [*values, *([limit] if limit else [])])]
        for row in rows:
            if row["result"] in ("ok", "late") and row["why"]:
                row["why"] = t(row["why"])
        return rows[::-1]

    def period(self, version_id, label):
        with self.connect() as db:
            row = db.execute("SELECT * FROM periods WHERE version_id=? AND label=?", (version_id, label)).fetchone()
            if row is None:
                return None
            period = dict(row)
            if period["result"] in ("ok", "late") and period["why"]:
                period["why"] = t(period["why"])
            return period

    def failure_logs(self, version_id):
        with self.connect() as db:
            return [{**dict(row), "location": json.loads(row["location"]) if row["location"] else None}
                    for row in db.execute("SELECT * FROM failure_logs WHERE version_id=? ORDER BY id DESC", (version_id,))]

    def catalog(self):
        """Each item's versions that hold values and are not archived, with what a data source lists of them."""
        with self.connect() as db:
            rows = db.execute("""SELECT items.id AS item_id, items.name, items.kind, versions.id AS version_id, versions.version, versions.state, versions.failure,
                versions.meta, versions.auto FROM items JOIN versions ON versions.item_id=items.id WHERE versions.archived_at IS NULL
                ORDER BY lower(items.name), versions.version DESC""").fetchall()
            out = []
            for row in rows:
                meta = json.loads(row["meta"])
                if not meta.get("fields"):
                    continue
                instruments = meta.get("instrumentCount")
                out.append({**dict(row), "meta": meta, "failure": json.loads(row["failure"]) if row["failure"] else None, "instruments": instruments})
            return out

    def published_runs(self):
        """Every run something was published from: run id -> its versions (newest first) with their state."""
        with self.connect() as db:
            rows = db.execute("""SELECT versions.run_id, versions.id, versions.version, versions.state, versions.archived_at, items.name, items.id AS item_id
                FROM versions JOIN items ON items.id=versions.item_id WHERE versions.run_id IS NOT NULL ORDER BY versions.version DESC""").fetchall()
        out = {}
        for row in rows:
            out.setdefault(row["run_id"], []).append({"versionId": row["id"], "version": row["version"], "itemId": row["item_id"], "name": row["name"],
                                                      "state": library_summary.public_state({"archived_at": row["archived_at"], "state": row["state"]})})
        return out

    # --- values ------------------------------------------------------------------------------------------------------------
    def files(self, version_id):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM value_files WHERE version_id=? ORDER BY first_datetime, name", (version_id,))]

    def value_path(self, version_id, name):
        return self.values_root / version_id / f"{name}.parquet"

    def exclusions(self, version_id):
        """The index's exclusions of a version published from index data (each name's stretches without prices): kept beside its values,
        not in its record, which is read whole by every page that shows the version."""
        return json.loads((self.values_root / version_id / EXCLUSIONS).read_text(encoding="utf-8"))

    def days(self, version_id):
        """The days a version has values for, with how many names had one: kept in the catalog, written with the values."""
        with self.connect() as db:
            return [(row["label"], row["names"]) for row in db.execute("SELECT label, names FROM days WHERE version_id=? ORDER BY label", (version_id,))]

    def frame(self, version_id, *, field=None, instrument=None, start=None, end=None, as_of=None, after=None, datetime=None, columns=None):
        """Values of one version as a table, read only from the files (and row groups) that can hold matching rows."""
        import pandas as pd
        import pyarrow.compute as pc
        import pyarrow.dataset as ds
        found = self.files(version_id)
        chosen = [item for item in found if not (start is not None and item["last_datetime"] is not None and item["last_datetime"] < start)
                  and not (end is not None and item["first_datetime"] is not None and item["first_datetime"] > end)
                  and not (datetime is not None and item["first_datetime"] is not None and not item["first_datetime"] <= datetime <= item["last_datetime"])
                  and not (after is not None and item["last_available"] is not None and item["last_available"] <= after)]
        wanted = list(columns or COLUMNS)
        if not chosen:
            return pd.DataFrame({name: pd.Series(dtype="object" if name in ("instrument", "field", "mode") else "float64" if name == "value" else "int64") for name in wanted})
        expression = None
        for column, value, operator in (("field", field, "eq"), ("instrument", instrument, "eq"), ("datetime", start, "ge"), ("datetime", end, "le"),
                                        ("availableAt", as_of, "le"), ("availableAt", after, "gt"), ("datetime", datetime, "eq")):
            if value is None:
                continue
            term = getattr(pc, {"eq": "equal", "ge": "greater_equal", "le": "less_equal", "gt": "greater"}[operator])(pc.field(column), value)
            expression = term if expression is None else expression & term
        dataset = ds.dataset([str(self.value_path(version_id, item["name"])) for item in chosen], format="parquet")
        table = dataset.to_table(columns=wanted, filter=expression)
        return table.to_pandas()

    def read_values(self, version_id, *, field=None, instrument=None, start=None, end=None, as_of=None, after=None):
        """Rows of one version, oldest first; `as_of` keeps what could be known by then, `after` only what became available later than that moment."""
        frame = self.frame(version_id, field=field, instrument=instrument, start=start, end=end, as_of=as_of, after=after)
        frame = frame.sort_values(["datetime", "instrument", "field"], kind="stable")
        return [{"datetime": int(row.datetime), "instrument": row.instrument, "field": row.field, "value": float(row.value), "availableAt": int(row.availableAt),
                 "recordedAt": int(row.recordedAt), "mode": row.mode} for row in frame.itertuples(index=False)]

    def last_available(self, version_id, *, field, instrument=None):
        frame = self.frame(version_id, field=field, instrument=instrument, columns=["availableAt"])
        return int(frame["availableAt"].max()) if len(frame) else None

    def latest_available(self, version_id, as_of, *, field=None):
        """The newest value of each instrument (and field) that could be known at `as_of`: what a decision at that moment may use."""
        frame = self.frame(version_id, field=field, as_of=as_of)
        if frame.empty:
            return []
        frame = frame.sort_values(["datetime", "availableAt"], kind="stable").groupby(["instrument", "field"], as_index=False).tail(1).sort_values(["instrument", "field"])
        return [{"datetime": int(row.datetime), "instrument": row.instrument, "field": row.field, "value": float(row.value), "availableAt": int(row.availableAt),
                 "recordedAt": int(row.recordedAt), "mode": row.mode} for row in frame.itertuples(index=False)]

    @staticmethod
    def table_of(rows):
        """Rows (tuples in COLUMNS order, or a table with those columns) as an Arrow table sorted by date, so a day reads one row group."""
        import pyarrow as pa
        if hasattr(rows, "columns"):
            table = pa.Table.from_pandas(rows[list(COLUMNS)].sort_values(["datetime", "instrument"], kind="stable"), preserve_index=False)
        else:
            ordered = sorted(rows, key=lambda row: (row[2], row[0]))
            table = pa.table({name: pa.array([row[index] for row in ordered]) for index, name in enumerate(COLUMNS)})
        return table.cast(pa.schema([("instrument", pa.string()), ("field", pa.string()), ("datetime", pa.int64()), ("value", pa.float64()),
                                     ("availableAt", pa.int64()), ("recordedAt", pa.int64()), ("mode", pa.string())]))

    def _write_file(self, version_id, rows, prefix="u"):
        """Rows to a new Parquet file of this version; returns the file's catalog entry and the names per day it holds.
        `h` names the publication's history, `u` an update, `c` updates merged."""
        import pyarrow.compute as pc
        import pyarrow.parquet as pq
        table = self.table_of(rows)
        name = f"{prefix}{self.clock():013d}-{uuid4().hex[:8]}"
        path = self.value_path(version_id, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, path, row_group_size=ROW_GROUP, compression="zstd")
        count = table.num_rows
        days = []
        if count:
            first, last = pc.min(table["datetime"]).as_py(), pc.max(table["datetime"]).as_py()
            available = pc.max(table["availableAt"]).as_py()
            counted = table.group_by("datetime").aggregate([("instrument", "count_distinct")])
            days = list(zip(counted["datetime"].to_pylist(), counted["instrument_count_distinct"].to_pylist(), strict=True))
            names = pc.count_distinct(table["instrument"]).as_py()
        else:
            first = last = available = None
            names = 0
        return {"name": name, "rows": count, "first_datetime": first, "last_datetime": last, "last_available": available, "path": path, "days": days, "names": names}

    @staticmethod
    def _register(db, version_id, entry, stamp):
        db.execute("INSERT INTO value_files VALUES (?,?,?,?,?,?,?)", (version_id, entry["name"], entry["rows"], entry["first_datetime"], entry["last_datetime"],
                                                                     entry["last_available"], stamp))
        db.executemany("INSERT INTO days VALUES (?,?,?) ON CONFLICT(version_id, label) DO UPDATE SET names=max(names, excluded.names)",
                       ((version_id, label, names) for label, names in entry["days"]))

    # --- writes ------------------------------------------------------------------------------------------------------------
    def commit_versions(self, specs):
        """Everything a publication adds, in one transaction: each item if new, its version and its history file.

        A spec gives name, kind, versionId, meta, rows (tuples in COLUMNS order, or a table), state, cursor, objectId, runId, workspaceId and
        optionally auto (on unless said) and checks (the publication's figures). Nothing stays behind when any step fails."""
        stamp = self.clock()
        written = []
        try:
            for spec in specs:
                written.append(self._write_file(spec["versionId"], spec["rows"], "h") if len(spec["rows"]) else None)
                if "index" in spec["meta"].get("info", {}):
                    path = self.values_root / spec["versionId"] / EXCLUSIONS
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(spec["meta"]["info"]["index"]["exclusions"]), encoding="utf-8")
            with self.connect() as db:
                created = []
                for spec, entry in zip(specs, written, strict=True):
                    kind = item_kind(spec["kind"])
                    if kind not in KINDS:
                        raise ContractError("invalid_input", t("The library keeps factors, models, allocations and data, not {kind}.", kind=kind))
                    item = db.execute("SELECT id, kind FROM items WHERE name=?", (spec["name"],)).fetchone()
                    if item is None:
                        item_id = str(uuid4())
                        db.execute("INSERT INTO items(id, name, kind, created_at) VALUES (?,?,?,?)", (item_id, spec["name"], kind, stamp))
                    elif item["kind"] != kind:
                        raise ContractError("conflict", t("{name} is already a {kind} in the library; choose another name.", name=spec['name'], kind=item['kind']), {"kind": item["kind"]})
                    else:
                        item_id = item["id"]
                    number = (db.execute("SELECT MAX(version) FROM versions WHERE item_id=?", (item_id,)).fetchone()[0] or 0) + 1
                    meta = {**spec["meta"], **({"published": spec["checks"]} if spec.get("checks") else {})}
                    if "instrumentCount" not in meta and entry is not None:
                        meta["instrumentCount"] = entry["names"]
                    if "index" in meta.get("info", {}):
                        # The version keeps the index's summary: its whole membership (50-66 MB for the S&P 500 over ten years) was parsed on every read
                        # of the version, some seven times to open its page; an update prepares its own data and never reads it from here.
                        # It keeps each member's stretches too, so a day's names without a value say whether they were members; the
                        # exclusions (up to 22 MB) are in their own file (`exclusions`).
                        meta["info"] = {**meta["info"], "index": index_summary(meta["info"]["index"]) | {"spans": member_spans(meta["info"]["index"])}}
                    db.execute("""INSERT INTO versions(id, item_id, version, state, auto, object_id, run_id, workspace_id, meta, cursor, created_at, updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (spec["versionId"], item_id, number, spec["state"], 1 if spec.get("auto", True) else 0, spec["objectId"],
                                                           spec["runId"], spec["workspaceId"], json.dumps(meta), spec["cursor"], stamp, stamp))
                    if entry is not None:
                        self._register(db, spec["versionId"], entry, stamp)
                    for owner in spec.get("holds", []):
                        # What a published version reads (a model's factor versions) is held by it, so it shows among their users.
                        db.execute("INSERT OR REPLACE INTO occupancy VALUES (?,?,?,?,?,?,?)", (owner["versionId"], "qlib:" + spec["versionId"], spec["workspaceId"],
                                                                                         kind, f"{spec['name']} v{number}", owner.get("how") or f"v{number} reads it every period", stamp))
                        held = db.execute("SELECT item_id FROM versions WHERE id=?", (owner["versionId"],)).fetchone()
                        if held is None:
                            raise ContractError("not_found", t("A library version this publication reads does not exist."))
                    created.append({"itemId": item_id, "versionId": spec["versionId"], "version": number})
                for item_id in {row["itemId"] for row in created} | {self._item_of(db, owner["versionId"]) for spec in specs for owner in spec.get("holds", [])}:
                    self._summarize(db, item_id)
                return created
        except BaseException:
            for spec, _ in zip(specs, written, strict=False):
                shutil.rmtree(self.values_root / spec["versionId"], ignore_errors=True)
            raise

    @staticmethod
    def _item_of(db, version_id):
        row = db.execute("SELECT item_id FROM versions WHERE id=?", (version_id,)).fetchone()
        return row["item_id"] if row else None

    def append_values(self, version_id, rows, *, cursor, checks=None, expected_cursor=None, periods=None, after=None, points=None, mode=None, fields=None):
        """New rows of one update and the cursor that says they are in, together with the periods they settle, the update log entry, the figures
        after publication and the item's summary. A cursor at or behind the stored one changes nothing (the period is in already); another update
        that moved it first is a conflict."""
        stamp = self.clock()
        entry = self._write_file(version_id, rows) if len(rows) else None
        try:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                version = db.execute("SELECT cursor, item_id, state, archived_at FROM versions WHERE id=?", (version_id,)).fetchone()
                current = version["cursor"]
                if expected_cursor is not None and current != expected_cursor:
                    raise ContractError("conflict", t("This library period has already been advanced by another update."))
                if current is not None and cursor <= current:
                    if entry is not None:
                        entry["path"].unlink(missing_ok=True)
                    return False
                modes = {row[6] for row in rows} if not hasattr(rows, "columns") else set(rows["mode"])
                labels = [row[2] for row in rows] if not hasattr(rows, "columns") else rows["datetime"].tolist()
                db.execute("INSERT INTO updates(version_id, at, outcome, rows, first_datetime, last_datetime, mode) VALUES (?,?,?,?,?,?,?)",
                           (version_id, stamp, "updated", len(rows), min(labels, default=None), max(labels, default=None),
                            mode or ("catch_up" if "catch_up" in modes else "scheduled" if modes else None)))
                if entry is not None:
                    self._register(db, version_id, entry, stamp)
                changes = {"cursor": cursor, "state": "updating", "failure": None, "retry_after": None, "updated_at": stamp, **(fields or {})}
                if version["state"] == "delisted" or version["archived_at"] is not None:
                    # Stopped while this update ran: what it made is kept, the version stays stopped.
                    changes = {key: value for key, value in changes.items() if key not in STATE_FIELDS}
                if after is not None:
                    changes["after"] = after
                self._mark(db, version_id, changes)
                if checks:
                    meta = json.loads(db.execute("SELECT meta FROM versions WHERE id=?", (version_id,)).fetchone()["meta"])
                    db.execute("UPDATE versions SET meta=? WHERE id=?", (json.dumps({**meta, "checks": {**meta.get("checks", {}), **checks}}), version_id))
                if points is not None:
                    db.execute("INSERT OR REPLACE INTO after_points VALUES (?,?)", (version_id, json.dumps(points)))
                for period in periods or []:
                    self._put_period(db, version_id, period)
                db.execute("DELETE FROM update_attempts WHERE version_id=?", (version_id,))
                self._summarize(db, version["item_id"])
        except BaseException:
            if entry is not None:
                entry["path"].unlink(missing_ok=True)
            raise
        return True

    @staticmethod
    def _put_period(db, version_id, period):
        """A period's row. A period that has its value is final: a later try cannot turn it into a failure or a miss."""
        found = db.execute("SELECT result, tries FROM periods WHERE version_id=? AND label=?", (version_id, period["label"])).fetchone()
        if found is not None and found["result"] in ("ok", "late"):
            return
        tries = period.get("tries", (found["tries"] if found else 0))
        db.execute("INSERT OR REPLACE INTO periods VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (version_id, period["label"], period.get("due_at"), period["result"], period.get("ready_at"), period.get("names"),
                    period.get("distinct_values"), tries, period.get("took"), period.get("why")))

    def put_periods(self, version_id, periods, **fields):
        """Periods that did not produce (missing, failed, upstream missing) and the version fields that go with them, in one transaction."""
        with self.connect() as db:
            for period in periods:
                self._put_period(db, version_id, period)
            found = db.execute("SELECT state, archived_at FROM versions WHERE id=?", (version_id,)).fetchone()
            if found is not None and (found["state"] == "delisted" or found["archived_at"] is not None):
                # Stopped while an update ran: its outcome does not bring the version back as failed or waiting (it was then tried every 10 minutes).
                fields = {key: value for key, value in fields.items() if key not in STATE_FIELDS}
            if fields:
                self._mark(db, version_id, fields)
            self._summarize(db, db.execute("SELECT item_id FROM versions WHERE id=?", (version_id,)).fetchone()["item_id"])

    def after_points(self, version_id):
        with self.connect() as db:
            row = db.execute("SELECT content FROM after_points WHERE version_id=?", (version_id,)).fetchone()
            return json.loads(row["content"]) if row else None

    def begin_update(self, version_id):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO update_attempts VALUES (?,?)", (version_id, self.clock()))

    def finish_update(self, version_id):
        with self.connect() as db:
            db.execute("DELETE FROM update_attempts WHERE version_id=?", (version_id,))

    def recover_updates(self):
        """Unfinished attempts belong to the previous Host: each is logged once as interrupted; no value, cursor or missing streak changes."""
        with self.connect() as db:
            db.execute("INSERT INTO updates(version_id,at,outcome,rows,message) SELECT version_id,?,'interrupted',0,? FROM update_attempts",
                       (self.clock(), "The Host or module stopped before this update completed."))
            db.execute("DELETE FROM update_attempts")

    def log_update(self, version_id, outcome, message, *, failure=None):
        """An update that added nothing (failed, interrupted, expired), as the log shows it; a failure keeps its traceback, the newest five only."""
        stamp = self.clock()
        with self.connect() as db:
            db.execute("INSERT INTO updates(version_id, at, outcome, rows, message) VALUES (?,?,?,0,?)", (version_id, stamp, outcome, message))
            db.execute("DELETE FROM update_attempts WHERE version_id=?", (version_id,))
            if failure is not None:
                db.execute("INSERT INTO failure_logs(version_id, at, label, message, location, traceback, output) VALUES (?,?,?,?,?,?,?)",
                           (version_id, stamp, failure.get("label"), message, json.dumps(failure.get("location")) if failure.get("location") else None,
                            failure.get("traceback"), failure.get("output")))
                db.execute("DELETE FROM failure_logs WHERE version_id=? AND id NOT IN (SELECT id FROM failure_logs WHERE version_id=? ORDER BY id DESC LIMIT ?)",
                           (version_id, version_id, FAILURE_LOGS))

    def updates(self, *, version_id=None, item_id=None, limit=50):
        """The newest updates first, of one version or of every version of an item."""
        clause, value = ("updates.version_id=?", version_id) if version_id else ("versions.item_id=?", item_id)
        with self.connect() as db:
            rows = db.execute("SELECT updates.*, versions.version FROM updates JOIN versions ON versions.id=updates.version_id WHERE " + clause
                              + " ORDER BY updates.id DESC LIMIT ?", (value, limit)).fetchall()
            return [{"at": row["at"], "versionId": row["version_id"], "version": row["version"], "outcome": row["outcome"], "values": row["rows"],
                     "firstDatetime": row["first_datetime"], "lastDatetime": row["last_datetime"], "mode": row["mode"], "message": row["message"]} for row in rows]

    def _mark(self, db, version_id, fields):
        assignments = {"updated_at": self.clock(), **fields}
        for key in ("failure", "after", "pending"):
            if key in assignments and assignments[key] is not None:
                assignments[key] = json.dumps(assignments[key])
        if "auto" in assignments:
            assignments["auto"] = 1 if assignments["auto"] else 0
        db.execute("UPDATE versions SET " + ", ".join(f"{key}=?" for key in assignments) + " WHERE id=?", (*assignments.values(), version_id))

    def mark(self, version_id, **fields):
        with self.connect() as db:
            self._mark(db, version_id, fields)
            row = db.execute("SELECT item_id FROM versions WHERE id=?", (version_id,)).fetchone()
            if row is not None:
                self._summarize(db, row["item_id"])

    def merge_meta(self, version_id, **changes):
        with self.connect() as db:
            meta = json.loads(db.execute("SELECT meta FROM versions WHERE id=?", (version_id,)).fetchone()["meta"])
            db.execute("UPDATE versions SET meta=?, updated_at=? WHERE id=?", (json.dumps({**meta, **changes}), self.clock(), version_id))

    # --- the monthly review of an allocation's period --------------------------------------------------------------------------
    def add_adjustment(self, version_id, label, weights, reason, at):
        """The period's one-off adjustment: a second set of values for the same `datetime`, every asset of the pool (a held-at-zero one too, or it would still read
        the computed weight), available from `at` and recorded now. A reader that asks as of a moment before `at` still gets the computed set; after it, this one.
        The version's `meta.reviews` keeps what was decided and why. The cursor and the period's own result do not move. A period is reviewed once."""
        stamp = self.clock()
        rows = [(str(asset), "weight", int(label), float(weight), int(at), stamp, "one_off") for asset, weight in sorted(weights.items())]
        entry = self._write_file(version_id, rows, "a")
        try:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                found = db.execute("SELECT meta, item_id FROM versions WHERE id=?", (version_id,)).fetchone()
                meta = json.loads(found["meta"])
                if str(label) in meta.get("reviews", {}):
                    raise ContractError("conflict", t("This period was already reviewed."), {"period": label})
                self._register(db, version_id, entry, stamp)
                meta["reviews"] = {**meta.get("reviews", {}), str(label): {"how": "one_off", "reason": reason, "at": int(at)}}
                db.execute("UPDATE versions SET meta=?, updated_at=? WHERE id=?", (json.dumps(meta), stamp, version_id))
                self._summarize(db, found["item_id"])
        except BaseException:
            entry["path"].unlink(missing_ok=True)
            raise

    def accept_period(self, version_id, label, at):
        """The period is taken as computed; nothing is written but the decision."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            found = db.execute("SELECT meta, item_id FROM versions WHERE id=?", (version_id,)).fetchone()
            meta = json.loads(found["meta"])
            if str(label) in meta.get("reviews", {}):
                raise ContractError("conflict", t("This period was already reviewed."), {"period": label})
            meta["reviews"] = {**meta.get("reviews", {}), str(label): {"how": "accepted", "at": int(at)}}
            db.execute("UPDATE versions SET meta=?, updated_at=? WHERE id=?", (json.dumps(meta), self.clock(), version_id))
            self._summarize(db, found["item_id"])

    # --- use, stop, archive ------------------------------------------------------------------------------------------------------
    def acquire(self, version_id, owner_id, workspace_id, *, kind=None, name=None, how=None):
        with self.connect() as db:
            version = db.execute("SELECT state, archived_at, item_id FROM versions WHERE id=?", (version_id,)).fetchone()
            if version is None:
                raise ContractError("not_found", t("This library version does not exist."))
            if version["state"] == "delisted" or version["archived_at"] is not None:
                raise ContractError("conflict", t("A stopped version cannot be used by anything new."))
            db.execute("INSERT OR REPLACE INTO occupancy VALUES (?,?,?,?,?,?,?)", (version_id, owner_id, workspace_id, kind, name, how, self.clock()))
            self._summarize(db, version["item_id"])

    def release(self, version_id, owner_id):
        with self.connect() as db:
            db.execute("DELETE FROM occupancy WHERE version_id=? AND owner_id=?", (version_id, owner_id))
            row = db.execute("SELECT item_id FROM versions WHERE id=?", (version_id,)).fetchone()
            if row is not None:
                self._summarize(db, row["item_id"])

    def held_by(self, owner_id):
        with self.connect() as db:
            return {row["version_id"] for row in db.execute("SELECT version_id FROM occupancy WHERE owner_id=?", (owner_id,))}

    def occupants(self, item_id):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT occupancy.* FROM occupancy JOIN versions ON versions.id=occupancy.version_id WHERE versions.item_id=?",
                                                    (item_id,))]

    def users(self, version_id):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM occupancy WHERE version_id=? ORDER BY created_at", (version_id,))]

    def stop(self, version_id):
        """Stop producing one version; the objects that use it keep the values made so far and are told."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("SELECT * FROM versions WHERE id=?", (version_id,)).fetchone()
            if version is None:
                raise ContractError("not_found", t("This library version does not exist."))
            if version["archived_at"] is not None:
                raise ContractError("conflict", t("This version is archived already."))
            if version["state"] != "delisted":
                self._mark(db, version_id, {"state": "delisted", "stopped_at": self.clock(), "retry_after": None, "pending": None})
            self._summarize(db, version["item_id"])
        return self.users(version_id)

    def resume(self, version_id):
        """Produce a stopped version again from where it stopped: the periods due while it was stopped are made now, known from now (as turning auto-update
        back on fills the periods it missed)."""
        with self.connect() as db:
            version = db.execute("SELECT * FROM versions WHERE id=?", (version_id,)).fetchone()
            if version is None:
                raise ContractError("not_found", t("This library version does not exist."))
            if version["archived_at"] is not None:
                raise ContractError("conflict", t("An archived version has no values to continue from; publish again from its run."))
            meta = json.loads(version["meta"])
            if meta.get("oneOff"):
                self._mark(db, version_id, {"state": "fixed", "stopped_at": None})
                self._summarize(db, version["item_id"])
                return
            if version["state"] not in ("delisted", "expired", "failed"):
                raise ContractError("invalid_input", t("This version is producing already."))
            self._mark(db, version_id, {"state": "updating", "stopped_at": None, "misses": 0, "auto": True, "failure": None, "retry_after": None, "pending": None})
            # Kept so the log can say why those periods came late: they were due while it was stopped.
            if version["stopped_at"] is not None:
                meta["stops"] = [*meta.get("stops", []), [version["stopped_at"], self.clock()]]
                db.execute("UPDATE versions SET meta=? WHERE id=?", (json.dumps(meta), version_id))
            self._summarize(db, version["item_id"])

    def set_auto(self, version_id, on):
        with self.connect() as db:
            version = db.execute("SELECT state, item_id, archived_at FROM versions WHERE id=?", (version_id,)).fetchone()
            if version is None:
                raise ContractError("not_found", t("This library version does not exist."))
            if version["state"] in ("delisted", "fixed") or version["archived_at"] is not None:
                raise ContractError("invalid_input", t("Only a producing version has auto-update."))
            self._mark(db, version_id, {"auto": bool(on), **({} if on else {"pending": None})})
            self._summarize(db, version["item_id"])

    def archive(self, version_id):
        """A stopped version nobody uses keeps its record, code and published figures; its values go. Returns what was dropped."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("SELECT * FROM versions WHERE id=?", (version_id,)).fetchone()
            if version is None:
                raise ContractError("not_found", t("This library version does not exist."))
            if version["archived_at"] is not None:
                raise ContractError("conflict", t("This version is archived already."))
            if version["state"] != "delisted":
                raise ContractError("conflict", t("Stop this version first; a stopped version nobody uses can be archived."))
            users = [dict(row) for row in db.execute("SELECT * FROM occupancy WHERE version_id=?", (version_id,))]
            if users:
                raise ContractError("conflict", t("Something still uses this version: {users}.", users=', '.join(row['name'] or row['owner_id'] for row in users)),
                                    {"users": users})
            rows = db.execute("SELECT COALESCE(SUM(rows),0) FROM value_files WHERE version_id=?", (version_id,)).fetchone()[0]
            db.execute("DELETE FROM value_files WHERE version_id=?", (version_id,))
            db.execute("DELETE FROM days WHERE version_id=?", (version_id,))
            db.execute("DELETE FROM after_points WHERE version_id=?", (version_id,))
            # What it read is no longer held by it.
            held = [row["version_id"] for row in db.execute("SELECT version_id FROM occupancy WHERE owner_id=?", ("qlib:" + version_id,))]
            db.execute("DELETE FROM occupancy WHERE owner_id=?", ("qlib:" + version_id,))
            self._mark(db, version_id, {"archived_at": self.clock()})
            for item_id in {version["item_id"], *(self._item_of(db, other) for other in held)}:
                self._summarize(db, item_id)
        shutil.rmtree(self.values_root / version_id, ignore_errors=True)
        return {"rows": rows, "meta": json.loads(version["meta"])}

    def compact(self, version_id, limit=40):
        """Many small update files are merged into one, swapped in one transaction; readers never see a period twice or not at all."""
        import pandas as pd
        small = [item for item in self.files(version_id) if item["name"].startswith("u")]
        if len(small) < limit:
            return False
        frame = pd.concat([pd.read_parquet(self.value_path(version_id, item["name"])) for item in small], ignore_index=True)
        entry = self._write_file(version_id, frame, "c")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for item in small:
                db.execute("DELETE FROM value_files WHERE version_id=? AND name=?", (version_id, item["name"]))
            self._register(db, version_id, entry, self.clock())
        for item in small:
            self.value_path(version_id, item["name"]).unlink(missing_ok=True)
        return True

    # --- summaries -------------------------------------------------------------------------------------------------------------
    def _summarize(self, db, item_id):
        item = db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if item is None:
            return
        versions = []
        for version in self.versions_of(db, item_id):
            periods = [dict(row) for row in db.execute("SELECT * FROM periods WHERE version_id=? ORDER BY label DESC LIMIT 60", (version["id"],))][::-1]
            users = [dict(row) for row in db.execute("SELECT owner_id, workspace_id, kind, name, how FROM occupancy WHERE version_id=?", (version["id"],))]
            last = db.execute("SELECT MAX(last_datetime) FROM value_files WHERE version_id=?", (version["id"],)).fetchone()[0]
            newest = db.execute("SELECT names FROM days WHERE version_id=? ORDER BY label DESC LIMIT 1", (version["id"],)).fetchone()
            inputs = []
            for entry in version["meta"].get("inputs", []):
                found = db.execute("SELECT state, archived_at, cursor, failure FROM versions WHERE id=?", (entry["versionId"],)).fetchone()
                inputs.append({**entry, "state": found["state"] if found else "archived", "cursor": found["cursor"] if found else None})
            versions.append({"version": version, "periods": periods, "users": users, "lastDatetime": last, "lastNames": None if newest is None else newest["names"], "inputs": inputs})
        content = library_summary.item_summary(dict(item), versions, self.clock())
        db.execute("INSERT OR REPLACE INTO summaries VALUES (?,?,?)", (item_id, json.dumps(content), self.clock()))

    def resummarize(self):
        """Every summary again (time moved: a late period became a day old, a pending period settled)."""
        with self.connect() as db:
            for row in db.execute("SELECT id FROM items").fetchall():
                self._summarize(db, row["id"])
