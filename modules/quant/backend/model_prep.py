"""A model's preparation: the settings the pipeline declares (TALYX's standard file, or the model's own code), the inputs it can take,
a batch of setting values, and the job of the check before training.

The preparation is generated from the declarations; the object keeps only the values (`settings.params`). The standard pipeline is
TALYX's current `native/model_standard.py`, read here and run in the research process. Inputs are rows of `params.inputs`
({source, name, ref}): a library factor or value at a version (`ref` is the version id), a factor run not yet published (the run id),
a built-in set (alpha158, alpha360, styles), a Market series (its field, such as `$close`) or a feature written in code
(`features.py:function`).
"""
from __future__ import annotations

import ast
import asyncio
import copy
import itertools
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from talyx_sdk import ContractError

from ..native import model_algorithms as A
from .object_params import check_values, read_parameters
from .project_paths import MODULE

STANDARD = MODULE / "native" / "model_standard.py"
BATCH_LIMIT = 20
BUILTIN = [{"source": "builtin", "ref": "alpha158", "name": "Alpha158", "features": 157, "note": "Qlib's 158 price features less VWAP0, which needs a VWAP the source does not give"},
           {"source": "builtin", "ref": "alpha360", "name": "Alpha360", "features": 300, "note": "Qlib's 360 price features less the 60 VWAP ones"},
           {"source": "builtin", "ref": "styles", "name": "Styles", "features": 7, "note": "Momentum, reversal, volatility, residual volatility, beta, liquidity and size"}]
SOURCES = ("library", "value", "run", "builtin", "market", "code")
ALGORITHM_KEYS = {"lightgbm": "lgb", "xgboost": "xgb", "catboost": "cat", "ridge": "ridge", "lasso": "lasso", "double_ensemble": "dens", **{key: "nn" for key in A.NEURAL_IDS}}


def standard_declarations():
    return read_parameters(STANDARD.read_text(encoding="utf-8"), "model_standard.py")


def pipeline_of(path=None):
    """The standard pipeline a run uses: its version and a digest of its text."""
    import hashlib
    text = (path or STANDARD).read_text(encoding="utf-8")
    version = next((line.split("=", 1)[1].strip().strip('"') for line in text.splitlines() if line.startswith("__version__")), None)
    return {"version": version, "digest": hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]}


def declarations(store, obj):
    """What the preparation shows: the standard file's declarations, or the model's own project's when it is written as code."""
    if obj["settings"].get("mode", "standard") == "standard":
        return standard_declarations()
    return store.project_parameters(obj)


def values_of(store, obj, changes=None):
    """Every declared setting's value: the object's where set, the declaration's default otherwise, `changes` on top; checked."""
    declared = declarations(store, obj)
    if declared["errors"]:
        raise ContractError("invalid_input", declared["errors"][0]["message"], {"errors": declared["errors"]})
    params = {item["name"]: copy.deepcopy(obj["settings"].get("params", {}).get(item["name"], item["default"])) for item in declared["parameters"]}
    params.update(changes or {})
    try:
        check_values(declared["parameters"], params)
    except ValueError as error:
        raise ContractError("invalid_input", str(error)) from error
    return params


def settings_of(params):
    """The run's data-set settings from the standard names (what `model_standard.py` passes to `q.model_data`): the check before
    training reads them without running code. Own code that renames them is checked with the standard defaults."""
    get = params.get
    take_out = [name for name, on in (("industry", get("take_out_industry", False)), ("size", get("take_out_size", False))) if on]
    return {"leaveOut": {"halted": get("leave_out_halted", True), "young": get("leave_out_young", True), "illiquid": get("leave_out_illiquid", False),
                         "minDollar": get("min_dollar", 5e6)},
            "processing": {"inputs": {"missing": get("inputs_missing", "median"), "extremes": get("inputs_extremes", "mad3"), "scale": get("inputs_scale", "rank"),
                                      "takeOut": take_out},
                           "label": {"missing": get("label_missing", "drop"), "extremes": get("label_extremes", "mad3"), "scale": get("label_scale", "rank")}},
            "label": {"formula": get("label") or _quick_label(get), "objective": get("objective", "l2"), "objectiveParam": get("objective_param", 1.0),
                      "weight": get("weight", "equal"), "weightFormula": get("weight_formula", ""),
                      "aux": get("label_aux", "") if get("algorithm") == "tcts" else None},
            "split": {"trainMonths": int(get("train_months", 36)), "mode": get("window_mode", "rolling"), "validMonths": int(get("valid_months", 6)),
                      "everyMonths": int(get("every_months", 3)), "firstTest": get("first_test", "")}}


