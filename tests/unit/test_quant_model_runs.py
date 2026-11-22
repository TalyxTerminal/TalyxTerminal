"""M section, P2: a model run's Host side on a stub engine (0.1 s a window, predictions a function of the run's settings): the queue and its order, stopping and
resuming from the finished windows, a batch, the held-out period, combining, where a run's code and pipeline are recorded, and the accounts that must always hold
(build draft section 4, B1-B8, with a random mix of actions)."""
from __future__ import annotations

import asyncio
import random

import numpy as np
import pytest
from talyx_sdk import ContractError

from tests.unit.quant_model_fakes import model_world, settled, wait_for


def run(coro):
    return asyncio.run(coro)


def test_manual_stop_keeps_finished_windows_and_resumes(tmp_path, monkeypatch):
    async def scenario():
        world = model_world(tmp_path, monkeypatch, windows=40)
        identity = await world.make()
        started = await world.models.train(identity)
        run_id = started["runs"][0]["id"]
        await wait_for(lambda: len(world.models.read(run_id).get("windows") or []) >= 2)
        await world.models.stop(run_id)
        record = world.models.read(run_id)
        assert record["state"] == "cancelled" and record["stopped"]["auto"] is False and 2 <= len(record["windows"]) < 40
        done = len(record["windows"])
        await world.models.resume(run_id)
        record = await settled(world.models, run_id)
        assert record["state"] == "completed" and len(record["windows"]) == 40
        assert [call["keepDone"] for call in world.engine.calls] == [False, True] and done >= 2
    run(scenario())


def test_resume_alone_keeps_finished_windows_and_runs_by_itself(tmp_path, monkeypatch):
    async def scenario():
        world = model_world(tmp_path, monkeypatch, windows=40)
        identity = await world.make()
        run_id = (await world.models.train(identity))["runs"][0]["id"]
        world.engine.fail_at[run_id] = 3
        record = await settled(world.models, run_id)
        assert record["state"] == "failed" and record["failure"]["window"] == 3 and len(record["windows"]) == 2 and record["failure"]["resumeFrom"] == 3
        world.engine.fail_at.clear()
        await world.models.resume(run_id, {"alone": True})
        record = await settled(world.models, run_id)
        assert record["state"] == "completed" and len(record["windows"]) == 40 and world.engine.calls[-1]["threads"] >= world.engine.calls[0]["threads"]
    run(scenario())


def test_resume_with_an_override_changes_only_the_windows_from_there(tmp_path, monkeypatch):
    async def scenario():
        world = model_world(tmp_path, monkeypatch, windows=40)
        identity = await world.make()
        run_id = (await world.models.train(identity))["runs"][0]["id"]
        world.engine.fail_at[run_id] = 4
        await settled(world.models, run_id)
        world.engine.fail_at.clear()
        await world.models.resume(run_id, {"override": {"lgb_depth": 5}})
        record = await settled(world.models, run_id)
        assert record["overrides"] == {"lgb_depth": 5} and world.engine.calls[-1]["params"]["lgb_depth"] == 5 and world.engine.calls[-1]["keepDone"]
        assert record["settings"]["params"]["lgb_depth"] != 5, "the run's own settings stay as they were; the override is recorded beside them"
        assert record["overrideFrom"] == 4
    run(scenario())


def test_stopping_a_batch_removes_the_queued_and_stops_the_running(tmp_path, monkeypatch):
    async def scenario():
        world = model_world(tmp_path, monkeypatch, windows=8, cpus=2)
        identity = await world.make()
        started = await world.models.train(identity, {"vary": [{"name": "seed", "values": [1, 2, 3, 4, 5]}]})
        batch = started["batch"]["id"]
        await wait_for(lambda: sum(1 for item in started["runs"] if world.models.read(item["id"])["state"] == "running") == 2)
        await world.models.stop(batch)
        states = sorted(world.models.read(item["id"])["state"] for item in started["runs"])
        assert states.count("cancelled") == 5 and world.engine.peak <= 2
    run(scenario())


def test_run_next_moves_a_queued_run_to_the_front(tmp_path, monkeypatch):
    async def scenario():
        world = model_world(tmp_path, monkeypatch, windows=3, cpus=1)
        identity = await world.make()
        started = await world.models.train(identity, {"vary": [{"name": "seed", "values": [1, 2, 3]}]})
        ids = [item["id"] for item in started["runs"]]
        await wait_for(lambda: world.models.read(ids[2])["state"] == "queued" and world.models.read(ids[0])["state"] == "running")
        await world.models.run_next(ids[2])
        for _ in range(3):
            await asyncio.gather(*[settled(world.models, item) for item in ids])
        starts = sorted(ids, key=lambda item: world.models.read(item)["startedAt"])
        assert starts == [ids[0], ids[2], ids[1]]
    run(scenario())


