"""Professional materials: fixed versions of source contract terms and quote sets (``professional.*``).

A material is a source's bond, option or futures terms, contract definitions or
quotes, kept as the file it came in plus validated rows. Each version is
immutable: its id comes from (name, kind, source) and its version from the
descriptor and the file's bytes. Positions and pricing belong to the caller.
Instants are nanosecond strings; dates keep their date precision and zone.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
import threading
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

import jsonschema
from referencing import Registry, Resource
from talyx_sdk.i18n import t

from .common import MarketError, now_ns
from .routing import in_worker

VOCABULARY = Path(__file__).parents[3] / "vocabulary" / "schema.json"
MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 100_000
JSON_COLUMNS = {"instrument", "subject", "terms", "dimensions", "sourceFields", "unresolvedTerms", "sourceExpiry",
                "effectiveFrom", "effectiveUntil", "observedAt"}
ROW_SCHEMA = {"instrument_terms": "InstrumentTermRow", "instrument_definitions": "InstrumentDefinitionRow",
              "quote_set": "QuoteRow"}


def _schemas():
    schema = json.loads((Path(__file__).parent / "professional-materials.schema.json").read_text(encoding="utf-8"))
    vocabulary = json.loads(VOCABULARY.read_text(encoding="utf-8"))
    draft = "https://json-schema.org/draft/2020-12/schema"
    registry = Registry().with_resources(
        (f"vocab:{name}", Resource.from_contents({"$schema": draft, **value, "$defs": vocabulary["$defs"]}))
        for name, value in vocabulary["$defs"].items())
    return schema, registry


SCHEMA, REGISTRY = _schemas()
_VALIDATORS: dict[str, jsonschema.Draft202012Validator] = {}
_LOCK = threading.RLock()


def encoded(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def digest(value) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def validate(name: str, value) -> None:
    validator = _VALIDATORS.get(name)
    if validator is None:
        validator = _VALIDATORS[name] = jsonschema.Draft202012Validator(
            {"$ref": "#/$defs/" + name, "$defs": SCHEMA["$defs"]}, registry=REGISTRY,
            format_checker=jsonschema.FormatChecker())
    try:
        validator.validate(value)
    except jsonschema.ValidationError as exc:
        field = ".".join(map(str, exc.path)) or {"Descriptor": "Material details", "Material": "Material",
                                                  "InstrumentTermRow": "Contract terms",
                                                  "QuoteRow": "Source quote"}.get(name, name)
        if exc.validator == "required" and isinstance(exc.instance, dict):
            explanation = "requires " + ", ".join(key for key in exc.validator_value if key not in exc.instance)
        elif exc.validator == "additionalProperties" and isinstance(exc.instance, dict):
            extra = sorted(set(exc.instance) - set(exc.schema.get("properties", {})))
            explanation = "has unrecognized fields: " + ", ".join(extra)
        elif exc.validator in {"oneOf", "anyOf"}:
            explanation = ("does not match the selected material type or its source time precision "
                           "(instants are nanosecond strings); check the file format")
        elif exc.validator == "pattern":
            explanation = "has an invalid format; keep decimal values and instants as strings"
        else:
            explanation = "has an invalid " + str(exc.validator) + "; check the selected material format"
        raise ValueError(f"{field} {explanation}") from exc


def _timepoints(value) -> None:
    if isinstance(value, dict):
        if value.get("precision") == "date":
            date.fromisoformat(value["value"])
            if value["timezone"] is not None:
                ZoneInfo(value["timezone"])
        for item in value.values():
            _timepoints(item)
    elif isinstance(value, list):
        for item in value:
            _timepoints(item)


def _instrument(value) -> dict:
    if not isinstance(value, dict) or not isinstance(value.get("instrumentId"), str) or not value["instrumentId"]:
        raise ValueError(t("Each instrument must carry its instrumentId"))
    return value


def member(row: dict) -> str:
    if "instrument" in row:
        return row["instrument"]["instrumentId"]
    subject = row["subject"]
    return subject["instrument"]["instrumentId"] if "instrument" in subject else \
        subject["namespace"] + ":" + subject["key"]


def missing_evidence(material: dict) -> list[str]:
    """What the material does not say about when its rows were known (warnings for an import)."""
    availability = material["availability"]
    missing = []
    if availability["publishedAt"] is None:
        missing.append("source publication time")
    for field in ("publishedAt", "revisedAt"):
        value = availability[field]
        if value and value["precision"] == "date" and value["timezone"] is None:
            missing.append(field + " source timezone")
    if availability["revisionHistory"] != "complete":
        missing.append("source revision history")
    if not availability["reference"]:
        missing.append("release and revision evidence reference")
    return missing


def _atomic(path: Path, content: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def _positive(value, name: str, index: int) -> None:
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(t("Row {index}: {name} must be a decimal", index=index + 1, name=name)) from exc
    if not number.is_finite() or number <= 0:
        raise ValueError(t("Row {index}: {name} must be a positive finite decimal", index=index + 1, name=name))


class MaterialStore:
    def __init__(self, root: Path):
        self.directory = root / "professional"
        self.directory.mkdir(parents=True, exist_ok=True)

    def paths(self, material_id: str, version: str) -> tuple[Path, Path, Path]:
        if not re.fullmatch(r"mpm_[0-9a-f]{32}", material_id) or not re.fullmatch(r"[0-9a-f]{64}", version):
            raise MarketError("invalid_input", t("Select an exact professional material version"))
        root = self.directory / material_id / version
        return root, root / "material.json", root / "rows.json"

    def prepare(self, raw: bytes, filename: str, descriptor: dict) -> tuple[dict, list[dict]]:
        """Validate a file against its descriptor: the material record and the rows it would store."""
        validate("Descriptor", descriptor)
        _timepoints(descriptor)
        if not 1 <= len(raw) <= MAX_BYTES:
            raise ValueError(t("Choose a professional material file up to 8 MB"))
        suffix = Path(filename).suffix.lower()
        if suffix == ".json":
            rows = json.loads(raw.decode("utf-8-sig"))
        elif suffix == ".csv":
            rows = []
            for row in csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))):
                if None in row:
                    raise ValueError(t("CSV rows must match the named columns"))
                rows.append({key: json.loads(value) if key in JSON_COLUMNS else value for key, value in row.items()})
        else:
            raise ValueError(t("Choose JSON rows or CSV with JSON structured columns"))
        if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
            raise ValueError(t("A file must contain between 1 and 100,000 rows"))
        identities = set()
        for index, row in enumerate(rows):
            try:
                validate(ROW_SCHEMA[descriptor["kind"]], row)
            except ValueError as exc:
                raise ValueError(t("Row {index}: {exc}", index=index + 1, exc=exc)) from exc
            _timepoints(row)
            if "instrument" in row:
                _instrument(row["instrument"])
                if descriptor["kind"] == "instrument_definitions":
                    identity = row["definitionId"]
                    if identity in identities:
                        raise ValueError(t("Row {index}: duplicate source definition identity", index=index + 1))
                    identities.add(identity)
                    continue
                terms = row["terms"]
                if row["termType"] == "option":
                    _instrument(terms["underlying"])
                for key in ("unitFaceValue", "multiplier", "strike"):
                    if key in terms:
                        _positive(terms[key], key, index)
                if row["termType"] == "bond":
                    if terms["couponType"] == "fixed" and terms["couponRate"] is None:
                        raise ValueError(t("Fixed coupon bonds require a source couponRate"))
                    if terms["couponType"] == "floating" and not terms["floatingIndex"]:
                        raise ValueError(t("Floating coupon bonds require a source floatingIndex"))
                identity = (member(row), digest(row["effectiveFrom"]), row["termType"])
            else:
                if "instrument" in row["subject"]:
                    _instrument(row["subject"]["instrument"])
                if row["quoteType"] == "volatility" and not row["dimensions"].get("volType"):
                    raise ValueError(t("Volatility quotes require the source volType convention"))
                identity = row["quoteId"]
            if identity in identities:
                raise ValueError(t("Row {index}: duplicate term or quote identity", index=index + 1))
            identities.add(identity)
        coverage = descriptor["coverage"]
        returned = set(map(member, rows))
        if returned != set(coverage["returnedMembers"]):
            raise ValueError(t("Coverage returnedMembers must match the actual file members"))
        requested = set(coverage["requestedMembers"])
        if returned - requested or set(coverage["missingMembers"]) != requested - returned:
            raise ValueError(t("Coverage missingMembers must identify requested members absent from the file"))
        if coverage["completeness"] == "complete" and coverage["missingMembers"]:
            raise ValueError(t("Complete coverage cannot contain missing requested members"))
        if any(item["member"] not in returned for item in coverage["timeRanges"]):
            raise ValueError(t("Coverage time ranges must identify actual returned members"))
        content_sha = hashlib.sha256(raw).hexdigest()
        material_id = "mpm_" + digest({key: descriptor[key] for key in ("name", "kind", "sourceId")})[:32]
        # Acquisition time is local metadata, never part of the version.
        version = digest({"descriptor": descriptor, "contentSha256": content_sha})
        rows_bytes = encoded(rows)
        files = [{"role": "source", "filename": "source" + suffix, "sha256": content_sha, "byteSize": len(raw),
                  "rowCount": len(rows), "format": suffix[1:]},
                 {"role": "rows", "filename": "rows.json", "sha256": hashlib.sha256(rows_bytes).hexdigest(),
                  "byteSize": len(rows_bytes), "rowCount": len(rows), "format": "json"}]
        material = {**descriptor, "materialId": material_id, "version": version, "acquiredAt": str(now_ns()),
                    "files": files}
        validate("Material", material)
        return material, rows

    def commit(self, material: dict, rows: list[dict], raw: bytes, extra_files: dict | None = None) -> dict:
        """Store a prepared version; the same version again returns the stored one unchanged."""
        root, metadata, body = self.paths(material["materialId"], material["version"])
        with _LOCK:
            if metadata.exists():
                return self.read(material["materialId"], material["version"])[0]
            root.mkdir(parents=True, exist_ok=True)
            _atomic(root / material["files"][0]["filename"], raw)
            _atomic(body, encoded(rows))
            for filename, content in (extra_files or {}).items():
                declared = next((item for item in material["files"] if item["filename"] == filename), None)
                if Path(filename).name != filename or not declared or declared["byteSize"] != len(content) \
                        or declared["sha256"] != hashlib.sha256(content).hexdigest():
                    raise ValueError(t("An original response differs from its declared file"))
                _atomic(root / filename, content)
            _atomic(metadata, encoded(material))
        return material

    def read(self, material_id: str, version: str) -> tuple[dict, list[dict]]:
        root, metadata, body = self.paths(material_id, version)
        with _LOCK:
            if not metadata.exists():
                raise MarketError("not_found", t("The selected professional material version is not here"))
            material = json.loads(metadata.read_text(encoding="utf-8"))
            for file in material["files"]:
                path = root / file["filename"]
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != file["sha256"]:
                    raise MarketError("source_unavailable", t("A stored professional material file is missing or changed"))
            return material, json.loads(body.read_text(encoding="utf-8"))

    def list(self) -> list[dict]:
        with _LOCK:
            values = [json.loads(path.read_text(encoding="utf-8"))
                      for path in self.directory.glob("mpm_*/*/material.json")]
        return sorted(values, key=lambda item: (-int(item["acquiredAt"]), item["materialId"], item["version"]))

    def delete(self, material_id: str, version: str) -> int:
        root, metadata, _body = self.paths(material_id, version)
        removed = 0
        with _LOCK:
            if not metadata.exists():
                return 0
            for path in root.iterdir():
                removed += path.stat().st_size
                path.unlink()
            root.rmdir()
            if not any(root.parent.iterdir()):
                root.parent.rmdir()
        return removed


def _cursor(offset: int, query: dict) -> str:
    return base64.urlsafe_b64encode(encoded({"offset": offset, "query": digest(query)})).decode()


def _offset(after: str | None, query: dict) -> int:
    if not after:
        return 0
    try:
        value = json.loads(base64.urlsafe_b64decode(after.encode()))
        offset = int(value["offset"])
    except (ValueError, KeyError, TypeError) as exc:
        raise MarketError("invalid_input", t("The paging cursor is not one Market gave")) from exc
    if value.get("query") != digest(query) or offset < 0:
        raise MarketError("invalid_input", t("The paging cursor belongs to a different fixed query"))
    return offset


class Professional:
    """``professional.list`` and ``professional.read``."""

    def __init__(self, market):
        self.market = market
        self.store = MaterialStore(market.root)

    async def list(self, value: dict) -> dict:
        values = await in_worker(self.store.list)
        instrument_id = _instrument(value["instrument"])["instrumentId"] if value.get("instrument") else None
        selected = [item for item in values
                    if not any(value.get(key) and value[key] != item[key] for key in ("kind", "sourceId"))
                    and (instrument_id is None or instrument_id in item["coverage"]["returnedMembers"])]
        query = {"filters": value | {"after": None, "limit": None}, "versions": [item["version"] for item in selected]}
        offset = _offset(value.get("after"), query)
        end = offset + value.get("limit", 50)
        return {"materials": selected[offset:end], "hasMore": end < len(selected),
                "nextAfter": _cursor(end, query) if end < len(selected) else None}

    async def read(self, value: dict) -> dict:
        material, rows = await in_worker(self.store.read, value["materialId"], value["version"])
        if value.get("instrument"):
            identity = _instrument(value["instrument"])["instrumentId"]
            rows = [row for row in rows if member(row) == identity]
        if value.get("quoteType"):
            rows = [row for row in rows if row.get("quoteType") == value["quoteType"]]
        query = value | {"after": None, "limit": None}
        offset = _offset(value.get("after"), query)
        end = offset + value.get("limit", 50)
        return {"material": material, "rows": rows[offset:end], "coverage": material["coverage"],
                "hasMore": end < len(rows), "nextAfter": _cursor(end, query) if end < len(rows) else None}
