"""A factor run from start to its saved end, with the data and the engine standing in: what the run, its changes and the object list keep;
and the queue that orders heavy work."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules.quant.backend.object_routes import Services
from modules.quant.backend.run_queue import RunQueue
from modules.quant.backend.work_records import WorkRecords
from tests.unit.test_quant_object_runs import DATA, FakeContext
from tests.unit.test_quant_object_runs import INSTRUMENT as INSTRUMENT_FIXTURE

DAY = 86_400_000


def numbers(rank_ic):
    return {"rankIc": rank_ic, "t": 3.1, "longShortNet": .08, "turnover": .7, "names": 40}


def engine_writing(rank_ics):
    """An engine that writes the Work result the research process would, one rank IC per run."""
    async def run_open(job, on_future=None, publish=None):
        output = Path(job["output"])
        work = {"kind": "run", "numbers": numbers(rank_ics.pop(0)), "checks": [{"id": "future_data", "state": "ok", "value": "none"}],
                "decay": [.01] * 20, "figures": {}, "dataFrom": 0, "dataTo": DAY, "notes": [], "seconds": .2}
        (output / "work.json").write_text(json.dumps(work), encoding="utf-8")
        return {"ok": True, "seconds": .2}
    return run_open


async def prepared(settings, report=None, facts=True):
    return {"key": "k", "barsPath": "bars.parquet", "extrasPath": "extras.json", "info": {"annualization": 252}, "dataFrom": 0, "dataTo": DAY,
            "names": 1, "requested": 1, "notes": [], "frozenAt": 0}


async def settled(factors, identity):
    async with asyncio.timeout(10):
        while identity in factors.tasks:
            await asyncio.sleep(.01)
    return factors.read(identity)


def services(tmp_path, monkeypatch, rank_ics):
    ctx = FakeContext(tmp_path, None)
    shell = Services(ctx, WorkRecords(tmp_path / "records"))
    factors = shell.factors
    monkeypatch.setattr(factors.data, "prepare", prepared)
    monkeypatch.setattr(factors.data, "key", lambda data, facts=True: "k")
    monkeypatch.setattr(factors.data, "library_values", lambda *args, **kwargs: asyncio.sleep(0, None))
    monkeypatch.setattr(factors.engine, "run_open", engine_writing(rank_ics))
    monkeypatch.setattr(factors.data, "calendar_items", lambda data: asyncio.sleep(0, None))  # an index's members are Market's to say; the stand-in has none
    return ctx, shell, factors


def test_a_finished_run_is_saved_with_its_numbers_and_the_next_run_says_what_changed(tmp_path, monkeypatch):
    async def scenario():
        ctx, shell, factors = services(tmp_path, monkeypatch, [.012, .015])
        created = await shell.store.create({"kind": "factor", "name": "Reversal"})
        await shell.store.update_settings(created["id"], {"data": DATA})
        first = await factors.start(created["id"], {"formula": "-($close / Ref($close, 5) - 1)"})
        done = await settled(factors, first["id"])
        assert (done["state"], done["numbers"]["rankIc"], done["metrics"]["rankIc"]) == ("completed", .012, .012)
        assert done["seconds"] is not None and done["endedAt"] >= done["createdAt"]
        assert done["settings"]["run"]["neutralize"] == "none" and "riskFree" not in done["settings"]["run"]  # crypto: nothing to take out, no rate
        second = await factors.start(created["id"], {"formula": "-($close / Ref($close, 5) - 1)", "settings": {"holding": 10}})
        # the answer is the run itself, made at once in the shared flow's first state; its check (the data's end, what changed) is its first step
        assert (second["state"], second["stage"]) == ("preparing", "check")
        second = await settled(factors, second["id"])
        assert [(item["what"], item["from"], item["to"]) for item in second["changes"]] == [("holding", 5, 10)]
        listed = {item["id"]: item for item in shell.store.list({"kind": "factor"})["items"]}
        assert listed[created["id"]]["lastRun"]["numbers"]["rankIc"] == .015 and listed[created["id"]]["lastRun"]["futureData"] is False
    asyncio.run(scenario())


def test_fetching_facts_marks_the_prices_done_once_not_with_every_facts_update(tmp_path, monkeypatch):
    """Every save of a run is an event each open page reads: the facts' progress (twice a second at most) does not save the prices' step again each time."""
    import time as real_time

    from modules.quant.backend import factor_runs
    clock = [1_000_000.0]
    monkeypatch.setattr(factor_runs, "time", SimpleNamespace(time=lambda: clock[0], perf_counter=real_time.perf_counter, monotonic=real_time.monotonic))

    async def scenario():
        ctx, shell, factors = services(tmp_path, monkeypatch, [.012])
        rows = [{"result": "fetched"}] * 4

        async def fetching(settings, report=None, facts=True):
            report(rows, "prices")
            for done in range(1, 11):
                clock[0] += .6
                report(rows, "facts", done)
            report(rows, "ready")
            return await prepared(settings)
        monkeypatch.setattr(factors.data, "prepare", fetching)
        steps = []
        step = factors.step
        monkeypatch.setattr(factors, "step", lambda identity, name, state, **values: (steps.append((name, state)), step(identity, name, state, **values))[1])
        created = await shell.store.create({"kind": "factor", "name": "Reversal"})
        await shell.store.update_settings(created["id"], {"data": DATA})
        run = await factors.start(created["id"], {"formula": "-($close / Ref($close, 5) - 1)"})
        done = await settled(factors, run["id"])
        assert done["state"] == "completed"
        assert steps.count(("facts", "running")) == 10, "each facts update half a second or more apart shows"
        assert steps.count(("data", "done")) == 3, "the prices are done when the facts start, when the data is ready and when it is kept; not with each of the 10 facts updates"
    asyncio.run(scenario())


def test_work_a_person_waits_for_goes_before_background_work_within_the_memory_budget():
    async def scenario():
        queue = RunQueue(memory=16 * 1024 ** 3, cpus=1)
        order, positions = [], []
        hold = asyncio.Event()

        async def work(owner, interactive, memory, wait=None):
            async with queue.slot(owner=owner, label=owner, interactive=interactive, memory=memory, on_wait=lambda place: positions.append((owner, place))):
                order.append(owner)
                if wait is not None:
                    await wait.wait()

        first = asyncio.create_task(work("sweep-1", False, 1024 ** 3, hold))
        await asyncio.sleep(0)
        later = [asyncio.create_task(work("sweep-2", False, 1024 ** 3)), asyncio.create_task(work("run", True, 1024 ** 3))]
        await asyncio.sleep(.01)
        view = queue.view()
        # The interactive run went past the one CPU slot the sweep holds; the second sweep waits for it.
        assert order == ["sweep-1", "run"] and [item["owner"] for item in view["running"]] == ["sweep-1"] and [item["owner"] for item in view["waiting"]] == ["sweep-2"]
        assert view["budget"] == 8 * 1024 ** 3
        hold.set()
        await asyncio.gather(first, *later)
        assert order == ["sweep-1", "run", "sweep-2"] and ("sweep-2", 1) in positions
    asyncio.run(scenario())


@pytest.mark.parametrize("total, budget", [(8 * 1024 ** 3, 4 * 1024 ** 3), (32 * 1024 ** 3, 16 * 1024 ** 3), (6 * 1024 ** 3, 2 * 1024 ** 3)])
def test_the_budget_is_half_the_memory_leaving_at_least_four_gigabytes(total, budget):
    assert RunQueue(memory=total).budget == budget


def test_the_factor_actions_answer_through_the_public_contract(tmp_path, monkeypatch):
    async def scenario():
        from talyx_sdk import ContractError

        from modules.quant.backend.object_routes import handle_actions
        ctx, shell, factors = services(tmp_path, monkeypatch, [.012, .02])
        handlers = {}
        ctx.handlers = handlers
        handle_actions(ctx, shell)
        created = await shell.store.create({"kind": "factor", "name": "Reversal"})
        await shell.store.update_settings(created["id"], {"data": {**DATA,"heldOutFrom":"2024-05-01"}})
        run = await settled(factors, (await factors.start(created["id"], {"formula": "-($close / Ref($close, 5) - 1)"}))["id"])

        fields = (await handlers["read_object"]({"objectId": created["id"], "part":"options"}, None))["options"]
        assert "$close" in {item["name"] for item in fields["fields"]} and any(item["name"] == "Ref" for item in fields["operators"])
        assert (await handlers["validate_factor_formula"]({"objectId": created["id"], "formula": "Ref($close, 5)"}, None))["error"] is None
        assert (await handlers["validate_factor_formula"]({"formula":"Stdev($close, 5)"},None))["error"]["fix"] == "Std"
        read = await handlers["read_run"]({"runId":run["id"]}, None)
        assert read["numbers"]["rankIc"] == .012 and "figures" not in read
        with pytest.raises(ContractError, match="not kept"):
            await handlers["read_run_table"]({"runId":run["id"],"table":"crossSection"},None)
        held = await handlers["validate_held_out"]({"runId":run["id"]},None)
        assert (await settled(factors,held["runId"]))["state"] == "completed"
        assert (await handlers["validate_held_out"]({"runId":run["id"]},None))["runId"] == held["runId"]
        made = await handlers["create_object"]({"fromRun":run["id"],"kind":"model"},None)
        assert made["kind"] == "model"
        code = await handlers["write_as_code"]({"objectId":created["id"],"formula":"-($close / Ref($close, 5) - 1)"},None)
        assert shell.store.read(code["objectId"])["settings"]["mode"] == "code"
    asyncio.run(scenario())

def test_a_strategy_or_model_started_from_a_run_reads_that_run_with_its_dates_holding_and_costs(tmp_path, monkeypatch):
    # the strategy got the published library name or nothing, the data ran to today and into the factor's held-out
    # stretch, and the holding and costs were left behind; the model's input went where the model does not read it.
    async def scenario():
        import numpy as np
        import pandas as pd
        ctx, shell, factors = services(tmp_path, monkeypatch, [.012])
        created = await shell.store.create({"kind": "factor", "name": "Reversal"})
        await shell.store.update_settings(created["id"], {"data": {**DATA,"heldOutFrom":"2024-05-01"}, "run": {"holding": 20, "costBps": 7}})
        run = await settled(factors, (await factors.start(created["id"], {"formula": "-($close / Ref($close, 5) - 1)"}))["id"])
        record = factors.read(run["id"])
        held = record["settings"]["data"]["heldOutFrom"]
        factors.records.put("qrun", {**record, "settings": {**record["settings"], "data": {**record["settings"]["data"], "untilLatest": True}}})
        cross = factors.directory(run["id"]) / "output" / "cross"
        cross.mkdir(parents=True)
        (cross / "meta.json").write_text(json.dumps({"labels": [0, DAY, 2 * DAY], "columns": ["BTC", "ETH"]}), encoding="utf-8")
        np.save(cross / "raw.npy", np.array([[np.nan, 1.0], [.5, 2.0], [.25, 3.0]], dtype=np.float32))
        np.save(factors.directory(run["id"]) / "output" / "values.npy", np.array([[np.nan, 1.0], [.5, 2.0], [.25, 3.0]]))
        np.save(cross / "missing.npy", np.array([[5, 0], [0, 0], [0, 1]], dtype=np.int8))  # ETH left the index on the last day
        info = factors.data.directory("k") / "info.json"
        info.parent.mkdir(parents=True, exist_ok=True)
        info.write_text(json.dumps({"instruments": [{"column": "BTC", "instrumentId": "id-btc"}, {"column": "ETH", "instrumentId": "id-eth"}]}), encoding="utf-8")

        strategy = await factors.create_from(run["id"], "strategy")
        settings = strategy["settings"]
        assert settings["data"]["since"] == DATA["since"] and settings["data"]["until"] == DATA["until"] and "untilLatest" not in settings["data"]
        assert settings["data"]["heldOutFrom"] == held  # the factor's held-out stretch stays held out
        assert settings["params"]["rebalance_every"] == 20
        costs = settings["run"]["costs"]
        assert (costs["commissionBps"], costs["feePct"], costs["halfSpreadBps"], costs["impact"]) == (7, .07, 0, 0)
        signal = settings["signalRun"]
        assert (signal["runId"], signal["number"], signal["kind"], signal["lastValueAt"]) == (run["id"], record["number"], "factor", 2 * DAY)
        values = pd.read_parquet(signal["path"])
        # this run's own values, only for the names that were members that day
        assert sorted(zip(values["datetime"], values["instrument"], values["value"], strict=True)) == \
            [(0, "id-eth", 1.0), (DAY, "id-btc", .5), (DAY, "id-eth", 2.0), (2 * DAY, "id-btc", .25)]
        await shell.store.update_settings(strategy["id"], {"data": {**DATA, "since": "2024-02-01"}})  # the copy can be changed

        model = await factors.create_from(run["id"], "model")
        assert model["settings"]["params"]["inputs"] == [{"source": "run", "ref": run["id"], "name": f"Reversal #{record['number']}"}]
        assert model["settings"]["run"]["cost"] == 7 and model["settings"]["data"]["heldOutFrom"] == held and "inputs" not in model["settings"]
    asyncio.run(scenario())


STOCK = {**INSTRUMENT_FIXTURE, "instrumentId": "vi1_" + "e" * 64, "assetClass": "equity", "marketType": "spot", "symbol": "AAPL", "displaySymbol": "AAPL"}
FUND = {**INSTRUMENT_FIXTURE, "instrumentId": "vi1_" + "f" * 64, "assetClass": "fund", "marketType": "spot", "symbol": "SPY", "displaySymbol": "SPY"}


@pytest.mark.parametrize("universe, members, industries", [
    ("crypto symbols", ["crypto"], False), ("stock symbols", ["stock"], True), ("a mix", ["crypto", "stock"], True),
    ("crypto research list", ["crypto"], False), ("stock research list", ["stock"], True),
    ("ETFs only: Market has no industry for them, so nothing is neutralized by default", ["fund"], False)])
def test_the_default_neutralize_the_settings_show_is_the_one_the_run_records(tmp_path, monkeypatch, universe, members, industries):
    async def scenario():
        ctx, shell, factors = services(tmp_path, monkeypatch, [.01])
        chosen = [{"instrument": {"crypto": INSTRUMENT_FIXTURE, "stock": STOCK, "fund": FUND}[kind]} for kind in members]
        base = {key: DATA[key] for key in ("timeframe", "since", "until")}
        if universe.endswith("research list"):
            monkeypatch.setattr(factors.data.lists, "members", lambda name: chosen)
            data = {**base, "list": "watch"}
        else:
            data = {**base, "symbols": chosen}
        created = await shell.store.create({"kind": "factor", "name": "Reversal"})
        obj = shell.store.read(created["id"])
        shell.store.save(obj, settings={**obj["settings"], "data": data})
        fields = await factors.fields(created["id"])
        run = await settled(factors, (await factors.start(created["id"], {"formula": "-($close / Ref($close, 5) - 1)"}))["id"])
        # Neutralize defaults by asset class: crypto has no industry, so nothing is taken out and the industry settings do not appear
        assert fields["industries"] is industries
        assert fields["neutralizeDefault"] == run["settings"]["run"]["neutralize"] == ("industry+size" if industries else "none")
        # the object keeps it left out, so the next names decide again and the settings still show what the run will take
        assert "neutralize" not in shell.store.read(created["id"])["settings"]["run"]
        assert (await factors.fields(created["id"]))["neutralizeDefault"] == fields["neutralizeDefault"]
    asyncio.run(scenario())


def test_names_the_code_picks_default_to_industry_and_size_before_they_are_known(tmp_path, monkeypatch):
    async def scenario():
        ctx, shell, factors = services(tmp_path, monkeypatch, [])
        created = await shell.store.create({"kind": "factor", "name": "Reversal"})
        obj = shell.store.read(created["id"])
        shell.store.save(obj, settings={**obj["settings"], "data": {key: DATA[key] for key in ("timeframe", "since", "until")} | {"universe": "code"}})
        fields = await factors.fields(created["id"])
        assert fields["industries"] is True and fields["neutralizeDefault"] == "industry+size"
    asyncio.run(scenario())

# ---------------------------------------------------------------------------------------------------------------- F: the foundation
from tests.unit.test_quant_object_runs import INDEX as SP500


def with_materials(ctx, instrument):
    async def resolve(args):
        assert args["text"] == "^GSPC"
        return {"instrument": instrument}
    ctx.require("market.instruments").resolve = resolve


def test_new_factor_gets_sp500_latest_by_default(tmp_path, monkeypatch):
    async def scenario():
        ctx, shell, factors = services(tmp_path, monkeypatch, [])
        with_materials(ctx, SP500)
        created = await shell.store.create({"kind": "factor", "name": "Reversal"})
        data = shell.store.read(created["id"])["settings"]["data"]
        assert data["index"] == {"instrument": SP500}
        assert (data["timeframe"], data["since"], data["until"]) == ("1d", "2016-01-01", "latest")
        assert "heldOutFrom" not in data
        # a preset starts the same way
        preset = await shell.store.create({"kind": "factor", "preset": "factor:Reversal"})
        assert shell.store.read(preset["id"])["settings"]["data"]["index"]["instrument"] == SP500
        # a model or a strategy does not get the factor's default
        model = await shell.store.create({"kind": "model", "name": "M"})
        assert not shell.store.read(model["id"])["settings"]["data"].get("index")
    asyncio.run(scenario())


def test_new_factor_propagates_market_index_resolution_failure(tmp_path, monkeypatch):
    from talyx_sdk import ContractError
    async def scenario():
        ctx, shell, factors = services(tmp_path, monkeypatch, [])
        async def missing(args):
            raise ContractError("not_found", "Market has no S&P 500 identity")
        ctx.require("market.instruments").resolve = missing
        with pytest.raises(ContractError, match=r"Market cannot identify \^GSPC"):
            await shell.store.create({"kind": "factor", "name": "Reversal"})
    asyncio.run(scenario())


def sweep_engine(cells):
    async def run_open(job, on_future=None, publish=None):
        (Path(job["output"]) / "work.json").write_text(json.dumps({"kind": "sweep", "axes": job["sweep"], "cells": cells, "notes": [], "dataFrom": 0, "dataTo": DAY, "seconds": .2}), encoding="utf-8")
        return {"ok": True, "seconds": .2}
    return run_open


def test_sweep_opens_on_current_settings_not_the_best(tmp_path, monkeypatch):
    async def scenario():
        ctx, shell, factors = services(tmp_path, monkeypatch, [])
        cells = [{"combo": {"holding": value}, "settings": {"holding": value, "delay": 1}, "numbers": {**numbers(.01), "longShortNet": net}, "checks": [], "figures": {}, "review": 0}
                 for value, net in ((1, .02), (5, .05), (10, .20))]  # 10 days is the best; 5 days are the current settings
        monkeypatch.setattr(factors.engine, "run_open", sweep_engine(cells))
        created = await shell.store.create({"kind": "factor", "name": "Reversal"})
        await shell.store.update_settings(created["id"], {"data": DATA})
        started = await factors.start(created["id"], {"formula": "-($close / Ref($close, 5) - 1)", "kind": "sweep", "sweep": [{"name": "holding", "values": [1, 5, 10]}]})
        done = await settled(factors, started["id"])
        assert done["sweep"]["selected"] == 1 and done["sweep"]["best"] == 2
        assert done["numbers"]["longShortNet"] == .05  # the run's own numbers are the current settings' cell, not the best's
    asyncio.run(scenario())


def test_run_with_holding_10_reports_10(tmp_path, monkeypatch):
    async def scenario():
        ctx, shell, factors = services(tmp_path, monkeypatch, [.012, .015])
        seen = []
        original = factors.engine.run_open

        async def watching(job, on_future=None, publish=None):
            seen.append(job["settings"]["holding"])
            return await original(job, on_future=on_future, publish=publish)
        monkeypatch.setattr(factors.engine, "run_open", watching)
        created = await shell.store.create({"kind": "factor", "name": "Reversal"})
        await shell.store.update_settings(created["id"], {"data": DATA})
        await settled(factors, (await factors.start(created["id"], {"formula": "-($close / Ref($close, 5) - 1)"}))["id"])
        second = await factors.start(created["id"], {"formula": "-($close / Ref($close, 5) - 1)", "settings": {"holding": 10}})
        done = await settled(factors, second["id"])
        assert seen == [5, 10] and done["settings"]["run"]["holding"] == 10
        assert shell.store.read(created["id"])["settings"]["run"]["holding"] == 10  # the object keeps what ran last
    asyncio.run(scenario())


def test_run_settings_wrapped_in_run_are_refused_and_old_stored_keys_do_not_stop_a_run(tmp_path, monkeypatch):
    from talyx_sdk import ContractError

    async def scenario():
        ctx, shell, factors = services(tmp_path, monkeypatch, [.012])
        created = await shell.store.create({"kind": "factor", "name": "Reversal"})
        await shell.store.update_settings(created["id"], {"data": DATA})
        with pytest.raises(ContractError, match="Unknown run setting: run") as error:
            await factors.start(created["id"], {"formula": "-($close / Ref($close, 5) - 1)", "settings": {"run": {"holding": 10}}})
        assert error.value.code == "invalid_input"
        # a key an older version of the object stored in its run settings is left out, the others still apply
        obj = shell.store.read(created["id"])
        shell.store.save(obj, settings={**obj["settings"], "run": {"holding": 3, "retiredSetting": 1, "run": {"holding": 9}}})
        done = await settled(factors, (await factors.start(created["id"], {"formula": "-($close / Ref($close, 5) - 1)"}))["id"])
        assert done["state"] == "completed" and done["settings"]["run"]["holding"] == 3 and "retiredSetting" not in done["settings"]["run"]
    asyncio.run(scenario())


def test_default_values_day_has_whole_next_returns():
    from modules.quant.backend.factor_runs import FactorRuns
    labels = [index * DAY for index in range(40)]
    names_a_day = [30] * 40
    names_a_day[0:3] = [0, 0, 0]
    record = {"settings": {"run": {"holding": 5, "delay": 1}, "data": {"heldOutFrom": None}}}
    chosen = FactorRuns._default_day(record, {"labels": labels, "names": names_a_day, "holding": 5})
    assert chosen == 40 - 1 - 6  # a full holding period and the delay of later returns remain after it
    held = {"settings": {"run": {"holding": 5, "delay": 1}, "data": {"heldOutFrom": "1970-01-21"}}}  # 20 days in
    chosen = FactorRuns._default_day(held, {"labels": labels, "names": names_a_day, "holding": 5})
    assert labels[chosen] < 20 * DAY and chosen == 19
    empty_tail = [30] * 20 + [0] * 20  # nothing valued after day 19: the last valued day with a whole future wins over the last valued day
    assert FactorRuns._default_day(record, {"labels": labels, "names": empty_tail, "holding": 5}) == 19
    sparse = [30 if index % 7 else 0 for index in range(40)]
    day = FactorRuns._default_day(record, {"labels": labels, "names": sparse, "holding": 5})
    assert sparse[day] and day <= 33 and not any(sparse[later] for later in range(day + 1, 34))


def test_a_run_on_latest_records_the_resolved_day_and_the_next_day_names_the_change(tmp_path, monkeypatch):
    from modules.quant.backend import factor_runs as FR
    from modules.quant.backend import object_data

    async def scenario():
        ctx, shell, factors = services(tmp_path, monkeypatch, [.012, .012])
        day = ["2026-11-05"]

        async def resolve(ctx, data, now_ms=None, **kwargs):
            return {**data, "until": day[0], "untilLatest": True} if data.get("until") == "latest" else data
        monkeypatch.setattr(FR, "resolve_until", resolve)
        monkeypatch.setattr(object_data, "resolve_until", resolve)
        created = await shell.store.create({"kind": "factor", "name": "Reversal"})
        await shell.store.update_settings(created["id"], {"data": {**DATA, "until": "latest"}})
        first = await settled(factors, (await factors.start(created["id"], {"formula": "-($close / Ref($close, 5) - 1)"}))["id"])
        assert first["settings"]["data"]["until"] == "2026-11-05" and first["settings"]["data"]["untilLatest"] is True
        assert shell.store.read(created["id"])["settings"]["data"]["until"] == "latest"  # the object keeps asking for the latest
        day[0] = "2026-11-06"
        second = await settled(factors, (await factors.start(created["id"], {"formula": "-($close / Ref($close, 5) - 1)"}))["id"])
        assert [(item["what"], item["from"], item["to"]) for item in second["changes"]] == [("data until", "2026-11-05", "2026-11-06")]
    asyncio.run(scenario())


def test_changes_name_a_different_last_day():
    from modules.quant.backend.factor_runs import changes_between

    def record(until, latest):
        return {"formula": "x", "mode": "formula", "settings": {"data": {"timeframe": "1d", "since": "2016-01-01", "until": until, **({"untilLatest": True} if latest else {})},
                                                                 "run": {}, "params": {}}}
    both = changes_between(record("2026-11-06", True), record("2026-11-05", True))
    assert [(item["what"], item["from"], item["to"]) for item in both] == [("data until", "2026-11-05", "2026-11-06")]
    assert changes_between(record("2026-11-06", True), record("2026-11-06", True)) == []


def test_the_data_step_says_how_the_data_came(tmp_path, monkeypatch):
    async def scenario():
        ctx, shell, factors = services(tmp_path, monkeypatch, [.012, .012, .012])
        created = await shell.store.create({"kind": "factor", "name": "Reversal"})
        await shell.store.update_settings(created["id"], {"data": DATA})
        details = []
        for how, extra in (("unchanged", {}), ("full", {})):
            async def ready(settings, report=None, how=how, extra=extra, facts=True):
                return {**(await prepared(settings)), "how": how, **extra}
            monkeypatch.setattr(factors.data, "prepare", ready)
            run = await settled(factors, (await factors.start(created["id"], {"formula": "-($close / Ref($close, 5) - 1)"}))["id"])
            details.append(next(step for step in run["progress"]["steps"] if step["name"] == "data").get("detail"))
        assert details == ["from the same Market revisions and research facts", None]
    asyncio.run(scenario())


def test_held_out_uses_count_across_factors(tmp_path, monkeypatch):
    async def scenario():
        ctx, shell, factors = services(tmp_path, monkeypatch, [.01] * 12)
        text = "-($close / Ref($close, 5) - 1)"

        async def make(formula, name, data=DATA):
            created = await shell.store.create({"kind": "factor", "name": name})
            await shell.store.update_settings(created["id"], {"data": {"heldOutFrom":"2024-05-01",**data}})
            return created["id"], await settled(factors, (await factors.start(created["id"], {"formula": formula}))["id"])
        a, run_a = await make(text, "A")
        assert run_a["heldOutUses"] == 0
        await settled(factors, (await factors.start(a, {"kind": "heldout", "of": run_a["id"]}))["id"])
        b, run_b = await make("-($close /  Ref($close,5) - 1)", "B")  # the same formula, spaces apart, in another factor
        assert run_b["heldOutUses"] == 1
        held = await factors.start(b, {"kind": "heldout", "of": run_b["id"]})
        assert (await settled(factors, held["id"])).get("heldOutUses") is None  # only a run that can be validated carries the count
        c, run_c = await make("-($close / Ref($close, 6) - 1)", "C")  # another formula
        assert run_c["heldOutUses"] == 0
        other = {**DATA, "since": "2024-01-05"}
        d, run_d = await make(text, "D", other)  # the same formula on other data
        assert run_d["heldOutUses"] == 0
        e, run_e = await make(text, "E", {**DATA, "heldOutFrom": "2024-04-01"})  # a held-out period starting elsewhere
        assert run_e["heldOutUses"] == 0
    asyncio.run(scenario())


def test_a_published_run_lists_its_library_version_as_a_number(tmp_path, monkeypatch):
    """Found in the real application: the record keeps the version's id and item, the page reads a number (an object there left the whole page blank)."""
    async def scenario():
        ctx, shell, factors = services(tmp_path, monkeypatch, [.012])
        created = await shell.store.create({"kind": "factor", "name": "Reversal"})
        await shell.store.update_settings(created["id"], {"data": DATA})
        started = await factors.start(created["id"], {"formula": "-($close / Ref($close, 5) - 1)"})
        await settled(factors, started["id"])
        assert factors.list(created["id"])["items"][0]["version"] is None
        factors.update(started["id"], version={"versionId": "ver-1", "version": 2, "itemId": "item-1", "name": "Reversal"})
        assert factors.list(created["id"])["items"][0]["version"] == 2
    asyncio.run(scenario())


