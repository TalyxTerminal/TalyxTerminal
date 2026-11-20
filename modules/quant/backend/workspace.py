"""Copy a workspace's research objects, runs and their private files, and translate saved panel selections."""
from __future__ import annotations

import json
import os
import shutil
from uuid import uuid4

from talyx_sdk import ContractError

from .run_flow import ACTIVE, terminal_state


def owned(records, workspace):
    rows = records.db.execute("""SELECT kind,content FROM records WHERE json_extract(content,'$.workspaceId')=?
        OR (kind='qmbatch' AND json_extract(content,'$.objectId') IN
            (SELECT id FROM records WHERE kind='qobject' AND json_extract(content,'$.workspaceId')=?))""",
                              (workspace, workspace))
    return [(k, json.loads(value)) for k, value in rows]


def directories(root, kind, identity):
    names = {"qobject": ("objects", "qmodel-checks"), "qrun": ("qruns", "qmodels", "qallocations")}
    for name in names.get(kind, ()):
        parent = root.resolve() / name
        path = parent / identity
        if path.parent != parent or path.resolve() != path or parent.resolve() != parent:
            raise ContractError("invalid_input", "Quant's workspace directory is outside its data folder.")
        yield path


def translate(value, ids, paths=()):
    if isinstance(value, str):
        if value in ids:
            return ids[value]
        for before, after in paths:
            if value == before or value.startswith((before + "/", before + "\\")):
                return after + value[len(before):]
    if isinstance(value, list):
        return [translate(item, ids, paths) for item in value]
    if isinstance(value, dict):
        return {ids.get(k, k): translate(v, ids, paths) for k, v in value.items()}
    return value


def copy_workspace(records, library, source, destination):
    rows = [(kind, value) for kind, value in owned(records, source)
            if kind in ("qobject", "qrun", "qmbatch") and not value.get("deleted")]
    ids = {item["id"]: ("run-" if kind == "qrun" else "batch-" if kind == "qmbatch" else "") + uuid4().hex
           for kind, item in rows}
    trees = [(old, new, kind == "qrun" and item["state"] == "completed")
             for kind, item in rows for old, new in zip(directories(records.root, kind, item["id"]),
                                                       directories(records.root, kind, ids[item["id"]])) if old.is_dir()]
    paths = [(str(old), str(new)) for old, new, _ in trees] + [(old.as_uri(), new.as_uri()) for old, new, _ in trees]
    created = []
    try:
        for old, new, finished in trees:
            new.mkdir()
            created.append(new)
            for file in old.rglob("*"):
                target = new / file.relative_to(old)
                if file.is_symlink() or file.is_junction():
                    raise ContractError("invalid_input", "Quant cannot duplicate a project link outside its source tree.")
                if file.is_dir():
                    target.mkdir(exist_ok=True)
                elif old.parent.name in {"qruns", "qmodels", "qallocations"} and file.relative_to(old).as_posix() in {
                    "invocation.json", "engine/invocation.json", "info.json"
                }:
                    value = json.loads(file.read_bytes())
                    target.write_text(json.dumps(translate(value, ids, paths), ensure_ascii=False), encoding="utf-8")
                elif finished and file.suffix.lower() in {".parquet", ".arrow", ".feather", ".npy", ".npz"}:
                    os.link(file, target)
                else:
                    shutil.copy2(file, target)
        db = records.db
        with db:
            for kind, item in rows:
                result = translate(item, ids, paths)
                if "workspaceId" in result:
                    result["workspaceId"] = destination
                if kind == "qobject":
                    result["developer"] = None  # the copied project opens from its own directory
                if kind == "qrun":
                    result.pop("callId", None)
                    result.pop("callOrigin", None)
                    if result["state"] in ACTIVE:
                        result.update(terminal_state(result, "interrupted", "The run was active when its workspace was copied.",
                                                     stopped={"auto": False}))
                db.execute("INSERT INTO records VALUES (?,?,?)", (kind, result["id"], json.dumps(result)))
            previous = dict(db.execute("SELECT original,copy FROM workspace_copies WHERE workspace=?", (source,)))
            db.executemany("INSERT INTO workspace_copies VALUES (?,?,?)",
                           [(destination, key, value) for key, value in ids.items()] +
                           [(destination, key, ids[value]) for key, value in previous.items() if value in ids])
            with library.store.connect() as catalog:
                for key, copy_id in ids.items():
                    catalog.execute("""INSERT INTO occupancy SELECT version_id,?,?,kind,name,how,created_at
                        FROM occupancy WHERE owner_id=? AND workspace_id=?""",
                                    ("qobject:" + copy_id, destination, "qobject:" + key, source))
                for item_id in occupied_items(catalog, destination):
                    library.store._summarize(catalog, item_id)
    except BaseException:
        for path in created:
            shutil.rmtree(path)
        raise


def delete_workspace(records, library, workspace, preview):
    rows = owned(records, workspace)
    if preview:
        return [f"{item['kind'].capitalize()} · {item['name']}" for kind, item in rows
                if kind == "qobject" and not item.get("deleted")] + [
                    f"Run · {item['name']}" for kind, item in rows if kind == "qrun"]
    for kind, item in rows:
        for path in directories(records.root, kind, item["id"]):
            if path.is_dir():
                shutil.rmtree(path)
    snapshots = (records.root / "change-sources").resolve()
    for (call_id,) in records.db.execute("SELECT DISTINCT call_id FROM quant_changes WHERE workspace=? AND original IS NULL AND kind='qobject'", (workspace,)):
        path = (snapshots / call_id).resolve()
        if path.parent != snapshots or snapshots.parent != records.root.resolve():
            raise ContractError("invalid_input", "Quant's change snapshot is outside its data folder.")
        if path.exists():
            shutil.rmtree(path)
    with library.store.connect() as catalog:
        affected = occupied_items(catalog, workspace)
        catalog.execute("DELETE FROM occupancy WHERE workspace_id=?", (workspace,))
        for item_id in affected:
            library.store._summarize(catalog, item_id)
    with records.db as db:
        db.executemany("DELETE FROM records WHERE kind=? AND id=?", [(k, v["id"]) for k, v in rows])
        db.execute("DELETE FROM quant_trigger_facts WHERE workspace=?", (workspace,))
        db.execute("DELETE FROM workspace_copies WHERE workspace=?", (workspace,))
        db.execute("DELETE FROM quant_changes WHERE workspace=?", (workspace,))
        db.execute("DELETE FROM child_changes WHERE workspace=?", (workspace,))
    return []


def occupied_items(db, workspace):
    return [row[0] for row in db.execute("""SELECT DISTINCT v.item_id FROM versions v JOIN occupancy o
        ON v.id=o.version_id WHERE o.workspace_id=?""", (workspace,))]


