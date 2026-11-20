"""Project listing and reversible folder removal use only disposable test roots."""
import ctypes
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from talyx_sdk import ContractError
from talyx_sdk import recycle as recycling
from talyx_sdk.recycle import (
    _method,
    _ProgressSink,
    _recycle_allowed,
)

from modules.developer.backend.projects import service as implementation
from modules.developer.backend.projects.service import ProjectService


def project(tmp_path, **options):
    directory = tmp_path / "source"
    directory.mkdir()
    (directory / "main.py").write_text("keep source\n", encoding="utf-8")
    service = ProjectService(tmp_path / "state", **options)
    summary = service.open(str(directory))
    return directory, service, summary


def remove(service, summary, mode="forget", **changes):
    values = {"expected_root": summary["root"], "root_identity": summary["rootIdentity"]}
    return service.remove(summary["projectId"], mode, **{**values, **changes})


def test_forget_preserves_source_metadata_and_history(tmp_path):
    directory, service, summary = project(tmp_path)
    token = summary["projectId"]
    opened = service.read(token, "main.py")
    service.write(token, "main.py", "saved source\n", opened["version"])
    history = service.history.list(token, "main.py")

    assert remove(service, summary) == {"projectId": token, "mode": "forget", "removed": True}
    assert not service.list()
    assert (directory / "main.py").read_text() == "saved source\n"
    restored = ProjectService(tmp_path / "state")
    assert not restored.list()
    assert restored.history.list(token, "main.py") == history
    with pytest.raises(ContractError, match="no longer registered"):
        restored.get(token)

    reopened = restored.open(str(directory))
    assert reopened["projectId"] == token
    assert restored.history.list(token, "main.py") == history
    assert ProjectService(tmp_path / "state").get(token)["status"] == "available"


def test_trash_passes_exact_registered_root_and_preserves_archives(tmp_path):
    recycled = tmp_path / "simulated-recycle-bin"
    calls = []

    def recycler(directory):
        calls.append(directory)
        directory.rename(recycled)

    directory, service, summary = project(tmp_path, recycler=recycler)
    service.read(summary["projectId"], "main.py")
    history = service.history.list(summary["projectId"], "main.py")
    assert remove(service, summary, "trash")["removed"]
    assert calls == [directory.resolve()]
    assert not directory.exists()
    assert (recycled / "main.py").read_text() == "keep source\n"
    restored = ProjectService(tmp_path / "state")
    assert not restored.list()
    assert restored.history.list(summary["projectId"], "main.py") == history
    recycled.rename(directory)
    assert restored.open(str(directory))["projectId"] == summary["projectId"]


def test_recycle_failure_keeps_registration_across_restart(tmp_path):
    def denied(_directory):
        raise PermissionError("Bin unavailable")

    directory, service, summary = project(tmp_path, recycler=denied)
    with pytest.raises(ContractError) as error:
        remove(service, summary, "trash")
    assert error.value.code == "recycle_failed"
    assert service.get(summary["projectId"])["status"] == "available"
    assert ProjectService(tmp_path / "state").get(summary["projectId"])["status"] == "available"
    assert (directory / "main.py").read_text() == "keep source\n"


def test_index_failure_does_not_attempt_recycling_or_lose_registration(tmp_path, monkeypatch):
    calls = []
    directory, service, summary = project(tmp_path, recycler=calls.append)

    def denied(*_args):
        raise PermissionError("Index read-only")

    monkeypatch.setattr(implementation, "atomic_json", denied)
    with pytest.raises(ContractError) as error:
        remove(service, summary, "trash")
    assert error.value.code == "storage_unavailable"
    assert error.value.effects == "none"
    assert not calls
    assert service.get(summary["projectId"])["status"] == "available"
    assert ProjectService(tmp_path / "state").get(summary["projectId"])["status"] == "available"
    assert directory.is_dir()


@pytest.mark.parametrize("changes", [
    {"expected_root": "different-folder"}, {"root_identity": "changed-identity"},
])
def test_stale_dialog_cannot_remove_a_changed_project(tmp_path, changes):
    calls = []
    directory, service, summary = project(tmp_path, recycler=calls.append)
    with pytest.raises(ContractError) as error:
        remove(service, summary, "trash", **changes)
    assert error.value.code == "conflict"
    assert not calls
    assert directory.is_dir()
    assert service.get(summary["projectId"])


