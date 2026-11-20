"""Research identity, valuation and settlement rules across factor and model workflows."""
import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from talyx_sdk import ContractError

from modules.quant.backend import factor_runs, library_after, notebook_routes
from modules.quant.backend.model_runs import ModelRuns
from modules.quant.backend.project_paths import local_path, source_identity
from modules.quant.native import factor_work, metrics, model_data, model_figures
from tests.unit.quant_model_fakes import model_world


@pytest.mark.parametrize(("scale", "value"), [("none", 20), ("minmax", 1 / 9), ("zscore_train", -0.579324), ("robust", 0)])
def test_missing_value_uses_the_training_median_before_scaling(scale, value):
    settings = {"scale": scale, "missing": "train_median"}
    fitted = model_data.fit_window(np.array([[10.], [20.], [100.]]), settings)
    found = model_data.apply_fitted(np.array([[np.nan]]), fitted, settings)
    assert found[0, 0] == pytest.approx(value, abs=1e-6)


def test_plotly_binary_trace_keeps_numeric_values():
    values = {"dtype": "f8", "bdata": base64.b64encode(np.array([0., .1], dtype="<f8").tobytes()).decode()}
    figure = model_figures.tag_native({"data": [{"type": "scatter", "x": [1, 2], "y": values}]}, "model.test")
    assert figure["data"][0]["y"] == [0., .1]


@pytest.mark.parametrize(("title", "name"), [("动量", "动量"), ("123alpha", "_123alpha"), ("class", "class_")])
def test_notebook_library_variable_is_a_python_identifier(title, name):
    assert notebook_routes.python_name(title) == name


def test_refreeze_changes_only_the_locator_of_identical_data():
    original = {"key": "old", "fingerprint": "A", "frozenAt": 5, "names": 2, "dataTo": 1234}
    record = {"data": original.copy(), "settings": {"data": {"until": "2025-01-03"}}}
    rebuilt = {"key": "rebuilt", "fingerprint": "A", "frozenAt": 10, "names": 3, "dataTo": 9876}
    async def prepare(settings):
        assert settings == record["settings"]["data"]
        return rebuilt
    service = ModelRuns.__new__(ModelRuns)
    service.read = lambda identity: record
    service.update = lambda identity, **fields: record.update(fields)
    service.data = SimpleNamespace(ready=lambda key: None, prepare=prepare)
    assert asyncio.run(service.ready_data("r")) == rebuilt
    assert record["data"] == {**original, "key": "rebuilt"}
    for data in (original, {key: val for key, val in original.items() if key != "fingerprint"}):
        record["data"] = data.copy()
        rebuilt["fingerprint"] = "B"
        with pytest.raises(ContractError, match="no longer available"):
            asyncio.run(service.ready_data("r"))
        assert record["data"] == data


def test_factor_differences_use_shared_valid_days_and_no_rank_ic_across_holdings():
    keys = ("rankIc", "longShortNet", "top", "bottom", "names")
    a = pd.DataFrame({key: [.7, -.2, .6] for key in keys}, index=[1, 2, 3])
    b = pd.DataFrame({key: [np.nan, -.1, .2, .8] for key in keys}, index=[1, 2, 3, 4])
    run = {"settings": {"run": {"holding": 1}}, "data": {"annualization": 252}}
    assert factor_runs.common_differences(a, b, run, run)["rankIc"] == pytest.approx(.15)
    found = factor_runs.common_differences(a, b, run, {**run, "settings": {"run": {"holding": 5}}})
    assert "rankIc" not in found and "t" not in found and found["names"] == pytest.approx(.15)


def test_scheduled_values_settle_once_and_earn_the_research_book():
    labels = np.arange(230) * 86_400_000
    names = list("ABCDEF")
    rng = np.random.default_rng(66)
    prices = pd.DataFrame(100 * np.exp(rng.normal(0, .012, (230, 6)).cumsum(axis=0)), index=labels, columns=names)
    scores = pd.DataFrame(rng.normal(size=prices.shape), index=labels, columns=names)
    values = scores.rename_axis(index="datetime", columns="instrument").stack().rename("value").reset_index()
    settings = factor_work.settings_of({"holding": 3, "delay": 1, "grouping": {"count": 2}, "costBps": 9, "borrowPct": 2})
    def measure(prices, scheduled):
        return library_after.measure(values[values.datetime.isin(prices.index)], prices, scheduled=scheduled, holding=3, delay=1, annualization=252,
                                     settings=settings)[0]
    assert measure(prices, [])["settled"] == 0  # the entire catch-up, including the 220 historical labels
    eligible = [labels[220]]
    assert measure(prices.iloc[:224], eligible)["pending"] == 1
    found = measure(prices.iloc[:225], eligible)
    assert found["settled"] == 1 and measure(prices, eligible)["settled"] == 1
    panel = factor_work.Panel(labels, names, {"close": prices.to_numpy()}, None, {"annualization": 252})
    research_scores = scores.loc[eligible].reindex(labels).to_numpy()
    groups = factor_work.group_of(research_scores, settings)
    earned = factor_work.returns_of(panel, groups, 2, settings, research_scores)["net"]
    assert found["ls"] == pytest.approx(metrics.annual_mean(earned, 252))