def _quick_label(get):
    """A model from before labels were formulas has label_days and label_form: the formula they stand for."""
    from ..native.model_data import quick_formula
    return quick_formula(int(get("label_days", 5)), get("label_form", "rank"))


def algorithm_params(params):
    """The chosen algorithm's own parameters by their short names (what the standard file passes to `q.fit`)."""
    algorithm = params.get("algorithm") or ""
    prefix = ALGORITHM_KEYS.get(algorithm)
    if prefix is None:
        return {}
    return {key[len(prefix) + 1:]: value for key, value in params.items() if key.startswith(prefix + "_")}


def algorithms():
    """The algorithm table, grouped, with each one's own parameters and what cannot run here; each parameter names the standard
    setting that holds it (`setting`), so the preparation edits the declared value."""
    declared = {item["name"] for item in standard_declarations()["parameters"]}
    out = []
    for item in A.declared():
        if item.get("unavailable"):
            continue  # what this environment cannot run is not listed
        prefix = ALGORITHM_KEYS.get(item["id"])
        params = [{**entry, **({"setting": f"{prefix}_{entry['key']}"} if prefix and f"{prefix}_{entry['key']}" in declared else {})} for entry in item["params"]]
        out.append({**item, "groupLabel": A.GROUP_LABELS[item["group"]], "params": params})
    return out


def feature_functions(directory):
    """`file.py:function` of each function decorated `@q.feature` in a project: the features written in code."""
    out = []
    root = Path(directory)
    for path in sorted(root.rglob("*.py")) if root.is_dir() else []:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and any(_is_feature(decorator) for decorator in node.decorator_list):
                out.append({"source": "code", "ref": f"{path.relative_to(root).as_posix()}:{node.name}", "name": node.name.replace("_", " ").capitalize()})
    return out


def _is_feature(node):
    node = node.func if isinstance(node, ast.Call) else node
    return isinstance(node, ast.Attribute) and node.attr == "feature" or isinstance(node, ast.Name) and node.id == "feature"


async def offers(services, obj):
    """What the inputs table can add: library factors and values at their newest version, factor runs not yet published (the newest
    completed run of each factor), the built-in sets, the Market series this model's data can give, and features written in code."""
    out = []
    for item in services.library.store.catalog():
        if item["state"] == "delisted" or item["kind"] not in ("factor", "prediction", "model"):
            continue
        out.append({"source": "library" if item["kind"] == "factor" else "value", "name": item["name"], "ref": item["version_id"], "version": item["version"], "state": item["state"],
                    "kind": item["kind"], **({"unavailable": "Update failed: " + item["failure"]["message"]} if item["state"] == "failed" and item.get("failure") else {})})
    rows = services.records.db.execute("""SELECT content FROM records WHERE kind='qrun' AND json_extract(content,'$.objectKind')='factor'
        AND json_extract(content,'$.state')='completed' AND json_extract(content,'$.runKind')='run' ORDER BY json_extract(content,'$.createdAt') DESC LIMIT 200""").fetchall()
    seen = set()
    for row in rows:
        record = json.loads(row[0])
        if record["objectId"] in seen:
            continue
        seen.add(record["objectId"])
        kept = record.get("mode") == "formula" or (services.factors.directory(record["id"]) / "output" / "cross" / "raw.npy").is_file()
        out.append({"source": "run", "name": f"{record['name']} #{record['number']}", "ref": record["id"], "formula": record.get("formula"),
                    "rankIc": (record.get("numbers") or {}).get("rankIc"), "published": record.get("version"),
                    **({} if kept else {"unavailable": "This run's values were not kept (only the newest runs and starred ones keep them); run the factor again"})})
    out.extend(copy.deepcopy(BUILTIN))
    try:
        fields = (await services.factors.fields(obj["id"]))["fields"]
    except ContractError:
        fields = []
    for field in fields:
        if field["group"] == "Library values":
            continue
        out.append({"source": "market", "name": field["label"], "ref": field["name"], "group": field["group"], **({"unavailable": field["unavailable"]} if field.get("unavailable") else {}),
                    **({"note": field["note"]} if field.get("note") else {})})
    out.extend(feature_functions(obj["directory"]))
    return out


