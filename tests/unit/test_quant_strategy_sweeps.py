"""A strategy's sweep: the combinations and their limits, the run settings that can be swept, how many run at once on a machine, a sweep that is
stopped and goes on, what the page reads (cells, neighbours, the edge, the pictures), and Use these values. No engine: the combinations are run by a
stand-in that writes the records a real combination would."""
import asyncio
import json

import pytest
from talyx_sdk import ContractError

from modules.quant.backend import object_sweeps as S
from modules.quant.backend import sweep_figures as G
from modules.quant.backend.run_queue import RunQueue
from tests.fixtures.strategy.fakes import services, strategy
from tests.unit.test_quant_object_runs import STARTER

GB = 1024 ** 3
DEFINITIONS = [{"name": "lookback", "type": "int", "label": "Lookback", "min": 12, "max": 240}, {"name": "stop", "type": "float", "label": "Stop", "min": 0.5, "max": 6.0},
               {"name": "shorts", "type": "bool", "label": "Short side", "min": None, "max": None}]


def axes_of(**values):
    return [{"name": name, "values": found} for name, found in values.items()]


# ---------------------------------------------------------------------------------------------------------------- the combinations
def test_a_sweep_of_500_combinations_is_not_refused():
    """Three parameters, 10 × 10 × 5: a sweep runs every combination; only one expected to take more than ten minutes asks first (on the page)."""
    definitions = [*DEFINITIONS, {"name": "fast", "type": "int", "label": "Fast", "min": 1, "max": 50}]
    found, combinations = S.expand(axes_of(lookback=list(range(12, 22)), stop=[round(0.5 + step * 0.5, 1) for step in range(10)], fast=[2, 4, 6, 8, 10]), definitions)
    assert len(combinations) == 500 and len({tuple(sorted(item.items())) for item in combinations}) == 500
    found, combinations = S.expand(axes_of(lookback=list(range(12, 32)), stop=[1.0] * 1 + [2.0] * 0), DEFINITIONS)
    assert len(combinations) == 20


@pytest.mark.parametrize("axes, code, message", [
    (axes_of(lookback=[24, 300]), "value_outside_declared", "Lookback 300 is outside 12–240"),
    (axes_of(lookback=[5]), "value_outside_declared", "Lookback 5 is outside 12–240"),
    (axes_of(stop=[0.1, 2.0]), "value_outside_declared", "Stop 0.1 is outside 0.5–6"),
    (axes_of(lookback=[24, 36.5]), "value_outside_declared", "Lookback 36.5 is not a whole number"),
    (axes_of(nope=[1, 2]), "invalid_input", "no parameter 'nope'"),
    (axes_of(shorts=[1, 0]), "invalid_input", "swept over on and off"),  # a switch sweeps over true and false
    (axes_of(lookback=[24, 24]), "invalid_input", "lists a value twice"),
    ([{"name": "lookback", "values": [24]}, {"name": "lookback", "values": [48]}], "invalid_input", "swept twice"),
    ([], "invalid_input", "Choose 1 to 8"),
    (axes_of(**{"@fill": ["sometimes"]}), "invalid_input", "@fill values"),
    (axes_of(**{"@costMultiple": [-1]}), "invalid_input", "0 to 10"),
    (axes_of(**{"@capital": [0]}), "invalid_input", "above zero"),
    (axes_of(**{"@period": [{"since": "2024-05-01", "until": "2024-01-01"}]}), "invalid_input", "not before"),
    (axes_of(**{"@slippage": [1]}), "invalid_input", "can vary these run settings"),
])
def test_sweep_value_outside_declared(axes, code, message):
    with pytest.raises(ContractError, match=message) as error:
        S.expand(axes, DEFINITIONS)
    assert error.value.code == code


def test_expand_is_every_mix_in_order_and_sorted_values():
    found, combinations = S.expand(axes_of(lookback=[48, 24], stop=[1.5, 3]), DEFINITIONS)
    assert [axis["values"] for axis in found] == [[24, 48], [1.5, 3.0]] and [axis["label"] for axis in found] == ["Lookback", "Stop"]
    assert combinations == [{"lookback": 24, "stop": 1.5}, {"lookback": 24, "stop": 3.0}, {"lookback": 48, "stop": 1.5}, {"lookback": 48, "stop": 3.0}]


