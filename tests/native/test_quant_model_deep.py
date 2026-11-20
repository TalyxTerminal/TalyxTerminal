"""Neural networks in the model pipeline, in the deep learning environment (CPU build).

Not part of the daily suite: set TALYX_NATIVE_SMOKE=1 (see environments.py); TALYX_DEEP_PYTHON points at an environment installed from deep-cpu.lock.
"""
from __future__ import annotations

import json
import os
import shutil

import numpy as np
import pytest

from tests.native import test_quant_model_pipeline as P
from tests.native.environments import environment

pytestmark = pytest.mark.skipif(not os.environ.get("TALYX_NATIVE_SMOKE"), reason="Set TALYX_NATIVE_SMOKE=1 to run native engines")
SEQUENCE = ["lstm", "gru", "alstm", "transformer", "localformer", "tcn", "sfm", "krnn", "sandwich", "gats"]
CROSS = ["mlp", "tabnet"]
SMALL = dict(nn_epochs=3, nn_stop=5, nn_batch=100, nn_width=16, nn_layers=1, nn_lookback=5, nn_pretrain=1, nn_lr=0.001)


def run(root, name, job):
    output = root / name
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    job = {"output": str(output), "cache": str(output / "windows"), "threads": 2, "run": {"cost": 5, "topN": 10}, **job}
    (output / "invocation.json").write_text(json.dumps({"job": job}), encoding="utf-8")
    import subprocess
    done = subprocess.run([str(environment("deep-cpu.lock", "3.12")), "-B", str(P.QUANT / "native" / "model_run.py"), str(output / "invocation.json")],
                          capture_output=True, text=True, encoding="utf-8", timeout=1800)
    (output / "stderr.log").write_text(done.stderr, encoding="utf-8")
    assert (output / "answer.json").is_file(), done.stderr[-3000:]
    return json.loads((output / "answer.json").read_text(encoding="utf-8")), output


@pytest.fixture(scope="module")
def bars(tmp_path_factory):
    root = tmp_path_factory.mktemp("deep")
    data, _ = P.synthetic(root / "data", days=420, names=20)
    return root, data


def job_of(data, algorithm, **changes):
    return P.job_of(data, algorithm, params={**SMALL, **changes.pop("params", {})}, **changes)


@pytest.mark.parametrize("algorithm", SEQUENCE + CROSS + ["igmtf", "add", "adarnn", "tra"])
def test_each_network_trains_and_predicts_on_cpu(bars, algorithm):
    root, data = bars
    answer, output = run(root, algorithm, job_of(data, algorithm, params={"nn_lookback": 12} if algorithm == "tra" else {}))
    assert answer["ok"], answer
    pred = np.load(output / "pred.npy")
    assert np.isfinite(pred[~np.isnan(pred)]).all() and (~np.isnan(pred)).sum() > 0
    window = json.loads(next((output / "windows").glob("*/window.json")).read_text(encoding="utf-8"))
    assert window["curve"]["unit"] == "epoch" and 1 <= window["best"] <= 3
    assert (next((output / "windows").glob("*")) / "model.pt").is_file()


def test_lstm_reports_every_epoch_and_names_the_inputs(bars):
    root, data = bars
    answer, output = run(root, "lstm-progress", job_of(data, "lstm"))
    assert answer["ok"], answer
    lines = [json.loads(line) for line in (output / "progress.jsonl").read_text(encoding="utf-8").splitlines()]
    epochs = [line for line in lines if line["stage"] == "window" and line.get("epoch")]
    assert epochs and {"trainLoss", "validLoss", "bestLoss", "bestEpoch", "epochs"} <= set(epochs[-1])
    window = json.loads(next((output / "windows").glob("*/window.json")).read_text(encoding="utf-8"))
    assert set(window["importance"]) <= {item["name"] for item in P.INPUTS}


def test_same_seed_same_network(bars):
    root, data = bars
    _, first = run(root, "seed-a", job_of(data, "gru"))
    _, second = run(root, "seed-b", job_of(data, "gru"))
    assert np.array_equal(np.load(first / "pred.npy"), np.load(second / "pred.npy"), equal_nan=True)