def expand(params, vary):
    """A batch: every combination of the varied settings' values on top of `params` (at most 20 groups), each group with only what differs."""
    axes = [(item["name"], list(item["values"])) for item in vary or [] if item.get("values")]
    if not axes:
        return [{"values": {}, "params": params}]
    unknown = [name for name, _ in axes if name not in params]
    if unknown:
        raise ContractError("invalid_input", f"The preparation has no setting {unknown[0]}.")
    if any(len(values) < 1 for _, values in axes):
        raise ContractError("invalid_input", "Give each varied setting at least one value.")
    total = 1
    for _, values in axes:
        total *= len(values)
    if total > BATCH_LIMIT:
        raise ContractError("invalid_input", f"A batch trains at most {BATCH_LIMIT} groups; these values make {total}.", {"groups": total})
    groups = []
    for combination in itertools.product(*[values for _, values in axes]):
        changed = dict(zip([name for name, _ in axes], combination, strict=True))
        groups.append({"values": changed, "params": {**params, **changed}})
    return groups


def library_table(library, version_id, info, path, known_by=None):
    """A library version's values on these names as the research process reads them (label, column, value, availableAt)."""
    columns = {item["instrumentId"]: item["column"] for item in info["instruments"]}
    # Read as a table (not row by row through `read_values`: millions of rows made of dicts took seconds), in the order `read_values` gives.
    table = library.store.frame(version_id, start=info["since"], end=info["until"])
    table = table.sort_values(["datetime", "instrument", "field"], kind="stable")[["datetime", "instrument", "field", "value", "availableAt", "recordedAt", "mode"]]
    if not table.empty:
        first = sorted(set(table["field"]))[0]
        table = table[(table["field"] == first) & table["instrument"].isin(columns)]
    available = table["availableAt"].astype("int64")
    if known_by is not None and not table.empty:
        # A library update runs after its inputs made their values: what was known by then counts as known at the bar it is about, so the model
        # reads the factor's value of the same bar even when that value was made late (training reads values as they could be known).
        available = available.where(available > known_by, available.clip(upper=table["datetime"].astype("int64")))
    pd.DataFrame({"label": table["datetime"].astype("int64"), "column": table["instrument"].map(columns), "value": table["value"].astype(float),
                  "availableAt": available}).to_parquet(path, index=False)
    return str(path)


def concept_table(library, version_id, info, path):
    """A library table of names by concepts (a data item's values: one field for each concept, 1 for a member, 0 or nothing for not) on these names,
    written beside the run as names by concepts; returns what the check tells: how many of the names are in it and how many concepts it has."""
    columns = {item["instrumentId"]: item["column"] for item in info["instruments"]}
    rows = library.store.read_values(version_id)
    table = pd.DataFrame(rows, columns=["datetime", "instrument", "field", "value", "availableAt", "recordedAt", "mode"])
    table = table[table["instrument"].isin(columns)]
    if table.empty:
        wide = pd.DataFrame()
    else:
        latest = table.sort_values("datetime").groupby(["instrument", "field"])["value"].last().unstack("field").fillna(0.0)
        latest.index = latest.index.map(columns)
        wide = (latest != 0).astype("float32")
        wide = wide.loc[wide.sum(axis=1) > 0, wide.sum(axis=0) > 0]
    wide.columns = [str(name) for name in wide.columns]
    wide.to_parquet(path)
    return {"path": str(path), "names": int(len(wide)), "concepts": int(wide.shape[1]), "of": len(columns)}


def concept_sources(library):
    """What the Concepts row can choose: Market's concept-sector tables when it declares any (it declares none now), and the library's data items."""
    out = []
    for item in library.list({"kind": "data"})["items"]:
        shown = next((version for version in item["versions"] if version["state"] != "archived"), None)
        if shown:
            out.append({"id": shown["versionId"], "label": item["name"], "kind": "library"})
    return out


