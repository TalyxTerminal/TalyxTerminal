"""Immutable JSON handoffs for results larger than one AI response."""
import hashlib
import os
import time
from pathlib import Path
from uuid import uuid4


def sweep_results(root):
    folder = root / "result-files"
    if folder.is_dir():
        cutoff = time.time() - 24 * 3600
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.name.endswith(".json") and entry.stat().st_mtime < cutoff:
                    Path(entry.path).unlink(missing_ok=True)


def publish_result(root, text):
    sweep_results(root)
    folder = root / "result-files"
    folder.mkdir(exist_ok=True)
    path = folder / (uuid4().hex + ".json")
    data = text.encode("utf-8")
    path.write_bytes(data)
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
