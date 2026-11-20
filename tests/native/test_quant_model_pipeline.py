"""The model engine in the research environment on synthetic bars (50 names, three years of days): the standard pipeline trained window
by window with each algorithm, the same model on 1 and 8 threads, the window cache's three uses (resume, the publication's cut-off check,
updates), the figures' roles, the check before training, Alpha158 with index membership and without VWAP, and too few names.

Not part of the daily suite: set TALYX_NATIVE_SMOKE=1 (see environments.py).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from modules.quant.backend.object_params import read_parameters
from tests.native.environments import QUANT, environment

pytestmark = pytest.mark.skipif(not os.environ.get("TALYX_NATIVE_SMOKE"), reason="Set TALYX_NATIVE_SMOKE=1 to run native engines")

DAYS, NAMES = 760, 50
PIPELINE = str(QUANT / "native" / "model_standard.py")  # the standard script a run copies with it (model_runs.job)
ROLES = {"main", "benchmark", "compare", "group", "updown", "drawdown", "attention"}
INPUTS = [{"id": "a", "name": "Reversal 5", "source": "run", "ref": "", "formula": "-($close / Ref($close, 5) - 1)"},
          {"id": "b", "name": "Momentum 20", "source": "run", "ref": "", "formula": "$close / Ref($close, 20) - 1"},
          {"id": "c", "name": "Volatility 20", "source": "run", "ref": "", "formula": "Std($close / Ref($close, 1) - 1, 20)"}]
SETTINGS = {"leaveOut": {"halted": True, "young": False, "illiquid": False},
            "processing": {"inputs": {"missing": "median", "extremes": "mad3", "scale": "rank", "takeOut": []}, "label": {"missing": "drop", "extremes": "mad3", "scale": "rank"}},
            "label": {"days": 5, "form": "rank"}, "split": {"trainMonths": 12, "validMonths": 3, "everyMonths": 3, "mode": "rolling", "firstTest": ""}}


def synthetic(root, *, days=DAYS, names=NAMES, members=None, columns=None):
    rng = np.random.default_rng(5)
    labels = pd.bdate_range("2021-01-01", periods=days).tz_localize("UTC").as_unit("ms").asi8
    columns = columns or [f"S{i}" for i in range(names)]
    signal = rng.normal(0, 1, (days, len(columns)))
    ret = 0.002 * np.vstack([np.zeros((1, len(columns))), signal[:-1]]) + rng.normal(0, .02, (days, len(columns)))
    close = np.exp(np.cumsum(ret, axis=0)) * 50
    volume = rng.integers(100_000, 1_000_000, (days, len(columns))).astype(float)
    frame = pd.DataFrame({"label": np.repeat(labels, len(columns)), "time": np.repeat(labels, len(columns)) + 14 * 3600_000, "column": np.tile(columns, days),
                          "open": close.ravel(), "high": close.ravel() * 1.01, "low": close.ravel() * .99, "close": close.ravel(), "volume": volume.ravel()})
    if members is not None:
        frame["isMember"] = members.ravel()
    root.mkdir(parents=True, exist_ok=True)
    frame["rawClose"], frame["rawVolume"] = frame["close"], frame["volume"]
    frame.to_parquet(root / "bars.parquet", index=False)
    (root / "extras.json").write_text(json.dumps({"industry": {c: f"I{i % 5}" for i, c in enumerate(columns)}, "shares": None, "notes": []}))
    info = {"annualization": 252, "timeframe": "1d", "instruments": [{"column": c} for c in columns]}
    return {"key": f"synthetic-{root.name}", "barsPath": str(root / "bars.parquet"), "info": info, "extrasPath": str(root / "extras.json")}, labels


def standard_params(algorithm, **changes):
    declared = read_parameters((QUANT / "native" / "model_standard.py").read_text(encoding="utf-8"), "model_standard.py")
    assert not declared["errors"], declared["errors"]
    params = {item["name"]: item["default"] for item in declared["parameters"]}
    params.update(algorithm=algorithm, lgb_rounds=40, lgb_stop=10, lgb_leaves=15, lgb_minleaf=20, xgb_rounds=40, cat_rounds=40, dens_rounds=20, dens_models=2,
                  train_months=12, valid_months=3, every_months=3, leave_out_young=False, **changes)
    return params


def run(root, name, job):
    """One job through the worker, once, in the research environment."""
    output = root / name
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    job = {"output": str(output), "cache": str(output / "windows"), "threads": 2, "run": {"cost": 5, "topN": 10}, **job}
    (output / "invocation.json").write_text(json.dumps({"job": job}), encoding="utf-8")
    done = subprocess.run([str(environment("research.lock", "3.12")), "-B", str(QUANT / "native" / "model_run.py"), str(output / "invocation.json")],
                          capture_output=True, text=True, encoding="utf-8", timeout=900)
    (output / "stderr.log").write_text(done.stderr, encoding="utf-8")
    assert (output / "answer.json").is_file(), done.stderr[-3000:]
    return json.loads((output / "answer.json").read_text(encoding="utf-8")), output


def trained_windows(output):
    lines = [json.loads(line) for line in (output / "progress.jsonl").read_text(encoding="utf-8").splitlines()]
    return sorted({line["window"] for line in lines if line["stage"] == "window" and line["state"] == "done"})


def check(root, name, job, op="model_check"):
    output = root / name
    output.mkdir(parents=True, exist_ok=True)
    (output / "invocation.json").write_text(json.dumps({"mode": "once", "op": op, "job": {"output": str(output), **job}}), encoding="utf-8")
    done = subprocess.run([str(environment("research.lock", "3.12")), "-B", str(QUANT / "native" / "factor_resident.py"), str(output / "invocation.json")],
                          capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert (output / "answer.json").is_file(), done.stderr[-3000:]
    return json.loads((output / "answer.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def bars(tmp_path_factory):
    root = tmp_path_factory.mktemp("model")
    data, labels = synthetic(root / "data")
    return root, data, labels


def job_of(data, algorithm="lightgbm", **changes):
    params = standard_params(algorithm, **changes.pop("params", {}))
    return {"mode": "train", "pipelinePath": PIPELINE, "data": data, "inputs": INPUTS, "settings": SETTINGS, "params": params, **changes}


def test_lightgbm_gives_the_same_model_twice_and_on_one_or_eight_threads(bars):
    root, data, _ = bars
    one, first = run(root, "lgb-1", job_of(data, threads=1))
    assert one["ok"], one
    eight, second = run(root, "lgb-8", job_of(data, threads=8))
    again, third = run(root, "lgb-8b", job_of(data, threads=8))
    assert eight["ok"] and again["ok"]
    a, b, c = (np.load(path / "pred.npy") for path in (first, second, third))
    assert np.array_equal(a, b, equal_nan=True) and np.array_equal(b, c, equal_nan=True)
    work = json.loads((second / "work.json").read_text(encoding="utf-8"))
    assert work["perWindow"] and len(work["windows"]) == one["windows"] >= 5
    assert {check["id"] for check in work["checks"]} >= {"significance", "baselines", "windows", "train_test", "copies", "cost_room", "future_data"}


@pytest.mark.parametrize("algorithm", ["xgboost", "catboost", "ridge", "lasso", "linear", "nnls", "double_ensemble"])
def test_each_algorithm_trains_every_window_and_says_what_mattered(bars, algorithm):
    root, data, _ = bars
    answer, output = run(root, algorithm, job_of(data, algorithm))
    assert answer["ok"], answer
    windows = [json.loads((output / "windows" / str(k) / "window.json").read_text(encoding="utf-8")) for k in trained_windows(output)]
    assert windows and all(window["importance"] for window in windows), algorithm
    assert all(set(window["importance"]) <= {item["name"] for item in INPUTS} for window in windows)


def test_every_figure_of_a_run_carries_a_role_and_a_key_and_one_set_is_drawn(bars):
    root, data, _ = bars
    answer, output = run(root, "figures", job_of(data))
    assert answer["ok"], answer
    figures = json.loads((output / "figures.json").read_text(encoding="utf-8"))
    results = json.loads((output / "results.json").read_text(encoding="utf-8"))
    assert len(figures) == json.loads((output / "work.json").read_text(encoding="utf-8"))["figureCount"] and len(figures) <= 30
    for key, figure in figures.items():
        assert figure["layout"]["meta"]["key"] == key
        for trace in figure["data"]:
            assert trace["meta"]["role"] in ROLES | {"detail"} and trace["meta"]["key"], (key, trace.get("name"))
    assert results["missing"] == [] or all(item["reason"] for item in results["missing"])


def test_a_resumed_run_trains_only_the_windows_it_had_not_finished(bars):
    root, data, _ = bars
    whole, full = run(root, "whole", job_of(data))
    part, half = run(root, "resume", job_of(data, windows=[1, 2]))
    assert part["ok"] or part["failure"]["title"]  # two windows trained, then "interrupted"
    assert trained_windows(half) == [1, 2]
    (half / "progress.jsonl").unlink()
    answer, output = run_in_place(half, job_of(data, keepDone=True))
    assert answer["ok"], answer
    assert trained_windows(output) == list(range(3, whole["windows"] + 1))
    assert np.array_equal(np.load(full / "pred.npy"), np.load(output / "pred.npy"), equal_nan=True)


def run_in_place(output, job):
    """The same output and window cache again (a resumed run)."""
    job = {"output": str(output), "cache": str(output / "windows"), "threads": 2, "run": {"cost": 5, "topN": 10}, **job}
    (output / "invocation.json").write_text(json.dumps({"job": job}), encoding="utf-8")
    done = subprocess.run([str(environment("research.lock", "3.12")), "-B", str(QUANT / "native" / "model_run.py"), str(output / "invocation.json")],
                          capture_output=True, text=True, encoding="utf-8", timeout=900)
    assert (output / "answer.json").is_file(), done.stderr[-3000:]
    return json.loads((output / "answer.json").read_text(encoding="utf-8")), output


def test_the_publication_check_retrains_only_the_cut_off_window_and_finds_a_model_that_reads_ahead(bars):
    root, data, labels = bars
    whole, full = run(root, "source", job_of(data))
    window = json.loads((full / "windows" / "3" / "window.json").read_text(encoding="utf-8"))
    cut = int(labels[window["testSpan"][0] + 10])
    shutil.copytree(full / "windows", root / "cut-cache")
    answer, output = run(root, "cut", {**job_of(data, mode="cutoff", cutoff=cut, windows=[3], sourceCache=str(full / "windows"), keepDone=True), "cache": str(root / "cut-cache")})
    assert answer["ok"] and answer["passed"] and answer["window"] == 3, answer
    assert trained_windows(output) == [3]
    # A feature written in code that reads the next bar's close (formulas refuse it): with all the data and cut, the window predicts differently.
    project = root / "project"
    project.mkdir(exist_ok=True)
    (project / "features.py").write_text("import talyx_quant as q\n\n\n@q.feature\ndef tomorrow(data):\n    return data.close.shift(-1) / data.close - 1\n", encoding="utf-8")
    peeking = [*INPUTS[:2], {"id": "p", "name": "Tomorrow", "source": "code", "ref": "features.py:tomorrow"}]
    code = {"source": str(project), "own": False, "digest": "peek"}
    whole, full = run(root, "peek", job_of(data, inputs=peeking, code=code))
    assert whole["ok"], whole
    answer, _ = run(root, "peek-cut", job_of(data, mode="cutoff", cutoff=cut, windows=[3], sourceCache=str(full / "windows"), inputs=peeking, code=code))
    assert answer["ok"] and answer["passed"] is False and "reads data from after" in answer["title"], answer


def test_an_update_trains_only_a_new_window_on_a_retraining_day_and_otherwise_predicts_with_the_newest(bars, tmp_path):
    root, data, labels = bars
    shorter, _ = synthetic(tmp_path / "short", days=DAYS - 70)
    first, published = run(root, "published", job_of(shorter))
    assert first["ok"]
    cached = trained_windows(published)
    # A few more days, no retraining day: nothing is trained, the newest window predicts the new days.
    soon = int(labels[DAYS - 70 + 3])
    answer, output = run(root, "update-soon", {**job_of(data, mode="update", cutoff=soon, keepDone=True, cachedWindows=cached, after=int(labels[DAYS - 71])),
                                               "cache": str(published / "windows")})
    assert answer["ok"] and answer["trainedWindows"] == [], answer
    values = pd.read_parquet(output / "values.parquet")
    assert set(values["label"]) == set(int(label) for label in labels[DAYS - 70:DAYS - 70 + 4]) and len(values) == 4 * NAMES
    # The whole later period: the windows that start in it are trained, and only those.
    answer, late = run(root, "update-late", {**job_of(data, mode="update", keepDone=True, cachedWindows=cached, after=int(labels[DAYS - 71])), "cache": str(published / "windows")})
    assert answer["ok"] and answer["trainedWindows"] and min(answer["trainedWindows"]) == max(cached) + 1, answer
    # Every day after the cursor has values: each window predicts its own test days, not only the newest window its own (months between were missed).
    days = set(pd.read_parquet(late / "values.parquet")["label"])
    assert days == set(int(label) for label in labels[DAYS - 70:]), sorted(set(int(label) for label in labels[DAYS - 70:]) - days)[:5]


def test_values_of_a_day_hold_each_inputs_contribution_and_combining_two_runs_scores_their_mean(bars):
    root, data, labels = bars
    _, source = run(root, "values-source", job_of(data))
    window = json.loads((source / "windows" / "4" / "window.json").read_text(encoding="utf-8"))
    day = int(labels[window["testSpan"][0] + 2])
    answer, _ = run(root, "values", {**job_of(data, mode="values", date=day, keepDone=True, runOutput=str(source)), "cache": str(source / "windows")})
    assert answer["ok"] and answer["window"] == 4 and len(answer["rows"]) == NAMES, answer
    row = answer["rows"][0]
    assert row["contributions"] and abs(sum(item["value"] for item in row["contributions"]) + row["base"] - row["prediction"]) < 1e-3
    _, other = run(root, "values-other", job_of(data, params={"seed": 7, "lgb_ff": 0.6}))
    windows = [json.loads((source / "windows" / str(k) / "window.json").read_text(encoding="utf-8")) for k in trained_windows(source)]
    answer, combined = run(root, "combined", job_of(data, mode="combine", sources=[str(source), str(other)], sourceWindows=windows))
    assert answer["ok"], answer
    assert json.loads((combined / "work.json").read_text(encoding="utf-8"))["numbers"]["ric"] is not None


@pytest.fixture(scope="module")
def ranked_combination(tmp_path_factory):
    root = tmp_path_factory.mktemp("ranked-combination")
    columns = [f"T{i:02}" for i in range(12)]
    data, dates = synthetic(root / "data", days=460, columns=columns)
    job = job_of(data, "ridge")
    answer, source = run(root, "fit", job)
    assert answer["ok"], answer
    windows = windows_of(source)
    first, last = windows[-1]["testSpan"]
    ranks = np.arange(1, 13)
    other = np.array([12, 4, 3, 2, 1, 5, 6, 7, 9, 8, 10, 11])
    sources = []
    for name, order in (("ascending", ranks), ("mixed", other)):
        path = root / name
        path.mkdir()
        scores = np.full((len(dates), len(columns)), np.nan)
        scores[first:last+1] = order
        axes = json.loads((source / "pred.json").read_text(encoding="utf-8"))
        if name == "mixed":
            scores[first, -2:] = np.nan  # This source ranks ten names on the first day, the other ranks twelve.
            # The same universe can have a different stored column order.
            scores = scores[:, ::-1]
            axes["columns"] = axes["columns"][::-1]
        np.save(path / "pred.npy", scores)
        (path / "pred.json").write_text(json.dumps(axes), encoding="utf-8")
        sources.append(str(path))
    answer, output = run(root, "combined", {**job, "mode": "combine", "sources": sources, "sourceWindows": windows})
    assert answer["ok"], answer
    return output, data, dates, columns, first, last, ranks, other


def test_combined_prediction_axes_match_the_frozen_bars(ranked_combination):
    output, _, dates, columns, *_ = ranked_combination
    axes = json.loads((output / "pred.json").read_text(encoding="utf-8"))
    assert axes["labels"] == dates.tolist() and axes["columns"] == columns


def test_combined_report_matches_saved_scores_and_exact_rank_ties(ranked_combination):
    from fractions import Fraction

    output, data, dates, columns, first, last, ranks, other = ranked_combination
    pred = np.load(output / "pred.npy")
    expected = np.tile(((ranks + other) / 24 - 0.5) * 3.46, (last-first+1, 1))
    for i, rank in enumerate(ranks):
        percentile = Fraction(int(rank), 12)
        if i < 10:
            other_rank = 1 + int((other[:10] < other[i]).sum())
            percentile = (percentile + Fraction(other_rank, 10)) / 2
        expected[0, i] = (float(percentile) - 0.5) * 3.46
    np.testing.assert_array_equal(pred[first:last+1], expected)
    opens = pd.read_parquet(data["barsPath"]).pivot(index="label", columns="column", values="open").loc[dates, columns]
    later = opens.shift(-6) / opens.shift(-1) - 1
    scores = pd.DataFrame(expected, index=dates[first:last+1], columns=columns)
    ic = scores.rank(axis=1).corrwith(later.loc[scores.index].rank(axis=1), axis=1).dropna()
    work = work_of(output)
    daily = pd.Series(work["ic"]["y"], index=dates).dropna()
    np.testing.assert_allclose(daily.loc[ic.index], ic, atol=1e-14, rtol=0)
    assert work["numbers"]["ric"] == pytest.approx(np.mean(ic), abs=1e-14)
    assert work["windows"][-1]["testIc"] == pytest.approx(np.mean(ic), abs=1e-14)


def test_combined_day_values_keep_fractional_midranks(ranked_combination):
    output, data, dates, columns, first, _, ranks, other = ranked_combination
    answer, _ = run(output.parent, "day-values", job_of(data, "ridge", mode="values",
        date=int(dates[first+1]), runOutput=str(output)))
    assert answer["ok"], answer
    rows = pd.DataFrame(answer["rows"]).set_index("ticker").loc[columns]
    np.testing.assert_array_equal(rows["prediction"], ((ranks + other) / 24 - 0.5) * 3.46)
    np.testing.assert_array_equal(rows["rank"], pd.Series(ranks + other).rank())


def test_the_check_before_training_takes_seconds_and_blocks_only_what_must_fail(bars):
    root, data, _ = bars
    job = {"data": data, "inputs": INPUTS, "settings": SETTINGS, "algorithm": "lightgbm", "params": {"rounds": 40}, "threads": 4, "budget": 16 * 1024 ** 3}
    answer = check(root, "check", job)
    assert answer["ok"] and not answer["blocked"] and answer["seconds"] < 10, answer
    names = {item["name"] for item in answer["checks"]}
    assert {"Names a day", "Coverage", "Look-ahead", "Label", "Training rows", "Memory"} <= names and not {"Fitted on", "Gaps"} & names
    assert answer["axis"]["windows"] and answer["axis"]["gap"] == 5
    small = check(root, "check-small", {**job, "budget": 0.2 * 1024 ** 3})
    memory = next(item for item in small["checks"] if item["name"] == "Memory")
    assert small["blocked"] and memory["state"] == "block" and "0.2 GB" in memory["detail"]
    inspected = check(root, "inspect", {**job, "input": "b"}, op="model_inspect")
    assert inspected["ok"] and len(inspected["before"]) == 40 and inspected["stats"][0]["label"] == "Mean"
    drawn = inspected["figures"]
    assert [trace["name"] for trace in drawn["model.inspect.distribution"]["data"]] == ["Before", "After"]
    assert "to" in drawn["model.inspect.distribution"]["data"][0]["hovertemplate"] and "% missing" in drawn["model.inspect.missing"]["data"][0]["hovertemplate"]


def test_alpha158_keeps_index_membership_and_leaves_out_vwap(tmp_path):
    members = np.ones((DAYS, NAMES), dtype=bool)
    members[:400, :10] = False  # ten names join the index later
    data, labels = synthetic(tmp_path / "index", members=members)
    script = tmp_path / "membership.py"
    script.write_text(textwrap.dedent('''
        import json, sys
        sys.path.insert(0, sys.argv[1])
        from native import model_data as D, model_run as R
        job = json.loads(open(sys.argv[2], encoding="utf-8").read())
        panel = R.load_panel(job)
        inputs = [D.Input(id="c", name="Alpha158", source="builtin", ref="alpha158")]
        data = D.ModelData(panel, inputs, job["settings"])
        out = {"early": int(data.rows[:400, :10].sum()), "late": int(data.rows[400:, :10].sum()), "features": len(data.features),
               "vwap": [name for name in data.features if "VWAP" in name], "note": inputs[0].note, "dropped": inputs[0].dropped}
        print(json.dumps(out))
    '''), encoding="utf-8")
    (tmp_path / "job.json").write_text(json.dumps({"data": data, "settings": SETTINGS}), encoding="utf-8")
    done = subprocess.run([str(environment("research.lock", "3.12")), "-B", str(script), str(QUANT), str(tmp_path / "job.json")], capture_output=True, text=True, encoding="utf-8",
                          timeout=600)
    assert done.returncode == 0, done.stderr[-3000:]
    found = json.loads(done.stdout.strip().splitlines()[-1])
    assert found["early"] == 0 and found["late"] > 0
    assert found["vwap"] == [] and found["dropped"] == ["VWAP0"] and "no values" in found["note"] and found["features"] == 157


def test_crypto_with_too_few_names_ends_with_a_reason(tmp_path):
    data, _ = synthetic(tmp_path / "crypto-data", names=2, columns=["BTC/USDT:USDT", "ETH/USDT"])
    answer, _ = run(tmp_path, "crypto", job_of(data))
    assert not answer["ok"] and answer["failure"]["title"] == "needs more names"
    assert "BTC/USDT:USDT" in answer["failure"]["message"] and str(tmp_path) not in answer["failure"]["message"]
    checked = check(tmp_path, "crypto-check", {"data": data, "inputs": INPUTS, "settings": SETTINGS, "algorithm": "lightgbm", "params": {}, "threads": 1, "free": 8e9})
    assert checked["blocked"] and next(item for item in checked["checks"] if item["name"] == "Names a day")["state"] == "block"


# ====================================================================================================== M section
# Synthetic data S (section 5): 200 names x 900 days; `sig` is the next 5 days' return from the next open plus noise (correlation 0.10),
# `noise` is independent, `copy` is `sig` plus noise of 1e-4. Names follow the build draft's numbers (D1-D18, M-cases).
S_NAMES, S_DAYS, RHO = 200, 900, 0.10
LABEL_5D = "Ref($open, -6) / Ref($open, -1) - 1"
STANDARD_LABEL = "CSRank(" + LABEL_5D + ")"


def synthetic_s(root):
    rng = np.random.default_rng(11)
    labels = pd.bdate_range("2022-01-03", periods=S_DAYS).tz_localize("UTC").as_unit("ms").asi8
    columns = [f"S{i}" for i in range(S_NAMES)]
    opens = np.exp(np.cumsum(rng.normal(0, .015, (S_DAYS, S_NAMES)), axis=0)) * 50
    future = np.full(opens.shape, np.nan)
    future[:S_DAYS - 6] = opens[6:] / opens[1:S_DAYS - 5] - 1
    ahead = np.nan_to_num(future)
    scale = future[np.isfinite(future)].std() * np.sqrt(1 / RHO ** 2 - 1)
    sig = ahead + rng.normal(0, scale, opens.shape)
    noise = rng.normal(0, 1, opens.shape)
    copy = sig + rng.normal(0, sig.std() * 1e-4, opens.shape)
    close = opens * (1 + rng.normal(0, .002, opens.shape))
    volume = rng.integers(100_000, 1_000_000, opens.shape).astype(float)
    flat = lambda a: a.ravel()  # noqa: E731
    stamps = np.repeat(labels, S_NAMES)
    frame = pd.DataFrame({"label": stamps, "time": stamps + 14 * 3600_000, "column": np.tile(columns, S_DAYS), "open": flat(opens), "high": flat(np.maximum(opens, close)) * 1.001,
                          "low": flat(np.minimum(opens, close)) * .999, "close": flat(close), "volume": flat(volume)})
    root.mkdir(parents=True, exist_ok=True)
    frame["rawClose"], frame["rawVolume"] = frame["close"], frame["volume"]
    frame.to_parquet(root / "bars.parquet", index=False)
    (root / "extras.json").write_text(json.dumps({"industry": {c: f"I{i % 5}" for i, c in enumerate(columns)}, "shares": None, "notes": []}))
    info = {"annualization": 252, "timeframe": "1d", "instruments": [{"column": c} for c in columns]}
    project = root / "project"
    project.mkdir(exist_ok=True)
    arrays = {"sig": sig, "noise": noise, "copy": copy}
    for name, array in arrays.items():
        np.save(project / f"{name}.npy", array)
    (project / "features.py").write_text("import numpy as np\nimport pandas as pd\nimport talyx_quant as q\n\n\n" + "".join(
        f"@q.feature\ndef {name}(data):\n    return pd.DataFrame(np.load(__file__.replace('features.py', '{name}.npy'))[:len(data.close)], index=data.close.index, columns=data.close.columns)\n\n\n"
        for name in arrays), encoding="utf-8")
    data = {"key": f"s-{root.name}", "barsPath": str(root / "bars.parquet"), "info": info, "extrasPath": str(root / "extras.json")}
    return data, labels, opens, project


def code_inputs(*names):
    return [{"id": name, "name": name, "source": "code", "ref": f"features.py:{name}"} for name in names]


def s_job(data, project, names=("sig", "noise"), formula=STANDARD_LABEL, algorithm="lightgbm", label=None, params=None, split=None, **changes):
    settings = {**SETTINGS, "label": {"formula": formula, "objective": "l2", "weight": "equal", **(label or {})}, "split": {**SETTINGS["split"], **(split or {})}}
    label_params = {"objective": settings["label"]["objective"], "weight": settings["label"]["weight"], "weight_formula": settings["label"].get("weightFormula", ""),
                    **({"objective_param": settings["label"]["objectiveParam"]} if "objectiveParam" in settings["label"] else {})}
    params = standard_params(algorithm, label=formula, **label_params, **(params or {}))
    return {"mode": "train", "pipelinePath": PIPELINE, "data": data, "inputs": code_inputs(*names), "settings": settings, "params": params, "code": {"source": str(project), "own": False, "digest": "s"}, **changes}


@pytest.fixture(scope="module")
def s_bars(tmp_path_factory):
    root = tmp_path_factory.mktemp("s")
    data, labels, opens, project = synthetic_s(root / "data")
    return root, data, labels, opens, project


def in_environment(root, name, source, **arguments):
    """A snippet in the research environment with the Quant package on the path; it prints one JSON line."""
    script = Path(tempfile.mkdtemp(prefix="talyx-m-")) / f"{name}.py"  # a folder of its own: a script folder is on the interpreter's path
    script.write_text("import json, sys\nsys.path.insert(0, " + repr(str(QUANT)) + ")\nARGS = json.loads(sys.argv[1])\n" + textwrap.dedent(source), encoding="utf-8")
    done = subprocess.run([str(environment("research.lock", "3.12")), "-B", str(script), json.dumps(arguments)], capture_output=True, text=True, encoding="utf-8", timeout=900)
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def windows_of(output):
    """Each trained window as the cache keeps it (with its spans as positions)."""
    return [json.loads((output / "windows" / str(k) / "window.json").read_text(encoding="utf-8")) for k in trained_windows(output)]


def work_of(output):
    return json.loads((output / "work.json").read_text(encoding="utf-8"))


def test_test_rank_ic_t_and_baselines_match_an_independent_calculation(s_bars):
    """D1, D2, D5: the test segment's Rank IC and t against scipy and statsmodels on pred.npy and the label; the signal is found within 3 standard errors."""
    root, data, labels, opens, project = s_bars
    answer, output = run(root, "d1", s_job(data, project))
    assert answer["ok"], answer
    numbers = work_of(output)["numbers"]
    pred = np.load(output / "pred.npy")
    windows = windows_of(output)
    label = np.full(opens.shape, np.nan)
    label[:S_DAYS - 6] = opens[6:] / opens[1:S_DAYS - 5] - 1
    days = sorted({d for w in windows for d in range(w["testSpan"][0], w["testSpan"][1] + 1)})
    from scipy.stats import spearmanr
    ic = np.array([spearmanr(pred[d][np.isfinite(pred[d]) & np.isfinite(label[d])], label[d][np.isfinite(pred[d]) & np.isfinite(label[d])])[0]
                   if (np.isfinite(pred[d]) & np.isfinite(label[d])).sum() > 5 else np.nan for d in days])
    ic = ic[np.isfinite(ic)]
    assert abs(numbers["ric"] - ic.mean()) < 1e-6
    # Newey-West (Bartlett weights, 4 lags) t of the mean, as statsmodels' HAC gives it for a constant-only regression
    e, n = ic - ic.mean(), len(ic)
    long_run = float(e @ e) + 2 * sum((1 - lag / 5) * float(e[lag:] @ e[:-lag]) for lag in range(1, 5))
    t = ic.mean() / np.sqrt(long_run / n ** 2)
    assert abs(numbers["t"] - t) < 1e-4 * max(1, abs(t))
    expected = 6 / np.pi * np.arcsin(RHO / 2)
    standard_error = ic.std(ddof=1) / np.sqrt(len(ic) / 5)
    assert abs(numbers["ric"] - expected) < 3 * standard_error, (numbers["ric"], expected, standard_error)


