"""Standard 14: a pure unit test runs in under 2 s from pytest's start, and the shared setup imports no platform or module."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PURE = "tests/unit/test_linkage_vocabulary.py"
PROBE = (
    "import json, sys, pytest\n"
    "code = pytest.main([sys.argv[1], '-q', '-p', 'no:cacheprovider', '--basetemp', sys.argv[2]])\n"
    "print('MODULES=' + json.dumps(sorted({name.split('.')[0] for name in sys.modules})))\n"
    "raise SystemExit(code)\n"
)


@pytest.mark.timing
def test_a_pure_unit_test_starts_and_finishes_within_two_seconds(tmp_path):
    started = time.perf_counter()
    done = subprocess.run([sys.executable, "-B", "-c", PROBE, PURE, str(tmp_path / "pt")], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)
    elapsed = time.perf_counter() - started
    assert done.returncode == 0, done.stdout + done.stderr
    assert elapsed < 2.0, f"{elapsed:.2f} s"


def test_the_shared_setup_imports_no_platform_or_module(tmp_path):
    done = subprocess.run([sys.executable, "-B", "-c", PROBE, PURE, str(tmp_path / "pt")], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    loaded = set(json.loads(done.stdout.split("MODULES=", 1)[1]))
    assert not loaded & {"kernel", "host", "modules", "talyx_sdk", "langsmith"}
