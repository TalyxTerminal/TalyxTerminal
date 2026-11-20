"""Publishing a factor run: the sheet's plan (what changes against the last version, who uses it, the closest library factor, a taken name), the
checks that are listed and never block, future data that blocks and says when, one transaction, progress by step, cancelling, publishing twice,
a held-out period no check reads, and the numbers published being the run's own."""
import asyncio
import contextlib
import re

import pytest
from talyx_sdk import ContractError

from tests.unit.quant_library_fakes import (
    DAY,
    factor_spec,
    publish_and_wait,
    publishing,
    source_run,
    write_similar,
)


def run(coro):
    return asyncio.run(coro)


def test_unpassed_checks_are_listed_and_do_not_block(tmp_path):
    checks = [{"id": "turnover", "name": "Turnover", "value": 0.31, "need": "under 0.25", "state": "watch"}, {"id": "ic", "name": "Rank IC", "value": 0.02, "need": "over 0.01", "state": "ok"}]
    world = publishing(tmp_path, source=source_run(checks=checks))
    plan = world.publisher.plan_factor("run-src", "Reversal")
    assert [(item["name"], item["state"]) for item in plan["checks"]] == [("Turnover", "watch")]
    record = run(publish_and_wait(world))
    assert record["state"] == "completed" and world.store.find_item("Reversal")[0]["name"] == "Reversal"


def test_sheet_compares_with_the_previous_version_and_lists_its_users(tmp_path):
    world = publishing(tmp_path)
    previous = factor_spec("Reversal", "v1", 50)
    previous["checks"] = {"metric": "rank_ic", "value": 0.0153, "t": 2.0, "ls": 0.05, "turnover": 0.4, "names": 497, "heldOut": 0.01, "holding": 3}
    previous["meta"]["settings"] = {"holding": 3, "neutralize": "none"}
    world.store.commit_versions([previous])
    world.library.acquire("v1", "qobject:s1", "ws", kind="strategy", name="Reversal top 50", how="research, pinned v1")
    plan = world.publisher.plan_factor("run-src", "Reversal")
    assert (plan["version"], plan["runNumber"], plan["previous"]["version"]) == (2, 7, 1)
    rows = {row["measure"]: row for row in plan["compare"]}
    assert rows["Rank IC"]["previous"] == 0.0153 and rows["Rank IC"]["current"] == 0.021 and rows["Rank IC"]["change"] == pytest.approx(0.021 - 0.0153)
    assert rows["Rank IC"]["unit"] == "ic" and rows["Long–short net"]["unit"] == "pct" and rows["Names a day"]["unit"] == "count"
    assert (rows["Names a day"]["previous"], rows["Names a day"]["current"]) == (497, 497)
    assert (rows["Holding"]["previous"], rows["Holding"]["current"], rows["Holding"]["unit"], rows["Holding"]["change"]) == ("3D", "5D", "text", None)
    assert plan["users"] == [{"name": "Reversal top 50", "kind": "strategy"}]


def test_the_first_version_has_nothing_to_compare_with(tmp_path):
    plan = publishing(tmp_path).publisher.plan_factor("run-src", "Reversal")
    assert plan["version"] == 1 and plan["previous"] is None and plan["compare"] == [] and plan["users"] == []


@pytest.mark.parametrize("rho", [0.83, -0.78, 0.31])
def test_sheet_names_the_closest_library_factor(tmp_path, rho):
    world = publishing(tmp_path)
    world.store.commit_versions([factor_spec("Reversal v2 source", "r1", 50)])
    write_similar(world, "run-src", [{"name": "Size", "version": "Style", "rho": 0.9, "style": True}, {"name": "Reversal", "version": "v1", "rho": 0.99},
                                     {"name": "Reversal 7D", "version": "v2", "rho": rho}, {"name": "Value", "version": "v1", "rho": 0.1}])
    closest = world.publisher.plan_factor("run-src", "Reversal")["closest"]
    assert (closest["name"], closest["version"], closest["rho"]) == ("Reversal 7D", "v2", rho), "not a style and not this item's own version"
    assert closest["itemId"] is None or isinstance(closest["itemId"], str)


