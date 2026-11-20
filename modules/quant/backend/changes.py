"""Quant settings, research lists and run annotations changed by a public call."""
from __future__ import annotations

import asyncio
import base64
import json
import shutil
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

from talyx_sdk import ContractError

from .workspace import directories

_active = ContextVar("quant_change_call", default=None)


@contextmanager
def recording(records, call, action):
    token = _active.set((records, call, asyncio.current_task(), action))
    try:
        yield
    finally:
        _active.reset(token)


def active():
    here = _active.get()
    if here is not None:
        try:
            task = asyncio.current_task()
        except RuntimeError:  # the public call may have sent synchronous work to a worker thread
            task = None
        if task is None or task is here[2]:
            return here
    return None


def install(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS quant_changes (
            call_id TEXT, run_id TEXT, workspace TEXT, kind TEXT, object_id TEXT, current_id TEXT,
            original TEXT, changed TEXT NOT NULL, PRIMARY KEY(call_id,kind,object_id));
        CREATE TABLE IF NOT EXISTS child_changes (parent TEXT, workspace TEXT, child TEXT PRIMARY KEY);
    """)


def remember(db, kind, previous, record):
    context = active()
    if not context or context[1].caller.origin == "user" or record.get("deleted") and context[3] != "rename_object_folder":
        return
    records, call = context[:2]
    if kind == "qrun":
        if not previous or all(record.get(k) == previous.get(k) for k in ("starred", "note")):
            return
        previous = {k: previous.get(k) for k in ("note", "starred")}
        after = {k: record.get(k) for k in ("note", "starred")}
    elif kind in ("qobject", "qlist", "qfolder"):
        after = record
        if kind == "qobject" and previous is None:
            source = next(directories(records.root, kind, record["id"]))
            if source.is_dir():
                target = records.root / "change-sources" / call.call_id / record["id"]
                shutil.copytree(source, target)
    else:
        return
    db.execute("""INSERT INTO quant_changes VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(call_id,kind,object_id) DO UPDATE SET changed=excluded.changed""",
               (call.call_id, call.caller.run["id"] if call.caller.run else None, call.workspace_id, kind,
                record["id"], record["id"], json.dumps(previous) if previous is not None else None, json.dumps(after)))


def remember_child(context, child):
    records, call = context[:2]
    with records.db as db:
        db.execute("INSERT INTO child_changes VALUES (?,?,?)", (call.call_id, call.workspace_id, child))


@contextmanager
def library_pins(library, obj):
    context = active()
    if context is None or context[1].caller.origin == "user":
        yield
        return
    records, call = context[:2]

    def snapshot():
        with library.store.connect() as db:
            rows = db.execute("SELECT * FROM occupancy WHERE owner_id=? ORDER BY version_id", ("qobject:" + obj["id"],))
            return {"name": obj["name"], "pins": [dict(row) for row in rows]}

    before = snapshot()
    try:
        yield
    finally:
        after = snapshot()
        if before != after:
            with records.db as db:
                db.execute("""INSERT INTO quant_changes VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(call_id,kind,object_id) DO UPDATE SET changed=excluded.changed""",
                    (call.call_id, call.caller.run["id"] if call.caller.run else None, call.workspace_id,
                     "pins", obj["id"], obj["id"], json.dumps(before), json.dumps(after)))


def write_local(records, obj, path, content):
    def snapshot(data):
        return {"object":obj["id"], "name":path.name, "bytes":base64.b64encode(data).decode("ascii")}

    previous = snapshot(path.read_bytes()) if path.exists() else None
    data = content.encode("utf-8")
    path.write_bytes(data)
    context = active()
    if context and context[1].caller.origin != "user":
        call = context[1]
        with records.db as db:
            db.execute("INSERT INTO quant_changes VALUES (?,?,?,?,?,?,?,?)", (
                call.call_id, call.caller.run["id"] if call.caller.run else None, call.workspace_id, "file",
                path.as_uri(), path.as_uri(), json.dumps(previous) if previous else None, json.dumps(snapshot(data))))


def target(kind, identity):
    if kind not in ("qobject", "qrun", "pins"):
        return None
    return {"module":"quant", "panelType":"workspace", "objectId":("run:" if kind == "qrun" else "object:") + identity}


async def listed(app, ids, run_id):
    db = app.store.records.db
    rows = db.execute("""SELECT * FROM quant_changes WHERE call_id IN (SELECT value FROM json_each(?))
        AND (? IS NULL OR run_id=?) ORDER BY rowid""", (json.dumps(ids), run_id, run_id))
    items = []
    for row in rows:
        value = json.loads(row["changed"])
        items.append({"callId":row["call_id"], "objectId":row["current_id"], "label":value.get("name", "Run annotation"),
                      "kind":"updated" if row["original"] is not None else "created", "openTarget":target(row["kind"],row["current_id"])})
    for parent, ws, child in db.execute("SELECT * FROM child_changes WHERE parent IN (SELECT value FROM json_each(?)) ORDER BY rowid",
                                        (json.dumps(ids),)).fetchall():
        children = await app.store.ctx.call("platform.changes.list", {"callIds": [child]}, workspace=ws)
        items.extend({**item, "callId":parent} for item in children)
    return items


async def revert(app, call_id, direction):
    records = app.store.records
    undo = direction == "undo"
    rows = records.db.execute("SELECT * FROM quant_changes WHERE call_id=? ORDER BY rowid", (call_id,)).fetchall()
    children = records.db.execute("SELECT workspace,child FROM child_changes WHERE parent=? ORDER BY rowid",
                                  (call_id,)).fetchall()
    if not rows and not children:
        raise ContractError("not_found", "Quant has no restorable change for this call.")

    async def child_changes():
        for ws, child in reversed(children) if undo else children:
            await app.store.ctx.call("platform.changes.revert", {"callId":child,"direction":direction}, workspace=ws)

    if undo:
        await child_changes()
    remapped = {}
    for row in reversed(rows) if undo else rows:
        kind = row["kind"]
        identity = remapped.get(row["current_id"], row["current_id"])
        original = row["original"]
        value = json.loads(row["changed"] if not undo or original is None else original)
        if kind == "pins":
            store, owner = app.library.store, "qobject:" + identity
            with store.connect() as db:
                versions = {r[0] for r in db.execute("SELECT version_id FROM occupancy WHERE owner_id=?", (owner,))}
                db.execute("DELETE FROM occupancy WHERE owner_id=?", (owner,))
                for pin in value["pins"]:
                    versions.add(pin["version_id"])
                    db.execute("""INSERT INTO occupancy(version_id,owner_id,workspace_id,kind,name,how,created_at)
                        VALUES (:version_id,:owner_id,:workspace_id,:kind,:name,:how,:created_at)""", {**pin,"owner_id":owner})
                for (item_id,) in db.execute("SELECT DISTINCT item_id FROM versions WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(sorted(versions)),)):
                    store._summarize(db, item_id)
            app.library.publish_change()
            continue
        if kind == "file":
            path = next(directories(records.root, "qobject", value["object"])) / value["name"]
            if undo and original is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(base64.b64decode(value["bytes"]))
            continue
        if kind == "qrun":
            current = records.get(kind, identity)
            records.put(kind, {**current, **value})
            app.store.ctx.publish("object_run_changed", {k:current[k] for k in ("objectId","workspaceId","state")} | {"runId":identity})
            continue
        if kind == "qobject" and original is None:
            current = records.find(kind, identity)
            if undo:
                if current and not current.get("deleted"):
                    app.store.delete(identity)
                continue
            if not current or current.get("deleted"):
                identity = str(uuid4())
                dest = next(directories(records.root, kind, identity))
                source = records.root / "change-sources" / call_id / row["object_id"]
                await asyncio.to_thread(shutil.copytree, source, dest)
                value.update(directory=str(dest), developer=None)
                with records.db:
                    records.db.execute("UPDATE quant_changes SET current_id=? WHERE kind IN ('qobject','pins') AND object_id=?",
                                       (identity, row["object_id"]))
                remapped[row["current_id"]] = identity
        if kind == "qobject":
            current = records.find(kind, identity)
            if current and identity != row["object_id"]:
                value.update(directory=current["directory"], developer=current["developer"])
            record = records.put(kind, {**value, "id":identity})
            app.store.publish(record)
        else:
            record = records.put(kind, {**value, "deleted":(undo and original is None) or value.get("deleted", False)})
            if kind == "qlist":
                app.lists.publish(record)
            else:
                app.store.ctx.publish("object_changed", {"objectId":"", "kind":record["kind"], "workspaceId":row["workspace"],"version":0})
    if not undo:
        await child_changes()
