"""Quant's home and all objects: the run that stands for each object, its library versions, what needs a hand now and what runs or waits."""
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

from tests.unit.quant_library_fakes import build, close_of, factor_spec


class Objects:
    def __init__(self, records, items):
        self.records, self.items = records, items

    def list(self, args):
        return {"items": [dict(item) for item in self.items if args.get("all") or item["workspaceId"] == "ws"], "folders": []}

    def workspace(self):
        return "ws"


def run(identity, number, state="completed", *, starred=False, kind="run", rank_ic=0.03, created=None, message=None):
    return {"id": identity, "objectId": "o1", "objectKind": "factor", "name": "Momentum", "runKind": kind, "state": state, "starred": starred, "createdAt": created or (number or 0) * 1000,
            "number": number, "message": message, "numbers": {"rankIc": rank_ic, "t": 2.0, "longShortNet": 0.1, "turnover": 0.3}, "settings": {"run": {"holding": 5}}}


def home(tmp_path, tasks=None, queue=None):
    from modules.quant.backend.quant_home import QuantHome
    world = build(tmp_path)
    objects = Objects(world.records, [{"id": "o1", "kind": "factor", "name": "Momentum", "workspaceId": "ws", "updatedAt": 1}])
    services = SimpleNamespace(store=objects, library=world.library,
                               factors=SimpleNamespace(queue=SimpleNamespace(view=lambda: queue or {"running": [], "waiting": []})),
                               runs=SimpleNamespace(records=world.records, tasks=tasks or {}), loop=world.loop)
    return world, QuantHome(services)


def test_an_object_is_shown_by_its_published_run_else_its_starred_one_else_its_newest_successful_one_and_publishing_is_not_a_run(tmp_path):
    world, quant = home(tmp_path)
    for record in (run("r1", 1, rank_ic=0.01), run("r2", 2, starred=True, rank_ic=0.02), run("r3", 3, rank_ic=0.03), run("r4", 4, "failed", message="KeyError")):
        world.records.put("qrun", record)
    world.records.put("qrun", run("p1", None, kind="publish", created=9000))
    shown = quant.objects({})["items"][0]
    assert shown["representative"]["how"] == "starred" and shown["keyNumbers"]["rank_ic"] == 0.02 and shown["keyNumbers"]["horizon"] == "5D"
    assert shown["runsCount"] == 4 and shown["lastRun"]["id"] == "r4", "a publication is not one of the object's runs"
    world.records.put("qrun", run("r2", 2, rank_ic=0.02))
    shown = quant.objects({})["items"][0]
    assert shown["representative"]["how"] == "latest_successful" and shown["representative"]["run"] == "r3", "the newest successful run, not the newest"
    for identity, number in (("r1", 1), ("r2", 2), ("r3", 3)):
        world.records.put("qrun", run(identity, number, "failed"))
    nothing = quant.objects({})["items"][0]["representative"]
    assert nothing["how"] == "none" and nothing["note"].startswith("#4 failed")
    world.records.put("qrun", run("r3", 3))
    world.store.commit_versions([{**factor_spec("Momentum", "v1", 50), "objectId": "o1", "runId": "r1"}])
    world.store.commit_versions([{**factor_spec("Momentum", "v2", 50), "objectId": "o1", "runId": "r3"}])
    world.library.stop("v1")
    shown = quant.objects({})["items"][0]
    assert shown["representative"] == {"run": "r3", "number": 3, "how": "published", "version": "v2"}
    assert shown["library"]["text"] == "v2, v1 stopped"


