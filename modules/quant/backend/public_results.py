"""Results shared by Quant's public handlers. Native report values keep their declared units."""

from typing import Literal, NotRequired, TypedDict

from pydantic import JsonValue

from vocabulary.python.types import FileRef, Summary, TableFile, TablePage

Fields = dict[str, JsonValue]


class Settings(TypedDict):
    data: Fields
    params: Fields
    run: Fields


Change = TypedDict("Change", {"path": str, "from": JsonValue, "to": JsonValue})


class ObjectSummary(TypedDict):
    mode: str | None
    objectId: str
    name: str
    kind: str
    version: int
    folder: str | None
    workspaceId: str | None
    settings: Settings
    effective: Fields
    codeErrors: list[Fields]
    formula: NotRequired[str | None]
    libraryInputs: NotRequired[dict[str, str]]
    representative: NotRequired[Fields | None]
    lastRun: NotRequired[Fields | None]
    entryUri: NotRequired[str | None]
    changed: NotRequired[list[Change]]
    notTaken: NotRequired[list[str]]
    restore: NotRequired[Fields]


class ObjectOptions(TypedDict):
    objectId: str
    options: Fields


class CodePage(TypedDict):
    entryUri: str
    files: list[Fields]
    nextCursor: str | None
    total: int


class Page(TypedDict):
    items: list[Fields]
    total: int
    nextCursor: str | None


class Objects(Page):
    folders: list[Fields]
    counts: list[Fields]


class RunReceipt(TypedDict):
    runId: str
    state: str


class Tested(TypedDict):
    start: str
    end: str
    bars: int
    timeframe: str


class SymbolCoverage(TypedDict):
    set: int
    used: int
    dropped: list[dict[str, str]]
    droppedTotal: int


class RunSummary(RunReceipt):
    objectId: str
    objectName: str
    number: int
    kind: str
    runKind: str
    summary: Summary | None
    tested: Tested | None
    requested: Fields
    heldOutFrom: str | None
    numbers: dict[str, float | None]
    symbols: SymbolCoverage
    createdAt: str
    endedAt: str | None
    progress: Fields | None
    failure: Fields | None
    settings: Settings
    checks: list[Fields]
    changed: list[Change]
    parts: list[str]
    tables: list[str]
    batch: NotRequired[Fields]
    compare: NotRequired[Fields]


class RunSection(TypedDict):
    runId: str
    part: str
    data: Fields
    compare: NotRequired[Fields]
    nextCursor: NotRequired[str | None]


class LayerInstrument(TypedDict):
    instrumentId: str
    symbol: str
    key: str


class LayerContributor(LayerInstrument):
    sector: str
    pnl: float
    trades: int
    weight: float | None


class LayerSignal(LayerInstrument):
    latest: float | None


class LayerAttribution(TypedDict):
    net: float
    remainder: float
    currency: str


LayerWindow = TypedDict("LayerWindow", {"from": str, "to": str})


class RunChartLayer(TypedDict):
    layer: FileRef | Fields
    """Chart's ResearchLayer, inline or as its JSON FileRef."""
    instrumentId: str
    label: str
    timeframe: str
    adjustment: str
    window: LayerWindow
    truncated: bool
    buys: int
    sells: int
    trades: NotRequired[int]
    openAtEnd: NotRequired[int]
    instruments: NotRequired[list[LayerContributor | LayerSignal | LayerInstrument]]
    """Instrument picker rows when includeInstruments is requested."""
    attribution: NotRequired[LayerAttribution]


class RunChartSection(TypedDict):
    runId: str
    part: Literal["chartLayer"]
    data: RunChartLayer


class ResearchList(TypedDict):
    listId: str
    name: str
    symbols: list[str]
    changed: NotRequired[list[Change]]


class ResearchBars(TypedDict):
    bars: FileRef
    info: FileRef
    coverage: Fields


class Environments(TypedDict):
    deep: Fields
    kernels: list[Fields]


class LibraryCatalog(Page):
    archivable: list[JsonValue]
    needsAttention: int
    nextRound: JsonValue
    production: Fields | None


class LibraryMetadata(TypedDict):
    versionId: str
    version: int
    state: str
    publishedAt: str
    producedByRun: str | None


class LibraryFile(TableFile):
    meta: LibraryMetadata


class LibraryPage(TablePage):
    meta: LibraryMetadata
