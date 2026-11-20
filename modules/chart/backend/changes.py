"""Persist chart definitions and workspace objects changed by a public call, including pinned alert inputs."""
from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from contextvars import ContextVar

from talyx_sdk import ContractError

from .document import ChartDocument

_call = ContextVar("chart_change_call", default=None)
OBJECTS = {"watchlist", "alert", "screen", "template"}


@contextmanager
def recording(call):
    # Alert subscriptions and notifications started by a write live beyond that call.
    token = _call.set((asyncio.current_task(), call))
    try:
        yield
    finally:
        _call.reset(token)


def current_call():
    record = _call.get()
    return record[1] if record and record[0] is asyncio.current_task() else None


def install(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS changes (
            call_id TEXT NOT NULL, run_id TEXT, workspace TEXT NOT NULL,
            kind TEXT NOT NULL, object_id TEXT NOT NULL, original TEXT, changed TEXT,
            PRIMARY KEY(call_id,kind,object_id));
        CREATE INDEX IF NOT EXISTS changes_workspace ON changes(workspace);
    """)


def open_target(kind, object_id):
    return {"module": "chart", "panelType": "main", "objectId": f"{kind}:{object_id}"}


def listed(store, call_ids, run_id):
    rows = store.db.execute(
        "SELECT call_id,kind,object_id,original,changed FROM changes "
        "WHERE call_id IN (SELECT value FROM json_each(?)) AND (? IS NULL OR run_id=?) ORDER BY rowid",
        (json.dumps(call_ids), run_id, run_id))
    items = []
    for call_id, kind, object_id, original, changed in rows:
        if kind not in OBJECTS and kind != "chart" or original == changed:
            continue
        value = json.loads(changed or original)
        label = value["instrument"]["displaySymbol"] if kind == "chart" else value["value"].get("name")
        items.append({"callId": call_id, "objectId": object_id, "label": label or kind.capitalize(),
                      "openTarget": None if kind == "template" else open_target(kind, object_id),
                      "kind": "created" if original is None else "deleted" if changed is None else "updated"})
    return items


def revert(app, call_id, direction):
    column = {"undo": "original", "redo": "changed"}[direction]
    rows = app.store.db.execute(
        f"SELECT workspace,kind,object_id,{column} FROM changes WHERE call_id=? ORDER BY rowid",
        (call_id,)).fetchall()
    if not rows:
        raise ContractError("not_found", "Chart has no changes for that call")
    refresh = []
    with app.store.transaction():
        for ws, kind, key, snapshot in rows:
            value = json.loads(snapshot) if snapshot is not None else None
            if kind == "chart":
                if value is None:
                    app.store.delete_chart(key)
                    app.charts.entries.pop(key, None)
                    app.charts.drawings_changed(ws, key, None)
                elif app.store.chart(key) is None:
                    app.store.put_chart(key, ws, 1, value)
                    app.charts.published(app.charts.entry(ws, key))
                else:
                    app.charts.set(app.charts.entry(ws, key), ChartDocument.model_validate(value))
                continue
            if kind in OBJECTS:
                refresh.append((ws, kind, key, value))
                if kind == "alert":
                    app.alerts.stop(key)
                if kind == "screen" and key in app.screens.runs:
                    app.screens.cancel(ws, key)
            if value is None:
                app.store.delete_object(kind, key)
            else:
                app.store.put_object(kind, value, None if kind == "template" else ws)
        # Restore pins, frozen drawing definitions and firing cursors before restarting a watch.
        for ws, kind, key, value in refresh:
            if kind == "alert":
                app.alerts.records.pop(key, None)
                if value:
                    app.alerts.records[key] = (ws, value["value"])
                    app.alerts.follow(ws, value["value"])
            if kind == "watchlist":
                for listener in app.watchlists.changed:
                    listener(ws, key)
            event = {"type": kind, "value": value["value"]} if value else {"type": kind + "_removed", "id": key}
            if kind == "template":
                app.workspaces.publish_all(event)
            else:
                app.workspaces.publish(ws, event)