def test_replaced_directory_refuses_trash_but_can_forget(tmp_path):
    calls = []
    directory, service, summary = project(tmp_path, recycler=calls.append)
    directory.rename(tmp_path / "original-source")
    directory.mkdir()
    (directory / "replacement.txt").write_text("preserve me")
    with pytest.raises(ContractError) as error:
        remove(service, summary, "trash")
    assert error.value.code == "source_replaced"
    assert not calls
    assert remove(service, summary)["removed"]
    assert (directory / "replacement.txt").read_text() == "preserve me"


def test_missing_directory_can_only_be_removed_from_list(tmp_path):
    directory, service, summary = project(tmp_path)
    directory.rename(tmp_path / "moved-source")
    with pytest.raises(ContractError) as error:
        remove(service, summary, "trash")
    assert error.value.code == "source_unavailable"
    assert remove(service, summary)["removed"]


@pytest.mark.parametrize("selection", ["protected", "child", "ancestor"])
def test_protected_configured_paths_and_their_ancestors_are_never_recycled(tmp_path, selection):
    protected = tmp_path / "installed"
    child = protected / "module"
    child.mkdir(parents=True)
    calls = []
    service = ProjectService(tmp_path / "state", recycler=calls.append, protected_paths=[protected])
    root = {"protected": protected, "child": child, "ancestor": tmp_path}[selection]
    summary = service.open(str(root))
    with pytest.raises(ContractError) as error:
        remove(service, summary, "trash")
    assert error.value.code == "permission_denied"
    assert not calls
    assert child.is_dir()


@pytest.mark.parametrize("selection", ["repo", "data", "home", "drive"])
def test_repository_data_home_and_drive_roots_are_never_recycled(tmp_path, selection):
    calls = []
    service = ProjectService(tmp_path / "state", recycler=calls.append)
    repository = Path(implementation.__file__).resolve().parents[4]
    root = {
        "repo": repository, "data": repository / "data", "home": Path.home(),
        "drive": Path(repository.anchor),
    }[selection]
    if not root.is_dir():
        pytest.skip("Protected directory does not exist on this machine")
    summary = service.open(str(root))
    with pytest.raises(ContractError) as error:
        remove(service, summary, "trash")
    assert error.value.code == "permission_denied"
    assert not calls
    assert root.is_dir()


def test_route_runs_async_session_guard_before_removing(tmp_path):
    calls = []

    async def before_remove(token, mode):
        calls.append((token, mode))
        raise ContractError("conflict", "Stop this project's running sessions first.")

    directory, service, summary = project(tmp_path, before_remove=before_remove)
    app = FastAPI()
    app.include_router(service.router())
    with TestClient(app) as client, pytest.raises(ContractError, match="running sessions"):
        client.post(f"/projects/{summary['projectId']}/remove", json={
            "mode": "trash", "expectedRoot": summary["root"],
            "rootIdentity": summary["rootIdentity"],
        })
    assert calls == [(summary["projectId"], "trash")]
    assert directory.is_dir()
    assert service.get(summary["projectId"])


def test_route_forget_returns_result_and_validates_payload(tmp_path):
    directory, service, summary = project(tmp_path)
    app = FastAPI()
    app.include_router(service.router())
    with TestClient(app) as client:
        for invalid in [None, [], {"mode": []}, {"mode": "permanent"}]:
            with pytest.raises(ContractError):
                client.post(f"/projects/{summary['projectId']}/remove", json=invalid)
        response = client.post(f"/projects/{summary['projectId']}/remove", json={
            "mode": "forget", "expectedRoot": summary["root"],
            "rootIdentity": summary["rootIdentity"],
        })
    assert response.json() == {"projectId": summary["projectId"], "mode": "forget", "removed": True}
    assert directory.is_dir()
    assert not ProjectService(tmp_path / "state").list()


def test_shell_progress_vetoes_permanent_delete_and_replaced_source(tmp_path):
    directory = tmp_path / "folder"
    directory.mkdir()
    stat = directory.stat()
    captured = (stat.st_dev, stat.st_ino)
    assert not _recycle_allowed(0, directory.resolve(), captured)
    assert _recycle_allowed(0x80, directory.resolve(), captured)
    directory.rename(tmp_path / "original")
    directory.mkdir()
    assert not _recycle_allowed(0x80, directory.resolve(), captured)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows COM calling convention")
