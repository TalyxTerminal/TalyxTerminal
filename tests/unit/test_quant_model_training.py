"""The model pipeline's rules that need no research environment: windows on the calendar with the label length between segments,
processing fitted on the training segment only, the label from the next open, a batch of at most 20 groups, the queue's "run next",
the memory estimate that blocks sending, figures that must carry roles, and a new model as the standard pipeline with nothing chosen."""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest
from talyx_sdk import ContractError

from modules.quant.backend import model_prep as P
from modules.quant.backend.object_routes import Services
from modules.quant.backend.run_queue import RunQueue
from modules.quant.backend.work_records import WorkRecords
from modules.quant.native import factor_work as W
from modules.quant.native import model_check as C
from modules.quant.native import model_data as D
from modules.quant.native import model_figures as F
from tests.unit.test_quant_object_runs import FakeContext

DAYS, NAMES = 800, 20
SPLIT = {"trainMonths": 12, "validMonths": 3, "everyMonths": 3, "mode": "rolling", "firstTest": ""}


def panel(days=DAYS, names=NAMES):
    rng = np.random.default_rng(1)
    labels = pd.bdate_range("2020-01-01", periods=days).tz_localize("UTC").as_unit("ms").asi8
    close = np.exp(np.cumsum(rng.normal(0, .02, (days, names)), axis=0)) * 50
    opens = close * (1 + rng.normal(0, .002, (days, names)))
    fields = {"open": opens, "high": close * 1.01, "low": close * .99, "close": close, "volume": rng.uniform(1e5, 1e6, (days, names))}
    fields.update(rawClose=fields["close"], rawVolume=fields["volume"])
    return W.Panel(labels, [f"S{i}" for i in range(names)], fields, np.ones((days, names), dtype=bool), {"annualization": 252, "timeframe": "1d"},
                   industry=[f"I{i % 4}" for i in range(names)])


def test_windows_leave_the_label_length_between_segments_and_roll_or_expand():
    p = panel()
    rolling = D.layout(p.labels, SPLIT, 6, warmup=0)
    expanding = D.layout(p.labels, {**SPLIT, "mode": "expanding"}, 6, warmup=0)
    assert len(rolling) == len(expanding) >= 6
    for window in rolling:
        assert window.valid[0] - window.train[1] == 6 and window.test[0] - window.valid[1] == 6  # 5 bars between, the label's length
    assert len({window.train[0] for window in expanding}) == 1 and len({window.train[0] for window in rolling}) == len(rolling)
    assert rolling[-1].test[1] == len(p.labels) - 1  # the last test period runs to the newest bar
    held = int(p.labels[600])
    cut = D.layout(p.labels, SPLIT, 6, warmup=0, held_out=held)
    assert all(window.test[1] < 600 for window in cut)


def test_statistics_over_time_are_fitted_on_the_training_segment_only():
    p = panel()
    x = np.random.default_rng(2).normal(0, 1, p.shape)
    settings = {"leaveOut": {"halted": True, "young": False, "illiquid": False},
                "processing": {"inputs": {"missing": "train_median", "extremes": "none", "scale": "robust", "takeOut": []}, "label": {"missing": "drop", "extremes": "none", "scale": "none"}},
                "label": {"days": 5, "form": "raw"}, "split": SPLIT}
    data = D.ModelData(p, [D.Input(id="a", name="A", source="code", ref="f.py:a")], settings, code=lambda item: x)
    window = data.windows[1]
    before = data.matrices(window)
    data.x[window.test[0]:window.test[1] + 1] = 1000.0  # the test segment changes completely
    after = data.matrices(window)
    assert np.array_equal(before["fitted"]["median"], after["fitted"]["median"]) and np.array_equal(before["fitted"]["mad"], after["fitted"]["mad"])
    assert np.array_equal(before["train"]["x"], after["train"]["x"]) and not np.array_equal(before["test"]["x"], after["test"]["x"])


def test_the_label_is_the_return_from_the_next_open():
    p = panel()
    label = D.label_of(p, 5)
    opens = p.fields["open"]
    assert label[10, 3] == pytest.approx(opens[16, 3] / opens[11, 3] - 1)
    assert np.isnan(label[-6:]).all()


def test_too_few_names_and_too_short_data_say_why():
    with pytest.raises(D.DataError) as error:
        D.ModelData(panel(names=3), [D.Input(id="a", name="A", source="code", ref="f.py:a")], {"processing": {"inputs": {"missing": "median", "extremes": "none", "scale": "rank"},
                    "label": {"missing": "drop", "extremes": "none", "scale": "rank"}}, "label": {"days": 5, "form": "rank"}, "split": SPLIT},
                    code=lambda item: np.zeros((DAYS, 3)))
    assert error.value.failure["title"] == "needs more names" and "S0" in error.value.failure["message"]
    with pytest.raises(D.DataError) as error:
        D.ModelData(panel(days=200), [D.Input(id="a", name="A", source="code", ref="f.py:a")], {"processing": {"inputs": {"missing": "median", "extremes": "none", "scale": "rank"},
                    "label": {"missing": "drop", "extremes": "none", "scale": "rank"}}, "label": {"days": 5, "form": "rank"}, "split": SPLIT},
                    code=lambda item: np.zeros((200, NAMES)))
    assert error.value.failure["title"] == "has no window" and "12 months of training" in error.value.failure["message"]


