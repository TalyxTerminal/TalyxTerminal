"""A run's fixed dependency lock, kept beside the run and resolved against this checkout's bundled wheels."""
from __future__ import annotations

import hashlib
from pathlib import Path

from talyx_sdk import ContractError

from .native_environment import bundled_wheels

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"


def write_recipe(directory, lock, origin, source, engine):
    path = Path(directory) / "execution.lock"
    path.write_text(lock, encoding="utf-8")
    return {"pythonVersion": origin["pythonVersion"], "lockPath": str(path),
            "lockDigest": hashlib.sha256(lock.encode()).hexdigest(),
            "sourceSnapshot": source, "engine": engine, "engineVersion": None}


def recipe_lock(recipe):
    if not recipe or not recipe.get("lockPath"):
        raise ContractError("unavailable", "This result has no saved dependency lock. Run its step again to continue from it.")
    lock = Path(recipe["lockPath"]).read_text(encoding="utf-8")
    if hashlib.sha256(lock.encode()).hexdigest() != recipe["lockDigest"]:
        raise ContractError("conflict", "The dependency lock saved with this result changed.")
    return bundled_wheels(lock, RUNTIME)
