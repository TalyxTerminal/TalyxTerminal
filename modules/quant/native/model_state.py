"""Fixed Recorder archives and separately usable numeric prediction materials."""
import hashlib
import importlib.metadata
import json
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

from .publish import safe


def archive_recorder(recorder, publisher, name, **metadata):
    from mlflow.tracking import MlflowClient
    client = MlflowClient(tracking_uri=recorder.uri)
    def files(directory=""):
        for entry in client.list_artifacts(recorder.id, directory):
            if entry.is_dir:
                yield from files(entry.path)
            else:
                yield entry.path
    artifacts = []
    with tempfile.TemporaryDirectory(dir=publisher.root) as downloads:
        for original in files():
            destination = (publisher.root / name / "native" / original).resolve()
            if not destination.is_relative_to(publisher.root.resolve()):
                raise ValueError("The Recorder contains a file outside its archive.")
            local = Path(recorder.download_artifact(original, dst_path=downloads))
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(local, destination)
            artifacts.append(publisher.artifact(destination, "Native Recorder · " + original, "native",
                             recorderId=recorder.id, experimentId=recorder.experiment_id,
                             uri=recorder.uri, nativePath=original, **metadata))
    return artifacts


def manifest(publisher, name, kind, files, operations, *, axes=None, units=None, config=None, engine="pyqlib", package=None):
    details = {"formatVersion": 1, "resultId": str(uuid4()), "engine": engine,
               "engineVersion": importlib.metadata.version(package or engine), "nativeKind": kind,
               "files": [{"name": item["name"], "digest": hashlib.sha256((publisher.root / item["name"]).read_bytes()).hexdigest(),
                          **({"role": {"pred.parquet": "prediction", "label.parquet": "labels", "signal.parquet": "signal"}.get(item.get("nativePath"), item.get("role")), "format": "parquet"} if item["name"].endswith(".parquet") else {}),
                          **({"nativePath": item["nativePath"]} if item.get("nativePath") else {})} for item in files],
               "recipeRef": publisher.invocation.get("recipeRef"), "inputRefs": publisher.invocation.get("materials", []),
               "axes": axes or {}, "units": units or {}, "availableOperations": operations, "config": config or {}}
    path = publisher.root / name / (kind + "-manifest.json" if kind in {"prediction", "signal"} else "model-manifest.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(safe(details), indent=2, allow_nan=False), encoding="utf-8")
    title = "Fixed prediction losses" if kind == "evaluation_losses" else "Fixed signal" if kind == "signal" else "Fixed prediction" if kind == "prediction" else "Saved native policy" if "qlib.rl.test" in operations else "Saved online manager" if "qlib.online.update" in operations else "Saved DDG-DA meta-model" if "qlib.ddgda.run" in operations else "Saved model"
    artifact = publisher.artifact(path, title, "result",
                                 digest=hashlib.sha256(path.read_bytes()).hexdigest(), resultId=details["resultId"], availableOperations=operations)
    publisher.index.setdefault("results", []).append({**details, "artifactName": artifact["name"], "digest": artifact["digest"], "unitId": name})
    from native.topic_artifacts import publish_manifest
    publish_manifest(publisher, publisher.index["results"][-1])
    return artifact


def publish_states(recorder, task, publisher, name, native_files, *, include_model=True, axes_metadata=None):
    files = {item["nativePath"]: item for item in native_files}
    config = {**publisher.index.get("effectiveConfig", {}), "task": task}
    axes = {"segments": task.get("dataset", {}).get("kwargs", {}).get("segments"), "index": ["datetime", "instrument"]}
    axes.update(axes_metadata or {})
    if include_model and "task" in files:
        original_task = recorder.load_object("task", trusted=True)
        axes["segments"] = original_task.get("dataset", {}).get("kwargs", {}).get("segments", axes["segments"])
        if "reweighter" in original_task:
            component = original_task["reweighter"]
            axes["nativeReweighter"] = type(component).__module__ + "." + type(component).__name__
            if callable(getattr(component, "reweight", None)):
                # The original object graph stays in the signed native task.
                # JSON describes the Dataset and Records used by continuation.
                config["task"] = {key:value for key,value in task.items() if key != "reweighter"}
                axes["nativeTaskArtifact"] = files["task"]["name"]
    providers = {}
    for item in publisher.invocation.get("materials", []):
        if not str(item.get("role", "")).startswith("provider") or not item.get("path"):
            continue
        receipt = Path(item["path"]) / "material.json"
        if receipt.is_file():
            metadata = json.loads(receipt.read_text(encoding="utf-8"))
            providers[item["role"]] = {key: metadata[key] for key in ("frequency", "timezone", "calendarTimeSemantics", "calendars", "historyMode", "priceBasis", "source", "units") if key in metadata}
    if providers:
        axes["providers"] = providers
    units = config.get("units", {})
    artifacts = []
    if include_model and {"params.pkl", "dataset", "task"}.issubset(files):
        try:
            model = recorder.load_object("params.pkl", trusted=True)
            dataset = recorder.load_object("dataset", trusted=True)
            if not callable(getattr(model, "predict", None)) or not callable(getattr(dataset, "config", None)) or not callable(getattr(dataset, "setup_data", None)):
                raise ValueError("The saved model or Dataset has no native continuation interface.")
            if not hasattr(dataset, "handler") or not hasattr(dataset.handler, "infer_processors"):
                raise ValueError("The Dataset needs its saved inference processing state.")
            axes["featureDefinition"] = task.get("dataset")
            artifacts.append(manifest(publisher, name, type(model).__module__ + "." + type(model).__name__, native_files,
                                      ["qlib.predict"], axes=axes, units=units, config=config))
        except Exception as error:
            publisher.index.setdefault("unavailableOperations", []).append({"unitId": name, "operation": "qlib.predict", "message": str(error)})
    if "pred.pkl" in files:
        numeric = []
        for key in ("pred.pkl", "label.pkl"):
            if key not in files:
                continue
            frame = recorder.load_object(key, trusted=True)
            if not hasattr(frame, "to_parquet"):
                frame = frame.to_frame("score")
            if frame.index.names != ["datetime", "instrument"] or frame.index.has_duplicates:
                raise ValueError("Prediction material needs unique native datetime/instrument observations.")
            path = publisher.root / name / (key[:-4] + "-values.parquet")
            frame.to_parquet(path)
            numeric.append(publisher.artifact(path, "Fixed " + key[:-4] + " values", "native", nativePath=key[:-4] + ".parquet"))
            axes[key[:-4] + "Columns"] = list(frame.columns)
            if len(frame):
                dates = frame.index.get_level_values("datetime")
                axes[key[:-4] + "Range"] = [dates.min(), dates.max()]
                axes["instruments"] = frame.index.get_level_values("instrument").unique().tolist()
        operations = ["qlib.evaluate"]
        if axes.get("nativeTaskArtifact"):
            numeric.append(files["task"])
        artifacts.append(manifest(publisher, name, "prediction", numeric, operations, axes=axes, units=units, config=config))
    return artifacts
