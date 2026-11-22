"""Developer's declarations: shared documents and projects other modules and AI use, the points modules provide
documents and editing help at, a new module from the template, and the panel's own routes. The contract is generated
from this file."""
from typing import Any

from talyx_sdk import ContractError
from talyx_sdk.declare import Module
from talyx_sdk.i18n import t
from talyx_sdk.process import Call, Context

from . import models as m
from . import sdk_sources
from .app import Developer
from .documents import actions as documents
from .documents.service import origin
from .projects.service import canonical_uri, file_path

module = Module("developer")
app: Developer | None = None   # the running Developer, set when the process starts
Answer = dict[str, Any]


async def show_file(input: dict, call: Call) -> dict:
    from pathlib import Path

    path = Path(input["path"])
    if not path.is_absolute() or not path.is_file():
        raise ContractError("not_found", t("Choose an existing file to open in the editor."))
    await call.call("platform.panels.open_target",
                    {"module": "developer", "panelType": "main", "objectId": "file:" + path.resolve().as_uri()})
    return {}


module.implement("agent.open_file", "editor", actions={"open": show_file})


@module.changes
class Changes:
    @staticmethod
    async def list(call_ids, run_id):
        return _running().documents.changes.list(call_ids, run_id)

    @staticmethod
    async def revert(call_id, direction):
        await _running().documents.revert(call_id, direction)


@module.on_delete_workspace
async def delete_workspace(workspace: str, *, preview: bool) -> list[str]:
    if not preview:
        _running().documents.changes.forget_workspace(workspace)
    return []

module.event("projects_changed", m.Empty, "The registered project list or a project in it changed.")
module.event("document_changed", m.DocumentChanged, "A shared document's buffer, saved text or state changed.")
module.extension_point(
    "development_support", descriptor=m.DevelopmentSupport,
    description="Help for documents a module knows: guides and examples listed in Developer's Help, and with the "
                "language capability the Python a matching file is analysed against.",
    actions={"invoke": (Answer, Answer, "{capability, command, document, project}: for language, command "
                                        "environment answers {environment: {python: {interpreterPath, "
                                        "environmentPath}}, languageMaterials}.")})
module.extension_point(
    "document_source", descriptor=m.DocumentSourceDescriptor,
    description="Documents a module serves itself, opened in Developer's editor by {kind: contribution, providerId: "
                "'module:id', sourceKey}.",
    actions={"read": (Answer, Answer, "{sourceKey, target}: {name, content, version, language, readOnly, context}."),
             "write": (Answer, Answer, "{sourceKey, target, content, expectedVersion}: {status, version, message}.")})
module.implement("developer.document_source", "talyx-sdk", actions={"read": sdk_sources.read, "write": sdk_sources.write})


def _value(model) -> dict:
    return model.model_dump(mode="json", exclude_unset=True)


@module.on_start
async def start(ctx: Context) -> None:
    global app
    app = Developer(ctx, await ctx.platform.storage())
    await app.start()


@module.private_routes
def routes(ctx: Context):
    """The Developer panel's own requests, and the editor's WebSocket to the Python language service."""
    return app.routes()


def _running() -> Developer:
    if app is None:
        raise ContractError("unavailable", t("Developer is starting"))
    return app


# ── documents ────────────────────────────────────────────────────────────────────────────────────────────────────────

@module.action("documents.open", effects=["write"], errors=['binary_resource', 'invalid_input', 'not_found', 'resource_too_large', 'source_replaced', 'source_unavailable', 'unavailable'],
               examples=[{'source': {'kind': 'file', 'uri': 'file:///d%3A/work/strategy/main.py'}}])
async def open_document(input: m.OpenDocumentInput, call: Call) -> m.DocumentReceipt:
    """Open a file (its folder becomes a project unless it is inside one) or a module's document as a shared document:
    every editor and caller of the same source shares one buffer. Returns its documentId and version; read it with
    documents.read. Example: {"source":{"kind":"file","uri":"file:///d%3A/work/strategy/main.py"}}."""
    return documents.receipt(await _running().documents.open(_value(input), call))


@module.action("documents.read", effects=["read"], errors=['conflict', 'not_found'],
               examples=[{'documentId': 'document:1a2b'}, {'documentId': 'document:1a2b', 'startLine': 400, 'lineCount': 50}])
async def read_document(input: m.ReadDocumentInput, call: Call) -> m.Document:
    """A shared document a page of whole lines at a time: the buffer (content) from startLine, totalLines, version,
    whether it is unsaved (dirty) or changed on disk under unsaved edits (conflict, with the saved text in baseContent).
    nextCursor continues; startLine and lineCount read a chosen part. full:true returns the whole document in one
    answer (for editors). expectedVersion fails with conflict when it moved on."""
    shared = _running().documents
    snapshot = shared.snapshot(shared.record(input.documentId))
    if input.expectedVersion is not None:
        shared.check_version(snapshot, input.expectedVersion)
    return documents.read_page(snapshot, input)


