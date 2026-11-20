"""Quant's contribution to Developer's editing language: the folders of Quant's objects and the common code folder get the environment they run in."""
from __future__ import annotations

import asyncio
from pathlib import Path

from talyx_sdk import ContractError

from . import editing_language
from .execution_recipe import RUNTIME
from .native_environment import bundled_wheels, prepare_lock
from .project_paths import MODULE, local_path


class SourceLanguage:
    def __init__(self, ctx, records, store, common, packages):
        self.ctx, self.records, self.store, self.common, self.packages = ctx, records, store, common, packages
        self.ready, self.preparing = {}, {}

    def environment_kind(self, root):
        """What a folder is: a strategy, another research object, or the common code (which research and strategies both import)."""
        if root == self.common.root.resolve():
            return "research"
        for record in self.records.list("qobject"):
            if not record.get("deleted") and Path(record["directory"]).resolve() == root:
                return "strategy" if record["kind"] == "strategy" else "research"
        return None

    async def prepare(self, kind, effective):
        state = await prepare_lock(self.ctx, bundled_wheels(effective["lock"], RUNTIME), effective["origin"])
        self.ready[kind] = (effective["digest"], await editing_language.prepare(self.ctx, self.records.root, kind, state))

    def environment(self, kind):
        """The environment for the lock new work uses now: once packages are installed the editor moves to the lock that holds them, no restart needed.
        While it is being prepared in the background the editor gets paths only (or the previous environment's, until the new one is ready)."""
        effective = self.packages.effective(kind)
        ready = self.ready.get(kind)
        if (ready is None or ready[0] != effective["digest"]) and self.preparing.get((kind, effective["digest"])) is None:
            key = (kind, effective["digest"])
            task = asyncio.get_running_loop().create_task(self.prepare(kind, effective))   # for every workspace's editor
            self.preparing[key] = task
            task.add_done_callback(lambda done, name=key: self.preparing.pop(name, None))
        return None if ready is None else ready[1]

    async def invoke(self, request, call):
        if request["capability"] != "language":
            raise ContractError("unknown_capability", "Quant provides only editing language for its own code folders.")
        project, document = request.get("project") or {}, request.get("document") or {}
        root = project.get("root") or project.get("rootPath") or project.get("rootUri") or (document.get("context") or {}).get("rootUri")
        if not root:
            return {"languageMaterials": []}
        folder = local_path(root)
        kind = self.environment_kind(folder)
        if kind is None:
            return {"languageMaterials": []}
        materials = [{"kind": "python-path-scope", "scope": "declared"}, {"kind": "python-path", "path": str(folder)},
                     {"kind": "python-path", "path": str(MODULE)}, {"kind": "python-path", "path": str(self.common.root)}]
        project_id = project.get("projectId") or (document.get("context") or {}).get("projectId")
        environment = self.environment(kind)
        if environment is None or not project_id:
            return {"languageMaterials": materials}
        # Developer resolves the project's tools from this (the platform's project_tools service went to Developer).
        resolved = {"projectId": project_id, "rootPath": str(folder), "python": {
            "interpreterPath": environment["interpreterPath"], "environmentPath": environment["environmentPath"], "version": environment["pythonVersion"]}}
        return {"environment": resolved, "languageMaterials": [*materials, *environment.get("languageMaterials", [])]}

    async def close(self):
        tasks = list(self.preparing.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def install(ctx, records, store, common, packages):
    """What Quant gives Developer's editor (declared in ``entry``: developer.development_support "source_language")."""
    return SourceLanguage(ctx, records, store, common, packages)
