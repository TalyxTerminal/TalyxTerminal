"""Market action inputs. Provider and stream protocols use the low-level types in models."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator
from talyx_sdk.declare import Model

from vocabulary.python.types import Symbol

from . import models as m

Symbols = Annotated[list[Symbol], Field(min_length=1, max_length=10000)]
# A date names a trading day; an instant must identify its time zone.
Moment = Annotated[
    str,
    Field(
        pattern=r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?(?:Z|[+-]\d{2}:\d{2}))?$"
    ),
]
Environment = Literal["production", "testnet", "demo"]
Adjustment = Literal["raw", "split", "split_dividend"]


class Page(Model):
    limit: Annotated[int, Field(gt=0, le=10000)] = 24
    cursor: str | None = None
    """nextCursor of the previous page."""


class ScanImport(Page):
    folder: m.Path
    spec: m.ImportSpec | None = None


class Search(Page):
    query: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    venue: m.Venue | list[m.Venue] | None = None
    market: m.MarketCode | list[m.MarketCode] | None = None
    assetClass: m.AssetClass | list[m.AssetClass] | None = None
    marketType: str | None = None
    includeInactive: bool = False
    environment: Environment = "production"
    constraint: m.TradeSearchConstraint | None = None


class Get(Page):
    symbols: Annotated[list[Symbol], Field(min_length=1, max_length=500)] | None = None
    records: Annotated[list[m.Instrument], Field(min_length=1, max_length=500)] | None = None
    at: Moment | None = None
    environment: Environment = "production"
    include: list[Literal["codes"]] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_lookup(self) -> Self:
        if (self.symbols is None) == (self.records is None):
            raise ValueError("Supply either symbols or records")
        if self.records and (
            "at" in self.model_fields_set or "environment" in self.model_fields_set
        ):
            raise ValueError("at and environment apply only to symbols")
        return self


class Index(Model):
    symbol: Symbol
    """The index, e.g. ^GSPC (S&P 500)."""
    at: Moment | None = None
    """Only the members on this date; without it every member for its own time in the index within start..end."""


class ExactSeries(Model):
    instrumentId: str
    series: m.SeriesId

    @model_validator(mode="after")
    def same_instrument(self) -> Self:
        if self.instrumentId != self.series.instrumentId:
            raise ValueError("series.instrumentId must match instrumentId")
        return self


class Targets(Model):
    symbols: Symbols | None = None
    """Market symbols (AAPL, 600519.SH, US:DASH, binance:BTC/USDT, okx:BTC/USDT:USDT) or instrumentIds."""
    index: Index | None = None

    @model_validator(mode="after")
    def one_target(self) -> Self:
        targets = ("symbols", "index", "series", "market")
        if (
            sum(getattr(self, key) is not None for key in targets if key in type(self).model_fields)
            != 1
        ):
            raise ValueError(
                "Supply exactly one target: "
                + ", ".join(k for k in targets if k in type(self).model_fields)
            )
        return self


class Window(Model):
    start: Moment | None = None
    """First day included: YYYY-MM-DD (exchange-local trading day for stocks, UTC for crypto) or a zoned ISO time."""
    end: Moment | None = None
    """Last day included; none means up to the latest."""


class ReadData(Targets, Window, Page):
    limit: Annotated[int, Field(gt=0, le=10000)] | None = None
    """Rows per symbol without start (default 24); with start, at most this many rows in the page."""
    data: str = "bars"
    """bars, or a crypto series: funding_rate, open_interest, long_short_account_ratio, top_trader_account_ratio,
    top_trader_position_ratio, taker_volume, option_metrics."""
    timeframe: m.Timeframe | None = None
    """1m, 5m, 1h, 4h, 1d, 1w, 1M ... (bars default 1d)."""
    adjustment: Adjustment | None = None
    """Stocks: split (default), split_dividend (total return) or raw (traded prices)."""
    anchor: Literal["earliest", "latest"] | None = None
    """Adjusted prices match actual prices at the latest bar (default) or the earliest."""
    priceBasis: Literal["last", "mark", "index", "premium"] | None = None
    """Derivatives: trade prices (last, default), mark, index or premium."""
    sessionScope: Literal["regular", "extended", "all", "continuous"] | None = None
    format: Literal["rows", "file"] = "rows"
    """file: the whole selection as one Parquet file (for Python)."""
    environment: Environment = "production"


class Research(Targets, Window, Page):
    limit: Annotated[int, Field(gt=0, le=10000)] = 1000
    data: Literal[
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
    market: Literal["US", "CN", "HK"] | None = None
    """classification only: the whole market's rows at `at`."""
    at: Moment | None = None
    """The state on this date (members, classification, shares)."""
    asOf: Moment | None = None
    """Hide what was published after this time (point in time)."""
    metrics: list[str] | None = None
    """financials only: the source's concept names, e.g. us-gaap:Revenues."""
    periodKinds: list[Literal["FY", "FQ"]] | None = None
    """financials only: fiscal years, fiscal quarters or both."""
    fill: Literal["none", "await"] = "await"
    """none: only what is stored locally."""


