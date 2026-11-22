"""Persist source preferences together with the fields changed by each recorded call."""
import json
import sqlite3
from contextlib import closing

from talyx_sdk.i18n import t

from .common import MarketError


class SourceSettings:
    def __init__(self, root):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "source-settings.sqlite3"
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, settings TEXT NOT NULL)")
            db.execute("""CREATE TABLE IF NOT EXISTS changes (
                call_id TEXT PRIMARY KEY, workspace TEXT, run_id TEXT, source TEXT NOT NULL,
                section TEXT NOT NULL, original TEXT NOT NULL, changed TEXT NOT NULL)""")
            self.saved = {key: json.loads(raw) for key, raw in db.execute("SELECT id, settings FROM sources")}

    def write(self, source, section, fields, values, call=None):
        record = dict(self.saved.get(source, {}))
        previous = record.get("settings", {}) if section == "settings" else record
        old = {key: previous[key] for key in fields if key in previous}
        if old == values:
            return
        updated = {**{k: v for k, v in previous.items() if k not in fields}, **values}
        result = {**record, "settings": updated} if section == "settings" else updated
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT OR REPLACE INTO sources VALUES (?,?)", (source, json.dumps(result)))
            if call is not None and call.caller.origin != "user":
                db.execute("INSERT INTO changes VALUES (?,?,?,?,?,?,?)", (
                    call.call_id, call.workspace_id, call.caller.run["id"] if call.caller.run else None, source,
                    section, json.dumps(old), json.dumps(values)))
        self.saved[source] = result

    def list(self, call_ids, run_id):
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("""SELECT call_id,source FROM changes WHERE call_id IN
                (SELECT value FROM json_each(?)) AND (? IS NULL OR run_id=?)""", (json.dumps(call_ids), run_id, run_id))
            return [{"callId": identity, "objectId": source, "label": t("{source} source settings", source=source), "kind": "updated",
                     "openTarget": {"module":"market","panelType":"main","objectId":"source:"+source}}
                    for identity, source in rows]

    def change(self, call_id, direction):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT source,section,original,changed FROM changes WHERE call_id=?", (call_id,)).fetchone()
        if not row:
            raise MarketError("not_found", t("No source settings were changed by this call."))
        before, after = json.loads(row[2]), json.loads(row[3])
        return row[0], row[1], before.keys() | after.keys(), before if direction == "undo" else after

    def forget(self, workspace):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DELETE FROM changes WHERE workspace=?", (workspace,))