def test_to_handle_lists_research_actions(tmp_path):
    from modules.quant.backend.library_loop import DELAY
    world, quant = home(tmp_path)
    world.store.commit_versions([factor_spec("Breadth", "b1", 50)])
    world.store.commit_versions([factor_spec("Momentum", "v1", 50)])
    world.store.commit_versions([factor_spec("Momentum", "v2", 50)])
    world.library.acquire("v1", "qobject:s1", "ws", kind="strategy", name="Momentum top 50", how="research, pinned v1")
    world.library.stop("v1")
    world.world.failing["b1"] = 10 ** 15
    world.clock["now"] = close_of(51) + DELAY + 60_000
    asyncio.run(world.loop.cycle())
    listed = quant.attention()["items"]
    assert [(item["subject"]["name"], item["state"]) for item in listed] == [("Breadth", "fail"), ("Momentum top 50", "pinned_stopped")]
    assert listed[0]["action"]["id"] == "retry"
    pinned = listed[-1]
    assert pinned["action"] == {"id": "switch_version", "label": "Switch to v2", "versionId": "v2", "itemId": world.store.find_item("Momentum")[0]["id"]}
    assert "reads Momentum v1, stopped" in pinned["why"] and pinned["why"].endswith("v2 is producing")


def test_the_queue_shows_runs_publications_library_updates_and_the_next_round(tmp_path):
    from modules.quant.backend.library_loop import DELAY
    tasks = {"pub-1": object()}
    queue = {"running": [{"owner": "r9", "label": "Momentum quick check", "state": "running", "started": 10}], "waiting": []}
    world, quant = home(tmp_path, tasks=tasks, queue=queue)
    world.records.put("qrun", {**run("r9", 9), "state": "running", "progress": {"steps": [{"state": "done"}] * 2}})
    world.records.put("qrun", {**run("pub-1", None, kind="publish"), "state": "running", "publish": {"name": "Momentum"}, "progress": {"stage": "checks", "step": 2, "of": 3}})
    world.store.commit_versions([factor_spec("Breadth", "b1", 50)])
    world.store.commit_versions([factor_spec("Value", "g1", 50)])
    world.store.commit_versions([factor_spec("Size", "s1", 50)])
    world.clock["now"] = close_of(51) + DELAY + 60_000

    async def scenario():
        world.queue.limit = asyncio.Semaphore(2)  # the machine has places for two
        world.world.gate = asyncio.Event()
        round_ = asyncio.create_task(world.loop.cycle())
        for _ in range(20):
            await asyncio.sleep(0)
        seen = quant.queue()
        world.world.gate.set()
        await round_
        return seen, quant.queue()
    during, after = asyncio.run(scenario())
    jobs = [(job["subject"]["name"], job["what"], job["state"]) for job in during["jobs"]]
    assert jobs[0] == ("Momentum", "Momentum quick check", "running") and during["jobs"][0]["progress"] == 0.29
    assert ("Momentum", "Publishing Momentum, check 2 of 3", "running") in jobs
    assert sum(1 for job in jobs if job[1] == "Library update") == 2 and sum(1 for job in jobs if job[1].startswith("Library update, waiting")) == 1
    # the next round is not a job: nothing runs or waits in the queue for it, so the count and the rows agree
    assert all(job["subject"]["name"] != "Library production" for job in after["jobs"]) and after["production"]["next"] and after["production"]["lastRound"]["versions"] == 3


# ---------------------------------------------------------------------------------------------------------------------------------------
# L segment additions
# ---------------------------------------------------------------------------------------------------------------------------------------
class Created:
    """The object store as `create_from_library` uses it: create, read, save, detail."""

    def __init__(self):
        self.saved, self.made = [], {}

    async def create(self, args):
        self.created = args
        self.made = {"id": "new-1", "kind": args["kind"], "name": args["name"], "workspaceId": "ws", "settings": {"run": {"holding": 1}}}
        return {"id": "new-1"}

    def read(self, identity):
        return self.made

    def save(self, target, **changes):
        self.made = {**target, **changes}
        self.saved.append(changes)
        return self.made

    def detail(self, target):
        return target

    def workspace(self):
        return "ws"

    async def start_from(self, kind, name, *, data, preset=None, run=None, params=None, keep=None):
        self.started = {"kind": kind, "name": name, "data": data, "preset": preset, "params": params, "keep": keep}
        self.made = {"id": "new-1", "kind": kind, "name": name, "workspaceId": "ws", "settings": {"data": data, "params": params, **(keep or {})}}
        if kind == "strategy":
            self.on_strategy_pins(self.made)
        return self.made