@pytest.mark.parametrize("kind", ["factor", "model"])
def test_use_version_restores_project_and_settings_through_developer(tmp_path, monkeypatch, kind):
    async def scenario():
        world = model_world(tmp_path, monkeypatch)
        services = world.shell
        store = services.store
        obj = await store.create({"name": "Study", "kind": kind})
        root = Path(obj["directory"])
        frozen = tmp_path / "frozen"
        frozen.mkdir()
        for name, value in [(obj["entry"], "import helper"), ("helper.py", "A=1"), ("deleted.txt", "old"), ("image.bin", bytes([255]))]:
            (frozen / name).write_bytes(value.encode() if isinstance(value, str) else value)
        (root / obj["entry"]).write_text("B=2")
        (root / "helper.py").write_text("A=2")
        (root / "extra.py").write_text("extra=1")
        (services.factors.common.root / "shared.py").write_text("different")
        hashes = source_identity(frozen)
        files = [{"path": name, "sha256": digest} for name, digest in hashes.items()]
        run_settings = {"holding": 11} if kind == "factor" else {"cost": 7, "topN": 3}
        data = {"symbols": [], "timeframe": "1d", "since": "2024-01-01", "until": "2024-10-01"}
        record = {"id": "old", "objectId": obj["id"], "objectKind": kind, "mode": "code", "modelRun": kind == "model",
                  "settings": {"data": data, "run": run_settings, "params": {"lookback": 12}}, "common": None,
                  "code": {"files": files, "snapshotPath": str(frozen)}, "codeSnapshot": {"source": str(frozen), "hashes": hashes}}
        services.records.put("qrun", record)
        history = {}
        async def call(name, args):
            if name.endswith("projects.open"):
                return {"projectId": "project"}
            if name.endswith("documents.create"):
                (root / args["relativePath"]).write_text(args["content"])
                return {}
            if name.endswith("documents.open"):
                path = local_path(args["source"]["uri"])
                return {"documentId": str(path), "version": 1, "conflict": False, "dirty": False}
            if name.endswith("documents.edit"):
                path = Path(args["documentId"])
                history[path.name] = path.read_text()
                path.write_text(args["content"])
                return {"documentId": str(path), "version": 2}
            assert name.endswith("documents.save")
            return {"status": "saved"}
        monkeypatch.setattr(world.ctx.actions, "call", call)
        restored = await (services.factors if kind == "factor" else services.models).apply("old", obj["version"])
        assert history == {obj["entry"]: "B=2", "helper.py": "A=2"}
        assert (root / "helper.py").read_text() == "A=1" and (root / "deleted.txt").read_text() == "old"
        assert (root / "extra.py").read_text() == "extra=1" and not (root / "image.bin").exists()
        assert restored["restore"] == {"extraFiles": ["extra.py"], "nonTextFiles": ["image.bin"], "commonDifferences": ["shared.py"]}
        assert restored["settings"]["data"] == data and restored["settings"]["params"] == {"lookback": 12}
        assert all(restored["settings"]["run"][key] == value for key, value in run_settings.items())
    asyncio.run(scenario())


def test_standard_model_execution_and_code_view_use_the_same_frozen_pipeline(tmp_path, monkeypatch):
    from modules.quant.backend import model_prep
    from tests.unit.quant_model_fakes import prepared

    async def scenario():
        world = model_world(tmp_path, monkeypatch)
        identity = await world.make()
        obj = world.shell.store.read(identity)
        standard = tmp_path / "model_standard.py"
        monkeypatch.setattr(model_prep, "STANDARD", standard)
        standard.write_text('__version__ = "A"')
        old = await world.models.create(obj, "train", obj["settings"]["params"])
        standard.write_text('__version__ = "B"')
        new = await world.models.create(obj, "train", obj["settings"]["params"])
        for run, version in ((old, "A"), (new, "B")):
            worker = await world.models.job(run["id"], await prepared({}))
            source = Path(worker["pipelinePath"]).read_text()
            assert source == world.models.code(run["id"])["files"][0]["text"] == f'__version__ = "{version}"'
    asyncio.run(scenario())


