"""Market's public types. The contract is generated from these; edit here, then run the generator."""

from __future__ import annotations

from datetime import date as date_aliased
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, RootModel
from talyx_sdk.columns import text_column
from talyx_sdk.declare import Model
from typing_extensions import TypeAliasType


class TaskState(RootModel[Literal["running", "completed", "failed", "cancelled", "interrupted"]]):
    root: Literal["running", "completed", "failed", "cancelled", "interrupted"]


Decimal = TypeAliasType("Decimal", Annotated[str, Field(description='Exact decimal, no exponent or binary floating point conversion', pattern="^-?(0|[1-9][0-9]*)(\\.[0-9]+)?$")])


TimestampNs = TypeAliasType("TimestampNs", Annotated[str, Field(description='Unix epoch nanoseconds as a decimal string', pattern="^(0|[1-9][0-9]*)$")])


class Instrument(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    instrumentId: Annotated[str, Field(pattern="^(vi1_[a-f0-9]{64}|vi2_[a-f0-9]{32})$")]
    identityVersion: Literal[1, 2]
    """
    1: venue contract; id = hash of the contract fields. The hash material keeps the key sourceId and fills both sourceId and venueId with the venue id, so existing crypto ids do not change. venueId: CCXT exchange id for crypto; ISO 10383 MIC of the listing exchange for futures and their options (XCME, XCBT, XNYM, XEUR, ...); fixed codes OPRA for US listed equity options and IDEALPRO for IB spot FX. The same contract from IB and Databento is one instrument. 2: listing assigned by Market (stocks, ETFs, funds, indices, warrants, bonds, source composites such as Yahoo FX or yields, CoinGecko coins); survives renames and code changes. A derived value series with scope single lives on a version-1 instrument with venueId = the data source id, symbol = the key, marketType unknown, assetClass unknown, calendarId 24_7.
    """
    venueId: Annotated[str, Field(max_length=64, min_length=1)]
    """
    Version 1: exchange id (binance). Version 2: listing market (US, CN, HK, FX, COIN).
    """
    symbol: Annotated[str, Field(max_length=128, min_length=1)]
    """
    Version 1: venue contract symbol. Version 2: the current code (AAPL, BRK.B, 600519.SH, 00700.HK).
    """
    displaySymbol: Annotated[str, Field(max_length=128, min_length=1)]
    """
    How the instrument is shown: TradingView-style (BTCUSDT, BTCUSDT.P, BTCUSDTZ2026; listings keep their code). Presentation only; identity is instrumentId.
    """
    marketType: Literal["spot", "swap", "future", "option", "unknown"]
    assetClass: Literal[
        "crypto",
        "equity",
        "tokenized_equity",
        "fund",
        "index",
        "fixed_income",
        "warrant",
        "fx",
        "precious_metal",
        "commodity",
        "rate",
        "unknown",
    ]
    base: str | None = None
    quote: str | None = None
    settle: str | None = None
    contractOrientation: Literal["none", "linear", "inverse", "quanto", "unknown"]
    contractSize: Decimal | None = None
    expiry: TimestampNs | None = None
    strike: Decimal | None = None
    optionSide: Literal["call", "put"] | None = None
    underlyingSymbol: str | None = None
    retiredAt: TimestampNs | None = None
    """
    Version 1 only: UTC day of the last bar with real trades of an earlier contract whose symbol the venue later reused (Binance spot LUNA/USDT until 2022-05-13), not the last day in the venue's files: zero-volume bars with open = high = low = close after it are recorded as source_has_none, never as prices. Assigned once, stored in Market's identity table and never recomputed. Null for the current contract; hashed only when set, so current ids do not change.
    """
    calendarId: Annotated[str, Field(max_length=64, min_length=1)]
    adjustmentEligible: bool
    adapterId: Annotated[str | None, Field(max_length=64)] = None
    nativeId: Annotated[str | None, Field(max_length=256)] = None
    securityType: Annotated[str | None, Field(max_length=64)] = None
    shareClass: Annotated[str | None, Field(max_length=64)] = None
    name: Annotated[str | None, Field(max_length=512)] = None
    venueName: Annotated[str | None, Field(max_length=64)] = None
    """
    The exchange's display name (Binance, OKX, NASDAQ, HKEX). Presentation only.
    """
    countryCode: Annotated[str | None, Field(max_length=16)] = None
    currency: Annotated[str | None, Field(max_length=16)] = None
    mic: Annotated[str | None, Field(max_length=16)] = None
    """
    Version 2: current primary listing exchange; descriptive.
    """
    listedFrom: TimestampNs | None = None
    delistedAt: TimestampNs | None = None
    active: bool | None = None


Timeframe = TypeAliasType("Timeframe", Annotated[str, Field(description='s seconds, m minutes, h hours, d days, w weeks, M or mo calendar months, y calendar years; aligned UTC unless instrument calendar specifies otherwise', pattern="^[1-9][0-9]*(s|m|h|d|w|M|mo|y)$")])


class TimeRange(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    start: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    Unix epoch nanoseconds as a decimal string
    """
    end: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    Unix epoch nanoseconds as a decimal string
    """
    unit: Literal["ns"]
    bounds: Literal["[)"]


class Bar(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    seriesId: Annotated[str, Field(max_length=256, min_length=1)]
    time: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    Unix epoch nanoseconds as a decimal string
    """
    timeUnit: Literal["ns"]
    open: Annotated[str, Field(pattern="^-?(0|[1-9][0-9]*)(\\.[0-9]+)?$")]
    """
    Exact decimal, no exponent or binary floating point conversion
    """
    high: Annotated[str, Field(pattern="^-?(0|[1-9][0-9]*)(\\.[0-9]+)?$")]
    """
    Exact decimal, no exponent or binary floating point conversion
    """
    low: Annotated[str, Field(pattern="^-?(0|[1-9][0-9]*)(\\.[0-9]+)?$")]
    """
    Exact decimal, no exponent or binary floating point conversion
    """
    close: Annotated[str, Field(pattern="^-?(0|[1-9][0-9]*)(\\.[0-9]+)?$")]
    """
    Exact decimal, no exponent or binary floating point conversion
    """
    volume: Annotated[str, Field(pattern="^-?(0|[1-9][0-9]*)(\\.[0-9]+)?$")]
    """
    Exact decimal, no exponent or binary floating point conversion
    """
    closed: bool


class BarColumns(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    # Market hands these over already encoded (talyx_sdk.columns); the contract says list[...].
    time: text_column(TimestampNs)
    """
    Period start; for 1d and longer on an exchange calendar, the trading date's local 00:00 as UTC.
    """
    closeTime: text_column(TimestampNs)
    open: text_column(Decimal)
    high: text_column(Decimal)
    low: text_column(Decimal)
    close: text_column(Decimal)
    volume: text_column(Decimal, nullable=True)


class Marker(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    id: Annotated[str, Field(max_length=256, min_length=1)]
    seriesId: Annotated[str, Field(max_length=256, min_length=1)]
    eventTime: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    Unix epoch nanoseconds as a decimal string
    """
    knownAt: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    Unix epoch nanoseconds as a decimal string
    """
    timeUnit: Literal["ns"]
    value: Annotated[str | None, Field(pattern="^-?(0|[1-9][0-9]*)(\\.[0-9]+)?$")] = None
    """
    Exact decimal, no exponent or binary floating point conversion
    """
    text: str | None = None
    kind: Annotated[str, Field(max_length=256, min_length=1)]


class DrawingPoint(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    time: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    Unix epoch nanoseconds as a decimal string
    """
    value: Annotated[str, Field(pattern="^-?(0|[1-9][0-9]*)(\\.[0-9]+)?$")]
    """
    Exact decimal, no exponent or binary floating point conversion
    """


class Drawing(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    id: Annotated[str, Field(max_length=256, min_length=1)]
    seriesId: Annotated[str, Field(max_length=256, min_length=1)]
    kind: Literal["trendline", "horizontal", "vertical", "rectangle", "polyline"]
    timeUnit: Literal["ns"]
    points: Annotated[list[DrawingPoint], Field(max_length=10000, min_length=1)]


MarketCode = TypeAliasType("MarketCode", Annotated[str, Field(description='Listing market of an assigned instrument: US, CN, HK, FX, COIN, ... (venueId of identityVersion 2).', pattern="^[A-Z]{2,8}$")])


class SourceRef(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    sourceId: str
    name: str


class Problem(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    code: Literal[
        "not_found",
        "no_data",
        "unsupported",
        "source_unavailable",
        "network_unreachable",
        "rate_limited",
        "unauthorized",
        "forbidden",
        "fill_too_large",
        "disk_full",
        "cancelled",
    ]
    message: Annotated[str, Field(max_length=300)]
    sourceId: str | None
    status: int | None
    """
    Source HTTP status when there was one (e.g. 451).
    """
    retryAt: TimestampNs | None


class Gap(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    start: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    Unix epoch nanoseconds as a decimal string
    """
    end: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    Unix epoch nanoseconds as a decimal string
    """
    reason: Literal[
        "source_has_none",
        "before_listing",
        "after_delisting",
        "halted",
        "not_fetched",
        "unavailable",
        "unknown",
    ]
    retryAt: TimestampNs | None = None
    """
    unavailable only: when Market asks the source again (null: no layer of the source reaches this span, e.g. between its last file and its API's depth).
    """
    problem: Problem | None = None
    """
    unavailable only: why the source could not give it (its file is not out yet, it refused the request).
    """


class Fill(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    state: Literal["not_needed", "skipped", "filling", "done", "partial", "failed"]
    """
    filling: only on a session's replace: the rows Market holds are here, sent without waiting for the source, and Market is filling what the window misses; when the newest bars were missing a replace with them follows as soon as they are in (still filling when more is missing), and exactly one replace follows with the whole window and the fill's outcome (done, partial or failed with the problem).
    """
    problem: Problem | None


class SeriesPage(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    instrumentId: str
    """
    Survivor id when the requested instrument was merged.
    """
    seriesId: str
    columns: BarColumns | dict[str, list[Any]]
    """
    At most 10,000 rows, about 1 MB (10,000 one-minute bars measured 0.97 MB), within the 2 MB bridge and worker frame.
    """
    forming: dict[str, Any] | None
    """
    The current forming bar (including an open aggregate built from stored bars), or the newest live observation.
    """
    range: TimeRange | None
    gaps: Annotated[list[Gap], Field(max_length=500)]
    hasMoreBefore: bool
    nextBefore: TimestampNs | None
    source: SourceRef | None
    fill: Fill
    revision: Annotated[int, Field(ge=0)]
    """
    Changes whenever stored rows or adjustment factors of this series change.
    """


class SessionItem1(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: Literal["replace"]
    page: SeriesPage


class SessionItem2(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: Literal["append"]
    columns: BarColumns | dict[str, list[Any]]
    revision: Annotated[int, Field(ge=0)]


class SessionItem3(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: Literal["update"]
    row: dict[str, Any]
    receivedAt: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    Unix epoch nanoseconds as a decimal string
    """


class SessionItem4(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: Literal["status"]
    state: Literal[
        "connecting", "live", "delayed", "history_only", "market_closed", "recovering", "failed"
    ]
    problem: Problem | None
    since: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    Unix epoch nanoseconds as a decimal string
    """


class RepairedItem(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    time: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    Unix epoch nanoseconds as a decimal string
    """
    open: str
    high: str
    low: str
    close: str


class Local(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    range: TimeRange
    rows: Annotated[int, Field(ge=0)]
    bytes: Annotated[int, Field(ge=0)]
    gaps: Annotated[list[Gap], Field(max_length=200)]
    repaired: Annotated[list[RepairedItem], Field(max_length=200)]
    """
    Bars the source gave with high and low not containing open and close (still so when asked again): stored with open and close as given and high and low widened just enough; these are the source's original values.
    """


class Available(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    periods: list[Timeframe]
    earliest: TimestampNs | None
    live: Literal["realtime", "delayed", "none"]


class CoverageItem(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    capability: str
    timeframe: Timeframe | None
    source: SourceRef | None
    local: Local | None
    available: Available
    status: Literal["current", "behind", "updating", "failed", "delisted", "ended", "not_stored"]
    problem: Problem | None
    revision: Annotated[int, Field(ge=0)]
    publishDelay: str | None
    """
    ISO-8601 duration from a bar's close until the source publishes it as final; Market fetches and stores a bar only past it (publishedThrough).
    """
    publishedThrough: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    The series' publish cutoff now: bars that closed at or before this instant are final at the source (now minus publishDelay). Later bars are not fetched, stored or exported yet; the last such bar is the series' latest close.
    """


class Quote(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    price: Decimal | None
    last: Decimal | None
    bid: Decimal | None
    ask: Decimal | None
    mark: Decimal | None
    index: Decimal | None
    change: Decimal | None
    changePercent: Decimal | None
    """
    Percent: 1.25 means 1.25%.
    """
    changeBasis: Literal["previous_session_close", "rolling_24h"] | None
    asOf: TimestampNs | None
    fieldAsOf: dict[str, TimestampNs | None]
    delayed: bool


class FormerCode(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    code: str
    until: TimestampNs | None


class CatalogState(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    venue: str
    """
    Market code or crypto venue.
    """
    venueName: str
    """
    The venue or market as shown (OKX, China), as an instrument's venueName.
    """
    family: Literal["listings", "spot", "swap", "future", "option"]
    state: Literal["ready", "loading", "failed"]
    problem: Problem | None


class Code(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    code: str
    name: str | None
    from_: Annotated[TimestampNs | None, Field(alias="from")]
    until: TimestampNs | None


class Conflict(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    code: str
    from_: Annotated[TimestampNs | None, Field(alias="from")]
    until: TimestampNs | None
    otherInstrumentId: str
    claimedBy: str
    """
    Source whose code history says so (alpaca).
    """
    reason: Annotated[str, Field(max_length=300)]


class InstrumentDetail(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    instrument: Instrument
    codes: list[Code] | None = None
    externalIds: dict[str, str] | None = None
    """
    Permanent ids Market knows, e.g. compositeFigi, cik, permaticker.
    """
    conflicts: Annotated[list[Conflict] | None, Field(max_length=50)] = None
    """
    Code periods a lower-ranked source assigns differently from the catalog; not merged until a higher source or an import decides.
    """


class DataKindRef(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: str
    """
    bars (any timeframe; Market picks the daily or intraday source by timeframe), a crypto capability (funding_rate, open_interest, long_short_account_ratio, top_trader_account_ratio, top_trader_position_ratio, taker_volume, option_metrics), a research kind, or professional.
    """
    timeframe: Annotated[str | None, Field(pattern="^[1-9][0-9]*(s|m|h|d|w|M|mo|y)$")] = None
    """
    s seconds, m minutes, h hours, d days, w weeks, M or mo calendar months, y calendar years; aligned UTC unless instrument calendar specifies otherwise
    """
    priceBasis: Literal["last", "mark", "index"] | None = None
    request: dict[str, Any] | None = None
    """
    professional only: the source-specific acquisition request (IB contract fields, Databento symbols/schema, option chain underlying/expiry).
    """


class Target1(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    instrument: Instrument


class Target2(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    symbol: Annotated[str, Field(max_length=128, min_length=1)]


Index = TypeAliasType("Index", Annotated[str, Field(max_length=64, min_length=1)])


class Target3(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    index: Instrument | Index
    membersFrom: Annotated[str | None, Field(pattern="^(0|[1-9][0-9]*)$")] = None
    """
    Unix epoch nanoseconds as a decimal string
    """
    membersUntil: Annotated[str | None, Field(pattern="^(0|[1-9][0-9]*)$")] = None
    """
    Unix epoch nanoseconds as a decimal string
    """


class Failure(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    target: Annotated[str, Field(max_length=64)]
    instrumentId: str | None
    kind: str
    problem: Problem


FailureCountsAdditionalProperty = TypeAliasType(
    "FailureCountsAdditionalProperty", Annotated[int, Field(ge=0)]
)


class FetchResult(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    jobId: str
    instruments: Annotated[int, Field(ge=0)]
    completed: Annotated[int, Field(ge=0)]
    rows: Annotated[int, Field(ge=0)]
    bytes: Annotated[int, Field(ge=0)]
    failures: Annotated[list[Failure], Field(max_length=20)]
    failureCount: Annotated[int, Field(ge=0)]
    unmappedMembers: Annotated[int, Field(ge=0)]
    failureCounts: dict[str, FailureCountsAdditionalProperty]
    """
    Failures by error code.
    """
    state: Literal["completed", "interrupted", "cancelled"] | None = None


class ExportSelection2(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    research: Literal[
        "actions",
        "factors",
        "members",
        "classification",
        "shares",
        "financials",
        "earnings_dates",
        "market_cap",
        "categories",
    ]
    instruments: Annotated[list[Instrument] | None, Field(max_length=5000)] = None
    index: Instrument | None = None


class ProfessionalMaterial(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    materialId: str
    version: str


class ExportSelection3(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    professionalMaterial: ProfessionalMaterial


class Destination(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    folder: Annotated[str, Field(max_length=1024, min_length=1)]


class Artifact(Model):
    """A file handed over by reference (``FileRef``): written once, named uniquely, kept 24 hours by Market.
    Hard-link it into your own directory to keep it longer."""

    model_config = ConfigDict(
        extra="forbid",
    )
    path: str
    sha256: Annotated[str, Field(pattern="^[0-9a-f]{64}$")]
    bytes: Annotated[int, Field(ge=0)]


class File(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    content: str
    """
    ohlcv:<timeframe>:<adjustment> (ohlcv:<timeframe>:<priceBasis>:<adjustment> for mark, index or premium prices), <capability>:<timeframe>,
    research:<kind> or professional:<materialId>@<version>; an imported series adds @<importId>. Two series of one instrument never share a file.
    """
    series: list[str]
    """
    The seriesId of each selected series whose rows are in this file (none for research rows and professional materials).
    """
    artifact: Artifact | None
    rows: Annotated[int, Field(ge=0)]
    instruments: Annotated[int, Field(ge=0)]
    columns: list[str]
    range: TimeRange | None
    sources: list[SourceRef]
    path: str | None
    """
    Set when the export went to a destination folder.
    """


class Gap1(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    instrumentId: str
    seriesId: str | None
    gap: Gap


class Failure1(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    instrumentId: str
    seriesId: str
    """
    The series left out; the instrument's other series are still written.
    """
    problem: Problem


class ExportResult(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    files: list[File]
    gaps: Annotated[list[Gap1], Field(max_length=2000)]
    failures: Annotated[list[Failure1], Field(max_length=200)]


class ImportSpec(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["bars"] | None = None
    """
    Price bars, the only kind Market imports.
    """
    name: Annotated[str | None, Field(max_length=128)] = None
    market: str | None = None
    """
    Market code or crypto venue of the codes in the file.
    """
    codeColumn: str | None = None
    symbol: str | None = None
    """
    When the file holds one instrument.
    """
    timeframe: Annotated[str | None, Field(pattern="^[1-9][0-9]*(s|m|h|d|w|M|mo|y)$")] = None
    """
    s seconds, m minutes, h hours, d days, w weeks, M or mo calendar months, y calendar years; aligned UTC unless instrument calendar specifies otherwise
    """
    timezone: str | None = None
    """
    IANA zone of date/time columns without offset.
    """
    columns: dict[str, str] | None = None
    """
    Market column name (time, open, high, low, close, volume) to file column name.
    """
    fileNameCode: Annotated[str | None, Field(max_length=256)] = None
    """
    Regular expression with one capture group that takes the code from each file name when the files have no code column (^([A-Z.]+)_ takes AAPL from AAPL_1min_2024.csv).
    """


UnmatchedItem = TypeAliasType("UnmatchedItem", Annotated[str, Field(max_length=32)])


class RejectedRow(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    line: int
    reason: Annotated[str, Field(max_length=200)]


class WriteDataResult(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    importId: str | None
    rows: Annotated[int, Field(ge=0)]
    instruments: Annotated[int, Field(ge=0)]
    matched: Annotated[int, Field(ge=0)]
    unmatched: Annotated[list[UnmatchedItem], Field(max_length=50)]
    rejectedRows: Annotated[list[RejectedRow], Field(max_length=20)]
    deletedBytes: Annotated[int, Field(ge=0)]
    rejectedCount: Annotated[int, Field(ge=0)]
    unmatchedCount: Annotated[int, Field(ge=0)]


class ReadSourceInput1(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    view: Literal["list"]
    cursor: str | None = None


class ReadSourceInput2(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    view: Literal["selection"]
    market: str | None = None


class ReadSourceInput3(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    view: Literal["details"]
    source: Annotated[str, Field(max_length=64, min_length=1)]


class Source(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    source: SourceRef
    state: Literal["working", "not_configured", "rate_limited", "failing", "unsubscribed", "off"]
    problem: Problem | None
    keyConfigured: bool | None
    paid: bool
    enabled: bool
    openTarget: str
    """
    objectId for the Market panel: source:<sourceId>.
    """


class SelectionItem(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    market: str
    kind: str
    source: SourceRef | None
    reason: Annotated[str, Field(max_length=200)]
    """
    Why this source ranks first, in words.
    """
    timeframes: Literal["daily", "intraday"] | None = None


class WouldUseItem(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    market: str
    kind: str
    reason: Annotated[str, Field(max_length=200)]


Add = TypeAliasType("Add", Annotated[str, Field(max_length=200)])


class FailedItem(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    check: Annotated[str, Field(max_length=64)]
    detail: Annotated[str, Field(max_length=300)]


class Test(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    source: str
    latencyMs: Annotated[int | None, Field(ge=0)]
    """
    Round trip of the source's sample request in milliseconds (median when several requests ran); null when the source was not reached.
    """
    unreachable: (
        Literal[
            "timeout",
            "http_error",
            "key_missing",
            "key_rejected",
            "local_program_off",
            "network_unreachable",
            "rate_limited",
        ]
        | None
    )
    """
    Why the sample request did not get through: timeout; http_error with httpStatus (451 and 403 are regional blocks); key_missing for a source that needs a key none is set for; key_rejected; local_program_off for IB Gateway or Futu OpenD not running; network_unreachable when no source answers; rate_limited.
    """
    httpStatus: int | None
    passed: Annotated[int, Field(ge=0)]
    failed: Annotated[list[FailedItem], Field(max_length=20)]


class WriteSourceResult(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    tests: Annotated[list[Test], Field(max_length=100)]
    changed: bool


class Setting(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    name: Annotated[str, Field(pattern="^[a-z][a-zA-Z0-9_]*$")]
    label: str
    type: Literal["string", "integer", "boolean", "choice"]
    choices: list[str] | None = None
    secret: bool
    required: bool


class SourceScope(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    markets: list[str] | None = None
    venues: list[str] | None = None
    assetClasses: list[str] | None = None
    marketTypes: list[str] | None = None


BasisRowsAdditionalProperty = TypeAliasType(
    "BasisRowsAdditionalProperty", Annotated[int, Field(ge=1)]
)


class Archive(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    unit: Literal["month", "day"]
    """
    One file holds one month or one day of this period.
    """
    from_: Annotated[date_aliased, Field(alias="from")]
    """
    First file's date in dayZone.
    """
    until: date_aliased | None
    """
    Date in dayZone the files stop at (exclusive); null while they continue.
    """
    publishDelay: str
    """
    ISO-8601 duration after the month or day ends before its file is out.
    """
    dayZone: Annotated[str, Field(pattern="^[+-][0-9]{2}:[0-9]{2}$")]
    """
    Where the source's files cut days and months (OKX: +08:00).
    """
    priceBases: Annotated[list[Literal["last", "mark", "index", "premium"]], Field(min_length=1)]


class Period(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    timeframe: Annotated[str, Field(pattern="^[1-9][0-9]*(s|m|h|d|w|M|mo|y)$")]
    """
    s seconds, m minutes, h hours, d days, w weeks, M or mo calendar months, y calendar years; aligned UTC unless instrument calendar specifies otherwise
    """
    depth: str
    """
    ISO-8601 duration back from now (P30D) or a fixed start date.
    """
    requestRows: Annotated[int | None, Field(ge=1)] = None
    """
    Rows one API request gives.
    """
    basisRows: dict[str, BasisRowsAdditionalProperty] | None = None
    """
    Rows one API request gives for a price basis that pages differently (OKX mark and index: 100).
    """
    archives: list[Archive] | None = None
    """
    Files the source keeps for this period. Market asks month files, then day files, then the API (depth and requestRows describe the API); history is then asked with archive naming one file.
    """


class Row(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    scope: SourceScope
    kind: str
    periods: list[Period] | None = None
    live: Literal["realtime", "delayed", "none"]
    liveLimit: int | None = None
    publishDelay: str | None = None
    """
    ISO-8601 duration after a period closes before it is published (Alpaca free: PT15M).
    """
    historyRole: Literal["bulk", "recent_only"] | None = "bulk"
    """
    recent_only: realtime, quotes and recent gap filling only (IB, Futu); never used for long history.
    """
    delisted: bool | None = False
    """
    Includes delisted instruments.
    """
    pointInTime: bool | None = False
    """
    Research kinds: publishes rows with their own publication times rather than only the current values.
    """
    livePeriods: list[Timeframe] | None = None
    """
    Base periods the live stream carries (default: every declared period). Market subscribes the finest one dividing the series period, so one instrument has one upstream whatever periods are open.
    """


class SourceCoverage(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    rows: list[Row]
    concurrency: Annotated[int, Field(ge=1)]
    requestsPerMinute: int | None
    plan: str | None
    """
    What the configured key gives, in words (Free, Basic, ...).
    """
    requestsPerDay: int | None
    subscriptionsPerConnection: int | None
    """
    Live subscriptions one upstream connection carries.
    """


class Candidate1(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: Literal["listing"]
    market: str
    code: str
    name: str | None = None
    assetClass: str
    securityType: str | None = None
    currency: str | None = None
    mic: str | None = None
    calendarId: str | None = None
    listedFrom: TimestampNs | None = None
    delistedAt: TimestampNs | None = None
    codeFrom: TimestampNs | None = None
    codeUntil: TimestampNs | None = None
    sourceKey: str | None = None
    """
    The source's own permanent id (permaticker, asset id, conId).
    """
    externalIds: dict[str, str] | None = None


class Candidate2(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: Literal["contract"]
    instrument: dict[str, Any]
    """
    vocab Instrument fields without instrumentId; Market computes the id.
    """


class Candidate3(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: Literal["derived"]
    key: str
    name: str
    scope: Literal["single", "per_symbol"]
    unit: str | None = None
    description: str | None = None


class Code1(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    code: str
    from_: Annotated[TimestampNs | None, Field(alias="from")]
    until: TimestampNs | None


class SourceTarget(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    instrument: Instrument
    codes: list[Code1]
    """
    Codes the listing had inside the requested range.
    """
    sourceKey: str | None
    key: str | None = None
    """
    derived_value only.
    """
    environment: Literal["production", "testnet", "demo"] | None = "production"


class Archive1(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    unit: Literal["month", "day"]
    start: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    Unix epoch nanoseconds as a decimal string
    """


class SourceHistoryInput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    sourceId: str
    target: SourceTarget
    kind: str
    timeframe: Timeframe | None
    priceBasis: Literal["last", "mark", "index"] | None = None
    start: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    Unix epoch nanoseconds as a decimal string
    """
    end: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    Unix epoch nanoseconds as a decimal string
    """
    archive: Archive1 | None = None
    """
    Answer from this one declared file (its month or day starting at start, in the archive's dayZone); absent: answer from the API.
    """


class SourceHistoryOutput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    columns: dict[str, list[Any]]
    next: TimestampNs | None
    """
    Continue from here inside the same window; null when the window is done.
    """
    emptyReason: Literal["before_listing", "after_delisting", "source_has_none", "missing"] | None
    """
    missing: the file asked for is not there (late, or never published); says nothing about the span, Market asks the next layer.
    """
    none: list[TimeRange] | None = None
    """
    Spans inside the answer the source confirms hold nothing (a venue's zero-trade filler rows, a hole in its own files).
    """


class SourceBulkInput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    sourceId: str
    kind: str
    timeframe: Timeframe | None
    scope: SourceScope | None = None
    codes: Annotated[list[str] | None, Field(max_length=10000)] = None
    start: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    Unix epoch nanoseconds as a decimal string
    """
    end: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    Unix epoch nanoseconds as a decimal string
    """


class SourceBulkOutput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    artifact: Artifact | None
    codeColumn: str
    next: TimestampNs | None


class SourceLiveParams(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    sourceId: str
    target: SourceTarget
    kind: Literal["bars", "trades", "quotes", "order_book", "observations"]
    timeframe: Timeframe | None = None
    depth: Annotated[int | None, Field(ge=1, le=500)] = None
    priceBasis: Literal["last", "mark", "index"] | None = "last"


class SourceLiveItem1(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: Literal["bar"]
    bar: dict[str, Any]
    closed: bool


class SourceLiveItem2(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: Literal["trade"]
    time: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    Unix epoch nanoseconds as a decimal string
    """
    price: Annotated[str, Field(pattern="^-?(0|[1-9][0-9]*)(\\.[0-9]+)?$")]
    """
    Exact decimal, no exponent or binary floating point conversion
    """
    size: Annotated[str, Field(pattern="^-?(0|[1-9][0-9]*)(\\.[0-9]+)?$")]
    """
    Exact decimal, no exponent or binary floating point conversion
    """
    side: Literal["buy", "sell"] | None = None
    id: str | None = None


class SourceLiveItem3(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: Literal["quote"]
    quote: dict[str, Any]


class SourceLiveItem4(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: Literal["book"]
    time: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    Unix epoch nanoseconds as a decimal string
    """
    bids: list[Any]
    asks: list[Any]


class SourceLiveItem5(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: Literal["observation"]
    row: dict[str, Any]


class SourceLiveItem6(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: Literal["status"]
    state: Literal["subscribed", "reconnecting", "degraded", "closed"]
    problem: Problem | None


class AsOf(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    quote: TimestampNs | None
    mark: TimestampNs | None
    greeks: TimestampNs | None
    openInterest: TimestampNs | None
    iv: TimestampNs | None


class OptionRow(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    instrument: Instrument
    bid: Decimal | None
    ask: Decimal | None
    mark: Decimal | None
    impliedVolatility: Decimal | None
    delta: Decimal | None
    gamma: Decimal | None
    theta: Decimal | None
    vega: Decimal | None
    openInterest: Decimal | None
    asOf: AsOf


AdapterId = TypeAliasType("AdapterId", Annotated[str, Field(max_length=256, min_length=1)])


InstrumentId = TypeAliasType("InstrumentId", Annotated[str, Field(max_length=256, min_length=1)])


class TradeSearchConstraint(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    revision: Annotated[int, Field(ge=0)]
    environment: Literal["production", "testnet", "demo"]
    venueId: Annotated[str, Field(max_length=256, min_length=1)]
    adapterIds: list[AdapterId]
    products: list[Literal["spot", "swap", "future", "option"]]
    instrumentIds: list[InstrumentId]


class TimePoint1(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    precision: Literal["date"]
    value: date_aliased
    timezone: str | None


class TimePoint2(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    precision: Literal["instant"]
    value: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    Unix epoch nanoseconds as a decimal string
    """


class Availability(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    publishedAt: TimePoint1 | TimePoint2 | None
    revisedAt: TimePoint1 | TimePoint2 | None
    revisionHistory: Literal["complete", "unknown"]
    reference: str | None


RequestedMember = TypeAliasType("RequestedMember", Annotated[str, Field(min_length=1)])


ReturnedMember = TypeAliasType("ReturnedMember", Annotated[str, Field(min_length=1)])


MissingMember = TypeAliasType("MissingMember", Annotated[str, Field(min_length=1)])


class TimeRange1(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    member: Annotated[str, Field(min_length=1)]
    start: TimePoint1 | TimePoint2 | None
    end: TimePoint1 | TimePoint2 | None


class Coverage(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    requestedMembers: list[RequestedMember]
    returnedMembers: list[ReturnedMember]
    missingMembers: list[MissingMember]
    completeness: Literal["complete", "partial", "unknown"]
    statement: Annotated[str, Field(min_length=1)]
    timeRanges: list[TimeRange1]


CouponRate = TypeAliasType("CouponRate", Annotated[str, Field(pattern="^-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?$")])


class ScheduleRules(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    start: TimePoint1 | TimePoint2 | None
    end: TimePoint1 | TimePoint2 | None
    tenor: str
    rule: str
    terminationConvention: str
    endOfMonth: bool | None


class BondTerms(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    unitFaceValue: Annotated[str, Field(pattern="^-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?$")]
    currency: Annotated[str, Field(min_length=1)]
    couponType: Literal["fixed", "floating", "zero"]
    couponRate: CouponRate | None
    floatingIndex: str | None
    paymentSchedule: list[TimePoint1 | TimePoint2 | None]
    resetSchedule: list[TimePoint1 | TimePoint2 | None]
    calendar: Annotated[str, Field(min_length=1)]
    dayCount: Annotated[str, Field(min_length=1)]
    businessDayConvention: Annotated[str, Field(min_length=1)]
    maturity: TimePoint1 | TimePoint2 | None
    issuer: str | None = None
    settlementDays: Annotated[int | None, Field(ge=0)] = None
    issueDate: TimePoint1 | TimePoint2 | None = None
    scheduleRules: ScheduleRules | None = None


class OptionTerms(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    underlying: dict[str, Any]
    strike: Annotated[str, Field(pattern="^-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?$")]
    currency: Annotated[str, Field(min_length=1)]
    style: Literal["european", "american", "bermudan"]
    settlement: Annotated[str, Field(min_length=1)]
    deliverable: Annotated[str, Field(min_length=1)]
    multiplier: Annotated[str, Field(pattern="^-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?$")]
    expiry: TimePoint1 | TimePoint2 | None
    optionType: Literal["call", "put"]
    payoffAtExpiry: bool | None = None


class FutureTerms(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    contractMonth: Annotated[str, Field(pattern="^[0-9]{4}-[0-9]{2}$")]
    multiplier: Annotated[str, Field(pattern="^-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?$")]
    lastTradingDate: TimePoint1 | TimePoint2 | None
    deliveryDate: TimePoint1 | TimePoint2 | None
    contractFamily: Annotated[str, Field(min_length=1)]
    settlement: Annotated[str, Field(min_length=1)]
    currency: Annotated[str, Field(min_length=1)]


class InstrumentTermRow1(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    instrument: dict[str, Any]
    effectiveFrom: TimePoint1 | TimePoint2 | None
    effectiveUntil: TimePoint1 | TimePoint2 | None
    termType: Literal["bond"]
    terms: BondTerms


class InstrumentTermRow2(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    instrument: dict[str, Any]
    effectiveFrom: TimePoint1 | TimePoint2 | None
    effectiveUntil: TimePoint1 | TimePoint2 | None
    termType: Literal["option"]
    terms: OptionTerms


class InstrumentTermRow3(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    instrument: dict[str, Any]
    effectiveFrom: TimePoint1 | TimePoint2 | None
    effectiveUntil: TimePoint1 | TimePoint2 | None
    termType: Literal["future"]
    terms: FutureTerms


class QuoteSubject1(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    instrument: dict[str, Any]


class QuoteSubject2(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    namespace: Annotated[str, Field(min_length=1)]
    key: Annotated[str, Field(min_length=1)]


class Dimensions(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    tenor: Annotated[str | None, Field(min_length=1)] = None
    expiry: TimePoint1 | TimePoint2 | None = None
    strike: Annotated[str | None, Field(min_length=1)] = None
    side: Literal["call", "put", "bid", "ask", "mid"] | None = None
    volType: Annotated[str | None, Field(min_length=1)] = None
    dayCount: Annotated[str | None, Field(min_length=1)] = None
    calendar: Annotated[str | None, Field(min_length=1)] = None
    businessDayConvention: Annotated[str | None, Field(min_length=1)] = None
    index: Annotated[str | None, Field(min_length=1)] = None
    contractMonth: Annotated[str | None, Field(min_length=1)] = None
    priceBasis: Annotated[str | None, Field(min_length=1)] = None
    greekName: Literal["delta", "gamma", "theta", "vega", "rho"] | None = None
    unitSemantics: Annotated[str | None, Field(min_length=1)] = None
    sourceDocumentation: Annotated[str | None, Field(min_length=1)] = None
    sourceEventTimeNs: Annotated[str | None, Field(pattern="^[0-9]+$")] = None
    sourceReceivedTimeNs: Annotated[str | None, Field(pattern="^[0-9]+$")] = None
    sourceReferenceTimeNs: Annotated[str | None, Field(pattern="^[0-9]+$")] = None
    dataset: Annotated[str | None, Field(min_length=1)] = None
    schema_: Annotated[str | None, Field(alias="schema", min_length=1)] = None
    providerInstrumentId: Annotated[str | None, Field(min_length=1)] = None
    publisherId: Annotated[int | None, Field(ge=0)] = None
    sourceRawSymbol: Annotated[str | None, Field(min_length=1)] = None
    sequence: Annotated[int | None, Field(ge=0)] = None
    recordFlags: Annotated[int | None, Field(ge=0)] = None
    tradeSide: Annotated[str | None, Field(min_length=1)] = None
    statType: Annotated[int | None, Field(ge=0)] = None
    statFlags: Annotated[int | None, Field(ge=0)] = None
    updateAction: Annotated[int | None, Field(ge=0)] = None
    size: Annotated[str | None, Field(pattern="^-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?$")] = None
    bidSize: Annotated[str | None, Field(pattern="^-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?$")] = None
    askSize: Annotated[str | None, Field(pattern="^-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?$")] = None
    sourceFields: dict[str, Any] | None = None
    """
    Original provider record fields; integers/decimals exceeding JSON numeric precision remain strings.
    """
    referenceDate: TimePoint1 | TimePoint2 | None = None


class QuoteRow(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    quoteId: Annotated[str, Field(min_length=1)]
    quoteType: Literal[
        "price",
        "bid",
        "ask",
        "yield",
        "rate",
        "discount_factor",
        "forward_points",
        "volatility",
        "settlement",
        "open_interest",
        "greek",
    ]
    subject: QuoteSubject1 | QuoteSubject2
    observedAt: TimePoint1 | TimePoint2 | None
    value: Annotated[str, Field(pattern="^-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?$")]
    unit: Annotated[str, Field(min_length=1)]
    currency: str | None
    dimensions: Dimensions
    sourceKey: Annotated[str, Field(min_length=1)]


class Descriptor(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    name: Annotated[str, Field(min_length=1)]
    kind: Literal["instrument_terms", "quote_set", "instrument_definitions"]
    sourceId: Annotated[str, Field(min_length=1)]
    sourceVersion: Annotated[str, Field(min_length=1)]
    availability: Availability
    formatVersion: Literal["talyx.market.professional.v1"]
    coverage: Coverage


class File2(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    role: Literal["source", "rows", "source_response"]
    filename: Annotated[str, Field(min_length=1)]
    sha256: Annotated[str, Field(pattern="^[0-9a-f]{64}$")]
    byteSize: Annotated[int, Field(ge=0)]
    rowCount: Annotated[int, Field(ge=0)]
    format: Literal["json", "csv"]


class Material(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    materialId: Annotated[str, Field(min_length=1)]
    version: Annotated[str, Field(pattern="^[0-9a-f]{64}$")]
    name: Annotated[str, Field(min_length=1)]
    kind: Literal["instrument_terms", "quote_set", "instrument_definitions"]
    sourceId: Annotated[str, Field(min_length=1)]
    sourceVersion: Annotated[str, Field(min_length=1)]
    acquiredAt: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    Unix epoch nanoseconds as a decimal string
    """
    availability: Availability
    formatVersion: Literal["talyx.market.professional.v1"]
    files: list[File2]
    coverage: Coverage


class ListInput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["instrument_terms", "quote_set", "instrument_definitions"] | None = None
    sourceId: Annotated[str | None, Field(min_length=1)] = None
    instrument: dict[str, Any] | None = None
    limit: Annotated[int | None, Field(ge=1, le=200)] = None
    after: str | None = None


class ListOutput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    materials: list[Material]
    hasMore: bool
    nextAfter: str | None


class ReadInput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    materialId: Annotated[str, Field(min_length=1)]
    version: Annotated[str, Field(pattern="^[0-9a-f]{64}$")]
    instrument: dict[str, Any] | None = None
    quoteType: Annotated[str | None, Field(min_length=1)] = None
    limit: Annotated[int | None, Field(ge=1, le=200)] = None
    after: str | None = None


UnresolvedTerm = TypeAliasType("UnresolvedTerm", Annotated[str, Field(min_length=1)])


class InstrumentDefinitionRow(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    definitionId: Annotated[str, Field(min_length=1)]
    instrument: dict[str, Any]
    effectiveFrom: TimePoint1 | TimePoint2 | None
    effectiveUntil: TimePoint1 | TimePoint2 | None
    sourceExpiry: TimePoint1 | TimePoint2 | None = None
    sourceFields: dict[str, Any]
    unresolvedTerms: list[UnresolvedTerm]


class StockPriceMap(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    scale: str
    offset: str
    maxError: str


class StockSession(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    sessionDate: date_aliased
    openAt: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    Unix epoch nanoseconds as a decimal string
    """
    closeAt: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    Unix epoch nanoseconds as a decimal string
    """
    priceMap: StockPriceMap
    rawOHLC: list[str]
    adjustedOHLC: list[str]
    rawReference: str | None
    rawLower: str | None
    rawUpper: str | None
    lower: str | None
    upper: str | None
    priceIncrement: str
    priceLimitEvidence: str
    rawVolume: str
    adjustedVolume: str
    rawPriceIncrement: str
    sourceOHLC: list[str]
    rawRestorationFactor: str


class OrderSize(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    minimum: float
    increment: float
    oddLotSellOnly: bool
    oddLotSellAll: bool
    effectiveFrom: str
    evidence: str


class Settlement(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    sellAfterTradingDays: int
    calendarId: str
    evidence: str


class Commission(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    rate: str
    perShare: str
    minimum: str
    maximumRate: str | None
    assumption: str
    evidence: str


class Tax(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    effectiveFrom: str
    effectiveUntil: str | None
    buyRate: str
    sellRate: str
    evidence: str


class CorporateAction(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    factId: str
    rowVersion: str
    assetId: str
    effectiveTime: str
    actionType: Literal["split", "dividend"]
    ratio: float | None = None
    amount: float | None = None
    currency: str | None
    exDate: str
    recordDate: str | None
    paymentDate: str | None
    listingDate: str | None
    publishedAt: str | None
    sourceRecord: dict[str, str]


class ProfessionalMaterials(RootModel[Any]):
    root: Any


class StockTerms(RootModel[Any]):
    root: Any


class FetchOutput(RootModel[FetchResult]):
    root: FetchResult


class ExportOutput(RootModel[ExportResult]):
    root: ExportResult


Path = TypeAliasType("Path", Annotated[str, Field(max_length=1024, min_length=1)])


class Operation(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    op: Literal["import"]
    spec: ImportSpec | None = None
    paths: Annotated[list[Path], Field(max_length=200, min_length=1)]
    """
    Absolute or workspace-relative CSV or Parquet files; one import spec for all of them.
    """


class Operation1(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    op: Literal["delete"]
    environment: Literal["production", "testnet", "demo"] = "production"
    """Environment of stored source series to delete."""
    symbols: Annotated[list[str] | None, Field(max_length=5000)] = None
    kinds: Annotated[list[DataKindRef] | None, Field(max_length=20)] = None
    importId: str | None = None
    sourceId: str | None = None
    """
    Delete everything stored from this source (e.g. after unsubscribing).
    """


class ImportFolder(Model):
    op: Literal["import"]
    folder: Path
    spec: ImportSpec | None = None


class WriteDataInput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    operation: Operation | ImportFolder | Operation1


class WriteDataOutput(RootModel[WriteDataResult]):
    root: WriteDataResult


class Operation2(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    op: Literal["enable"]
    source: str


class Operation3(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    op: Literal["disable"]
    source: str


class Operation4(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    op: Literal["configure"]
    source: str
    settings: dict[str, str | int | bool | None]


class Operation5(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    op: Literal["test"]
    sources: Annotated[list[str], Field(max_length=30, min_length=1)]
    sample: str | None = None


class Operation6(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    op: Literal["reconnect"]
    source: str


class Operation7(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    op: Literal["test_all"]


class WriteSourceInput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    operation: Operation2 | Operation3 | Operation4 | Operation5 | Operation6 | Operation7


class WriteSourceOutput(RootModel[WriteSourceResult]):
    root: WriteSourceResult


Venue = TypeAliasType("Venue", Annotated[str, Field(max_length=64, min_length=1)])


AssetClass = TypeAliasType("AssetClass", Annotated[str, Field(max_length=32, min_length=1)])


InstrumentId1 = TypeAliasType("InstrumentId1", Annotated[str, Field(max_length=80, min_length=1)])


class Redirect(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    from_: Annotated[str, Field(alias="from")]
    to: str


class MissingItem(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    instrumentId: str
    candidates: Annotated[list[Instrument], Field(max_length=10)]


class Fees(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    maker: Annotated[str, Field(pattern="^-?(0|[1-9][0-9]*)(\\.[0-9]+)?$")]
    """
    Exact decimal, no exponent or binary floating point conversion
    """
    taker: Annotated[str, Field(pattern="^-?(0|[1-9][0-9]*)(\\.[0-9]+)?$")]
    """
    Exact decimal, no exponent or binary floating point conversion
    """
    basis: Literal["venue_metadata", "market_fee_table"]


UnavailableAdditionalProperty = TypeAliasType(
    "UnavailableAdditionalProperty", Annotated[str, Field(max_length=300)]
)


class DataReadOutput(RootModel[SeriesPage]):
    root: SeriesPage


Capability = TypeAliasType("Capability", Annotated[str, Field(max_length=64, min_length=1)])


class DataCoverageOutput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    instrumentId: str
    items: list[CoverageItem]


class Item1(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    instrumentId: str
    quote: Quote | None
    problem: Problem | None


class QuotesCurrentOutput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    receivedAt: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    Unix epoch nanoseconds as a decimal string
    """
    items: list[Item1]


class QuotesOptionChainOutput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    rows: list[OptionRow]
    expiries: list[TimestampNs]
    underlyingPrice: Decimal | None
    nextCursor: str | None
    environment: Literal["production", "testnet", "demo"]
    asOf: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    Unix epoch nanoseconds as a decimal string
    """


Metric = TypeAliasType("Metric", Annotated[str, Field(max_length=256)])


class CoverageItem1(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    instrumentId: str
    start: TimestampNs | None
    end: TimestampNs | None
    source: SourceRef | None
    problem: Problem | None


class ResearchReadOutput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal[
        "actions",
        "factors",
        "members",
        "classification",
        "shares",
        "financials",
        "earnings_dates",
        "market_cap",
        "categories",
    ]
    columns: dict[str, list[Any]]
    coverage: list[CoverageItem1]
    unmapped: Annotated[int, Field(ge=0)]
    note: str | None
    """
    Source limitation in words, e.g. a member list with survivorship bias.
    """
    nextCursor: str | None


class Kind(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal[
        "actions",
        "factors",
        "members",
        "classification",
        "shares",
        "financials",
        "earnings_dates",
        "market_cap",
        "categories",
    ]
    source: SourceRef | None
    instruments: Annotated[int, Field(ge=0)]
    start: TimestampNs | None
    end: TimestampNs | None


class Indice(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    index: Instrument
    source: SourceRef
    start: TimestampNs | None
    end: TimestampNs | None
    pointInTime: bool
    survivorshipBias: bool
    note: str | None


class ResearchDescribeOutput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    kinds: list[Kind]
    indices: list[Indice]


class CalendarSessionsInput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    calendarId: Annotated[str, Field(max_length=64, min_length=1)]
    startDate: date_aliased
    endDate: date_aliased


class Session(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    date: date_aliased
    open: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    Unix epoch nanoseconds as a decimal string
    """
    close: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    Unix epoch nanoseconds as a decimal string
    """
    breaks: list[TimeRange]
    short: bool


class CalendarSessionsOutput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    calendarId: str
    timezone: str | None
    mode: Literal["exchange", "continuous"]
    evidence: Literal["verified", "unknown"]
    coveredFrom: date_aliased | None
    coveredUntil: date_aliased | None
    sessions: Annotated[list[Session], Field(max_length=4000)]


class CalendarBarTimesInput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    calendarId: Annotated[str, Field(max_length=64, min_length=1)]
    timeframe: Annotated[str, Field(pattern="^[1-9][0-9]*(s|m|h|d|w|M|mo|y)$")]
    """
    s seconds, m minutes, h hours, d days, w weeks, M or mo calendar months, y calendar years; aligned UTC unless instrument calendar specifies otherwise
    """
    sessionScope: Literal["regular", "extended", "all", "continuous"]
    start: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    Unix epoch nanoseconds as a decimal string
    """
    end: Annotated[str, Field(pattern="^(0|[1-9][0-9]*)$")]
    """
    Unix epoch nanoseconds as a decimal string
    """
    after: Annotated[str | None, Field(pattern="^(0|[1-9][0-9]*)$")] = None
    """
    Unix epoch nanoseconds as a decimal string
    """
    limit: Annotated[int | None, Field(ge=1, le=5000)] = 250


class CalendarBarTimesOutput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    timezone: str | None
    evidence: Literal["verified", "unknown"]
    coveredFrom: date_aliased | None
    coveredUntil: date_aliased | None
    open: list[TimestampNs]
    close: list[TimestampNs]
    next: TimestampNs | None
    reason: str | None


class ProfessionalListInput(RootModel[ListInput]):
    root: ListInput


class ProfessionalListOutput(RootModel[ListOutput]):
    root: ListOutput


class ProfessionalReadInput(RootModel[ReadInput]):
    root: ReadInput


class QuotesStreamParams(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    instruments: Annotated[list[Instrument], Field(max_length=500, min_length=1)]
    basis: Literal["rolling_24h", "last"] | None = "rolling_24h"
    environment: Literal["production", "testnet", "demo"] | None = "production"


class QuotesStreamItem(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: Literal["quotes"]
    items: list[Item1]


class SourceDescribeInput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    sourceId: str


class SourceDescribeOutput(RootModel[SourceCoverage]):
    root: SourceCoverage


class SourceSearchInput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    sourceId: str
    query: Annotated[str, Field(max_length=128)]
    scope: SourceScope | None = None
    limit: Annotated[int, Field(ge=1, le=200)]


class SourceSearchOutput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    candidates: list[Candidate1 | Candidate2 | Candidate3]


class SourceCatalogInput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    sourceId: str
    scope: SourceScope
    environment: Literal["production", "testnet", "demo"] | None = "production"
    cursor: str | None


class SourceCatalogOutput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    candidates: Annotated[list[Candidate1 | Candidate2 | Candidate3], Field(max_length=5000)]
    nextCursor: str | None


class SourceConfigureInput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    sourceId: str
    values: dict[str, str | int | bool | None]


class SourceConfigureOutput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    keyConfigured: bool


class SeriesId(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    seriesId: Annotated[str, Field(pattern="^vs1_[a-f0-9]{64}$")]
    identityVersion: Literal[1]
    instrumentId: str
    capability: Literal[
        "ohlcv",
        "reference",
        "ticker",
        "trades",
        "order_book",
        "funding_rate",
        "open_interest",
        "liquidations",
        "option_metrics",
        "long_short_account_ratio",
        "top_trader_account_ratio",
        "top_trader_position_ratio",
        "taker_volume",
        "derived_value",
    ]
    timeframe: Timeframe | None
    priceBasis: Literal["last", "mark", "index", "premium", "settlement", "reference"]
    sessionScope: Literal["continuous", "regular", "extended", "all"]
    adjustment: Literal["not_applicable", "raw", "split", "split_dividend", "provider_adjusted"]
    """
    provider_adjusted only for imported datasets that arrived adjusted.
    """
    adjustmentAnchor: Literal["latest", "earliest"] | None = None
    """
    split and split_dividend only. latest (forward adjusted, newest price unchanged) is the default and is written as null, so it never enters the hash; earliest keeps the first price unchanged and enters the hash.
    """
    importId: Annotated[str | None, Field(max_length=64)] = None
    """
    An imported dataset; null for the source Market selects.
    """
    referenceKind: Literal["exchange_rate", "yield", "commodity_price"] | None = None
    valueUnit: str | None = None
    provider: Annotated[str | None, Field(max_length=256)] = None
    key: Annotated[str | None, Field(max_length=256)] = None


class Series(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    series: SeriesId
    label: str


class SearchResult(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    instrument: Instrument
    matched: Literal["code", "former_code", "name", "contract"]
    formerCode: FormerCode | None
    local: bool
    """
    Market already stores data for it.
    """
    series: Annotated[list[Series], Field(max_length=20)]
    """
    Only imported datasets and derived value series of this instrument; ordinary series are built by the caller.
    """


class ExportSelection1(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    instrument: Instrument
    series: SeriesId


class Details(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    coverage: SourceCoverage
    settings: dict[str, Any]
    lastTest: WriteSourceResult | None
    route: dict[str, Any]
    wouldUse: Annotated[list[WouldUseItem], Field(max_length=120)]
    """
    Market x kind pairs this source would rank first for if it were enabled with its key (from withKey); empty when it already ranks first or would not.
    """
    adds: Annotated[list[Add], Field(max_length=40)]
    """
    In words, what configuring the key adds over the current declaration (from withKey).
    """


class ReadSourceOutput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    sources: Annotated[list[Source], Field(max_length=40)]
    selection: Annotated[list[SelectionItem], Field(max_length=120)]
    details: Details | None
    nextCursor: str | None


class SourceDescriptor(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    name: str
    sourceIds: Annotated[list[str], Field(min_length=1)]
    kinds: Annotated[list[str], Field(min_length=1)]
    """
    bars, quotes, trades, order_book, crypto capabilities, research kinds, professional, derived_value.
    """
    assetClasses: list[str]
    credentials: Literal["none", "optional", "required"]
    paid: bool
    supports: Annotated[
        list[Literal["search", "catalog", "history", "bulk", "live", "research", "configure"]],
        Field(min_length=1),
    ]
    localGateway: bool | None = False
    """
    Connects to a program on this computer (IB Gateway, Futu OpenD) instead of the internet.
    """
    settings: Annotated[list[Setting] | None, Field(max_length=20)] = None
    """
    Fields shown in the Market panel; secret fields are entered only there. Usage-billed sources declare their scope here (e.g. which Databento datasets may be used).
    """
    attribution: Annotated[str | None, Field(max_length=200)] = None
    """
    Credit the source requires wherever its data is shown (Data provided by CoinGecko).
    """
    withKey: SourceCoverage | None = None
    """
    Declared statically: what the source gives once its key is configured on its usual plan. The Market panel shows it before any key exists and ranks with it to say what Market would then use the source for; after configure, describe() answers with the real plan.
    """


class ReadOutput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    material: Material
    rows: list[
        QuoteRow
        | InstrumentDefinitionRow
        | InstrumentTermRow1
        | InstrumentTermRow2
        | InstrumentTermRow3
    ]
    coverage: Coverage
    hasMore: bool
    nextAfter: str | None


class StockExecutionMaterial(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    version: str
    currency: str
    orderSize: OrderSize
    settlement: Settlement
    commission: Commission
    taxes: list[Tax]
    limitations: list[str]
    sessions: list[StockSession]
    corporateActions: list[CorporateAction] | None = None
    series: SeriesId | None = None
    sourceVersion: str | None = None
    acquiredAt: Annotated[str | None, Field(pattern="^(0|[1-9][0-9]*)$")] = None
    """
    Unix epoch nanoseconds as a decimal string
    """
    materialVersion: str | None = None
    sourcePriceBasis: Literal["raw", "split"] | None = None


class InstrumentsSearchOutput(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    results: list[SearchResult]
    venues: list[CatalogState]
    nextCursor: str | None
    file: Artifact | None = None


class Item(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    instrumentId: str
    fees: Fees | None
    minQuantity: Decimal | None
    quantityIncrement: Decimal | None
    quantityUnit: Literal["base", "contracts", "shares"] | None
    contractMultiplier: Decimal | None
    priceIncrement: Decimal | None
    """
    Minimum price step. Crypto: the venue's tick. A-shares: 0.01. US and Hong Kong stocks: the step at the latest stored daily close, since it depends on the price (US $0.0001 below $1, else $0.01; HKEX's spread table); null with a reason in unavailable when no close is stored, and for Hong Kong exchange traded products.
    """
    unavailable: dict[str, UnavailableAdditionalProperty]
    stock: StockExecutionMaterial | None = None


class ProfessionalReadOutput(RootModel[ReadOutput]):
    root: ReadOutput


class SessionStreamParams(Model):
    model_config = ConfigDict(
        extra="forbid",
    )
    instrument: Instrument
    series: SeriesId
    warmup: Annotated[int | None, Field(ge=1, le=10000)] = 500
    """
    Closed rows in the first replace.
    """
    start: Annotated[str | None, Field(pattern="^(0|[1-9][0-9]*)$")] = None
    """
    Unix epoch nanoseconds as a decimal string
    """
    depth: Annotated[int | None, Field(ge=1, le=500)] = None
    """
    order_book only.
    """
    environment: Literal["production", "testnet", "demo"] | None = "production"


SessionStreamItem = SessionItem1 | SessionItem2 | SessionItem3 | SessionItem4
ReadSourceInput = ReadSourceInput1 | ReadSourceInput2 | ReadSourceInput3
SourceLiveItem = SourceLiveItem1 | SourceLiveItem2 | SourceLiveItem3 | SourceLiveItem4 | SourceLiveItem5 | SourceLiveItem6
