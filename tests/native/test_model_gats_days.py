"""GATs predicts one day at a time: a name's prediction is the network run on that day's rows only, and the rows of a later day never move it.

Not part of the daily suite: set TALYX_NATIVE_SMOKE=1; runs in the deep environment (TALYX_DEEP_PYTHON may point at one installed elsewhere)."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.native.environments import QUANT, environment

pytestmark = pytest.mark.skipif(not os.environ.get("TALYX_NATIVE_SMOKE"), reason="Set TALYX_NATIVE_SMOKE=1 to run native engines")
ROOT = Path(__file__).resolve().parents[2]


def test_gats_predicts_each_day_on_its_own_rows():
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(QUANT), str(ROOT)])}
    done = subprocess.run([str(environment("deep-cpu.lock", "3.12")), "-B", "-W", "ignore", str(ROOT / "tests" / "fixtures" / "model" / "gats_days.py")], capture_output=True, text=True,
                          env=env, timeout=900)
    assert done.returncode == 0, done.stderr[-2000:]
    found = json.loads(done.stdout.strip().splitlines()[-1])
    assert found["mixed_diff"] > 1e-4                  # one pass over all days would have mixed them
    assert found["diff"] < 1e-6 and found["first_days_unmoved"] == 0.0
    assert found["refused"] == "GATs predicts one day at a time: give the day of each row"
