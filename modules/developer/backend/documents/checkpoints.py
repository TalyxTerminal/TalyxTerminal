"""Immutable shared-document content, separate from latest crash recovery."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from threading import RLock

from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from ..projects.service import atomic_json


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def source_identity(source: dict) -> str:
    if source.get("kind") == "file" and isinstance(source.get("uri"), str):
        uri = source["uri"]
        if not uri.startswith(("file:", "untitled:")):
            raise ContractError("invalid_input", t("The document needs a stable file or untitled source."))
        identity = {"kind": "file", "uri": uri.lower() if os.name == "nt" and uri.startswith("file:") else uri}
    elif source.get("kind") == "contribution" and all(isinstance(source.get(key), str) and source[key] for key in ("providerId", "sourceKey")):
        identity = {"kind": "contribution", "providerId": source["providerId"], "sourceKey": source["sourceKey"], "target": source.get("target")}
    else:
        raise ContractError("invalid_input", t("The document needs a stable source identity."))
    return digest(identity)


class CheckpointStore:
    MAX_BYTES = 16 * 1024 * 1024

    def __init__(self, storage_directory: Path):
        self.root = Path(storage_directory) / "document-checkpoints-v1"
        self.lock = RLock()

    @staticmethod
    def key(submission_id: str) -> str:
        if not isinstance(submission_id, str) or not submission_id.strip() or len(submission_id) > 512:
            raise ContractError("invalid_input", t("Provide a stable checkpoint submission identity."))
        return hashlib.sha256(submission_id.encode("utf-8")).hexdigest()

    def _load(self, checkpoint_id: str):
        if not isinstance(checkpoint_id, str) or re.fullmatch(r"[0-9a-f]{64}", checkpoint_id) is None:
            raise ContractError("invalid_input", t("The saved document checkpoint reference is invalid."))
        path = self.root / (checkpoint_id + ".json")
        if not path.is_file():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if record["formatVersion"] != 1 or record["checkpoint"]["checkpointId"] != checkpoint_id or digest(record["documents"]) != record["checkpoint"]["digest"]:
                raise ValueError("integrity")
            return record
        except (ValueError, KeyError, OSError):
            raise ContractError("checkpoint_damaged", t("The saved source checkpoint could not be read. Keep the workspace and current edits.")) from None

    def submission(self, submission_id: str, request: list[dict]):
        with self.lock:
            record = self._load(self.key(submission_id))
            if record and record["request"] != request:
                raise ContractError("conflict", t("This checkpoint submission already captured a different document selection. Use a new submission identity."))
            return self.summary(record) if record else None

    @staticmethod
    def summary(record: dict):
        return {"checkpoint": record["checkpoint"], "documents": [{key: value for key, value in item.items() if key not in {"content", "baseContent"}} for item in record["documents"]]}

    def create(self, submission_id: str, request: list[dict], documents: list[dict], *, max_bytes=MAX_BYTES):
        checkpoint_id = self.key(submission_id)
        prepared = []
        for item in documents:
            if not isinstance(item, dict) or not isinstance(item.get("capturedDocumentId"), str) or not isinstance(item.get("source"), dict) or not isinstance(item.get("content"), str) or not isinstance(item.get("baseContent"), str) or not isinstance(item.get("name"), str) or not isinstance(item.get("version"), int) or isinstance(item.get("version"), bool) or item["version"] < 1 or "sourceVersion" not in item:
                raise ContractError("invalid_input", t("The checkpoint document snapshot is incomplete."))
            prepared.append({**{key: item[key] for key in ("capturedDocumentId", "source", "name", "content", "baseContent", "version", "sourceVersion")},
                             **({"codeOrigin": item["codeOrigin"]} if "codeOrigin" in item else {}),
                             "sourceKey": source_identity(item["source"]), "contentDigest": hashlib.sha256(item["content"].encode("utf-8")).hexdigest()})
        if len({item["sourceKey"] for item in prepared}) != len(prepared):
            raise ContractError("invalid_input", t("Capture each document source only once."))
        prepared.sort(key=lambda item: item["sourceKey"])
        record = {"formatVersion": 1, "request": request, "checkpoint": {"checkpointId": checkpoint_id, "digest": digest(prepared)}, "documents": prepared}
        if max_bytes is not None and len(json.dumps(record, ensure_ascii=False).encode("utf-8")) > max_bytes:
            raise ContractError("invalid_input", t("The source checkpoint exceeds 16 MiB. Capture a smaller document selection."))
        try:
            with self.lock:
                previous = self.submission(submission_id, request)
                if previous:
                    return previous
                atomic_json(self.root / (checkpoint_id + ".json"), record)
                return self.summary(record)
        except OSError:
            raise ContractError("checkpoint_failed", t("The fixed source checkpoint could not be saved. Your documents are still open; retry saving the workspace."), execution="failed", effects="none") from None

    def read(self, checkpoint: dict, source_keys: list[str] | None = None):
        with self.lock:
            record = self._load(checkpoint.get("checkpointId"))
            if not record:
                raise ContractError("source_unavailable", t("The saved source checkpoint is unavailable. Keep the workspace and current edits."))
            if record["checkpoint"] != checkpoint:
                raise ContractError("checkpoint_damaged", t("The saved source checkpoint reference does not match its content."))
            selected = set(source_keys) if source_keys is not None else None
            if selected is not None and selected - {item["sourceKey"] for item in record["documents"]}:
                raise ContractError("source_unavailable", t("A selected document is absent from this source checkpoint."))
            return {"checkpoint": record["checkpoint"], "documents": [item for item in record["documents"] if selected is None or item["sourceKey"] in selected]}

    def release(self, checkpoint):
        with self.lock:
            record = self._load(checkpoint["checkpointId"])
            if record is not None:
                if record["checkpoint"] != checkpoint:
                    raise ContractError("checkpoint_damaged", t("The retained document checkpoint differs from its reference"))
                (self.root / (checkpoint["checkpointId"] + ".json")).unlink()