@module.action("documents.read_source", effects=["read"], errors=['binary_resource', 'resource_too_large', 'source_replaced', 'source_unavailable', 'unavailable'],
               examples=[{'source': {'kind': 'contribution', 'providerId': 'chart:indicator-docs', 'sourceKey': 'guide'}}])
async def read_source(input: m.ReadSourceInput, call: Call) -> m.Document:
    """The shared document of a source that exists, without opening a new project for it (a file in an open project,
    or a module's document), paged like documents.read."""
    snapshot = await _running().documents.read_source(_value(input), call)
    return documents.read_page(snapshot, input)


@module.action("documents.edit", effects=["write"], errors=['conflict', 'not_found', 'permission_denied'],
               examples=[{'documentId': 'document:1a2b', 'expectedVersion': 3, 'edits': [{'startLine': 12, 'endLine': 13, 'text': 'WINDOW = 60'}]},
                         {'documentId': 'document:1a2b', 'expectedVersion': 3, 'content': 'print("hello")\n'}])
async def edit_document(input: m.EditDocumentInput, call: Call) -> m.DocumentReceipt:
    """Change a shared document's buffer at the version you read (a stale one fails with conflict): edits replaces
    line ranges (startLine..endLine, both included, numbered as in the version you read; text "" deletes them;
    the line ending is kept), content replaces the whole text. Nothing is written to disk until documents.save."""
    return documents.receipt(await _running().documents.edit(_value(input), call))


@module.action("documents.save", effects=["write"], errors=['conflict', 'not_found', 'permission_denied'],
               examples=[{'documentId': 'document:1a2b', 'expectedVersion': 4}])
async def save_document(input: m.SaveDocumentInput, call: Call) -> m.SaveResult:
    """Write a shared document's buffer to its source. status saved, clean (nothing to write) or conflict (the file
    changed on disk since it was read: compare first; nothing is overwritten). Saving runs no code. Example:
    {"documentId":"document:1a2b","expectedVersion":4}."""
    return await _running().documents.save(_value(input), call)


@module.action("documents.create", effects=["write"], errors=['conflict', 'permission_denied', 'source_replaced', 'source_unavailable'],
               examples=[{'projectId': '<id>', 'relativePath': 'backend/tools.py', 'content': '...'}])
async def create_document(input: m.CreateDocumentInput, call: Call) -> m.DocumentReceipt:
    """Create a new UTF-8 file in an open project and open it; an existing file fails with conflict. Example:
    {"projectId":"<id>","relativePath":"backend/tools.py","content":"..."}."""
    return documents.receipt(await _running().documents.create(_value(input), call))


@module.action("documents.list", effects=["read"], errors=['conflict', 'invalid_input'],
               examples=[{'rootUris': ['file:///d%3A/work/strategy']}])
async def list_documents(input: m.ListDocumentsInput, call: Call) -> m.DocumentList:
    """The shared documents open under these folders (file URIs), with their version and whether they are unsaved or
    in conflict. Omit rootUris to list every unsaved document in Recovery, including module-provided sources. nextCursor pages on. Example: {"rootUris":["file:///d%3A/work/strategy"]}."""
    shared = _running().documents
    if input.rootUris is None:
        items = [{key: record[key] for key in ("documentId", "uri", "version", "conflict")} | {"dirty": True}
                 for record in shared.unsaved_records()]
    else:
        items = await shared.list(input.rootUris)
    selected, cursor = documents.page(items, input.rootUris, input.cursor, input.limit)
    return {"documents": selected, "nextCursor": cursor}


@module.action("documents.create_checkpoint", effects=["write"], errors=['conflict', 'checkpoint_failed'],
               examples=[{'submissionId': 'save-1', 'documents': [{'documentId': 'document:1a2b', 'expectedVersion': 4}]}])
async def create_checkpoint(input: m.CreateCheckpointInput, call: Call) -> m.Checkpoint:
    """Keep the current content of these documents (each at the version you read) as a checkpoint, to restore later
    with documents.restore_checkpoint. The same submissionId always gives the same checkpoint. Example:
    {"submissionId":"save-1","documents":[{"documentId":"document:1a2b","expectedVersion":4}]}."""
    return await documents.create_checkpoint(_running().documents, _value(input))


