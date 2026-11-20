import json
from types import SimpleNamespace

from modules.quant.backend.model_runs import ModelRuns


def record(**extra):
    return {"id": "run-2", "mode": "standard", "objectId": "obj", "number": 2, "runKind": "heldout", "state": "completed", "stage": "done", "createdAt": 1,
            "settings": {"params": {"algorithm": "lgbm"}, "data": {}}, **extra}


def results_of(tmp_path, rec):
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "results.json").write_text(json.dumps({"robustness": {}}), encoding="utf-8")
    fake = SimpleNamespace(read=lambda identity: rec, directory=lambda identity: tmp_path, environment_of=lambda r: {})
    return ModelRuns.results(fake, rec["id"])


def test_a_held_out_run_opens_its_own_results(tmp_path):
    # The held-out run's record names its source ({of, ofNumber, numbers}); its Results read 'Private route failed' (KeyError 'number').
    out = results_of(tmp_path, record(heldOut={"of": "run-1", "ofNumber": 1, "numbers": {"ric": 0.02, "t": 1.5}}))
    held = out["results"]["robustness"]["heldOut"]
    assert held["number"] == 2 and held["numbers"]["ric"] == 0.02


def test_a_source_run_names_its_held_out_run(tmp_path):
    out = results_of(tmp_path, record(number=1, runKind="single", heldOut={"runId": "run-2", "number": 2, "state": "completed", "numbers": {"ric": 0.01}}))
    assert out["results"]["robustness"]["heldOut"]["number"] == 2