def test_progress_is_written_at_most_once_a_second(tmp_path, monkeypatch):
    async def scenario():
        world = model_world(tmp_path, monkeypatch, windows=8)
        identity = await world.make()
        started = await world.models.train(identity, {"vary": [{"name": "seed", "values": list(range(10))}]})
        await asyncio.gather(*[settled(world.models, item["id"], 30) for item in started["runs"]])
        events = [payload for name, payload in world.ctx.published if name == "object_run_changed" and payload["state"] == "running"]
        per_run = {}
        for payload in events:
            per_run[payload["runId"]] = per_run.get(payload["runId"], 0) + 1
        elapsed = max(world.models.read(item["id"])["seconds"] for item in started["runs"])
        assert all(count <= elapsed + 2 for count in per_run.values()), (per_run, elapsed)
    run(scenario())


def test_a_run_stopped_by_closing_resumes_from_its_finished_windows(tmp_path, monkeypatch):
    async def scenario():
        world = model_world(tmp_path, monkeypatch, windows=40)
        identity = await world.make()
        run_id = (await world.models.train(identity))["runs"][0]["id"]
        await wait_for(lambda: len(world.models.read(run_id).get("windows") or []) >= 2)
        world.models.tasks[run_id].cancel()
        if task := world.models.tasks.get(run_id):
            await asyncio.gather(task, return_exceptions=True)
        await wait_for(lambda: world.models.read(run_id)["state"] == "interrupted")
        assert world.models.read(run_id)["stopped"]["auto"] is True
        assert world.models.recover() == [run_id]
        assert world.models.read(run_id)["resumedAfterClose"] is True
        record = await settled(world.models, run_id)
        assert record["state"] == "completed" and len(record["windows"]) == 40
    run(scenario())


def test_manual_stop_is_not_resumed_by_recover(tmp_path, monkeypatch):
    async def scenario():
        world = model_world(tmp_path, monkeypatch, windows=40)
        identity = await world.make()
        run_id = (await world.models.train(identity))["runs"][0]["id"]
        await wait_for(lambda: len(world.models.read(run_id).get("windows") or []) >= 1)
        await world.models.stop(run_id)
        assert world.models.recover() == []
    run(scenario())


def test_each_group_of_a_batch_is_one_run(tmp_path, monkeypatch):
    async def scenario():
        world = model_world(tmp_path, monkeypatch, windows=2, cpus=2)
        identity = await world.make()
        started = await world.models.train(identity, {"vary": [{"name": "seed", "values": [1, 2, 3]}]})
        assert [item["group"] for item in started["runs"]] == [1, 2, 3] and len({item["number"] for item in started["runs"]}) == 3
        await asyncio.gather(*[settled(world.models, item["id"]) for item in started["runs"]])
    run(scenario())


def test_a_finished_run_records_the_pipeline_it_ran(tmp_path, monkeypatch):
    async def scenario():
        world = model_world(tmp_path, monkeypatch, windows=2)
        identity = await world.make()
        run_id = (await world.models.train(identity))["runs"][0]["id"]
        record = await settled(world.models, run_id)
        assert record["pipeline"] != True and record["pipeline"]["version"] == "1.0" and record["pipeline"]["digest"]  # noqa: E712
        assert (world.models.directory(run_id) / "source" / "model_standard.py").is_file()
    run(scenario())


def test_runs_record_which_pipeline_they_ran(tmp_path, monkeypatch):
    async def scenario():
        world = model_world(tmp_path, monkeypatch, windows=2)
        identity = await world.make()
        first = (await world.models.train(identity))["runs"][0]["id"]
        await settled(world.models, first)
        await world.models.write_as_code(identity)
        second = (await world.models.train(identity))["runs"][0]["id"]
        await settled(world.models, second)
        assert world.models.read(first)["mode"] == "standard" and world.models.read(second)["mode"] == "code"
        assert world.models.read(first)["pipeline"]["version"] == "1.0" and world.models.read(second)["pipeline"] is not True
    run(scenario())