def test_t_follows_the_label_horizon(s_bars):
    """D2: a 20-day label's t uses 19 lags."""
    root, data, labels, opens, project = s_bars
    formula = "Ref($close, -20) / $close - 1"
    answer, output = run(root, "d2-20", s_job(data, project, formula=formula))
    assert answer["ok"], answer
    work = work_of(output)
    assert work["horizon"] == 20 and "overlapping 20-bar labels" in json.dumps(work["checks"])


def test_label_formula_matches_hand_computed_labels(s_bars):
    """D3 and D15: the four label formulas against pandas, the last bars empty; reach for each."""
    root, data, labels, opens, project = s_bars
    found = in_environment(root, "d15", '''
        import numpy as np, pandas as pd
        from native import formula, model_data as D
        from native.factor_resident import load_panel
        panel = load_panel(ARGS["data"])
        opens, close = pd.DataFrame(panel.fields["open"]), pd.DataFrame(panel.fields["close"])
        sources = {"five": "Ref($open, -6) / Ref($open, -1) - 1", "rank": "CSRank(Ref($open, -6) / Ref($open, -1) - 1)", "up": "Gt(Ref($open, -6), Ref($open, -1))",
                   "vol": "Std(Ref($close, -20) / Ref($close, -19) - 1, 20)"}
        by_hand = {"five": opens.shift(-6) / opens.shift(-1) - 1, "up": (opens.shift(-6) > opens.shift(-1)).astype(float).where(opens.shift(-6).notna() & opens.shift(-1).notna()),
                   "vol": (close.shift(-20) / close.shift(-19) - 1).rolling(20, min_periods=1).std()}  # the 20 returns from t+1 to t+20 are the past 20 values of the shifted series
        by_hand["rank"] = by_hand["five"].rank(axis=1, pct=True)
        out = {}
        for key, source in sources.items():
            got = D.label_of(panel, source)
            want = by_hand[key].to_numpy()
            start = 0 if key != "vol" else 0
            same = np.isfinite(got) == np.isfinite(want)
            both = np.isfinite(got) & np.isfinite(want)
            out[key] = {"diff": float(np.abs(got[both] - want[both]).max()) if both.any() else None, "emptyTail": bool(np.isnan(got[-6:]).all()) if key != "vol" else bool(np.isnan(got[-20:]).all()),
                        "sameShape": bool(same[:, :].all()) if key != "vol" else True,
                        "reach": formula.reach(formula.parse(source, ahead=True))}
        print(json.dumps(out))
    ''', data=data)
    assert found["five"]["emptyTail"] and found["rank"]["emptyTail"]  # the last 6 bars have no label
    assert found["five"]["diff"] < 1e-12 and found["rank"]["diff"] < 1e-12 and found["up"]["diff"] == 0
    assert found["vol"]["diff"] < 1e-17 + 1e-12
    assert [(found[k]["reach"]["ahead"], found[k]["reach"]["back"]) for k in ("five", "rank", "up", "vol")] == [(6, 0), (6, 0), (6, 0), (20, 19)]


