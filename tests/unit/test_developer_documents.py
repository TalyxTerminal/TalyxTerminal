"""Shared documents: the file on disk (or a module's document) plus one edit buffer, shared by editors and callers."""
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from talyx_sdk import ContractError

from modules.developer.backend import models
from modules.developer.backend.documents import actions
from modules.developer.backend.documents.service import DocumentService
from modules.developer.backend.projects.service import ProjectService

CONTRACT = json.loads((Path(__file__).resolve().parents[2] / "modules/developer/public/contract.json").read_text(encoding="utf8"))


@pytest.mark.parametrize(("change", "valid"), [
    ({"content": ""}, True),
    ({}, False),
    ({"edits": [{"start": 0, "end": 0, "text": "new"}]}, False),
    ({"content": "new", "unexpected": True}, False),
])
def test_edit_input_replaces_the_buffer_content(change, valid):
    value = {"documentId": "document", "expectedVersion": 1, **change}
    if valid:
        models.EditDocumentInput.model_validate(value)
    else:
        with pytest.raises(ValidationError):
            models.EditDocumentInput.model_validate(value)


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Developer:
    """What the documents need of Developer: somewhere to publish document_changed, and other modules' document sources."""

    def __init__(self, read=None, write=None):
        self.events, self.read, self.write = [], read, write
        self.descriptor, self.available = {"name": "Owner's documents", "readOnly": False}, True

    def publish(self, event, payload):
        self.events.append((event, payload))

    def implementations(self, point):
        return [{"moduleId": "owner", "id": "source", "descriptor": self.descriptor}] if point == "document_source" and self.available else []

    async def extension(self, point, module_id, id, name, value):
        assert (point, module_id, id) == ("document_source", "owner", "source")
        return await (self.read if name == "read" else self.write)(value)


def caller(name):
    return SimpleNamespace(caller=SimpleNamespace(origin="module", module=name, workspace_id=None))


def current(documents, document_id):
    return documents.snapshot(documents.record(document_id))


