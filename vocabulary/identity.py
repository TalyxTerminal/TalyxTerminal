"""Shared v2 identity semantics; wire numbers retain decimal precision.

Instruments of identityVersion 1 are venue contracts whose id hashes the contract
fields; identityVersion 2 ids are assigned by Market and only checked for shape.
"""

import hashlib
import json
import re
from decimal import Decimal

VERSION = "2.0.0"
ASSIGNED_ID = re.compile(r"vi2_[a-f0-9]{32}")

# Timeframe units in nanoseconds; months and years are calendar periods.
_UNIT_NS = {"s": 10**9, "m": 60 * 10**9, "h": 3600 * 10**9, "d": 86400 * 10**9, "w": 7 * 86400 * 10**9}


def _identity(prefix, payload):
    # Identity material consists solely of strings, booleans, null and the fixed
    # integer version. Sorted UTF-8 JSON is JCS for this bounded material.
    return (
        prefix
        + hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
    )


def _text(value, field):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(f"invalid {field}")
    return value.strip()


def _optional(value, *, upper=False):
    if value is None:
        return None
    value = value.strip()
    return value.upper() if upper and value else value or None


def _decimal(value):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?", value):
        raise ValueError("decimal must use exact string representation")
    number = Decimal(value)
    result = format(number, "f").rstrip("0").rstrip(".") if "." in value else value
    return "0" if number == 0 else result


def normalize_timeframe(value):
    value = value.strip()
    value = value[:-1] + "mo" if re.fullmatch(r"[1-9][0-9]*M", value) else value.lower()
    if not re.fullmatch(r"[1-9][0-9]*(s|m|h|d|w|mo|y)", value):
        raise ValueError("invalid timeframe")
    return value


def timeframe_nanoseconds(value):
    """Fixed length of a timeframe; calendar months and years have none (None)."""
    value = normalize_timeframe(value)
    count, unit = int(re.match(r"[0-9]+", value).group()), re.sub(r"^[0-9]+", "", value)
    return None if unit in {"mo", "y"} else count * _UNIT_NS[unit]


def instrument_id(record):
    if record["identityVersion"] == 2:
        raise ValueError("identityVersion 2 ids are assigned by Market, not computed")
    venue = _text(record["venueId"], "venueId").casefold()
    payload = {
        "version": 1,
        # The hash material keeps the key sourceId, filled with the venue id.
        "sourceId": venue,
        "venueId": venue,
        "symbol": _text(record["symbol"], "symbol").upper(),
        "calendarId": _text(record["calendarId"], "calendarId").upper(),
    }
    for key in (
        "marketType",
        "assetClass",
        "contractOrientation",
        "optionSide",
        "adjustmentEligible",
    ):
        payload[key] = record.get(key)
    for key in ("base", "quote", "settle", "underlyingSymbol"):
        payload[key] = _optional(record.get(key), upper=True)
    for key in ("contractSize", "strike"):
        payload[key] = _decimal(record.get(key))
    for key in ("expiry", "retiredAt"):
        value = record.get(key)
        if value is not None and (
            not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]*", value)
        ):
            raise ValueError(f"{key} must use nanosecond decimal string")
    payload["expiry"] = record.get("expiry")
    if record.get("retiredAt") is not None:
        payload["retiredAt"] = record["retiredAt"]
    return _identity("vi1_", payload)


def series_id(record):
    adjustment = record["adjustment"]
    anchor = record.get("adjustmentAnchor")
    if anchor not in {None, "latest", "earliest"}:
        raise ValueError("invalid adjustmentAnchor")
    if adjustment not in {"split", "split_dividend"} and anchor is not None:
        raise ValueError("only split and split_dividend series take an adjustment anchor")
    timeframe = _optional(record.get("timeframe"))
    if timeframe is not None:
        timeframe = normalize_timeframe(timeframe)
    capability = record["capability"]
    if capability in {"ohlcv", "reference"} and timeframe is None:
        raise ValueError("series requires timeframe")
    provider, key = record.get("provider"), record.get("key")
    if (capability == "derived_value") != (provider is not None and key is not None) or (
        capability != "derived_value" and (provider is not None or key is not None)
    ):
        raise ValueError("derived_value series require provider and key; other series cannot carry them")
    reference, unit = record.get("referenceKind"), record.get("valueUnit")
    if capability == "reference":
        if (
            record["priceBasis"] != "reference"
            or record["sessionScope"] != "all"
            or adjustment != "not_applicable"
            or reference not in {"exchange_rate", "yield", "commodity_price"}
            or not unit
        ):
            raise ValueError("invalid reference semantics")
    elif (
        reference is not None
        or record["priceBasis"] == "reference"
        or unit is not None
        and not (capability == "open_interest" and unit == "contracts")
    ):
        raise ValueError("reference fields require reference capability")
    payload = {
        "version": 1,
        "instrumentId": _text(record["instrumentId"], "instrumentId"),
        "capability": capability,
        "timeframe": timeframe,
        "priceBasis": record["priceBasis"],
        "sessionScope": record["sessionScope"],
        "adjustment": adjustment,
    }
    if capability == "reference":
        payload.update(referenceKind=reference, valueUnit=_text(unit, "valueUnit"))
    elif capability == "open_interest" and unit is not None:
        payload["valueUnit"] = unit
    if capability == "derived_value":
        payload.update(provider=_text(provider, "provider"), key=_text(key, "key"))
    # latest (forward adjusted) is the default and never enters the hash.
    if anchor == "earliest":
        payload["adjustmentAnchor"] = anchor
    if record.get("importId") is not None:
        payload["importId"] = _text(record["importId"], "importId")
    return _identity("vs1_", payload)


def validate_semantics(kind, record):
    if kind == "Instrument":
        if record["identityVersion"] == 2:
            if not ASSIGNED_ID.fullmatch(record["instrumentId"]) or record.get("retiredAt") is not None:
                raise ValueError("invalid assigned instrument id")
        elif record["instrumentId"] != instrument_id(record):
            raise ValueError("instrument identity mismatch")
    if kind == "SeriesId" and record["seriesId"] != series_id(record):
        raise ValueError("series identity mismatch")
    if kind == "TimeRange" and int(record["end"]) <= int(record["start"]):
        raise ValueError("time range must be nonempty and half-open")
    if kind == "Bar":
        lo, hi = Decimal(record["low"]), Decimal(record["high"])
        if (
            lo > hi
            or not all(lo <= Decimal(record[key]) <= hi for key in ("open", "close"))
            or Decimal(record["volume"]) < 0
        ):
            raise ValueError("invalid bar prices or volume")
    if kind == "Drawing":
        expected = {"trendline": 2, "horizontal": 1, "vertical": 1, "rectangle": 2}
        if record["kind"] in expected and len(record["points"]) != expected[record["kind"]]:
            raise ValueError("invalid drawing point count")
