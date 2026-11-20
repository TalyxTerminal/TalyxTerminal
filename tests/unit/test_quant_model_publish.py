"""M section, P4: publishing a model run with the factors it reads that are not in the library yet, on the new store (L's): one transaction, the plan's
comparison and blocks, a combined run, where training ends, what the history of values is, and how a published model is scored afterwards."""
from __future__ import annotations

import asyncio
import json

import numpy as np
import pandas as pd
import pytest
from talyx_sdk import ContractError

from tests.unit.quant_library_fakes import DAY, build, model_publishing


def run(coro):
    return asyncio.run(coro)


def windows_of(labels, count=4, length=20, ahead=6):
    """Windows laid over the calendar: validation then test, `ahead` bars between; the layout `model_data.layout` makes."""
    out, test_start = [], len(labels) - count * length
    for k in range(1, count + 1):
        first = test_start + (k - 1) * length
        valid_end = first - ahead
        out.append({"k": k, "train": {"from": int(labels[0]), "to": int(labels[valid_end - 20])}, "valid": {"from": int(labels[valid_end - 19]), "to": int(labels[valid_end])},
                    "test": {"from": int(labels[first]), "to": int(labels[first + length - 1])}})
    return out


def prepared(tmp_path, *, factors=("Value", "Size"), name="Blend", **extra):
    world = build(tmp_path)
    setup = model_publishing(world, tmp_path)
    identity, publish = setup.models.new_run(name, list(factors))
    record = setup.models.runs[identity]
    labels = json.loads((setup.models.directory(identity) / "output" / "pred.json").read_text(encoding="utf-8"))["labels"]
    shape = np.load(setup.models.directory(identity) / "output" / "pred.npy").shape
    np.save(setup.models.directory(identity) / "output" / "label.npy", np.random.default_rng(3).normal(size=shape))
    record.setdefault("params", record["settings"]["params"])
    setup.models.ready_data = _ready(setup)
    record.update(windows=windows_of(labels), reach={"ahead": 6, "back": 0, "start": {"bar": 1, "at": "open"}}, **extra)
    return world, setup, identity, publish, labels


def test_an_unpublished_factor_run_is_published_with_the_model_in_one_transaction(tmp_path):
    world, setup, identity, publish, _ = prepared(tmp_path)
    created, _ = run(setup.library.commit("pub", setup.models.read(identity), setup.world.ready, publish))
    assert len(created) == 3
    stamps = {world.store.version(item["versionId"])["created_at"] for item in created}
    assert len(stamps) == 1, "one commit: the factor versions and the model's carry the same moment"


def test_a_name_taken_by_another_kind_blocks_publish(tmp_path):
    from tests.unit.quant_library_fakes import factor_spec
    world, setup, identity, _, _ = prepared(tmp_path, name="Momentum")
    world.store.commit_versions([factor_spec("Momentum", "m1", 50, kind="factor")])
    plan = setup.library.plan(identity, "Momentum")
    assert plan["blocked"] == {"reason": "Momentum is already a factor in the library; choose another name.", "field": "name"}
    with pytest.raises(ContractError, match="already a factor in the library"):
        run(setup.library.publish(identity, {"name": "Momentum"}))


def test_publish_refuses_a_stopped_input_version_unless_auto_is_off(tmp_path):
    world, setup, identity, _, _ = prepared(tmp_path, factors=())
    from tests.unit.quant_library_fakes import factor_spec
    world.store.commit_versions([factor_spec("Momentum 12-1", "mom-v2", 50)])
    setup.models.runs[identity]["pins"] = [{"versionId": "mom-v2", "name": "Momentum 12-1", "version": 2}]
    world.store.stop("mom-v2") if hasattr(world.store, "stop") else None
    plan = setup.library.plan(identity)
    assert plan["pinned"][0]["state"] == "stopped"
    assert plan["blocked"] == {"reason": "Momentum 12-1 v2 is stopped; retrain on a producing version, or publish without new values.", "field": "inputs"}
    with pytest.raises(ContractError, match="is stopped; retrain on"):
        run(setup.library.publish(identity, {"name": "Blend"}))
    allowed = run(setup.library.publish(identity, {"name": "Blend", "autoUpdate": False}))
    assert allowed["state"] in {"running", "completed"}


def test_the_plan_compares_with_the_previous_version(tmp_path):
    world, setup, identity, publish, _ = prepared(tmp_path, factors=())
    run(setup.library.commit("first", setup.models.read(identity), setup.world.ready, publish))
    again, _ = _new(setup, "Blend again")
    record = setup.models.runs[again]
    record["windows"] = setup.models.runs[identity]["windows"]
    record["numbers"] = {"icMean": 0.04, "ric": 0.04}
    plan = setup.library.plan(again, "Blend")
    assert plan["version"] == 2 and plan["compare"] and all({"measure", "previous", "current", "change"} <= set(row) for row in plan["compare"])
    assert "paramsDiff" in plan and all({"name", "kind"} <= set(user) for user in plan["users"])