def test_sweep_run_settings_axes():
    found, combinations = S.expand(axes_of(**{"@fill": ["nextOpen", "nextClose"], "@costMultiple": [0, 1, 2], "@capital": [10_000, 100_000]}), DEFINITIONS)
    assert [axis["label"] for axis in found] == ["Fill", "Costs ×", "Capital"] and len(combinations) == 12
    code, overrides = S.split({"lookback": 24, "@fill": "nextClose", "@costMultiple": 2})
    assert code == {"lookback": 24} and overrides == {"fill": "nextClose", "costMultiple": 2}
    run = {"fill": "nextOpen", "capital": 10_000, "costs": {"feePct": 0.05, "makerPct": 0.02, "halfSpreadBps": 2.0, "impact": 0.3, "borrowPct": None, "funding": True}}
    changed = S.case_settings(run, overrides)
    assert changed["fill"] == "nextClose" and changed["costs"]["feePct"] == pytest.approx(0.1) and changed["costs"]["halfSpreadBps"] == 4.0 and changed["costs"]["borrowPct"] is None
    assert run["costs"]["feePct"] == 0.05  # the sweep's own settings are not touched
    assert S.case_settings(run, {"capital": 50_000})["capital"] == 50_000
    prepared = {"runSettings": run, "venues": [{"name": "OKX", "balances": ["10000 USDT"], "fees": {"type": "maker_taker", "maker": 0.0002, "taker": 0.0005}}],
                "instruments": [{"id": "A", "makerFee": "0.0002", "takerFee": "0.0005"}]}
    zero = S.case_prepared(prepared, {"costMultiple": 0})
    assert float(zero["venues"][0]["fees"]["taker"]) == 0 and float(zero["instruments"][0]["takerFee"]) == 0 and float(zero["instruments"][0]["makerFee"]) == 0
    twice = S.case_prepared(prepared, {"costMultiple": 2})
    assert float(twice["venues"][0]["fees"]["taker"]) == pytest.approx(0.001) and float(twice["venues"][0]["fees"]["maker"]) == pytest.approx(0.0004)
    bigger = S.case_prepared(prepared, {"capital": 100_000})
    assert bigger["venues"][0]["balances"] == ["100000 USDT"] and S.case_prepared(prepared, {}) is prepared
    carried = S.case_prepared(prepared, {}, scale=1.25)
    assert carried["venues"][0]["balances"] == ["12500 USDT"]


def test_sweep_metrics():
    assert {"sharpe", "cagr", "totalReturn", "maxDrawdown", "trades", "profitFactor", "winRate"} == set(S.METRICS)


# ---------------------------------------------------------------------------------------------------------------- a sweep as a record
def scenario(tmp_path, body, **more):
    async def run():
        ctx, records, shell = services(tmp_path, **more)
        try:
            return await body(ctx, shell)
        finally:
            records.close()
    return asyncio.run(run())


