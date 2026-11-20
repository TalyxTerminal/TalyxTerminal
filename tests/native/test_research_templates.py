"""The three research templates in the research environment on synthetic bars: standard reports, publications and values-mode runs (no future data).

Not part of the daily suite: set TALYX_NATIVE_SMOKE=1 (see environments.py).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import numpy as np
import pandas as pd
import pytest

from modules.quant.native.object_templates import SOURCES
from tests.native.environments import QUANT, environment

pytestmark = pytest.mark.skipif(not os.environ.get("TALYX_NATIVE_SMOKE"), reason="Set TALYX_NATIVE_SMOKE=1 to run native engines")


def prepare(root, kind, mode, cutoffs=None, code=None, bars=600):
    (root / "source").mkdir(parents=True)
    (root / "output").mkdir()
    entry, source = SOURCES[kind]
    (root / "source" / entry).write_text(code or source, encoding="utf-8")
    rng = np.random.default_rng(3)
    index = pd.date_range("2022-01-03", periods=bars, freq="B", tz="UTC").as_unit("ms").asi8
    frames = []
    for number in range(8):
        close = 100 * np.exp(np.cumsum(rng.normal(.0003, .012, bars)))
        opened = np.r_[close[0], close[:-1]]
        frames.append(pd.DataFrame({"column": f"S{number}", "time": index, "label": index, "closeAt": index + 86_400_000, "open": opened, "high": np.maximum(opened, close) * 1.005,
                                    "low": np.minimum(opened, close) * .995, "close": close, "volume": rng.uniform(1e5, 1e6, bars)}))
    for frame in frames:
        frame["rawClose"], frame["rawVolume"] = frame["close"], frame["volume"]
        frame["knownAt"] = frame["closeAt"]
    pd.concat(frames).to_parquet(root / "bars.parquet", index=False)
    info = {"timeframe": "1d", "annualization": 252, "instruments": [{"column": f"S{number}", "instrumentId": f"x:S{number}"} for number in range(8)]}
    invocation = {"runId": "run-test", "title": "t", "entry": entry, "source": str(root / "source"), "output": str(root / "output"), "mode": mode, "params": {},
                  "research": {"barsPath": str(root / "bars.parquet"), "info": info}, "support": None, "common": None, **({"cutoffs": cutoffs} if cutoffs else {})}
    if kind == 'model':
        name, version = 'Momentum', 'momentum-v1'
        features = []
        for number, frame in enumerate(frames):
            values = frame['close'].pct_change(5, fill_method=None).shift(1)
            features.append(pd.DataFrame({'instrument': f'x:S{number}', 'name': name, 'versionId': version,
                'field': 'momentum', 'datetime': index, 'availableAt': frame['closeAt'], 'value': values}).dropna())
        factor_path = root / 'training-factors.parquet'
        pd.concat(features, ignore_index=True).to_parquet(factor_path, index=False)
        invocation['params']['factor'] = name
        invocation['research']['factors'] = {'path': str(factor_path),
            'items': [{'parameter': 'factor', 'name': name, 'versionId': version, 'version': 1}]}
    (root / "invocation.json").write_text(json.dumps(invocation), encoding="utf-8")


def run(root):
    subprocess.run([str(environment("research.lock", "3.12")), "-B", str(QUANT / "native" / "research_worker.py"), str(root / "invocation.json")],
                   cwd=root / "source", capture_output=True, text=True, timeout=900)


def index(root):
    return json.loads((root / "output" / "result-index.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("kind", ["factor", "model", "allocation"])
def test_a_template_runs_to_its_standard_report_and_offers_what_it_publishes(tmp_path, kind):
    prepare(tmp_path, kind, "run")
    run(tmp_path)
    result = index(tmp_path)
    assert result["status"] == "completed", result.get("error")
    assert result["metrics"] and result["publications"]
    assert {item["kind"] for item in result["publications"]} == {"factor": {"factor"}, "model": {"prediction", "model"}, "allocation": {"allocation"}}[kind]


def test_a_values_run_writes_the_values_for_each_cutoff_and_no_report(tmp_path):
    prepare(tmp_path, "factor", "values", cutoffs=[None, 1_650_000_000_000])
    run(tmp_path)
    output = tmp_path / "output"
    assert (output / "done.json").is_file() and not (output / "result-index.json").exists()
    full, cut = (pd.read_parquet(output / f"values-{number}-factor.parquet") for number in (0, 1))
    assert len(cut) < len(full) and cut["datetime"].max() <= 1_650_000_000_000


def test_a_failing_template_names_the_authors_file_and_line(tmp_path):
    prepare(tmp_path, "factor", "run", code="import talyx_quant as q\n\nraise RuntimeError('boom')\n")
    run(tmp_path)
    result = index(tmp_path)
    frame = next(item for item in reversed(result["failure"]["frames"]) if item["root"] == "source")
    assert result["status"] == "failed" and (frame["file"], frame["line"]) == ("factor.py", 3)
    shutil.rmtree(tmp_path / "output")