def test_create_from_library_pins_the_version_and_copies_the_data_settings(tmp_path):
    import pytest
    from talyx_sdk import ContractError
    world, quant = home(tmp_path)
    spec = factor_spec("Reversal", "v1", 50)
    spec["meta"]["data"] = {"symbols": [{"instrument": {"displaySymbol": "AAPL"}}], "timeframe": "1d", "since": "2020-01-01", "until": "2026-11-11", "untilLatest": True,
                            "heldOutFrom": "2024-01-01"}
    world.store.commit_versions([spec])
    quant.services.store = Created()
    quant.services.store.on_strategy_pins = world.library.sync_strategy_users
    made = asyncio.run(quant.create_from_library("v1", "strategy"))
    started = quant.services.store.started
    # the strategy's own entry takes the item's range as the object takes it (to the latest close, not the internal
    # `untilLatest` it refused on every later change), and the item's held-out stretch stays held out.
    assert started["data"] == {"symbols": [{"instrument": {"displaySymbol": "AAPL"}}], "timeframe": "1d", "since": "2020-01-01", "until": "latest", "heldOutFrom": "2024-01-01"}
    # A strategy starts from Top N with the item as its signal, pinned the way a run pins it (the empty skeleton read nothing).
    assert started["preset"] == "ranked" and started["params"]["signal"] == "Reversal"
    assert started["keep"]["inputs"] == {"library": {"signal": {"name": "Reversal", "versionId": "v1"}}, "models": {}}
    users = world.store.users("v1")
    assert [(user["owner_id"], user["how"]) for user in users] == [("qobject:new-1", "research, pinned")] and made["name"] == "Reversal v1 strategy"
    asyncio.run(quant.create_from_library("v1", "allocation"))
    assert "preset" not in quant.services.store.created and quant.services.store.saved[-1]["settings"]["inputs"][0]["versionId"] == "v1"
    asyncio.run(quant.create_from_library("v1", "model"))
    # a model reads its inputs from its settings' inputs table ('s other way in)
    assert quant.services.store.started["params"] == {"inputs": [{"source": "library", "ref": "v1", "name": "Reversal"}]}
    world.library.release("v1", "qobject:new-1")
    world.library.stop("v1")
    with pytest.raises(ContractError, match="stopped"):
        asyncio.run(quant.create_from_library("v1", "model"))
    with pytest.raises(ContractError):
        asyncio.run(quant.create_from_library("v1", "factor"))


def test_an_object_without_a_successful_run_shows_none(tmp_path):
    world, quant = home(tmp_path)
    shown = quant.objects({})["items"][0]
    assert shown["representative"]["how"] == "none" and shown["keyNumbers"] is None and shown["runsCount"] == 0
    world.records.put("qrun", run("r1", 1, "failed", message="KeyError: 'x'"))
    shown = quant.objects({})["items"][0]
    assert shown["representative"]["how"] == "none" and shown["representative"]["note"] == "#1 failed: KeyError: 'x'" and shown["keyNumbers"] is None


def test_every_object_pinned_on_a_stopped_version_is_listed_however_old(tmp_path):
    world, quant = home(tmp_path)
    world.store.commit_versions([factor_spec("Momentum", "v1", 50)])
    world.store.commit_versions([factor_spec("Momentum", "v2", 50)])
    world.library.acquire("v1", "qobject:s1", "ws", kind="strategy", name="Old study", how="research, pinned v1")
    world.library.stop("v1")
    world.clock["now"] += 200 * 86_400_000  # the version has been stopped for more than half a year
    listed = quant.attention()["items"]
    assert [(item["subject"]["name"], item["state"]) for item in listed] == [("Old study", "pinned_stopped")]
    # An archived version is not listed: the object reports it when it runs.
    world.library.release("v1", "qobject:s1")
    asyncio.run(world.library.archive("v1"))
    assert quant.attention()["items"] == []