def test_network_engine_parameters_are_checked_against_the_class():
    import subprocess
    code = ("from native import model_algorithms as A\n"
            "assert A.engine_params('transformer', 'reg=0.01') == {'reg': 0.01}\n"
            "for text, message in (('GPU=0', 'set by TALYX'), ('batch_size=3', 'set by TALYX'), ('foo=1', 'has no such parameter')):\n"
            "    try:\n        A.engine_params('gru', text)\n    except ValueError as error:\n        assert message in str(error)\n    else:\n        raise SystemExit(text)\n")
    done = subprocess.run([str(environment("deep-cpu.lock", "3.12")), "-B", "-c", code], cwd=str(P.QUANT), env={**os.environ, "PYTHONPATH": str(P.QUANT)}, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-1500:]


def test_permutation_and_integrated_gradients_find_the_signal():
    import subprocess
    code = '''
import numpy as np
from native import model_deep as D
rng = np.random.default_rng(1)
days, names, F, L = 120, 40, 3, 3
x = rng.standard_normal((days, names, F)).astype(np.float32)
y = (0.8 * x[:, :, 0] + rng.standard_normal((days, names)) * 0.6).astype(np.float32)
def part(a, b):
    t, i = np.meshgrid(np.arange(a, b), np.arange(names), indexing="ij")
    t, i = t.ravel(), i.ravel()
    return {"t": t, "i": i, "y": y[t, i]}
tr, va = part(5, 90), part(90, 120)
src = lambda t, i: D.sequences(x, t, i, L)
p = {"epochs": 15, "stop": 6, "batch": 200, "width": 16, "layers": 1, "lr": 0.01}
m, c, b = D.fit_network("gru", {**tr, "x": src(tr["t"], tr["i"])}, {**va, "x": src(va["t"], va["i"])}, p, d_feat=F, lookback=L, seed=1)
f = D.NeuralFitted("gru", m, ["sig", "n1", "n2"], c, b, params=p, d_feat=F, lookback=L, seed=1)
f.source = src
f.valid_rows = {k: va[k] for k in ("t", "i", "y")}
share = f.importance()
assert share["sig"] > 0.9 and share["n1"] < 0.1 * share["sig"], share
rows = src(va["t"][:40], va["i"][:40])
ig = D.integrated_gradients(f, rows)
gap = D.predict(m, "gru", rows) - ig[:, -1]
assert np.median(np.abs(ig[:, :-1].sum(1) - gap) / np.abs(gap)) < 0.02
assert np.abs(ig[:, :-1]).mean(0).argmax() == 0
assert np.array_equal(ig, D.integrated_gradients(f, rows))
'''
    done = subprocess.run([str(environment("deep-cpu.lock", "3.12")), "-B", "-c", code], cwd=str(P.QUANT), env={**os.environ, "PYTHONPATH": str(P.QUANT)}, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-1500:]


def test_sequences_read_the_look_back_features_outside_oldest_first():
    import subprocess
    code = '''
import numpy as np
from native import model_deep as D
rng = np.random.default_rng(0)
x = rng.standard_normal((30, 7, 4)).astype(np.float32)
t, i = rng.integers(0, 30, 20), rng.integers(0, 7, 20)
L = 5
out = D.sequences(x, t, i, L)
for n in range(20):
    for f in range(4):
        for j in range(L):
            assert out[n, f * L + j] == x[max(t[n] - L + 1 + j, 0), i[n], f]
'''
    done = subprocess.run([str(environment("deep-cpu.lock", "3.12")), "-B", "-c", code], cwd=str(P.QUANT), env={**os.environ, "PYTHONPATH": str(P.QUANT)}, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-1500:]


