"""Thin work/run index. Native libraries own experiments and financial results."""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from . import changes
from .runtime import ACTIVITY, ACTIVITY_RUN, call_attribution


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


class WorkRecords:
    def __init__(self, root: Path):
        self.on_change = None
        self.on_trigger = None
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        # The page's and the Agent's reads of runs run on worker threads so they do not hold the event loop. One connection used by
        # several threads at once fails ("bad parameter or other API misuse"), so `db` is this thread's own connection: the one opened here
        # for the thread that made the records, one more for each worker thread (WAL lets them read while another writes).
        self._owner = threading.get_ident()
        self._local = threading.local()
        self._opened = []
        self._main = self._connect()
        self._main.execute("PRAGMA journal_mode=WAL")
        self._writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="quant-records")
        self._writer_db = None
        self._reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="quant-observations")
        self._reader_db = None
        self._write_lock = asyncio.Lock()
        self.db.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, content TEXT, PRIMARY KEY(kind,id))")
        self.db.execute("CREATE TABLE IF NOT EXISTS workspace_copies (workspace TEXT, original TEXT, copy TEXT, PRIMARY KEY(workspace,original))")
        changes.install(self.db)
        self.db.execute("CREATE INDEX IF NOT EXISTS record_created ON records(kind, json_extract(content,'$.createdAt'), id)")
        # An object's runs of one kind (its held-out validations, walk-forwards, sweeps), newest first, are found without reading every run's record.
        self.db.execute("CREATE INDEX IF NOT EXISTS record_runs ON records(kind, json_extract(content,'$.objectId'), json_extract(content,'$.runKind'), "
                        "json_extract(content,'$.createdAt'), id)")
        from .triggers import QuantTriggers
        self.triggers = QuantTriggers(self.db)
        from .run_flow import interrupted
        interrupted(self)

    def _connect(self):
        found = sqlite3.connect(self.root / "work-records.sqlite3", timeout=30, check_same_thread=False)
        found.row_factory = sqlite3.Row
        self._opened.append(found)
        return found

    @property
    def db(self):
        if threading.get_ident() == self._owner:
            return self._main
        found = getattr(self._local, "db", None)
        if found is None:
            found = self._local.db = self._connect()
        return found

    def put(self, kind, record):
        record = {**record, "updatedAt": int(time.time() * 1000)}
        with self.db:
            previous, facts = self._persist_record(self.db, kind, record)
        if self.on_change:
            self.on_change(kind, record, previous)
        self.publish_facts(facts)
        if kind == "qrun" and ACTIVITY_RUN.get() == record["id"]:
            activity = ACTIVITY.get()
            if previous is None or previous.get("progress") != record.get("progress") or previous.get("stage") != record.get("stage"):
                progress = record.get("progress") or {}
                activity.progress({"runId":record["id"], "number":record.get("number"),"stage":record.get("stage"),
                                   **{k:progress[k] for k in ("done","total","simulatedTo") if k in progress}})
            if record["state"] in ("cancelled", "interrupted", "failed"):
                activity.outcome = "failed" if record["state"] == "failed" else "stopped"
        return record

    def _persist_record(self, db, kind, record):
        previous = db.execute("SELECT content FROM records WHERE kind=? AND id=?", (kind, record["id"])).fetchone()
        previous = json.loads(previous[0]) if previous is not None else None
        if kind == "qrun" and "callId" not in record:
            record.update(call_attribution() if previous is None else {
                "callId": previous.get("callId"), "callOrigin": previous.get("callOrigin")})
        changes.remember(db, kind, previous, record)
        db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?)", (kind, record["id"], encoded(record)))
        return previous, self.triggers.record(db, kind, record, previous)

    def publish_facts(self, facts):
        if self.on_trigger is not None:
            for fact in facts:
                self.on_trigger(fact)

    def get(self, kind, identity):
        record = self.find(kind, identity)
        if record is None:
            raise ContractError("not_found", t("The selected research record is unavailable."))
        return record

    def _write(self, operation, args):
        if self._writer_db is None:
            self._writer_db = sqlite3.connect(self.root / "work-records.sqlite3")
            self._writer_db.row_factory = sqlite3.Row
        return operation(self._writer_db, *args)

    async def write(self, operation, *args):
        async with self._write_lock:
            writing = asyncio.get_running_loop().run_in_executor(
                self._writer, self._write, operation, args
            )
            try:
                return await asyncio.shield(writing)
            except asyncio.CancelledError:
                await writing
                raise

    def _read(self, operation):
        if self._reader_db is None:
            self._reader_db = sqlite3.connect(self.root / "work-records.sqlite3")
            self._reader_db.row_factory = sqlite3.Row
            self._reader_db.execute("PRAGMA query_only=ON")
        self._reader_db.execute("BEGIN")
        try:
            return operation(RecordReadView(self._reader_db))
        finally:
            self._reader_db.rollback()

    async def read(self, operation):
        return await asyncio.get_running_loop().run_in_executor(self._reader, contextvars.copy_context().run, self._read, operation)

    def list(self, kind, *, all_workspaces=False):
        rows = self.db.execute("SELECT content FROM records WHERE kind=?", (kind,))
        records = (json.loads(row["content"]) for row in rows)
        return sorted(records, key=lambda item: item["updatedAt"], reverse=True)

    def held_out_for(self, identity):
        row = self.db.execute("""SELECT content FROM records WHERE kind='qrun'
            AND json_extract(content,'$.runKind')='heldout' AND json_extract(content,'$.of')=?
            ORDER BY json_extract(content,'$.createdAt') LIMIT 1""", (identity,)).fetchone()
        return json.loads(row[0]) if row is not None else None

    def find(self, kind, identity):
        row = self.db.execute("SELECT content FROM records WHERE kind=? AND id=?", (kind, identity)).fetchone()
        record = json.loads(row[0]) if row else None
        return record

    def close(self):
        if self._reader_db is not None:
            self._reader.submit(self._reader_db.close).result()
        self._reader.shutdown()
        if self._writer_db is not None:
            self._writer.submit(self._writer_db.close).result()
        self._writer.shutdown()
        for found in self._opened:
            found.close()


class RecordReadView:
    """One consistent read-only SQLite view on the observation reader thread."""
    def __init__(self, db):
        self.db = db

    get = WorkRecords.get
    find = WorkRecords.find
    list = WorkRecords.list
