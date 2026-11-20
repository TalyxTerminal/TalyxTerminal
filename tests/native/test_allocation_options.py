"""The allocation choices against skfolio: the standard file's literal lists equal what skfolio offers, the excluded classes are absent, what each
objective accepts is read from its parameters, and the Engine parameters line is checked."""
from __future__ import annotations

import json
import os
import subprocess

import pytest

from modules.quant.backend.object_params import read_parameters
from tests.native.environments import QUANT, environment

pytestmark = pytest.mark.skipif(not os.environ.get("TALYX_NATIVE_SMOKE"), reason="Set TALYX_NATIVE_SMOKE=1 to run native engines")
SCRIPT = '''
import json
from native import allocation_options as O
from skfolio import RiskMeasure
out = {"measures": [m for m, _ in O.risk_measures()], "caps": {k: O.capabilities(k) for k in O.OBJECTIVES}, "classes": sorted({v[1] for v in O.OBJECTIVES.values()})}
try:
    O.parse_engine_parameters("max_weight=0.2", O.objective_class("min_risk"), set())
except ValueError as e: out["bad_key"] = str(e)
try:
    O.parse_engine_parameters("max_weights=0.2", O.objective_class("min_risk"), {"max_weights"})
except ValueError as e: out["dup"] = str(e)
out["ok"] = O.parse_engine_parameters("risk_free_rate=0.02; l2_coef=0.1", O.objective_class("min_risk"), set())
print(json.dumps(out))
'''


def run():
    env = {**os.environ, "PYTHONPATH": str(QUANT)}
    done = subprocess.run([str(environment("research.lock", "3.12")), "-B", "-W", "ignore", "-c", SCRIPT], capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr[-1500:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def declared():
    return {p["name"]: p for p in read_parameters((QUANT / "native" / "allocation_standard.py").read_text(encoding="utf-8"))["parameters"]}


def test_choices_match_skfolio():
    out, params = run(), declared()
    assert [o["value"] for o in params["risk_measure"]["options"]] == out["measures"]
    assert [o["value"] for o in params["objective"]["options"]] == list(out["caps"])


def test_excluded_choices_absent():
    out = run()
    assert not {"StackingOptimization", "DistributionallyRobustCVaR"} & set(out["classes"])


def test_capabilities_read_from_the_class():
    caps = run()["caps"]
    assert json.loads((QUANT / "native" / "allocation_capabilities.json").read_text(encoding="utf-8")) == caps   # the file the page reads is skfolio's
    assert caps["max_utility"]["returns"] and caps["max_return"]["costAware"] and not caps["min_risk"]["returns"]
    assert caps["min_risk"]["short"] and caps["min_risk"]["count"] and not caps["risk_parity"]["short"] and not caps["hrp"]["count"]
    assert not caps["equal_weight"]["robustReturns"] and caps["min_risk"]["robustCovariance"]


def test_engine_parameters_validated():
    out = run()
    assert "is not a parameter" in out["bad_key"] and "max_weights" in out["bad_key"] or "did you mean" in out["bad_key"]
    assert "set by a control" in out["dup"] and out["ok"] == {"risk_free_rate": 0.02, "l2_coef": 0.1}