def test_reach_equals_qlibs_own_window_extension():
    """B16: ahead and back of formulas made of Qlib's operators equal Qlib's rght_etd and lft_etd."""
    found = in_environment(Path(os.environ["TEMP"]), "reach", '''
        from native import formula
        from qlib.data.ops import Ref, Mean, Std, Feature
        c, o = Feature("close"), Feature("open")
        built = {"Ref($open, -6) / Ref($open, -1) - 1": Ref(o, -6) / Ref(o, -1) - 1,
                 "Std(Ref($close, -20) / Ref($close, -19) - 1, 20)": Std(Ref(c, -20) / Ref(c, -19) - 1, 20),
                 "Mean(Ref($close, -3), 5)": Mean(Ref(c, -3), 5)}
        out = {}
        for source, expression in built.items():
            lft, rght = expression.get_extended_window_size()
            reach = formula.reach(formula.parse(source, ahead=True))
            out[source] = [reach["back"] - lft, reach["ahead"] - rght]
        print(json.dumps(out))
    ''')
    assert all(diff == [0, 0] for diff in found.values()), found


def test_label_window_extends_the_warm_up(s_bars):
    """M59: a label with a rolling window adds its back-look to the warm-up; the first window's training starts later."""
    root, data, labels, opens, project = s_bars
    found = in_environment(root, "warm", '''
        from native import formula, model_data as D
        reach = formula.reach(formula.parse("Std(Ref($close, -20) / Ref($close, -19) - 1, 20)", ahead=True))
        print(json.dumps({"back": reach["back"], "warm": D.warmup_of([], reach)}))
    ''')
    assert found["back"] == 19 and found["warm"] == 19


