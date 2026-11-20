"""Shared documents: the file on disk (or a module's document) plus one edit buffer per source."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4

from talyx_sdk import ContractError

from ..projects.service import atomic_json, canonical_uri, file_path
from .changes import DocumentChanges
from .checkpoints import CheckpointStore, source_identity

LANGUAGES = {".py": "python", ".pyi": "python", ".json": "json", ".ts": "typescript", ".tsx": "typescript", ".js": "javascript",
             ".mjs": "javascript", ".css": "css", ".md": "markdown", ".yaml": "yaml", ".yml": "yaml", ".toml": "ini",
             ".html": "html", ".xml": "xml"}
SNAPSHOT_KEYS = ("documentId", "uri", "name", "source", "context", "content", "version", "language", "conflict", "readOnly",
                 "externalState", "baseContent", "error")
# Folders whose files are tools' output, not source: not listed among a folder's documents.
NOT_SOURCE = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", "node_modules", ".git",
                        "dist", "build", "mlruns"})


def origin(call) -> str:
    """Who an edit came from, as the document_changed event says it."""
    if call is None:
        return "source"
    caller = call.caller
    return caller.module if caller.origin == "module" else caller.origin


def content_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def disk_stat(path: Path):
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return [stat.st_mtime_ns, stat.st_size, stat.st_dev, stat.st_ino]


def replace_lines(content, edits):
    """All inclusive, 1-based ranges refer to the same original buffer."""
    lines = content.splitlines(keepends=True)
    result = []
    first_ending = re.search(r"\r\n|\r|\n", content)
    consumed = 0
    for edit in sorted(edits, key=lambda item: item["startLine"]):
        start, end = edit["startLine"] - 1, edit["endLine"]
        if not consumed <= start < end <= len(lines):
            raise ContractError("invalid_input", "Line edits must cover existing, non-overlapping lines")
        result.extend(lines[consumed:start])
        replacement = edit["text"]
        if first_ending:
            line = lines[start]
            ending = line[len(line.rstrip("\r\n")):] or first_ending[0]
            replacement = re.sub(r"\r\n|\r|\n", ending, replacement)
        if replacement and not replacement.endswith(("\n", "\r")):
            last = lines[end - 1]
            replacement += last[len(last.rstrip("\r\n")):]
        result.append(replacement)
        consumed = end
    return "".join([*result, *lines[consumed:]])


class DocumentService:
    """Unsaved buffers survive restarts; clean documents are reread from their source."""

    def __init__(self, developer, projects, root: Path):
        self.developer, self.projects = developer, projects
        self.root = Path(root) / "unsaved-documents"
        self.checkpoints = CheckpointStore(Path(root))
        self.changes = DocumentChanges(Path(root), self.checkpoints)
        self.records = {record["documentId"]: record for path in self.root.glob("*.json")
                        for record in [json.loads(path.read_text(encoding="utf-8"))]}
        self.lock = asyncio.Lock()
        self.watcher: asyncio.Task | None = None

    def record(self, document_id):
        if document_id not in self.records:
            raise ContractError("not_found", "This shared document is unavailable.")
        return self.records[document_id]

    @staticmethod
    def snapshot(record):
        return copy.deepcopy({**{key: record[key] for key in SNAPSHOT_KEYS}, "sourceVersion": record["baseVersion"],
                              "dirty": record["content"] != record["baseContent"]})

    def commit(self, record, kind, by):
        """Store the record; only unsaved buffers are kept on disk for recovery."""
        path = self.root / (record["documentId"].split(":")[1] + ".json")
        if record["content"] != record["baseContent"]:
            atomic_json(path, record)
        else:
            path.unlink(missing_ok=True)
        self.records[record["documentId"]] = record
        self.developer.publish("document_changed", {"documentId": record["documentId"], "version": record["version"],
                                                    "kind": kind, "origin": by, "externalState": record["externalState"]})
        return self.snapshot(record)

    def file_location(self, record):
        located = self.projects.locate(record["uri"], record["source"].get("projectId"))
        return located["project"]["projectId"], located["path"]

    async def source_read(self, source):
        if source["kind"] == "contribution":
            module, _, provider = source["providerId"].partition(":")
            loaded = await self.developer.extension("document_source", module, provider, "read",
                                                    {key: source[key] for key in ("sourceKey", "target") if source.get(key) is not None})
            if (loaded.get("canonicalUri") or "").startswith("file:"):
                loaded["canonicalUri"] = canonical_uri(file_path(loaded["canonicalUri"]))
            context = loaded["context"]
            loaded["context"] = {"contextId": context["contextId"], "projectId": context.get("projectId"),
                                 "rootUri": canonical_uri(file_path(context["rootUri"])) if (context.get("rootUri") or "").startswith("file:") else None,
                                 "languageMaterials": context.get("languageMaterials", [])}
            return loaded
        located = self.projects.locate(source["uri"], source.get("projectId"))
        project_id, path = located["project"]["projectId"], located["path"]
        stat = disk_stat(self.projects.path(project_id, path))
        if stat is None:
            return {"content": "", "version": None, "exists": False, "stat": None}
        return {**self.projects.read(project_id, path), "stat": stat}

    @staticmethod
    def reloaded(record, loaded):
        """Apply a newer source version: a clean buffer follows it, an unsaved one records a conflict."""
        exists = loaded.get("exists", True)
        record["stat"] = loaded.get("stat")
        if loaded["version"] == record["baseVersion"] and (record["externalState"] == "present") == exists:
            return False
        state = "present" if exists else "deleted"
        if record["content"] != record["baseContent"]:
            record.update(conflict=True, externalState=state)
        else:
            if record["content"] != loaded["content"]:
                record["version"] += 1
            record.update(content=loaded["content"], baseContent=loaded["content"], baseVersion=loaded["version"],
                          conflict=False, externalState=state)
        return True

    def existing(self, source):
        identity = source_identity(source)
        return next((record for record in self.records.values() if source_identity(record["source"]) == identity), None)

    async def open(self, value, call=None):
        return await self.load_source(value, call, register=True)

    async def read_source(self, value, call=None):
        """The shared snapshot of a source that exists (or has a buffer), without registering a new project."""
        return await self.load_source(value, call, register=False)

    async def load_source(self, value, call, *, register):
        by = origin(call)
        async with self.lock:
            source = copy.deepcopy(value["source"])
            if source["kind"] == "file":
                source["uri"] = canonical_uri(file_path(source["uri"]))
                located = self.projects.locate(source["uri"], source.get("projectId"), register=register,
                                               reference=value.get("reference", False))
                project, uri, name = located["project"], located["uri"], Path(located["path"]).name
                source = {"kind": "file", "uri": uri, "projectId": project["projectId"]}
                context = {"contextId": project["contextId"], "projectId": project["projectId"], "rootUri": project["rootUri"],
                           "languageMaterials": []}
                read_only = bool(project.get("readOnly"))
            else:
                module, _, provider = source["providerId"].partition(":")
                found = [item for item in self.developer.implementations("document_source")
                         if item["moduleId"] == module and item["id"] == provider]
                if not found:
                    raise ContractError("unavailable", "Choose an available document source provider.")
                read_only = bool(found[0]["descriptor"].get("readOnly"))
            loaded = await self.source_read(source)
            existing = self.existing(source)
            if not register and existing is None and not loaded.get("exists", True):
                raise ContractError("not_found", "This source does not exist and has no shared buffer.")
            if source["kind"] == "contribution":
                uri = loaded.get("canonicalUri") or "talyx-source:/" + source_identity(source)
                context, name = loaded["context"], loaded["name"]
                read_only = read_only or loaded.get("readOnly", False)
            if existing:
                record = copy.deepcopy(existing)
                record.update(source=source, context=context, readOnly=read_only)
                self.reloaded(record, loaded)
                return self.commit(record, "opened", by)
            record = {"documentId": "document:" + uuid4().hex, "source": source, "uri": uri, "name": name, "context": context,
                      "content": loaded["content"], "baseContent": loaded["content"], "baseVersion": loaded["version"],
                      "version": 1, "conflict": False, "readOnly": read_only, "error": None, "stat": loaded.get("stat"),
                      "language": loaded.get("language") or LANGUAGES.get(Path(name).suffix.lower(), "plaintext"),
                      "externalState": "present" if loaded.get("exists", True) else "deleted"}
            return self.commit(record, "opened", by)

    async def list(self, root_uris):
        """Current shared file buffers in these folders, including recovered conflicts."""
        roots = [file_path(uri).resolve() for uri in root_uris]
        async with self.lock:
            result = []
            for record in self.records.values():
                if not record["uri"].startswith("file:"):
                    continue
                path = file_path(record["uri"]).resolve()
                root = next((root for root in roots if path.is_relative_to(root)), None)
                if root is None or path.suffix == ".pyc" or NOT_SOURCE & set(path.relative_to(root).parts):
                    continue
                result.append({"documentId": record["documentId"], "uri": record["uri"], "version": record["version"],
                               "dirty": record["content"] != record["baseContent"], "conflict": record["conflict"]})
            return sorted(result, key=lambda item: item["documentId"])

    async def create(self, value, call):
        async with self.lock:
            project_id, relative, content = value["projectId"], value["relativePath"], value["content"]
            project = self.projects.get(project_id)
            if project.get("readOnly"):
                raise ContractError("permission_denied", "This project is read-only")
            path = self.projects.path(project_id, relative)
            if path.exists():
                raise ContractError("conflict", "This file already exists; open and edit its current document version")
            source = {"kind": "file", "uri": canonical_uri(path), "projectId": project_id}
            if self.existing(source) is not None:
                raise ContractError("conflict", "This source already has a shared document; edit and save using its expectedVersion")
            saved = self.projects.write(project_id, relative, content, None)
            if saved["status"] != "saved":
                raise ContractError("conflict", saved["message"])
            record = {"documentId": "document:" + uuid4().hex, "source": source, "uri": source["uri"], "name": path.name,
                      "context": {"contextId": project["contextId"], "projectId": project_id, "rootUri": project["rootUri"],
                                  "languageMaterials": []},
                      "content": content, "baseContent": content, "baseVersion": saved["version"], "version": 1,
                      "conflict": False, "readOnly": False, "error": None, "stat": disk_stat(path),
                      "language": LANGUAGES.get(path.suffix.lower(), "plaintext"), "externalState": "present"}
            created = self.commit(record, "opened", origin(call))
            self.changes.remember(call, {**record, "content":"", "baseContent":"", "baseVersion":None}, record, "create")
            return created

    def check_version(self, record, expected):
        if record["version"] != expected:
            raise ContractError("conflict", "Read the current revision before submitting an edit.",
                                details={"documentId": record["documentId"], "version": record["version"]})

    def set_content(self, record, content, by):
        if content == record["content"]:
            return self.snapshot(record)
        return self.commit({**copy.deepcopy(record), "content": content, "version": record["version"] + 1}, "changed", by)

    async def edit(self, value, call):
        async with self.lock:
            record = self.record(value["documentId"])
            self.check_version(record, value["expectedVersion"])
            if record["readOnly"]:
                raise ContractError("permission_denied", "This source is read-only.")
            content = replace_lines(record["content"], value["edits"]) if "edits" in value else value["content"]
            result = self.set_content(record, content, origin(call))
            if result["version"] != record["version"]:
                self.changes.remember(call, record, self.record(record["documentId"]), "edit")
            return result

    async def save(self, value, call=None):
        """Write the buffer; edits made while a provider saves stay unsaved on top of it."""
        async with self.lock:
            record = copy.deepcopy(self.record(value["documentId"]))
            if value.get("expectedVersion") is not None:
                self.check_version(record, value["expectedVersion"])
            if record["readOnly"]:
                raise ContractError("permission_denied", "This source is read-only.")
            result = {"documentId": record["documentId"], "version": record["version"], "message": None}
            if record["conflict"]:
                return {**result, "status": "conflict", "message": "Compare the external source before saving."}
            if record["content"] == record["baseContent"] and record["externalState"] == "present":
                return {**result, "status": "clean"}
            source, content = record["source"], record["content"]
            if source["kind"] == "file":
                project_id, path = self.file_location(record)
                saved = self.projects.write(project_id, path, content, record["baseVersion"])
                # Commit before the watcher can see the new file, so it is not taken for an external change.
                self.saved(record["documentId"], content, saved, disk_stat(self.projects.path(project_id, path)), call)
                if saved["status"] == "saved":
                    self.changes.remember(call, record, self.record(record["documentId"]), "save")
                return {**result, "status": saved["status"], "message": saved.get("message")}
        module, _, provider = source["providerId"].partition(":")
        saved = await self.developer.extension("document_source", module, provider, "write", {
            **{key: source[key] for key in ("sourceKey", "target") if source.get(key) is not None},
            "content": content, "expectedVersion": record["baseVersion"]})
        async with self.lock:
            self.saved(record["documentId"], content, saved, None, call)
            if saved["status"] == "saved":
                self.changes.remember(call, record, self.record(record["documentId"]), "save")
        return {**result, "status": saved["status"], "message": saved.get("message")}

    def saved(self, document_id, content, saved, stat, call):
        current = copy.deepcopy(self.record(document_id))
        if saved["status"] == "saved":
            current.update(baseContent=content, baseVersion=saved["version"], conflict=False, externalState="present", stat=stat)
            self.commit(current, "saved", origin(call))
        else:
            current["conflict"] = saved["status"] == "conflict"
            self.commit(current, "changed", "source")

    async def revert(self, call_id, direction):
        writes_source, snapshot = self.changes.read(call_id, direction)
        source = snapshot["source"]
        opened = await self.open({"source": source})
        async with self.lock:
            record = copy.deepcopy(self.record(opened["documentId"]))
            if record["readOnly"]:
                raise ContractError("permission_denied", "This source is read-only.")
            if writes_source:
                current = await self.source_read(source)
                content = snapshot["baseContent"]
                if source["kind"] == "file":
                    project, relative = self.file_location(record)
                    path = self.projects.path(project, relative)
                    if snapshot["sourceVersion"] is None:
                        path.unlink(missing_ok=True)
                        version = None
                    else:
                        disk_text = "\ufeff" + content if content_digest("\ufeff" + content) == snapshot["sourceVersion"] else content
                        result = self.projects.write(project, relative, disk_text, current["version"])
                        if result["status"] != "saved":
                            raise ContractError("conflict", result["message"])
                        version = result["version"]
                    record["stat"] = disk_stat(path)
                else:
                    owner, _, provider = source["providerId"].partition(":")
                    result = await self.developer.extension("document_source", owner, provider, "write", {
                        "sourceKey": source["sourceKey"], "target": source.get("target"), "content": content,
                        "expectedVersion": current["version"]})
                    if result["status"] != "saved":
                        raise ContractError("conflict", result["message"])
                    version = result["version"]
                record.update(baseContent=content, baseVersion=version, externalState="deleted" if version is None else "present")
            record.update(content=snapshot["content"], conflict=False, version=record["version"] + 1)
            self.commit(record, "changed", "source")

    async def versions(self, document_id):
        record = self.record(document_id)
        disk = await self.source_read(record["source"])
        return {"base": {"content": record["baseContent"], "version": record["baseVersion"]},
                "disk": {key: disk[key] for key in ("content", "version")}, "unsaved": self.snapshot(record)}

    async def resolve(self, document_id, mode, expected_version, expected_source_version, by="user"):
        """Keep the unsaved buffer against the current disk version, or replace it with that version."""
        async with self.lock:
            record = copy.deepcopy(self.record(document_id))
            self.check_version(record, expected_version)
            if record["readOnly"]:
                raise ContractError("permission_denied", "This source is read-only.")
            external = await self.source_read(record["source"])
            if external["version"] != expected_source_version:
                raise ContractError("conflict", "The external version changed again. Compare it before resolving.")
            record.update(baseContent=external["content"], baseVersion=external["version"], conflict=False, stat=external.get("stat"),
                          externalState="present" if external.get("exists", True) else "deleted")
            if mode == "external" and record["content"] != external["content"]:
                record.update(content=external["content"], version=record["version"] + 1)
            return self.commit(record, "resolved", by)

    def unsaved_records(self):
        return [record for record in self.records.values() if record["content"] != record["baseContent"]]

    def unsaved(self):
        return [self.snapshot(record) for record in self.unsaved_records()]

    async def refresh(self):
        """Reread files whose size or modification time changed on disk."""
        paths = {}
        for record in list(self.records.values()):
            if record["source"]["kind"] != "file" or record["source"].get("projectId") in self.projects.removed_projects:
                continue
            try:
                project_id, path = self.file_location(record)
                paths[record["documentId"]] = self.projects.path(project_id, path)
            except (ContractError, OSError) as exc:
                paths[record["documentId"]] = exc

        def file_stats():
            stats = {}
            for doc_id, path in paths.items():
                if isinstance(path, Path):
                    try:
                        stats[doc_id] = disk_stat(path)
                    except OSError as exc:
                        stats[doc_id] = exc
                else:
                    stats[doc_id] = path
            return stats

        for document_id, stat in (await asyncio.to_thread(file_stats)).items():
            async with self.lock:
                record = self.record(document_id)
                if stat == record["stat"] and record["error"] is None:
                    continue
                updated = copy.deepcopy(record)
                try:
                    if isinstance(stat, Exception):
                        raise stat
                    changed = self.reloaded(updated, await self.source_read(updated["source"]))
                except (ContractError, OSError) as exc:
                    message = exc.message if isinstance(exc, ContractError) else str(exc)
                    if record["error"] != message:
                        self.commit({**updated, "error": message}, "changed", "source")
                    continue
                if changed or record["error"] is not None:
                    updated["error"] = None
                    self.commit(updated, "changed", "disk")
                else:
                    self.records[document_id] = updated

    async def watch(self):
        while True:
            await self.refresh()
            await asyncio.sleep(1)

    def start(self):
        self.watcher = asyncio.create_task(self.watch())