@module.action("documents.restore_checkpoint", effects=["write"], errors=['checkpoint_damaged', 'not_found', 'source_unavailable'],
               examples=[{'checkpoint': {'checkpointId': '<id>', 'digest': '<digest>'}}])
async def restore_checkpoint(input: m.RestoreCheckpointInput, call: Call) -> m.Restored:
    """Put a checkpoint's content back into its documents' buffers. A document changed since (status conflict) keeps
    its content unless a resolution names its current version; then what it had is kept in preservedCheckpoint.
    Example: {"checkpoint":{"checkpointId":"<id>","digest":"<digest>"}}."""
    return await documents.restore_checkpoint(_running().documents, _value(input), call)


# ── projects ─────────────────────────────────────────────────────────────────────────────────────────────────────────

@module.action("projects.list", effects=["read"], errors=['conflict', 'invalid_input'],
               examples=[{}])
async def list_projects(input: m.ListProjectsInput, call: Call) -> m.ProjectList:
    """The folders open in Developer as projects. Continue a long directory with nextCursor."""
    items = [{key: project[key] for key in ("projectId", "name", "rootUri", "rootIdentity", "contextId", "status")}
             for project in _running().projects.list()]
    items, cursor = documents.page(items, "projects", input.cursor, input.limit or 50)
    return {"projects":items, "nextCursor":cursor}


@module.action("projects.open", effects=["write"], errors=['invalid_input', 'source_unavailable'],
               examples=[{'directory': 'D:/work/strategy', 'name': 'Strategy'}])
async def open_project(input: m.OpenProjectInput, call: Call) -> m.Project:
    """Open a folder as a project (a folder inside an open project gives that project). Example:
    {"directory":"D:/work/strategy","name":"Strategy"}."""
    project = _running().projects.open(input.directory, name=input.name)
    return {key: project[key] for key in ("projectId", "name", "rootUri", "rootIdentity", "contextId", "status")}


@module.action("projects.read_tree", effects=["read"], errors=['invalid_input', 'permission_denied', 'source_replaced', 'source_unavailable'],
               examples=[{'projectId': '<id>', 'path': 'backend'}])
async def read_tree(input: m.ReadTreeInput, call: Call) -> m.Tree:
    """The files and folders in one folder of a project (path relative to its root, "" for the root); nextCursor
    pages on. Example: {"projectId":"<id>","path":"backend"}."""
    return _running().tree_page(input.projectId, input.path, input.cursor, input.limit)


@module.action("projects.instructions", effects=["read"], errors=['conflict', 'source_replaced', 'source_unavailable'],
               examples=[{'projectId': '<id>'}])
async def instructions(input: m.InstructionsInput, call: Call) -> m.Instructions:
    """The project's AGENTS.md (how to work in it), a part at a time: startLine, lineCount, nextLine. Example:
    {"projectId":"<id>"}."""
    return _running().instructions(input.projectId, input.startLine, input.lineCount, input.expectedDigest, input.startChar)


@module.action("projects.create_module", effects=["write"], errors=['conflict', 'invalid_input', 'invalid_module'],
               examples=[{'name': 'Signals'}])
async def create_module(input: m.CreateModuleInput, call: Call) -> m.CreatedModule:
    """Create a new TALYX module from Developer's template (one action, greet, and a panel that calls it) in the folder
    of the user's installed modules, and open it as a project. First select its project in Developer and click
    Use in TALYX to install and start it. After a
    change, reloading it regenerates its contract and restarts only it. Example: {"name":"Signals"}."""
    return await _running().create_module(input.name, input.moduleId)


@module.action("documents.read_checkpoint", effects=["read"], errors=["checkpoint_damaged", "source_unavailable", "invalid_input"],
               examples=[{"checkpoint": {"checkpointId": "<id>", "digest": "<digest>"}}])
async def read_checkpoint(input: m.ReadCheckpointInput, call: Call) -> m.CheckpointList | m.CheckpointText:
    """List the documents in a checkpoint, or read one sourceKey's captured buffer and saved baseline.
    Continue the list with nextCursor, or the text by passing nextChar as startChar."""
    store = _running().documents.checkpoints
    fixed = store.read(_value(input.checkpoint), [input.sourceKey] if input.sourceKey is not None else None)
    if input.sourceKey is None:
        summary = store.summary(fixed)
        items, cursor = documents.page(summary["documents"], fixed["checkpoint"], input.cursor)
        return {**summary, "documents": items, "nextCursor": cursor}
    saved, = fixed["documents"]
    return documents.read_chars({key: saved[key] for key in ("sourceKey", "content", "baseContent")}, input.startChar)


@module.action("documents.read_versions", effects=["read"], errors=["not_found", "source_unavailable"],
               examples=[{"documentId": "document:1a2b"}])
