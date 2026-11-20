"""A cash account that cannot pay an order's commission refuses or trims the order instead of breaking the account.

Not part of the daily suite: it runs the compiled Nautilus runtime. On this machine:

    $env:TALYX_NATIVE_SMOKE = "1"; .venv\\Scripts\\python.exe -B -m pytest tests/native/test_nautilus_fees.py -s

The environment is installed from `strategy.lock` with uv into %LOCALAPPDATA%\\TALYX\\native-smoke (reused while
the lock is unchanged); TALYX_NATIVE_PYTHON points at an interpreter that already has the wheel instead.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("TALYX_NATIVE_SMOKE"), reason="Set TALYX_NATIVE_SMOKE=1 to run native engines")

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "modules" / "quant" / "runtime"
SCENARIOS = Path(__file__).with_name("nautilus_fee_scenarios.py")


def interpreter():
    if os.environ.get("TALYX_NATIVE_PYTHON"):
        return Path(os.environ["TALYX_NATIVE_PYTHON"])
    lock = (RUNTIME / "strategy.lock").read_text(encoding="utf-8").replace("file:./modules/quant/runtime/", (RUNTIME.as_uri() + "/"))
    key = hashlib.sha256(("3.13.13" + lock).encode()).hexdigest()[:16]
    target = Path(os.environ["LOCALAPPDATA"]) / "TALYX" / "native-smoke" / f"fees-{key}"
    python = target / "Scripts" / "python.exe"
    if not (target / "ready").is_file():
        shutil.rmtree(target, ignore_errors=True)
        uv = shutil.which("uv")
        subprocess.run([uv, "venv", "--python", "3.13.13", str(target)], check=True)
        (target / "requirements.lock").write_text(lock, encoding="utf-8")
        subprocess.run([uv, "pip", "install", "--python", str(python), "-r", str(target / "requirements.lock")], check=True)
        (target / "ready").write_text("", encoding="utf-8")
    return python


def scenario(kind, info):
    completed = subprocess.run([str(interpreter()), "-I", str(SCENARIOS), kind, info], capture_output=True, text=True, timeout=180)
    assert completed.returncode == 0, completed.stderr[-3000:]
    return json.loads(completed.stdout.strip().splitlines()[-1])


def amounts(result):
    return {code: Decimal(text.split()[0]) for code, text in result["balances"].items()}


@pytest.mark.parametrize("kind", ["spot", "equity", "sandbox"])
def test_buy_that_the_estimated_fee_does_not_cover_is_denied(kind):
    result = scenario(kind, "with-fee-info")
    assert result["error"] is None
    assert "OrderDenied" in result["events"] and "OrderFilled" not in result["events"]
    assert all(Decimal(order["filled"]) == 0 for order in result["orders"])
    assert all(value >= 0 for value in amounts(result).values())


@pytest.mark.parametrize("kind", ["spot", "equity", "sandbox"])
def test_buy_the_account_cannot_fully_pay_is_filled_in_part_and_the_rest_canceled(kind):
    result = scenario(kind, "no-fee-info")
    assert result["error"] is None
    (order,) = result["orders"]
    assert order["status"] == "CANCELED"
    assert 0 < Decimal(order["filled"]) < Decimal(str(result["requested"]))
    assert all(value >= 0 for value in amounts(result).values())
