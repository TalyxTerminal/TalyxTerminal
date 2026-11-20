"""Values opens on a day the engine chooses (the last test day whose label every name has realised), not the last test day, whose label lies past the data."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from modules.quant.backend.model_runs import ModelRuns
from tests.unit.quant_runtime_fakes import adapt


def test_values_without_a_day_lets_the_engine_choose_and_keeps_its_answer(tmp_path: Path):
    jobs = []

    async def run_model(job, *_args, **_kwargs):
        jobs.append(dict(job))
        return {"ok": True, "date": 1_700_000_000_000, "rows": []}

    runs = ModelRuns.__new__(ModelRuns)
    runs.read = lambda _identity: {"state": "completed", "number": 3, "objectId": "o", "windows": [{"test": {"to": 1_800_000_000_000}}]}
    runs.directory = lambda _identity: tmp_path
    async def ready_data(_identity):
        return {"barsPath": str(tmp_path / "bars"), "extrasPath": str(tmp_path / "extras")}
    runs.ready_data = ready_data
    runs.job = lambda _identity, _ready: asyncio.sleep(0, {})
    runs.engine = SimpleNamespace(run_model=run_model)

    runs.ctx = adapt(SimpleNamespace(platform=SimpleNamespace()))

    async def call(op, job, seconds=None):
        assert op == "model_values"
        return await run_model(job)
    runs.values_engine = lambda deep: SimpleNamespace(call=call)  # the process kept for values answers like the one-shot did

    first = asyncio.run(runs.values("r"))
    assert jobs[0]["date"] is None, "the last test day's label lies past the data; the engine picks the day"
    assert first["date"] == 1_700_000_000_000
    assert json.loads((tmp_path / "values" / "first.json").read_text(encoding="utf-8"))["date"] == 1_700_000_000_000
    asyncio.run(runs.values("r"))
    assert len(jobs) == 1, "the chosen day is kept for the next look"
    asyncio.run(runs.values("r", 1_600_000_000_000))
    assert jobs[1]["date"] == 1_600_000_000_000