class Runner:
    """Stands in for the engine: a combination's run record is written as a real one is, its numbers come from its values, and what runs at once is counted."""

    def __init__(self, shell, *, delay=0.01, fail=lambda params: False, stop_after=None):
        self.shell, self.delay, self.fail, self.stop_after = shell, delay, fail, stop_after
        self.active = self.peak = 0
        self.ran = []
        shell.sweeps.prepare = self.prepare
        shell.sweeps.reprepare = self.prepare
        shell.sweeps.run_case = self.run_case

    async def prepare(self, identity, obj, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.shell.runs.update(identity, code={"entry": obj["entry"], "digest": "d" * 64, "files": [], "snapshotPath": str(directory), "directory": obj["directory"]},
                               data={"runSettings": {"capital": 10_000, "fill": "nextOpen"}, "sources": [], "library": [], "models": []},
                               environment={"state": "ready", "preparationId": "prep"})
        return {"runSettings": {"capital": 10_000}, "venues": [], "instruments": []}, {}

    @staticmethod
    def metrics(params):
        lookback, stop = params.get("lookback", 48), params.get("stop", 2.0)
        return {"sharpe": round(1.0 + lookback / 100 - abs(stop - 2) / 4, 4), "totalReturn": lookback / 200, "maxDrawdown": -stop / 10, "trades": int(lookback), "cagr": 0.1,
                "profitFactor": 1.5, "winRate": 0.5}

    async def run_case(self, sweep_id, obj, directory, prepared, state, *, identity, params, overrides, link, extra, scale=None, on_start=None):
        runs = self.shell.runs
        record = runs.read(sweep_id)
        base = {key: value for key, value in record.items() if key not in ("of", "walk")}
        runs.records.put("qrun", {**base, "id": identity, "runKind": "case", "mode": "run", "state": "running", "stage": "backtest", "number": None, "params": params,
                                  "overrides": overrides, "metrics": {}, "sweep": link, "message": None, "error": None, "endedAt": None})
        if on_start:
            on_start()
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(self.delay)
            self.ran.append(link.get("index", link.get("window")))
            if self.stop_after is not None and len(self.ran) > self.stop_after:
                raise asyncio.CancelledError
            if self.fail(params):
                return runs.update(identity, state="failed", message="ValueError: boom", endedAt=1)
            return runs.update(identity, state="completed", metrics=self.metrics({**params, **{"@" + key: value for key, value in overrides.items()}}), endedAt=1)
        finally:
            self.active -= 1


async def started(shell, axes, *, queue=None, memory=GB, **args):
    shell.sweeps.queue = shell.runs.queue = queue or RunQueue(memory=64 * GB, cpus=8)

    async def per_case(obj, settings, newest):
        return memory
    shell.sweeps.case_memory = per_case
    obj = await strategy(shell, STARTER)
    record = await shell.sweeps.start(obj, {"axes": axes, **args})
    return obj, record


async def settled(shell, identity):
    while identity in shell.runs.tasks:
        await asyncio.sleep(0.01)
    return shell.runs.read(identity)


AXES = axes_of(fast=[5, 10, 20], slow=[30, 60])


def test_sweep_does_not_select_best(tmp_path):
    async def body(ctx, shell):
        runner = Runner(shell)
        obj, record = await started(shell, AXES)
        done = await settled(shell, record["id"])
        assert done["state"] == "completed" and len(runner.ran) == 6
        detail = shell.sweeps.detail(done["id"])
        assert detail["selected"] is None and detail["bestIndex"] is not None and done["metrics"] == {}
        best = max(detail["cases"], key=lambda case: case["metrics"]["sharpe"])
        assert detail["bestIndex"] == best["index"]
        # Looking at one cell is the user's act, and it is only a look.
        looked = shell.sweeps.select(done["id"], 2)
        assert looked["selected"] == 2 and looked["bestIndex"] == detail["bestIndex"] and shell.runs.read(done["id"])["metrics"] == {}
        view = shell.strategy.run(done["id"])["run"]
        assert view["sweep"]["best"]["index"] == detail["bestIndex"] and view["stats"] is None and view["kind"] == "sweep"
    scenario(tmp_path, body)


@pytest.mark.parametrize("cores, memory_gb, per_case_gb, expect, limit", [
    (16, 64, 1, 8, "cores"), (16, 64, 3, 8, "cores"), (4, 8, 1, 2, "cores"), (4, 8, 3, 1, "memory"), (32, 16, 3, 2, "memory"), (32, 64, 4, 8, "memory")])
def test_sweep_concurrency_follows_machine(tmp_path, cores, memory_gb, per_case_gb, expect, limit):
    machine = RunQueue(memory=memory_gb * GB, cpus=cores // 2)
    assert machine.budget == min(memory_gb / 2, memory_gb - 4) * GB
    assert S.concurrency(machine, per_case_gb * GB) == {"atOnce": expect, "limit": limit}

    async def body(ctx, shell):
        runner = Runner(shell, delay=0.05)
        obj, record = await started(shell, axes_of(fast=list(range(5, 45, 5)), slow=[30, 60]), queue=machine, memory=per_case_gb * GB)
        assert record["sweep"]["atOnce"] == expect and record["sweep"]["limit"] == limit
        await settled(shell, record["id"])
        assert runner.peak == expect  # what ran together is what the machine allows, not a fixed number
        detail = shell.sweeps.detail(record["id"])
        assert detail["machine"] == {"memory": memory_gb * GB, "budget": machine.budget, "cores": cores // 2} and detail["concurrency"]["atOnce"] == expect
    scenario(tmp_path, body)


def test_sweep_concurrency_changes_with_the_machine_not_a_constant(tmp_path):
    assert {S.concurrency(RunQueue(memory=64 * GB, cpus=8), GB)["atOnce"], S.concurrency(RunQueue(memory=8 * GB, cpus=2), GB)["atOnce"]} == {8, 2}


def test_case_over_budget_runs_one_at_a_time(tmp_path):
    async def body(ctx, shell):
        Runner(shell)
        shell.sweeps.queue = shell.runs.queue = RunQueue(memory=16 * GB, cpus=4)

        async def per_case(obj, settings, newest):
            return 9 * GB
        shell.sweeps.case_memory = per_case
        shell.sweeps.seconds_of = lambda run: 60
        obj = await strategy(shell, STARTER)
        found = await shell.sweeps.estimate(obj, {"axes": AXES})
        assert found["atOnce"] == 1 and found["limit"] == "memory"
    scenario(tmp_path, body)


def test_sweep_estimate_shown(tmp_path):
    async def body(ctx, shell):
        Runner(shell)
        shell.sweeps.queue = shell.runs.queue = RunQueue(memory=64 * GB, cpus=3)

        async def per_case(obj, settings, newest):
            return 2 * GB
        shell.sweeps.case_memory = per_case
        shell.sweeps.seconds_of = lambda run: 240
        obj = await strategy(shell, STARTER)
        found = await shell.sweeps.estimate(obj, {"axes": axes_of(fast=list(range(5, 35, 5)), slow=[30, 60, 90, 120, 150])})
        assert found["combinations"] == 30 and found["atOnce"] == 3 and found["perCase"] == 240 and found["seconds"] == 10 * 240
        assert found["machine"]["budget"] == 32 * GB
    scenario(tmp_path, body)


def test_sweep_cases_wait_behind_interactive(tmp_path):
    async def body(ctx, shell):
        runner = Runner(shell)
        queue = RunQueue(memory=64 * GB, cpus=2)
        release = asyncio.Event()

        async def person():
            async with queue.slot(owner="person", label="a person's run", interactive=True, memory=40 * GB):
                await release.wait()
        waiting = asyncio.create_task(person())
        await asyncio.sleep(0.05)
        obj, record = await started(shell, AXES, queue=queue, memory=GB)
        await asyncio.sleep(0.2)
        assert runner.ran == []  # the person's run holds the budget; the sweep waits in the queue, visibly
        assert [item["interactive"] for item in queue.view()["waiting"]] == [False] * 6
        release.set()
        await waiting
        done = await settled(shell, record["id"])
        assert done["state"] == "completed" and len(runner.ran) == 6
    scenario(tmp_path, body)


def test_all_cases_failed(tmp_path):
    async def body(ctx, shell):
        Runner(shell, fail=lambda params: True)
        obj, record = await started(shell, AXES)
        done = await settled(shell, record["id"])
        assert done["state"] == "failed" and done["message"] == "All 6 combinations failed; the first one: ValueError: boom"
        assert done["error"]["code"] == "no_result" and done["error"]["details"]["first"] == 0
    scenario(tmp_path, body)


def test_failed_cases_skipped_in_neighbours(tmp_path):
    async def body(ctx, shell):
        Runner(shell, fail=lambda params: params["fast"] == 10 and params["slow"] == 60)
        obj, record = await started(shell, axes_of(fast=[5, 10, 20], slow=[30, 60, 90]))
        done = await settled(shell, record["id"])
        assert done["state"] == "completed"
        detail = shell.sweeps.detail(done["id"])
        failed = next(case for case in detail["cases"] if case["state"] == "failed")
        assert failed["params"] == {"fast": 10, "slow": 60}
        centre = failed
        around = next(case for case in detail["cases"] if case["params"] == {"fast": 10, "slow": 30})
        assert around["neighbourCount"] == 4  # five neighbours in the grid, one of them failed
        assert centre["neighbours"] is not None and centre["neighbourCount"] == 8
    scenario(tmp_path, body)


def test_one_axis_heatmap_and_neighbours(tmp_path):
    async def body(ctx, shell):
        Runner(shell)
        obj, record = await started(shell, axes_of(fast=[5, 10, 20, 40]))
        done = await settled(shell, record["id"])
        detail = shell.sweeps.detail(done["id"])
        heat = detail["figures"]["heat"]
        assert heat["data"][0]["y"] == [""] and len(heat["data"][0]["z"]) == 1 and len(heat["data"][0]["z"][0]) == 4
        counts = {case["params"]["fast"]: case["neighbourCount"] for case in detail["cases"]}
        assert counts == {5: 1, 10: 2, 20: 2, 40: 1}
    scenario(tmp_path, body)


def test_heatmap_holds_other_axes(tmp_path):
    async def body(ctx, shell):
        Runner(shell)
        obj, record = await started(shell, axes_of(fast=[5, 10], slow=[30, 60], stake=[1.0, 2.0]))
        done = await settled(shell, record["id"])
        shell.sweeps.select(done["id"], 5)
        picked = shell.sweeps.detail(done["id"])["cases"][5]["params"]
        heat = shell.sweeps.detail(done["id"], view={"x": "fast", "y": "slow"})["figures"]["heat"]
        meta = heat["layout"]["meta"]
        assert (meta["x"], meta["y"]) == ("fast", "slow") and meta["heldValues"] == {"stake": picked["stake"]} and meta["held"] == {"stake": f"{picked['stake']:g}"}
        other = shell.sweeps.detail(done["id"], view={"x": "stake", "y": "fast"})["figures"]["heat"]["layout"]["meta"]
        assert other["heldValues"] == {"slow": picked["slow"]}
        z = shell.sweeps.detail(done["id"], view={"x": "fast", "y": "slow"})["figures"]["heat"]["data"][0]["z"]
        assert len(z) == 2 and all(len(row) == 2 for row in z)
    scenario(tmp_path, body)


def test_heat_is_neutral_single_hue(tmp_path):
    async def body(ctx, shell):
        Runner(shell, fail=lambda params: params["fast"] == 5 and params["slow"] == 30)
        obj, record = await started(shell, AXES)
        done = await settled(shell, record["id"])
        heat = shell.sweeps.detail(done["id"])["figures"]["heat"]
        trace = heat["data"][0]
        assert trace["type"] == "heatmap" and trace["meta"] == {"role": "group", "key": "heat"} and "colorscale" not in trace and "zmid" not in trace
        assert trace["z"][0][0] is None  # the failed cell is not a number
        marks = heat["data"][1]
        assert marks["meta"]["key"] == "failed" and marks["customdata"][0][1] == "Failed: ValueError: boom"
        assert all(item["meta"]["role"] != "updown" for item in heat["data"]) and heat["layout"]["meta"]["key"] == "strategy.sweep.heat"
    scenario(tmp_path, body)


def test_every_figure_reads_out_like_the_draft(tmp_path):
    async def body(ctx, shell):
        Runner(shell)
        obj, record = await started(shell, AXES)
        done = await settled(shell, record["id"])
        for metric, text in (("sharpe", "Sharpe: %{z:.2f}"), ("totalReturn", "Return: %{z:+.0%}"), ("maxDrawdown", "Max DD: %{z:.0%}"), ("trades", "Trades: %{z:d}")):
            trace = shell.sweeps.detail(done["id"], view={"x": "fast", "y": "slow", "metric": metric})["figures"]["heat"]["data"][0]
            assert trace["hovertemplate"] == f"<b>%{{customdata[0]}}</b><br>{text}<extra></extra>"
        title = shell.sweeps.detail(done["id"], view={"x": "fast", "y": "slow"})["figures"]["heat"]["data"][0]["customdata"][0][0][0]
        assert title == "Fast window 5 · Slow window 30"
        # The cell's equity: dates, the picked cell as a signed return to one place, the run it started from dashed.
        directory = shell.runs.directory(done["sweep"]["cases"][0]["runId"] if "sweep" in done else shell.runs.read(done["id"])["sweep"]["cases"][0]["runId"]) / "output" / "strategy"
        directory.mkdir(parents=True)
        (directory / "work.json").write_text(json.dumps({"equity": [[1_700_000_000_000, 10_000], [1_700_086_400_000, 10_250]]}), encoding="utf-8")
        figure = G.preview({"equity": [[1_700_000_000_000, 10_000], [1_700_086_400_000, 10_250]]}, {"equity": [[1_700_000_000_000, 10_000], [1_700_086_400_000, 10_100]]}, {}, "r", 11)
        assert [item["meta"]["role"] for item in figure["data"]] == ["compare", "main"] and figure["data"][0]["name"] == "#11" and figure["data"][1]["name"] == "Picked cell"
        assert figure["data"][1]["hovertemplate"] == "<b>%{x}</b><br>Picked cell: %{customdata:+.1%}<extra></extra>" and figure["layout"]["meta"]["key"] == "strategy.sweep.preview"
        assert figure["data"][1]["customdata"] == [0.0, 0.025]
        walk = {"stitched": [[1_700_000_000_000, 1.0], [1_700_086_400_000, 1.02]], "windows": [{"n": 1, "traded": [1_700_000_000_000, 1_700_086_400_000], "values": {"fast": 48, "stop": 2.0}}]}
        picture = G.walk(walk, {"equity": [[1_700_000_000_000, 10_000], [1_700_086_400_000, 10_100]]}, "r", 11, 13)
        names = [item["name"] for item in picture["data"] if "name" in item]
        assert names[:2] == ["#11 fixed values", "#13"] and picture["data"][1]["hovertemplate"] == "<b>%{x}</b><br>#13: %{customdata:+.1%}<extra></extra>"
        assert picture["layout"]["annotations"][0]["text"] == "48 · 2"
        years = G.years([{"params": {"@period": {"since": "2023-01-01", "until": "2023-12-31"}}, "metrics": {"totalReturn": 0.12, "benchmarkReturn": 0.2}}],
                        [{"name": "@period", "values": [], "label": "Period"}])
        assert [item["hovertemplate"] for item in years["data"]] == ["<b>%{x}</b><br>Strategy: %{y:+.1f}%<extra></extra>", "<b>%{x}</b><br>Benchmark: %{y:+.1f}%<extra></extra>"]
        assert years["data"][0]["x"] == ["2023"] and years["data"][0]["y"] == [12.0]
    scenario(tmp_path, body)


def test_cases_write_no_figures(tmp_path):
    async def body(ctx, shell):
        Runner(shell)
        obj, record = await started(shell, AXES)
        done = await settled(shell, record["id"])
        figures = shell.sweeps.detail(done["id"])["figures"]
        assert set(figures) <= {"heat", "preview", "walk", "years"}  # one set of pictures for the whole sweep, drawn on the Host from the numbers
    scenario(tmp_path, body)


def test_period_axis_years(tmp_path):
    extra = S.period_extra({"since": "2023-01-01", "until": "2023-12-31"})
    assert extra["tradeFrom"] == 1_672_531_200_000 and extra["window"]["to"] == 1_704_067_199_999
    assert extra["window"]["warmUpMonths"] == S.WARM_UP_MONTHS == 12 and extra["tradeFrom"] - extra["window"]["from"] == 365 * 86_400_000

    async def body(ctx, shell):
        Runner(shell)
        years = [{"since": f"{year}-01-01", "until": f"{year}-12-31"} for year in (2021, 2022, 2023)]
        obj, record = await started(shell, axes_of(**{"@period": years}))
        done = await settled(shell, record["id"])
        figures = shell.sweeps.detail(done["id"])["figures"]
        assert "heat" not in figures and figures["years"]["layout"]["meta"]["key"] == "strategy.sweep.years" and figures["years"]["data"][0]["x"] == ["2021", "2022", "2023"]
    scenario(tmp_path, body)


def test_old_format_sweep_says_so(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell, STARTER)
        base = {"objectId": obj, "objectKind": "strategy", "name": "T", "workspaceId": "ws", "params": {}, "overrides": {}, "settings": shell.store.read(obj)["settings"], "message": None,
                "error": None, "code": {"digest": "d"}, "common": None, "data": {}, "environment": None, "recipe": None, "metrics": {}, "starred": False, "note": "",
                "exitCode": None, "endedAt": 5}
        shell.runs.records.put("qrun", {**base, "id": "old", "runKind": "sweep", "mode": "sweep", "state": "completed", "stage": "sweep", "createdAt": 9, "number": 4,
                                        "sweep": {"ranges": [{"name": "fast", "type": "int", "values": [5, 10]}], "metric": "totalReturn", "selected": 0, "cases": []}})
        view = shell.strategy.run("old")["run"]
        assert "Sweep #4 was made by an earlier TALYX; run it again to see it here" in view["notes"] and "sweep" not in view
        assert shell.sweeps.detail("old") == {"runId": "old", "objectId": obj, "state": "completed", "old": True, "number": 4}
        listed = shell.strategy.runs(obj)["items"]
        assert [item["id"] for item in listed] == ["old"]
    scenario(tmp_path, body)


# ---------------------------------------------------------------------------------------------------------------- stopped and going on
def test_sweep_resume_runs_only_unfinished(tmp_path):
    async def body(ctx, shell):
        runner = Runner(shell, stop_after=3)
        obj, record = await started(shell, AXES, queue=RunQueue(memory=64 * GB, cpus=1))
        stopped = await settled(shell, record["id"])
        assert stopped["state"] == "cancelled"
        states = [case["state"] for case in stopped["sweep"]["cases"]]
        assert states.count("completed") == 3 and states.count("cancelled") == 3
        done_ids = {case["index"]: case["runId"] for case in stopped["sweep"]["cases"] if case["state"] == "completed"}
        runner.stop_after, runner.ran = None, []
        again = await shell.sweeps.resume(record["id"])
        assert again["state"] == "preparing"
        finished = await settled(shell, record["id"])
        assert finished["state"] == "completed" and sorted(runner.ran) == [index for index, state in enumerate(states) if state != "completed"]
        assert {case["index"]: case["runId"] for case in finished["sweep"]["cases"] if case["index"] in done_ids and case["state"] == "completed"} == done_ids
        assert [case["state"] for case in finished["sweep"]["cases"]] == ["completed"] * 6
        with pytest.raises(ContractError, match="Only a stopped sweep") as error:
            await shell.sweeps.resume(record["id"])
        assert error.value.code == "conflict"
    scenario(tmp_path, body)


def test_sweep_interrupted_resume(tmp_path):
    async def body(ctx, shell):
        runner = Runner(shell)
        obj, record = await started(shell, AXES)
        await settled(shell, record["id"])
        # Host closed in the middle: two cells never ran, one was running.
        stored = shell.runs.read(record["id"])
        cases = [{**case, "state": "interrupted", "message": "TALYX restarted while this ran", "metrics": None} if case["index"] in (1, 2, 5) else case for case in stored["sweep"]["cases"]]
        cases = [{**case, "state": "failed", "message": "MemoryError", "metrics": None} if case["index"] == 3 else case for case in cases]  # a failed one is tried again
        shell.runs.update(record["id"], state="interrupted", sweep={**stored["sweep"], "cases": cases})
        runner.ran = []
        await shell.sweeps.resume(record["id"])
        done = await settled(shell, record["id"])
        assert sorted(runner.ran) == [1, 2, 3, 5] and done["state"] == "completed"
    scenario(tmp_path, body)


# ---------------------------------------------------------------------------------------------------------------- Use these values
def test_use_values_writes_params_not_selection(tmp_path):
    async def body(ctx, shell):
        Runner(shell)
        obj, record = await started(shell, axes_of(fast=[5, 10], **{"@fill": ["nextOpen", "nextClose"]}))
        done = await settled(shell, record["id"])
        before = shell.runs.read(done["id"])
        case = next(item for item in before["sweep"]["cases"] if item["params"] == {"fast": 10, "@fill": "nextClose"})
        version = shell.store.read(obj)["version"]
        await shell.sweeps.use_values(done["id"], case["index"], version)
        stored = shell.store.read(obj)["settings"]
        assert stored["params"]["fast"] == 10 and stored["run"]["fill"] == "nextClose"
        after = shell.runs.read(done["id"])
        assert after["sweep"] == before["sweep"] and after["sweep"]["selected"] is None
        with pytest.raises(ContractError, match="Read the current object"):
            await shell.sweeps.use_values(done["id"], case["index"], version)
    scenario(tmp_path, body)


def test_use_values_after_code_change(tmp_path):
    async def body(ctx, shell):
        Runner(shell)
        obj, record = await started(shell, AXES)
        done = await settled(shell, record["id"])
        path = shell.store.source_path(shell.store.read(obj))
        path.write_text(STARTER.replace('self.slow = q.param("slow", 30, min=3, max=500, label="Slow window")', 'self.slow = 30'), encoding="utf-8")
        with pytest.raises(ContractError, match="no longer declares slow") as error:
            await shell.sweeps.use_values(done["id"], 0)
        assert error.value.code == "conflict"
        assert shell.runs.read(done["id"])["code"]["digest"] == "d" * 64  # the sweep keeps the code it started with
    scenario(tmp_path, body)


# ---------------------------------------------------------------------------------------------------------------- held-out part
@pytest.mark.parametrize("kind, cuts", [("run", True), ("sweep", True), ("walk", True), ("case", True), ("heldout", False)])
def test_sweep_and_walk_stop_before_held_out(kind, cuts):
    from modules.quant.backend import strategy_settings
    data = {"since": "2024-01-01", "until": "2024-12-31", "heldOutFrom": "2024-10-01"}
    cut = strategy_settings.cut_data(data, kind)
    assert (cut["until"] == "2024-09-30") is cuts


def test_axis_label_drops_the_unit_in_brackets():
    assert S.label_of({"name": "lookback", "label": "Lookback (bars)"}) == "Lookback" and S.label_of({"name": "x"}) == "x"
    assert S.label_of({"name": "n", "label": "Names (top, by score)"}) == "Names (top, by score)"


def test_held_out_split_is_one_return_line_in_two_parts_with_rows():
    day = 86_400_000
    equity = [[i * day, 100.0 + i] for i in range(10)]
    # Each part as the research process writes it (strategy_report.held_out): every number the table reads, here a held-out part that lost and has no benchmark.
    inside = {"totalReturn": .05, "cagr": .2, "sharpe": 1.2, "sortino": 1.8, "maxDrawdown": -.1, "volatility": .25, "beta": .4, "days": 6, "trades": 7, "winRate": .6}
    outside = {"totalReturn": -.02, "cagr": None, "sharpe": -0.85, "sortino": -1.1, "maxDrawdown": -.03, "volatility": .3, "beta": None, "days": 4, "trades": 2, "winRate": .5}
    work = {"equity": equity, "heldOut": {"from": 6 * day, "inSample": inside, "heldOut": outside}}
    fig = G.held_split(work, 11, 14)
    names = [trace["name"] for trace in fig["data"] if trace.get("name")]
    assert names == ["#11, in-sample", "#14, held out"]
    first = fig["data"][0]
    assert first["y"][0] == 0 and first["hovertemplate"].count("<b>") == 1 and "customdata:+.1%" in first["hovertemplate"]
    # n/a only where the part has no such number (a CAGR over too short a span, a beta without a benchmark); a negative Sharpe takes the table's minus sign.
    assert G.held_rows(work["heldOut"]) == [["Return", "+5.0%", "−2.0%"], ["CAGR", "+20.0%", "n/a"], ["Sharpe", "1.20", "−0.85"], ["Sortino", "1.80", "−1.10"],
                                            ["Max drawdown", "−10%", "−3%"], ["Volatility", "25.0%", "30.0%"], ["Beta to BTC", "+0.40", "n/a"], ["Days", "6", "4"],
                                            ["Trades", "7", "2"], ["Win rate", "60.0%", "50.0%"]]


def test_the_research_process_writes_every_number_the_held_out_table_reads():
    """The held-out table's rows come from strategy_report.held_out: with a benchmark and trades on both sides, no row of either part is n/a."""
    import sys
    from pathlib import Path

    import numpy as np
    import pandas as pd
    sys.path.insert(0, str(Path(__file__).parents[2] / "modules/quant"))
    from types import SimpleNamespace

    from native import metrics as M
    from native.strategy_report import Report
    made = Report.__new__(Report)
    made.A, made._fills, made.ledger = 365, [], SimpleNamespace(entries=[], order_costs=lambda fills: [])
    days = pd.date_range("2024-01-01", periods=260, freq="D", tz="UTC")
    rng = np.random.default_rng(0)
    returns, bench = pd.Series(rng.normal(.001, .02, 260), index=days), pd.Series(rng.normal(.0005, .02, 260), index=days)
    equity = pd.concat([pd.Series([10_000.0], index=[days[0] - pd.Timedelta(days=1)]), 10_000 * (1 + returns).cumprod()])
    at = lambda index: int(days[index].timestamp() * 1000)  # noqa: E731
    trades = [{"entry": at(index - 3), "exit": at(index), "pnl": 10.0 if index % 3 else -10.0, "fees": 1.0} for index in range(5, 260, 10)]
    held = made.held_out(equity, pd.Series(0.0, index=days), trades, [], at(150), bench)
    rows = G.held_rows(held)
    assert [row[0] for row in rows] == ["Return", "CAGR", "Sharpe", "Sortino", "Max drawdown", "Volatility", "Beta to BTC", "Days", "Trades", "Win rate"]
    assert not [row for row in rows if "n/a" in row[1:]], rows
    assert (rows[7][1], rows[7][2]) == ("150", "110")
    # each part by the same definition as a whole run: the held-out end from the equity at the close before it starts
    after = equity.iloc[150:]
    assert held["heldOut"]["sharpe"] == pytest.approx(M.sharpe(after.pct_change().dropna().to_numpy(), 365), abs=1e-6)
    assert held["heldOut"]["totalReturn"] == pytest.approx(after.iloc[-1] / after.iloc[0] - 1, abs=1e-6)


def test_every_other_figure_reads_out_like_the_draft():
    """The readouts the draft gives each picture: a signed return to one place under the date, the run's own name once, years with Strategy and Benchmark."""
    day = 86_400_000
    equity = [[i * day, 100.0 + i] for i in range(10)]
    case = {"index": 0, "params": {"fast": 5}}
    preview = G.preview({"equity": equity}, {"equity": equity}, case, "r", 11)
    assert [trace["name"] for trace in preview["data"]] == ["#11", "Picked cell"]
    assert all(trace["hovertemplate"] == f"<b>%{{x}}</b><br>{trace['name']}: %{{customdata:+.1%}}<extra></extra>" for trace in preview["data"])
    stitched = [[i * day, 1 + i / 100] for i in range(10)]
    walk = G.walk({"stitched": stitched, "windows": [{"n": 1, "traded": [2 * day, 5 * day], "values": {"fast": 5, "slow": 30}}, {"n": 2, "traded": [5 * day, 8 * day], "values": {"fast": 10, "slow": 60}}]},
                  {"equity": equity}, "r", 11, 13)
    named = [trace for trace in walk["data"] if trace.get("name")]
    assert [trace["name"] for trace in named] == ["#11 fixed values", "#13"] and all(trace.get("showlegend") is False for trace in walk["data"] if "band" in str(trace.get("meta", {}).get("key", "")))
    assert [item["text"] for item in walk["layout"]["annotations"]] == ["5 · 30", "10 · 60"]
    cases = [{"params": {"@period": {"since": f"{year}-01-01", "until": f"{year}-12-31"}}, "metrics": {"totalReturn": 0.1 * (year - 2022), "benchmarkReturn": 0.05}} for year in (2023, 2024)]
    years = G.years(cases, [{"name": "@period", "values": [item["params"]["@period"] for item in cases]}])
    assert [trace["name"] for trace in years["data"]] == ["Strategy", "Benchmark"] and years["data"][0]["x"] == ["2023", "2024"]
    assert all("%{y:+.1f}%" in trace["hovertemplate"] for trace in years["data"])
    for figure in (preview, walk, years):
        for trace in figure["data"]:
            template = trace.get("hovertemplate") or ""
            assert template.count("<b>") == template.count("</b>") and "nan" not in template.lower()


def test_a_switch_and_a_choice_are_swept_over_their_values():
    """a switch is swept over off and on, a choice over options the code declares (in the code's order); anything else is refused."""
    definitions = [{"name": "shorts", "type": "bool", "label": "Shorts"},
                   {"name": "weighting", "type": "choice", "label": "Weighting", "options": [{"value": "equal", "label": "Equal"}, {"value": "rank", "label": "By rank"}, {"value": "inverse_vol", "label": "Inverse volatility"}]}]
    found, combinations = S.expand([{"name": "shorts", "values": [True, False]}, {"name": "weighting", "values": ["inverse_vol", "equal"]}], definitions)
    assert [axis["values"] for axis in found] == [[False, True], ["equal", "inverse_vol"]] and len(combinations) == 4
    with pytest.raises(ContractError, match="no option 'signal'"):
        S.expand([{"name": "weighting", "values": ["signal"]}], definitions)
    with pytest.raises(ContractError, match="on and off"):
        S.expand([{"name": "shorts", "values": [1, 0]}], definitions)


def test_resume_before_data_was_frozen_and_recover_live_cells(tmp_path):
    async def body(ctx, shell):
        runner = Runner(shell)
        entered = asyncio.Event()

        async def preparing(*args):
            entered.set()
            await asyncio.Event().wait()

        shell.sweeps.prepare = preparing
        _, run = await started(shell, AXES)
        identity = run["id"]
        await entered.wait()
        shell.runs.tasks[identity].cancel()
        assert (await settled(shell, identity))["state"] == "cancelled"
        del shell.sweeps.reprepare
        shell.sweeps.prepare = runner.prepare
        resumed = await shell.sweeps.resume(identity)
        assert {cell["state"] for cell in resumed["sweep"]["cases"]} == {"queued"}
        done = await settled(shell, identity)
        assert done["state"] == "completed" and len(runner.ran) == 6
        sweep = done["sweep"]
        cases = [dict(case) for case in sweep["cases"]]
        cases[0]["state"], cases[1]["state"] = "running", "queued"
        shell.runs.update(identity, state="running", sweep={**sweep, "cases": cases})
        from modules.quant.backend.work_records import WorkRecords
        reopened = WorkRecords(shell.runs.records.root)
        try:
            record = reopened.find("qrun", identity)
        finally:
            reopened.close()
        assert record["state"] == "interrupted"
        recovered = record["sweep"]["cases"]
        assert [c["state"] for c in recovered[:2]] == ["interrupted", "interrupted"]
        assert recovered[2:] == cases[2:]

    scenario(tmp_path, body)