def test_recent_shows_each_kinds_key_number_and_publishing_is_not_a_run(tmp_path):
    world, quant = home(tmp_path)
    kinds = [("o1", "factor", "Momentum"), ("o2", "model", "Blend"), ("o3", "strategy", "Breakout"), ("o4", "allocation", "Risk parity")]
    quant.services.store.items = [{"id": identity, "kind": kind, "name": name, "workspaceId": "ws", "updatedAt": index} for index, (identity, kind, name) in enumerate(kinds, 1)]
    world.records.put("qrun", run("f1", 1, rank_ic=0.031))
    world.records.put("qrun", {**run("m1", 1, kind="train"), "objectId": "o2", "objectKind": "model", "numbers": {"ric": 0.04, "icir": 0.5}})
    world.records.put("qrun", {**run("s1", 1), "objectId": "o3", "objectKind": "strategy", "numbers": None, "metrics": {"cagr": 0.050585, "totalReturn": 0.10373, "sharpe": 1.1, "maxDrawdown": -0.12}})
    world.records.put("qrun", {**run("a1", 1), "objectId": "o4", "objectKind": "allocation", "numbers": None, "metrics": {"sharpe": 0.9, "volatility": 0.08}})
    world.records.put("qrun", run("p1", None, kind="publish", created=10 ** 9))
    world.records.put("qrun", {**run("scan", 2, kind="sweep", starred=True), "objectId": "o3"})
    found = {item["id"]: item for item in quant.recent(10)}
    assert found["o1"]["keyNumbers"]["rank_ic"] == 0.031 and found["o2"]["keyNumbers"]["test_rank_ic"] == 0.04
    assert (found["o3"]["keyNumbers"]["return_a_year"], found["o3"]["keyNumbers"]["sharpe"]) == (0.050585, 1.1) and found["o4"]["keyNumbers"]["sharpe"] == 0.9
    assert found["o1"]["lastRun"]["id"] == "f1", "the publication is not the object's last run"


def test_all_objects_can_be_this_workspace_or_every_workspace(tmp_path):
    world, quant = home(tmp_path)
    quant.services.store.items = [{"id": "o1", "kind": "factor", "name": "Mine", "workspaceId": "ws", "updatedAt": 1},
                                  {"id": "o2", "kind": "factor", "name": "Elsewhere", "workspaceId": "other", "updatedAt": 2}]
    here, everywhere = quant.objects({}), quant.objects({"scope": "all"})
    assert [item["name"] for item in here["items"]] == ["Mine"] and [item["name"] for item in everywhere["items"]] == ["Mine", "Elsewhere"]
    assert [item["inThisWorkspace"] for item in everywhere["items"]] == [True, False]


def test_switch_version_points_an_object_at_a_producing_version_of_the_same_item(tmp_path):
    import pytest
    from talyx_sdk import ContractError

    from tests.fixtures.strategy.fakes import services, strategy

    async def scenario():
        _, records, shell = services(tmp_path)
        try:
            library = shell.library
            for name, ref in (("Reversal", "v1"), ("Reversal", "v2"), ("Other", "x")):
                library.store.commit_versions([factor_spec(name, ref, 50)])
            source = 'import talyx_quant as q\nsignal = q.library_param("signal", "Reversal")\nother = q.library_param("other", "Other")'
            identity = await strategy(shell, source)
            obj = shell.store.read(identity)
            legacy = [{"kind": "library", "name": name, "versionId": ref} for name, ref in (("Reversal", "v1"), ("Other", "x"))]
            shell.store.save(obj, settings={**obj["settings"], "inputs": legacy})
            library.stop("v1")
            moved = shell.home.switch_version(identity, "v2")
            assert moved["version"] == 2
            pins = shell.store.read(identity)["settings"]["inputs"]["library"]
            assert pins == {"signal": {"name": "Reversal", "versionId": "v2"}, "other": {"name": "Other", "versionId": "x"}}
            assert library.store.held_by("qobject:" + identity) == {"v2", "x"}
            assert not library.users("v1")
            library.stop("v2")
            with pytest.raises(ContractError, match="producing"):
                shell.home.switch_version(identity, "v2")
        finally:
            records.close()
    asyncio.run(scenario())