def test_a_batch_is_every_combination():
    params = {"lgb_lr": 0.05, "lgb_leaves": 63, "label_days": 5}
    groups = P.expand(params, [{"name": "lgb_lr", "values": [0.02, 0.05]}, {"name": "label_days", "values": [5, 10]}])
    assert [group["values"] for group in groups] == [{"lgb_lr": 0.02, "label_days": 5}, {"lgb_lr": 0.02, "label_days": 10}, {"lgb_lr": 0.05, "label_days": 5},
                                                     {"lgb_lr": 0.05, "label_days": 10}]
    assert P.expand(params, None) == [{"values": {}, "params": params}]
    assert len(P.expand(params, [{"name": "lgb_lr", "values": list(range(5))}, {"name": "lgb_leaves", "values": list(range(5))}])) == 25   # no cap


def test_the_standard_names_become_the_data_set_settings_and_each_algorithm_has_its_own_parameters():
    params = {item["name"]: item["default"] for item in P.standard_declarations()["parameters"]}
    params.update(algorithm="xgboost", take_out_industry=True)
    settings = P.settings_of(params)
    assert settings["label"]["formula"] == "CSRank(Ref($open, -6) / Ref($open, -1) - 1)" and settings["split"]["trainMonths"] == 36 and settings["processing"]["inputs"]["takeOut"] == ["industry"]
    assert set(P.algorithm_params(params)) == {"lr", "depth", "ff", "bf", "l1", "l2", "minleaf", "rounds", "stop"}
    table = {item["id"]: item for item in P.algorithms()}
    assert {item["group"] for item in table.values()} == {"tree", "linear", "ensemble", "neural"}
    assert table["catboost"]["params"][2]["label"] == "L2 leaf" and table["catboost"]["params"][2]["setting"] == "cat_l2"
    assert not any(item.get("unavailable") for item in table.values())


def test_run_next_moves_a_waiting_job_ahead():
    async def scenario():
        queue = RunQueue(memory=64 * 1024 ** 3, cpus=1)
        order = []

        async def job(name):
            async with queue.slot(owner=name, label=name, interactive=False, memory=1):
                order.append(name)
                await asyncio.sleep(.05)
        tasks = [asyncio.create_task(job(name)) for name in ("a", "b", "c")]
        await asyncio.sleep(.01)
        assert await queue.promote("c") and not await queue.promote("missing")
        await asyncio.gather(*tasks)
        return order
    assert asyncio.run(scenario()) == ["a", "c", "b"]


def test_memory_that_does_not_fit_is_the_only_estimate_that_blocks():
    small = C.estimate(algorithm="lightgbm", params={"rounds": 100}, rows=50_000, features=10, bars=750, names=50, windows=6, threads=4)
    large = C.estimate(algorithm="lightgbm", params={"rounds": 1000}, rows=5_000_000, features=360, bars=2520, names=500, windows=84, threads=8)
    assert small["memory"] < 2 * 1024 ** 3 < large["memory"] and large["seconds"] > small["seconds"]


def test_a_figure_without_a_role_or_key_is_refused():
    with pytest.raises(ValueError, match="role and a key"):
        F.figure("model.x", [{"type": "scatter", "name": "line", "meta": {"role": "main"}}])
    ok = F.figure("model.x", [F.line([1, 2], [0.1, float("nan")], "IC", "main", "ic")])
    assert ok["layout"]["meta"]["key"] == "model.x" and ok["data"][0]["y"] == [0.1, None]


def test_a_new_model_is_the_standard_pipeline_with_nothing_chosen(tmp_path):
    async def scenario():
        shell = Services(FakeContext(tmp_path, None), WorkRecords(tmp_path / "records"))
        created = await shell.store.create({"kind": "model"})
        assert created["name"] == "New model" and not created["hasCode"] and created["settings"]["mode"] == "standard"
        names = {item["name"]: item for item in created["parameters"]}
        assert names["algorithm"]["default"] == "" and names["inputs"]["default"] == [] and names["label"]["default"] == "CSRank(Ref($open, -6) / Ref($open, -1) - 1)"
        assert {item["group"] for item in created["parameters"]} == {"Data", "Inputs", "Processing", "Label", "Split", "Algorithm"}
        updated = await shell.store.update_settings(created["id"], {"data": {"symbols": [], "timeframe": "1d", "since": "2016-01-01", "until": "2024-12-31"},
                                                                    "run": {"cost": 10, "topN": 50}, "params": {"algorithm": "lightgbm", "lgb_lr": 0.1}})
        assert "heldOutFrom" not in updated["settings"]["data"] and updated["settings"]["run"]["cost"] == 10
        with pytest.raises(ContractError):
            await shell.store.update_settings(created["id"], {"params": {"label_days": 7}})
        with pytest.raises(ContractError, match="no setting"):
            await shell.store.update_settings(created["id"], {"params": {"nope": 1}})
    asyncio.run(scenario())
