"""SDK receipts for Quant objects, runs and library versions."""
import asyncio
import json

from tests.unit.quant_model_fakes import model_world, settled, wait_for
from tests.unit.quant_public import public_actions
from tests.unit.test_quant_object_runs import DATA


def test_restored_activity_keeps_its_historical_call_even_inside_another_task():
    from types import SimpleNamespace

    from talyx_sdk import process

    messages = []
    module = SimpleNamespace(module=SimpleNamespace(id="quant"), activities={},
                             next_id=lambda: str(len(messages)), send=lambda head, body: messages.append(json.loads(body)))
    ctx = process.Context(module)
    call = process._CALL_ID.set("current-reading")
    try:
        current = ctx.activity("research", "current")
        owner = process._TASK_ACTIVITY.set(current)
        try:
            assert ctx.activity("research", "nested") is current
            assert ctx.activity("research", "old run", call_id="original-training").call_id == "original-training"
            assert ctx.activity("research", "legacy run", call_id=None).call_id is None
        finally:
            process._TASK_ACTIVITY.reset(owner)
    finally:
        process._CALL_ID.reset(call)
    assert [message["callId"] for message in messages] == ["current-reading", "original-training", None]


def entry_task(name):
    from modules.quant.backend.entry import module
    return module.actions[name].task


def test_model_daily_table_reads_the_model_output_and_only_scores_labelled_names(tmp_path, monkeypatch):
    import numpy as np

    async def scenario():
        world = model_world(tmp_path, monkeypatch, windows=1)
        act = public_actions(world.ctx, world.shell, monkeypatch)
        try:
            model = await world.make(params={"algorithm": "lstm"})
            for kind in ("train", "combine"):
                obj = world.shell.store.read(model)
                run = await world.models.create(obj, kind, obj["settings"]["params"], state="completed")
                output = world.models.directory(run["id"]) / "output"
                output.mkdir(exist_ok=True, parents=True)
                (output / "pred.json").write_text(json.dumps({"labels": [0, 86400000, 172800000],
                    "columns": ["B", "A", "D", "C"]}), encoding="utf-8")
                np.save(output / "pred.npy", [[2, 3, 1, 4], [2, 3, 1, 4], [4, 1, 3, 2]])
                np.save(output / "label.npy", [[2, 3, 1, 4], [3, 2, 4, np.nan], [2, 4, np.nan, np.nan]])
                page = await act("read_run_table", {"runId": run["id"], "table": "daily", "start": "1970-01-02"})
                assert page["rows"] == [["1970-01-02T00:00:00Z", 4, -1.0], ["1970-01-03T00:00:00Z", 4, None]]
        finally:
            await world.models.close()
            world.shell.store.records.close()
    asyncio.run(scenario())


