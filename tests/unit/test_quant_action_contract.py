"""Budget the actual tool schemas and representative full-scale wire results before implementing their handlers."""

import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from host.ai import tool_schema
from modules.quant.backend import public_results as results
from modules.quant.backend.entry import module
from vocabulary.python.types import TablePage

CONTRACT = Path(__file__).parents[2] / "modules/quant/public/contract.json"


@pytest.mark.parametrize("name", ["create_object", "update_object"])
def test_settings_tool_schemas_fit(name):
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    rendered = tool_schema(contract["actions"][name]["input"], contract["$defs"])
    assert len(json.dumps(rendered, ensure_ascii=False, separators=(",", ":"))) <= 4300   # 4000 before `code` (write code in the same call)


def test_action_examples_match_their_input_models():
    for name, spec in module.actions.items():
        assert spec.examples, name
        for example in spec.examples:
            TypeAdapter(spec.input).validate_python(example)


@pytest.mark.parametrize("kind,bars", [("strategy", 2520), ("factor", 2520), ("model", 2520)])
def test_large_run_summary_wire_budget(kind, bars):
    names = [f"XNYS:S{i:03}" for i in range(500)]
    span = {"start": "2015-01-02", "end": "2024-12-31", "timeframe": "1d"}
    summary = {
        "title": "Completed",
        "subtitle": "10 years",
        "openTarget": {"module": "quant", "panelType": "quant", "objectId": "o"},
        "keyNumbers": [{"label": "Return", "value": "18.2", "unit": "%", "direction": "up"}],
    }
    value = {
        "objectId": "o",
        "runId": "r",
        "objectName": "Experiment",
        "number": 2,
        "kind": kind,
        "runKind": "run",
        "state": "completed",
        "summary": summary,
        "tested": {**span, "bars": bars},
        "requested": {**span, "untilLatest": False},
        "heldOutFrom": None,
        "numbers": {"return": 0.182},
        "symbols": {"set": 500, "used": 500, "dropped": [], "droppedTotal": 0},
        "createdAt": "2025-01-02T09:00:00Z",
        "endedAt": "2025-01-02T09:05:10Z",
        "progress": None,
        "failure": None,
        "settings": {"data": {**span, "symbols": names}, "params": {}, "run": {}},
        "checks": [],
        "changed": [],
        "parts": ["summary", "settings"],
        "tables": ["daily"],
    }
    codec = TypeAdapter(results.RunSummary)
    assert len(codec.dump_json(codec.validate_python(value)).decode()) <= 18_000


@pytest.mark.parametrize("total", [20, 200])
def test_batch_page_wire_budget(total):
    values = [[index, "completed", 120, 0.32, 0.0029] for index in range(total)]
    page = TablePage.model_validate(
        {
            "columns": [
                {"name": n, "unit": u}
                for n, u in [
                    ("cell", None),
                    ("state", None),
                    ("long", "bars"),
                    ("sharpe", None),
                    ("return", "fraction"),
                ]
            ],
            "rows": values[:25],
            "nextCursor": "25" if total > 25 else None,
            "total": total,
        }
    )
    assert len(page.model_dump_json()) <= 18_000
