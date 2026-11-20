"""Python projection of the vocabulary Instrument and SeriesId (vocabulary 2).

identityVersion 1 instruments are venue contracts (all crypto, futures, options,
IB FX) whose id is a hash of the contract fields; identityVersion 2 instruments
are listings (stocks, funds, indices, source composites) whose id Market assigns
and keeps through renames and code changes. No identity names a data source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

AssetClass = Literal[
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
InstrumentType = Literal["spot", "swap", "future", "option", "unknown"]
SecurityType = Literal[
    "common_stock",
    "preferred_stock",
    "depositary_receipt",
    "etf",
    "mutual_fund",
    "index",
    "warrant",
    "bond",
    "option",
    "rights",
    "unit",
    "unknown",
]
ShareClass = Literal["a_share", "b_share", "h_share", "ordinary", "unknown"]
ContractOrientation = Literal["none", "linear", "inverse", "quanto", "unknown"]
OptionSide = Literal["call", "put"]
MarketDataCapability = Literal[
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
PriceBasis = Literal["last", "mark", "index", "premium", "settlement", "reference"]
ReferenceKind = Literal["exchange_rate", "yield", "commodity_price"]
SessionScope = Literal["continuous", "regular", "extended", "all"]
AdjustmentPolicy = Literal[
    "not_applicable",
    "raw",
    "split",
    "split_dividend",
    "provider_adjusted",
]

_SECURITY_ASSET_CLASSES: dict[SecurityType, frozenset[AssetClass]] = {
    "common_stock": frozenset({"equity"}),
    "preferred_stock": frozenset({"equity"}),
    "depositary_receipt": frozenset({"equity"}),
    "etf": frozenset({"fund"}),
    "mutual_fund": frozenset({"fund"}),
    "index": frozenset({"index"}),
    "warrant": frozenset({"warrant"}),
    "bond": frozenset({"fixed_income"}),
    "option": frozenset({"equity"}),
    "rights": frozenset({"equity", "warrant"}),
    "unit": frozenset({"equity", "fund"}),
    "unknown": frozenset({"equity", "fund", "index", "fixed_income", "warrant", "rate", "fx", "unknown"}),
}
_ADJUSTMENT_ELIGIBLE_SECURITY_TYPES = frozenset(
    {
        "common_stock",
        "preferred_stock",
        "depositary_receipt",
        "etf",
        "mutual_fund",
    }
)
_SHARE_CLASS_SECURITY_TYPES = frozenset(
    {"common_stock", "preferred_stock", "depositary_receipt", "unit", "unknown"}
)


def security_asset_class_compatible(asset_class: AssetClass, security_type: SecurityType) -> bool:
    return asset_class in _SECURITY_ASSET_CLASSES[security_type]


def security_adjustment_eligible(security_type: SecurityType, market_type: InstrumentType) -> bool:
    return market_type == "spot" and security_type in _ADJUSTMENT_ELIGIBLE_SECURITY_TYPES


def security_share_class_compatible(
    security_type: SecurityType, share_class: ShareClass | None
) -> bool:
    return share_class is None or security_type in _SHARE_CLASS_SECURITY_TYPES


def _required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"market {field_name} must not be empty")
    if len(normalized) > 256:
        raise ValueError(f"market {field_name} is too long")
    return normalized


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _optional_code(value: str | None) -> str | None:
    normalized = _optional_text(value)
    return normalized.upper() if normalized is not None else None


def _canonical_symbol(value: str) -> str:
    return _required_text(value, "symbol").upper()


def _canonical_timeframe(value: str | None) -> str | None:
    normalized = _optional_text(value)
    # CCXT's uppercase M is a calendar month; lowercase m is a minute.
    if normalized is not None and normalized.endswith("M") and normalized[:-1].isdigit():
        return normalized
    return normalized.casefold() if normalized is not None else None





def _wire_decimal(value: float | str | None) -> str | None:
    if value is None:
        return None
    from decimal import Decimal
    return format(Decimal(str(value)), "f")

def _ns_text(value: int | str | None, name: str, *, milliseconds: bool = False) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        if not value.isascii() or not value.isdecimal() or str(int(value)) != value:
            raise ValueError(f"market {name} must be a canonical nanosecond timestamp")
        return value
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"market {name} must be a non-negative integer timestamp")
    return str(value * 1_000_000 if milliseconds else value)


ASSET_CLASSES = frozenset(AssetClass.__args__)
SECURITY_TYPES = frozenset(SecurityType.__args__)
SHARE_CLASSES = frozenset(ShareClass.__args__)
_DESCRIPTIVE = ("adapterId", "nativeId", "securityType", "shareClass", "name", "countryCode", "currency",
                "mic", "listedFrom", "delistedAt", "active", "venueName")


@dataclass(frozen=True, slots=True)
class MarketInstrument:
    """One instrument record.

    Version 1 computes ``instrumentId`` from the contract fields (``expiry`` accepts
    integer milliseconds or a nanosecond string). Version 2 requires the id Market
    assigned; only its shape is checked here.
    """

    venueId: str
    symbol: str
    displaySymbol: str
    marketType: InstrumentType
    assetClass: AssetClass = "unknown"
    identityVersion: Literal[1, 2] = 1
    instrumentId: str | None = None
    adapterId: str | None = None
    nativeId: str | None = None
    base: str | None = None
    quote: str | None = None
    settle: str | None = None
    contractOrientation: ContractOrientation = "none"
    contractSize: float | str | None = None
    expiry: int | str | None = None
    strike: float | str | None = None
    optionSide: OptionSide | None = None
    underlyingSymbol: str | None = None
    retiredAt: str | None = None
    calendarId: str = "24_7"
    adjustmentEligible: bool = False
    securityType: SecurityType | None = None
    shareClass: ShareClass | None = None
    name: str | None = None
    countryCode: str | None = None
    currency: str | None = None
    mic: str | None = None
    listedFrom: str | None = None
    delistedAt: str | None = None
    active: bool | None = None
    venueName: str | None = None

    def __post_init__(self) -> None:
        from decimal import Decimal

        from vocabulary.identity import ASSIGNED_ID, instrument_id

        if self.identityVersion not in {1, 2}:
            raise ValueError("market identityVersion is invalid")
        if self.marketType not in {"spot", "swap", "future", "option", "unknown"}:
            raise ValueError("market marketType is invalid")
        if self.assetClass not in ASSET_CLASSES:
            raise ValueError("market assetClass is invalid")
        if self.securityType not in SECURITY_TYPES | {None}:
            raise ValueError("market securityType is invalid")
        if self.shareClass not in SHARE_CLASSES | {None}:
            raise ValueError("market shareClass is invalid")
        if self.contractOrientation not in {"none", "linear", "inverse", "quanto", "unknown"}:
            raise ValueError("market contractOrientation is invalid")
        if self.marketType == "spot" and self.contractOrientation != "none":
            raise ValueError("spot market instrument requires contractOrientation=none")
        if self.marketType != "option" and self.optionSide is not None:
            raise ValueError("market optionSide requires an option instrument")
        for name, positive in (("contractSize", True), ("strike", False)):
            raw = getattr(self, name)
            if raw is None:
                continue
            number = Decimal(str(raw))
            if not number.is_finite() or number < 0 or positive and number == 0:
                raise ValueError(f"market {name} is out of range")
            object.__setattr__(self, name, _wire_decimal(raw))
        object.__setattr__(self, "expiry", _ns_text(self.expiry, "expiry", milliseconds=isinstance(self.expiry, int)))
        for name in ("retiredAt", "listedFrom", "delistedAt"):
            object.__setattr__(self, name, _ns_text(getattr(self, name), name))
        object.__setattr__(self, "venueId", _required_text(self.venueId, "venueId"))
        object.__setattr__(self, "symbol", _canonical_symbol(self.symbol))
        object.__setattr__(self, "displaySymbol", _required_text(self.displaySymbol, "displaySymbol"))
        object.__setattr__(self, "calendarId", _required_text(self.calendarId, "calendarId").upper())
        for name in ("adapterId", "nativeId", "name", "venueName"):
            object.__setattr__(self, name, _optional_text(getattr(self, name)))
        for name in ("base", "quote", "settle", "underlyingSymbol", "countryCode", "currency", "mic"):
            object.__setattr__(self, name, _optional_code(getattr(self, name)))
        if self.identityVersion == 1:
            object.__setattr__(self, "venueId", self.venueId.casefold())
            computed = instrument_id(self.canonical_dict(with_id=False))
            if self.instrumentId is not None and self.instrumentId != computed:
                raise ValueError("market instrumentId does not match the contract fields")
            object.__setattr__(self, "instrumentId", computed)
        else:
            if self.instrumentId is None or not ASSIGNED_ID.fullmatch(self.instrumentId):
                raise ValueError("market listing instruments carry the id Market assigned")
            if self.retiredAt is not None:
                raise ValueError("retiredAt belongs to venue contracts")

    @classmethod
    def from_wire(cls, value: dict[str, Any]) -> MarketInstrument:
        fields = {key: item for key, item in value.items() if key in cls.__dataclass_fields__}
        return cls(**fields)

    def to_dict(self) -> dict[str, Any]:
        return self.wire_dict()

    def wire_dict(self) -> dict[str, Any]:
        """The vocabulary Instrument plus the descriptive fields this record has."""
        value = self.canonical_dict()
        for name in _DESCRIPTIVE:
            if getattr(self, name) is not None:
                value[name] = getattr(self, name)
        return value

    def canonical_dict(self, *, with_id: bool = True) -> dict[str, Any]:
        names = ("identityVersion", "venueId", "symbol", "displaySymbol", "marketType", "assetClass", "base",
                 "quote", "settle", "contractOrientation", "contractSize", "expiry", "strike", "optionSide",
                 "underlyingSymbol", "retiredAt", "calendarId", "adjustmentEligible")
        value = {name: getattr(self, name) for name in names}
        if with_id:
            value = {"instrumentId": self.instrumentId, **value}
        return value


@dataclass(frozen=True, slots=True)
class MarketSeries:
    instrumentId: str
    capability: MarketDataCapability
    timeframe: str | None = None
    priceBasis: PriceBasis = "last"
    sessionScope: SessionScope = "continuous"
    adjustment: AdjustmentPolicy = "not_applicable"
    adjustmentAnchor: Literal["latest", "earliest"] | None = None
    importId: str | None = None
    referenceKind: ReferenceKind | None = None
    valueUnit: str | None = None
    provider: str | None = None
    key: str | None = None
    seriesId: str = field(init=False)
    identityVersion: Literal[1] = field(default=1, init=False)

    def __post_init__(self) -> None:
        from vocabulary.identity import normalize_timeframe, series_id

        if self.capability not in MarketDataCapability.__args__:
            raise ValueError("market capability is invalid")
        if self.priceBasis not in PriceBasis.__args__:
            raise ValueError("market priceBasis is invalid")
        if self.sessionScope not in SessionScope.__args__:
            raise ValueError("market sessionScope is invalid")
        if self.adjustment not in AdjustmentPolicy.__args__:
            raise ValueError("market adjustment is invalid")
        # latest is the default anchor and is written as null.
        if self.adjustmentAnchor == "latest":
            object.__setattr__(self, "adjustmentAnchor", None)
        timeframe = _optional_text(self.timeframe)
        object.__setattr__(self, "timeframe", None if timeframe is None else normalize_timeframe(timeframe))
        object.__setattr__(self, "instrumentId", _required_text(self.instrumentId, "instrumentId"))
        for name in ("importId", "provider", "key", "valueUnit"):
            object.__setattr__(self, name, _optional_text(getattr(self, name)))
        object.__setattr__(self, "seriesId", series_id(self._identity()))

    def _identity(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in (
            "instrumentId", "capability", "timeframe", "priceBasis", "sessionScope", "adjustment",
            "adjustmentAnchor", "importId", "referenceKind", "valueUnit", "provider", "key")}

    @classmethod
    def from_wire(cls, value: dict[str, Any]) -> MarketSeries:
        fields = {key: item for key, item in value.items() if key in cls.__dataclass_fields__ and key not in {"seriesId", "identityVersion"}}
        series = cls(**fields)
        if "seriesId" in value and value["seriesId"] != series.seriesId:
            raise ValueError("market seriesId does not match its fields")
        return series

    def to_dict(self) -> dict[str, Any]:
        value = {"seriesId": self.seriesId, "identityVersion": 1, **self._identity()}
        # Optional identity fields appear only when set.
        return {key: item for key, item in value.items()
                if item is not None or key in {"timeframe", "referenceKind", "valueUnit"}}


def default_adjustment(instrument: MarketInstrument) -> AdjustmentPolicy:
    """Chart default: A shares adjust for dividends too (domestic convention); other
    stocks only for splits (TradingView default); everything else is not adjustable."""
    if not instrument.adjustmentEligible:
        return "not_applicable"
    return "split_dividend" if instrument.venueId == "CN" else "split"


def default_session_scope(instrument: MarketInstrument) -> SessionScope:
    return "continuous" if instrument.calendarId == "24_7" else "regular"


def ohlcv_series(
    instrument: MarketInstrument,
    timeframe: str,
    *,
    price_basis: PriceBasis = "last",
    session_scope: SessionScope | None = None,
    adjustment: AdjustmentPolicy | None = None,
    adjustment_anchor: Literal["latest", "earliest"] | None = None,
    import_id: str | None = None,
) -> MarketSeries:
    return MarketSeries(
        instrumentId=instrument.instrumentId,
        capability="ohlcv",
        timeframe=timeframe,
        priceBasis=price_basis,
        sessionScope=session_scope or default_session_scope(instrument),
        adjustment=adjustment or default_adjustment(instrument),
        adjustmentAnchor=adjustment_anchor,
        importId=import_id,
    )


def reference_series(instrument: MarketInstrument, timeframe: str, *, reference_kind: ReferenceKind,
                     value_unit: str) -> MarketSeries:
    return MarketSeries(instrumentId=instrument.instrumentId, capability="reference", timeframe=timeframe,
                        priceBasis="reference", sessionScope="all", adjustment="not_applicable",
                        referenceKind=reference_kind, valueUnit=value_unit)


def derived_value_series(instrument: MarketInstrument, provider: str, key: str) -> MarketSeries:
    """One source-owned value per observation time, attached to an exact instrument."""
    return MarketSeries(instrumentId=instrument.instrumentId, capability="derived_value", priceBasis="last",
                        sessionScope="all", adjustment="not_applicable", provider=provider, key=key)


def derived_value_instrument(provider: str, key: str) -> MarketInstrument:
    """The contract-style instrument a scope=single derived value lives on."""
    return MarketInstrument(venueId=provider, symbol=key, displaySymbol=key, marketType="unknown",
                            assetClass="unknown", calendarId="24_7")


def market_data_series(
    instrument: MarketInstrument,
    capability: MarketDataCapability,
    *,
    timeframe: str | None = None,
    price_basis: PriceBasis = "last",
) -> MarketSeries:
    """Canonical series of one capability for an exact instrument."""
    if capability == "ohlcv":
        if timeframe is None:
            raise ValueError("OHLCV market data series requires a timeframe")
        return ohlcv_series(instrument, timeframe, price_basis=price_basis)
    if capability == "reference":
        raise ValueError("reference market data requires reference_series semantics")
    if capability in RATIO_CAPABILITIES:
        timeframe = timeframe or "5m"
    return MarketSeries(
        instrumentId=instrument.instrumentId,
        capability=capability,
        timeframe=timeframe,
        priceBasis=price_basis,
        sessionScope=default_session_scope(instrument),
        adjustment="not_applicable",
    )


RATIO_CAPABILITIES: frozenset[MarketDataCapability] = frozenset({
    "long_short_account_ratio", "top_trader_account_ratio", "top_trader_position_ratio", "taker_volume"})
AUXILIARY_CAPABILITIES: tuple[MarketDataCapability, ...] = (
    "ticker", "trades", "order_book", "funding_rate", "open_interest", "liquidations", "option_metrics",
    *sorted(RATIO_CAPABILITIES),
)
# Exchange statistics published per interval rather than as point observations.
STATISTICS_CAPABILITIES: frozenset[MarketDataCapability] = RATIO_CAPABILITIES
