"""Wire results of Quant public actions. Native reports and project-defined settings keep their own named fields."""
from typing import NotRequired, TypedDict

from pydantic import JsonValue

Fields = dict[str, JsonValue]


class ObjectDeleted(TypedDict):
    objectId: str
    deleted: bool


class Folder(TypedDict):
    kind: str
    name: str


class FolderDeleted(TypedDict):
    kind: str
    name: str
    deleted: bool


class RunAnnotation(TypedDict):
    id: str
    state: str
    starred: bool
    note: str


class FormulaCheck(TypedDict):
    error: Fields | None


class ModelCheck(TypedDict):
    ok: bool
    checks: list[Fields]
    blocked: bool
    seconds: float
    failure: NotRequired[Fields]
    axis: NotRequired[Fields]
    inputs: NotRequired[list[Fields]]
    features: NotRequired[int]
    estimates: NotRequired[list[Fields]]
    windows: NotRequired[int]
    rowsPerWindow: NotRequired[int]
    bars: NotRequired[int]
    names: NotRequired[int]
    label: NotRequired[Fields]
    engineParams: NotRequired[Fields | None]
    batchDefault: NotRequired[JsonValue]
    batch: NotRequired[Fields]
    pins: NotRequired[list[Fields]]
    dataKey: NotRequired[str]
    frozenData: NotRequired[Fields]
    blockedBy: NotRequired[str | None]


class InspectedInput(TypedDict):
    ok: bool
    name: str
    input: str
    features: list[str]
    before: list[int]
    after: list[int]
    edges: list[float]
    afterEdges: list[float]
    missing: list[list[float]]
    stats: list[Fields]
    note: str


class PublicationPlan(TypedDict):
    runId: str
    name: str
    version: int
    alsoPublished: NotRequired[list[JsonValue] | None]
    autoUpdate: NotRequired[bool | None]
    blocked: NotRequired[Fields | None]
    checks: NotRequired[list[JsonValue] | None]
    checksToReview: NotRequired[list[JsonValue] | None]
    compare: NotRequired[list[JsonValue] | None]
    firstPeriod: NotRequired[Fields | None]
    futureDataCheck: NotRequired[Fields | list[JsonValue] | None]
    measures: NotRequired[Fields | None]
    number: NotRequired[int | None]
    paramsDiff: NotRequired[list[JsonValue] | None]
    pinned: NotRequired[list[JsonValue] | None]
    previous: NotRequired[Fields | None]
    produces: NotRequired[str | None]
    runNumber: NotRequired[int | None]
    schedule: NotRequired[Fields | None]
    users: NotRequired[list[JsonValue] | None]


class Concurrency(TypedDict):
    concurrency: int
    cpus: int
    memoryUsed: float
    memoryTotal: float
    budget: float
    running: int
    waiting: int


class LibraryItem(TypedDict):
    compare: NotRequired[Fields]
    versionId: str | None
    versions: list[Fields]
    failureCount: NotRequired[int | None]
    failures: NotRequired[list[JsonValue] | None]
    feeds: NotRequired[list[JsonValue] | None]
    figures: NotRequired[Fields | None]
    files: NotRequired[Fields | list[JsonValue] | None]
    inputs: NotRequired[list[JsonValue] | None]
    issue: NotRequired[Fields | None]
    item: NotRequired[Fields | None]
    nextDue: NotRequired[Fields | None]
    oneOff: NotRequired[bool | None]
    origin: NotRequired[Fields | None]
    overview: NotRequired[Fields | None]
    periods: NotRequired[Fields | list[JsonValue] | None]
    read: NotRequired[str | None]
    summary: NotRequired[Fields | None]
    users: NotRequired[list[JsonValue] | None]


class PublishedValues(TypedDict):
    itemId: str
    versionId: str
    version: int


class DeletedList(TypedDict):
    listId: str
    deleted: bool


class CellOutput(TypedDict):
    objectId: str
    cellId: str
    codeIdentity: str
    status: str
    executionCount: int | None
    outputs: list[Fields]


class CellRef(TypedDict):
    cellId: str
    type: str


class CellWritten(TypedDict):
    objectId: str
    cellId: str
    version: int | str
    cells: list[CellRef]


class StopVersion(TypedDict):
    state: NotRequired[str]
    autoUpdate: NotRequired[bool]
    versionId: str
    consequence: NotRequired[str | None]
    lastDatetime: NotRequired[int | None]
    producing: NotRequired[list[JsonValue] | None]
    stopped: NotRequired[bool | None]
    users: NotRequired[list[JsonValue] | None]
    version: NotRequired[int | None]


class ArchivedVersion(TypedDict):
    versionId: str
    rows: int
    archived: bool


class RetryVersion(TypedDict):
    versionId: str
    started: bool
    running: bool


class CancelUpdate(TypedDict):
    versionId: str
    cancelled: bool


class Attention(TypedDict):
    total: int
    nextCursor: str | None
    items: list[Fields]
    watchInLibrary: int


class Queue(TypedDict):
    total: int
    nextCursor: str | None
    jobs: list[Fields]
    production: Fields


HeldOutPlan = TypedDict("HeldOutPlan", {
    "from": int,
    "to": int,
    "windows": int,
    "minutes": float | None,
    "uses": list[Fields],
})


class DeepEnvironment(TypedDict):
    installed: bool
    variant: str | None
    offer: Fields
    installing: Fields | None
    devices: list[Fields]
    newCard: JsonValue
    failure: JsonValue


class AllocationTrial(TypedDict):
    computedAt: int
    fullRun: bool
    wall: float


class SupersededTrial(TypedDict):
    superseded: bool


class Shock(TypedDict):
    scenario: str
    kind: str
    portfolio: NotRequired[float | None]
    worst: NotRequired[str | None]


class ReviewedPeriod(TypedDict):
    versionId: str
    period: JsonValue
    review: JsonValue


class KernelSelected(TypedDict):
    objectId: str
    environment: str


class NotebookVariables(TypedDict):
    variables: list[JsonValue]
    loaded: JsonValue


class Materials(TypedDict):
    kind: str
    object: str
    runId: str
    number: int | None
    state: str
    settings: Fields
    metrics: Fields
    dataTo: str | None
    failure: JsonValue
    paths: Fields
    data: Fields | None


class NotebookFromRun(TypedDict):
    notebookId: str
    created: bool


class NotebookBacktest(TypedDict):
    runId: str
    number: int | None


class PublishedModel(TypedDict):
    name: str
    versionId: str
    version: int
    trainingCutoff: JsonValue
    model: Fields
