"""The SDK's uv tool: environments are named by what they are made of, and one with a holder is never pruned."""
import asyncio
import json

from talyx_sdk.uv import Environments


def _completed(environments: Environments, lock: str) -> str:
    async def submit():
        environments.uv = None   # no real preparation: it fails at once, then the record is marked completed by hand
        state = environments.submit(python_version="3.13", requirements_lock=lock, verify_imports=["json"])
        await asyncio.gather(*environments.running.values(), return_exceptions=True)
        return state["preparationId"]
    identity = asyncio.run(submit())
    path = environments._record_path(identity)
    record = json.loads(path.read_text())
    record.update(state="completed", updatedAt=0)
    path.write_text(json.dumps(record))
    (environments.root / identity[4:24]).mkdir()
    return identity


def test_the_same_lock_is_the_same_environment(tmp_path):
    environments = Environments(tmp_path)

    async def twice():
        environments.uv = None
        first = environments.submit(python_version="3.13", requirements_lock="a==1\n", verify_imports=["a"])
        await asyncio.gather(*environments.running.values(), return_exceptions=True)
        second = environments.submit(python_version="3.13", requirements_lock="a==1\n", verify_imports=["a"])
        return first, second
    first, second = asyncio.run(twice())
    assert first["preparationId"] == second["preparationId"] and second["duplicate"]
    assert environments.get(first["preparationId"])["state"] == "failed"   # no uv here: says so instead of hanging


def test_held_environments_survive_pruning(tmp_path):
    environments = Environments(tmp_path)
    kept, dropped = _completed(environments, "a==1\n"), _completed(environments, "b==1\n")
    environments.hold(kept, "quant:library-version:v1")
    assert environments.prune(min_age_days=0) == [dropped]
    assert environments.get(kept)["state"] == "completed"
    environments.release(kept, "quant:library-version:v1")
    assert environments.prune(min_age_days=0) == [kept]
