"""Data kept across versions: the steps from the stored format up, resumable, never over newer data."""

from __future__ import annotations

import asyncio
import json

import pytest
from talyx_sdk.errors import ContractError
from talyx_sdk.upgrade import bring_up_to_date, bring_up_to_date_now, stored_version


def test_data_without_a_format_is_version_one_and_every_later_step_runs_in_order(tmp_path):
    ran = []
    steps = {2: lambda: ran.append(2), 3: lambda: ran.append(3)}
    assert bring_up_to_date_now(tmp_path, 3, steps) == [2, 3]
    assert ran == [2, 3] and stored_version(tmp_path) == 3
    assert bring_up_to_date_now(tmp_path, 3, steps) == [] and ran == [2, 3]


def test_a_step_that_fails_runs_again_next_time_from_where_it_stopped(tmp_path):
    ran, failing = [], {"step": True}

    def third():
        if failing["step"]:
            raise OSError("disk full")
        ran.append(3)
    steps = {2: lambda: ran.append(2), 3: third}
    with pytest.raises(OSError):
        bring_up_to_date_now(tmp_path, 3, steps)
    assert stored_version(tmp_path) == 2
    failing["step"] = False
    assert bring_up_to_date_now(tmp_path, 3, steps) == [3] and ran == [2, 3]


def test_data_of_a_newer_format_is_left_alone_and_says_so(tmp_path):
    (tmp_path / "format.json").write_text(json.dumps({"version": 4}), encoding="utf-8")
    with pytest.raises(ContractError) as error:
        asyncio.run(bring_up_to_date(tmp_path, 2, {2: lambda: None}))
    assert error.value.code == "data_too_new" and "format 4" in error.value.message
    assert stored_version(tmp_path) == 4


def test_a_format_must_have_one_step_for_each_later_version(tmp_path):
    with pytest.raises(ValueError):
        bring_up_to_date_now(tmp_path, 3, {3: lambda: None})