def test_the_plan_names_a_different_label_length_instead_of_a_change(tmp_path):
    world, setup, identity, publish, _ = prepared(tmp_path, factors=())
    created, _ = run(setup.library.commit("first", setup.models.read(identity), setup.world.ready, publish))
    world.store.merge_meta(created[-1]["versionId"], horizon={"holding": 10, "delay": 1})
    again, _ = _new(setup, "Blend again")
    setup.models.runs[again]["windows"] = setup.models.runs[identity]["windows"]
    rank = next(row for row in setup.library.plan(again, "Blend")["compare"] if "Rank IC" in row["measure"])
    assert rank["change"] is None and rank["note"] == "v1 predicts 10D"


def test_published_model_records_where_training_ends(tmp_path, monkeypatch):
    world, setup, identity, publish, labels = prepared(tmp_path, factors=())
    created, _ = run(setup.library.commit("pub", setup.models.read(identity), setup.world.ready, publish))
    last_valid_end = setup.models.runs[identity]["windows"][-1]["valid"]["to"]
    position = labels.index(last_valid_end)
    expected = labels[position + 6]
    version = created[-1]["versionId"]
    assert world.store.version(version)["meta"]["pipeline"]["trainedThrough"] == expected
    assert world.library.read_version(version)["trainingCutoff"] == expected
    from tests.unit.quant_public import library_actions
    act = library_actions(monkeypatch, library=world.library)
    with pytest.raises(ContractError) as unsupported:
        run(act("read_library_model", {"name":"Blend"}))
    assert unsupported.value.code == "model_not_callable"


def test_published_model_records_held_out_from(tmp_path):
    world, setup, identity, publish, labels = prepared(tmp_path, factors=())
    setup.models.runs[identity]["settings"]["data"]["heldOutFrom"] = "2021-01-01"
    created, _ = run(setup.library.commit("pub", setup.models.read(identity), setup.world.ready, publish))
    assert world.store.version(created[-1]["versionId"])["meta"]["pipeline"]["heldOutFrom"] == "2021-01-01"
    other, other_publish = _new(setup, "Other")
    setup.models.runs[other]["windows"] = setup.models.runs[identity]["windows"]
    again, _ = run(setup.library.commit("pub2", setup.models.read(other), setup.world.ready, other_publish))
    assert world.store.version(again[-1]["versionId"])["meta"]["pipeline"]["heldOutFrom"] is None


def test_published_values_are_the_windows_test_predictions(tmp_path):
    world, setup, identity, publish, labels = prepared(tmp_path, factors=())
    created, _ = run(setup.library.commit("pub", setup.models.read(identity), setup.world.ready, publish))
    windows = setup.models.runs[identity]["windows"]
    frame = world.store.frame(created[-1]["versionId"])
    days = sorted({int(day) for day in frame["datetime"]})
    assert days and all(any(w["test"]["from"] <= day <= w["test"]["to"] for w in windows) for day in days), "every published day is inside a test period"


def test_a_combined_run_publishes_and_updates_each_source(tmp_path):
    world, setup, identity, publish, labels = prepared(tmp_path, factors=())
    other, _ = _new(setup, "Other")
    setup.models.runs[other]["windows"] = setup.models.runs[identity]["windows"]
    combined, combined_publish = _new(setup, "Mean")
    record = setup.models.runs[combined]
    record.update(runKind="combine", combinedFrom=[identity, other], windows=setup.models.runs[identity]["windows"])
    plan = setup.library.plan(combined, "Mean")
    assert isinstance(plan["futureDataCheck"], list) and len(plan["futureDataCheck"]) == 2, "a combined run: one check for each source"
    created, _ = run(setup.library.commit("pub", setup.models.read(combined), setup.world.ready, combined_publish))
    meta = world.store.version(created[-1]["versionId"])["meta"]["pipeline"]
    assert [item["runId"] for item in meta["sources"]] == [identity, other]
    parts = setup.library.library.version_directory(created[-1]["versionId"]) / "parts"
    assert sorted(path.name for path in parts.iterdir()) == ["0", "1"] or sorted(path.name for path in parts.iterdir()) == ["1", "2"]