def test_model_run_receipts_and_research_actions(tmp_path, monkeypatch):
    async def scenario():
        world = model_world(tmp_path, monkeypatch, windows=2)
        act = public_actions(world.ctx, world.shell, monkeypatch)
        model = await world.make()
        obj = {"objectId": model}
        try:
            settings = world.shell.store.read(model)["settings"]
            await world.shell.store.update_settings(model, {"data":{**settings["data"],"heldOutFrom":"2024-01-01"}})
            await act("read_object",{**obj, "part":"options"})
            for name in ("check_model","list_runs","read_object"):
                await act(name,obj,origin="ai")
            for name in ("list_objects","read_quant_attention","read_queue","list_library","list_research_lists"):
                await act(name,origin="ai")
            await act("set_model_concurrency", {"count": 1})
            async def inspect(op, job):
                assert op == "model_inspect"
                return {"ok":True,"name":"Price","input":"Price","features":["Price"],"before":[1,2],"after":[2,1],
                        "edges":[0.,1.],"afterEdges":[-1.,1.],"missing":[],"stats":[],"note":"3 observations","figures":{}}
            monkeypatch.setattr(world.models.engine, "call", inspect)
            await act("inspect_model_input", {**obj,"input":"i1"})
            await act("update_object", {**obj, "name": "Model results"})
            strategy = await act("create_object", {"kind":"strategy","name":"Momentum"})
            copy = await act("create_object", {"copyOf":strategy["objectId"], "kind":"strategy"})
            await act("delete_object", {"objectId": copy["objectId"]})
            await act("create_object_folder", {"kind":"factor", "name":"Signals"})
            await act("rename_object_folder", {"kind":"factor", "from":"Signals", "name":"Factors"})
            await act("delete_object_folder", {"kind":"factor", "name":"Factors"})
            research = await act("create_research_list", {"name": "Equities", "symbols": []})
            listing = {"listId": research["listId"]}
            await act("read_research_list", listing)
            await act("update_research_list", {**listing, "name": "Universe"})
            await act("delete_research_list", listing)
            await act("read_object",{"objectId":strategy["objectId"],"part":"options"})
            await act("read_environments")
            assert entry_task("install_deep_environment")
            first = (await act("start_run", obj))["runId"]
            await wait_for(lambda: bool(world.engine.calls))
            await act("cancel_run", {"runId":first})
            run = {"runId":first}
            await act("resume_run", run)
            assert (await settled(world.models, first))["state"] == "completed"
            observed = await act("read_run", run, origin="ai")
            assert observed["summary"]["openTarget"]["objectId"] == "run:" + first
            report = world.models.directory(first) / "output"
            (report / "results.json").write_text('{"overview":{"windows":2}}', encoding="utf-8")
            (report / "figures.json").write_text("{}", encoding="utf-8")
            values = world.models.directory(first) / "values"
            values.mkdir(exist_ok=True)
            (values / "first.json").write_text('{"rows":[],"date":0}', encoding="utf-8")
            await act("read_run_table",{**run,"table":"values"})
            assert (await act("read_run", run, origin="ai"))["summary"] == observed["summary"]
            await act("read_run",{**run,"part":"code"})
            for which in ("windows","log","values"):
                await act("read_run_table",{**run,"table":which})
            for name in ("read_run_materials","apply_run","plan_publication"):
                await act(name,run)
            await act("update_run", {**run, "note": "Reviewed", "starred": True})
            second = (await act("start_run", obj))["runId"]
            await settled(world.models, second)
            await act("read_run",{**run,"compareTo":second})
            combined = await act("combine_model_runs", {"runIds":[first,second]})
            await settled(world.models, combined["runId"])
            await act("plan_held_out", run)
            held = await act("validate_held_out", run)
            await settled(world.models, held["runId"])
            notebook = (await act("open_run_in_notebook", run))["notebookId"]
            from types import SimpleNamespace

            from modules.quant.backend import entry
            notebooks = entry.state["notebooks"]
            monkeypatch.setattr(notebooks.kernels, "session", lambda _: SimpleNamespace(variables=[{"name":"returns","type":"Series"}],loaded=[]))
            await act("read_notebook_variables", {"notebookId":notebook})
            cells = (await notebooks.files.read(notebook))["notebook"]["cells"]
            extract = {"notebookId":notebook,"cells":[cells[0]["id"]],"kind":"factor"}
            await act("create_object",{"kind":"factor","name":"Notebook signal","fromNotebook":{"notebookId":notebook,"cells":extract["cells"]}})
            await act("build_results",run)
            await wait_for(lambda: world.models.read(first).get("portfolio",{}).get("state") != "running")
            await act("write_as_code", obj)
            await act("update_object",{**obj,"params":None})
            await world.shell.store.update_settings(model, settings)
            batch = await act("start_sweep",{**obj,"axes":[{"name":"seed","values":[1,2,3]}]})
            members = world.models.records.get("qmbatch",world.models.read(batch["runId"])["batch"])["runs"]
            await wait_for(lambda: world.models.read(members[-1])["state"] == "queued")
            await act("cancel_run",{"runId":members[-1]})
        finally:
            await world.models.close()
            world.shell.store.records.close()

    asyncio.run(scenario())


