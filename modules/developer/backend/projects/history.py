"""Bounded history for affected files. Source files stay intact."""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from talyx_sdk import ContractError

MAX_FILE = 16 * 1024 * 1024
MAX_PROJECT = 64 * 1024 * 1024
MAX_REVISIONS = 50


def path_key(relative: str):
    return os.path.normcase(relative.replace("\\", "/")).replace("\\", "/")


def atomic_bytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class FileHistory:
    def __init__(self, root: Path, lock):
        self.root, self.lock = root, lock

    def _directory(self, project_id: str):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", project_id):
            raise ContractError("invalid_input", "The project identity is invalid.")
        return self.root / "file-history" / project_id

    def _index(self, project_id: str):
        file = self._directory(project_id) / "index.json"
        return json.loads(file.read_text(encoding="utf-8")) if file.is_file() else {"version": 1, "files": {}}

    def _save(self, project_id: str, index):
        # Evict history material, never source files. Identical content is shared.
        all_revisions = sorted((revision for source in index["files"].values() for revision in source["revisions"]), key=lambda revision: revision["createdAt"])
        keep = {item["revisionId"] for item in all_revisions}
        sizes = {}
        for item in all_revisions:
            sizes[item["version"]] = item["size"]
        while sum(sizes.values()) > MAX_PROJECT and all_revisions:
            removed = all_revisions.pop(0)
            keep.discard(removed["revisionId"])
            if not any(item["version"] == removed["version"] for item in all_revisions):
                sizes.pop(removed["version"], None)
        for source in index["files"].values():
            source["revisions"] = [item for item in source["revisions"][-MAX_REVISIONS:] if item["revisionId"] in keep]
        directory = self._directory(project_id)
        atomic_bytes(directory / "index.json", (json.dumps(index, ensure_ascii=False) + "\n").encode("utf-8"))
        used = {item["version"] for source in index["files"].values() for item in source["revisions"]}
        for blob in (directory / "blobs").glob("*"):
            if blob.is_file() and blob.name not in used:
                blob.unlink()

    @staticmethod
    def _source(index, relative: str):
        key = path_key(relative)
        return index["files"].setdefault(key, {"sourceId": uuid4().hex, "path": relative, "revisions": []})

    def _append(self, project_id, source, data, reason):
        if len(data) > MAX_FILE:
            return
        version = hashlib.sha256(data).hexdigest()
        if source["revisions"] and source["revisions"][-1]["version"] == version:
            return
        blob = self._directory(project_id) / "blobs" / version
        if not blob.is_file():
            atomic_bytes(blob, data)
        source["revisions"].append({"revisionId": uuid4().hex, "createdAt": datetime.now(UTC).isoformat(), "reason": reason, "size": len(data), "version": version})
        source["revisions"].sort(key=lambda item: item["createdAt"])

    def capture(self, project_id: str, relative: str, data: bytes, reason: str):
        relative = relative.replace("\\", "/")
        with self.lock:
            index = self._index(project_id)
            source = self._source(index, relative)
            if source["revisions"] and source["revisions"][-1]["version"] == hashlib.sha256(data).hexdigest():
                return
            self._append(project_id, source, data, reason)
            self._save(project_id, index)

    def list(self, project_id: str, relative: str):
        relative = relative.replace("\\", "/")
        with self.lock:
            index = self._index(project_id)
            source = self._source(index, relative)
            return {"sourceId": source["sourceId"], "path": source["path"], "revisions": [{key: value for key, value in item.items() if key != "version"} for item in reversed(source["revisions"])]}

    def read(self, project_id: str, relative: str, revision_id: str, *, keep_bom=False):
        relative = relative.replace("\\", "/")
        with self.lock:
            source = self._source(self._index(project_id), relative)
            item = next((item for item in source["revisions"] if item["revisionId"] == revision_id), None)
            if not item:
                raise ContractError("source_unavailable", "This file revision is no longer available.")
            data = (self._directory(project_id) / "blobs" / item["version"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != item["version"]:
                raise ContractError("invalid_material", "The saved revision is damaged. The current file is unchanged.")
            try:
                content = data.decode("utf-8" if keep_bom else "utf-8-sig")
                if "\x00" in content:
                    raise UnicodeError()
            except UnicodeError:
                raise ContractError("binary_resource", "This revision is a binary resource.") from None
            return {"content": content, "version": item["version"]}
