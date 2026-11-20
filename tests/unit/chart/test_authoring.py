"""Chart's indicator authoring help in Developer: the declared descriptors fit Developer's extension
points, every listed document reads back in the document source's shape, and the guide's suffix
table is the one ``derived.py`` resolves."""
import asyncio
import json
import re
from pathlib import Path

import pytest
from talyx_sdk import ContractError

from modules.chart.backend import authoring
from modules.chart.backend.derived import PRICE_SUFFIXES, SUFFIXES

ROOT = Path(__file__).resolve().parents[3]


def test_the_declared_documentation_reads_back_from_charts_own_document_source():
    """What the manifest provides to Developer names Chart's own document source, and every document it lists reads back
    in the document source's shape (Developer's extension points are checked against Developer's contract once Developer
    is on the new platform)."""
    provides = json.loads((ROOT / "modules" / "chart" / "talyx.module.json").read_text(encoding="utf-8"))["provides"]
    from modules.chart.backend.entry import module
    assert {(point, identity) for point, items in module.implementations.items() for identity in items} >= {
        ("developer." + point, item["id"]) for point in ("document_source", "development_support") for item in provides["developer." + point]}
    documentation = provides["developer.development_support"][0]["descriptor"]["documentation"]
    for material in documentation:
        assert material["source"]["providerId"] == "chart:" + provides["developer.document_source"][0]["id"]
        document = asyncio.run(authoring.read({"sourceKey": material["source"]["sourceKey"]}))
        assert document["content"] and document["readOnly"] and set(document) >= {"sourceKey", "name", "version", "language", "context"}
    for key in ("builtin/../authoring", "rsi", "builtin/nope"):
        with pytest.raises(ContractError) as missing:
            asyncio.run(authoring.read({"sourceKey": key}))
        assert missing.value.code == "not_found"
    with pytest.raises(ContractError) as refused:
        asyncio.run(authoring.write({"sourceKey": "guide", "expectedVersion": None, "content": ""}))
    assert refused.value.code == "permission_denied"


def test_the_indicators_folder_exists_from_the_start_and_opens_in_developer(tmp_path):
    """Chart declares the folder, so it makes it: Developer opens it, the catalog answers with the built-ins."""
    from modules.chart.backend.pine.indicators import Catalog
    from modules.developer.backend.projects.service import ProjectService
    folder = tmp_path / "user" / "indicators"
    catalog = Catalog(None, folder)
    assert ProjectService(tmp_path / "developer").open(str(folder))["root"] == str(folder.resolve())
    assert catalog.refs() and all(ref["source"] == "builtin" for ref in catalog.refs())


def test_the_guide_lists_exactly_the_suffixes_chart_resolves():
    guide = authoring.GUIDE.read_text(encoding="utf-8")
    assert set(re.findall(r"^\| `(_[A-Z]+)` \|", guide, re.M)) == {*SUFFIXES, *PRICE_SUFFIXES}
