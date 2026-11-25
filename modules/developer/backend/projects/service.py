"""Real directory identity and bounded, on-demand access for the workbench."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from threading import RLock
from urllib.parse import urlparse
from urllib.request import url2pathname
from uuid import uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel
from talyx_sdk import ContractError
from talyx_sdk.i18n import mark, t
from talyx_sdk.recycle import recycle_directory

from .history import FileHistory


class OpenProject(BaseModel):
    directory: str
    create: bool = False
    name: str | None = None


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def canonical_uri(path: Path) -> str:
    # Editor URIs normalize drive letters to lowercase. Windows files are one
    # document even when an external provider uses a different path spelling.
    value = path.resolve().as_uri()
    return value[:8] + value[8].lower() + "%3A" + value[10:] if os.name == "nt" and len(value) > 9 and value[9] == ":" else value


def file_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise ContractError("invalid_input", t("Choose a local file or a contributed source."))
    path = re.sub(r"^/([a-zA-Z])%3[Aa]/", r"/\1:/", parsed.path) if os.name == "nt" else parsed.path
    return Path(url2pathname(path))


def identity(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_dev}:{stat.st_ino}"


class ProjectService:
    def __init__(self, data_dir: Path, *, recycler=None, protected_paths=(), before_remove=None):
        """``data_dir``: Developer's own data directory. ``protected_paths``: folders no project inside, around or
        equal to may be moved to the Recycle Bin (TALYX's data, modules' own folders, installed modules)."""
        self.root = Path(data_dir)
        self.index = self.root / "workspace-projects.json"
        self.lock = RLock()
        self.history = FileHistory(self.root, self.lock)
        self.history_warnings = {}
        self.references = {}
        self.projects = {}
        self.removed_projects = {}
        self.recycler = recycler
        self.before_remove = before_remove
        self.on_changed = None
        self.after_write = None
        repository = Path(__file__).resolve().parents[4]
        self.protected_paths = [repository / "data", *(Path(path).resolve() for path in protected_paths)]
        self.protected_roots = [repository, Path.home().resolve()]
        for variable in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
            if os.environ.get(variable):
                self.protected_paths.append(Path(os.environ[variable]).resolve())
        if self.index.is_file():
            stored = json.loads(self.index.read_text(encoding="utf-8"))
            self.projects = stored["projects"]
            self.removed_projects = stored.get("removedProjects", {})

    def _persist(self):
        atomic_json(self.index, {"version": 2, "projects": self.projects, "removedProjects": self.removed_projects})
        if self.on_changed is not None:
            self.on_changed()

    def summary(self, project):
        root = Path(project["root"])
        available = root.is_dir()
        status = "available" if available else "unavailable"
        if available and project.get("rootIdentity") != identity(root):
            status = "replaced"
        if project.get("readOnly") and status == "available":
            status = "reference" if (root / project["referenceFile"]).is_file() else "unavailable"
        return {**project, "rootUri": canonical_uri(root), "status": status, "contextId": "project:" + project["projectId"]}

    def list(self):
        with self.lock:
            return [self.summary(project) for project in self.projects.values()]

    def registered(self, directory: Path) -> bool:
        with self.lock:
            return any(os.path.normcase(str(directory)) == os.path.normcase(project["root"]) for project in self.projects.values())

    def open(self, directory: str, *, create=False, name=None):
        if not isinstance(directory, str) or not directory.strip():
            raise ContractError("invalid_input", t("Choose a directory to open."))
        root = Path(directory).expanduser().resolve()
        if create:
            root.mkdir(parents=True, exist_ok=False)
        if not root.is_dir():
            raise ContractError("source_unavailable", t("The selected directory is unavailable."))
        with self.lock:
            for project in self.projects.values():
                if os.path.normcase(str(root)) == os.path.normcase(project["root"]):
                    # Choosing this directory again is the explicit rebinding
                    # step after a root was removed or replaced.
                    project["rootIdentity"] = identity(root)
                    if name:
                        project["name"] = name
                    self._persist()
                    return self.summary(project)
            # A folder inside a registered project is part of that project, not a second one.
            enclosing = [project for project in self.projects.values() if not create and root.is_relative_to(Path(project["root"]))]
            if enclosing:
                return self.summary(max(enclosing, key=lambda project: len(project["root"])))
            project_id = uuid4().hex
            if not name:
                name = root.name or str(root)
                try:
                    manifest = json.loads((root / "talyx.module.json").read_text(encoding="utf-8"))
                    if isinstance(manifest.get("name"), str) and manifest["name"].strip():
                        name = manifest["name"].strip()
                except (OSError, ValueError, AttributeError):
                    pass
            project = {"projectId": project_id, "name": name, "root": str(root), "kind": "local", "rootIdentity": identity(root)}
            # Removing a listing preserves its metadata and file history. Only an
            # explicit Open folder restores it, including after a Bin restore.
            for removed_id, removed in self.removed_projects.items():
                if os.path.normcase(str(root)) == os.path.normcase(removed["root"]):
                    project_id = removed_id
                    project = {key: value for key, value in removed.items() if key not in {"removedAt", "removalMode"}}
                    project["rootIdentity"] = identity(root)
                    break
            self.projects[project_id] = project
            removed = self.removed_projects.pop(project_id, None)
            try:
                self._persist()
            except OSError:
                self.projects.pop(project_id)
                if removed is not None:
                    self.removed_projects[project_id] = removed
                raise
            return self.summary(project)

    def _trash_target(self, project) -> Path:
        registered = Path(project["root"])
        root = registered.resolve()
        if os.path.normcase(str(root)) != os.path.normcase(str(registered)):
            raise ContractError("source_replaced", t("The project directory changed. Reopen it before moving it to the Recycle Bin."))
        if not root.is_dir():
            raise ContractError("source_unavailable", t("The project directory is unavailable. You can remove it from the list."))
        if identity(root) != project.get("rootIdentity"):
            raise ContractError("source_replaced", t("The project directory was replaced. Reopen it before moving it to the Recycle Bin."))
        if root == Path(root.anchor) or any(path.is_relative_to(root) for path in self.protected_roots):
            raise ContractError("permission_denied", t("This application, home or drive directory cannot be moved to the Recycle Bin."))
        if any(root.is_relative_to(path) or path.is_relative_to(root) for path in self.protected_paths):
            raise ContractError("permission_denied", t("This directory contains protected application, installed module or system data."))
        return root

    def remove(self, project_id: str, mode: str, expected_root: str, root_identity: str):
        if mode not in ("forget", "trash") or not isinstance(expected_root, str) or not isinstance(root_identity, str):
            raise ContractError("invalid_input", t("Choose how to remove this project."))
        with self.lock:
            project = self.projects.get(project_id)
            if project is None:
                raise ContractError("unavailable", t("The project is no longer registered."))
            if os.path.normcase(expected_root) != os.path.normcase(project["root"]) or root_identity != project.get("rootIdentity"):
                raise ContractError("conflict", t("The project changed while the dialog was open. Review it and try again."))
            root = self._trash_target(project) if mode == "trash" else None
            previous_removed = self.removed_projects.get(project_id)
            self.projects.pop(project_id)
            self.removed_projects[project_id] = {**project, "removedAt": datetime.now(UTC).isoformat(), "removalMode": mode}
            try:
                # Check and save the listing before touching the source. History,
                # builds, recoveries and old locators remain where they were.
                self._persist()
            except OSError as exc:
                self.projects[project_id] = project
                self.removed_projects.pop(project_id)
                if previous_removed is not None:
                    self.removed_projects[project_id] = previous_removed
                raise ContractError("storage_unavailable", t("The project list could not be saved. The project folder is preserved.")) from exc
            if root is not None:
                try:
                    self._trash_target(project)
                    if self.recycler is None:
                        recycle_directory(root, expected_identity=tuple(int(value) for value in project["rootIdentity"].split(":")))
                    else:
                        self.recycler(root)
                    if root.exists():
                        raise OSError("The project directory is still present.")
                except Exception as exc:
                    self.projects[project_id] = project
                    self.removed_projects.pop(project_id)
                    if previous_removed is not None:
                        self.removed_projects[project_id] = previous_removed
                    try:
                        self._persist()
                    except OSError:
                        raise ContractError("storage_unavailable", t("The Recycle Bin operation failed and the project list could not be restored. Reopen the folder to recover its listing."), execution="failed", effects="possible") from exc
                    if isinstance(exc, ContractError):
                        raise
                    raise ContractError("recycle_failed", t("The folder could not be moved to the Windows Recycle Bin. The project remains listed."), {"reason": str(exc)}, execution="failed", effects="possible") from exc
            return {"projectId": project_id, "mode": mode, "removed": True}

    def get(self, project_id: str):
        project = self.projects.get(project_id) or self.references.get(project_id)
        if not project:
            raise ContractError("unavailable", t("The project is no longer registered."))
        return self.summary(project)

    def activate(self, project_id: str):
        """The project chosen in the editor. One whose folder is gone can be chosen too, to be removed from the list."""
        with self.lock:
            self.get(project_id)
            if project_id in self.projects:
                self.projects[project_id]["lastOpened"] = datetime.now(UTC).isoformat()
                self._persist()
            return self.get(project_id)

    def _capture_history(self, project_id: str, relative: str, data: bytes, reason: str):
        try:
            self.history.capture(project_id, relative, data, reason)
            self.history_warnings.pop(project_id, None)
        except (OSError, ValueError):
            # History must not turn a readable source into an unavailable one,
            # or make a completed file save appear to be an uncompleted write.
            self.history_warnings[project_id] = "File history could not be recorded. The source file is preserved; review history storage before relying on older versions."

    def _reference(self, file: Path):
        if not file.is_file():
            raise ContractError("source_unavailable", t("This reference file is unavailable."))
        parent_identity = identity(file.parent)
        project_id = "reference-" + hashlib.sha256((os.path.normcase(str(file)) + ":" + parent_identity).encode()).hexdigest()[:32]
        with self.lock:
            project = self.references.setdefault(project_id, {"projectId": project_id, "name": file.name, "root": str(file.parent), "kind": "reference", "rootIdentity": parent_identity, "readOnly": True, "referenceFile": file.name})
        return self.summary(project)

    def locate(self, uri: str, project_id: str | None = None, *, register=False, reference=False):
        parsed = urlparse(uri)
        if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
            raise ContractError("invalid_input", t("Choose a local file or a contributed source."))
        # url2pathname decodes percent escapes itself. Decoding twice changes
        # real names such as "%20folder" into another path.
        uri_path = re.sub(r"^/([a-zA-Z])%3[Aa]/", r"/\1:/", parsed.path) if os.name == "nt" else parsed.path
        file = Path(url2pathname(uri_path)).resolve()
        if project_id and project_id.startswith("reference-") and project_id not in self.references:
            restored = self._reference(file)
            if restored["projectId"] != project_id:
                raise ContractError("source_replaced", t("The reference directory changed. Open the reference again."))
        candidates = [self.get(project_id)] if project_id else self.list()
        if not project_id and not register:
            candidates.extend(self.summary(project) for project in self.references.values())
        for project in sorted(candidates, key=lambda item: len(item["root"]), reverse=True):
            root = Path(project["root"]).resolve()
            # Saved editor tabs and immutable checkpoints may still name the project's pre-migration folder.
            previous = project.get("previousRoot")
            if project_id and previous and file.is_relative_to(previous):
                file = root / file.relative_to(previous)
            if file.is_relative_to(root) and (not project.get("readOnly") or os.path.normcase(str(file)) == os.path.normcase(str(root / project["referenceFile"]))):
                relative = file.relative_to(root).as_posix()
                self.path(project["projectId"], relative)
                return {"project": project, "path": relative, "uri": canonical_uri(file)}
        if register:
            project = self._reference(file) if reference else self.open(str(file.parent))
            return {"project": project, "path": file.name, "uri": canonical_uri(file)}
        raise ContractError("source_unavailable", t("This file does not belong to an open project."))

    def path(self, project_id: str, relative: str = "") -> Path:
        project = self.get(project_id)
        root = Path(project["root"]).resolve()
        if not root.is_dir():
            raise ContractError("source_unavailable", t("The project directory is unavailable."))
        current_identity = identity(root)
        if project.get("rootIdentity") and project["rootIdentity"] != current_identity:
            raise ContractError("source_replaced", t("The project directory was replaced. Reopen the directory before changing files."))
        pure = PurePosixPath(relative.replace("\\", "/"))
        if pure.is_absolute() or ".." in pure.parts or any(":" in part or "\x00" in part for part in pure.parts):
            raise ContractError("invalid_input", t("The file path must stay inside this project."))
        if project.get("readOnly") and os.path.normcase(relative) != os.path.normcase(project["referenceFile"]):
            raise ContractError("permission_denied", t("This context grants access only to the selected reference file."))
        path = (root / Path(*pure.parts)).resolve()
        if not path.is_relative_to(root):
            raise ContractError("permission_denied", t("The linked file is outside this project."))
        return path

    def stat(self, project_id: str, relative: str):
        path = self.path(project_id, relative)
        if not path.exists():
            return {"path": relative, "uri": canonical_uri(path), "exists": False, "kind": "missing", "version": None, "size": 0}
        stat = path.stat()
        directory = path.is_dir()
        return {"path": relative, "uri": canonical_uri(path), "exists": True, "kind": "directory" if directory else "file", "version": f"{stat.st_mtime_ns}:{stat.st_size}:{stat.st_ino}", "size": stat.st_size, "identity": identity(path)}

    def children(self, project_id: str, relative: str = ""):
        path = self.path(project_id, relative)
        if not path.is_dir():
            raise ContractError("source_unavailable", t("This directory is unavailable."))
        entries = []
        for child in path.iterdir():
            name = (PurePosixPath(relative) / child.name).as_posix()
            try:
                entries.append(self.stat(project_id, name))
            except (OSError, ContractError):
                entries.append({"path": name, "kind": "unavailable", "exists": True, "version": None, "size": 0, "uri": child.as_uri()})
        return sorted(entries, key=lambda entry: (entry["kind"] != "directory", entry["path"].casefold()))

    def read(self, project_id: str, relative: str):
        path = self.path(project_id, relative)
        stat = self.stat(project_id, relative)
        if stat["kind"] != "file":
            raise ContractError("source_unavailable", t("This file is unavailable."))
        if stat["size"] > 16 * 1024 * 1024:
            raise ContractError("resource_too_large", t("This file is too large for the text editor. Open it in an external editor."))
        data = path.read_bytes()
        try:
            content = data.decode("utf-8-sig")
            if "\x00" in content:
                raise UnicodeError()
        except UnicodeError:
            raise ContractError("binary_resource", t("This resource is preserved on disk. Open it with an external application.")) from None
        if not self.get(project_id).get("readOnly"):
            self._capture_history(project_id, relative, data, mark("External change"))
        return {**stat, "content": content, "version": hashlib.sha256(data).hexdigest()}

    def write(self, project_id: str, relative: str, content: str, expected_version: str | None):
        with self.lock:
            if self.get(project_id).get("readOnly"):
                raise ContractError("permission_denied", t("Reference files are read-only."))
            path = self.path(project_id, relative)
            if path.exists() and not path.is_file():
                raise ContractError("invalid_input", t("Choose a file rather than a directory."))
            current = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            if current != expected_version:
                return {"status": "conflict", "version": current, "message": t("The file changed outside the editor. Compare the changes before saving.")}
            if current is None:
                # New nested files may create their
                # parent directories. Recheck authority and CAS after creation.
                self.path(project_id, relative)
                path.parent.mkdir(parents=True, exist_ok=True)
                path = self.path(project_id, relative)
                current = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
                if current != expected_version:
                    return {"status": "conflict", "version": current, "message": t("The file appeared while creating its directory. Read its current version before saving.")}
            if path.is_file():
                self._capture_history(project_id, relative, path.read_bytes(), mark("Before save"))
            data = content.encode("utf-8")
            temporary = path.with_name(uuid4().hex + ".tmp")
            try:
                with temporary.open("wb") as stream:
                    stream.write(data); stream.flush(); os.fsync(stream.fileno())
                self.path(project_id, relative)
                verified = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
                if verified != current:
                    return {"status": "conflict", "version": verified, "message": t("The file changed while saving. Compare the changes before saving.")}
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            self._capture_history(project_id, relative, data, mark("Saved"))
            return {"status": "saved", "version": hashlib.sha256(data).hexdigest(), **({"historyWarning": self.history_warnings[project_id]} if project_id in self.history_warnings else {})}

    async def remove_checked(self, project_id, mode, expected_root, root_identity):
        if self.before_remove is not None:
            await self.before_remove(project_id, mode)
        return await asyncio.to_thread(self.remove, project_id, mode, expected_root, root_identity)

    def history_path(self, project_id, path):
        if self.get(project_id).get("readOnly"):
            raise ContractError("permission_denied", t("Reference files do not have editable file history."))
        return self.path(project_id, path)

    def file_history(self, project_id, path):
        file = self.history_path(project_id, path)
        if file.is_file() and file.stat().st_size <= 16 * 1024 * 1024:
            self._capture_history(project_id, path, file.read_bytes(), mark("Current file"))
        result = self.history.list(project_id, path)
        result["warnings"] = [self.history_warnings[project_id]] if project_id in self.history_warnings else []
        return result

    def revision(self, project_id, path, revision_id):
        self.history_path(project_id, path)
        return self.history.read(project_id, path, revision_id)

    async def restore_revision(self, project_id, path, revision_id, expected_version):
        self.history_path(project_id, path)
        previous = self.history.read(project_id, path, revision_id, keep_bom=True)
        result = self.write(project_id, path, previous["content"], expected_version)
        if result["status"] == "saved" and self.after_write is not None:
            await self.after_write(self.path(project_id, path))
        return result

    def router(self):
        root = APIRouter()

        @root.get("/projects")
        async def list_projects():
            return {"projects": self.list()}

        @root.post("/projects/open")
        async def open_project(value: OpenProject):
            return {"project": self.open(value.directory, create=value.create, name=value.name)}

        @root.get("/projects/{project_id}/files")
        async def children(project_id: str, path: str = ""):
            return {"entries": await asyncio.to_thread(self.children, project_id, path)}

        @root.post("/projects/{project_id}/remove")
        async def remove(project_id: str, request: Request):
            try:
                payload = await request.json()
            except (json.JSONDecodeError, UnicodeError) as exc:
                raise ContractError("invalid_input", t("Choose how to remove this project.")) from exc
            if not isinstance(payload, dict) or payload.get("mode") not in ("forget", "trash"):
                raise ContractError("invalid_input", t("Choose how to remove this project."))
            return await self.remove_checked(project_id, payload["mode"], payload.get("expectedRoot"), payload.get("rootIdentity"))

        @root.post("/projects/{project_id}/activate")
        async def activate(project_id: str):
            return {"project": self.activate(project_id)}

        @root.get("/projects/{project_id}/history")
        async def history(project_id: str, path: str):
            return self.file_history(project_id, path)

        @root.get("/projects/{project_id}/history/{revision_id}")
        async def revision(project_id: str, revision_id: str, path: str):
            return self.revision(project_id, path, revision_id)

        @root.post("/projects/{project_id}/history/{revision_id}/restore")
        async def restore(project_id: str, revision_id: str, request: Request):
            payload = await request.json()
            return await self.restore_revision(project_id, payload["path"], revision_id, payload["expectedVersion"])

        return root
