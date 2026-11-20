"""Typed, immutable research outputs, separate from report furniture."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

TYPES = {"factor_values", "predictions", "model", "target_weights", "valuation", "curve", "strategy_version"}


def provenance_context(config, axes, inputs, type):
    """Describe declared scopes separately from actual artifact observations."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    task = config.get("task")
    dataset = axes.get("featureDefinition") or (task.get("dataset", {}) if isinstance(task, dict) else {})
    dataset = dataset if isinstance(dataset, dict) else {}
    handler = dataset.get("kwargs", {}).get("handler", {})
    handler = handler.get("kwargs", {}) if isinstance(handler, dict) else {}
    providers = [value for value in (axes.get("providers") or {}).values() if isinstance(value, dict)]
    declarations = config.get("fixedInputs", {})
    declarations = declarations if isinstance(declarations, dict) else {}
    available = {role: value.get("availability") for role, value in declarations.items() if isinstance(value, dict) and value.get("availability")}

    def exact_ids(values):
        if not isinstance(values, (tuple, list)):
            return []  # A provider universe name is not its constituent list.
        found = []
        for value in values or []:
            if isinstance(value, str):
                found.append(value)
            elif isinstance(value, dict):
                identity = value.get("id") or value.get("instrument_id") or value.get("instrumentId")
                if isinstance(identity, str):
                    found.append(identity)
        return list(dict.fromkeys(found))

    instruments, scope = [], "undeclared"
    candidates = [(axes.get("assets") or axes.get("instruments"), "native_result"),
                  (handler.get("instruments"), "declared_handler"),
                  (config.get("assets") or config.get("instruments"), "declared_configuration")]
    if isinstance(axes.get("instrument"), str):
        candidates.insert(0, ([axes["instrument"]], "declared_source_identity"))
    for values, origin in candidates:
        instruments = exact_ids(values)
        if instruments:
            scope = origin
            break
    if not instruments and any(isinstance(value, dict) for value in (axes.get("instruments") or []) if isinstance(axes.get("instruments"), list)):
        scope = "native_identity_unavailable"
    if not instruments:
        instruments = exact_ids([asset for value in [*providers, *inputs] for asset in exact_ids(value.get("assets") or value.get("instruments"))])
        if instruments:
            scope = "fixed_input_scope"

    segments = axes.get("segments") or dataset.get("kwargs", {}).get("segments") or {}
    def interval(value):
        if isinstance(value, (tuple, list)) and len(value) == 2:
            return {"start": value[0], "end": value[1]}
        return value if isinstance(value, dict) else None
    ranges = {target: interval(segments.get(source)) for source, target in (("train", "trainingRange"), ("valid", "validationRange"), ("test", "predictionRange"))}
    ranges = {name: value for name, value in ranges.items() if value}
    if "trainingRange" not in ranges and handler.get("fit_start_time") and handler.get("fit_end_time"):
        ranges["trainingRange"] = {"start": handler["fit_start_time"], "end": handler["fit_end_time"]}
    known_range = axes.get("range") or axes.get("predRange") or axes.get("evaluationRange") or axes.get("estimationRange")
    basis = "native_result_range" if known_range else None
    if type == "model" and not known_range:
        known_range = ranges.get("trainingRange")
        basis = "declared_training_segment" if known_range else None

    time_candidates = [axes.get("timezone"), config.get("timezone")]
    secondary = [value.get("timezone") or (value["timeSemantics"].get("timezone") if isinstance(value.get("timeSemantics"), dict) else None) for value in [*providers, *inputs]]
    secondary.extend(value.get("timezone") or value.get("calendar", {}).get("timezone") for value in declarations.values() if isinstance(value, dict))
    def timezone(value):
        try:
            return value if isinstance(value, str) and ZoneInfo(value) else None
        except (ZoneInfoNotFoundError, ValueError):
            return None
    zone = next((valid for value in time_candidates if (valid := timezone(value))), None)
    if zone is None:
        zones = {valid for value in secondary if (valid := timezone(value))}
        zone = next(iter(zones)) if len(zones) == 1 else None
    daily = providers and all(value.get("frequency") in {"day", "1d"} for value in providers)
    time = {"kind": "source_session_date" if daily else axes.get("time", "source_observation"), "timezone": zone,
            "availability": axes.get("availability") or config.get("signalAvailability"), "inputAvailability": available,
            "inputs": [value.get("timeSemantics", value.get("calendarTimeSemantics")) for value in [*providers, *inputs] if value.get("timeSemantics") or value.get("calendarTimeSemantics")]}
    variables = []
    for value in (axes.get("dependent"), axes.get("independent")):
        variables.extend([value] if isinstance(value, str) else value if isinstance(value, list) else [])
    return {"instruments": instruments, "instrumentScope": scope, "variables": list(dict.fromkeys(variables)), "range": known_range, "rangeBasis": basis, "timeSemantics": time, **ranges}