@pytest.fixture
def setup(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "main.py"
    source.write_text("alpha\nbeta\ngamma\n", encoding="utf8", newline="\n")
    projects = ProjectService(tmp_path / "data")
    projects.open(str(project))
    developer = Developer()
    return DocumentService(developer, projects, tmp_path / "store"), source, developer.events


async def open_file(documents, source):
    return await documents.open({"source": {"kind": "file", "uri": source.as_uri()}}, caller("editor-a"))


async def edit(documents, snapshot, content, who):
    return await documents.edit({"documentId": snapshot["documentId"], "expectedVersion": snapshot["version"], "content": content}, caller(who))


@pytest.mark.anyio
async def test_read_source_shares_dirty_deleted_buffer_without_registering_projects(setup, tmp_path):
    documents, source, _ = setup
    value = {"source": {"kind": "file", "uri": source.as_uri()}}
    first = await documents.read_source(value)
    changed = await edit(documents, first, "unsaved", "editor")
    source.unlink()
    shared = await documents.read_source(value)
    assert shared["documentId"] == changed["documentId"] and shared["version"] == changed["version"]
    assert shared["content"] == "unsaved" and shared["dirty"] and shared["conflict"]
    assert shared["externalState"] == "deleted" and shared["sourceVersion"] == first["sourceVersion"]
    models.Document.model_validate(shared)
    projects = documents.projects.list()
    outside = tmp_path / "outside.py"
    outside.write_text("outside", encoding="utf8")
    for uri, code in ((outside.as_uri(), "source_unavailable"), ((source.parent / "missing.py").as_uri(), "not_found")):
        with pytest.raises(ContractError) as error:
            await documents.read_source({"source": {"kind": "file", "uri": uri}})
        assert error.value.code == code
    assert documents.projects.list() == projects
    assert len(documents.records) == 1
    assert CONTRACT["actions"]["documents.read_source"]["effects"] == ["read"]
    assert CONTRACT["actions"]["documents.open"]["effects"] == ["write"]


@pytest.mark.anyio
async def test_a_module_document_is_read_through_its_source_and_keeps_unsaved_edits(tmp_path, monkeypatch):
    state = {"allowed": True, "readOnly": False}

    async def read(value):
        assert value == {"sourceKey": "code"}
        if not state["allowed"]:
            raise ContractError("permission_denied", "The attached root was detached")
        return {"content": "base", "version": "v1", "name": "code.py", "readOnly": state["readOnly"],
                "context": {"contextId": "source", "projectId": None, "rootUri": None, "languageMaterials": []}}
    developer = Developer(read=read)
    documents = DocumentService(developer, ProjectService(tmp_path / "data"), tmp_path / "store")
    value = {"source": {"kind": "contribution", "providerId": "owner:source", "sourceKey": "code"}}
    first = await documents.read_source(value)
    assert first["content"] == "base" and first["language"] == "python" and first["uri"].startswith("talyx-source:/")
    changed = await edit(documents, first, "unsaved", "editor")
    from modules.developer.backend import entry

    monkeypatch.setattr(entry, "app", SimpleNamespace(documents=documents))
    unsaved = await entry.list_documents(models.ListDocumentsInput(), None)
    assert [item["documentId"] for item in unsaved["documents"]] == [changed["documentId"]]
    assert not (await entry.list_documents(models.ListDocumentsInput(rootUris=[tmp_path.as_uri()]), None))["documents"]
    state["readOnly"] = True
    shared = await documents.read_source(value)
    assert shared["readOnly"] and shared["dirty"] and shared["documentId"] == changed["documentId"]
    developer.descriptor["readOnly"], state["readOnly"] = True, False
    assert (await documents.read_source(value))["readOnly"]
    state["allowed"] = False
    with pytest.raises(ContractError) as error:
        await documents.read_source(value)
    assert error.value.code == "permission_denied"
    developer.available = False
    with pytest.raises(ContractError) as error:
        await documents.read_source(value)
    assert error.value.code == "unavailable"


@pytest.mark.anyio
async def test_two_editors_and_backend_reject_old_revision_without_losing_committed_text(setup):
    documents, source, events = setup
    a, b = await asyncio.gather(open_file(documents, source), open_file(documents, source))
    assert a["documentId"] == b["documentId"]
    outcomes = await asyncio.gather(*(edit(documents, a, f"{name}\n", name) for name in ("editor-a", "editor-b", "agent")), return_exceptions=True)
    assert sum(isinstance(item, dict) for item in outcomes) == 1
    rejected = [item for item in outcomes if isinstance(item, ContractError)]
    assert len(rejected) == 2
    assert all(item.code == "conflict" and item.details["version"] == 2 for item in rejected)
    assert current(documents, a["documentId"])["content"] == "editor-a\n"
    assert events[-1] == ("document_changed", {"documentId": a["documentId"], "version": 2, "kind": "changed", "origin": "editor-a",
                                               "externalState": "present"})


@pytest.mark.anyio
async def test_unsaved_buffer_survives_restart_and_saves_to_disk(setup, tmp_path):
    documents, source, _ = setup
    opened = await open_file(documents, source)
    changed = await edit(documents, opened, "unsaved\n", "editor")
    restarted = DocumentService(documents.developer, documents.projects, tmp_path / "store")
    assert (await open_file(restarted, source))["content"] == "unsaved\n"
    remote = await edit(restarted, changed, "agent changed closed document\n", "agent")
    saved = await restarted.save({"documentId": remote["documentId"], "expectedVersion": remote["version"]}, caller("agent"))
    assert saved["status"] == "saved"
    assert source.read_text(encoding="utf8") == remote["content"]
    assert not current(restarted, remote["documentId"])["dirty"]
    # Clean documents are not kept for recovery; the next start rereads the file.
    assert not list((tmp_path / "store" / "unsaved-documents").glob("*.json"))


@pytest.mark.anyio
@pytest.mark.parametrize("dirty", [False, True])
async def test_external_save_and_delete_follow_a_clean_buffer_and_conflict_with_an_unsaved_one(setup, dirty):
    documents, source, _ = setup
    opened = await open_file(documents, source)
    if dirty:
        opened = await edit(documents, opened, "my unsaved input\n", "a")
    replacement = source.with_suffix(".tmp")
    replacement.write_text("external save\n", encoding="utf8", newline="\n")
    os.replace(replacement, source)
    await documents.refresh()
    shared = current(documents, opened["documentId"])
    assert shared["conflict"] is dirty
    assert shared["content"] == ("my unsaved input\n" if dirty else "external save\n")
    assert shared["version"] == opened["version"] + (0 if dirty else 1)
    source.unlink()
    await documents.refresh()
    deleted = current(documents, opened["documentId"])
    assert deleted["externalState"] == "deleted"
    assert deleted["conflict"] is dirty


@pytest.mark.anyio
async def test_resolve_keeps_mine_or_takes_the_disk_version(setup):
    documents, source, _ = setup
    opened = await edit(documents, await open_file(documents, source), "mine\n", "a")
    source.write_text("theirs\n", encoding="utf8", newline="\n")
    await documents.refresh()
    disk = (await documents.versions(opened["documentId"]))["disk"]
    kept = await documents.resolve(opened["documentId"], "mine", opened["version"], disk["version"])
    assert kept["content"] == "mine\n" and kept["dirty"] and not kept["conflict"]
    assert (await documents.save({"documentId": kept["documentId"]}, caller("a")))["status"] == "saved"
    source.write_text("theirs again\n", encoding="utf8", newline="\n")
    await documents.refresh()
    changed = await edit(documents, current(documents, kept["documentId"]), "mine again\n", "a")
    source.write_text("final disk\n", encoding="utf8", newline="\n")
    await documents.refresh()
    disk = (await documents.versions(changed["documentId"]))["disk"]
    taken = await documents.resolve(changed["documentId"], "external", changed["version"], disk["version"])
    assert taken["content"] == "final disk\n" and not taken["dirty"]


@pytest.mark.anyio
async def test_watcher_reloads_external_change_once_a_second(setup):
    documents, source, events = setup
    opened = await open_file(documents, source)
    documents.start()
    try:
        source.write_text("outside\n", encoding="utf8", newline="\n")
        async with asyncio.timeout(3):
            while current(documents, opened["documentId"])["content"] != "outside\n":
                await asyncio.sleep(.05)
        assert events[-1][1]["origin"] == "disk"
    finally:
        documents.watcher.cancel()


@pytest.mark.anyio
async def test_disk_compare_and_swap_before_watcher_rejects_save(setup):
    documents, source, _ = setup
    opened = await open_file(documents, source)
    changed = await edit(documents, opened, "mine", "a")
    source.write_text("outside", encoding="utf8", newline="\n")
    result = await documents.save({"documentId": changed["documentId"]}, caller("a"))
    assert result["status"] == "conflict"
    assert source.read_text(encoding="utf8") == "outside"
    assert current(documents, changed["documentId"])["content"] == "mine"


@pytest.mark.anyio
async def test_a_file_save_commits_before_anything_queued_on_the_lock_sees_the_new_file(setup):
    documents, source, events = setup
    changed = await edit(documents, await open_file(documents, source), "mine\n", "a")
    await documents.lock.acquire()
    saves = [asyncio.create_task(documents.save({"documentId": changed["documentId"]}, caller(name))) for name in ("a", "b")]
    await asyncio.sleep(0)
    documents.lock.release()
    assert [result["status"] for result in await asyncio.gather(*saves)] == ["saved", "clean"]
    await documents.refresh()
    shared = current(documents, changed["documentId"])
    assert (shared["conflict"], shared["dirty"], source.read_text(encoding="utf8")) == (False, False, "mine\n")
    assert [payload["origin"] for _, payload in events if payload["origin"] in ("disk", "source")] == []


@pytest.mark.anyio
async def test_a_module_document_save_keeps_edits_made_while_it_saves(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    external = {"content": "base", "version": "base-v"}

    async def read(_value):
        return {**external, "name": "code.py", "canonicalUri": None, "language": "python",
                "context": {"contextId": "source", "projectId": None, "rootUri": None, "languageMaterials": []}}

    async def write(value):
        entered.set()
        await release.wait()
        external.update(content=value["content"], version="saved-v")
        return {"status": "saved", "version": "saved-v"}
    documents = DocumentService(Developer(read=read, write=write), ProjectService(tmp_path / "data"), tmp_path)
    opened = await documents.open({"source": {"kind": "contribution", "providerId": "owner:source", "sourceKey": "code"}})
    first = await edit(documents, opened, "first", "a")
    saving = asyncio.create_task(documents.save({"documentId": first["documentId"], "expectedVersion": first["version"]}, caller("a")))
    await entered.wait()
    second = await edit(documents, first, "second while saving", "a")
    release.set()
    assert (await saving)["version"] == first["version"]
    shared = current(documents, second["documentId"])
    assert shared["dirty"] and shared["content"] == second["content"]
    assert shared["baseContent"] == external["content"] == "first"


@pytest.mark.anyio
async def test_failed_document_commit_keeps_previous_persisted_and_live_generation(setup, tmp_path, monkeypatch):
    documents, source, _ = setup
    base = await open_file(documents, source)
    first = await edit(documents, base, "protected", "a")

    def denied(*_):
        raise PermissionError("Storage is read-only")
    with monkeypatch.context() as patch:
        patch.setattr("modules.developer.backend.documents.service.atomic_json", denied)
        with pytest.raises(PermissionError):
            await edit(documents, first, "uncommitted", "a")
    assert current(documents, first["documentId"])["content"] == "protected"
    restarted = DocumentService(documents.developer, documents.projects, tmp_path / "store")
    assert current(restarted, first["documentId"])["version"] == first["version"]


@pytest.mark.anyio
async def test_reopening_a_clean_file_rereads_disk(setup):
    documents, source, _ = setup
    opened = await open_file(documents, source)
    source.write_text("changed before reopen\n", encoding="utf8", newline="\n")
    reopened = await open_file(documents, source)
    assert reopened["documentId"] == opened["documentId"]
    assert reopened["content"] == "changed before reopen\n" and reopened["version"] == 2


@pytest.mark.anyio
async def test_document_list_covers_recovered_buffers_in_exactly_the_given_folders(setup, tmp_path):
    documents, source, _ = setup
    helper = source.parent / "strategy.py"
    helper.write_text("original")
    library = tmp_path / "topic" / "lib"
    library.mkdir(parents=True)
    shared = library / "helper.py"
    shared.write_text("shared")
    sibling = tmp_path / "topic" / "steps" / "other.py"
    sibling.parent.mkdir()
    sibling.write_text("other")
    generated = source.parent / "__pycache__" / "generated.py"
    generated.parent.mkdir()
    generated.write_text("generated")
    opened = []
    for path in (helper, shared, sibling, generated):
        opened.append(await edit(documents, await open_file(documents, path), "unsaved", "author"))
    documents.records[opened[0]["documentId"]]["conflict"] = True
    # Buffers recovered after a restart, before any editor opened them again.
    recovered = DocumentService(documents.developer, documents.projects, documents.root.parent)
    recovered.records[opened[0]["documentId"]]["conflict"] = True
    result = await recovered.list([source.parent.as_uri(), library.as_uri()])
    assert {item["documentId"] for item in result} == {item["documentId"] for item in opened[:2]}
    assert all(item["dirty"] for item in result)
    assert next(item for item in result if item["documentId"] == opened[0]["documentId"])["conflict"]
    assert all(set(item) == {"documentId", "uri", "version", "dirty", "conflict"} for item in result)


@pytest.mark.anyio
async def test_checkpoint_restore_is_unchanged_after_its_buffer_was_saved(setup):
    documents, source, _ = setup
    edited = await edit(documents, await open_file(documents, source), "selected market inputs", "author")
    saved = await actions.create_checkpoint(documents, {"submissionId": "before-run-save", "documents": [
        {"documentId": edited["documentId"], "expectedVersion": edited["version"]}]})
    assert (await documents.save({"documentId": edited["documentId"]}))["status"] == "saved"
    after_save = current(documents, edited["documentId"])
    assert after_save["sourceVersion"] != edited["sourceVersion"]
    stamp = source.stat().st_mtime_ns
    restored = await actions.restore_checkpoint(documents, {"checkpoint": saved["checkpoint"]}, caller("quant"))
    assert restored["documents"][0]["status"] == "unchanged"
    assert current(documents, edited["documentId"]) == after_save
    assert source.stat().st_mtime_ns == stamp

    # Matching checkpoint text must not hide a real unsaved/disk conflict.
    changed = await edit(documents, after_save, "another saved version", "author")
    await documents.save({"documentId": changed["documentId"]})
    changed = current(documents, edited["documentId"])
    await edit(documents, changed, edited["content"], "author")
    source.write_text("external version", encoding="utf8")
    conflicted = await actions.restore_checkpoint(documents, {"checkpoint": saved["checkpoint"]}, caller("quant"))
    assert conflicted["documents"][0]["status"] == "conflict"
    assert source.read_text() == "external version"
    assert current(documents, edited["documentId"])["content"] == edited["content"]


@pytest.mark.anyio
async def test_empty_checkpoint_still_reports_deleted_source(setup):
    documents, source, _ = setup
    source.write_text("", encoding="utf8")
    opened = await open_file(documents, source)
    saved = await actions.create_checkpoint(documents, {"submissionId": "empty-source", "documents": [
        {"documentId": opened["documentId"], "expectedVersion": opened["version"]}]})
    source.unlink()
    restored = await actions.restore_checkpoint(documents, {"checkpoint": saved["checkpoint"]}, caller("quant"))
    assert restored["documents"][0]["status"] == "conflict"
    assert not source.exists()


def test_a_long_document_is_read_a_page_of_lines_at_a_time():
    content = "".join(f"line {number}\n" for number in range(1, 5001))
    snapshot = {"documentId": "document:1", "content": content, "baseContent": content, "version": 1}
    first = actions.read_lines(dict(snapshot), 1, 10_000)
    assert first["startLine"] == 1 and first["totalLines"] == 5000 and first["nextLine"] > 1
    assert len(json.dumps(first).encode()) <= actions.PAGE_BYTES
    rest = actions.read_lines(dict(snapshot), first["nextLine"], 3)
    assert rest["content"] == "".join(f"line {number}\n" for number in range(first["nextLine"], first["nextLine"] + 3))
def test_a_long_single_line_pages_through_both_versions_at_character_offsets():
    from modules.developer.backend.documents import actions
    snapshot = {"content":"工" * 18_001,"baseContent":"Saved\t" * 8_000}
    from modules.developer.backend.models import ReadDocumentInput
    pages = [actions.read_page(snapshot, ReadDocumentInput(documentId="doc"))]
    while pages[-1]["nextChar"] is not None:
        pages.append(actions.read_chars(snapshot, pages[-1]["nextChar"]))
    for key in ("content","baseContent"):
        assert "".join(page[key] for page in pages) == snapshot[key]



@pytest.mark.anyio
async def test_all_origins_read_the_same_page_and_line_edits_preserve_neighbors(setup, monkeypatch):
    from talyx_sdk.process import Call, Caller, Codec

    from modules.developer.backend import entry

    documents, source, _ = setup
    source.write_text("first\nsecond\nthird\n" + "longé" * 9000, encoding="utf-8")
    doc = await open_file(documents, source)
    monkeypatch.setattr(entry, "app", SimpleNamespace(documents=documents, projects=documents.projects))
    async def invoke(action, args, origin):
        spec = entry.module.actions[action]
        return Codec(spec.output).check(await spec.handler(Codec(spec.input).parse(json.dumps(args).encode()),
                                                            Call(None, Caller("editor", None, origin), "r")))
    for action, args in (("documents.read", {"documentId": doc["documentId"]}), ("projects.list", {})):
        page = await invoke(action, args, "ai")
        assert page == await invoke(action, args, "code")
        assert len(page.decode()) <= 18_000
        if action == "documents.read":
            part = json.loads(page)
            assert part["nextCursor"] and len(part["content"]) < len(doc["content"])
            following = json.loads(await invoke(action, {**args, "cursor":part["nextCursor"]}, "code"))
            assert following["content"].startswith(doc["content"][len(part["content"]):][:20])
    await documents.edit({"documentId": doc["documentId"], "expectedVersion": doc["version"],
                          "edits":[{"startLine":2,"endLine":2,"text":"changed"}]}, caller("editor-a"))
    assert current(documents, doc["documentId"])["content"] == doc["content"].replace("second", "changed")

@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["stat", "read_bytes"])
async def test_locked_file_does_not_stop_refreshing_other_buffers(setup, monkeypatch, failure):
    documents, locked, _ = setup
    other = locked.with_name("other.py")
    other.write_bytes(b"original")
    first = await open_file(documents, locked)
    second = await open_file(documents, other)
    locked.write_bytes(b"now locked")
    other.write_bytes(b"new content")
    original = getattr(Path, failure)

    def access(path, *args, **kwargs):
        if path == locked:
            raise PermissionError("locked for editing")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, failure, access)
        await documents.refresh()
        assert current(documents, first["documentId"])["error"]
        assert current(documents, second["documentId"])["content"] == "new content"
    await documents.refresh()
    assert current(documents, first["documentId"])["error"] is None
    assert current(documents, first["documentId"])["content"] == "now locked"


@pytest.mark.anyio
@pytest.mark.parametrize("ending", [b"\r\n", b"\n", b"\r"])
async def test_multiline_edit_preserves_file_endings_and_unterminated_last_line(setup, ending):
    documents, source, _ = setup
    source.write_bytes(ending.join([b"first", b"second", b"third"]))
    first = await open_file(documents, source)
    await documents.edit({"documentId": first["documentId"], "expectedVersion": first["version"],
                          "edits": [{"startLine": 2, "endLine": 3, "text": "new\nlast"}]}, None)
    await documents.save({"documentId": first["documentId"]})
    assert source.read_bytes() == ending.join([b"first", b"new", b"last"])
