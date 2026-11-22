"""Workspaces: the open ones, the window showing each, their folders, and their saved states.

A workspace has one id, ``workspaceId``, whether it is open or saved: every call made for it names that id, and
modules keep what belongs to it under that id. Saving stores the workspace's state (its layout and panels, which the
window owns) as a snapshot with its own ``snapshotId``; opening the snapshot later opens the same workspace again.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import re
import shutil
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import orjson
from talyx_sdk.errors import ContractError
from talyx_sdk.i18n import t
from talyx_sdk.recycle import recycle_directory

from .files import finish_before_cancel, write_atomic, write_json
from .platform.declarations import SavedWorkspace

# A saved workspace's state is the window's layout and panel states: generous, but bounded.
SNAPSHOT_BYTES = 4 * 1024 * 1024


def panel_modules(state: dict) -> set[str]:
    """The modules whose panels a saved state holds: the window saves each panel with kind ``module:<id>``."""
    return {panel["kind"].removeprefix("module:") for panel in state.get("panels", [])}


class Workspaces:
    def __init__(self, root: Path, folders_root: Path, private_roots: tuple[Path, ...]):
        self.folders_root, self.private_roots = folders_root, private_roots
        self.path = root / "workspaces.json"
        self.snapshots = root / "snapshots"
        self.opened: dict[str, dict] = {}
        self.folders: dict[str, str] = {}
        self.copy_names: dict[str, str] = {}
        self.closing: set[str] = set()
        self.sessions: dict[str, dict] = {}       # window session -> {workspaceId, moduleInstances}
        self.listeners: set[asyncio.Queue] = set()
        self.lock = asyncio.Lock()
        self.persisting = asyncio.Lock()
        if self.path.exists():
            state = orjson.loads(self.path.read_bytes())
            self.opened, self.folders = state["opened"], state["folders"]

    async def persist(self) -> None:
        """The index as it is now, written off the event loop; a later write always lands after an earlier one."""
        data = orjson.dumps({"opened": self.opened, "folders": self.folders})
        async with self.persisting:
            await finish_before_cancel(asyncio.to_thread(write_atomic, self.path, data))

    def require(self, workspace_id: str) -> dict:
        if workspace_id not in self.opened or workspace_id in self.closing:
            raise ContractError("target_gone", t("Workspace {workspace_id} is not open", workspace_id=workspace_id))
        return self.opened[workspace_id]

    def is_open(self, workspace_id: str) -> bool:
        return workspace_id in self.opened and workspace_id not in self.closing

    # ── changes ──────────────────────────────────────────────────────────

    def publish(self, change: dict | None) -> None:
        """A workspace opened, closed or was copied (``None``: only what a window shows changed)."""
        for queue in self.listeners:
            queue.put_nowait(change)

    async def changes(self) -> AsyncIterator[dict]:
        queue: asyncio.Queue = asyncio.Queue()
        self.listeners.add(queue)
        try:
            while True:
                change = await queue.get()
                if change is not None:
                    yield change
        finally:
            self.listeners.discard(queue)

    async def windows(self) -> AsyncIterator[dict]:
        """Every open workspace with the window showing it: now, then again whenever anything about that changes."""
        queue: asyncio.Queue = asyncio.Queue()
        self.listeners.add(queue)
        try:
            while True:
                yield {"workspaces": self.shown()}
                await queue.get()
                while not queue.empty():
                    queue.get_nowait()
        finally:
            self.listeners.discard(queue)

    # ── opening and closing ──────────────────────────────────────────────

    async def open(self, args: dict) -> dict:
        """A new workspace, an open one again (``workspaceId``), or a saved one (``snapshotId``: the workspace it is
        a saved state of opens again under its own id)."""
        snapshot_id = args.get("snapshotId")
        workspace_id = args.get("workspaceId")
        if snapshot_id is not None:
            saved = await self.load(snapshot_id)
            workspace_id = saved["workspaceId"]
            args = {"name": saved["name"], **args}
        elif workspace_id and workspace_id not in self.opened and "snapshotId" not in args:
            saved = await self._saved_for(workspace_id)
            if saved is not None:
                args = {"name": saved["name"], "snapshotId": saved["snapshotId"], **args}
        workspace_id = workspace_id or uuid4().hex
        if workspace_id in self.closing:
            raise ContractError("unavailable", t("The workspace is closing"))
        opened = workspace_id not in self.opened
        record = self.opened.setdefault(workspace_id, {"workspaceId": workspace_id, "name": None,
                                                      "snapshotId": None, "presentation": None})
        record.update({key: args[key] for key in ("name", "snapshotId", "presentation") if key in args})
        await self.persist()
        if opened:
            self.publish({"kind": "opened", "workspaceId": workspace_id})
        else:
            self.publish(None)
        return dict(record)

    def list_open(self) -> list[dict]:
        return [dict(record) for key, record in self.opened.items() if key not in self.closing]

    async def closed(self, workspace_id: str) -> None:
        self.opened.pop(workspace_id, None)
        self.closing.discard(workspace_id)
        for session, value in list(self.sessions.items()):
            if value["workspaceId"] == workspace_id:
                del self.sessions[session]
        await self.persist()
        self.publish({"kind": "closed", "workspaceId": workspace_id})

    # ── which window shows which workspace ───────────────────────────────

    def show(self, session: str, workspace_id: str, module_instances: dict[str, int]) -> None:
        self.require(workspace_id)
        self.sessions[session] = {"workspaceId": workspace_id, "moduleInstances": dict(module_instances)}
        self.publish(None)

    def window_closed(self, session: str) -> None:
        if self.sessions.pop(session, None) is not None:
            self.publish(None)

    def session_workspace(self, session: str) -> str | None:
        return (self.sessions.get(session) or {}).get("workspaceId")

    def window_of(self, workspace_id: str) -> str | None:
        return next((session for session, value in self.sessions.items() if value["workspaceId"] == workspace_id),
                    None)

    def shown(self) -> list[dict]:
        result = []
        for workspace_id, record in self.opened.items():
            if workspace_id in self.closing:
                continue
            session = self.window_of(workspace_id)
            result.append({"workspaceId": workspace_id, "name": record["name"], "snapshotId": record["snapshotId"],
                           "sessionId": session,
                           "moduleInstances": self.sessions[session]["moduleInstances"] if session else {}})
        return result

    # ── folders ──────────────────────────────────────────────────────────

    def _new_folder(self, name: str | None) -> Path:
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name or "Workspace").strip(" .")[:80]
        return (self.folders_root / f"{name}-{uuid4().hex[:12]}").resolve()

    def own_folder(self, workspace_id: str) -> Path | None:
        """The folder TALYX made for the workspace, if it has one; a folder the user bound is the user's."""
        value = self.folders.get(workspace_id)
        return Path(value) if value is not None and Path(value).parent == self.folders_root.resolve() else None

    async def folder(self, workspace_id: str) -> dict:
        async with self.lock:
            if workspace_id not in self.folders:
                path = self._new_folder(self.require(workspace_id)["name"])
                await asyncio.to_thread(path.mkdir, parents=True, exist_ok=False)
                self.folders[workspace_id] = str(path)
                await self.persist()
            return {"workspaceId": workspace_id, "path": self.folders[workspace_id]}

    async def copy_folder(self, source_id: str, copy_id: str, name: str) -> None:
        """A copy's folder: its own copy of the folder TALYX made for the source, or the folder the user bound."""
        if source_id not in self.folders:
            return      # the source never used a folder: the copy gets its own when first asked
        target = self.folders[source_id]
        if (own := self.own_folder(source_id)) is not None:
            if not own.is_dir():
                return  # the user removed it: nothing to copy, the copy gets its own folder when first asked
            target = self._new_folder(name)
            try:
                await finish_before_cancel(asyncio.to_thread(shutil.copytree, own, target))
            except BaseException:
                if target.exists():
                    await finish_before_cancel(asyncio.to_thread(shutil.rmtree, target))
                raise
        async with self.lock:
            self.folders[copy_id] = str(target)
            await self.persist()

    async def folder_preview(self, workspace_id: str) -> list[str]:
        """The folder deleting the workspace moves to the Recycle Bin: the one TALYX made for it."""
        own = self.own_folder(workspace_id)
        if own is None or not own.is_dir():
            return []
        files = await asyncio.to_thread(lambda: sum(1 for item in own.rglob("*") if item.is_file()))
        return [f"Workspace folder · {own} ({files} file{'' if files == 1 else 's'})"]

    async def discard_folder(self, workspace_id: str, *, rollback_copy: bool = False) -> None:
        """A deleted workspace's folder: the one TALYX made goes to the Recycle Bin, one the user bound stays."""
        own = self.own_folder(workspace_id)
        if own is not None and own.is_dir():
            await asyncio.to_thread(shutil.rmtree if rollback_copy else recycle_directory, own)
        async with self.lock:
            self.folders.pop(workspace_id, None)
            await self.persist()

    async def set_folder(self, workspace_id: str, value: str | None) -> dict:
        self.require(workspace_id)
        if value is None:
            async with self.lock:
                self.folders.pop(workspace_id, None)
            return await self.folder(workspace_id)
        path = Path(value)
        if not path.is_absolute() or not path.is_dir():
            raise ContractError("invalid_input", t("Choose an existing folder"))
        path = path.resolve(strict=True)
        # The folders TALYX makes for workspaces are its own too: deleting their workspace recycles them.
        if any(path.is_relative_to(root) or root.is_relative_to(path)
               for root in (*self.private_roots, self.folders_root.resolve())):
            raise ContractError("permission_denied", t("TALYX's own data folders cannot be a workspace folder"))
        async with self.lock:
            self.folders[workspace_id] = str(path)
            await self.persist()
        return {"workspaceId": workspace_id, "path": str(path)}

    # ── saved states ─────────────────────────────────────────────────────

    def _snapshot_path(self, snapshot_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", snapshot_id):
            raise ContractError("not_found", t("No saved workspace {snapshot_id}", snapshot_id=snapshot_id))
        return self.snapshots / f"{snapshot_id}.json"

    def _read(self, path: Path) -> dict:
        try:
            record = orjson.loads(path.read_bytes())
            SavedWorkspace.model_validate(record, strict=True)
            if record["snapshotId"] != path.stem:
                raise ValueError("snapshot ID does not match its file")
            return record
        except FileNotFoundError as exc:
            raise ContractError("not_found", t("No saved workspace {stem}", stem=path.stem)) from exc
        except (OSError, ValueError) as exc:
            raise ContractError("workspace_corrupt", t("The saved workspace {stem} cannot be read: {exc}", stem=path.stem, exc=exc)) from exc

    def _all(self, unreadable: list[str] | None = None) -> list[dict]:
        if not self.snapshots.exists():
            return []
        records = []
        for path in sorted(self.snapshots.glob("*.json")):
            try:
                records.append(self._read(path))
            except ContractError as exc:
                if exc.code != "not_found" and unreadable is not None:
                    unreadable.append(path.name)
        return records

    async def load(self, snapshot_id: str) -> dict:
        return await asyncio.to_thread(self._read, self._snapshot_path(snapshot_id))

    async def list_saved(self) -> dict:
        unreadable = []
        saved = await asyncio.to_thread(self._all, unreadable)
        summaries = [{key: item[key] for key in ("snapshotId", "workspaceId", "name", "savedAt", "revision", "summary")}
                for item in sorted(saved, key=lambda item: item["savedAt"], reverse=True)]
        return {"saved": summaries, "unreadable": unreadable}

    async def save(self, args: dict, workspace_id: str | None) -> dict:
        """Save a workspace's state under its name: a new snapshot, or (``snapshotId`` with ``expectedRevision``)
        over an existing one. Names are unique. The snapshot is the workspace's and the workspace opens from it. A
        workspace has one saved state."""
        name = args["name"].strip()
        if len(orjson.dumps(args["workspace"])) > SNAPSHOT_BYTES:
            raise ContractError("too_large", t("A saved workspace is at most {value} MB", value=SNAPSHOT_BYTES // 1024 // 1024))
        async with self.lock:
            saved = await asyncio.to_thread(self._all)
            snapshot_id = args.get("snapshotId")
            if snapshot_id is not None:
                current = await self.load(snapshot_id)
                if current["revision"] != args.get("expectedRevision"):
                    raise ContractError("conflict", t("The saved workspace changed since it was read"))
                workspace_id = current["workspaceId"]
            elif workspace_id is None:
                raise ContractError("invalid_target", t("Save a workspace from its window"))
            elif any(item["workspaceId"] == workspace_id for item in saved):
                raise ContractError("conflict", t("This workspace is saved already: save over its snapshot, or copy the workspace to save it under another name"))
            if workspace_id in self.closing:
                raise ContractError("unavailable", t("The workspace is closing"))
            same_name = [item for item in saved if item["name"].casefold() == name.casefold()
                         and item["snapshotId"] != snapshot_id]
            if name.casefold() in self.copy_names and self.copy_names[name.casefold()] != workspace_id:
                raise ContractError("already_exists", t("A workspace is being copied as {name}", name=name))
            if same_name:
                raise ContractError("already_exists", t("A saved workspace is already called {name}", name=same_name[0]['name']))
            record = {"snapshotId": snapshot_id or uuid4().hex, "workspaceId": workspace_id,
                      "name": name, "savedAt": datetime.now(UTC).isoformat(), "summary": args.get("summary"),
                      "workspace": args["workspace"]}
            record["revision"] = hashlib.sha256(orjson.dumps(
                {key: record[key] for key in ("name", "summary", "workspace")}, option=orjson.OPT_SORT_KEYS)).hexdigest()
            await finish_before_cancel(self._write_snapshot(record))
        return {key: record[key] for key in ("snapshotId", "workspaceId", "savedAt", "revision")}

    async def _write_snapshot(self, record: dict) -> None:
        await asyncio.to_thread(write_json, self._snapshot_path(record["snapshotId"]), record)
        if record["workspaceId"] in self.opened:
            self.opened[record["workspaceId"]].update(snapshotId=record["snapshotId"], name=record["name"])
            await self.persist()
            # The window holding it follows, whichever window saved or renamed it.
            self.publish({"kind": "saved", "workspaceId": record["workspaceId"], "snapshotId": record["snapshotId"],
                          "name": record["name"], "revision": record["revision"]})

    async def _saved_for(self, workspace_id: str) -> dict | None:
        saved = await asyncio.to_thread(self._all)
        return next((item for item in saved if item["workspaceId"] == workspace_id), None)

    async def saved_of(self, workspace_id: str) -> dict:
        """A workspace's saved state."""
        state = await self._saved_for(workspace_id)
        if state is None:
            raise ContractError("not_found", t("Save the workspace before copying it"))
        return state

    @asynccontextmanager
    async def reserve_copy(self, source: dict, copy_id: str, name: str | None):
        async with self.lock:
            taken = self.copy_names.keys() | {item["name"].casefold() for item in await asyncio.to_thread(self._all)}
            if name is None:
                name = next(candidate for index in itertools.count(1)
                            if (candidate := f"{source['name']} copy" + (f" {index}" if index > 1 else "")).casefold()
                            not in taken)
            name = name.strip()
            if name.casefold() in taken:
                raise ContractError("already_exists", t("A saved workspace is already called {name}", name=name))
            self.copy_names[name.casefold()] = copy_id
        try:
            yield name
        finally:
            del self.copy_names[name.casefold()]

    async def delete(self, snapshot_id: str, expected_revision: str | None,
                     cleanup: Callable[[str], Awaitable[None]]) -> None:
        async with self.lock:
            path = self._snapshot_path(snapshot_id)
            current = await asyncio.to_thread(self._read, path)
            if expected_revision is not None and current["revision"] != expected_revision:
                raise ContractError("conflict", t("The saved workspace changed since it was read"))
            workspace_id = current["workspaceId"]
            if workspace_id in self.closing:
                raise ContractError("unavailable", t("The workspace is closing"))
            self.closing.add(workspace_id)
        try:
            await cleanup(workspace_id)
            await self.discard_folder(workspace_id)
            await asyncio.to_thread(path.unlink)
            await self.closed(workspace_id)
        finally:
            self.closing.discard(workspace_id)
