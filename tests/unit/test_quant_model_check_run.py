"""A job run in its own process: a channel folder a failed run left behind does not stop the next one. And a model run says how far its data is while
its check fetches it."""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

from modules.quant.backend import model_runs as R
from modules.quant.backend.factor_engine import FactorEngine
from tests.unit.quant_runtime_fakes import adapt


def _platform(started):
    async def start(launch):
        started.append(launch)
        channel = Path(launch["channel"]["directory"])
        assert not channel.exists(), "The code channel directory must not exist; the platform creates it for this run"
        channel.mkdir(parents=True)  # the platform makes it for the run
        (Path(launch["cwd"]) / "answer.json").write_text(json.dumps({"ok": True, "checks": []}), encoding="utf-8")
        return {"id": "lease"}

    async def status(_args):
        return {"running": False}

    async def nothing(_args):
        return {}
    return SimpleNamespace(execution=SimpleNamespace(start=start, status=status, wait=nothing), leases=SimpleNamespace(close=nothing),
                           python_env=SimpleNamespace(acquire=nothing, release=nothing))


def _engine(started):
    async def environment(_publish):
        return {"interpreterPath": "python", "preparationId": "p1"}
    engine = object.__new__(FactorEngine)
    engine.ctx = adapt(SimpleNamespace(platform=_platform(started)))
    engine.packages = SimpleNamespace(research_environment=environment)
    return engine


def test_a_channel_folder_a_failed_run_left_behind_does_not_stop_the_next(tmp_path):
    started = []
    engine = _engine(started)
    output = tmp_path / "output"
    (output / "channel").mkdir(parents=True)
    (output / "channel" / "leftover").write_text("x", encoding="utf-8")
    job = {"output": str(output)}
    for _ in range(2):  # the second run finds the first one's channel as well
        answer = asyncio.run(engine.run_once(job, tmp_path / "engine", context={}))
        assert answer["ok"] is True
    assert len(started) == 2


def test_a_run_says_how_far_its_data_is_while_the_check_fetches_it(tmp_path):
    """Fetching new bars for hundreds of names took minutes under a bare "Checking": the runs show the data step with its count, then the check again."""

    from talyx_sdk import ContractError
    records = {"r1": {"id": "r1", "state": "preparing", "stage": "check", "progress": {}}}
    seen = []
    runs = object.__new__(R.ModelRuns)
    runs.read = lambda identity: records[identity]
    runs.update = lambda identity, **changes: (records[identity].update(changes), seen.append(copy.deepcopy(changes)))
    runs.end_checking = lambda identities, made: asyncio.sleep(0)
    runs.check_failure = lambda identity, failure: failure
    runs.tasks = {}

    async def check(_object_id, _args, report=None):
        report([{"result": "fetched"}, {"result": None}], "prices", 120, {"total": 503, "waitingFor": None})
        report([], "ready")
        raise ContractError("unavailable", "stop here")
    runs.check = check
    asyncio.run(runs.check_then_queue("m1", {}, ["r1"]))
    assert seen[0] == {"stage": "data", "progress": {"stage": "prices", "done": 120, "total": 503, "waitingFor": None}}
    assert seen[-1] == {"stage": "check", "progress": {}}