def test_strategy_sweep_actions_stopped_and_resumed(tmp_path, monkeypatch):
    from modules.quant.backend.run_queue import RunQueue
    from tests.fixtures.strategy.fakes import services, strategy
    from tests.unit.test_quant_object_runs import STARTER
    from tests.unit.test_quant_strategy_sweeps import AXES, GB, Runner, settled
    from tests.unit.test_quant_strategy_walk import WalkRunner, patch

    async def scenario():
        ctx, records, shell = services(tmp_path)
        act = public_actions(ctx, shell, monkeypatch)
        try:
            obj = {"objectId":await strategy(shell, STARTER)}
            shell.sweeps.queue = shell.runs.queue = RunQueue(memory=64 * GB, cpus=8)
            monkeypatch.setattr(shell.sweeps, "case_memory", lambda *args: asyncio.sleep(0, GB))
            Runner(shell, delay=.03)
            run = {"runId":(await act("start_sweep", {**obj,"axes":AXES}))["runId"]}
            await wait_for(lambda: shell.runs.read(run["runId"])["state"] == "running")
            await act("cancel_run", run)
            await act("resume_run", run)
            await settled(shell, run["runId"])
            await act("read_run", run)
            shell.sweeps.select(run["runId"],0)
            await act("apply_run",{**run,"cell":0})
            patch(shell, monkeypatch)
            WalkRunner(shell)
            walk = await act("start_walk_forward", {**obj,"axes":AXES,"pickOn":6,"trade":2})
            await settled(shell, walk["runId"])
        finally:
            records.close()

    asyncio.run(scenario())


def test_allocation_preparation_history_and_code_receipts(tmp_path, monkeypatch):
    from tests.unit.test_quant_allocation_runs import finishing, services, settled

    async def scenario():
        ctx, shell, runs = services(tmp_path, monkeypatch, finishing)
        act = public_actions(ctx, shell, monkeypatch)
        obj = {"objectId": (await act("create_object", {"kind":"allocation","name":"Mix"}))["objectId"]}
        try:
            await shell.store.update_settings(obj["objectId"], {"data": DATA})
            await act("read_object",{**obj,"part":"options"},origin="ai")
            async def trial(op, job, **options):
                assert op == "allocation_trial"
                return {"ok":True, "weights":{"BTC/USDT:USDT":1.0}, "checks":[]}
            monkeypatch.setattr(runs.engine, "call", trial)
            await act("trial_allocation", obj)
            identity = (await act("start_run", obj))["runId"]
            assert (await settled(runs, identity))["state"] == "completed"
            run = {"runId":identity}
            assert (await act("read_run", run, origin="ai"))["summary"] == (await act("read_run", run))["summary"]
            await act("list_runs", obj)
            for name in ("read_run", "apply_run", "cancel_run"):
                await act(name, run)
            await act("start_sweep", {**obj,"axes":[{"name":"lookback","values":[126,252]}]})
            for row in runs.of_object(obj["objectId"]):
                await settled(runs,row["id"])
            await act("write_as_code", obj)
        finally:
            await runs.close()
            shell.store.records.close()

    asyncio.run(scenario())