def long_table(frame, *, datetime=None, instrument=None):
    """Preserve source observations and numeric fields on an explicit long axis."""
    import pandas as pd
    if isinstance(frame, pd.Series):
        frame = frame.to_frame(frame.name or "value")
    frame = frame.copy()
    if isinstance(frame.index, pd.DatetimeIndex) and frame.index.name is None and "datetime" not in frame:
        frame.index.name = "datetime"
    index_dimensions = [name for name in frame.index.names if name is not None]
    if not isinstance(frame.index, pd.RangeIndex) or frame.index.name:
        frame = frame.reset_index()
    frame.columns = [str(column) for column in frame.columns]
    aliases = {"date": "datetime", "Date": "datetime", "asset": "instrument", "entity": "instrument", "instrument_id": "instrument", "contract": "instrument", "curve": "instrument", "maturity": "datetime", "TradeId": "instrument", "#TradeId": "instrument", "CurveId": "instrument"}
    for old, new in aliases.items():
        if old in frame and new not in frame:
            frame = frame.rename(columns={old: new})
    if "datetime" not in frame:
        if "timestampNs" in frame:
            frame["datetime"] = pd.to_datetime(frame.pop("timestampNs").map(int), unit="ns", utc=True)
        else:
            frame["datetime"] = datetime
    if "instrument" not in frame:
        frame["instrument"] = instrument
    if "value" in frame:
        frame["value"] = pd.to_numeric(frame["value"], errors="raise")
        return frame
    values = [column for column in frame.select_dtypes(include="number") if column not in {"datetime", "instrument", "index", *index_dimensions}]
    if not values:
        raise ValueError("A named table needs numeric output values.")
    keys = [column for column in frame if column not in values and column != "index"]
    return frame.drop(columns=["index"], errors="ignore").melt(id_vars=keys, value_vars=values, var_name="field", value_name="value")


def metadata(publisher, path, name, type, **details):
    if type not in TYPES:
        raise ValueError("Unknown named artifact type: " + str(type))
    path = Path(path).resolve()
    root = publisher.root.resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Named artifacts must be complete files inside this run output.")
    if any(item["name"] == name for item in publisher.index.get("namedArtifacts", [])):
        raise ValueError("Named artifact names must be unique within a run.")
    original_config = publisher.invocation.get("configuration")
    effective_config = publisher.index.get("effectiveConfig")
    config = {**(original_config if isinstance(original_config, dict) else {}), **(effective_config if isinstance(effective_config, dict) else {})}
    inputs = []
    for material in publisher.invocation.get("materials", []):
        if material.get("sourceArtifact"):
            inputs.append(material["sourceArtifact"])
        elif material.get("path"):
            receipt = Path(material["path"]) / "material.json" if Path(material["path"]).is_dir() else Path(material["path"]).with_suffix(".metadata.json")
            if receipt.is_file():
                inputs.append(json.loads(receipt.read_text(encoding="utf8")))
    axes = details.pop("sourceAxes", None) or {}
    context = provenance_context(config, axes, inputs, type)
    adjustments = [value.get("adjustment") or value.get("priceBasis") or "unspecified" for value in inputs]
    distinct_adjustments = {json.dumps(value, sort_keys=True) for value in adjustments}
    inherited_adjustment = adjustments[0] if len(distinct_adjustments) == 1 else "unspecified" if "unspecified" in adjustments else "mixed" if adjustments else "unspecified"
    time = details.pop("timeSemantics", None) or context.pop("timeSemantics")
    item = {"id": publisher.invocation["runId"] + ":" + name, "name": name, "type": type,
            "format": "parquet" if path.suffix == ".parquet" else path.suffix.lstrip("."), "path": path.relative_to(root).as_posix(),
            "digest": hashlib.sha256(path.read_bytes()).hexdigest(), "topicId": publisher.invocation.get("topicId"),
            "runId": publisher.invocation["runId"], "stepId": publisher.invocation.get("stepId"),
            "timeSemantics": time, "adjustment": details.pop("adjustment", None) or config.get("priceBasis") or config.get("adjustment") or inherited_adjustment,
            **context, "instruments": details.pop("instruments", None) or context["instruments"],
            "range": details.pop("range", None) or context["range"],
            "sourceAxes": axes, "inputAdjustments": adjustments, **details}
    publisher.index.setdefault("namedArtifacts", []).append(item)
    publisher.write()
    return item


