import asyncio
from types import SimpleNamespace

import pytest
from talyx_sdk import ContractError

from modules.quant.backend.execution_recipe import RUNTIME, recipe_lock, write_recipe
from modules.quant.backend.native_environment import prepare_lock
from tests.unit.quant_runtime_fakes import adapt


def test_saved_lock_is_verified_and_resolves_bundled_wheels_in_this_checkout(tmp_path):
    lock = "nautilus-trader @ file:./modules/quant/runtime/wheels/native.whl --hash=sha256:abc\nstatsmodels==0.14.6\n"
    recipe = write_recipe(tmp_path, lock, {"pythonVersion": "3.12.13"}, {"digest": "source"}, "statsmodels")
    assert recipe["pythonVersion"] == "3.12.13"
    restored = recipe_lock(recipe)
    assert (RUNTIME / "wheels/native.whl").resolve().as_uri() in restored
    assert "statsmodels==0.14.6" in restored
    (tmp_path / "execution.lock").write_text("statsmodels==0.15.0\n", encoding="utf-8")
    with pytest.raises(ContractError, match="lock saved with this result changed"):
        recipe_lock(recipe)


def test_missing_recipe_never_replaces_old_dependencies_with_current_project():
    with pytest.raises(ContractError, match="no saved dependency lock"):
        recipe_lock({})


def test_environment_submission_identity_includes_python_and_import_checks():
    async def scenario():
        calls = []
        async def submit(args):
            calls.append(args)
            return {"state": "completed", "interpreterPath": "fixed-python"}
        ctx = adapt(SimpleNamespace(platform=SimpleNamespace(python_env=SimpleNamespace(submit=submit))))
        for origin in ({"kind": "bundled-research", "pythonVersion": "3.12"},
                       {"kind": "saved-result", "pythonVersion": "3.12.13"},
                       {"kind": "original-project", "pythonVersion": "3.12.13"},
                       {"kind": "bundled-research", "pythonVersion": "3.12.13"}):
            await prepare_lock(ctx, "same-installed-lock", origin)
        assert calls[1]["submission_key"] == calls[2]["submission_key"]
        assert len({call["submission_key"] for call in calls}) == 3
        assert len({call["lock_digest"] for call in calls}) == 1
    asyncio.run(scenario())
