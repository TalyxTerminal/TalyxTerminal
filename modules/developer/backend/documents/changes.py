"""The shared buffer and its saved text at either end of a public edit or save."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing

from talyx_sdk import ContractError


class DocumentChanges:
    def __init__(self, root, checkpoints):
        self.path = root / "document-changes.sqlite3"
        self.checkpoints = checkpoints
        root.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS changes (
                call_id TEXT PRIMARY KEY, workspace TEXT, run_id TEXT, document TEXT NOT NULL,
                uri TEXT NOT NULL, name TEXT NOT NULL, operation TEXT NOT NULL,
                original TEXT NOT NULL, changed TEXT NOT NULL)""")

    def remember(self, call, before, after, operation):
        if call is None or call.caller.workspace_id is None or call.caller.origin == "user":
            return
        refs = []
        for label, record in (("before", before), ("after", after)):
            snapshot = {key: record[key] for key in ("source", "name", "version", "content", "baseContent")}
            snapshot.update(capturedDocumentId=record["documentId"], sourceVersion=record["baseVersion"])
            # A journal entry stays on disk. Only checkpoint documents handed to another caller have a wire budget.
            checkpoint = self.checkpoints.create(call.call_id + "/" + label, [], [snapshot], max_bytes=None)["checkpoint"]
            refs.append(json.dumps(checkpoint))
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO changes VALUES (?,?,?,?,?,?,?,?,?)",
                       (call.call_id, call.workspace_id, call.caller.run["id"] if call.caller.run else None,
                        after["documentId"], after["uri"], after["name"], operation, *refs))

    def list(self, call_ids, run_id):
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("""SELECT call_id,document,uri,name,operation FROM changes
                WHERE call_id IN (SELECT value FROM json_each(?)) AND (? IS NULL OR run_id=?)""",
                              (json.dumps(call_ids), run_id, run_id))
            return [{"callId": cid, "objectId": doc, "kind": "created" if op == "create" else "updated", "label": name,
                     "openTarget": {"module": "developer", "panelType": "main", "objectId": "file:" + uri}
                     if uri.startswith("file:") else None} for cid, doc, uri, name, op in rows]

    def read(self, call_id, direction):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT operation,original,changed FROM changes WHERE call_id=?", (call_id,)).fetchone()
        if row is None:
            raise ContractError("not_found", "No shared-document change was recorded for this call.")
        checkpoint = json.loads(row[1 if direction == "undo" else 2])
        return row[0] != "edit", self.checkpoints.read(checkpoint)["documents"][0]

    def forget_workspace(self, workspace):
        with closing(sqlite3.connect(self.path)) as db, db:
            for row in db.execute("SELECT original,changed FROM changes WHERE workspace=?", (workspace,)):
                for reference in row:
                    self.checkpoints.release(json.loads(reference))
            db.execute("DELETE FROM changes WHERE workspace=?", (workspace,))
