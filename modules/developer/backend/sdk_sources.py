"""The TALYX SDK references in Developer's Help: read-only documents (``developer.document_source`` "talyx-sdk")."""
from __future__ import annotations

import hashlib
from pathlib import Path

from talyx_sdk import ContractError
from talyx_sdk.i18n import mark, t

from .projects.service import canonical_uri

_ROOT = Path(__file__).resolve().parents[3]
SOURCES = {
    "declare": ("sdk/talyx_sdk/declare.py", "python", mark("Declaring a module (Python)")),
    "process": ("sdk/talyx_sdk/process.py", "python", mark("A module's process: Context and Call (Python)")),
    "typescript": ("sdk/ui/src/linkage.tsx", "typescript", mark("Frontend context and components (TypeScript)")),
    "manifest": ("contracts/manifest_v5.schema.json", "json", mark("The manifest, talyx.module.json")),
    "platform": ("host/public/contract.json", "json", mark("The platform's actions")),
}


async def read(input: dict, call=None) -> dict:
    key = input["sourceKey"]
    if key not in SOURCES:
        raise ContractError("not_found", t("Choose a known SDK reference."))
    relative, language, _ = SOURCES[key]
    path = _ROOT / relative
    raw = path.read_bytes()
    return {"sourceKey": key, "name": path.name, "canonicalUri": canonical_uri(path), "content": raw.decode("utf-8"),
            "version": hashlib.sha256(raw).hexdigest(), "language": language, "readOnly": True,
            "context": {"contextId": "talyx-sdk", "projectId": None, "rootUri": None,
                        "languageMaterials": [{"kind": "python-path", "path": str(_ROOT / "sdk")}]}}


async def write(input: dict, call=None):
    raise ContractError("permission_denied", t("SDK reference files are read-only."))


def documentation() -> list[dict]:
    """How Help lists them."""
    return [{"name": title, "source": {"kind": "contribution", "providerId": "developer:talyx-sdk", "sourceKey": key}}
            for key, (_, _, title) in SOURCES.items()]
