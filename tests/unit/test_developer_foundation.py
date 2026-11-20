"""Projects: real folders, their files' versions and history, and a folder inside a registered project."""
import pytest
from talyx_sdk import ContractError

from modules.developer.backend.projects.service import ProjectService, canonical_uri


def test_project_identity_version_conflict_and_rebinding(tmp_path):
    directory = tmp_path / "plain"
    directory.mkdir()
    file = directory / "main.py"
    file.write_text("first\n", encoding="utf8")
    store = ProjectService(tmp_path / "state")
    project = store.open(str(directory))
    assert not (directory / "talyx.module.json").exists()
    located = store.locate(canonical_uri(file))
    assert located["project"]["projectId"] == project["projectId"]
    initial = store.read(project["projectId"], "main.py")
    file.write_text("external\n", encoding="utf8")
    assert store.write(project["projectId"], "main.py", "mine\n", initial["version"])["status"] == "conflict"
    assert file.read_text() == "external\n"
    current = store.read(project["projectId"], "main.py")
    assert store.write(project["projectId"], "main.py", "saved\n", current["version"])["status"] == "saved"
    directory.rename(tmp_path / "original")
    directory.mkdir()
    file.write_text("replacement\n", encoding="utf8")
    with pytest.raises(ContractError) as replaced:
        store.read(project["projectId"], "main.py")
    assert replaced.value.code == "source_replaced"
    rebound = store.open(str(directory))
    assert rebound["projectId"] == project["projectId"]
    assert store.read(project["projectId"], "main.py")["content"].splitlines() == ["replacement"]


def test_unchanged_reads_do_not_rewrite_history_but_changes_do(tmp_path, monkeypatch):
    from modules.developer.backend.projects import history as history_module

    directory = tmp_path / "project"
    directory.mkdir()
    file = directory / "main.py"
    file.write_bytes(b"first\n")
    store = ProjectService(tmp_path / "state")
    project_id = store.open(str(directory))["projectId"]
    store.read(project_id, "main.py")
    source_id = store.history.list(project_id, "main.py")["sourceId"]
    writes = []
    original = history_module.atomic_bytes

    def capture_write(path, data):
        writes.append(path)
        original(path, data)

    monkeypatch.setattr(history_module, "atomic_bytes", capture_write)
    for _ in range(3):
        assert store.read(project_id, "main.py")["content"] == "first\n"
    assert writes == []
    file.write_bytes(b"changed\n")
    store.read(project_id, "main.py")
    assert sum(path.name == "index.json" for path in writes) == 1
    assert len(store.history.list(project_id, "main.py")["revisions"]) == 2
    assert store.history.list(project_id, "main.py")["sourceId"] == source_id


def test_project_names_and_folders_inside_a_registered_project(tmp_path):
    store = ProjectService(tmp_path / "state")
    topic = tmp_path / "workspace" / "7d1f"
    step = topic / "steps" / "9a2b"
    step.mkdir(parents=True)
    project = store.open(str(topic), name="Gold study")
    assert project["name"] == "Gold study"
    assert store.open(str(step))["projectId"] == project["projectId"]
    assert [item["name"] for item in store.list()] == ["Gold study"]
    assert store.open(str(topic), name="Gold study v2")["name"] == "Gold study v2"
    assert store.open(str(tmp_path / "workspace" / "other"), create=True)["name"] == "other"