def test_alpha360_alone_feeds_sixty_days_as_the_sequence(bars):
    root, data = bars
    job = job_of(data, "lstm", params={"nn_epochs": 2})
    job["inputs"] = [{"id": "a", "name": "Alpha360", "source": "builtin", "ref": "alpha360"}]
    answer, output = run(root, "alpha360", job)
    assert answer["ok"], answer
    import subprocess
    model = next((output / "windows").glob("*/model.pt"))
    code = f"import torch; found = torch.load(r'{model}', weights_only=False); assert (found['d_feat'], found['lookback']) == (5, 60), (found['d_feat'], found['lookback'])"
    done = subprocess.run([str(environment("deep-cpu.lock", "3.12")), "-B", "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-800:]


# ---------------------------------------------------------------------------------------------------------------- the networks Qlib's own fit trains
QLIB = ["igmtf", "add", "adarnn", "tra", "tcts", "hist"]
FIT_SCRIPT = r'''
import json, sys
import numpy as np
sys.path.insert(0, sys.argv[1])
from native import model_deep as ND
name, workdir, same_names = sys.argv[2], sys.argv[3], sys.argv[4] == "same"
rng = np.random.default_rng(0)
N, D, L, F = 24, 90, 6, 4
names = [f"S{n:03d}" for n in range(N)]
def part(first, last):
    t = np.repeat(np.arange(first, last), N)
    i = np.tile(np.arange(N), last - first)
    if not same_names:
        keep = rng.random(len(t)) < 0.9
        t, i = t[keep], i[keep]
    x = rng.standard_normal((len(t), F * L)).astype(np.float32)
    y = (0.2 * x[:, 0] + rng.standard_normal(len(t))).astype(np.float32)
    return {"t": t, "i": i, "x": x, "y": y, "aux": [y * 0.9, y * 0.8]}
train, valid, test = part(0, 60), part(62, 78), part(80, 90)
aux = {"train": train["aux"], "valid": valid["aux"]} if name == "tcts" else None
concepts = {"matrix": (rng.random((N, 5)) < 0.3).astype(np.float32)} if name == "hist" else None
seen = []
params = {"epochs": 2, "stop": 2, "batch": 300, "lr": 0.001, "width": 8, "layers": 1, "dropout": 0.0, "lookback": L, "states": 3, "domains": 2, "steps": 1, "metric": "ic" if name == "add" else "loss"}
model, curve, best = ND.fit_qlib(name, train, valid, params, d_feat=F, lookback=L, seed=1, threads=2, workdir=workdir, on_epoch=lambda *a: seen.append(a), names=names, horizon=2,
                                aux=aux, concepts=concepts)
fitted = ND.QlibFitted(name, model, [f"f{n}" for n in range(F)], curve, best, params=params, d_feat=F, lookback=L, seed=1, names=names, horizon=2)
fitted.source = lambda t, i: test["x"] if len(t) == len(test["t"]) else np.zeros((len(t), F * L), dtype=np.float32)
fitted.context = ({k: train[k] for k in ("x", "t", "i", "y")}, {k: valid[k] for k in ("x", "t", "i", "y")})
pred = fitted.predict_rows(test["t"], test["i"])
ND.save(fitted, workdir + "/model.pt")
loaded = ND.load(workdir + "/model.pt")
loaded.source = fitted.source
again = loaded.predict_rows(test["t"], test["i"])
print(json.dumps({"rows": len(pred), "finite": int(np.isfinite(pred).sum()), "epochs": len(seen), "best": best, "curve": len(curve["valid"]), "same_after_load": bool(np.allclose(pred, again))}))
'''


@pytest.mark.parametrize("name", QLIB)
def test_qlib_fit_networks_train_predict_and_reload(tmp_path, name):
    import subprocess
    script = tmp_path / "fit.py"
    script.write_text(FIT_SCRIPT, encoding="utf-8")
    for same in ("same", "gaps"):
        if same == "gaps" and name != "igmtf":
            continue
        done = subprocess.run([str(environment("deep-cpu.lock", "3.12")), "-B", str(script), str(P.QUANT), name, str(tmp_path / f"w-{same}"), same],
                              capture_output=True, text=True, encoding="utf-8", timeout=900)
        assert done.returncode == 0, done.stderr[-3000:]
        found = json.loads(done.stdout.strip().splitlines()[-1])
        assert found["rows"] == found["finite"] > 0 and found["epochs"] >= 1 and found["curve"] >= 1 and found["same_after_load"], found


def test_tcts_trains_with_auxiliary_labels_from_the_main_label(bars):
    root, data = bars
    answer, output = run(root, "tcts", job_of(data, "tcts"))
    assert answer["ok"], answer
    pred = np.load(output / "pred.npy")
    assert (~np.isnan(pred)).sum() > 0 and np.isfinite(pred[~np.isnan(pred)]).all()


def test_hist_without_concept_data_stops_before_training(bars):
    root, data = bars
    answer, _ = run(root, "hist", job_of(data, "hist"))
    assert not answer["ok"] and "concept" in json.dumps(answer["failure"]).lower()


# ---------------------------------------------------------------------------------------------------------------- running out of memory
OOM_SCRIPT = r'''
import json, sys
from types import SimpleNamespace
import numpy as np
sys.path.insert(0, sys.argv[1])
from native import model_deep as ND
from native import model_run as MR
rng = np.random.default_rng(0)
def part(n):
    return {"t": np.repeat(np.arange(n // 10), 10), "i": np.tile(np.arange(10), n // 10), "x": rng.standard_normal((n, 12)).astype(np.float32), "y": rng.standard_normal(n).astype(np.float32)}
train, valid = part(400), part(100)
params = {"epochs": 10, "stop": 10, "batch": 100, "lr": 0.001, "width": 8, "layers": 1, "lookback": 3}
out = {"kinds": [ND.oom_kind(MemoryError()), ND.oom_kind(RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")), ND.oom_kind(RuntimeError("DefaultCPUAllocator: not enough memory")),
                 ND.oom_kind(RuntimeError("shape mismatch"))]}
for message in ("CUDA out of memory. Tried to allocate 2.00 GiB", "[enforce fail] not enough memory: you tried to allocate 9 GiB"):
    original = ND.NETWORKS
    calls = {"n": 0}
    real = ND.build
    def building(*a, **k):
        model = real(*a, **k)
        train_epoch = model.train_epoch
        def failing(*x, **y):
            calls["n"] += 1
            if calls["n"] == 3:
                raise RuntimeError(message)
            return train_epoch(*x, **y)
        model.train_epoch = failing
        return model
    ND.build = building
    calls["n"] = 0
    try:
        ND.fit_network("lstm", train, valid, params, d_feat=4, lookback=3)
        out.setdefault("raised", []).append(None)
    except ND.DeepFailure as error:
        failure = error.failure
        holder = SimpleNamespace(job={"memoryEstimate": 3 * 2**30}, data=SimpleNamespace(windows=[0] * 12))
        holder.out_of_memory = lambda *a, **k: MR.Training.out_of_memory(holder, *a, **k)
        shown = MR.Training.deep_failure(holder, failure, SimpleNamespace(k=5))
        out.setdefault("raised", []).append({"failure": failure, "shown": shown})
    ND.build = real
print(json.dumps(out, default=str))
'''


def test_failure_names_the_epoch_it_can_resume_from(tmp_path):
    import subprocess
    script = tmp_path / "oom.py"
    script.write_text(OOM_SCRIPT, encoding="utf-8")
    done = subprocess.run([str(environment("deep-cpu.lock", "3.12")), "-B", str(script), str(P.QUANT)], capture_output=True, text=True, encoding="utf-8", timeout=900)
    assert done.returncode == 0, done.stderr[-3000:]
    found = json.loads(done.stdout.strip().splitlines()[-1])
    assert found["kinds"] == ["memory", "gpu-memory", "memory", None]
    gpu, host = found["raised"]
    assert gpu["failure"]["kind"] == "gpu-memory" and gpu["failure"]["epoch"] == 3 and gpu["failure"]["saved"] == 2 and gpu["failure"]["batch"] == 100
    assert gpu["shown"]["title"] == "Ran out of GPU memory in window 5, epoch 3" and gpu["shown"]["tryBatch"] == 50 and "Window 5 saved epoch 2." in gpu["shown"]["detail"]
    assert host["shown"]["title"] == "Ran out of memory in window 5 of 12, epoch 3" and host["failure"]["kind"] == "memory"


BASE_SCRIPT = r'''
import json, sys
import torch
sys.path.insert(0, sys.argv[1])
from native import model_deep as ND
params = {"epochs": 1, "width": 8, "layers": 1, "batch": 100, "lr": 0.001}
out = {}
lstm = ND.build("lstm", params, 4, 3, 1)
torch.save({"state": ND.state_of(lstm, "lstm")}, sys.argv[2])
gats = ND.build("gats", {**params, "base_model": "LSTM"}, 4, 3, 2)
before = {k: v.clone() for k, v in ND.network_of(gats, "gats").state_dict().items() if k.startswith("rnn.")}
ND.start_from_base(gats, sys.argv[2])
after = ND.network_of(gats, "gats").state_dict()
base = ND.state_of(lstm, "lstm")
out["copied"] = all(torch.equal(after[k], base[k]) for k in before)
out["changed"] = any(not torch.equal(after[k], before[k]) for k in before)
wide = ND.build("gats", {**params, "width": 16, "base_model": "LSTM"}, 4, 3, 2)
try:
    ND.start_from_base(wide, sys.argv[2])
    out["mismatch"] = None
except ND.DeepFailure as error:
    out["mismatch"] = error.failure["title"]
print(json.dumps(out))
'''


def test_start_from_copies_the_base_rnn(tmp_path):
    import subprocess
    script = tmp_path / "base.py"
    script.write_text(BASE_SCRIPT, encoding="utf-8")
    done = subprocess.run([str(environment("deep-cpu.lock", "3.12")), "-B", str(script), str(P.QUANT), str(tmp_path / "base.pt")], capture_output=True, text=True, encoding="utf-8", timeout=600)
    assert done.returncode == 0, done.stderr[-3000:]
    found = json.loads(done.stdout.strip().splitlines()[-1])
    assert found["copied"] and found["changed"] and found["mismatch"] == "Start from a run with the same inputs and look back"


QLIB_START_SCRIPT = r'''
import json, os, sys, tempfile
import torch
sys.path.insert(0, sys.argv[1])
from native import model_deep as ND
params = {"epochs": 1, "width": 8, "layers": 1, "lr": 0.001, "base_model": "LSTM", "model_path": "base.pt"}
out = {}
work = tempfile.mkdtemp()
import numpy as np
for name in ("igmtf", "add", "hist"):
    held = {"matrix_file": os.path.join(work, "m.npy"), "index_file": os.path.join(work, "i.json")} if name == "hist" else None
    if held:
        np.save(held["matrix_file"], np.zeros((740, 3), dtype=np.float32))
        json.dump({"A": 0}, open(held["index_file"], "w"))
    model = ND.build_qlib(name, params, 4, 3, 1, workdir=work, concepts=held)
    out[name] = [model.base_model, model.model_path]
print(json.dumps(out))
'''


def test_igmtf_add_and_hist_start_from_a_base_through_model_path(tmp_path):
    import subprocess
    script = tmp_path / "start.py"
    script.write_text(QLIB_START_SCRIPT, encoding="utf-8")
    done = subprocess.run([str(environment("deep-cpu.lock", "3.12")), "-B", str(script), str(P.QUANT)], capture_output=True, text=True, encoding="utf-8", timeout=900)
    assert done.returncode == 0, done.stderr[-3000:]
    found = json.loads(done.stdout.strip().splitlines()[-1])
    assert found == {name: ["LSTM", "base.pt"] for name in ("igmtf", "add", "hist")}


IG_QLIB_SCRIPT = r'''
import json, sys
import numpy as np
import torch
sys.path.insert(0, sys.argv[1])
from native import model_deep as ND
name, workdir = sys.argv[2], sys.argv[3]
rng = np.random.default_rng(0)
N, D, L, F = 24, 90, 6, 4
names = [f"S{n:03d}" for n in range(N)]
daily = rng.standard_normal((D, N, F)).astype(np.float32)
def part(first, last):
    t = np.repeat(np.arange(first, last), N)
    i = np.tile(np.arange(N), last - first)
    # a row's inputs are the last L days of each field, oldest first: the window a sequence network reads from the daily rows
    x = np.stack([daily[t - L + 1 + j, i, :] for j in range(L)], axis=2).reshape(len(t), F * L).astype(np.float32)
    y = (0.3 * x[:, L - 1] + rng.standard_normal(len(t))).astype(np.float32)
    return {"t": t, "i": i, "x": x, "y": y, "aux": [y * 0.9, y * 0.8]}
train, valid, test = part(6, 60), part(62, 78), part(80, 90)
aux = {"train": train["aux"], "valid": valid["aux"]} if name == "tcts" else None
concepts = {"matrix": (rng.random((N, 5)) < 0.4).astype(np.float32)} if name == "hist" else None
params = {"epochs": 2, "stop": 2, "batch": 300, "lr": 0.001, "width": 8, "layers": 1, "dropout": 0.0, "lookback": L, "states": 3, "domains": 2, "steps": 1, "metric": "ic" if name == "add" else "loss"}
model, curve, best = ND.fit_qlib(name, train, valid, params, d_feat=F, lookback=L, seed=1, threads=2, workdir=workdir, on_epoch=lambda *a: None, names=names, horizon=2, aux=aux, concepts=concepts)
fitted = ND.QlibFitted(name, model, [f"f{n}" for n in range(F)], curve, best, params=params, d_feat=F, lookback=L, seed=1, names=names, horizon=2)
day = test["t"] == 85
t, i, x = test["t"][day], test["i"][day], test["x"][day]
fitted.source = lambda tt, ii: x
fitted.context = ({k: train[k] for k in ("x", "t", "i", "y")}, {k: valid[k] for k in ("x", "t", "i", "y")})
got = fitted.contributions_rows(t, i)
forward = ND.qlib_forward(fitted, x, t, i)
with torch.no_grad():
    own = forward(torch.from_numpy(x)).numpy()
    base = float(got[0, F])
actual = fitted.predict_array(test["x"], test["t"], test["i"])[day]  # as the run predicted it: the whole test period, so each row has its window
again = fitted.contributions_rows(t, i)
rows = got[:, :F].sum(axis=1) - (own - got[:, F])
print(json.dumps({"shape": list(got.shape), "finite": bool(np.isfinite(got).all()), "nonzero": bool(np.abs(got[:, :F]).sum() > 0),
                  "complete": float(np.max(np.abs(rows)) / (np.max(np.abs(own - got[:, F])) + 1e-9)), "faithful": float(np.max(np.abs(own - actual))),
                  "scale": float(np.max(np.abs(actual))), "same": bool(np.array_equal(got, again))}))
'''


@pytest.mark.parametrize("name", QLIB)
def test_every_qlib_fit_network_has_contributions_that_add_up(tmp_path, name):
    import subprocess
    script = tmp_path / "ig.py"
    script.write_text(IG_QLIB_SCRIPT, encoding="utf-8")
    done = subprocess.run([str(environment("deep-cpu.lock", "3.12")), "-B", str(script), str(P.QUANT), name, str(tmp_path / "w")], capture_output=True, text=True, encoding="utf-8", timeout=900)
    assert done.returncode == 0, done.stderr[-3000:]
    found = json.loads(done.stdout.strip().splitlines()[-1])
    assert found["shape"] == [24, 5] and found["finite"] and found["nonzero"], found
    assert found["complete"] < 0.02, found  # each row: its inputs' contributions add up to its prediction less the day-average baseline's
    assert found["faithful"] < 1e-4 * max(found["scale"], 1.0), found  # the function integrated is the network's own prediction
    assert found["same"], found
IGMTF_MEMORY_SCRIPT = r'''
import json, sys
import numpy as np
sys.path.insert(0, sys.argv[2])
from native import model_deep as ND
from scipy.stats import spearmanr
rng = np.random.default_rng(3)
N, D, L, F = 30, 380, 6, 4
names = [f"S{n:03d}" for n in range(N)]
daily = rng.standard_normal((D, N, F)).astype(np.float32)
regime = np.cumsum(rng.standard_normal(D)) * 0.1  # a slow market state the days differ by
daily += regime[:, None, None]
def part(first, last):
    t = np.repeat(np.arange(first, last), N); i = np.tile(np.arange(N), last - first)
    x = np.stack([daily[t - L + 1 + j, i, :] for j in range(L)], axis=2).reshape(len(t), F * L).astype(np.float32)
    y = (0.4 * (x[:, L - 1] - x[:, 0]) + 0.3 * rng.standard_normal(len(t))).astype(np.float32)
    return {"t": t, "i": i, "x": x, "y": y}
train, valid, test = part(6, 260), part(262, 300), part(302, 380)
params = {"epochs": 3, "stop": 3, "batch": 300, "lr": 0.003, "width": 16, "layers": 1, "dropout": 0.0, "lookback": L}
model, curve, best = ND.fit_qlib("igmtf", train, valid, params, d_feat=F, lookback=L, seed=1, threads=2, workdir=sys.argv[1], on_epoch=lambda *a: None, names=names, horizon=2)
f = ND.QlibFitted("igmtf", model, [f"f{n}" for n in range(F)], curve, best, params=params, d_feat=F, lookback=L, seed=1, names=names, horizon=2)
f.context = ({k: train[k] for k in ("x", "t", "i", "y")}, {k: valid[k] for k in ("x", "t", "i", "y")})
full = f.predict_array(test["x"], test["t"], test["i"])
f.tail = f._context(keep_days=120)
short = f.predict_array(test["x"], test["t"], test["i"])
cors = [spearmanr(full[test["t"] == d], short[test["t"] == d])[0] for d in range(302, 380)]
mem_days = len(set(train["t"]))
print(json.dumps({"min": float(np.nanmin(cors)), "mean": float(np.nanmean(cors)), "days": len(cors), "train_days": mem_days}))
'''


def test_igmtf_keeping_120_days_predicts_as_keeping_all(tmp_path):
    import subprocess
    script = tmp_path / "memory.py"
    script.write_text(IGMTF_MEMORY_SCRIPT, encoding="utf-8")
    done = subprocess.run([str(environment("deep-cpu.lock", "3.12")), "-B", str(script), str(tmp_path / "w"), str(P.QUANT)], capture_output=True, text=True, encoding="utf-8", timeout=900)
    assert done.returncode == 0, done.stderr[-3000:]
    found = json.loads(done.stdout.strip().splitlines()[-1])
    assert found["train_days"] > 120 and found["days"] == 78
    assert found["min"] >= 0.99, found  # a saved window's 120 days of memory predict as the run's full memory did