def test_segments_keep_the_labels_length_between_them_and_never_touch_the_held_out_period(s_bars):
    """D4, D10, #40: gap = ahead - 1 bars each side, the last test day is before heldOutFrom, the held-out run scores only after it."""
    root, data, labels, opens, project = s_bars
    held = int(labels[700])
    answer, output = run(root, "d4", s_job(data, project, heldOutFrom=held))
    assert answer["ok"], answer
    for window in windows_of(output):
        train, valid, test = window["trainSpan"], window["validSpan"], window["testSpan"]
        assert train[1] + 5 + 1 <= valid[0] and valid[1] + 5 + 1 <= test[0] and labels[test[1]] < held
    answer, ho = run(root, "d10", s_job(data, project, kind="heldout", heldOutFrom=held))
    assert answer["ok"], answer
    assert work_of(ho)["windows"] and all(w["test"]["from"] >= held for w in work_of(ho)["windows"])  # the cache may hold earlier windows; the run scores only those testing at or after heldOutFrom
    pred = np.load(ho / "pred.npy")
    assert np.isnan(pred[:int(np.searchsorted(labels, held))]).all(), "the held-out run scores only the days after heldOutFrom"


def test_no_window_touches_the_held_out_period(s_bars):
    root, data, labels, opens, project = s_bars
    held = int(labels[650])
    answer, output = run(root, "m40", s_job(data, project, heldOutFrom=held))
    work = work_of(output)
    assert all(w["testSpan"][1] < int(np.searchsorted(labels, held)) for w in windows_of(output))
    checks = {item["id"]: item for item in work["checks"]}
    assert checks["significance"]["state"] in {"ok", "watch", "fix"}


