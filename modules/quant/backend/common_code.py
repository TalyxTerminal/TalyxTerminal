"""The common code folder: one place, shared by every workspace, whose modules notebooks, research and strategies can import."""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

from talyx_sdk import ContractError

from .materials import plain
from .project_paths import EXCLUDED, filesystem_path, source_identity
from .project_snapshots import snapshot_project


class CommonCode:
    def __init__(self, ctx, records):
        self.ctx, self.records = ctx, records
        # Under the user root, beside the indicators and workspaces, so it is the user's own files and no workspace owns it.
        self.root = ctx.user_dir / "common"
        self.root.mkdir(parents=True, exist_ok=True)

    async def read(self):
        files = await asyncio.to_thread(source_identity, self.root)
        return {"directory": str(self.root), "files": sorted(files)}

    async def open(self):
        """The folder as a Developer project, so it is edited, saved and kept in file history like any other code."""
        opened = plain(await self.ctx.call("developer.projects.open", {"directory": str(self.root), "name": "Common code"}))
        return {"directory": str(self.root), "projectId": opened["projectId"], "contextId": opened["contextId"]}

    async def capture(self, staging_parent, destination):
        """Fix the folder as it is now into `destination`; None when it holds no files. A run keeps this copy, so later edits do not reach it."""
        before = await asyncio.to_thread(source_identity, self.root)
        if not before:
            return None
        with tempfile.TemporaryDirectory(dir=filesystem_path(staging_parent)) as staging_io:
            staging = Path(staging_parent) / Path(staging_io).name
            shutil.copytree(filesystem_path(self.root), filesystem_path(staging), dirs_exist_ok=True, ignore=shutil.ignore_patterns(*EXCLUDED, "*.pyc"))
            captured = await asyncio.to_thread(snapshot_project, self.ctx.data_dir, str(staging))
        if before != {item["path"]: item["sha256"] for item in captured["files"]}:
            raise ContractError("conflict", "The common code changed while it was being fixed for the run. Run again.")
        shutil.copytree(filesystem_path(captured["snapshotPath"]), filesystem_path(destination))
        return captured