def test_held_out_uses_count_across_models(tmp_path, monkeypatch):
    async def scenario():
        world = model_world(tmp_path, monkeypatch, windows=2)
        data = {"heldOutFrom": "2024-01-01"}
        first = await world.make("First")
        second = await world.make("Second")
        for identity in (first, second):
            settings = world.shell.store.read(identity)["settings"]
            await world.shell.store.update_settings(identity, {"data": {**settings["data"], **data}, "run": settings["run"], "params": settings["params"]})
        run_id = (await world.models.train(first))["runs"][0]["id"]
        await settled(world.models, run_id)
        await world.models.held_out(run_id)
        plan = world.models.held_out_plan(run_id)
        assert {"from", "to", "windows", "minutes", "uses"} <= set(plan) and plan["uses"] == [] or all("objectName" in use for use in plan["uses"])
        other = (await world.models.train(second))["runs"][0]["id"]
        await settled(world.models, other)
        plan = world.models.held_out_plan(other)
        assert [use["objectName"] for use in plan["uses"]] == ["First"]
    run(scenario())


def test_runs_keep_the_held_out_start_they_ran_with(tmp_path, monkeypatch):
    async def scenario():
        world = model_world(tmp_path, monkeypatch, windows=2)
        identity = await world.make()
        settings = world.shell.store.read(identity)["settings"]
        await world.shell.store.update_settings(identity, {"data": {**settings["data"], "heldOutFrom": "2024-01-01"}, "run": settings["run"], "params": settings["params"]})
        first = (await world.models.train(identity))["runs"][0]["id"]
        await settled(world.models, first)
        await world.shell.store.update_settings(identity, {"data": {**settings["data"], "heldOutFrom": "2024-06-01"}, "run": settings["run"], "params": settings["params"]})
        second = (await world.models.train(identity))["runs"][0]["id"]
        await settled(world.models, second)
        assert world.models.read(first)["settings"]["data"]["heldOutFrom"] == "2024-01-01" and world.models.read(second)["settings"]["data"]["heldOutFrom"] == "2024-06-01"
    run(scenario())


def test_combine_refuses_runs_on_different_data(tmp_path, monkeypatch):
    async def scenario():
        world = model_world(tmp_path, monkeypatch, windows=2)
        first, second = await world.make("A"), await world.make("B")
        a = (await world.models.train(first))["runs"][0]["id"]
        b = (await world.models.train(second))["runs"][0]["id"]
        await asyncio.gather(settled(world.models, a), settled(world.models, b))
        world.models.update(b, settings={**world.models.read(b)["settings"], "data": {**world.models.read(b)["settings"]["data"], "since": "2020-01-01"}})
        with pytest.raises(ContractError, match="use different data .*; combine runs on the same data"):
            await world.models.combine([a, b])
    run(scenario())


def test_combine_ranks_each_day_then_averages():
    pytest.skip("runs in the research environment: tests/native (combine mode); the Host only passes sources")