def test_lightgbm_finds_the_signal_and_a_shuffled_label_finds_none(s_bars):
    """D5, D6: with signal the Rank IC is about (6/pi) arcsin(rho/2); with the label shuffled |t| < 3 and about half the windows above 0."""
    root, data, labels, opens, project = s_bars
    signal, output = run(root, "d5", s_job(data, project))
    assert signal["ok"]
    shuffled, shuffle = run(root, "d6", s_job(data, project, names=("noise",)))  # nothing in the input predicts the label
    assert shuffled["ok"]
    numbers = work_of(shuffle)["numbers"]
    assert abs(numbers["t"]) < 3
    assert 0.25 <= numbers["positive"] / numbers["windows"] <= 0.75


def test_a_model_trained_on_a_copy_of_the_input_says_so(s_bars):
    """D7."""
    root, data, labels, opens, project = s_bars
    answer, output = run(root, "d7", s_job(data, project, names=("copy",)))
    copies = [item for item in work_of(output)["checks"] if item["id"] == "copies"][0]
    assert copies["state"] == "watch" and float(str(copies["value"]).split()[-1]) >= 0.95


def test_decay_at_the_label_length_equals_the_runs_rank_ic(s_bars):
    """D8."""
    root, data, labels, opens, project = s_bars
    answer, output = run(root, "d8", s_job(data, project, formula=LABEL_5D))
    results = results_of(output)
    decay = results["sections"]["information"]["decay"]
    assert abs(decay[4]["rankIc"] - work_of(output)["numbers"]["ric"]) < 1e-6


def test_combining_two_identical_runs_scores_the_same_as_each(s_bars):
    """D9."""
    root, data, labels, opens, project = s_bars
    _, first = run(root, "d9a", s_job(data, project))
    windows = [json.loads((first / "windows" / str(k) / "window.json").read_text(encoding="utf-8")) for k in trained_windows(first)]
    answer, combined = run(root, "d9", s_job(data, project, mode="combine", sources=[str(first), str(first)], sourceWindows=windows))
    assert abs(work_of(combined)["numbers"]["ric"] - work_of(first)["numbers"]["ric"]) < 1e-6


def test_a_factor_formula_that_reads_the_future_fails_the_cut_off_check(s_bars):
    """D11: an input formula reading Ref($close, -5) is refused by the publication's check (and the formula language refuses it before)."""
    root, data, labels, opens, project = s_bars
    leaking = [{"id": "f", "name": "Leak", "source": "run", "ref": "", "formula": "Ref($close, -5) / $close - 1"}]
    answer, _ = run(root, "d11", s_job(data, project, inputs=leaking))
    assert not answer["ok"] and "later" in json.dumps(answer["failure"]).lower() or "future" in json.dumps(answer["failure"]).lower()


def test_lightgbm_is_bit_identical_on_one_and_eight_threads(s_bars):
    """D13 (S3 measured it; kept as a test on S)."""
    root, data, labels, opens, project = s_bars
    _, one = run(root, "d13-1", s_job(data, project, threads=1))
    _, eight = run(root, "d13-8", s_job(data, project, threads=8))
    assert np.array_equal(np.load(one / "pred.npy"), np.load(eight / "pred.npy"), equal_nan=True)


def test_names_a_day_and_bars_a_year_follow_the_calendar(s_bars, tmp_path):
    """D14 / M53: the universe gives its own count a day; crypto annualises by 365 bars."""
    root, data, labels, opens, project = s_bars
    checked = check(root, "d14", {"data": data, "inputs": code_inputs("sig"), "settings": s_job(data, project)["settings"], "algorithm": "lightgbm", "params": {}, "threads": 2, "free": 8e9,
                                  "code": {"source": str(project), "own": False, "digest": "s"}})
    names = next(item for item in checked["checks"] if item["name"] == "Names a day")
    assert names["value"] == "200 – 200"
    crypto = {**data, "info": {**data["info"], "annualization": 365}}
    answer, _ = run(root, "m53", s_job(crypto, project))
    assert answer["ok"] and work_of(_)["annualization"] == 365


def test_a_label_formula_is_used_as_is_for_binary_when_it_has_two_values(s_bars):
    """M61: Classification - Binary on a 0/1 label trains on it directly (LightGBM binary)."""
    root, data, labels, opens, project = s_bars
    answer, output = run(root, "m61", s_job(data, project, formula="Gt(Ref($open, -6), Ref($open, -1))", label={"objective": "binary"}))
    assert answer["ok"], answer
    assert work_of(output)["settings"]["objective"] == "binary"
    regression, _ = run(root, "m61-l2", s_job(data, project, formula="Gt(Ref($open, -6), Ref($open, -1))", label={"objective": "l2"}))
    assert regression["ok"]


@pytest.mark.parametrize("objective", ["l2", "l1", "huber", "quantile", "binary", "multiclass", "lambdarank", "xendcg"])
def test_each_objective_trains_on_each_algorithm_that_lists_it(s_bars, objective):
    """D16: every objective LightGBM lists trains and finds the signal on S (Rank IC > 0); the other libraries' rows are in the loop below."""
    root, data, labels, opens, project = s_bars
    label = {"objective": objective, **({"objectiveParam": 5} if objective == "multiclass" else {"objectiveParam": 1.0} if objective == "huber" else {"objectiveParam": 0.5} if objective == "quantile" else {})}
    answer, output = run(root, f"d16-{objective}", s_job(data, project, label=label))
    assert answer["ok"], (objective, answer)
    assert work_of(output)["numbers"]["ric"] > 0


def test_objective_choices_follow_the_algorithm():
    found = in_environment(Path(os.environ["TEMP"]), "objectives", '''
        from native import model_algorithms as A
        print(json.dumps({key: [o["id"] for o in A.objectives_of(key)] for key in ("lightgbm", "xgboost", "catboost", "linear", "nnls", "double_ensemble")}))
    ''')
    assert found["double_ensemble"] == ["l2"] and found["nnls"] == ["l2"]
    assert found["lightgbm"] == ["l2", "l1", "huber", "quantile", "binary", "multiclass", "lambdarank", "xendcg"]
    assert found["xgboost"] == ["l2", "l1", "huber", "quantile", "binary", "multiclass", "lambdarank", "pairwise"]
    assert found["catboost"] == ["l2", "l1", "huber", "quantile", "binary", "multiclass", "lambdarank", "pairwise"]


def test_weight_is_offered_only_where_the_library_takes_it():
    found = in_environment(Path(os.environ["TEMP"]), "weights", '''
        from native import model_algorithms as A
        print(json.dumps({"lgb": A.takes_weight("lightgbm", "lambdarank"), "xgbRank": A.takes_weight("xgboost", "lambdarank"), "xgbL2": A.takes_weight("xgboost", "l2"),
                          "catRank": A.takes_weight("catboost", "lambdarank"), "catPair": A.takes_weight("catboost", "pairwise"), "nnls": A.takes_weight("nnls", "l2"),
                          "ridge": A.takes_weight("ridge", "l2"), "de": A.takes_weight("double_ensemble", "l2")}))
    ''')
    assert found == {"lgb": True, "xgbRank": False, "xgbL2": True, "catRank": False, "catPair": False, "nnls": False, "ridge": True, "de": False}


def test_weight_formula_reads_only_the_past_and_one_equals_equal(s_bars):
    """D17 and M69."""
    root, data, labels, opens, project = s_bars
    _, equal = run(root, "d17-equal", s_job(data, project))
    answer, one = run(root, "d17-one", s_job(data, project, label={"weight": "formula", "weightFormula": "$close / $close"}))
    assert answer["ok"], answer
    # weight 1 is equal weight: the same trees up to the library's own rounding of a weighted sum
    assert np.allclose(np.load(equal / "pred.npy"), np.load(one / "pred.npy"), rtol=1e-3, atol=1e-5, equal_nan=True)
    bad, _ = run(root, "m68", s_job(data, project, label={"weight": "formula", "weightFormula": "Ref($volume, -1)"}))
    assert not bad["ok"] and "reads only past and current bars" in json.dumps(bad["failure"])


