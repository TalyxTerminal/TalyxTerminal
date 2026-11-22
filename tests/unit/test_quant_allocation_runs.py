"""An allocation run from start to its saved end, with the data and the research process standing in: what the run records, what changed from
the run before, the periods a stopped run keeps, and the object created in the standard flow."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from modules.quant.backend.object_routes import Services
from modules.quant.backend.work_records import WorkRecords
from tests.unit.test_quant_object_runs import DATA, FakeContext

DAY = 86_400_000
METRICS = {"total": .2, "cagr": .03, "vol": .07, "sharpe": .5, "maxDrawdown": -.2, "turnover": .4, "costDrag": .0004}


async def prepared(settings, report=None, calendars="one"):
    return {"key": "k", "barsPath": "bars.parquet", "extrasPath": "extras.json", "info": {"annualization": 252, "instruments": []}, "dataFrom": 0, "dataTo": DAY,
            "names": 1, "requested": 1, "notes": [], "frozenAt": 0}


def services(tmp_path, monkeypatch, behave):
    ctx = FakeContext(tmp_path, None)
    shell = Services(ctx, WorkRecords(tmp_path / "records"))
    runs = shell.allocation
    async def data_for(settings, report=None, calendars="one"):
        result = await prepared(settings, report, calendars)
        bars = tmp_path / "bars.parquet"
        bars.touch()
        return {**result, "barsPath": str(bars)}
    monkeypatch.setattr(runs.data, "prepare", data_for)
    monkeypatch.setattr(runs.data, "calendar_items", lambda data: asyncio.sleep(0, None))

    lock = tmp_path / "research.lock"
    lock.write_text("numpy==1")
    async def environment(publish=None):
        return {"state": "completed", "preparationId": "research", "interpreterPath": "python", "environmentPath": "env", "pythonVersion": "3.12", "lockPath": str(lock)}
    monkeypatch.setattr(shell.packages, "research_environment", environment)

    async def run_once(job, directory, **kwargs):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "invocation.json").write_text(json.dumps({"job": job}), encoding="utf-8")
        output = Path(job["output"])
        output.mkdir(parents=True, exist_ok=True)
        await behave(job, output)
        return {}
    monkeypatch.setattr(runs.engine, "run_once", run_once)
    return ctx, shell, runs


def finishing(job, output):
    async def go():
        work = {"metrics": METRICS, "equalMetrics": METRICS, "checks": [], "notes": [], "periods": 3, "from": "2024-01-02", "to": "2024-06-28", "thisPeriod": [], "seconds": .1}
        (output / "work.json").write_text(json.dumps(work), encoding="utf-8")
        (output / "answer.json").write_text(json.dumps({"ok": True, "seconds": .1}), encoding="utf-8")
    return go()


async def settled(runs, identity):
    async with asyncio.timeout(10):
        while identity in runs.tasks:
            await asyncio.sleep(.01)
    return runs.read(identity)


def test_a_new_allocation_is_the_standard_flow_with_an_empty_pool(tmp_path, monkeypatch):
    async def scenario():
        ctx, shell, runs = services(tmp_path, monkeypatch, finishing)
        created = await shell.store.create({"kind": "allocation", "name": "Mix"})
        assert created["settings"]["mode"] == "standard" and created["settings"]["data"]["symbols"] == [] and created["settings"]["data"]["timeframe"] == "1d"
        assert any(item["name"] == "objective" for item in created["parameters"])
        from talyx_sdk import ContractError
        try:
            await runs.start(created["id"])
        except ContractError as error:
            assert error.message == "The pool is empty"
        else:
            raise AssertionError("an empty pool must be refused")
    asyncio.run(scenario())


def test_a_run_is_saved_and_the_next_one_says_every_setting_that_changed(tmp_path, monkeypatch):
    async def scenario():
        ctx, shell, runs = services(tmp_path, monkeypatch, finishing)
        created = await shell.store.create({"kind": "allocation", "name": "Mix"})
        await shell.store.update_settings(created["id"], {"data": DATA})
        first = await runs.start(created["id"])
        done = await settled(runs, first["id"])
        assert (done["state"], done["metrics"]["cagr"], done["settings"]["data"]["until"] != "latest") == ("completed", .03, True)
        assert done["data"]["dataFrom"] == "2024-01-02" and first["changes"] == []
        second = await runs.start(created["id"], {"changes": {"lookback": 126, "covariance": "ledoit_wolf", "cost_multiple": 2.0}})
        await settled(runs, second["id"])
        names = {item["name"]: (item["from"], item["to"]) for item in second["changes"]}
        assert names == {"lookback": (252, 126), "covariance": ("empirical", "ledoit_wolf"), "cost_multiple": (1.0, 2.0)}  # the whole setting set, not only the data
        assert [item["number"] for item in runs.list(created["id"])["items"]] == [2, 1]
    asyncio.run(scenario())


def test_a_failed_run_says_which_settings_tripped_it(tmp_path, monkeypatch):
    async def behave(job, output):
        (output / "answer.json").write_text(json.dumps({"ok": False, "failure": {"stage": "settings", "title": "stopped", "message": "Limits can't add up to 100%", "fields": ["max_weight"]}}))
    async def scenario():
        ctx, shell, runs = services(tmp_path, monkeypatch, behave)
        created = await shell.store.create({"kind": "allocation", "name": "Mix"})
        await shell.store.update_settings(created["id"], {"data": DATA})
        started = await runs.start(created["id"])
        done = await settled(runs, started["id"])
        assert done["state"] == "failed" and done["failure"]["fields"] == ["max_weight"] and done["metrics"] is None
    asyncio.run(scenario())


def test_cancel_keeps_periods(tmp_path, monkeypatch):
    async def behave(job, output):
        (output / "periods.jsonl").write_text("\n".join(json.dumps({"date": f"2024-0{k}-28", "heldFrom": f"2024-0{k}-29", "weights": {"A": 1.0}, "riskShare": {"A": 1.0}, "turnover": 0, "cost": 0,
                                                                     "status": "optimal", "note": "", "bound": {}, "seconds": 0}) for k in (1, 2, 3)))
        await asyncio.sleep(30)
    async def scenario():
        ctx, shell, runs = services(tmp_path, monkeypatch, behave)
        created = await shell.store.create({"kind": "allocation", "name": "Mix"})
        await shell.store.update_settings(created["id"], {"data": DATA})
        started = await runs.start(created["id"])
        await asyncio.sleep(.3)
        stopped = await runs.stop(started["id"])
        assert stopped["state"] == "cancelled" and stopped["message"] == "Stopped at period 4; the periods so far are kept."
        view = runs.work(started["id"])
        assert view["work"] is None and len(view["periods"]) == 3 and runs.period(started["id"], 2)["date"] == "2024-03-28"
    asyncio.run(scenario())


def test_interrupted_keeps_periods(tmp_path, monkeypatch):
    async def scenario():
        ctx, shell, runs = services(tmp_path, monkeypatch, finishing)
        created = await shell.store.create({"kind": "allocation", "name": "Mix"})
        await shell.store.update_settings(created["id"], {"data": DATA})
        record = await runs.create(shell.store.read(created["id"]), "backtest", {}, [])
        runs.save(record, state="running")
        runs.recover(record["workspaceId"])
        assert runs.read(record["id"])["state"] == "interrupted"
    asyncio.run(scenario())


def test_allocation_held_out_once(tmp_path, monkeypatch):
    """The confirmation names the part and its length; once a held-out run is done, asking again says it was seen (and with the same values or not)."""
    async def scenario():
        ctx, shell, runs = services(tmp_path, monkeypatch, finishing)
        created = await shell.store.create({"kind": "allocation", "name": "Mix"})
        await shell.store.update_settings(created["id"], {"data":{**DATA,"heldOutFrom":"2024-05-01"}})
        from talyx_sdk import ContractError
        try:
            await runs.held_status(created["id"])
        except ContractError as error:
            assert "Run a backtest first" in error.message
        first = await runs.start(created["id"])
        await settled(runs, first["id"])
        status = await runs.held_status(created["id"])
        assert status["number"] == 1 and status["used"] is None and status["to"] == "2024-06-30" and status["from"] < "2024-06-30"
        assert status["short"] is True and status["days"] < 126  # a short part is said to be noisy: test_held_out_short
        await shell.store.update_settings(created["id"], {"params": {"objective": "equal_weight"}})
        held = await runs.start(created["id"], {"kind": "heldout", "of": first["id"]})
        assert runs.read(held["id"])["settings"]["params"] == runs.read(first["id"])["settings"]["params"]
        await settled(runs, held["id"])
        again = await runs.held_status(created["id"])
        assert again["used"] == {"runId": held["id"], "number": 2, "same": True}
        await shell.store.update_settings(created["id"], {"params": {"lookback": 126}})
        changed = await runs.start(created["id"])
        await settled(runs, changed["id"])
        assert (await runs.held_status(created["id"]))["used"]["same"] is False
    asyncio.run(scenario())


def test_cost_multiple_axis(tmp_path, monkeypatch):
    """A sweep over the cost multiple (and a second setting) sends one run for each cell, each recording its values, and picks none of them."""
    async def scenario():
        ctx, shell, runs = services(tmp_path, monkeypatch, finishing)
        created = await shell.store.create({"kind": "allocation", "name": "Mix"})
        await shell.store.update_settings(created["id"], {"data": DATA})
        started = await runs.start_sweep(created["id"], {"axes": [{"name": "@costMultiple", "values": [0, 1, 2]}, {"name": "lookback", "values": [126, 252]}]})
        assert len(started["runs"]) == 6 and {tuple(run["sweep"]["cell"].items()) for run in started["runs"]} >= {(("cost_multiple", 2), ("lookback", 126))}
        for run in started["runs"]:
            await settled(runs, run["id"])
        done = runs.list(created["id"])["items"]
        assert all(item["state"] == "completed" and item["sweep"]["id"] == started["sweepId"] for item in done) and not any("best" in str(item.get("sweep")) for item in done)
        assert sorted(item["settings"]["params"]["cost_multiple"] for item in map(lambda row: runs.read(row["id"]), done)) == [0, 0, 1, 1, 2, 2]
        from talyx_sdk import ContractError
        try:
            await runs.start_sweep(created["id"], {"axes": [{"name": "lookback", "values": [126]}]})
        except ContractError:
            pass
        else:
            raise AssertionError("a sweep needs two or more values")
        many = await runs.start_sweep(created["id"], {"axes": [{"name": "lookback", "values": list(range(60, 85))}, {"name": "@costMultiple", "values": [0, 1, 2, 3]}]})
        assert len(many["runs"]) == 100   # no cap on how many runs a sweep sends
        for run in many["runs"]:
            await settled(runs, run["id"])
    asyncio.run(scenario())


def enable_publishing(ctx, shell, runs, monkeypatch, tmp_path, column, instrument_id):
    """What the library's publication needs around an allocation: frozen bars on a calendar, the research environment, and a research process that, cut at a
    point, hands back the weights it was given (a code that sees no future)."""
    import numpy as np
    import pandas as pd

    from modules.quant.backend import library_publish
    HOUR = 3_600_000
    first = pd.Timestamp("2023-06-01", tz="UTC")
    labels = [int((first + pd.Timedelta(days=k)).value // 1_000_000) for k in range(0, 460)]
    bars = tmp_path / "bars.parquet"
    pd.DataFrame({"label": labels, "column": column, "close": np.linspace(100, 160, len(labels))}).to_parquet(bars, index=False)
    info = {"timeframe": "1d", "calendarId": "XNYS", "sessionScope": "regular", "timezone": "UTC", "annualization": 252, "since": labels[0], "until": labels[-1],
            "instruments": [{"column": column, "instrumentId": instrument_id, "displaySymbol": column, "sourceId": "fake"}]}

    async def ready(settings, report=None, calendars="one"):
        return {"key": "k", "barsPath": str(bars), "extrasPath": str(tmp_path / "extras.json"), "info": info, "dataFrom": labels[0], "dataTo": labels[-1], "names": 1, "requested": 1, "notes": [], "frozenAt": 0}
    monkeypatch.setattr(runs.data, "prepare", ready)
    lock = tmp_path / "research.lock"
    lock.write_text("numpy==1\n", encoding="utf-8")

    async def environment(publish=None):
        return {"state": "completed", "preparationId": "prep", "interpreterPath": "python", "environmentPath": "env", "pythonVersion": "3.12", "lockPath": str(lock)}
    monkeypatch.setattr(shell.packages, "research_environment", environment)

    async def nothing(args):
        return None

    async def boundaries(args):
        selected = [label for label in labels if int(args["start"]) <= (label + 21 * HOUR) * 1000000 and label * 1000000 < int(args["end"])]
        return {"evidence": "verified", "reason": None, "timezone": "UTC", "next": None,
                "open": [str((label // DAY * DAY + 14 * HOUR) * 1000000) for label in selected],
                "close": [str((label // DAY * DAY + 21 * HOUR) * 1000000) for label in selected]}
    ctx.platform.execution = SimpleNamespace(cancel=nothing, status=nothing)
    ctx.platform.leases = SimpleNamespace(close=nothing)
    ctx.platform.python_env = SimpleNamespace(get=lambda args: asyncio.sleep(0, {"state": "completed"}), acquire=nothing)
    ctx.require("market.calendar").bar_times = boundaries
    seen = []

    class Process:
        write_support = staticmethod(lambda directory: (directory / "support").mkdir(exist_ok=True) or directory / "support")
        write_invocation = staticmethod(lambda directory, invocation: (directory / "invocation.json").write_text(json.dumps(invocation), encoding="utf-8"))

        @staticmethod
        async def start(ctx_, *, key, directory, invocation, **_):
            seen.append(invocation)
            return {"lease": key}

        @staticmethod
        async def wait(ctx_, lease):
            invocation = seen[-1]
            output = Path(invocation["output"])
            history = pd.read_parquet(Path(invocation["research"]["barsPath"]).parent.parent / "output" / "weights.parquet")
            for number, cutoff in enumerate(invocation["cutoffs"]):
                path = output / f"values-{number}-weights.parquet"
                history[history["datetime"] <= cutoff].to_parquet(path, index=False)
                (output / f"values-{number}.json").write_text(json.dumps({"cutoff": cutoff, "publications": [{"name": "weights", "kind": "allocation", "path": path.name}]}), encoding="utf-8")
            (output / "done.json").write_text("{}", encoding="utf-8")
            return 0, []
    monkeypatch.setattr(library_publish, "research_process", Process)
    return seen, labels


def stub_market(runs, monkeypatch, listed):
    """`prepare_data` against a Market that lists only `listed` (symbol text -> instrument id); `prepare` answers one column for each symbol asked for."""
    from modules.quant.backend import allocation_runs as module
    asked = {}

    async def resolve_text(ctx, text):
        if text not in listed:
            from talyx_sdk import ContractError
            raise ContractError("not_found", text)
        return {"instrumentId": listed[text], "symbol": text, "displaySymbol": text}

    async def prepare(data, calendars="one"):
        asked["symbols"] = [item["instrument"]["instrumentId"] for item in data["symbols"]]
        return {"barsPath": "b", "extrasPath": "e", "info": {"instruments": [{"instrumentId": item["instrument"]["instrumentId"], "column": item["instrument"]["displaySymbol"]} for item in data["symbols"]]}}

    monkeypatch.setattr(module, "resolve_text", resolve_text)
    monkeypatch.setattr(runs.data, "prepare", prepare)
    return asked


def pool_rows(**extra):
    return [{"ticker": "IEF", "instrumentId": "i-ief", "currency": "USD", "proxy": extra.get("proxy", "")},
            {"ticker": "SAP", "instrumentId": "i-sap", "currency": extra.get("currency", "USD"), "proxy": ""}]


def test_proxy_and_fx_columns_are_frozen_next_to_the_pool(tmp_path, monkeypatch):
    runs = Services(FakeContext(tmp_path, None), WorkRecords(tmp_path / "records")).allocation
    asked = stub_market(runs, monkeypatch, {"VFITX": "i-vf", "EURUSD=X": "i-fx"})
    data = {"symbols": [{"instrument": {"instrumentId": "i-ief", "displaySymbol": "IEF"}}, {"instrument": {"instrumentId": "i-sap", "displaySymbol": "SAP"}}]}
    ready, extras = asyncio.run(runs.prepare_data(data, {"currency": "USD"}, pool_rows(proxy="VFITX", currency="EUR"), False))
    assert asked["symbols"] == ["i-ief", "i-sap", "i-vf", "i-fx"]
    assert extras == {"proxyColumns": {"IEF": "VFITX"}, "fxColumns": {"SAP": {"column": "EURUSD=X", "invert": False}}, "benchmarkColumns": {}}
    assert len(data["symbols"]) == 2        # the object's own data settings are not touched


def test_fx_missing_refused(tmp_path, monkeypatch):
    from talyx_sdk import ContractError
    runs = Services(FakeContext(tmp_path, None), WorkRecords(tmp_path / "records")).allocation
    stub_market(runs, monkeypatch, {})
    data = {"symbols": [{"instrument": {"instrumentId": "i-ief", "displaySymbol": "IEF"}}]}
    try:
        asyncio.run(runs.prepare_data(data, {"currency": "USD"}, pool_rows(currency="EUR"), False))
    except ContractError as error:
        assert error.code == "not_found" and error.message == "EURUSD=X"
    else:
        raise AssertionError("a currency Market has no rate for must be refused")


def test_dollar_stablecoin_needs_no_rate(tmp_path, monkeypatch):
    runs = Services(FakeContext(tmp_path, None), WorkRecords(tmp_path / "records")).allocation
    asked = stub_market(runs, monkeypatch, {})
    data = {"symbols": [{"instrument": {"instrumentId": "i-ief", "displaySymbol": "IEF"}}, {"instrument": {"instrumentId": "i-sap", "displaySymbol": "SAP"}}]}
    _, extras = asyncio.run(runs.prepare_data(data, {"currency": "USD"}, pool_rows(currency="USDT"), False))
    assert extras == {"proxyColumns": {}, "fxColumns": {}, "benchmarkColumns": {}} and asked["symbols"] == ["i-ief", "i-sap"]


def test_a_tracked_benchmark_is_frozen_beside_the_pool_whether_in_it_or_not(tmp_path, monkeypatch):
    runs = Services(FakeContext(tmp_path, None), WorkRecords(tmp_path / "records")).allocation
    asked = stub_market(runs, monkeypatch, {"QQQ": "i-qqq", "IEF": "i-ief"})
    data = {"symbols": [{"instrument": {"instrumentId": "i-ief", "displaySymbol": "IEF"}}, {"instrument": {"instrumentId": "i-sap", "displaySymbol": "SAP"}}]}
    tracking = {"currency": "USD", "objective": "benchmark_tracking", "benchmark": "QQQ 70%, IEF 30%"}
    _, extras = asyncio.run(runs.prepare_data(data, tracking, pool_rows(), False))
    assert extras["benchmarkColumns"] == {"QQQ": 70.0, "IEF": 30.0} and asked["symbols"] == ["i-ief", "i-sap", "i-qqq"]
    # settings that do not follow it freeze none (code may: its estimator can track one)
    _, extras = asyncio.run(runs.prepare_data(data, {**tracking, "objective": "min_risk"}, pool_rows(), False))
    assert extras["benchmarkColumns"] == {} and asked["symbols"] == ["i-ief", "i-sap"]
    _, extras = asyncio.run(runs.prepare_data(data, {**tracking, "objective": "min_risk"}, pool_rows(), True))
    assert extras["benchmarkColumns"] == {"QQQ": 70.0, "IEF": 30.0}


def test_show_code_reads_the_code_each_run_ran(tmp_path):
    """A standard-flow run keeps the standard flow file it ran and a code-mode run its project's files, beside the run: Show code is never empty and a later run
    (or a newer standard flow) does not change what an earlier one shows."""
    from types import SimpleNamespace

    from modules.quant.backend.allocation_runs import AllocationRuns
    from modules.quant.backend.project_paths import MODULE
    runs = AllocationRuns.__new__(AllocationRuns)
    runs.records = SimpleNamespace(root=tmp_path, find=lambda kind, identity: {"id": identity, "allocation": True})
    runs.keep_code("run-a", None)
    standard = runs.code("run-a")["files"]
    assert [item["file"] for item in standard] == ["allocation_standard.py"]
    assert standard[0]["text"] == (MODULE / "native" / "allocation_standard.py").read_text(encoding="utf-8")
    project = tmp_path / "project"
    (project / "lib").mkdir(parents=True)
    (project / "main.py").write_text("x = 1\n", encoding="utf-8")
    (project / "lib" / "helpers.py").write_text("y = 2\n", encoding="utf-8")
    runs.keep_code("run-b", {"source": str(project), "entry": "main.py"})
    (project / "main.py").write_text("x = 99\n", encoding="utf-8")
    assert runs.code("run-b")["files"] == [{"file": "lib/helpers.py", "text": "y = 2\n"}, {"file": "main.py", "text": "x = 1\n"}]
    assert runs.code("run-a")["files"] == standard


def test_what_changed_between_two_runs_names_the_dates_the_assets_and_the_settings():
    """only the settings were compared, so a later start read "No changes" and an added BTC "Same as #2"."""
    from modules.quant.backend.allocation_runs import run_changes
    pool = [{"ticker": "SPY", "group": "Stocks"}, {"ticker": "IEF", "group": "Bonds"}]
    before = {"data": {"since": "2016-11-01", "until": "2024-10-31", "heldOutFrom": None}, "pool": pool, "params": {"lookback": 252, "objective": "risk_parity"}}
    same = run_changes(before, before)
    assert same == []
    after = {"data": {"since": "2018-01-01", "until": "2024-10-31", "heldOutFrom": "2024-01-01"}, "pool": [*pool, {"ticker": "BTC", "group": "Crypto"}],
             "params": {"lookback": 126, "objective": "risk_parity"}}
    found = {item["name"]: (item["from"], item["to"]) for item in run_changes(before, after)}
    assert found == {"start": ("2016-11-01", "2018-01-01"), "held out from": ("none", "2024-01-01"), "assets": ("2", "3 (+BTC)"), "lookback": (252, 126)}