def table(publisher, frame, name, type, **details):
    import pandas as pd

    from .publish import safe
    if type not in TYPES:
        raise ValueError("Unknown named artifact type: " + str(type))
    if any(item["name"] == name for item in publisher.index.get("namedArtifacts", [])):
        raise ValueError("Named artifact names must be unique within a run.")
    frame = long_table(frame, datetime=details.pop("datetime", None), instrument=details.pop("instrument", None))
    if frame["datetime"].notna().any():
        dates = pd.to_datetime(frame["datetime"], errors="raise")
        details.setdefault("range", {"start": safe(dates.min()), "end": safe(dates.max())})
        details.setdefault("rangeBasis", "artifact_observations")
    details.setdefault("instruments", sorted(frame["instrument"].dropna().astype(str).unique().tolist()))
    if details["instruments"]:
        details.setdefault("instrumentScope", "artifact_observations")
    path = (publisher.root / "named" / (name + ".parquet")).resolve()
    if not path.is_relative_to((publisher.root / "named").resolve()):
        raise ValueError("Named artifact names must stay inside this run's named directory.")
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return metadata(publisher, path, name, type, columns=list(frame.columns), rows=len(frame), **details)


def publish_manifest(publisher, indexed):
    """Reuse native continuation archives; numeric outputs get portable long tables."""
    import pandas as pd
    ref = {key: indexed[key] for key in ("unitId", "artifactName", "digest")}
    ref["runId"] = publisher.invocation["runId"]
    common = {"sourceRef": ref, "sourceAxes": indexed.get("axes", {}), "units": indexed.get("units", {}), "engine": indexed.get("engine"), "nativeKind": indexed.get("nativeKind")}
    unit = indexed["unitId"]
    for file in indexed.get("files", []):
        role = file.get("role")
        type = {"signal": "factor_values", "prediction": "predictions"}.get(role)
        if type:
            table(publisher, pd.read_parquet(publisher.root / file["name"]), unit + "/" + type, type, **common)
    if "qlib.predict" in indexed.get("availableOperations", []):
        bundle(publisher, indexed, unit + "/model", "model", **common)


def bundle(publisher, indexed, name, type, **details):
    """A single retained file contains every file declared by the native manifest."""
    import zipfile
    if type not in TYPES:
        raise ValueError("Unknown named artifact type: " + str(type))
    if any(item["name"] == name for item in publisher.index.get("namedArtifacts", [])):
        raise ValueError("Named artifact names must be unique within a run.")
    target = (publisher.root / "named" / (name + ".zip")).resolve()
    root = publisher.root.resolve()
    if not target.is_relative_to(root / "named"):
        raise ValueError("Named bundle paths must stay inside this run.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in [{"name": indexed["artifactName"], "digest": indexed["digest"]}, *indexed.get("files", [])]:
            path = (root / item["name"]).resolve()
            if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest() != item["digest"]:
                raise ValueError("A native bundle file changed during publication.")
            archive.write(path, "output/" + item["name"])
        if type in {"model", "strategy_version"} and publisher.invocation.get("source"):
            source = Path(publisher.invocation["source"])
            for path in source.rglob("*"):
                if path.is_file() and not any(part in {"__pycache__", ".venv", ".git"} for part in path.relative_to(source).parts):
                    archive.write(path, "source/" + path.relative_to(source).as_posix())
        library = publisher.invocation.get("library")
        if library:
            library = Path(library)
            for path in library.rglob("*"):
                if path.is_file() and not any(part in {"__pycache__", ".venv", ".git"} for part in path.relative_to(library).parts):
                    archive.write(path, "lib/" + path.relative_to(library).as_posix())
        archive.writestr("provenance.json", json.dumps({key: publisher.invocation.get(key) for key in ("topicId", "stepId", "runId", "recipeRef", "entry", "definition")}, ensure_ascii=False))
    return metadata(publisher, target, name, type, **details)


def read_table(material):
    import pandas as pd
    item = material["sourceArtifact"]
    path = Path(material["path"])
    if hashlib.sha256(path.read_bytes()).hexdigest() != item["digest"]:
        raise ValueError("The fixed named artifact changed before consumption.")
    frame = pd.read_parquet(path)
    if not {"datetime", "instrument", "value"}.issubset(frame):
        raise ValueError("A named table needs datetime, instrument and value columns.")
    return frame


def wide_table(material):
    frame = read_table(material)
    if "field" not in frame:
        frame["field"] = "value"
    keys = ["datetime", "instrument", "field"]
    if frame.duplicated(keys).any():
        raise ValueError("The selected artifact has extra dimensions; select a unique observation and instrument table.")
    return frame.pivot(index=["datetime", "instrument"], columns="field", values="value").rename_axis(columns=None)
