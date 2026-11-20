"""Public Market results shared by callers."""

from typing import Any, Literal

from pydantic import Field
from talyx_sdk.declare import Model

from vocabulary.python.types import TableFile, TablePage

from . import models as m


class SearchResult(m.SearchResult):
    symbol: str
    instrumentId: str


class Search(Model):
    results: list[SearchResult]
    venues: list[m.CatalogState]
    nextCursor: str | None


class CodePeriod(Model):
    code: str
    name: str | None
    start: str | None
    end: str | None


class Match(Model):
    status: Literal["found", "redirect", "ambiguous", "not_found"]
    symbol: str
    instrument: m.Instrument | None = None
    previousId: str | None = None
    candidates: list[m.Instrument] | None = None
    codes: list[CodePeriod] | None = None
    externalIds: dict[str, str] | None = None
    conflicts: list[m.Conflict] | None = None


class Matches(Model):
    items: list[Match]
    nextCursor: str | None


class MarketTable(TablePage):
    meta: list[dict[str, Any]]


class SeriesMeta(Model):
    instrumentId: str
    symbol: str
    name: str | None
    source: str | None
    timeframe: str | None
    adjustment: str
    anchor: str | None
    priceBasis: str
    sessionScope: str
    timezone: str
    units: dict[str, str | None]
    first: str | None
    last: str | None
    gaps: list[dict[str, Any]]


class MissingListing(Model):
    symbol: str
    reason: str


class DataPage(MarketTable):
    missing: list[MissingListing]
    meta: list[SeriesMeta]


class MarketFile(TableFile):
    """Per-symbol metadata is carried in the Parquet schema."""

    missing: list[MissingListing]


class TradingSession(m.StockSession):
    openAt: str
    closeAt: str


class StockExecutionMaterial(m.StockExecutionMaterial):
    sessions: list[TradingSession]
    acquiredAt: str | None = None


class InstrumentTerms(m.Item):
    symbol: str
    stock: StockExecutionMaterial | None = None


class Terms(Model):
    items: list[InstrumentTerms]
    asOf: str
    nextCursor: str | None


class Quote(Model):
    price: m.Decimal | None
    last: m.Decimal | None
    bid: m.Decimal | None
    ask: m.Decimal | None
    mark: m.Decimal | None
    index: m.Decimal | None
    change: m.Decimal | None
    changePct: m.Decimal | None = Field(json_schema_extra={"x-unit": "percent"})
    changeBasis: Literal["previous_session_close", "rolling_24h"] | None
    asOf: str | None
    fieldAsOf: dict[str, str | None]
    delayed: bool


class QuoteItem(Model):
    symbol: str
    instrumentId: str
    quote: Quote | None
    problem: dict[str, Any] | None


class Quotes(Model):
    receivedAt: str
    items: list[QuoteItem]
    nextCursor: str | None


class OptionRow(m.OptionRow):
    symbol: str
    instrumentId: str
    asOf: dict[str, str | None]


class OptionChain(Model):
    rows: list[OptionRow]
    expiries: list[str]
    underlyingPrice: str | None
    nextCursor: str | None
    environment: str
    asOf: str


class Research(MarketTable):
    data: str
    unmapped: int
    note: str | None


class SessionBreak(Model):
    start: str
    end: str


class TradingDay(Model):
    date: str
    open: str
    close: str
    breaks: list[SessionBreak]
    short: bool


class Sessions(m.CalendarSessionsOutput):
    sessions: list[TradingDay]
    nextCursor: str | None


class Materials(Model):
    materials: list[m.Material]
    nextCursor: str | None


class MaterialRows(Model):
    material: m.Material
    rows: list[
        m.QuoteRow
        | m.InstrumentDefinitionRow
        | m.InstrumentTermRow1
        | m.InstrumentTermRow2
        | m.InstrumentTermRow3
    ]
    coverage: m.Coverage
    nextCursor: str | None


class FetchEstimate(Model):
    estimatedBytes: int
    freeBytes: int
    folder: str
    items: int
    unmappedMembers: int
    failures: list[m.Failure]


class DownloadState(Model):
    state: str


class Download(DownloadState):
    jobId: str
    createdAt: str
    request: dict[str, Any]
    done: int
    total: int
    rows: int
    bytes: int
    failed: int
    waitingFor: str | None
    waitingUntil: str | None
    interruption: str | None


class Downloads(Model):
    jobs: list[Download]
    nextCursor: str | None
