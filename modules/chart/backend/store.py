"""Chart's one SQLite database: charts per workspace and the workspace objects.

Writes are single small statements on the event loop, made only when something changed. A
write that raises is ``failure`` (Chart's health item) until one succeeds.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from . import changes

SCHEMA = """
CREATE TABLE IF NOT EXISTS charts (
    id TEXT PRIMARY KEY, workspace TEXT NOT NULL, revision INTEGER NOT NULL, document TEXT NOT NULL,
    saved_revision INTEGER, saved_document TEXT);
CREATE INDEX IF NOT EXISTS charts_workspace ON charts(workspace);
CREATE TABLE IF NOT EXISTS objects (
    kind TEXT NOT NULL, id TEXT NOT NULL, workspace TEXT, record TEXT NOT NULL, PRIMARY KEY (kind, id));
CREATE INDEX IF NOT EXISTS objects_workspace ON objects(kind, workspace);
"""

FIRED_SCHEMA = """CREATE TABLE IF NOT EXISTS fired (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, alert TEXT NOT NULL,
    workspace TEXT NOT NULL, at INTEGER NOT NULL, condition TEXT, record TEXT NOT NULL)"""


def dumps(value) -> str:
    return json.dumps(value, separators=(",", ":"), allow_nan=False)


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")  # WAL stays consistent; a write no longer waits for the disk
        self.db.executescript(SCHEMA)
        changes.install(self.db)
        with self.transaction():
            self.db.execute(FIRED_SCHEMA)
            self.db.execute("CREATE INDEX IF NOT EXISTS fired_alert ON fired(workspace,alert,at)")
            self.db.execute("CREATE INDEX IF NOT EXISTS fired_workspace_seq ON fired(workspace,seq)")
        self.firing = asyncio.Event()
        self.failure: str | None = None
        self.failure_listeners: list[Callable[[], None]] = []

    def write(self, sql: str, args: tuple) -> None:
        failure = self.failure
        try:
            self.db.execute(sql, args)
            self.failure = None
        except sqlite3.Error as error:
            self.failure = str(error)
            raise
        finally:
            if self.failure != failure:
                for listener in self.failure_listeners:
                    listener()

    # charts

    @contextmanager
    def transaction(self):
        self.db.execute("SAVEPOINT chart_write")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK TO chart_write")
            raise
        finally:
            self.db.execute("RELEASE chart_write")

    def recorded(self, call, kind, key, workspace, old, new):
        self.write(
            "INSERT INTO changes(call_id,run_id,workspace,kind,object_id,original,changed) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(call_id,kind,object_id) DO UPDATE SET changed=excluded.changed",
            (call.call_id, call.caller.run["id"] if call.caller.run else None, workspace, kind, key,
             dumps(old) if old is not None else None, dumps(new) if new is not None else None))

    def chart(self, chart_id: str) -> dict | None:
        row = self.db.execute("SELECT workspace, revision, document, saved_revision, saved_document FROM charts "
                              "WHERE id = ?", (chart_id,)).fetchone()
        if row is None:
            return None
        return {"workspace": row[0], "revision": row[1], "document": json.loads(row[2]), "savedRevision": row[3],
                "savedDocument": json.loads(row[4]) if row[4] else None}

    def charts(self, workspace: str, *, saved_only: bool = False) -> list[str]:
        query = "SELECT id FROM charts WHERE workspace = ?"
        if saved_only:
            query += " AND saved_revision IS NOT NULL"
        return [row[0] for row in self.db.execute(query, (workspace,))]

    def put_chart(self, chart_id: str, workspace: str, revision: int, document: dict) -> None:
        with self.transaction():
            if call := changes.current_call():
                previous = self.chart(chart_id)
                self.recorded(call, "chart", chart_id, workspace, previous["document"] if previous else None, document)
            self.write("INSERT INTO charts (id, workspace, revision, document) VALUES (?, ?, ?, ?) "
                       "ON CONFLICT(id) DO UPDATE SET revision = excluded.revision, document = excluded.document",
                       (chart_id, workspace, revision, dumps(document)))

    def rewrite_chart(self, chart_id: str, document: dict, saved_document: dict | None) -> None:
        """A derived change (a merged instrument): both revisions keep their numbers."""
        self.write("UPDATE charts SET document = ?, saved_document = ? WHERE id = ?",
                   (dumps(document), dumps(saved_document) if saved_document else None, chart_id))

    def copy_chart(self, chart_id: str, copy_id: str, workspace: str) -> None:
        """A chart of another workspace as it is now, with its saved revision, under a new id."""
        stored = self.chart(chart_id)
        saved = stored["savedDocument"]
        self.write("INSERT INTO charts (id, workspace, revision, document, saved_revision, saved_document) "
                   "VALUES (?, ?, ?, ?, ?, ?)",
                   (copy_id, workspace, stored["revision"], dumps({**stored["document"], "id": copy_id}),
                    stored["savedRevision"], dumps({**saved, "id": copy_id}) if saved else None))

    def save_chart(self, chart_id: str) -> None:
        self.write("UPDATE charts SET saved_revision = revision, saved_document = document WHERE id = ?",
                   (chart_id,))

    def revert_chart(self, chart_id: str) -> None:
        self.write("UPDATE charts SET revision = saved_revision, document = saved_document WHERE id = ?",
                   (chart_id,))

    def delete_chart(self, chart_id: str) -> None:
        self.write("DELETE FROM charts WHERE id = ?", (chart_id,))

    # workspace objects (alerts, watchlists, screens; templates have no workspace)

    def objects(self, kind: str, workspace: str | None = None) -> list[dict]:
        rows = self.db.execute("SELECT record FROM objects WHERE kind = ?" + (" AND workspace = ?" if workspace else ""),
                               (kind, workspace) if workspace else (kind,))
        return [json.loads(row[0]) for row in rows]

    def object(self, kind: str, object_id: str) -> dict | None:
        row = self.db.execute("SELECT record FROM objects WHERE kind = ? AND id = ?", (kind, object_id)).fetchone()
        return json.loads(row[0]) if row else None

    def put_object(self, kind: str, record: dict, workspace: str | None) -> None:
        with self.transaction():
            if call := changes.current_call():
                self.recorded(call, kind, record["id"], workspace or call.workspace_id, self.object(kind, record["id"]), record)
            self.write("INSERT OR REPLACE INTO objects (kind, id, workspace, record) VALUES (?, ?, ?, ?)",
                       (kind, record["id"], workspace, dumps(record)))

    def delete_object(self, kind: str, object_id: str) -> None:
        with self.transaction():
            if (call := changes.current_call()) and (old := self.object(kind, object_id)) is not None:
                self.recorded(call, kind, object_id, call.workspace_id, old, None)
            self.write("DELETE FROM objects WHERE kind = ? AND id = ?", (kind, object_id))

    # alert triggers, kept 90 days

    def add_fired(self, workspace: str, record: dict, condition: str) -> None:
        self.write("INSERT INTO fired (id, alert, workspace, at, condition, record) VALUES (?, ?, ?, ?, ?, ?)",
                   (record["id"], record["alertId"], workspace, record["at"], condition, dumps(record)))
        self.firing.set()
        self.firing = asyncio.Event()

    async def follow_firings(self, workspace, alert_id, instrument_id, cursor):
        if cursor is None:
            cursor = self.db.execute("SELECT COALESCE(MAX(seq),0) FROM fired").fetchone()[0]
        elif type(cursor) is not int or not 0 <= cursor < 2**63:
            raise ContractError("invalid_input", t("Invalid alert firing cursor"))
        while True:
            waiting = self.firing
            rows = self.db.execute("SELECT seq,condition,record FROM fired WHERE workspace=? AND seq>? "
                                   "AND (? IS NULL OR alert=?) ORDER BY seq LIMIT 128",
                                   (workspace, cursor, alert_id, alert_id)).fetchall()
            for cursor, condition, raw in rows:
                event = json.loads(raw)
                if instrument_id is None or instrument_id == event["instrumentId"]:
                    yield {"alertId": event["alertId"], "instrumentId": event["instrumentId"],
                           "triggeredAt": str(event["at"] * 1_000_000), "barOpen": str(event["barTime"] * 1_000_000),
                           "condition": condition, "openTarget": changes.open_target("alert", event["alertId"])}, cursor
            if len(rows) < 128:
                await waiting.wait()

    def update_fired(self, record: dict) -> None:
        self.write("UPDATE fired SET record = ? WHERE id = ?", (dumps(record), record["id"]))

    def fired(self, workspace: str, alert: str | None, before: int | None, limit: int) -> list[dict]:
        query, args = "SELECT record FROM fired WHERE workspace = ?", [workspace]
        if alert:
            query, args = query + " AND alert = ?", [*args, alert]
        if before is not None:
            query, args = query + " AND at < ?", [*args, before]
        rows = self.db.execute(query + " ORDER BY at DESC LIMIT ?", (*args, limit))
        return [json.loads(row[0]) for row in rows]

    def last_fired(self, alert: str) -> dict[str, int]:
        """When the alert last fired on each member (instrument id → ms)."""
        rows = self.db.execute("SELECT json_extract(record, '$.instrumentId'), MAX(at) FROM fired WHERE alert = ? "
                               "GROUP BY 1", (alert,))
        return dict(rows.fetchall())

    def prune_fired(self, before: int) -> None:
        self.write("DELETE FROM fired WHERE at < ?", (before,))

    def close(self) -> None:
        self.db.close()
