"""Resolving user packages against the bundled research and strategy locks with uv (needs network).

Not part of the daily suite: set TALYX_NATIVE_SMOKE=1 (see environments.py).
"""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest
from talyx_sdk import ContractError

from modules.quant.backend.packages import BASE_LOCKS, UserPackages, lock_blocks

pytestmark = pytest.mark.skipif(not os.environ.get("TALYX_NATIVE_SMOKE"), reason="Set TALYX_NATIVE_SMOKE=1 to run native engines")


def packages(tmp_path):
    common = SimpleNamespace(root=tmp_path / "common")
    common.root.mkdir()
    return UserPackages(SimpleNamespace(), SimpleNamespace(root=tmp_path / "records"), common, SimpleNamespace())


@pytest.mark.parametrize("environment", ["research", "strategy"])
def test_a_package_is_added_to_each_lock_and_a_pinned_version_is_refused_naming_the_package(tmp_path, environment):
    async def scenario():
        user = packages(tmp_path)
        base = lock_blocks(BASE_LOCKS[environment].read_text(encoding="utf-8"))
        merged = lock_blocks(await user.resolve(environment, ["ta"]))
        assert set(merged) - set(base) == {"ta"} and all(merged[name][0] == base[name][0] for name in base)
        with pytest.raises(ContractError) as error:
            await user.resolve(environment, ["pandas==2.2.0"])
        assert error.value.code == "conflict" and f"{environment} environment" in error.value.message and "pandas" in error.value.message
    asyncio.run(scenario())