def test_no_closest_line_without_a_comparable_factor(tmp_path):
    world = publishing(tmp_path)
    assert world.publisher.plan_factor("run-src", "Reversal")["closest"] is None
    write_similar(world, "run-src", [{"name": "Size", "version": "Style", "rho": 0.9, "style": True}, {"name": "Reversal", "version": "v1", "rho": 0.5}])
    assert world.publisher.plan_factor("run-src", "Reversal")["closest"] is None


def test_a_name_that_is_another_kind_blocks_and_says_so(tmp_path):
    world = publishing(tmp_path)
    world.store.commit_versions([{**factor_spec("Momentum", "m1", 50, kind="model"), "meta": {**factor_spec("Momentum", "m1", 50, kind="model")["meta"]}}])
    plan = world.publisher.plan_factor("run-src", "Momentum")
    assert plan["blocked"] == {"reason": "Momentum is already a model in the library; choose another name."}
    with pytest.raises(ContractError, match="already a model"):
        run(world.publisher.start("run-src", {"name": "Momentum"}))
    assert world.publisher.plan_factor("run-src", "Reversal")["blocked"] is None


def test_future_data_blocks_and_commits_nothing(tmp_path):
    world = publishing(tmp_path)
    world.engine.future_at = 2
    world.engine.found = {"instrument": "AAPL", "withAll": 0.4, "whenCut": 0.1, "changed": 12}
    before = len(world.store.all_versions())
    record = run(publish_and_wait(world))
    assert record["state"] == "failed" and record["error"]["code"] == "future_data"
    said = re.search(r"AAPL's value on ([A-Z][a-z]{2} \d{1,2}, \d{4}) changes when data after ([A-Z][a-z]{2} \d{1,2}, \d{4}) is removed \(12 values change\)\.", record["message"])
    assert said and said.group(1) == said.group(2) and record["message"].endswith("The formula or code reads later data.")
    assert record["error"]["details"]["instrument"] == "AAPL" and record["progress"]["stage"] == "future" and record["progress"]["of"] == 3
    assert len(world.store.all_versions()) == before == 0 and world.store.summaries() == []
    assert not any(world.store.values_root.glob("*")) and "version" not in world.runs.read("run-src")


def test_publish_reports_each_step(tmp_path):
    world = publishing(tmp_path)
    seen = []
    original = world.publisher.progress

    def spy(identity, stage, step=None, of=None):
        seen.append((stage, step, of))
        return original(identity, stage, step, of)
    world.publisher.progress = spy
    record = run(publish_and_wait(world))
    assert record["state"] == "completed"
    stages = [stage for stage, _, _ in seen]
    assert stages[0] == "future" and stages[-1] == "commit" and "checks" not in stages
    assert [(step, of) for stage, step, of in seen if stage == "future"][-1] == (3, 3)
    assert world.runs.read("run-src")["version"]["version"] == 1


def test_cancel_mid_publish_leaves_nothing(tmp_path):
    world = publishing(tmp_path)
    world.engine.gate = asyncio.Event()

    async def scenario():
        record = await world.publisher.start("run-src", {"name": "Reversal"})
        task = world.runs.tasks[record["id"]]
        for _ in range(30):
            await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return world.runs.read(record["id"])
    record = run(scenario())
    assert record["state"] == "cancelled" and record["message"] == "Stopped before anything was added to the library."
    assert world.store.summaries() == [] and world.store.all_versions() == [] and "version" not in world.runs.read("run-src")