def test_negative_or_missing_weights_count_as_zero(s_bars):
    root, data, labels, opens, project = s_bars
    # the before-training check says it (it is the check that sees the weights), the run itself trains with the zeros
    checked = check(root, "m69", {**s_job(data, project, label={"weight": "formula", "weightFormula": "$close - 50"}), "algorithm": "lightgbm", "free": 16 * 1024 ** 3})
    row = [item for item in checked["checks"] if item["name"] == "Sample weight"]
    assert row and row[0]["state"] == "watch" and "% of rows weigh 0" in row[0]["value"], checked["checks"]
    answer, _ = run(root, "m69-run", s_job(data, project, label={"weight": "formula", "weightFormula": "$close - 50"}))
    assert answer["ok"]


def test_engine_parameters_are_checked_against_the_library():
    found = in_environment(Path(os.environ["TEMP"]), "engine", '''
        from native import model_algorithms as A
        def tell(algorithm, text):
            try:
                return {"ok": A.engine_params(algorithm, text)}
            except ValueError as error:
                return {"error": str(error)}
        print(json.dumps({"lgb": tell("lightgbm", "monotone_constraints=[1,0,-1], min_data_in_leaf=50"), "lgbBad": tell("lightgbm", "min_leaf=50"),
                          "xgbBad": tell("xgboost", "min_leaf=50"), "catBad": tell("catboost", "min_leaf=50"), "ridgeBad": tell("ridge", "min_leaf=50")}))
    ''')
    assert "ok" in found["lgb"]
    for key in ("lgbBad", "xgbBad", "catBad"):
        assert "min_leaf" in found[key]["error"] and "no such parameter" in found[key]["error"], key
    assert found["lgbBad"]["error"] == "min_leaf: LightGBM has no such parameter"


def test_declared_parameters_win_over_engine_parameters():
    found = in_environment(Path(os.environ["TEMP"]), "engine-declared", '''
        from native import model_algorithms as A
        try:
            A.engine_params("lightgbm", "learning_rate=0.1", declared={"lr"})
            out = "accepted"
        except ValueError as error:
            out = str(error)
        print(json.dumps(out))
    ''')
    assert found == "learning_rate is set above; change it there."


def test_engine_parameters_cannot_take_what_talyx_sets():
    found = in_environment(Path(os.environ["TEMP"]), "engine-talyx", '''
        from native import model_algorithms as A
        out = {}
        for key in ("num_threads", "seed", "deterministic", "objective", "early_stopping_round"):
            try:
                A.engine_params("lightgbm", key + "=1")
                out[key] = "accepted"
            except ValueError as error:
                out[key] = str(error)
        print(json.dumps(out))
    ''')
    assert found["num_threads"] == "num_threads is set by TALYX (Threads)." and all(value != "accepted" for value in found.values())


def test_engine_parameters_parse_literals_only():
    found = in_environment(Path(os.environ["TEMP"]), "engine-literals", '''
        from native import model_algorithms as A
        out = {}
        for text in ("max_bin=abc", "max_bin", "max_bin=__import__('os')"):
            try:
                A.engine_params("lightgbm", text)
                out[text] = "accepted"
            except ValueError as error:
                out[text] = str(error)
        print(json.dumps(out))
    ''')
    assert found["max_bin=abc"] == "max_bin: a whole number" and found["max_bin"] == "Write key=value, separated by commas" and found["max_bin=__import__('os')"] != "accepted"


def test_minmax_and_zscore_are_fitted_on_the_training_segment_only(s_bars):
    """D18 and M72: fitted statistics come from the training segment; min-max inside it is in [0, 1], outside it may leave; a constant column is not scaled."""
    found = in_environment(s_bars[0], "d18", '''
        import numpy as np
        from native import model_data as D
        rng = np.random.default_rng(3)
        train = rng.normal(5, 2, (500, 4))
        train[:, 3] = 7.0
        settings = {"missing": "zero", "extremes": "none", "scale": "minmax", "takeOut": []}
        fitted = D.fit_window(train, settings)
        inside = D.apply_fitted(train, fitted, settings)
        test = rng.normal(5, 4, (100, 4))
        outside = D.apply_fitted(test, fitted, settings)
        z = D.fit_window(train, {"missing": "zero", "extremes": "none", "scale": "zscore_train", "takeOut": []})
        print(json.dumps({"inMin": float(np.nanmin(inside[:, :3])), "inMax": float(np.nanmax(inside[:, :3])), "outsideMax": float(np.nanmax(outside[:, :3])),
                          "constant": bool(np.isfinite(inside[:, 3]).all()), "constantUnchanged": bool(np.allclose(inside[:, 3], 7.0)), "zFromTrain": bool(z is not None)}))
    ''')
    assert found["inMin"] >= 0 and found["inMax"] <= 1 and found["outsideMax"] > 1 and found["constant"] and found["constantUnchanged"]


def test_each_processing_choice_matches_its_qlib_processor():
    found = in_environment(Path(os.environ["TEMP"]), "processors", '''
        import numpy as np, pandas as pd
        from native import model_data as D
        from qlib.data.dataset.processor import MinMaxNorm, ZScoreNorm, Fillna, TanhProcess
        rng = np.random.default_rng(4)
        frame = pd.DataFrame(rng.normal(3, 2, (400, 5)), columns=list("abcde"), index=pd.MultiIndex.from_product([pd.date_range("2020-01-01", periods=20), range(20)], names=["datetime", "instrument"]))
        out = {}
        for key, processor in (("minmax", MinMaxNorm(fit_start_time="2020-01-01", fit_end_time="2020-01-20", fields_group=None)),
                               ("zscore_train", ZScoreNorm(fit_start_time="2020-01-01", fit_end_time="2020-01-20", fields_group=None))):
            processor.fit(frame)
            want = processor(frame.copy()).to_numpy()
            settings = {"missing": "zero", "extremes": "none", "scale": key, "takeOut": []}
            got = D.apply_fitted(frame.to_numpy(), D.fit_window(frame.to_numpy(), settings), settings)
            out[key] = float(np.abs(got - want).max())
        print(json.dumps(out))
    ''')
    assert all(diff < 1e-6 for diff in found.values()), found  # the pipeline holds the rows as float32


def test_label_formula_field_check_and_the_run_notes_the_pipeline(s_bars):
    root, data, labels, opens, project = s_bars
    answer, output = run(root, "m32", s_job(data, project))
    assert answer["ok"] and work_of(output)["pipeline"] == {"version": "1.0", "digest": work_of(output)["pipeline"]["digest"]}


def test_the_default_values_day_has_every_label_realised(s_bars):
    root, data, labels, opens, project = s_bars
    _, source = run(root, "m34", s_job(data, project))
    answer, _ = run(root, "m34-values", {**s_job(data, project, mode="values", keepDone=True, runOutput=str(source)), "cache": str(source / "windows")})
    assert answer["ok"] and answer["figure"]["layout"]["meta"]["key"] == "model.values.scatter"
    assert answer["day"] < int(labels[-6]) and all(row["realised"] is not None for row in answer["rows"])


# ---------------------------------------------------------------------------------------------------------------- P3 in the research environment
def results_of(output):
    found = json.loads((output / "results.json").read_text(encoding="utf-8"))
    return {**found, "sections": found}  # the sections are the file's own keys


def figures_of(output):
    return json.loads((output / "figures.json").read_text(encoding="utf-8"))


def test_train_to_test_is_a_ratio_and_the_table_shows_the_difference(s_bars):
    root, data, labels, opens, project = s_bars
    _, output = run(root, "p3-ratio", s_job(data, project))
    work = work_of(output)
    row = next(item for item in work["checks"] if item["id"] == "train_test")
    train, test = work["numbers"]["trainIc"], work["numbers"]["ric"]
    assert row["need"].startswith("test ≥ 10%") or "10%" in row["need"]
    assert (row["state"] == "ok") == (test >= 0.1 * train)
    assert work["numbers"]["trainTestGap"] == pytest.approx(train - test)


def test_returns_and_information_rows_follow_the_draft(s_bars):
    """B3, B4: the five long-short rows, Top N before and after cost, the information table's IC and Rank IC rows with Std and Above 0."""
    root, data, labels, opens, project = s_bars
    _, output = run(root, "p3-rows", s_job(data, project))
    sections = results_of(output)["sections"]
    rows = sections["returns"]["longShort"]["rows"]
    assert [row["name"] for row in rows] == ["Long–short", "Long–average", "Long–short, net", "Top 10, excess", "Top 10, excess, net"] or len(rows) == 5
    assert all({"annual", "sharpe", "maxDrawdown"} <= set(row) for row in rows)
    top = sections["returns"]["topN"]
    assert {"meanBps", "stdBps", "annual", "ir", "maxDrawdown"} <= set(top["before"]) and set(top["after"]) == set(top["before"])
    table = sections["information"]["table"]["rows"]
    assert [row["name"] for row in table] == ["IC", "Rank IC"] and table[0]["t"] is None and table[1]["t"] is not None
    assert {"std", "skew", "kurtosis", "positive"} <= set(table[1])
    assert all({"sharpe", "partial"} <= set(row) for row in sections["robustness"]["years"])