def test_factor_details_used_by_current_model_are_retained_beyond_thirty(tmp_path, monkeypatch):
    async def scenario():
        world = model_world(tmp_path, monkeypatch)
        await world.make(params={"inputs": [{"source": "run", "ref": "run-0", "name": "Old factor"}]})
        factors = world.shell.factors
        for i in range(32):
            factors.records.put("qrun", {"id": f"run-{i}", "objectId": "factor", "objectKind": "factor", "runKind": "run",
                                         "state": "completed", "number": i, "starred": False})
        for run in ("run-0", "run-1"):
            cross = factors.directory(run) / "output" / "cross"
            cross.mkdir(parents=True)
            (cross / "after.npy").write_bytes(b"input")
        factors.prune("factor")
        assert (factors.directory("run-0") / "output" / "cross" / "after.npy").read_bytes() == b"input"
        assert not (factors.directory("run-1") / "output" / "cross").exists()
    asyncio.run(scenario())


def test_notebook_preview_and_creation_use_the_same_data_request(tmp_path, monkeypatch):
    from modules.quant.backend.notebook_routes import NotebookServices

    async def scenario():
        world = model_world(tmp_path, monkeypatch)
        cells = [{"id": "c", "type": "code", "source": 'bars = q.data(["US:AAPL","US:MSFT"], timeframe="1h", start="2025-01-01", end="2025-06-30")'}]
        async def get(_): return {"notebook": {"cells": cells}}
        async def exact(data):
            return {**data, "symbols": [{"instrument": {"displaySymbol": x["symbol"], "instrumentId": x["symbol"]}} for x in data["symbols"]]}
        store = world.shell.store
        monkeypatch.setattr(store, "exact_symbols", exact)
        notebook = await store.create({"kind": "notebook", "name": "Data selection"})
        store.save(store.read(notebook["id"]), settings={"data": {"heldOutFrom": "2025-07-01"}})
        nb = NotebookServices.__new__(NotebookServices)
        nb.store, nb.files = store, SimpleNamespace(read=get)
        args = {"notebookId": notebook["id"], "cells": ["c"], "kind": "factor", "name": "Read two"}
        shown = (await nb.extract_view(args))["dataSettings"]
        made = await nb.create_from_notebook(args)
        assert made["dataSettings"] == shown
        saved = made["settings"]["data"]
        assert [x["instrument"]["displaySymbol"] for x in saved["symbols"]] == shown["symbols"] == ["US:AAPL", "US:MSFT"]
        assert (saved["timeframe"], saved["since"], saved["until"]) == ("1h", "2025-01-01", "2025-06-30")
        assert saved["heldOutFrom"] == "2025-07-01"
    asyncio.run(scenario())


def test_notebook_preview_names_a_run_read_by_its_id(monkeypatch):
    from modules.quant.backend.notebook_routes import NotebookServices

    async def scenario():
        cells = [{"id": "c", "type": "code", "source": "run = q.run('run-12bc')\nrun"}]
        async def get(_): return {"notebook": {"cells": cells}}
        record = {"id": "run-12bc", "objectId": "s", "name": "QA Trend", "number": 2, "createdAt": 1795000000000}
        nb = NotebookServices.__new__(NotebookServices)
        async def unavailable():
            raise AssertionError("Market cannot identify ^GSPC")
        nb.store = SimpleNamespace(default_data=unavailable, read=lambda _: {"kind": "notebook", "settings": {
            "data": {}, "openedFrom": {"heldOutFrom": 1735689600000}}})
        nb.files = SimpleNamespace(read=get)
        nb.runs = SimpleNamespace(records=SimpleNamespace(find=lambda kind, identity: record if (kind, identity) == ("qrun", "run-12bc") else None))
        shown = await nb.extract_view({"notebookId": "n", "cells": ["c"], "kind": "strategy"})
        assert shown["heldOutFrom"] == "2025-01-01"
        assert shown["dataSettings"]["runs"] == [{"object": "run-12bc", "name": "QA Trend", "number": 2, "at": 1795000000000}]
    asyncio.run(scenario())
