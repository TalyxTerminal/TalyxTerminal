"""Indicator authoring help in Developer: a read-only document source (the guide, ``authoring.md``,
and the built-ins' code as examples) and the development support whose descriptor lists them
for Developer's help (both declared in ``talyx.module.json``)."""
from __future__ import annotations

import hashlib
from pathlib import Path

from talyx_sdk import ContractError

from .pine.indicators import BUILTIN

GUIDE = Path(__file__).with_name("authoring.md")


async def read(input: dict, call=None) -> dict:
    key = input["sourceKey"]
    if key == "guide":
        path, language = GUIDE, "markdown"
    else:
        path, language = BUILTIN / (key.removeprefix("builtin/") + ".py"), "python"
        if not key.startswith("builtin/") or path.parent != BUILTIN or not path.is_file():
            raise ContractError("not_found", f"No indicator document {key}")
    content = path.read_text(encoding="utf-8")
    return {"sourceKey": key, "name": path.name, "canonicalUri": None, "content": content,
            "version": hashlib.sha256(content.encode()).hexdigest(), "language": language, "readOnly": True,
            "context": {"contextId": "chart-indicators", "projectId": None, "rootUri": None, "languageMaterials": []}}


async def write(input: dict, call=None) -> dict:
    raise ContractError("permission_denied", "Chart's indicator documents are read-only.")


async def invoke(request: dict, call) -> dict:
    raise ContractError("unknown_capability", "Chart's indicator support only lists documentation.")
