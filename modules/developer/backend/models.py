"""The types of Developer's public actions, event and extension points (the contract is generated from them)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator
from talyx_sdk.declare import Model


class FileSource(Model):
    """A file on disk: its file URI; ``projectId`` picks the project when folders are nested."""
    kind: Literal["file"]
    uri: str
    projectId: str | None = None


class ContributionSource(Model):
    """A document another module provides at ``developer.document_source`` (``providerId`` is ``module:id``)."""
    kind: Literal["contribution"]
    providerId: str
    sourceKey: str
    target: dict[str, Any] | None = None


Source = FileSource | ContributionSource
Version = str | int | None


class DocumentContext(Model):
    """Where a document belongs: its project (none for a provider's document) and what a language support adds."""
    contextId: str
    projectId: str | None
    rootUri: str | None
    languageMaterials: list[dict[str, Any]] = []


class Document(Model):
    """One shared document: the source's saved text (``baseContent`` at ``sourceVersion``) and the edit buffer on top."""
    documentId: str
    uri: str
    name: str
    source: Source
    context: DocumentContext
    content: str
    baseContent: str | None
    """The saved text; null for a document without unsaved edits (the same as content)."""
    version: int
    sourceVersion: Version
    language: str
    dirty: bool
    conflict: bool
    readOnly: bool
    externalState: Literal["present", "deleted"]
    error: str | None
    nextCursor: str | None = None
    startLine: int | None = None
    totalLines: int | None = None
    nextLine: int | None = None
    startChar: int | None = None
    nextChar: int | None = None


class DocumentReceipt(Model):
    documentId: str
    uri: str
    name: str
    version: int
    dirty: bool
    conflict: bool


class OpenDocumentInput(Model):
    source: Source
    reference: bool = False
    """Open a file outside every project read-only, instead of opening its folder as a project."""


class DocumentReadOptions(Model):
    full: bool = False
    startLine: int | None = Field(None, ge=1)
    lineCount: int | None = Field(None, ge=1, le=2000)
    startChar: int | None = Field(None, ge=0)
    """Continue inside a line longer than one page (nextChar)."""
    cursor: str | None = Field(None, pattern=r"^[0-9]+(:[0-9]+)?$")
    """nextCursor of the previous page."""

    @model_validator(mode="after")
    def check_page(self):
        page = self.model_fields_set - {"full", "source", "documentId", "expectedVersion"}
        if self.full and page:
            raise ValueError("full cannot be combined with paging")
        if "cursor" in page and page & {"startLine", "startChar"}:
            raise ValueError("Use cursor or startLine, not both")
        return self


class ReadDocumentInput(DocumentReadOptions):
    documentId: str
    expectedVersion: int | None = None


class ReadSourceInput(DocumentReadOptions):
    source: Source


class LineEdit(Model):
    startLine: int = Field(ge=1)
    endLine: int = Field(ge=1)
    text: str


class EditDocumentInput(Model):
    documentId: str
    expectedVersion: int
    content: str | None = None
    edits: list[LineEdit] | None = Field(None, min_length=1)

    @model_validator(mode="after")
    def one_edit(self):
        if (self.content is None) == (self.edits is None):
            raise ValueError("Supply either content or edits")
        return self


class SaveDocumentInput(Model):
    documentId: str
    expectedVersion: int | None = None


class SaveResult(Model):
    documentId: str
    version: int
    status: Literal["saved", "clean", "conflict"]
    message: str | None


class CreateDocumentInput(Model):
    projectId: str
    relativePath: str
    content: str


class ListDocumentsInput(Model):
    rootUris: list[str] | None = None
    """Omit for all unsaved documents, including contributions. Supply folders to list their open file buffers."""
    cursor: str | None = None
    limit: int = Field(50, ge=1, le=200)


class ListedDocument(Model):
    documentId: str
    uri: str
    version: int
    dirty: bool
    conflict: bool


class DocumentList(Model):
    documents: list[ListedDocument]
    nextCursor: str | None


class CheckpointReference(Model):
    checkpointId: str
    digest: str


class CheckpointSelection(Model):
    documentId: str
    expectedVersion: int


class CreateCheckpointInput(Model):
    submissionId: str
    documents: list[CheckpointSelection]


class CheckpointDocument(Model):
    capturedDocumentId: str
    source: Source
    name: str
    version: int
    sourceVersion: Version
    sourceKey: str
    contentDigest: str


class Checkpoint(Model):
    checkpoint: CheckpointReference
    documents: list[CheckpointDocument]


class Resolution(Model):
    sourceKey: str
    expectedVersion: int
    expectedSourceVersion: Version


class RestoreCheckpointInput(Model):
    checkpoint: CheckpointReference
    sourceKeys: list[str] | None = None
    resolutions: list[Resolution] = []


class RestoredDocument(Model):
    sourceKey: str
    name: str
    documentId: str | None
    version: int | None
    sourceVersion: Version
    status: Literal["restored", "unchanged", "conflict", "read_only", "unavailable"]
    message: str | None
    preservedCheckpoint: CheckpointReference | None


class Restored(Model):
    checkpoint: CheckpointReference
    documents: list[RestoredDocument]


class Project(Model):
    rootIdentity: str
    projectId: str
    name: str
    rootUri: str
    contextId: str
    status: Literal["available", "unavailable", "replaced", "reference"]


class OpenProjectInput(Model):
    directory: str
    name: str | None = None
    """Shown instead of the folder's name; opening the folder again updates it."""


class Empty(Model):
    pass


class ProjectList(Model):
    projects: list[Project]
    nextCursor: str | None = None


class ListProjectsInput(Model):
    cursor: str | None = None
    limit: int | None = Field(None, ge=1, le=200)


class ReadTreeInput(Model):
    projectId: str
    path: str = ""
    cursor: str | None = None
    limit: int = Field(50, ge=1, le=200)


class TreeEntry(Model):
    path: str
    uri: str
    kind: Literal["file", "directory", "missing", "unavailable"]
    size: int


class Tree(Model):
    entries: list[TreeEntry]
    nextCursor: str | None


class InstructionsInput(Model):
    projectId: str
    startLine: int = Field(1, ge=1)
    lineCount: int = Field(200, ge=1, le=2000)
    expectedDigest: str | None = None
    startChar: int | None = Field(None, ge=0)


class Instructions(Model):
    projectId: str
    uri: str
    exists: bool
    digest: str | None
    content: str
    startLine: int
    nextLine: int | None
    startChar: int | None = None
    nextChar: int | None = None


class CreateModuleInput(Model):
    name: str = Field(min_length=1, max_length=80)
    moduleId: str | None = Field(None, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    """Defaults to the name in lowercase with underscores."""


class CreatedModule(Model):
    moduleId: str
    projectId: str
    name: str
    rootUri: str
    directory: str


class DocumentChanged(Model):
    documentId: str
    version: int
    kind: Literal["opened", "changed", "saved", "resolved"]
    origin: str
    """Who changed it: ``user``, ``ai``, ``code``, a module's id, ``disk`` or ``source`` (its provider)."""
    externalState: Literal["present", "deleted"]


# ── extension points ─────────────────────────────────────────────────────────────────────────────────────────────────

class AppliesTo(Model):
    sourceKinds: list[Literal["file", "contribution"]] = []
    filePatterns: list[str] = []
    providerIds: list[str] = []
    projectKinds: list[Literal["local", "reference"]] = []


class Documentation(Model):
    name: str
    source: Source


class DevelopmentSupport(Model):
    """A module's help for documents it knows: guides and examples listed in Developer's Help, and (``language``) the
    Python a file is analysed against."""
    name: str
    capabilities: list[Literal["language"]] = []
    appliesTo: AppliesTo = AppliesTo()
    documentation: list[Documentation] = []


class DocumentSourceDescriptor(Model):
    """Documents a module serves itself (read, and written unless ``readOnly``)."""
    name: str
    readOnly: bool = False
    watch: bool = False


class ReadCheckpointInput(Model):
    checkpoint: CheckpointReference
    sourceKey: str | None = None
    """Omit to list the captured documents. Select a sourceKey to read its captured text."""
    cursor: str | None = None
    startChar: int = Field(0, ge=0)

    @model_validator(mode="after")
    def page_selection(self):
        if (self.sourceKey is None and self.startChar) or (self.sourceKey is not None and self.cursor is not None):
            raise ValueError("Use cursor for the list, or startChar for one captured document")
        return self


class CheckpointList(Checkpoint):
    nextCursor: str | None


class TextPage(Model):
    content: str
    startChar: int
    nextChar: int | None


class CheckpointText(TextPage):
    baseContent: str
    sourceKey: str


class VersionsInput(Model):
    documentId: str
    startChar: int = Field(0, ge=0)


class VersionsPage(TextPage):
    baseContent: str
    externalContent: str
    version: int
    sourceVersion: Version
    baseVersion: Version


class ResolveInput(Model):
    documentId: str
    mode: Literal["mine", "external"]
    expectedVersion: int
    expectedSourceVersion: Version


class HelpItem(Documentation):
    support: str


class HelpPage(Model):
    entries: list[HelpItem]
    nextCursor: str | None


class FileRevisionInput(Model):
    projectId: str
    path: str
    revisionId: str | None = None
    startChar: int = Field(0, ge=0)

    @model_validator(mode="after")
    def selected_revision(self):
        if self.startChar and self.revisionId is None:
            raise ValueError("Select a revisionId to page its content")
        return self


class Revision(Model):
    revisionId: str
    createdAt: str
    reason: str
    size: int


class RevisionList(Model):
    sourceId: str
    path: str
    revisions: list[Revision]
    warnings: list[str]


class RevisionText(TextPage):
    version: str


class RestoreRevisionInput(Model):
    projectId: str
    path: str
    revisionId: str
    expectedVersion: str | None
    """Current file digest on disk, or null if the file is absent."""


class RevisionSaveResult(Model):
    status: Literal["saved", "conflict"]
    version: str | None
    message: str | None = None
    historyWarning: str | None = None


class ProjectId(Model):
    projectId: str


class ProjectModule(Model):
    id: str
    name: str
    directory: str
    installed: bool
    """Whether the source directory is in TALYX's modules folder."""


class RuntimeState(Model):
    enabled: bool
    state: str
    problem: str | None
    source: str


class ProjectModuleState(Model):
    module: ProjectModule | None
    runtime: RuntimeState | None


class RemoveProjectInput(ProjectId):
    mode: Literal["forget", "trash"]
    expectedRootUri: str
    rootIdentity: str


class RemovedProject(ProjectId):
    mode: Literal["forget", "trash"]
    removed: bool