def test_published_factor_and_library_period_review(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from modules.quant.backend.allocation_routes import handle_allocation_actions
    from modules.quant.backend.object_history import RunHistory
    from tests.unit.quant_library_fakes import publishing
    from tests.unit.quant_public import library_actions, service_actions
    from tests.unit.test_quant_allocation_review import MONTH, reviewing, store_with_version

    async def scenario():
        world = publishing(tmp_path)
        act = library_actions(monkeypatch, library=world.library, publisher=world.publisher, runs=world.runs, history=RunHistory(world.records,world.runs))
        for name in ("publish_run",):
            receipt = await act(name, {"runId":"run-src","name":"Reversal"})
            await world.runs.tasks[receipt["runId"]]
            assert world.runs.read(receipt["runId"])["state"] == "completed"
        store = store_with_version(tmp_path/"review", lambda: 5 * MONTH)
        act = service_actions(monkeypatch, handle_allocation_actions, SimpleNamespace(allocation=reviewing(store)))
        await act("review_allocation_period", {"versionId":"v1","period":__import__("datetime").datetime.fromtimestamp(2*MONTH/1000,__import__("datetime").UTC).date().isoformat(),"how":"accept"})
        world.records.close()

    asyncio.run(scenario())


def test_research_files_and_index_members(tmp_path, monkeypatch):
    import pandas as pd

    from tests.unit.test_quant_object_runs import SPY

    async def scenario():
        world = model_world(tmp_path, monkeypatch)
        act = public_actions(world.ctx, world.shell, monkeypatch)
        try:
            # The frozen Market read is a real table on disk; Quant hands it over with its digest and instrument metadata.
            pd.DataFrame([{"time":0,"close":1.25}]).to_parquet(tmp_path/"bars.parquet", index=False)
            (tmp_path/"info.json").write_text(json.dumps({"instruments":[SPY],"since":"2024-01-01","until":"2024-12-31"}),encoding="utf-8")
            monkeypatch.setattr(world.shell.research_bars,"frozen",lambda *_:asyncio.sleep(0,tmp_path))
            data = await act("read_research_bars",{"symbols":["US:SPY"],"timeframe":"1d","start":"2024-01-01","end":"2024-12-31"})
            assert data["coverage"]["names"] == 1 and data["bars"]["sha256"]
        finally:
            world.shell.store.records.close()

    asyncio.run(scenario())


def test_explicit_first_strategy_pin_uses_the_declared_parameter_and_version(tmp_path, monkeypatch):
    from tests.unit.quant_library_fakes import factor_spec
    from tests.unit.test_quant_factor_runs import services

    async def scenario():
        ctx, shell, factors = services(tmp_path, monkeypatch, [])
        act = public_actions(ctx, shell, monkeypatch)
        try:
            shell.library.store.commit_versions([factor_spec("Signal", "v1", 50)])
            obj = await act("create_object", {"kind": "strategy", "preset": "ranked", "params": {"signal": "Signal"}})
            identity = obj["objectId"]
            assert obj["libraryInputs"] == {} and not shell.library.users("v1")
            await act("update_object", {"objectId": identity, "libraryInputs": {"Signal": "v1"}})
            shell.library.store.commit_versions([factor_spec("Signal", "v2", 50)])
            assert (await act("read_object", {"objectId": identity}))["libraryInputs"] == {"Signal": "v1"}
            pin = shell.store.read(identity)["settings"]["inputs"]["library"]
            assert shell.runs.pinned(pin, "signal", "Signal", lambda: "v2") == "v1"
            assert [u["objectId"] for u in shell.library.users("v1")] == [identity]
            assert not shell.library.users("v2")
            copy = await act("create_object", {"kind": "strategy", "copyOf": identity})
            assert {u["objectId"] for u in shell.library.users("v1")} == {identity, copy["objectId"]}
            await act("update_object", {"objectId": identity, "params": {"signal": "Other"}})
            assert (await act("read_object", {"objectId": identity}))["libraryInputs"] == {}
            assert [u["objectId"] for u in shell.library.users("v1")] == [copy["objectId"]]
            shell.runs.pin_inputs(identity, {"library": pin, "models": {}})
            shell.runs.pin_inputs(copy["objectId"], {"library": {"signal": {"name": "Signal", "versionId": "v2"}}, "models": {}})
            assert [u["objectId"] for u in shell.library.users("v1")] == [copy["objectId"]]
            assert not shell.library.users("v2")
            await act("update_object", {"objectId": identity, "params": {"signal": "Signal"}})
            shell.runs.pin_inputs(identity, {"library": {"signal": {"name": "Signal", "versionId": "v2"}}, "models": {}})
            assert (await act("read_object", {"objectId": identity}))["libraryInputs"] == {"Signal": "v2"}
            assert [u["objectId"] for u in shell.library.users("v2")] == [identity]
            shell.library.acquire("v1", "qobject:" + identity, "ws", kind="strategy", name="stale")
            shell.library.release("v2", "qobject:" + identity)
            shell.library.reconcile_strategy_users(shell.store.records.list("qobject"))
            assert [u["objectId"] for u in shell.library.users("v1")] == [copy["objectId"]]
            assert [u["objectId"] for u in shell.library.users("v2")] == [identity]
        finally:
            await factors.close()
            shell.store.records.close()
    asyncio.run(scenario())


def test_factor_run_receipts_and_library_versions(tmp_path, monkeypatch):
    from tests.unit.quant_library_fakes import factor_spec
    from tests.unit.test_quant_factor_runs import services, settled

    async def scenario():
        ctx, shell, factors = services(tmp_path, monkeypatch, [.012,.018])
        act = public_actions(ctx, shell, monkeypatch)
        try:
            obj = {"objectId": (await act("create_object", {"kind":"factor","name":"Reversal"}))["objectId"]}
            await shell.store.update_settings(obj["objectId"], {"data":DATA})
            await act("read_object",{**obj,"part":"options"})
            formula = "-($close / Ref($close, 5) - 1)"
            await act("validate_factor_formula", {**obj,"formula":formula})
            await act("update_object",{**obj,"formula":formula})
            identity = (await act("start_run",obj))["runId"]
            await settled(factors, identity)
            run = {"runId":identity}
            assert (await act("read_run", run, origin="ai"))["summary"] == (await act("read_run", run))["summary"]
            for name in ("read_run", "cancel_run"):
                await act(name, run)
            await act("create_object", {"fromRun":identity,"kind":"model"})
            import numpy as np
            np.save(factors.directory(identity) / "output" / "values.npy", np.array([[.012]]))
            factors._put_results(identity, None, {"state":"done","progress":1,"tabs":{}})
            await act("build_results", run)
            await act("plan_publication", {**run,"name":"Reversal"})
            await act("write_as_code", {**obj,"formula":formula})

            library = shell.library
            versions = [factor_spec("Signal", key, 50) for key in ("signal1","signal2")]
            created = [library.store.commit_versions([spec])[0] for spec in versions]
            version = {"versionId":"signal1"}
            using = await act("create_object",{"fromVersion":"signal1","kind":"strategy"})
            await act("update_object",{"objectId":using["objectId"],"libraryInputs":{"Signal":"signal2"}})
            await act("retry_library_version", {"versionId":"signal2"})
            await act("cancel_library_update", {"versionId":"signal2"})
            await act("read_library_item", {"itemId":created[0]["itemId"]})
            await act("read_library_log",version)
            await act("read_library_item",{"name":"Signal",**version,"compareTo":"signal2"})
            for patch in ({"autoUpdate":False},{"producing":False},{"producing":True},{"producing":False}):
                await act("update_library_version",{**version,**patch})
            await act("archive_library_version", version)
            await act("publish_library_values", {"name":"Returns","kind":"data","rows":[
                {"date":"1970-01-02", "symbol":"SPY","field":"close","value":1.125}]})
            await act("read_library_values", {"name":"Returns","format":"file"})
            await act("read_library_values", {"name":"Returns"})
        finally:
            await factors.close()
            shell.store.records.close()

    asyncio.run(scenario())


def test_ai_adds_replaces_and_deletes_notebook_cells_through_the_panels_save(tmp_path, monkeypatch):
    async def scenario():
        world = model_world(tmp_path, monkeypatch, windows=2)
        act = public_actions(world.ctx, world.shell, monkeypatch)
        notebook = (await act("create_object", {"kind": "notebook", "name": "Cells"}))["objectId"]
        added = await act("write_cell", {"objectId": notebook, "op": "add", "source": "1 + 1"}, origin="ai")
        cell = added["cellId"]
        assert added["cells"][-1] == {"cellId": cell, "type": "code"}
        await act("write_cell", {"objectId": notebook, "op": "add", "source": "# Notes", "type": "markdown",
                                 "after": cell}, origin="ai")
        await act("write_cell", {"objectId": notebook, "op": "replace", "cellId": cell, "source": "2 + 2"},
                             origin="ai")
        saved = (await world.shell.notebooks.files.read(notebook))["notebook"]["cells"]
        assert next(item for item in saved if item["id"] == cell)["source"] == "2 + 2"
        deleted = await act("write_cell", {"objectId": notebook, "op": "delete", "cellId": cell}, origin="ai")
        assert cell not in [item["cellId"] for item in deleted["cells"]]
        try:
            await act("write_cell", {"objectId": notebook, "op": "delete", "cellId": cell}, origin="ai")
        except Exception as error:
            assert getattr(error, "code", None) == "not_found"
        else:
            raise AssertionError("deleting a missing cell succeeded")
    asyncio.run(scenario())