def test_native_progress_sink_vtable_vetoes_permanent_delete(tmp_path):
    directory = tmp_path / "folder"
    directory.mkdir()
    stat = directory.stat()
    sink = _ProgressSink(directory.resolve(), (stat.st_dev, stat.st_ino))
    pointer = ctypes.byref(sink.instance)
    callback = _method(pointer, 11, ctypes.c_uint32, ctypes.c_void_p)
    assert callback(pointer, 0, None) < 0
    assert callback(pointer, 0x80, None) == 0
    post = _method(pointer, 12, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p)
    assert post(pointer, 0x80, None, ctypes.c_long(0x80270021).value, None) == 0
    assert sink.safe_to_retry
    assert post(pointer, 0x80, None, 0, 1) == 0
    assert post(pointer, 0x80, None, ctypes.c_long(0x80270021).value, None) == 0
    assert not sink.safe_to_retry


@pytest.mark.skipif(os.name != "nt", reason="Windows recycling")
@pytest.mark.parametrize("result", [0x80270021, 0x80270027])
def test_native_recycling_retries_transient_source_failure_with_original_identity(
    tmp_path, monkeypatch, result,
):
    directory = tmp_path / "folder"
    directory.mkdir()
    stat = directory.stat()
    captured = (stat.st_dev, stat.st_ino)
    attempts, pauses = [], []

    def attempt(path, *, expected_identity):
        attempts.append((path, expected_identity))
        if len(attempts) == 1:
            raise recycling._RecycleError(result, safe_to_retry=True)

    monkeypatch.setattr(recycling, "_recycle_directory_once", attempt)
    monkeypatch.setattr(
        recycling, "time", SimpleNamespace(monotonic=lambda: 0, sleep=pauses.append),
    )
    recycling.recycle_directory(directory)
    assert attempts == [(directory, captured), (directory, captured)]
    assert pauses == [0.1]


@pytest.mark.skipif(os.name != "nt", reason="Windows recycling")
@pytest.mark.parametrize(
    "result, safe", [(0x80270021, False), (0x80270022, True), (0x80070005, True)],
)
def test_native_recycling_does_not_retry_partial_or_other_failures(
    tmp_path, monkeypatch, result, safe,
):
    directory = tmp_path / "folder"
    directory.mkdir()
    failure = recycling._RecycleError(result, safe_to_retry=safe)
    attempts, pauses = [], []

    def attempt(path, *, expected_identity):
        attempts.append(path)
        raise failure

    monkeypatch.setattr(recycling, "_recycle_directory_once", attempt)
    monkeypatch.setattr(
        recycling, "time", SimpleNamespace(monotonic=lambda: 0, sleep=pauses.append),
    )
    with pytest.raises(recycling._RecycleError) as error:
        recycling.recycle_directory(directory)
    assert error.value is failure
    assert attempts == [directory]
    assert not pauses
    assert directory.is_dir()


@pytest.mark.skipif(os.name != "nt", reason="Windows recycling")
def test_native_recycling_retries_only_once_even_when_first_shell_call_is_slow(
    tmp_path, monkeypatch,
):
    directory = tmp_path / "folder"
    directory.mkdir()
    attempts, pauses = [], []

    def attempt(path, *, expected_identity):
        attempts.append(path)
        raise recycling._RecycleError(0x80270021, safe_to_retry=True)

    monkeypatch.setattr(recycling, "_recycle_directory_once", attempt)
    monkeypatch.setattr(
        recycling, "time", SimpleNamespace(sleep=pauses.append),
    )
    with pytest.raises(recycling._RecycleError):
        recycling.recycle_directory(directory)
    assert attempts == [directory, directory]
    assert pauses == [0.1]
    assert directory.is_dir()


@pytest.mark.skipif(os.name != "nt", reason="Windows recycling")
def test_native_recycling_rejects_replaced_root_before_next_attempt(tmp_path, monkeypatch):
    directory = tmp_path / "folder"
    directory.mkdir()
    original_attempt = recycling._recycle_directory_once
    calls = []

    def attempt(path, *, expected_identity):
        calls.append(expected_identity)
        if len(calls) == 1:
            directory.rename(tmp_path / "original")
            directory.mkdir()
            (directory / "replacement.txt").write_text("preserve me")
            raise recycling._RecycleError(0x80270021, safe_to_retry=True)
        return original_attempt(path, expected_identity=expected_identity)

    monkeypatch.setattr(recycling, "_recycle_directory_once", attempt)
    monkeypatch.setattr(
        recycling, "time", SimpleNamespace(monotonic=lambda: 0, sleep=lambda _: None),
    )
    with pytest.raises(OSError, match="replaced"):
        recycling.recycle_directory(directory)
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert (directory / "replacement.txt").read_text() == "preserve me"
    assert (tmp_path / "original").is_dir()