async def prepare_inputs(services, rows, info, directory, known_by=None):
    """Capture run metadata on its SQLite owner, then build the large input tables off the event loop."""
    records = {row["ref"]: services.factors.read(row["ref"]) for row in rows if row.get("source") == "run"}
    frozen = SimpleNamespace(library=services.library)
    if records:
        frozen.factors = SimpleNamespace(read=records.__getitem__, directory=services.factors.directory)
    return await asyncio.to_thread(resolve_inputs, frozen, rows, info, directory, known_by)


def resolve_inputs(services, rows, info, directory, known_by=None):
    """The inputs table as the research process takes them (`model_data.Input`), each pinned to what it is now: a library version (its
    values exported beside the run), a factor run's formula or kept values, a built-in set, a Market field, a function in the code."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    out, pins = [], []
    for index, row in enumerate(rows):
        source, name, ref = row.get("source"), (row.get("name") or "").strip(), (row.get("ref") or "").strip()
        identity = f"i{index + 1}"
        if source not in SOURCES:
            raise ContractError("invalid_input", f"Input {index + 1} has source {source!r}; it is one of {', '.join(SOURCES)}.")
        if source in ("library", "value"):
            try:
                version = services.library.store.version(ref)
            except ContractError as error:
                raise ContractError("invalid_input", f"{name or ref}: version not found; choose a version.") from error
            summary = services.library.read_version(version["id"])
            path = library_table(services.library, version["id"], info, directory / f"{identity}.parquet", known_by)
            out.append({"id": identity, "name": name or summary["name"], "source": source, "ref": version["id"], "path": path})
            pins.append({"input": identity, "name": summary["name"], "versionId": version["id"], "version": summary["version"], "kind": summary["kind"]})
        elif source == "run":
            record = services.factors.read(ref)
            if record["state"] != "completed":
                raise ContractError("invalid_input", f"{name or record['name']}: use a completed factor run.")
            if record.get("mode") == "formula":
                out.append({"id": identity, "name": name or f"{record['name']} #{record['number']}", "source": "run", "ref": ref, "formula": record["formula"]})
            else:
                cross = services.factors.directory(ref) / "output" / "cross"
                if not (cross / "raw.npy").is_file():
                    raise ContractError("invalid_input", f"{record['name']} #{record['number']}: this run's values were not kept; run the factor again.")
                out.append({"id": identity, "name": name or f"{record['name']} #{record['number']}", "source": "run", "ref": ref, "path": str(cross)})
            pins.append({"input": identity, "name": record["name"], "runId": ref, "number": record["number"], "objectId": record["objectId"], "formula": record.get("formula"),
                         "version": record.get("version")})
        elif source == "builtin":
            if ref not in {item["ref"] for item in BUILTIN}:
                raise ContractError("invalid_input", f"{name or ref}: the built-in sets are alpha158, alpha360 and styles.")
            out.append({"id": identity, "name": name or next(item["name"] for item in BUILTIN if item["ref"] == ref), "source": "builtin", "ref": ref})
        elif source == "market":
            if not ref.startswith("$"):
                raise ContractError("invalid_input", f"{name or ref}: a Market series is a field such as $close.")
            out.append({"id": identity, "name": name or ref[1:], "source": "market", "ref": ref, "formula": ref})
        else:
            if ":" not in ref or not ref.split(":", 1)[0].endswith(".py"):
                raise ContractError("invalid_input", f"{name or ref}: a feature in code is file.py:function.")
            out.append({"id": identity, "name": name or ref.split(":", 1)[1], "source": "code", "ref": ref})
    return out, pins


def data_ready(data):
    if not all(data.get(key) for key in ("since", "until")) or not (data.get("symbols") or data.get("list") or data.get("index")):
        raise ContractError("invalid_input", "Choose the names and the period in the data settings first.")


def held_out_of(resolved, day_ms):
    """The held-out period; `resolved` is the settings as a run takes them, with the last day already resolved (see object_data.resolve_until)."""
    held = resolved.get("heldOutFrom")
    if not held:
        return None
    return {"from": day_ms(held, False), "to": day_ms(resolved["until"], True)}