class Quotes(Page):
    symbols: Annotated[list[Symbol], Field(min_length=1, max_length=100)]
    basis: Literal["last", "rolling_24h"] = "rolling_24h"
    """Crypto change basis; stocks always compare with the previous session close."""
    environment: Environment = "production"


class OptionChain(Page):
    symbol: Symbol
    expiry: Moment | None = None
    environment: Environment = "production"


class Coverage(Page):
    environment: Environment = "production"
    symbols: Symbols | None = None
    data: str | None = None
    timeframe: m.Timeframe | None = None
    group: Literal["dataset", "instrument"] = "dataset"
    market: str | None = None
    venue: str | None = None
    status: str | None = None
    query: str | None = None

    @model_validator(mode="after")
    def check_filters(self) -> Self:
        inventory_filters = {"group", "market", "venue", "query", "status"} & self.model_fields_set
        if self.symbols and inventory_filters:
            raise ValueError(
                f"{', '.join(sorted(inventory_filters))} filters the local inventory; omit symbols"
            )
        return self


class Terms(Window, Page):
    symbols: Symbols | None = None
    series: list[ExactSeries] | None = None
    allowMissingPrices: bool = False

    @model_validator(mode="after")
    def one_target(self) -> Self:
        if bool(self.symbols) == bool(self.series):
            raise ValueError("Supply either symbols or series")
        return self


class Sessions(m.CalendarSessionsInput, Page):
    pass


class ReadSeries(Model):
    instrumentId: str
    series: m.SeriesId
    start: m.TimestampNs | None = None
    end: m.TimestampNs | None = None
    before: m.TimestampNs | None = None
    limit: Annotated[int, Field(ge=1, le=10000)] = 48
    fill: Literal["none", "await"] = "await"
    environment: Environment = "production"


class Fetch(Targets, Window):
    series: Annotated[list[ExactSeries], Field(min_length=1, max_length=10000)] | None = None
    """For modules: exact Market series instead of symbols."""
    data: Annotated[list[m.DataKindRef], Field(min_length=1, max_length=20)] | None = None
    """What to download: [{kind:"bars", timeframe:"1d"}, {kind:"funding_rate"}, {kind:"actions"} ...]."""
    environment: Environment = "production"


class Export(Targets, Window):
    series: Annotated[list[ExactSeries], Field(min_length=1, max_length=10000)] | None = None
    """For modules: exact Market series instead of symbols."""
    data: str | None = None
    """bars, a crypto series or a research kind (actions, members, ...)."""
    timeframe: m.Timeframe | None = None
    adjustment: Adjustment | None = None
    fill: Literal["none", "missing"] = "missing"
    """missing: download what is missing first; none: only what is stored."""
    environment: Environment = "production"
    allowEmpty: bool = False
    format: Literal["parquet", "csv"] = "parquet"
    destination: m.Destination | None = None


class Professional(Page):
    materialId: str | None = None
    version: str | None = None
    kind: str | None = None
    sourceId: str | None = None
    symbol: Symbol | None = None
    quoteType: str | None = None


class ListDownloads(Page):
    op: Literal["list"]


class ChangeDownload(Model):
    op: Literal["cancel", "resume"]
    jobId: str


class Downloads(Model):
    operation: ListDownloads | ChangeDownload
