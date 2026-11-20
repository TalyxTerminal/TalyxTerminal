"""Read a verified native result package without loading its financial objects."""
import hashlib
import json
from pathlib import Path


def read_source(invocation):
    root = Path(invocation["fittedSource"]).resolve()
    manifest = json.loads((root / invocation["sourceManifest"]).read_text(encoding="utf-8"))
    files = {}
    for item in manifest["files"]:
        path = (root / item["name"]).resolve()
        if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest() != item["digest"]:
            raise ValueError("A fixed result file changed before this operation.")
        files[item.get("nativePath", item["name"])] = path
    return manifest, files