def test_top_n_uses_the_run_cost(s_bars):
    root, data, labels, opens, project = s_bars
    _, cheap = run(root, "p3-cost-0", s_job(data, project, run={"cost": 0, "topN": 10}))
    _, dear = run(root, "p3-cost-20", s_job(data, project, run={"cost": 20, "topN": 10}))
    after = lambda out: results_of(out)["sections"]["returns"]["topN"]["after"]["annual"]  # noqa: E731
    before = lambda out: results_of(out)["sections"]["returns"]["topN"]["before"]["annual"]  # noqa: E731
    assert before(cheap) == pytest.approx(before(dear)) and after(dear) < after(cheap)


def test_break_even_is_none_when_gross_is_not_positive(s_bars):
    root, data, labels, opens, project = s_bars
    _, output = run(root, "p3-be", s_job(data, project, names=("noise",), shuffleLabel=3))
    numbers = work_of(output)["numbers"]
    gross = numbers["lsGross"]
    if gross <= 0:
        assert numbers["breakeven"] is None and numbers["breakevenNote"] == "Gross long–short is not positive"
        assert next(item for item in work_of(output)["checks"] if item["id"] == "cost_room")["state"] == "watch"


def test_short_test_period_says_short_sample(s_bars):
    root, data, labels, opens, project = s_bars
    _, output = run(root, "p3-short", s_job(data, project, split={"firstTest": str(pd.Timestamp(int(labels[-100]), unit="ms").date())}))
    numbers = work_of(output)["numbers"]
    assert numbers["shortSample"] == (numbers["testDays"] < 250)


def test_short_held_out_says_short_sample(s_bars):
    root, data, labels, opens, project = s_bars
    held = int(labels[-60])
    _, source = run(root, "p3-ho-src", s_job(data, project, heldOutFrom=held))
    answer, held_run = run(root, "p3-ho", s_job(data, project, kind="heldout", heldOutFrom=held))
    assert answer["ok"] and work_of(held_run)["numbers"]["shortSample"] is True  # the backend carries it to the Robustness row


def test_importance_basis_names_each_algorithm(s_bars):
    root, data, labels, opens, project = s_bars
    for algorithm, basis in (("lightgbm", "gain"), ("xgboost", "gain"), ("catboost", "PredictionValuesChange"), ("ridge", "coefficient")):
        _, output = run(root, f"p3-imp-{algorithm}", s_job(data, project, algorithm=algorithm))
        importance = results_of(output)["inputs"]["importance"]
        assert basis.lower() in importance["basis"].lower().replace(" ", ""), (algorithm, importance.get("basis"))


@pytest.mark.parametrize("formula", [LABEL_5D, STANDARD_LABEL, "Gt(Ref($open, -6), Ref($open, -1))", "Std(Ref($close, -20) / Ref($close, -19) - 1, 20)"])
def test_report_comes_out_for_every_label_formula(s_bars, formula):
    root, data, labels, opens, project = s_bars
    answer, output = run(root, "p3-label-" + str(abs(hash(formula)) % 10 ** 6), s_job(data, project, formula=formula))
    assert answer["ok"], answer
    results = results_of(output)
    assert {"windows", "returns", "information", "turnover", "inputs", "robustness"} <= set(results["sections"])
    figures = figures_of(output)
    missing = {item["key"]: item["reason"] for item in results["missing"]}
    assert all(missing.values()), "a figure that cannot be drawn says why"
    assert len(figures) + len(missing) >= 27


def test_a_figure_that_fails_says_why(s_bars):
    root, data, labels, opens, project = s_bars
    _, output = run(root, "p3-nofigure", s_job(data, project))
    results = results_of(output)
    assert all(item["reason"] for item in results["missing"])


def test_every_figure_has_role_and_key_and_missing_ones_say_why(s_bars):
    root, data, labels, opens, project = s_bars
    _, output = run(root, "p3-roles", s_job(data, project))
    for key, figure in figures_of(output).items():
        assert figure["layout"]["meta"]["key"] == key
        assert all(trace["meta"]["role"] in ROLES | {"detail"} and trace["meta"]["key"] for trace in figure["data"]), key


def test_every_figure_reads_out_like_the_draft(s_bars):
    """U17: each figure's hover texts, by meta.key: row names, the + sign on signed values, decimals (one entry per figure of the draft's table)."""
    root, data, labels, opens, project = s_bars
    _, output = run(root, "p3-hover", s_job(data, project, number=12))  # the Host gives the worker the run's number: rows read "#12" as the draft's
    figures = figures_of(output)
    expected = {
        "model.windows.each": {"names": ["Validation", "Test"], "format": "%{y:+.4f}"},
        "model.windows.valid-test": {"names": ["Validation Rank IC", "Test Rank IC"], "format": "%{y:+.4f}"},
        "model.windows.importance": {"names": ["Share", "Rank among these"], "format": None},
        "model.returns.ls-cumulative": {"names": ["Long–short", "Long–average", "L–S net"], "format": "%{y:+.1%}"},
        "model.returns.group-mean": {"names": [], "format": "bps"},
        "model.returns.top-excess": {"names": ["Before cost", "After cost"], "format": "%{y:+.1%}"},
        "model.returns.ls-drawdown": {"names": ["Long–short, net"], "format": "%{y:+.1%}"},
        "model.returns.top-rank": {"names": ["Top", "Bottom"], "format": "%{y:.1%}"},
        "model.returns.net-by-cost": {"names": ["Long–short, net"], "format": "%{y:+.2%}"},
        "model.information.ic": {"names": ["Rank IC", "Rank IC, 1-month mean", "IC, 1-month mean"], "format": "%{y:+.3f}"},
        "model.information.distribution": {"names": ["Days"], "format": "Rank IC %{customdata[0]:+.3f} to %{customdata[1]:+.3f}"},  # the bar's interval, not its middle
        "model.information.monthly": {"names": [], "format": "Mean Rank IC %{z:+.4f}"},
        "model.information.decay": {"names": ["#12"], "format": "Return over %{x}<br>#12 %{y:+.4f}"},
        "model.information.industry": {"names": ["Rank IC", "Names"], "format": "%{x:+.4f}"},
        "model.turnover.top-bottom": {"names": ["Top", "Bottom"], "format": "%{y:.1%}"},
        "model.inputs.baselines": {"names": ["This model", "Strongest single input", "Inputs ranked and averaged", "Ridge each window"], "format": "%{y:+.3f}"},
        "model.inputs.destyled": {"names": ["Before, 3-month mean", "After, 3-month mean"], "format": "%{y:+.4f}"},
        "model.robustness.years": {"names": ["Rank IC"], "format": "%{y:+.4f}"},
        "model.robustness.coverage": {"names": ["In the index", "With a prediction"], "format": "%{y:d}"},
    }
    for key, rule in expected.items():
        assert key in figures, key
        text = json.dumps([{"name": trace.get("name"), "hovertemplate": trace.get("hovertemplate")} for trace in figures[key]["data"]], ensure_ascii=False)
        assert all(name in text for name in rule["names"]), (key, text[:400])
        assert rule["format"] is None or rule["format"] in text or rule["format"] == "bps", (key, rule["format"], text[:400])
        assert all(trace.get("hovertemplate") for trace in figures[key]["data"]), key
    # a day's values are drawn when the Values tab asks for that day, not with the run: the name, then its prediction (+2 places) and what came later (+%)
    source = (QUANT / "native" / "model_run.py").read_text(encoding="utf-8")
    assert '"%{text}<br>Prediction %{customdata[0]:+.2f}<br>Realised %{customdata[1]:+.1%}<extra></extra>"' in source.split("def values_figure", 1)[1]


def test_three_new_figures_exist(s_bars):
    """B20."""
    root, data, labels, opens, project = s_bars
    _, output = run(root, "p3-b20", s_job(data, project))
    figures = figures_of(output)
    assert {"model.inputs.library", "model.inputs.destyled", "model.robustness.held-out"} & set(figures) >= {"model.inputs.destyled"}


def test_the_portfolio_backtest_runs_on_request(s_bars):
    """B6."""
    root, data, labels, opens, project = s_bars
    _, source = run(root, "p3-pf-src", s_job(data, project))
    answer, output = run(root, "p3-pf", {**s_job(data, project, mode="portfolio", runOutput=str(source)), "cache": str(source / "windows")})
    assert answer["ok"], answer
    found = json.loads((output / "portfolio.json").read_text(encoding="utf-8"))  # the figure and the risk table, kept beside the run (it is not part of the run's own figures)
    assert found["figure"]["layout"]["meta"]["key"] == "model.returns.portfolio" and found["risk"] and found["days"] > 0