async def read_versions(input: m.VersionsInput, call: Call) -> m.VersionsPage:
    """Compare the unsaved buffer (content), its saved baseline and the current external content at the same character
    offset. Continue at nextChar. version and sourceVersion identify the buffer and external source for resolve."""
    versions = await _running().documents.versions(input.documentId)
    base, disk, buffer = versions["base"], versions["disk"], versions["unsaved"]
    return documents.read_chars({"content": buffer["content"], "baseContent": base["content"], "externalContent": disk["content"],
                                 "version": buffer["version"], "sourceVersion": disk["version"], "baseVersion": base["version"]},
                                input.startChar, fields=("content", "baseContent", "externalContent"))


@module.action("documents.resolve", effects=["write"], errors=["conflict", "permission_denied", "not_found"],
               examples=[{"documentId": "document:1a2b", "mode": "mine", "expectedVersion": 2, "expectedSourceVersion": "<digest>"}])
async def resolve_document(input: m.ResolveInput, call: Call) -> m.DocumentReceipt:
    """Resolve the versions you compared with documents.read_versions. external replaces the buffer with the external
    text. mine keeps the buffer against that external version; use documents.save to write it. Stale versions conflict."""
    shared = _running().documents
    result = await shared.resolve(input.documentId, input.mode, input.expectedVersion, input.expectedSourceVersion, origin(call))
    return documents.receipt(result)


@module.action("help", effects=["read"], examples=[{}])
async def help_entries(input: m.ListProjectsInput, call: Call) -> m.HelpPage:
    """List the SDK and development support references shown in Help. Read an entry with documents.read_source."""
    entries = [{"support": group["name"], **item} for group in _running().help() for item in group["documentation"]]
    items, cursor = documents.page(entries, "help", input.cursor, input.limit or 50)
    return {"entries": items, "nextCursor": cursor}


@module.action("projects.file_history", effects=["read"], examples=[{"projectId": "<id>", "path": "main.py"}])
async def file_history(input: m.FileRevisionInput, call: Call) -> m.RevisionList | m.RevisionText:
    """List the file's saved revisions newest first, or read one revisionId. Continue its text at nextChar."""
    projects = _running().projects
    if input.revisionId is None:
        return projects.file_history(input.projectId, input.path)
    saved = projects.revision(input.projectId, input.path, input.revisionId)
    return documents.read_chars(saved, input.startChar, fields=("content",))


@module.action("projects.restore_revision", effects=["write"], errors=["source_unavailable", "permission_denied", "conflict"],
               examples=[{"projectId": "<id>", "path": "main.py", "revisionId": "<id>", "expectedVersion": "<current file digest>"}])
async def restore_revision(input: m.RestoreRevisionInput, call: Call) -> m.RevisionSaveResult:
    """Write a saved revision to the file if its disk version still matches. Take expectedVersion from documents.read's
    sourceVersion. Resolve or save unsaved edits first. A changed disk version returns status conflict."""
    app = _running()
    uri = canonical_uri(app.projects.path(input.projectId, input.path))
    if any(item["uri"] == uri for item in app.documents.unsaved_records()):
        raise ContractError("conflict", t("Save or discard this file's unsaved edits before restoring a revision."))
    return await app.projects.restore_revision(input.projectId, input.path, input.revisionId, input.expectedVersion)


@module.action("projects.module_status", effects=["read"], examples=[{"projectId": "<id>"}])
async def project_module_status(input: m.ProjectId, call: Call) -> m.ProjectModuleState:
    """The project's module manifest and TALYX runtime status. module is null for an ordinary project; runtime is null
    before installation. A module stored in TALYX's modules folder still needs Use in TALYX on first installation."""
    app = _running()
    module = app.module_of(input.projectId)
    runtime = None
    if module:
        state = await app.module_runtime(module["id"], call.workspace_id)
        if state:
            runtime = {key: state[key] for key in ("enabled", "state", "problem", "source")}
    return {"module": module, "runtime": runtime}


@module.action("projects.remove", effects=["delete"], errors=["conflict", "unavailable", "permission_denied", "recycle_failed"],
               examples=[{"projectId": "<id>", "mode": "forget", "expectedRootUri": "file:///D:/work", "rootIdentity": "<identity>"}])
async def remove_project(input: m.RemoveProjectInput, call: Call) -> m.RemovedProject:
    """Remove a project listing (forget), or also move the project folder to the Windows Recycle Bin (trash).
    Use rootUri and rootIdentity from projects.list or projects.open. Save or discard unsaved project files first."""
    return await _running().projects.remove_checked(input.projectId, input.mode, str(file_path(input.expectedRootUri)), input.rootIdentity)
