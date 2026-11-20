"""Engine-owned language material, derived from the selected execution environment."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .native_environment import command


async def prepare(ctx, records_root, kind, environment):
    """The environment an object of this kind is edited in; a strategy's also carries Nautilus's declarations adapted for the editor."""
    if kind != "strategy":
        return environment
    script = Path(__file__).resolve().parents[1] / "native" / "nautilus_language.py"
    identity = hashlib.sha256(script.read_bytes() + environment["interpreterPath"].encode()).hexdigest()
    directory = records_root / "language-materials" / identity
    receipt = directory / "receipt.json"
    if not receipt.is_file():
        directory.mkdir(parents=True, exist_ok=True)
        await command(ctx, [environment["interpreterPath"], str(script), str(directory)], script.parent, directory, "python-language", read_roots=[script.parent])
    # This is an editor search path only. It never enters Python's execution
    # environment, and the original wheel and its generated stubs stay intact.
    manifest = json.loads(receipt.read_text(encoding="utf-8"))
    return {**environment, "languageMaterials": [{"kind": "python-path", "path": str(directory)}] if manifest["files"] else [],
            "languageMaterialIdentity": manifest["identity"]}