def test_every_member_of_a_day_is_one_row_and_the_count_plus_the_missing_rows_is_the_members(tmp_path, monkeypatch):
    """The rule behind a day's member count: a member is a row once whatever lacks (no instrument, no prices at all, prices starting later, a halted day,
    a value the preprocessing dropped), two classes of one company are two members, and only a non-member is no row."""
    import numpy as np
    day = [86_400_000, 2 * 86_400_000]
    columns = ["A", "B", "C", "D", "P"]  # A valued; B a member without a price that day; C not a member; D valued; P's prices start on the second day
    ctx, shell, factors = services(tmp_path, monkeypatch, [])
    folder = factors.directory("run-x") / "output" / "cross"
    folder.mkdir(parents=True)
    nan = np.nan
    arrays = {"after": [[.5, nan, nan, -.5, nan], [.4, nan, nan, -.4, .1]], "raw": [[1., nan, nan, 2., nan], [1., nan, nan, 2., 3.]],
              "group": [[2, 0, 0, 1, 0], [2, 0, 0, 1, 1]], "missing": [[0, 2, 1, 0, 1], [0, 2, 1, 0, 0]], "next": [[.01, nan, nan, .02, nan], [.01, nan, nan, .02, .03]]}
    for name, values in arrays.items():
        np.save(folder / f"{name}.npy", np.array(values, dtype=float if name != "missing" else int))
    (folder / "meta.json").write_text(json.dumps({"labels": day, "columns": columns, "inputs": [], "flags": [], "holding": 5, "groups": 2}), encoding="utf-8")
    data = factors.data.directory("k")
    data.mkdir(parents=True)

    def gone(asset, symbol, kind, since, until):
        return {"assetId": asset, "instrumentId": None if kind == "unmapped" else "i:" + symbol, "symbol": symbol, "since": since, "until": until, "kind": kind}
    exclusions = [gone("unmapped:x", "X", "unmapped", day[0], day[0]), gone("unmapped:x", "X", "unmapped", day[1], day[1]),  # one member over two stretches
                  gone("a:goog", "GOOG", "no_prices", day[0], day[1]), gone("a:googl", "GOOGL", "no_prices", day[0], day[1]),  # two classes of one company
                  gone("a:p", "P", "no_prices", 0, day[0])]  # P's prices start on the second day
    (data / "info.json").write_text(json.dumps({"index": {"exclusions": exclusions}}), encoding="utf-8")
    record = {"number": 1, "data": {"key": "k"}}
    for at, valued, tickers in ((day[0], 2, {"A", "B", "D", "P", "X", "GOOG", "GOOGL"}), (day[1], 3, {"A", "B", "D", "P", "X", "GOOG", "GOOGL"})):
        cross = factors.cross_section("run-x", at, record)
        rows = [row["ticker"] for row in cross["rows"]]
        assert sorted(rows) == sorted(set(rows)) and set(rows) == tickers and cross["members"] == len(tickers), (at, rows)
        assert cross["count"] == valued and cross["count"] + sum(1 for row in cross["rows"] if row.get("missing")) == cross["members"]
        assert "C" not in rows  # the only non-member


def test_a_run_reads_industry_and_shares_only_when_its_settings_or_formula_use_them():
    from modules.quant.backend.factor_runs import reads_facts
    from modules.quant.native.factor_work import DEFAULT_SETTINGS

    def record(formula="Rank($close)", mode="formula", **run):
        return {"mode": mode, "formula": formula, "settings": {"run": {**DEFAULT_SETTINGS, "neutralize": "none", **run}}}
    assert not reads_facts(record())
    assert reads_facts(record(neutralize="industry+size")) and reads_facts(record(groupAdjust=True))
    assert reads_facts(record(grouping={**DEFAULT_SETTINGS["grouping"], "withinIndustry": True}))
    assert reads_facts(record(formula="Rank($size)")) and reads_facts(record(mode="code", formula=None))