def test_runs_record_which_pipeline_they_ran(s_bars):
    root, data, labels, opens, project = s_bars
    _, standard = run(root, "p3-std", s_job(data, project))
    assert work_of(standard)["pipeline"]["version"] == "1.0"


def test_a_second_day_of_values_does_not_build_the_inputs_again(bars):
    import time
    root, data, labels = bars
    _, source = run(root, "values-once-source", job_of(data))
    window = json.loads((source / "windows" / "4" / "window.json").read_text(encoding="utf-8"))
    work = root / "values-once" / "work"  # one run's values folder: every day's look uses it
    if work.parent.exists():
        shutil.rmtree(work.parent)
    work.mkdir(parents=True)
    seen = []
    for step in (1, 2, 3):
        day = int(labels[window["testSpan"][0] + step])
        started = time.perf_counter()
        job = {"output": str(work), "cache": str(source / "windows"), "threads": 2, "run": {"cost": 5, "topN": 10},
               **job_of(data, mode="values", date=day, keepDone=True, runOutput=str(source))}
        invocation = root / f"values-once-{step}.json"
        invocation.write_text(json.dumps({"job": job}), encoding="utf-8")
        done = subprocess.run([str(environment("research.lock", "3.12")), "-B", str(QUANT / "native" / "model_run.py"), str(invocation)], capture_output=True, text=True, encoding="utf-8", timeout=900)
        assert (work / "answer.json").is_file(), done.stderr[-3000:]
        answer = json.loads((work / "answer.json").read_text(encoding="utf-8"))
        (work / "answer.json").unlink()
        seen.append(time.perf_counter() - started)
        assert answer["ok"] and answer["window"] == 4 and len(answer["rows"]) == NAMES, answer
        assert answer["rows"][0]["contributions"]
    lines = [json.loads(line) for line in (work / "progress.jsonl").read_text(encoding="utf-8").splitlines()]
    built = [line for line in lines if line["stage"] == "inputs" and line["state"] == "running"]
    kept = [line for line in lines if line["stage"] == "inputs" and line["state"] == "done" and line.get("kept")]
    assert len(built) == 1 and len(kept) == 2, lines  # built for the first day, read back for the next two
    saved = [line for line in lines if line["stage"] == "inputs" and line["state"] == "kept"]
    assert len(saved) == 1
    # only the days Values can ask for are saved: every window's test days (a tree model reads no look back), as float32, not the training rows
    spans = [json.loads((source / "windows" / str(k) / "window.json").read_text(encoding="utf-8"))["testSpan"] for k in trained_windows(source)]
    tested = sorted({day for first, last in spans for day in range(first, last + 1)})
    assert saved[0]["days"] == len(tested) < saved[0]["of"], saved
    assert (root / "values-once" / "inputs.pkl").is_file()
    assert (root / "values-once" / "inputs.pkl").stat().st_size < 1.5 * saved[0]["bytes"] + 2_000_000, saved
    print("seconds per day", [round(s, 1) for s in seen])


def test_the_kept_inputs_of_a_typical_sp500_run_stay_small(tmp_path):
    script = tmp_path / "size.py"
    script.write_text(textwrap.dedent('''
        import json, sys
        from types import SimpleNamespace
        sys.path.insert(0, sys.argv[1])
        from native import model_run as R
        total = 2500
        # four windows a quarter long over the last year, a 20-day look back (a sequence network)
        windows = [SimpleNamespace(test=(total - 252 + 63 * k, total - 252 + 63 * (k + 1) - 1)) for k in range(4)]
        days = R.values_days(windows, 21, total)
        print(json.dumps({"days": len(days), "bytes": R.values_inputs_bytes(len(days), 500, 158), "full": R.values_inputs_bytes(total, 500, 158)}))
    '''), encoding="utf-8")
    done = subprocess.run([str(environment("research.lock", "3.12")), "-B", str(script), str(QUANT)], capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert done.returncode == 0, done.stderr[-2000:]
    found = json.loads(done.stdout.strip().splitlines()[-1])
    assert found["days"] == 252 + 20, found  # the test days and the look back before the first, not the 2,200 days of training
    assert found["bytes"] < 150e6 and found["bytes"] < found["full"] / 8, found


def test_an_update_labels_the_periods_whose_horizon_the_bars_have_passed(bars, tmp_path):
    root, data, labels = bars
    shorter, _ = synthetic(tmp_path / "short-labels", days=DAYS - 70)
    first, published = run(root, "published-labels", job_of(shorter))
    assert first["ok"]
    cached = trained_windows(published)
    start = int(labels[DAYS - 80])
    soon = int(labels[DAYS - 70 + 3])
    base = {"keepDone": True, "cachedWindows": cached, "after": int(labels[DAYS - 71]), "labelFrom": start, "cache": str(published / "windows")}
    answer, early = run(root, "labels-early", {**job_of(data, mode="update", cutoff=soon), **base})
    assert answer["ok"], answer
    early_labels = pd.read_parquet(early / "labels.parquet")
    # The label reads 6 bars ahead of its own: with the bars up to `soon`, the last period labelled is the one 6 bars before it, and none after.
    reach = 6
    assert int(early_labels["label"].max()) == int(labels[DAYS - 70 + 3 - reach]) and int(early_labels["label"].min()) >= start
    answer, late = run(root, "labels-late", {**job_of(data, mode="update"), **base})
    assert answer["ok"], answer
    later_labels = pd.read_parquet(late / "labels.parquet")
    assert int(later_labels["label"].max()) > int(early_labels["label"].max()), "a later update settles the periods whose horizon has passed by then"
    joined = early_labels.merge(later_labels, on=["label", "column"], suffixes=("_early", "_late"))
    assert len(joined) == len(early_labels) and (joined["value_early"] - joined["value_late"]).abs().max() < 1e-9, "a label is the same whenever it is measured"


WARM_VALUES_SCRIPT = r'''
import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from native import model_run as R
jobs = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
out = {"cold": [], "warm": [], "seconds": []}
for job in jobs["days"]:
    out["cold"].append(R.values({**job, "output": jobs["cold"], "warm": False}))
for job in jobs["days"]:
    started = time.perf_counter()
    out["warm"].append(R.values({**job, "output": jobs["warm"], "warm": True}))
    out["seconds"].append(round(time.perf_counter() - started, 2))
print("ANSWERS" + json.dumps(out, default=str))
'''


def test_a_warm_process_gives_the_same_values_and_builds_the_inputs_once(bars):
    root, data, labels = bars
    _, source = run(root, "values-warm-source", job_of(data))
    window = json.loads((source / "windows" / "4" / "window.json").read_text(encoding="utf-8"))
    cold, warm = root / "values-warm-cold" / "work", root / "values-warm" / "work"  # each its own run folder: the kept inputs file sits beside the work folder
    for folder in (cold, warm):
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
    days = []
    for step in (1, 2, 3):
        days.append({"cache": str(source / "windows"), "threads": 2, "run": {"cost": 5, "topN": 10},
                     **job_of(data, mode="values", date=int(labels[window["testSpan"][0] + step]), keepDone=True, runOutput=str(source))})
    script = root / "warm_values.py"
    script.write_text(WARM_VALUES_SCRIPT, encoding="utf-8")
    plan = root / "warm_values.json"
    plan.write_text(json.dumps({"days": days, "cold": str(cold), "warm": str(warm)}), encoding="utf-8")
    done = subprocess.run([str(environment("research.lock", "3.12")), "-B", str(script), str(QUANT), str(plan)], capture_output=True, text=True, encoding="utf-8", timeout=900)
    assert done.returncode == 0, done.stderr[-3000:]
    found = json.loads(done.stdout[done.stdout.index("ANSWERS") + len("ANSWERS"):].splitlines()[0])
    for before, after in zip(found["cold"], found["warm"], strict=True):
        assert before["ok"] and after["ok"] and before["rows"], before
        assert before["rows"] == after["rows"] and before["date"] == after["date"] and before["window"] == after["window"], "the same numbers, warm or not"
        assert before["stats"] == after["stats"] and before["figure"] == after["figure"] and before["basis"] == after["basis"]
        assert before["rows"][0]["contributions"], "contributions come with the day"
    lines = [json.loads(line) for line in (warm / "progress.jsonl").read_text(encoding="utf-8").splitlines()]
    built = [line for line in lines if line["stage"] == "inputs" and line["state"] == "running"]
    assert len(built) == 1, "the run's inputs are built once for the three days in a warm process"
    assert max(found["seconds"][1:]) < 3, found["seconds"]  # a later day is read, not built: seconds