def test_a_notebook_shows_its_cells_and_what_it_reads_and_a_model_its_test_rank_ic_icir_and_horizon(tmp_path):
    world, quant = home(tmp_path)
    notebook = tmp_path / "study.ipynb"
    cell = lambda kind, source: {"cell_type": kind, "id": source[:6] or kind, "source": source, "metadata": {}, **({"outputs": [], "execution_count": None} if kind == "code" else {})}
    notebook.write_text(json.dumps({"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [
        cell("markdown", "# Why it weakened"), cell("code", "rev = q.library.read('Reversal 1M', version=2)"), cell("code", "run = q.run(run_id='run-21')\nrun.numbers")]}), encoding="utf-8")
    quant.services.store.items = [{"id": "n1", "kind": "notebook", "name": "Study", "workspaceId": "ws", "updatedAt": 1}, {"id": "o2", "kind": "model", "name": "Blend", "workspaceId": "ws", "updatedAt": 2}]
    quant.services.store.read = lambda identity: {"directory": str(tmp_path), "entry": "study.ipynb"}
    quant.services.store.source_path = lambda record: Path(record["directory"]) / record["entry"]
    world.records.put("qrun", {**run("m1", 1), "objectId": "o2", "objectKind": "model", "horizon": 5, "numbers": {"ric": 0.04, "icir": 0.31, "lsNet": 0.07},
                               "metrics": {"rankIc": 0.04, "t": 2.1, "longShortNet": 0.07}})
    found = {item["id"]: item for item in quant.objects({})["items"]}
    assert (found["n1"]["cells"], found["n1"]["reads"]) == (3, "Reversal 1M v2, run-21")
    assert found["o2"]["keyNumbers"] == {"test_rank_ic": 0.04, "icir": 0.31, "long_short_net": 0.07, "horizon": "5D"}
    notebook.write_text("not a notebook", encoding="utf-8")
    os.utime(notebook, ns=(notebook.stat().st_atime_ns, notebook.stat().st_mtime_ns + 10 ** 9))
    assert quant.objects({})["items"][0]["cells"] is None, "a changed file is read again; one Quant cannot read shows no cells"


def test_an_allocation_is_shown_by_its_newest_completed_backtest_with_its_sharpe_return_and_volatility(tmp_path):
    world, quant = home(tmp_path)
    quant.services.store.items = [{"id": "o1", "kind": "allocation", "name": "Risk parity", "workspaceId": "ws", "updatedAt": 1}]
    world.records.put("qrun", {**run("a1", 1, "failed", kind="backtest"), "objectKind": "allocation", "numbers": None})
    world.records.put("qrun", {**run("a2", 2, kind="backtest"), "objectKind": "allocation", "numbers": None, "metrics": {"cagr": 0.10, "sharpe": 1.29, "vol": 0.078, "maxDrawdown": -0.07}})
    shown = quant.objects({})["items"][0]
    assert shown["representative"]["how"] == "latest_successful" and shown["representative"]["run"] == "a2"
    assert shown["keyNumbers"] == {"sharpe": 1.29, "return_a_year": 0.10, "volatility": 0.078, "max_drawdown": -0.07}


def test_one_kind_holds_each_name_once(tmp_path):
    """two "Top N by signal" could not be told apart in the lists: a second one takes the next number, and a rename onto a name in use is refused."""
    import asyncio

    import pytest
    from talyx_sdk import ContractError

    from tests.unit.test_quant_object_runs import setup

    async def scenario():
        _, _, store, _ = setup(tmp_path, None)
        first = await store.create({"kind": "strategy", "name": "Top N by signal"})
        second = await store.create({"kind": "strategy", "name": "Top N by signal"})
        other = await store.create({"kind": "factor", "name": "Top N by signal"})
        with pytest.raises(ContractError) as refused:
            store.update(second["id"], {"name": "Top N by signal"})
        return first["name"], second["name"], other["name"], refused.value.code, store.update(first["id"], {"name": "Top N by signal"})["name"]

    first, second, other, code, kept = asyncio.run(scenario())
    assert (first, second, other, code, kept) == ("Top N by signal", "Top N by signal 2", "Top N by signal", "conflict", "Top N by signal")
