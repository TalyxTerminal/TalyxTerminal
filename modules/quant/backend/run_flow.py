"""Shared run history and queue rules."""
from __future__ import annotations

RUN = "qrun"

# The states, in order: made the moment it is asked for, its checks as the first step, waiting for room, running, and how it ended.
PREPARING, QUEUED, RUNNING = "preparing", "queued", "running"
CANCEL_REQUESTED = "cancel_requested"
COMPLETED, FAILED, CANCELLED, INTERRUPTED = "completed", "failed", "cancelled", "interrupted"
ACTIVE = frozenset({PREPARING, QUEUED, RUNNING, CANCEL_REQUESTED})
TERMINAL = frozenset({COMPLETED, FAILED, CANCELLED, INTERRUPTED})
LABELS = {PREPARING: "Preparing", QUEUED: "In the queue", RUNNING: "Running", CANCEL_REQUESTED: "Stopping",
          COMPLETED: "Completed", FAILED: "Failed", CANCELLED: "Stopped",
          INTERRUPTED: "Interrupted"}
# What a run that was going when the application stopped says (startup marks it; nothing resumes it silently). What it had written by then
# is read with it; a run whose process ended with the application has no part of its own to show, so this does not promise one.
INTERRUPTED_MESSAGE = "The application stopped while this ran."


def next_number(records, object_id):
    """The number the next run of an object gets: one more than its highest run's. A publication is not a run of the object."""
    row = records.db.execute("SELECT MAX(json_extract(content,'$.number')) FROM records WHERE kind=? AND json_extract(content,'$.objectId')=?"
                             " AND COALESCE(json_extract(content,'$.runKind'),'') != 'publish'", (RUN, object_id)).fetchone()
    return int(row[0] or 0) + 1


# ---------------------------------------------------------------------------------------------------------------- what changed
def _label(value):
    """A data selection as the user named it: an instrument by its symbol, a list of them by theirs."""
    if isinstance(value, dict) and isinstance(value.get("instrument"), dict):
        return value["instrument"].get("displaySymbol") or value["instrument"].get("symbol") or value["instrument"].get("instrumentId")
    if isinstance(value, dict) and set(value) >= {"displaySymbol"}:
        return value["displaySymbol"]
    if isinstance(value, list):
        return [_label(item) for item in value]
    return value


def _flat(value, prefix):
    if isinstance(value, dict) and not isinstance(value.get("instrument"), dict):
        out = {}
        for name, part in value.items():
            out.update(_flat(part, f"{prefix}.{name}" if prefix else str(name)))
        return out
    return {prefix: _label(value)}


def changes(before, after):
    """What differs between two runs, from their whole effective settings: {section: value} with the sections a block has, such as
    data (the range, the held-out start, the names or the index or list), pool, params, run (the run settings with their defaults
    filled in), code (a digest) and formula. Every leaf is compared; the data's names are compared by symbol. [{what, from, to}],
    empty when nothing differs; None `before` (no run before) gives []."""
    if before is None:
        return []
    old, new = _flat(before, ""), _flat(after, "")
    return [{"what": key, "from": old.get(key), "to": new.get(key)} for key in sorted(old.keys() | new.keys()) if old.get(key) != new.get(key)]


def terminal_state(record, state, message, **details):
    changes = {"state": state, "message": message, **details}
    if record.get("objectKind") == "strategy" and record.get("runKind") in {"sweep", "walk"}:
        sweep = record["sweep"]
        stopped = CANCELLED if state == CANCELLED else INTERRUPTED
        changes["sweep"] = {**sweep, "cases": [
            {**cell, "state": stopped, "message": message} if cell["state"] in ACTIVE else cell
            for cell in sweep["cases"]]}
    return changes


def interrupted(records):
    """At startup: every run that was going when the application stopped is marked interrupted (what it had finished stays)."""
    for record in records.list(RUN):
        if record["state"] not in TERMINAL:
            records.put(RUN, {**record, **terminal_state(record, INTERRUPTED, INTERRUPTED_MESSAGE)})