def test_publish_twice_is_one_version(tmp_path):
    world = publishing(tmp_path)
    world.engine.gate = asyncio.Event()

    async def scenario():
        first = await world.publisher.start("run-src", {"name": "Reversal"})
        with pytest.raises(ContractError, match="Already publishing #7") as refused:
            await world.publisher.start("run-src", {"name": "Reversal"})
        assert refused.value.code == "conflict"
        world.engine.gate.set()
        await asyncio.gather(world.runs.tasks[first["id"]], return_exceptions=True)
    run(scenario())
    assert [version["version"] for version in world.store.all_versions()] == [1]


def test_publish_checks_never_read_the_held_out_period(tmp_path):
    held = 90 * DAY
    world = publishing(tmp_path, source=source_run(held_from=held))
    record = run(publish_and_wait(world))
    assert record["state"] == "completed"
    cutoffs = world.engine.jobs[0]["cutoffs"]
    assert len(cutoffs) == 3 and all(cutoff < held for cutoff in cutoffs) and cutoffs == sorted(cutoffs)
    assert all(cutoff >= DAY for cutoff in cutoffs), "cut points lie in [since, held-out start)"



@pytest.mark.parametrize("factor_held, model_held", [(None, 60), (90, 60), (60, 90)])
def test_a_factor_published_with_a_model_is_cut_only_before_the_earlier_held_out_start(tmp_path, factor_held, model_held):
    """The factor's values are checked on the model's data: its future-data cuts stay out of the model's held-out period too."""
    world = publishing(tmp_path, source=source_run(held_from=factor_held and factor_held * DAY))
    source = world.runs.read("run-src")

    async def check():
        with contextlib.suppress(Exception):  # only the cuts the engine was given matter here
            await world.publisher.factor_values("model-run", source, tmp_path / "with-model", checks=3, ready=world.ready,
                                                report=lambda stage, done, total: None, held_out=model_held * DAY)
    run(check())
    earlier = min(start for start in (factor_held, model_held) if start) * DAY
    cutoffs = world.engine.jobs[0]["cutoffs"]
    assert len(cutoffs) == 3 and all(DAY <= cutoff < earlier for cutoff in cutoffs)

def test_a_strategy_run_cannot_be_published(tmp_path):
    world = publishing(tmp_path, source=source_run(kind="strategy"))
    with pytest.raises(ContractError):
        run(world.publisher.start("run-src", {"name": "Breakout"}))
    assert world.store.summaries() == []


def test_the_published_figures_are_the_runs_own_numbers_digit_for_digit(tmp_path):
    source = source_run(rank_ic=0.021437851234567)
    world = publishing(tmp_path, source=source)
    run(publish_and_wait(world))
    published = world.store.all_versions()[0]["meta"]["published"]
    assert published["value"] == source["numbers"]["rankIc"] and published["t"] == source["numbers"]["t"]
    # The library follows the same long-short after trade and borrow costs.
    assert published["ls"] == source["numbers"]["longShortNet"] and published["se"] == source["numbers"]["rankIc"] / source["numbers"]["t"]
    assert published["holding"] == 5, "the run's own holding, not one day"


def test_the_plan_is_read_on_the_loops_thread_because_the_run_records_belong_to_it(tmp_path):
    """Found in the real application: a plan read in a worker thread stopped at the records database's own-thread rule."""
    import threading
    from types import SimpleNamespace

    from fastapi import APIRouter

    from modules.quant.backend.library_routes import mount_library
    world = publishing(tmp_path, source=source_run())
    seen = []
    real = world.publisher.plan_factor
    world.publisher.plan_factor = lambda *args: (seen.append(threading.get_ident()), real(*args))[1]
    router = APIRouter()
    mount_library(router, SimpleNamespace(library=world.library, publisher=world.publisher, lists=None, common=None, notifications=None, history=None, source=None, home=None))
    endpoint = next(item.endpoint for item in router.routes if item.path == "/object-runs/{run_id}/publish-plan")

    async def scenario():
        plan = await endpoint("run-src", "Reversal")
        return plan, threading.get_ident()
    plan, loop_thread = run(scenario())
    assert plan["version"] == 1 and seen == [loop_thread]

