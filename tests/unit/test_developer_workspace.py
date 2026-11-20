"""Affected-file history and management metadata against actual disk sources."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from talyx_sdk import ContractError

from modules.developer.backend.projects import history as implementation
from modules.developer.backend.projects.service import ProjectService, canonical_uri


def workspace(tmp_path):
    directory = tmp_path / "ordinary"
    directory.mkdir()
    (directory / "first.py").write_bytes(b"first\n")
    (directory / "other.py").write_bytes(b"other\n")
    service = ProjectService(tmp_path / "state")
    project = service.open(str(directory))
    app = FastAPI()
    app.include_router(service.router())
    return directory, service, project["projectId"], TestClient(app)


def test_history_restores_only_selected_file_and_rejects_changed_disk(tmp_path):
    directory, service, project, client = workspace(tmp_path)
    opened = service.read(project, "first.py")
    service.write(project, "first.py", "second\n", opened["version"])
    history = client.get(f"/projects/{project}/history", params={"path": "first.py"}).json()
    assert len(history["revisions"]) == 2
    selected = history["revisions"][-1]["revisionId"]
    old = client.get(f"/projects/{project}/history/{selected}", params={"path": "first.py"}).json()
    assert old["content"] == "first\n"
    current = service.read(project, "first.py")
    (directory / "first.py").write_text("external\n", encoding="utf-8")
    refused = client.post(f"/projects/{project}/history/{selected}/restore", json={"path": "first.py", "expectedVersion": current["version"]}).json()
    assert refused["status"] == "conflict"
    fresh = service.read(project, "first.py")
    restored = client.post(f"/projects/{project}/history/{selected}/restore", json={"path": "first.py", "expectedVersion": fresh["version"]}).json()
    assert restored["status"] == "saved"
    assert (directory / "first.py").read_text() == "first\n"
    assert (directory / "other.py").read_text() == "other\n"
    assert len(service.history.list(project, "first.py")["revisions"]) == 4


def test_history_interruption_preserves_previous_record_and_completed_source_write(tmp_path, monkeypatch):
    directory, service, project, client = workspace(tmp_path)
    original = service.read(project, "first.py")
    history_before = service.history.list(project, "first.py")
    atomic = implementation.atomic_bytes
    def unavailable(path, data):
        if path.name == "index.json":
            raise PermissionError("History storage unavailable")
        atomic(path, data)
    with monkeypatch.context() as patch:
        patch.setattr(implementation, "atomic_bytes", unavailable)
        assert service.read(project, "first.py")["content"] == "first\n"
        saved = service.write(project, "first.py", "completed source\n", original["version"])
        assert saved["status"] == "saved" and saved["historyWarning"]
        assert (directory / "first.py").read_text() == "completed source\n"
    assert service.history.list(project, "first.py")["revisions"] == history_before["revisions"]
    service.read(project, "first.py")
    assert len(service.history.list(project, "first.py")["revisions"]) == 2


def test_history_capacity_evicts_owned_material_without_touching_sources(tmp_path, monkeypatch):
    directory, service, project, client = workspace(tmp_path)
    monkeypatch.setattr(implementation, "MAX_PROJECT", 15)
    one = service.read(project, "first.py")
    two = service.write(project, "first.py", "second\n", one["version"])
    service.write(project, "first.py", "third\n", two["version"])
    revisions = service.history.list(project, "first.py")["revisions"]
    assert len(revisions) == 2
    assert (directory / "first.py").read_text() == "third\n"
    assert (directory / "other.py").read_text() == "other\n"
    assert sum(file.stat().st_size for file in (service.history._directory(project) / "blobs").iterdir()) <= 15


def test_the_recently_opened_project_survives_reopen(tmp_path):
    directory, service, project, client = workspace(tmp_path)
    activated = client.post(f"/projects/{project}/activate").json()
    restored = ProjectService(tmp_path / "state").open(str(directory))
    assert restored["projectId"] == project
    assert restored["lastOpened"] == activated["project"]["lastOpened"]


def test_a_project_whose_folder_is_gone_can_still_be_chosen_to_remove_it(tmp_path):
    directory, service, project, client = workspace(tmp_path)
    for path in directory.iterdir():
        path.unlink()
    directory.rmdir()
    chosen = client.post(f"/projects/{project}/activate").json()["project"]
    assert chosen["status"] == "unavailable"
    with pytest.raises(ContractError) as error:
        client.get(f"/projects/{project}/files")
    assert error.value.code == "source_unavailable"


def test_percent_names_and_distinct_unicode_spellings_keep_their_own_history(tmp_path):
    directory, service, project, client = workspace(tmp_path)
    literal = directory / "literal%20name.py"
    literal.write_bytes(b"percent name\n")
    located = service.locate(canonical_uri(literal))
    assert located["path"] == "literal%20name.py"
    assert service.read(project, located["path"])["content"] == "percent name\n"
    # Windows file comparison does not collapse distinct Unicode spellings.
    service.history.capture(project, "straße.py", b"one", "Saved")
    service.history.capture(project, "strasse.py", b"two", "Saved")
    assert service.history.list(project, "straße.py")["sourceId"] != service.history.list(project, "strasse.py")["sourceId"]


def test_native_reference_is_single_file_readonly_and_never_a_user_project(tmp_path):
    directory, service, project, client = workspace(tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    file = library / "declaration.py"
    file.write_bytes(b"class Context: pass\n")
    (library / "neighbor.py").write_bytes(b"unselected\n")
    before = service.list()
    located = service.locate(canonical_uri(file), register=True, reference=True)
    reference = located["project"]
    assert reference["status"] == "reference" and reference["readOnly"]
    assert service.list() == before
    assert service.read(reference["projectId"], located["path"])["content"] == "class Context: pass\n"
    assert not service.history._directory(reference["projectId"]).exists()
    with pytest.raises(ContractError):
        service.path(reference["projectId"], "neighbor.py")
    with pytest.raises(ContractError):
        service.path(reference["projectId"])
    with pytest.raises(ContractError):
        service.write(reference["projectId"], located["path"], "changed", None)
    restored = ProjectService(tmp_path / "state")
    assert restored.locate(canonical_uri(file), reference["projectId"])["project"]["projectId"] == reference["projectId"]
    assert restored.list() == before
    # An explicitly opened file still grants its ordinary editable directory.
    explicit = restored.locate(canonical_uri(file), register=True)
    assert not explicit["project"].get("readOnly")
    assert len(restored.list()) == len(before) + 1


def test_native_nested_new_file_creates_parents_without_bypassing_file_cas(tmp_path, monkeypatch):
    directory, service, project, client = workspace(tmp_path)
    created = service.write(project, ".vscode/settings.json", '{"editor.fontSize": 13}', None)
    assert created["status"] == "saved"
    assert json.loads((directory / ".vscode/settings.json").read_text()) == {"editor.fontSize": 13}
    assert service.write(project, ".vscode/settings.json", "{}", None)["status"] == "conflict"
    target = directory / "newdir" / "appeared.py"
    original_mkdir = type(directory).mkdir
    def mkdir_with_external_file(path, *args, **kwargs):
        result = original_mkdir(path, *args, **kwargs)
        if path == target.parent:
            target.write_bytes(b"external owner\n")
        return result
    monkeypatch.setattr(type(directory), "mkdir", mkdir_with_external_file)
    refused = service.write(project, "newdir/appeared.py", "my input\n", None)
    assert refused["status"] == "conflict"
    assert target.read_bytes() == b"external owner\n"