def test_strategy_from_a_run_copies_the_data_settings_and_the_held_out_start(tmp_path, monkeypatch):
    from modules.quant.backend.object_data import resolve_series
    from tests.unit.quant_library_fakes import factor_spec
    from tests.unit.test_quant_market2_inputs import stock

    async def scenario():
        world = model_world(tmp_path, monkeypatch, windows=2)
        identity = await world.make()
        settings = world.shell.store.read(identity)["settings"]
        await world.shell.store.update_settings(identity, {"data": {**settings["data"], "symbols": [{"instrument": stock()}], "adjustment": "adjusted", "heldOutFrom": "2024-01-01"}, "run": {**settings["run"], "cost": 7},
                                                           "params": settings["params"]})
        run_id = (await world.models.train(identity))["runs"][0]["id"]
        await settled(world.models, run_id)
        # An unpublished run is traded on its own out-of-sample predictions: the days of its test windows, never the training fit.
        import json

        import pandas as pd
        output = world.models.directory(run_id) / "output"
        output.mkdir(parents=True, exist_ok=True)
        labels = [86_400_000 * day for day in range(1, 7)]
        np.save(output / "pred.npy", np.arange(12, dtype="float32").reshape(6, 2))
        (output / "pred.json").write_text(json.dumps({"labels": labels, "columns": ["A", "B"]}), encoding="utf-8")
        world.models.save(world.models.read(run_id), windows=[{"k": 1, "test": {"from": labels[3], "to": labels[5]}}])
        from tests.unit.quant_public import public_actions
        act = public_actions(world.ctx, world.shell, monkeypatch)
        made = await act("create_object",{"kind":"strategy","fromRun":run_id})
        strategy = world.shell.store.read(made["objectId"] if "objectId" in made else made["id"])
        assert "adjustment" not in strategy["settings"]["data"]
        held = strategy["settings"]["signalRun"]
        kept = pd.read_parquet(held["path"])
        assert held["runId"] == run_id and strategy["settings"]["params"]["signal"] == "" and sorted(set(kept["datetime"])) == labels[3:]
        # the run's held-out start goes with it, set through the strategy's own entry (design.md, block to block)
        assert strategy["settings"]["data"]["since"] == "2016-01-01" and strategy["settings"]["data"]["heldOutFrom"] == "2024-01-01"
        # It trades the predictions: the Top N preset, not the empty skeleton, and no list of sources where the strategy keeps its pinned inputs.
        assert strategy.get("presetOf") == "ranked"
        assert not isinstance(strategy["settings"].get("inputs"), list)
        # The run's cost and holding go with it (design.md, block to block): 7 bps a trade, held the label's horizon
        run_settings = strategy["settings"]["run"]
        assert run_settings["costs"]["commissionBps"] == 7 and run_settings["costs"]["halfSpreadBps"] == 0
        assert strategy["settings"]["params"]["rebalance_every"] == world.models.read(run_id)["horizon"] and "untilLatest" not in strategy["settings"]["data"]
        # Published or not, a strategy started from a run stays on that run's predictions, never on what the library name holds later
        world.models.save(world.models.read(run_id), version={"name": "Reversal LightGBM", "version": 1, "versionId": "ver-x"})
        made = await world.models.create_strategy(run_id)
        again = world.shell.store.read(made["id"])["settings"]
        assert again["params"]["signal"] == "" and again["signalRun"]["runId"] == run_id

        version = factor_spec("Reversal LightGBM", "ver-x", 15, kind="model")
        version["meta"]["data"] = world.models.read(run_id)["settings"]["data"]
        world.shell.library.store.commit_versions([version])
        published = await world.shell.home.create_from_library("ver-x", "strategy")
        for target in (strategy, published):
            data = target["settings"]["data"]
            instrument, series = await resolve_series(world.ctx, data["symbols"][0]["instrument"], data["timeframe"])
            assert instrument["displaySymbol"] == "AAPL" and series["adjustment"] == "split_dividend"
    run(scenario())


def test_the_memory_check_names_the_group_and_blocks(tmp_path, monkeypatch):
    pytest.skip("the check's wording is tested on the engine's answer in test_quant_model_prep (memory)")


def test_empty_window_names_the_input():
    pytest.skip("research environment: tests/native, with a late-starting input")


def test_work_figure_for_a_running_run_draws_finished_windows_only(tmp_path, monkeypatch):
    async def scenario():
        world = model_world(tmp_path, monkeypatch, windows=8)
        identity = await world.make()
        run_id = (await world.models.train(identity))["runs"][0]["id"]
        await wait_for(lambda: len(world.models.read(run_id).get("windows") or []) >= 2)
        figure = world.models.figures(run_id, ["model.work.windows"])["figures"]["model.work.windows"]
        done = len(world.models.read(run_id)["windows"])
        trace = next(item for item in figure["data"] if item["name"] == "Test")
        assert len(trace["x"]) >= 2 and len(trace["x"]) <= done + 1
        await world.models.stop(run_id)
    run(scenario())


def always_true_accounts(world, ids):
    """B1, B2, B3, B6 after each step."""
    models = world.models
    assert world.engine.peak <= models.queue.cpus
    for identity in ids:
        record = models.read(identity)
        cache = models.directory(identity) / "windows"
        finished = {int(path.parent.name) for path in cache.glob("*/window.json")} if cache.is_dir() else set()
        have_both = {int(path.parent.name) for path in cache.glob("*/test.npz")}
        assert finished == have_both or record["state"] in {"running", "preparing"}, "B3: a window's two files exist together"
        if record["state"] not in {"running", "preparing", "cancel_requested"}:
            assert {item["k"] for item in record.get("windows") or []} <= finished


