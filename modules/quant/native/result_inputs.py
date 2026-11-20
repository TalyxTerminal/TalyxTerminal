"""Read a fixed result table, checked against the manifest it was saved with."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd


def fixed_table(material):
    if material.get("sourceArtifact"):
        from .topic_artifacts import wide_table
        item = material["sourceArtifact"]
        roles = {"factor_values": "signal", "predictions": "prediction", "target_weights": "target_weights", "valuation": "valuation", "curve": "curve"}
        material["sourceResult"] = {"tableRole": roles.get(item["type"], item["type"]), "runId": item["runId"], "artifactId": item["id"]}
        return {"axes": item.get("sourceAxes", {}), "units": item.get("units", {}), "engine": item.get("engine"), "nativeKind": item.get("nativeKind")}, wide_table(material)
    source = material["sourceResult"]
    retained = material["sourceManifest"]
    manifest_path = Path(retained["path"])
    table_path = Path(material["path"])
    if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != source["digest"] or hashlib.sha256(table_path.read_bytes()).hexdigest() != source["tableDigest"]:
        raise ValueError("The fixed result input changed; restore its original manifest and table.")
    manifest = json.loads(manifest_path.read_text(encoding="utf8"))
    declared = next((item for item in manifest.get("files", []) if item.get("name") == source["tableName"]
                    and item.get("digest") == source["tableDigest"] and item.get("role") == source["tableRole"]), None)
    if declared is None or manifest.get("engine") != source["engine"]:
        raise ValueError("The selected table does not belong to this fixed native result.")
    return manifest, pd.read_parquet(table_path)