def test_the_future_data_check_runs_once_for_each_source_of_a_combined_run(tmp_path, monkeypatch):
    world, setup, identity, _, _ = prepared(tmp_path, factors=())
    checked = []

    async def fake(source, ready, directory, spec):
        checked.append(source["id"])
        return {"passed": True, "value": "unchanged", "title": "ok", "window": 1, "cutoff": 1}
    setup.library.future_check = fake
    other, _ = _new(setup, "Other")
    setup.models.runs[other]["windows"] = setup.models.runs[identity]["windows"]
    combined, _ = _new(setup, "Mean")
    setup.models.runs[combined].update(runKind="combine", combinedFrom=[identity, other], windows=setup.models.runs[identity]["windows"])
    async def publish():

        view = await setup.library.publish(combined,{"name":"Mean"})
        await asyncio.gather(*setup.library.tasks.values(),return_exceptions=True)
        assert setup.library.read(view["runId"])["state"] == "completed"
        return view
    run(publish())
    assert sorted(checked) == sorted([identity, other]), "each source of the combined run is checked once"


def test_future_data_blocks_and_commits_nothing(tmp_path):
    world, setup, identity, publish, _ = prepared(tmp_path, factors=())
    before = len(world.store.all_versions())

    async def fail(source, ready, directory, spec):
        return {"passed": False, "value": "changed", "title": "Not published: AAPL on Mar 3, 2021 is 0.0137 with all the data but 0.0021 when the data is cut at Mar 3, 2021: the model reads data from after the time it predicts.", "window": 14, "cutoff": 1}
    setup.library.future_check = fail
    view = run(setup.library.publish(identity, {"name": "Blend"}))
    run(asyncio.sleep(0.5))
    final = setup.library.read(view["id"])
    assert final["state"] == "failed" and "reads data from after the time it predicts" in final["message"] and len(world.store.all_versions()) == before


def test_a_factor_published_with_the_model_is_checked_for_future_data():
    pytest.skip("the factor's values and cuts are made by the research engine: tests/native/test_quant_model_workflow.py publishes a model with an unpublished factor run")


def test_cancel_mid_publish_leaves_nothing(tmp_path):
    world, setup, identity, _, _ = prepared(tmp_path, factors=())
    before = len(world.store.all_versions())

    async def scenario():
        started = asyncio.Event()

        async def slow(source, ready, directory, spec):
            started.set()
            await asyncio.sleep(30)
        setup.library.future_check = slow
        view = await setup.library.publish(identity, {"name": "Blend"})
        await asyncio.wait_for(started.wait(), 5)
        await setup.library.cancel(view["id"])
        return setup.library.read(view["id"])
    final = run(scenario())
    assert final["state"] == "cancelled" and final["message"] == "Stopped before anything was added to the library." and len(world.store.all_versions()) == before


def test_after_publication_scores_the_model_against_its_own_label():
    """B9: `library_after.measure` takes the realised label; settled by `reach.ahead`, Newey-West lag horizon - 1. On a volatility label the model's
    after-publication Rank IC equals the formula's mean computed directly; the close-price return gives something else."""
    from modules.quant.backend import library_after
    rng = np.random.default_rng(2)
    days, names = 100, 30
    label = rng.normal(size=(days, names))
    prediction = label + rng.normal(size=(days, names))
    rows = [(d * DAY, f"i:{n}", "prediction", float(prediction[d, n])) for d in range(days) for n in range(names)]
    realised = [(d * DAY, f"i:{n}", "label", float(label[d, n])) for d in range(days) for n in range(names)]
    from scipy.stats import spearmanr
    expected = float(np.mean([spearmanr(prediction[d], label[d])[0] for d in range(days)]))
    scored, _points = library_after.measure(pd.DataFrame(rows, columns=["datetime", "instrument", "field", "value"])[["datetime", "instrument", "value"]], None, scheduled=[row[0] for row in rows], holding=20, delay=1, annualization=252, realised=pd.DataFrame(realised, columns=["datetime", "instrument", "field", "value"])[["datetime", "instrument", "value"]], ahead=21)
    assert scored["value"] == pytest.approx(expected, abs=1e-9)


def test_model_prediction_history_written_with_the_label_field(tmp_path):
    world, setup, identity, publish, labels = prepared(tmp_path, factors=())
    created, _ = run(setup.library.commit("pub", setup.models.read(identity), setup.world.ready, publish))
    fields = set(world.store.frame(created[-1]["versionId"])["field"])
    assert fields == {"prediction", "label"}


def test_publish_appears_only_on_completed_rows_is_a_frontend_rule():
    pytest.skip("F1 `publish appears only on completed rows` in Model.test.tsx")


def _ready(setup):
    async def ready(_identity):
        return setup.world.ready
    return ready


def _new(setup, name):
    identity, publish = setup.models.new_run(name, [])
    setup.models.runs[identity].setdefault("params", setup.models.runs[identity]["settings"]["params"])
    return identity, publish