@pytest.mark.parametrize("seed", range(6))
def test_the_accounts_hold_under_a_random_mix_of_actions(tmp_path, monkeypatch, seed):
    async def scenario():
        rng = random.Random(seed)
        world = model_world(tmp_path, monkeypatch, windows=4, cpus=2)
        identity = await world.make()
        ids, actions = [], []
        try:
            for step in range(40):
                action = rng.choice(["train", "batch", "stop", "stop_batch", "next", "remove", "resume", "resume_override", "close", "oom", "combine", "heldout"])
                actions.append(action)
                try:
                    if action == "train":
                        ids += [item["id"] for item in (await world.models.train(identity))["runs"]]
                    elif action == "batch":
                        started = await world.models.train(identity, {"vary": [{"name": "seed", "values": list(range(rng.randint(2, 6)))}]})
                        ids += [item["id"] for item in started["runs"]]
                        assert [item["group"] for item in started["runs"]] == list(range(1, len(started["runs"]) + 1)), "B2"
                    elif ids:
                        target = rng.choice(ids)
                        state = world.models.read(target)["state"]
                        if action == "stop":
                            await world.models.stop(target)
                        elif action == "next" and state == "queued":
                            await world.models.run_next(target)
                        elif action == "remove" and state == "queued":
                            await world.models.remove(target)
                        elif action in {"resume", "resume_override"}:
                            await world.models.resume(target, {"override": {"lgb_depth": 7}} if action == "resume_override" else None)
                        elif action == "close":
                            for item in list(world.models.tasks.values()):
                                item.cancel()
                            await asyncio.sleep(.05)
                            world.models.recover()
                        elif action == "oom":
                            world.engine.fail_at[target] = 2
                        elif action == "combine":
                            done = [item for item in ids if world.models.read(item)["state"] == "completed"][:2]
                            if len(done) == 2:
                                await world.models.combine(done)
                except ContractError:
                    pass
                await asyncio.sleep(rng.random() * .05)
                running = sum(1 for item in ids if world.models.read(item)["state"] in {"running", "preparing"})
                assert running <= world.models.queue.cpus, f"B1 at step {step}; seed {seed}; actions {actions}"
                always_true_accounts(world, ids)
            for state_check in ids:
                assert world.models.read(state_check)["state"] in {"queued", "preparing", "running", "completed", "failed", "cancelled", "interrupted", "cancel_requested"}
        except AssertionError as error:
            raise AssertionError(f"seed {seed}; actions {actions}") from error
        finally:
            for item in list(world.models.tasks.values()):
                item.cancel()
            await asyncio.gather(*list(world.models.tasks.values()), return_exceptions=True)
    run(scenario())


def test_a_run_left_running_by_a_killed_process_is_resumed_too(tmp_path, monkeypatch):
    async def scenario():
        world = model_world(tmp_path, monkeypatch, windows=40)
        identity = await world.make()
        run_id = (await world.models.train(identity))["runs"][0]["id"]
        await wait_for(lambda: len(world.models.read(run_id).get("windows") or []) >= 2)
        world.models.tasks[run_id].cancel()
        if task := world.models.tasks.get(run_id):
            await asyncio.gather(task, return_exceptions=True)
        await wait_for(lambda: world.models.read(run_id)["state"] == "interrupted")
        world.models.save(world.models.read(run_id), state="running", stopped=None)  # what a killed process leaves behind: no time to mark it
        world.models.tasks.pop(run_id, None)
        assert world.models.recover() == [run_id]
        record = await settled(world.models, run_id)
        assert record["state"] == "completed" and len(record["windows"]) == 40
    run(scenario())


def test_train_answers_with_the_run_at_once_and_the_check_is_its_first_step(tmp_path, monkeypatch):
    """Train ran the whole check (minutes) before any run existed; a blocked check refused the click instead of ending a run that says why."""
    async def scenario():
        world = model_world(tmp_path, monkeypatch, windows=3)
        identity = await world.make()
        gate = asyncio.Event()
        checked = world.models.check

        async def slow_check(object_id, args=None, report=None):
            await gate.wait()
            return await checked(object_id, args)
        world.models.check = slow_check
        started = await world.models.train(identity)
        run_id = started["runs"][0]["id"]
        record = world.models.read(run_id)
        assert (record["state"], record["stage"]) == ("preparing", "check")
        gate.set()
        assert (await settled(world.models, run_id))["state"] == "completed"

        async def blocked(object_id, args=None, report=None):
            return {"ok": True, "blocked": True, "checks": [{"name": "Memory", "value": "this run needs about 90.0 GB; runs get 24.0 GB", "need": "fits", "state": "block",
                                                              "group": "batch", "detail": "This run needs about 90.0 GB; runs on this computer get 24.0 GB in all, so it can never start."}]}
        world.models.check = blocked
        failed = (await world.models.train(identity))["runs"][0]["id"]
        record = await settled(world.models, failed)
        assert record["state"] == "failed" and record["failure"]["title"] == "Memory" and "can never start" in record["message"]
        assert record["failure"]["checks"][0]["state"] == "block" and not record.get("resumedAfterClose")
    run(scenario())
