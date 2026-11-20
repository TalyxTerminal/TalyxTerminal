"""Fixed source content survives restarts and submission retries unchanged."""
import json

import pytest
from talyx_sdk import ContractError

from modules.developer.backend.documents.checkpoints import CheckpointStore, source_identity


def captured(content="draft", *, source=None, document_id="document:first", version=3):
    return {"capturedDocumentId": document_id, "source": source or {"kind": "file", "uri": "file:///project/main.py", "projectId": "project"},
            "name": "main.py", "content": content, "baseContent": "stored", "version": version, "sourceVersion": "disk-v1"}


def test_content_source_and_submission_are_fixed_across_restarts(tmp_path):
    request = [{"documentId": "document:first", "expectedVersion": 3}]
    store = CheckpointStore(tmp_path)
    saved = store.create("workspace:save:one", request, [captured()])
    assert "content" not in saved["documents"][0]
    assert saved["documents"][0]["sourceKey"] == source_identity({"kind": "file", "uri": "file:///project/main.py", "projectId": "new session project"})
    restarted = CheckpointStore(tmp_path)
    assert restarted.submission("workspace:save:one", request) == saved
    assert restarted.create("workspace:save:one", request, [captured("newer input")]) == saved
    assert restarted.read(saved["checkpoint"])["documents"][0]["content"] == "draft"
    with pytest.raises(ContractError, match="different document selection"):
        restarted.create("workspace:save:one", [{"documentId": "document:first", "expectedVersion": 4}], [captured("newer", version=4)])


def test_contribution_identity_includes_target_and_is_order_independent(tmp_path):
    left = {"kind": "contribution", "providerId": "quant:source", "sourceKey": "config", "target": {"a": 1, "b": 2}}
    same = {**left, "target": {"b": 2, "a": 1}}
    other = {**left, "target": {"a": 2, "b": 2}}
    assert source_identity(left) == source_identity(same)
    assert source_identity(left) != source_identity(other)
    store = CheckpointStore(tmp_path)
    saved = store.create("many", [], [captured(source=left), captured(source=other)])
    assert len(store.read(saved["checkpoint"], [source_identity(left)])["documents"]) == 1
    with pytest.raises(ContractError, match="only once"):
        store.create("duplicate", [], [captured(source=left), captured(source=same)])


def test_integrity_failure_and_path_escape_are_explicit(tmp_path):
    store = CheckpointStore(tmp_path)
    saved = store.create("original", [], [captured()])
    path = store.root / (saved["checkpoint"]["checkpointId"] + ".json")
    record = json.loads(path.read_text(encoding="utf8"))
    record["documents"][0]["content"] = "changed behind saved workspace"
    path.write_text(json.dumps(record), encoding="utf8")
    with pytest.raises(ContractError) as failure:
        store.read(saved["checkpoint"])
    assert failure.value.code == "checkpoint_damaged"
    with pytest.raises(ContractError) as failure:
        store.read({"checkpointId": "../external", "digest": "anything"})
    assert failure.value.code == "invalid_input"


def test_failed_capture_keeps_previous_fixed_content(tmp_path, monkeypatch):
    store = CheckpointStore(tmp_path)
    saved = store.create("successful", [], [captured()])
    def fail(*_args):
        raise PermissionError("blocked module storage")
    monkeypatch.setattr("modules.developer.backend.documents.checkpoints.atomic_json", fail)
    with pytest.raises(ContractError) as failure:
        store.create("failed", [], [captured("new")])
    assert failure.value.code == "checkpoint_failed"
    assert store.read(saved["checkpoint"])["documents"][0]["content"] == "draft"
    assert store.submission("failed", []) is None
