"""The same exact-number and identity vectors are consumed by Python and TS."""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from vocabulary.identity import instrument_id, series_id, validate_semantics

ROOT = Path(__file__).resolve().parents[2] / "vocabulary"
VECTORS = json.loads((ROOT / "identity-vectors.json").read_text(encoding="utf-8"))
SCHEMA = json.loads((ROOT / "schema.json").read_text(encoding="utf-8"))

def validator(kind): return Draft202012Validator({"$ref": f"#/$defs/{kind}", "$defs": SCHEMA["$defs"]})

def test_common_identity_vectors_and_financial_dimensions():
    for kind, records, identity, field in [("Instrument", VECTORS["instruments"], instrument_id, "instrumentId"), ("SeriesId", VECTORS["series"], series_id, "seriesId")]:
        for record in records:
            validator(kind).validate(record)
            assert identity(record) == record[field]
            validate_semantics(kind, record)
            with pytest.raises(ValueError): validate_semantics(kind, {**record, field: "incorrect"})
    assert len({item["seriesId"] for item in VECTORS["series"]}) == len(VECTORS["series"])
    original = VECTORS["instruments"][0]
    assert instrument_id({**original, "strike": "9007199254740993.123456790"}) != original["instrumentId"]
    assert instrument_id({**original, "venueId": "STRASSE", "contractSize": "100"}) == original["instrumentId"]

def test_ns_half_open_boundaries_and_exact_bar_prices():
    interval = {"start": "1790892000000000000", "end": "1790892000000000001", "unit": "ns", "bounds": "[)"}
    validator("TimeRange").validate(interval)
    validate_semantics("TimeRange", interval)
    with pytest.raises(ValueError): validate_semantics("TimeRange", {**interval, "end": interval["start"]})
    value = {"open": "9007199254740993.123456789", "high": "9007199254740993.123456790", "low": "9007199254740993.123456788", "close": "9007199254740993.123456789", "volume": "0.000000001"}
    validate_semantics("Bar", value)
    with pytest.raises(ValueError): validate_semantics("Bar", {**value, "close": "9007199254740993.123456791"})
    with pytest.raises(ValueError): validate_semantics("Bar", {**value, "volume": "-0.000000001"})
    assert "knownAt" in SCHEMA["$defs"]["Marker"]["required"]
    assert SCHEMA["$defs"]["TimestampNs"]["type"] == "string"

def test_native_calendar_month_alias_preserves_existing_month_and_minute_hashes():
    original = VECTORS["series"][0]
    assert series_id({**original, "timeframe": "1m"}) == original["seriesId"]
    expected_month = "vs1_20f54dc11e0ff90dbdd5a4e6a0d3b84fb83a31539f4315b7da79cd0a7bc6e6bf"
    assert series_id({**original, "timeframe": "1mo"}) == expected_month
    assert series_id({**original, "timeframe": "1M"}) == expected_month
    assert expected_month != original["seriesId"]
