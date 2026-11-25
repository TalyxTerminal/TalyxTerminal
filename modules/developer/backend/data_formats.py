"""Developer's data format steps (`Module.data_format`): each turns the data a version before kept into the next version's.

2, the platform's own folder for installed modules' code: in format 1 a module the user installed or wrote was in
`modules/<id>` among the user's files; the platform now keeps its code in `modulesDir` (it copied each one there and left the
old folder, which in the installed TALYX is the module's data folder). A project open on a module in the old folder, and the
unsaved edits of its files, move to the copy: editing the old folder would change nothing TALYX runs.
"""
from __future__ import annotations

import json
from pathlib import Path

from .projects.service import atomic_json, canonical_uri, file_path, identity


async def installed_modules_moved(ctx) -> None:
    directories = await ctx.platform.storage()
    data, modules = Path(directories["dataDir"]), Path(directories["modulesDir"])
    move_module_projects(data, Path(directories["userDir"]).parents[1] / "modules", modules)


def move_module_projects(data: Path, old: Path, modules: Path) -> None:
    index = data / "workspace-projects.json"
    if not index.is_file() or not old.is_dir():
        return
    stored = json.loads(index.read_text(encoding="utf-8"))
    moved: dict[str, tuple[Path, Path]] = {}   # project id -> (old root, new root)
    for project_id, project in {**stored.get("removedProjects", {}), **stored["projects"]}.items():
        root = Path(project["root"])
        copy = modules / root.name
        if root.parent.resolve() == old.resolve() and (copy / "talyx.module.json").is_file():
            moved[project_id] = (root, copy.resolve())
            project.update(previousRoot=str(root), root=str(copy.resolve()), rootIdentity=identity(copy))
    if not moved:
        return
    for path in (data / "unsaved-documents").glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        source = record["source"]
        if source["kind"] != "file" or source.get("projectId") not in moved:
            continue
        before, after = moved[source["projectId"]]
        current = file_path(record["uri"])
        if current.is_relative_to(after):
            continue  # already written before an interrupted migration
        uri = canonical_uri(after / current.relative_to(before))
        source["uri"] = record["uri"] = uri
        record["context"]["rootUri"] = canonical_uri(after)
        atomic_json(path, record)
    atomic_json(index, stored)
