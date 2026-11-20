"""The environment a language server analyses a document against, as a development support declares it.

A support that lists the `language` capability answers `invoke(capability="language", command="environment")` with
`environment` (`python.interpreterPath`, and `python.environmentPath` when it is a virtual environment) and
`languageMaterials`: `python-path` (a folder imports resolve against, in addition to the environment's own packages),
`python-path-scope` (`declared`: those folders are all the supports adds, no folder is added on its own) and
`python-analysis` (`typeCheckingMode`). Anything else in the list is ignored.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

MODES = {"off", "basic", "standard", "strict", "recommended", "all"}
DEFAULT_MODE = "standard"


def language_spec(output: dict | None) -> dict | None:
    """The interpreter and import folders from one support's answer; None when it names no Python."""
    output = output or {}
    python = (output.get("environment") or {}).get("python") or {}
    interpreter = python.get("interpreterPath")
    if not isinstance(interpreter, str) or not interpreter:
        return None
    paths: list[str] = []
    declared, mode = False, DEFAULT_MODE
    for item in output.get("languageMaterials") or []:
        kind = item.get("kind") if isinstance(item, dict) else None
        if kind == "python-path" and isinstance(item.get("path"), str) and item["path"] not in paths:
            paths.append(item["path"])
        elif kind == "python-path-scope":
            declared = item.get("scope") == "declared"
        elif kind == "python-analysis" and item.get("typeCheckingMode") in MODES:
            mode = item["typeCheckingMode"]
    return {"interpreter": interpreter, "environment": python.get("environmentPath"), "paths": paths, "declared": declared, "mode": mode}


def environment_key(spec: dict) -> str:
    """One language server per interpreter, wherever the importing folder is."""
    return hashlib.sha256(os.path.normcase(os.path.abspath(spec["interpreter"])).encode()).hexdigest()[:24]


def packages_stamp(spec: dict) -> str:
    """Changes when packages are installed into or removed from the environment, so its server is started again."""
    root = Path(spec["environment"] or Path(spec["interpreter"]).parent.parent)
    stamps = []
    for candidate in (root / "Lib" / "site-packages", *sorted((root / "lib").glob("python*/site-packages"))):
        try:
            stamps.append(f"{candidate}:{candidate.stat().st_mtime_ns}")
        except OSError:
            continue
    return "|".join(stamps)
